"""Abstract archive handler interface.

A handler is stateless: every call opens the archive itself, so nothing has to
be kept alive between a listing and an extraction. Paths inside an archive
always use ``/`` separators and never start with one.

Writing into an existing archive must never leave a half-written file behind,
so handlers that rebuild an archive (delete, replace) write a temporary file
next to the original and ``os.replace`` it at the end – see ``rebuilt_archive``.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterator


@dataclass
class ArchiveEntry:
    """A single entry inside an archive."""

    name: str
    path: str          # full path inside the archive ("/" separators, no leading "/")
    size: int
    compressed_size: int
    modified: datetime
    is_dir: bool
    crc: int = 0


class ArchiveError(Exception):
    """Archive could not be read or written (corrupt, unsupported, no library)."""


class PasswordRequired(ArchiveError):
    """The archive (or this member) is encrypted and needs a password."""


class ArchiveHandler(ABC):
    """Abstract base class for all archive format handlers."""

    #: can this format be modified (add / delete / replace members)?
    writable: bool = False
    #: can a new archive of this format be created?
    creatable: bool = False

    @property
    @abstractmethod
    def format_name(self) -> str:
        """Short name for the UI, e.g. "ZIP"."""

    @property
    @abstractmethod
    def extensions(self) -> list[str]:
        """Lower-case extensions this handler creates / recognises, e.g. ["zip"].

        Multi-part extensions ("tar.gz") are matched against the whole file name.
        """

    @abstractmethod
    def can_handle(self, path: Path) -> bool:
        """True if this handler can open *path*. Decided by content (magic bytes)
        wherever the library allows it, so a renamed archive is still recognised."""

    @abstractmethod
    def list_contents(self, archive_path: Path) -> list[ArchiveEntry]:
        """Every entry inside the archive."""

    @abstractmethod
    def extract(
        self,
        archive_path: Path,
        destination: Path,
        members: list[str] | None = None,
    ) -> None:
        """Extract *members* (or everything) into *destination*."""

    @abstractmethod
    def create(
        self,
        archive_path: Path,
        sources: list[Path],
        base_dir: Path | None = None,
        level: int | None = None,
    ) -> None:
        """Create a new archive from *sources* (directories go in recursively).

        Names inside the archive are relative to *base_dir* when given, otherwise
        just the file name.
        """

    @abstractmethod
    def add_files(
        self,
        archive_path: Path,
        sources: list[Path],
        base_dir: Path | None = None,
        prefix: str = "",
    ) -> None:
        """Add *sources* to an existing archive under *prefix* (a path inside it)."""

    def delete_members(self, archive_path: Path, members: list[str]) -> None:
        """Remove *members* (and everything below a directory member)."""
        raise ArchiveError(f"{self.format_name} archives cannot be modified")

    def read_member(self, archive_path: Path, member_path: str) -> bytes:
        """Read one member as bytes."""
        raise ArchiveError(f"{self.format_name} member reading is not supported")

    def write_member(self, archive_path: Path, member_path: str, data: bytes) -> None:
        """Replace (or add) one member with *data* – used by the F4 editor."""
        raise ArchiveError(f"{self.format_name} archives cannot be modified")

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def normalise(path: str) -> str:
        """Archive-internal path: ``/`` separators, no leading or trailing slash."""
        return path.replace("\\", "/").strip("/")

    @staticmethod
    def arc_name(src: Path, base_dir: Path | None, prefix: str = "") -> str:
        """Name *src* gets inside the archive."""
        if base_dir is not None:
            try:
                rel = src.relative_to(base_dir).as_posix()
            except ValueError:
                rel = src.name
        else:
            rel = src.name
        prefix = ArchiveHandler.normalise(prefix)
        return f"{prefix}/{rel}" if prefix else rel

    @staticmethod
    def walk_sources(sources: list[Path]) -> Iterator[Path]:
        """Every file of *sources*, directories expanded recursively (empty
        directories are yielded themselves so they survive into the archive)."""
        for src in sources:
            if src.is_dir():
                empty = True
                for child in sorted(src.rglob("*")):
                    empty = False
                    if child.is_file() or (child.is_dir() and not any(child.iterdir())):
                        yield child
                if empty:
                    yield src
            else:
                yield src

    @staticmethod
    def is_below(member: str, members: list[str]) -> bool:
        """True if *member* is one of *members* or lies inside one of them."""
        m = ArchiveHandler.normalise(member)
        for target in members:
            t = ArchiveHandler.normalise(target)
            if m == t or m.startswith(t + "/"):
                return True
        return False

    @staticmethod
    @contextmanager
    def rebuilt_archive(archive_path: Path) -> Iterator[Path]:
        """Write a replacement archive to a temporary file next to the original and
        swap it in only once the block finished – a crash leaves the original intact.

        Windows refuses to replace a file that is still open, and the caller is
        usually reading the original to copy entries across, so the block must be
        left (closing both archives) before ``commit`` is called::

            with handler.rebuilt_archive(path) as tmp:
                with open_source() as src, open_target(tmp) as dst:
                    ...
            # the archive is swapped in here
        """
        tmp = archive_path.with_name(archive_path.name + ".uc-tmp")
        ok = False
        try:
            yield tmp
            ok = True
        finally:
            if ok and tmp.exists():
                os.replace(str(tmp), str(archive_path))
            elif tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass
