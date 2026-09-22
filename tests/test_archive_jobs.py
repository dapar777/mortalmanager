"""Archive jobs: extract / compress / add / delete through JobQueue."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from src.archive import archive_manager as am
from src.core.file_model import OperationProgress
from src.jobs import archive_ops
from src.jobs.job import JobSpec, JobStatus, JobType
from src.jobs.job_queue import JobQueue


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    root = tmp_path / "src"
    (root / "docs").mkdir(parents=True)
    (root / "readme.txt").write_text("hello", encoding="utf-8")
    (root / "docs" / "index.md").write_text("# index", encoding="utf-8")
    return root


@pytest.fixture
def archive(tmp_path: Path, tree: Path) -> Path:
    out = tmp_path / "pack.zip"
    am.create_archive(out, [tree], base_dir=tree.parent)
    return out


def _run(queue: JobQueue, spec: JobSpec, timeout: float = 30.0):
    """Submit *spec* and pump the loop until the job reports back."""
    loop = asyncio.new_event_loop()
    try:
        queue.set_event_loop(loop)
        done: list = []
        queue.job_finished.connect(lambda _jid, res: done.append(res))
        queue.job_failed.connect(lambda _jid, msg: done.append(msg))
        queue.submit(spec)
        loop.run_until_complete(_wait(done, timeout))
    finally:
        loop.close()
    assert done, "job never finished"
    return done[0]


async def _wait(done: list, timeout: float) -> None:
    for _ in range(int(timeout * 100)):
        if done:
            return
        await asyncio.sleep(0.01)


# ------------------------------------------------------------------ extract

def test_extract_job(qapp, archive: Path, tmp_path: Path):
    out = tmp_path / "out"
    result = _run(JobQueue(), JobSpec(
        job_type=JobType.EXTRACT, sources=[str(archive)], destination=str(out),
    ))
    assert result.status == JobStatus.FINISHED
    assert result.files_processed == 2 and not result.errors
    assert (out / "src" / "readme.txt").read_text(encoding="utf-8") == "hello"
    assert (out / "src" / "docs" / "index.md").exists()


def test_extract_job_selected_members(qapp, archive: Path, tmp_path: Path):
    out = tmp_path / "part"
    result = _run(JobQueue(), JobSpec(
        job_type=JobType.EXTRACT, sources=[str(archive)], destination=str(out),
        options={"members": ["src/docs"]},
    ))
    assert result.status == JobStatus.FINISHED
    assert (out / "src" / "docs" / "index.md").exists()
    assert not (out / "src" / "readme.txt").exists()


def test_extract_strips_the_current_inner_directory(archive: Path, tmp_path: Path):
    """X3: extracting from inside src/docs drops that prefix."""
    out = tmp_path / "here"
    files, _, errors = archive_ops.extract(
        archive, out, ["src/docs/index.md"], strip_prefix="src/docs")
    assert files == 1 and not errors
    assert (out / "index.md").read_text(encoding="utf-8") == "# index"


def test_extract_reports_progress(archive: Path, tmp_path: Path):
    seen: list[OperationProgress] = []
    archive_ops.extract(archive, tmp_path / "out", None, progress=seen.append)
    assert seen and seen[-1].files_total == 2
    assert all(p.bytes_total > 0 for p in seen)


def test_extract_can_be_cancelled(archive: Path, tmp_path: Path):
    files, _, _ = archive_ops.extract(archive, tmp_path / "out", None,
                                      cancelled=lambda: True)
    assert files == 0


def test_extract_keeps_going_after_one_bad_member(archive: Path, tmp_path: Path, monkeypatch):
    """X7: a failing member is collected, the rest still lands on disk."""
    handler = am.get_handler(archive)
    original = type(handler).read_member

    def flaky(self, path, member, password=None):
        if member.endswith("readme.txt"):
            raise OSError("boom")
        return original(self, path, member, password)

    monkeypatch.setattr(type(handler), "read_member", flaky)
    out = tmp_path / "out"
    files, _, errors = archive_ops.extract(archive, out, None)
    assert files == 1 and len(errors) == 1 and "boom" in errors[0]
    assert (out / "src" / "docs" / "index.md").exists()


# ------------------------------------------------------------------ compress

def test_compress_job(qapp, tree: Path, tmp_path: Path):
    out = tmp_path / "made.zip"
    result = _run(JobQueue(), JobSpec(
        job_type=JobType.COMPRESS, sources=[str(tree)], destination=str(out),
        options={"base_dir": str(tree.parent)},
    ))
    assert result.status == JobStatus.FINISHED
    assert out.exists()
    assert {e.path for e in am.list_archive(out)} >= {"src/readme.txt", "src/docs/index.md"}


def test_compress_without_paths_flattens(tree: Path, tmp_path: Path):
    out = tmp_path / "flat.zip"
    archive_ops.compress(out, [tree], tree.parent, store_paths=False)
    names = {e.path for e in am.list_archive(out)}
    assert "readme.txt" in names and "src/readme.txt" not in names


def test_compress_append_into_existing(archive: Path, tmp_path: Path):
    extra = tmp_path / "extra.txt"
    extra.write_text("more", encoding="utf-8")
    archive_ops.compress(archive, [extra], extra.parent, append=True)
    names = {e.path for e in am.list_archive(archive)}
    assert "extra.txt" in names and "src/readme.txt" in names


def test_compress_unknown_extension_fails(tree: Path, tmp_path: Path):
    with pytest.raises(am.ArchiveError):
        archive_ops.compress(tmp_path / "x.unknown", [tree], tree.parent)


# ------------------------------------------------------------------ add / delete

def test_archive_add_job(qapp, archive: Path, tmp_path: Path):
    extra = tmp_path / "note.txt"
    extra.write_text("note", encoding="utf-8")
    result = _run(JobQueue(), JobSpec(
        job_type=JobType.ARCHIVE_ADD, sources=[str(extra)], destination="",
        options={"archive": str(archive), "base_dir": str(extra.parent), "prefix": "src/docs"},
    ))
    assert result.status == JobStatus.FINISHED
    assert am.read_member(archive, "src/docs/note.txt") == b"note"


def test_archive_delete_job(qapp, archive: Path):
    result = _run(JobQueue(), JobSpec(
        job_type=JobType.ARCHIVE_DELETE, sources=["src/docs"], destination="",
        options={"archive": str(archive)},
    ))
    assert result.status == JobStatus.FINISHED
    names = {e.path for e in am.list_archive(archive)}
    assert "src/docs/index.md" not in names and "src/readme.txt" in names


def test_extract_keeps_existing_files_when_asked(archive: Path, tmp_path: Path):
    """X6: overwrite=False leaves what is already on disk and reports how many."""
    out = tmp_path / "out"
    (out / "src").mkdir(parents=True)
    (out / "src" / "readme.txt").write_text("mine", encoding="utf-8")
    files, _, errors = archive_ops.extract(archive, out, None, overwrite=False)
    assert (out / "src" / "readme.txt").read_text(encoding="utf-8") == "mine"
    assert (out / "src" / "docs" / "index.md").exists()
    assert files == 1 and any("kept" in e for e in errors)


def test_unique_target(tmp_path: Path):
    assert archive_ops.unique_target(tmp_path, "a.txt") == tmp_path / "a.txt"
    (tmp_path / "a.txt").write_text("x")
    assert archive_ops.unique_target(tmp_path, "a.txt") == tmp_path / "a (2).txt"
