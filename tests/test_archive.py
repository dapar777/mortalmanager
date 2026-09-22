"""Archive layer: handlers, format detection, safe extraction, virtual tree."""

from __future__ import annotations

import tarfile
import zipfile
from pathlib import Path

import pytest

from src.archive import archive_manager as am
from src.archive.base import ArchiveError, ArchiveHandler
from src.archive.extract import safe_target
from src.archive.single_handler import SingleFileHandler
from src.archive.tar_handler import TarHandler
from src.archive.vfs import ArchiveLocation, listdir, split_archive_path
from src.archive.zip_handler import ZipHandler


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    """src/ with a nested structure and a unicode name."""
    root = tmp_path / "src"
    (root / "docs" / "api").mkdir(parents=True)
    (root / "readme.txt").write_text("hello", encoding="utf-8")
    (root / "docs" / "index.md").write_text("# index", encoding="utf-8")
    (root / "docs" / "api" / "ref.html").write_text("<h1>ref</h1>", encoding="utf-8")
    (root / "příloha č. 1.txt").write_text("ěščřž", encoding="utf-8")
    return root


def _formats() -> list[str]:
    """Writable container formats; 7z only when py7zr is installed."""
    from src.archive.sevenzip_handler import _HAS_PY7ZR

    return ["zip", "tar", "tar.gz"] + (["7z"] if _HAS_PY7ZR else [])


@pytest.fixture(params=_formats())
def archive(request, tmp_path: Path, tree: Path) -> Path:
    """The same content in every writable container format."""
    out = tmp_path / f"pack.{request.param}"
    am.create_archive(out, [tree], base_dir=tree.parent)
    return out


# ------------------------------------------------------------------ detection

def test_detects_format_by_content_not_extension(tmp_path: Path, tree: Path):
    zip_path = tmp_path / "plain.zip"
    ZipHandler().create(zip_path, [tree / "readme.txt"])
    renamed = tmp_path / "invoice.dat.zip"          # looks_like_archive needs a known suffix
    zip_path.rename(renamed)
    handler = am.get_handler(renamed)
    assert handler is not None and handler.format_name == "ZIP"
    assert am.is_archive(renamed)


def test_not_an_archive(tmp_path: Path):
    plain = tmp_path / "notes.zip"                  # right name, wrong content
    plain.write_text("just text")
    assert am.get_handler(plain) is None
    assert not am.is_archive(plain)
    assert not am.is_archive(tmp_path)              # a directory is never an archive


def test_handler_for_new_prefers_the_longest_extension(tmp_path: Path):
    assert am.handler_for_new(tmp_path / "a.tar.gz").format_name == "TAR"
    assert am.handler_for_new(tmp_path / "a.zip").format_name == "ZIP"
    assert am.handler_for_new(tmp_path / "a.gz").format_name == "GZ/BZ2/XZ"
    assert am.handler_for_new(tmp_path / "a.unknown") is None


def test_damaged_archive_raises_archive_error(tmp_path: Path, tree: Path):
    good = tmp_path / "good.zip"
    am.create_archive(good, [tree / "readme.txt"])
    data = bytearray(good.read_bytes())
    data[20:40] = b"\x00" * 20                      # wreck the middle, keep the directory
    broken = tmp_path / "broken.zip"
    broken.write_bytes(bytes(data))
    with pytest.raises(ArchiveError):
        ZipHandler().extract(broken, tmp_path / "out")


# ------------------------------------------------------------------ round trip

def test_create_list_extract_roundtrip(archive: Path, tmp_path: Path, tree: Path):
    names = {e.path for e in am.list_archive(archive)}
    assert "src/readme.txt" in names
    assert "src/docs/api/ref.html" in names
    assert "src/příloha č. 1.txt" in names          # unicode survives (T6)

    out = tmp_path / "out"
    am.extract_archive(archive, out)
    assert (out / "src" / "readme.txt").read_text(encoding="utf-8") == "hello"
    assert (out / "src" / "docs" / "api" / "ref.html").exists()
    assert (out / "src" / "příloha č. 1.txt").read_text(encoding="utf-8") == "ěščřž"


def test_extract_selected_members_only(archive: Path, tmp_path: Path):
    out = tmp_path / "part"
    am.extract_archive(archive, out, ["src/docs"])   # a directory takes its whole subtree
    assert (out / "src" / "docs" / "index.md").exists()
    assert (out / "src" / "docs" / "api" / "ref.html").exists()
    assert not (out / "src" / "readme.txt").exists()


def test_add_and_delete_members(archive: Path, tmp_path: Path):
    extra = tmp_path / "extra.txt"
    extra.write_text("added", encoding="utf-8")

    am.add_to_archive(archive, [extra], extra.parent, prefix="src/docs")
    assert "src/docs/extra.txt" in {e.path for e in am.list_archive(archive)}
    assert am.read_member(archive, "src/docs/extra.txt") == b"added"

    am.delete_from_archive(archive, ["src/docs/api"])
    names = {e.path for e in am.list_archive(archive)}
    assert "src/docs/api/ref.html" not in names
    assert "src/readme.txt" in names                 # the rest is untouched
    assert "src/docs/extra.txt" in names


def test_add_replaces_an_existing_name(archive: Path, tmp_path: Path):
    newer = tmp_path / "readme.txt"
    newer.write_text("replaced", encoding="utf-8")
    am.add_to_archive(archive, [newer], newer.parent, prefix="src")
    paths = [e.path for e in am.list_archive(archive)]
    assert paths.count("src/readme.txt") == 1        # not a duplicate entry
    assert am.read_member(archive, "src/readme.txt") == b"replaced"


def test_write_member_edits_in_place(archive: Path):
    am.write_member(archive, "src/docs/index.md", "# edited".encode())
    assert am.read_member(archive, "src/docs/index.md") == b"# edited"
    assert "src/readme.txt" in {e.path for e in am.list_archive(archive)}


def test_failed_rebuild_keeps_the_original(archive: Path, monkeypatch):
    """E5: a write that dies half way must not damage the archive on disk.

    ``normalise`` is called by every handler's rebuild path, whatever library it
    uses, so failing it aborts the rewrite in all formats alike.
    """
    before = archive.read_bytes()
    handler = am.get_handler(archive)
    calls = {"n": 0}
    original = ArchiveHandler.normalise

    def flaky(path: str) -> str:
        calls["n"] += 1
        if calls["n"] > 3:                           # let it start, then break
            raise RuntimeError("disk full")
        return original(path)

    monkeypatch.setattr(ArchiveHandler, "normalise", staticmethod(flaky))
    with pytest.raises(RuntimeError):
        handler.delete_members(archive, ["src/readme.txt"])
    monkeypatch.undo()
    assert archive.read_bytes() == before            # untouched
    assert not list(archive.parent.glob("*.uc-tmp"))  # and no leftover temp file


# ------------------------------------------------------------------ path traversal (X5)

@pytest.mark.parametrize("evil", [
    "../evil.txt", "../../evil.txt", "a/../../evil.txt",
    "/abs.txt", "C:/abs.txt", "C:\\abs.txt", "..\\evil.txt", "..",
])
def test_safe_target_rejects_escapes(tmp_path: Path, evil: str):
    assert safe_target(tmp_path / "dest", evil) is None


def test_safe_target_accepts_normal_names(tmp_path: Path):
    dest = tmp_path / "dest"
    dest.mkdir()
    assert safe_target(dest, "a/b/c.txt") == dest / "a" / "b" / "c.txt"
    assert safe_target(dest, "./a.txt") == dest / "a.txt"


def test_zip_extract_skips_traversal_members(tmp_path: Path):
    evil = tmp_path / "evil.zip"
    with zipfile.ZipFile(evil, "w") as zf:
        zf.writestr("../escaped.txt", "nope")
        zf.writestr("ok.txt", "fine")
    out = tmp_path / "out"
    am.extract_archive(evil, out)
    assert (out / "ok.txt").read_text() == "fine"
    assert not (tmp_path / "escaped.txt").exists()


def test_tar_extract_skips_traversal_members(tmp_path: Path):
    evil = tmp_path / "evil.tar"
    payload = tmp_path / "payload.txt"
    payload.write_text("nope")
    with tarfile.open(evil, "w") as tf:
        tf.add(str(payload), arcname="../escaped.txt")
        tf.add(str(payload), arcname="ok.txt")
    out = tmp_path / "out"
    am.extract_archive(evil, out)
    assert (out / "ok.txt").exists()
    assert not (tmp_path / "escaped.txt").exists()


# ------------------------------------------------------------------ single file (F3)

def test_single_file_gz(tmp_path: Path):
    import gzip

    gz = tmp_path / "dump.sql.gz"
    with gzip.open(gz, "wb") as f:
        f.write(b"select 1;")
    handler = am.get_handler(gz)
    assert isinstance(handler, SingleFileHandler)
    entries = am.list_archive(gz)
    assert [e.name for e in entries] == ["dump.sql"]
    assert am.read_member(gz, "dump.sql") == b"select 1;"

    am.write_member(gz, "dump.sql", b"select 2;")
    assert am.read_member(gz, "dump.sql") == b"select 2;"

    out = tmp_path / "out"
    am.extract_archive(gz, out)
    assert (out / "dump.sql").read_bytes() == b"select 2;"


def test_tar_gz_is_a_tar_not_a_single_file(tmp_path: Path, tree: Path):
    out = tmp_path / "pack.tar.gz"
    am.create_archive(out, [tree], base_dir=tree.parent)
    assert isinstance(am.get_handler(out), TarHandler)


# ------------------------------------------------------------------ virtual tree (B2, B4)

def test_listdir_synthesises_directories(archive: Path):
    root = listdir(ArchiveLocation(archive))
    assert [e.name for e in root] == ["src"]
    assert root[0].is_dir

    src = listdir(ArchiveLocation(archive, "src"))
    assert [e.name for e in src] == ["docs", "příloha č. 1.txt", "readme.txt"]
    assert src[0].is_dir and not src[1].is_dir

    api = listdir(ArchiveLocation(archive, "src/docs/api"))
    assert [(e.name, e.is_dir) for e in api] == [("ref.html", False)]
    assert api[0].size == len("<h1>ref</h1>")


def test_listdir_without_any_directory_entries(tmp_path: Path):
    flat = tmp_path / "flat.zip"
    with zipfile.ZipFile(flat, "w") as zf:          # only files, no directory records
        zf.writestr("a/b/c.txt", "x")
        zf.writestr("a/d.txt", "y")
    assert [e.name for e in listdir(ArchiveLocation(flat))] == ["a"]
    a = listdir(ArchiveLocation(flat, "a"))
    assert [(e.name, e.is_dir) for e in a] == [("b", True), ("d.txt", False)]


def test_listdir_unknown_directory_raises(archive: Path):
    with pytest.raises(KeyError):
        listdir(ArchiveLocation(archive, "nope"))


def test_location_navigation(archive: Path):
    loc = ArchiveLocation(archive).child("src").child("docs")
    assert loc.inner == "src/docs"
    assert str(loc) == str(archive / "src" / "docs")
    assert loc.parent().inner == "src"
    assert loc.parent().parent().is_root
    assert loc.parent().parent().parent() is None   # B3: root leads back to disk


def test_split_archive_path(archive: Path, tmp_path: Path, tree: Path):
    loc = split_archive_path(str(archive / "src" / "docs"))
    assert loc is not None and loc.archive == archive and loc.inner == "src/docs"

    root = split_archive_path(str(archive))
    assert root is not None and root.is_root

    assert split_archive_path(str(tree)) is None            # a real directory
    assert split_archive_path(str(tree / "readme.txt")) is None   # a plain file
    assert split_archive_path(str(tmp_path / "nope.zip" / "x")) is None


# ------------------------------------------------------------------ cache (B8)

def test_listing_is_cached_until_the_archive_changes(archive: Path, tmp_path: Path, monkeypatch):
    am.invalidate()
    calls = []
    handler = am.get_handler(archive)
    original = type(handler).list_contents

    def counted(self, path, password=None):
        calls.append(path)
        return original(self, path, password)

    monkeypatch.setattr(type(handler), "list_contents", counted)
    am.list_archive(archive)
    am.list_archive(archive)
    assert len(calls) == 1                                   # second call served from cache

    extra = tmp_path / "new.txt"
    extra.write_text("x")
    am.add_to_archive(archive, [extra], extra.parent)        # a write invalidates it
    am.list_archive(archive)
    assert len(calls) == 2
    assert "new.txt" in {e.path for e in am.list_archive(archive)}


# ------------------------------------------------------------------ read-only formats (F7)

def test_read_only_format_refuses_writes(tmp_path: Path):
    from src.archive.rar_handler import RarHandler

    rar = RarHandler()
    assert not rar.writable and not rar.creatable
    with pytest.raises(ArchiveError):
        rar.add_files(tmp_path / "x.rar", [])
    with pytest.raises(ArchiveError):
        rar.delete_members(tmp_path / "x.rar", ["a"])
