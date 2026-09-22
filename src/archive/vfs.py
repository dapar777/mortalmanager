"""Archive as a folder tree: paths, listings and the ".." entry.

An archive stores flat names ("docs/api/index.html"); a file manager needs the
directory *docs* to exist even when the archive has no entry for it. ``listdir``
therefore synthesises the intermediate directories (requirement B4).

An archive location is written as the archive path plus the path inside it, the
way Total Commander does it::

    C:\\work\\src.zip            -> root of the archive
    C:\\work\\src.zip\\docs\\api  -> directory inside it

``split_archive_path`` recognises such a string by walking it from the left and
stopping at the first component that is a file on disk.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .archive_manager import list_archive, looks_like_archive
from .base import ArchiveEntry, ArchiveHandler


@dataclass(frozen=True)
class ArchiveLocation:
    """A directory inside an archive."""

    archive: Path       # the archive file on disk
    inner: str = ""     # path inside it ("" = root), "/" separators

    def __str__(self) -> str:
        inner = self.inner.replace("/", os.sep)
        return str(self.archive / inner) if inner else str(self.archive)

    @property
    def is_root(self) -> bool:
        return not self.inner

    def child(self, name: str) -> "ArchiveLocation":
        inner = f"{self.inner}/{name}" if self.inner else name
        return ArchiveLocation(self.archive, ArchiveHandler.normalise(inner))

    def parent(self) -> "ArchiveLocation | None":
        """Directory above; None at the archive root (the caller goes back to disk)."""
        if not self.inner:
            return None
        head = self.inner.rsplit("/", 1)[0] if "/" in self.inner else ""
        return ArchiveLocation(self.archive, head)


def split_archive_path(path: str) -> ArchiveLocation | None:
    """Split "C:\\a\\x.zip\\docs" into the archive and the path inside it.

    None when *path* does not lead through an archive file. Only names that look
    like an archive are stat-ed, so an ordinary deep path costs nothing.
    """
    p = Path(path)
    if p.is_dir():
        return None                                   # a real directory wins
    parts = list(p.parts)
    for i in range(len(parts), 0, -1):
        head = Path(*parts[:i])
        if not looks_like_archive(head):
            continue
        try:
            if not head.is_file():
                continue
        except OSError:
            continue
        inner = "/".join(parts[i:])
        return ArchiveLocation(head, ArchiveHandler.normalise(inner))
    return None


def _dir_entry(name: str, path: str, modified: datetime) -> ArchiveEntry:
    return ArchiveEntry(name=name, path=path, size=-1, compressed_size=0,
                        modified=modified, is_dir=True)


def listdir(location: ArchiveLocation, entries: list[ArchiveEntry] | None = None) -> list[ArchiveEntry]:
    """Direct children of *location*, with the intermediate directories synthesised.

    Raises KeyError when the inner path does not exist in the archive.
    """
    if entries is None:
        entries = list_archive(location.archive)
    prefix = f"{location.inner}/" if location.inner else ""
    files: dict[str, ArchiveEntry] = {}
    dirs: dict[str, ArchiveEntry] = {}
    inner_exists = not location.inner

    for entry in entries:
        path = ArchiveHandler.normalise(entry.path)
        if not path:
            continue
        if location.inner:
            if path == location.inner:
                inner_exists = True
                if not entry.is_dir:
                    return []                        # asked to list a file
                continue
            if not path.startswith(prefix):
                continue
            inner_exists = True
        rest = path[len(prefix):]
        if not rest:
            continue
        name, _, tail = rest.partition("/")
        if tail or entry.is_dir:
            # a directory: either an explicit entry or implied by a deeper file
            child_path = f"{prefix}{name}"
            existing = dirs.get(name)
            if existing is None or (not tail and entry.is_dir):
                dirs[name] = _dir_entry(name, child_path, entry.modified)
        else:
            files[name] = ArchiveEntry(
                name=name, path=path, size=entry.size,
                compressed_size=entry.compressed_size, modified=entry.modified,
                is_dir=False, crc=entry.crc,
            )

    if not inner_exists:
        raise KeyError(location.inner)
    out = sorted(dirs.values(), key=lambda e: e.name.lower())
    out += sorted(files.values(), key=lambda e: e.name.lower())
    return out


def members_below(entries: list[ArchiveEntry], targets: list[str]) -> list[str]:
    """Every stored member covered by *targets* (a directory takes its content)."""
    out: list[str] = []
    for entry in entries:
        if ArchiveHandler.is_below(entry.path, targets):
            out.append(entry.path)
    return out or list(targets)
