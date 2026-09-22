"""Archive listing as panel entries, and the temp files the viewer / editor need.

The panel works with ``FileEntry`` objects whose ``full_path`` is the single
source of truth for a path. Inside an archive that path is the Total Commander
form ``C:\\work\\src.zip\\docs\\index.md``: ``split_archive_path`` turns it back
into (archive, inner path), so entering, going up and the path field all work
without the panel carrying a second notion of "where am I".
"""

from __future__ import annotations

import logging
import shutil
import tempfile
from datetime import datetime
from pathlib import Path

from src.archive import archive_manager as am
from src.archive.base import ArchiveEntry
from src.archive.vfs import ArchiveLocation, listdir, split_archive_path
from src.core.file_model import FileEntry

logger = logging.getLogger(__name__)

_DIR_ATTR = 0x10
_FILE_ATTR = 0x20
_READONLY = 0x01


def to_file_entry(entry: ArchiveEntry, location: ArchiveLocation, writable: bool) -> FileEntry:
    """One archive entry as a panel row."""
    full = Path(str(location)) / entry.name
    attrs = _DIR_ATTR if entry.is_dir else _FILE_ATTR
    if not writable:
        attrs |= _READONLY
    return FileEntry(
        name=entry.name,
        path=full,
        size=-1 if entry.is_dir else entry.size,
        modified=entry.modified,
        created=entry.modified,
        is_dir=entry.is_dir,
        is_symlink=False,
        attributes=attrs,
        extension="" if entry.is_dir else Path(entry.name).suffix.lstrip("."),
    )


def parent_entry(location: ArchiveLocation) -> FileEntry:
    """The ".." row: one level up inside the archive, or back to the folder
    holding the archive when we are at its root (requirement B3)."""
    up = location.parent()
    target = Path(str(up)) if up is not None else location.archive.parent
    now = datetime.now()
    return FileEntry(
        name="..", path=target, size=-1, modified=now, created=now,
        is_dir=True, is_symlink=False, attributes=_DIR_ATTR, is_parent=True,
    )


def list_location(location: ArchiveLocation) -> list[FileEntry]:
    """Panel rows for a directory inside an archive, ".." first."""
    writable = am.is_writable(location.archive)
    rows = [parent_entry(location)]
    rows += [to_file_entry(e, location, writable) for e in listdir(location)]
    return rows


def location_of(path: str) -> ArchiveLocation | None:
    """Archive location *path* points into, or None for an ordinary path."""
    return split_archive_path(path)


def inner_path(entry: FileEntry, location: ArchiveLocation) -> str:
    """Path of *entry* inside its archive ("src/docs/index.md")."""
    return location.child(entry.name).inner


class TempExtracts:
    """Members extracted for the viewer (F3) and the editor (F4).

    Files live in one directory that is removed when the window closes (E6);
    each member keeps its own subdirectory so two archives can hold the same
    name. ``pending_writes`` lets the panel find out which temp file belongs to
    which archive member when the editor saves it back (E4).
    """

    def __init__(self) -> None:
        self._root: Path | None = None
        self._members: dict[str, tuple[Path, str]] = {}   # temp path -> (archive, member)

    @property
    def root(self) -> Path:
        if self._root is None:
            self._root = Path(tempfile.mkdtemp(prefix="uc-archive-"))
        return self._root

    def extract(self, archive: Path, member: str) -> Path:
        """Write one member into the temp area and return its path."""
        slot = self.root / f"{abs(hash((str(archive).lower(), member))):x}"
        slot.mkdir(parents=True, exist_ok=True)
        target = slot / Path(member).name
        target.write_bytes(am.read_member(archive, member))
        self._members[str(target).lower()] = (archive, member)
        return target

    def source_of(self, temp_path: str | Path) -> tuple[Path, str] | None:
        """(archive, member) the temp file came from, or None."""
        return self._members.get(str(temp_path).lower())

    def save_back(self, temp_path: str | Path) -> tuple[Path, str] | None:
        """Write an edited temp file back into its archive; returns what was written."""
        origin = self.source_of(temp_path)
        if origin is None:
            return None
        archive, member = origin
        am.write_member(archive, member, Path(temp_path).read_bytes())
        return origin

    def changed(self, temp_path: str | Path) -> bool:
        """True if the temp file differs from what the archive holds."""
        origin = self.source_of(temp_path)
        if origin is None:
            return False
        archive, member = origin
        try:
            return Path(temp_path).read_bytes() != am.read_member(archive, member)
        except Exception:
            return False

    def cleanup(self) -> None:
        if self._root is not None:
            shutil.rmtree(self._root, ignore_errors=True)
            self._root = None
            self._members.clear()
