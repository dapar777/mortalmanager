"""Archive jobs: extract, compress, add to and delete from an archive.

Every function runs in the job queue's executor (never on the GUI thread) and
reports progress per file, so a large archive can be followed and cancelled.
``cancelled()`` is polled between files – an archive library cannot be
interrupted in the middle of a single member.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Callable

from src.archive import archive_manager as am
from src.archive.base import ArchiveHandler
from src.archive.vfs import members_below
from src.core.file_model import OperationProgress

logger = logging.getLogger(__name__)

ProgressCb = Callable[[OperationProgress], None]
Cancelled = Callable[[], bool]


def _noop() -> bool:
    return False


def extract(
    archive: Path,
    destination: Path,
    members: list[str] | None,
    progress: ProgressCb | None = None,
    cancelled: Cancelled = _noop,
    strip_prefix: str = "",
    op_id: str = "",
    overwrite: bool = True,
) -> tuple[int, int, list[str]]:
    """Extract *members* of *archive* into *destination*.

    *strip_prefix* is the directory inside the archive the user is looking at:
    extracting ``docs/api/ref.html`` from inside ``docs`` writes ``api/ref.html``,
    the way Total Commander does it (requirement X3).

    Returns (files, bytes, errors); a failed member is logged and skipped (X7).
    """
    from src.archive.extract import safe_target

    handler = am.get_handler(archive)
    if handler is None:
        raise am.ArchiveError(f"Unsupported archive format: {archive.name}")

    entries = am.list_archive(archive)
    wanted = members_below(entries, members) if members else [e.path for e in entries]
    selected = [e for e in entries if e.path in set(wanted)]
    total_bytes = sum(e.size for e in selected if not e.is_dir)
    prefix = ArchiveHandler.normalise(strip_prefix)

    files = done = skipped = 0
    errors: list[str] = []
    destination.mkdir(parents=True, exist_ok=True)

    for entry in selected:
        if cancelled():
            break
        name = entry.path
        rel = name[len(prefix) + 1:] if prefix and name.startswith(prefix + "/") else name
        target = safe_target(destination, rel)
        if target is None:
            errors.append(f"{name}: unsafe path, skipped")
            continue
        try:
            if entry.is_dir:
                target.mkdir(parents=True, exist_ok=True)
                continue
            if progress is not None:
                progress(OperationProgress(
                    operation_id=op_id, description=f"Extracting {archive.name}",
                    current_file=entry.name, bytes_done=done, bytes_total=total_bytes,
                    files_done=files, files_total=len(selected),
                ))
            if target.exists() and not overwrite:
                skipped += 1                            # X6: keep what is on disk
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(handler.read_member(archive, name))
            files += 1
            done += entry.size
        except Exception as exc:
            logger.warning("Extracting %s from %s failed: %s", name, archive, exc)
            errors.append(f"{entry.name}: {exc}")
    if skipped:
        errors.append(f"{skipped} existing file(s) kept")
    return files, done, errors


def compress(
    archive: Path,
    sources: list[Path],
    base_dir: Path | None,
    progress: ProgressCb | None = None,
    cancelled: Cancelled = _noop,
    level: int | None = None,
    store_paths: bool = True,
    append: bool = False,
    op_id: str = "",
) -> tuple[int, int, list[str]]:
    """Pack *sources* into *archive*.

    *store_paths* False flattens everything to plain file names (the TC option).
    *append* adds to an existing archive instead of replacing it (C6).
    """
    handler = am.get_handler(archive) if (append and archive.exists()) else am.handler_for_new(archive)
    if handler is None:
        raise am.ArchiveError(f"Cannot create an archive named {archive.name}")
    if append and not handler.writable:
        raise am.ArchiveError(f"{handler.format_name} archives cannot be modified")

    files = [p for p in ArchiveHandler.walk_sources(sources)]
    total_bytes = sum(p.stat().st_size for p in files if p.is_file())
    if progress is not None and files:
        progress(OperationProgress(
            operation_id=op_id, description=f"Packing {archive.name}",
            current_file=files[0].name, bytes_done=0, bytes_total=total_bytes,
            files_done=0, files_total=len(files),
        ))

    root = base_dir if store_paths else None
    errors: list[str] = []
    if cancelled():
        return 0, 0, ["Cancelled"]
    try:
        if append and archive.exists():
            handler.add_files(archive, sources, root)
        else:
            handler.create(archive, sources, root, level)
    except Exception as exc:
        logger.exception("Compressing into %s failed", archive)
        raise am.ArchiveError(str(exc)) from exc
    am.invalidate(archive)

    if progress is not None:
        progress(OperationProgress(
            operation_id=op_id, description=f"Packing {archive.name}",
            current_file=archive.name, bytes_done=total_bytes, bytes_total=total_bytes,
            files_done=len(files), files_total=len(files), finished=True,
        ))
    return len(files), total_bytes, errors


def add_to(
    archive: Path,
    sources: list[Path],
    base_dir: Path | None,
    prefix: str = "",
    progress: ProgressCb | None = None,
    cancelled: Cancelled = _noop,
    op_id: str = "",
) -> tuple[int, int, list[str]]:
    """Add files into an open archive, under *prefix* (E1)."""
    files = list(ArchiveHandler.walk_sources(sources))
    total = sum(p.stat().st_size for p in files if p.is_file())
    if progress is not None and files:
        progress(OperationProgress(
            operation_id=op_id, description=f"Adding to {archive.name}",
            current_file=files[0].name, bytes_done=0, bytes_total=total,
            files_done=0, files_total=len(files),
        ))
    am.add_to_archive(archive, sources, base_dir, prefix)
    return len(files), total, []


def delete_from(
    archive: Path,
    members: list[str],
    progress: ProgressCb | None = None,
    cancelled: Cancelled = _noop,
    op_id: str = "",
) -> tuple[int, int, list[str]]:
    """Delete members from an archive (E2)."""
    if progress is not None:
        progress(OperationProgress(
            operation_id=op_id, description=f"Deleting from {archive.name}",
            current_file=Path(members[0]).name if members else "",
            files_done=0, files_total=len(members),
        ))
    am.delete_from_archive(archive, members)
    return len(members), 0, []


def unique_target(destination: Path, name: str) -> Path:
    """Free path for *name* in *destination* ("x.txt", "x (2).txt", …)."""
    target = destination / name
    if not target.exists():
        return target
    stem, suffix = os.path.splitext(name)
    for i in range(2, 1000):
        candidate = destination / f"{stem} ({i}){suffix}"
        if not candidate.exists():
            return candidate
    return target
