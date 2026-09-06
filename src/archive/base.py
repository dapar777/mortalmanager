"""Abstract archive handler interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass
class ArchiveEntry:
    """Represents a single entry inside an archive."""

    name: str
    path: str          # full path inside the archive (using / separators)
    size: int
    compressed_size: int
    modified: datetime
    is_dir: bool
    crc: int = 0


class ArchiveHandler(ABC):
    """Abstract base class for all archive format handlers."""

    @property
    @abstractmethod
    def extensions(self) -> list[str]:
        """File extensions handled by this handler, e.g. ['zip', 'ZIP']."""

    @abstractmethod
    def can_handle(self, path: Path) -> bool:
        """Return True if this handler can open the given file."""

    @abstractmethod
    def list_contents(self, archive_path: Path) -> list[ArchiveEntry]:
        """Return all entries inside the archive."""

    @abstractmethod
    def extract(
        self,
        archive_path: Path,
        destination: Path,
        members: list[str] | None = None,
    ) -> None:
        """Extract *members* (or all) to *destination*."""

    @abstractmethod
    def create(
        self,
        archive_path: Path,
        sources: list[Path],
        base_dir: Path | None = None,
    ) -> None:
        """Create a new archive from *sources*."""

    @abstractmethod
    def add_files(self, archive_path: Path, sources: list[Path], base_dir: Path | None = None) -> None:
        """Add files to an existing archive."""

    def read_member(self, archive_path: Path, member_path: str) -> bytes:
        """Read a single member from the archive as bytes."""
        raise NotImplementedError

    @staticmethod
    def _normalise_path(p: str) -> str:
        return p.replace("\\", "/")
