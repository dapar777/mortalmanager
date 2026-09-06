"""Abstract filesystem provider interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator, Callable
from typing import Protocol

from src.core.file_model import DriveInfo, FileEntry, OperationProgress, SearchQuery


class ProgressCallback(Protocol):
    def __call__(self, progress: OperationProgress) -> None: ...


class FileSystemProvider(ABC):
    """Abstract base class for all filesystem providers (local, FTP, archive...)."""

    # ------------------------------------------------------------------ identity

    @property
    @abstractmethod
    def provider_id(self) -> str:
        """Unique identifier, e.g. 'local', 'ftp:session1', 'archive:/path/to/file.zip'."""

    @property
    @abstractmethod
    def root(self) -> str:
        """Filesystem root path / URL."""

    # ------------------------------------------------------------------ listing

    @abstractmethod
    async def list_directory(
        self, path: str, show_hidden: bool = False
    ) -> list[FileEntry]:
        """List contents of *path*. Raises FileNotFoundError if absent."""

    @abstractmethod
    async def get_entry(self, path: str) -> FileEntry:
        """Return a single FileEntry for *path*."""

    # ------------------------------------------------------------------ navigation

    @abstractmethod
    def get_parent(self, path: str) -> str | None:
        """Return parent path string, or None if already at root."""

    @abstractmethod
    def path_exists(self, path: str) -> bool: ...

    @abstractmethod
    def is_directory(self, path: str) -> bool: ...

    # ------------------------------------------------------------------ drives

    def get_drives(self) -> list[DriveInfo]:
        """Return available drives. Override in providers that expose drives."""
        return []

    # ------------------------------------------------------------------ operations

    @abstractmethod
    async def copy(
        self,
        sources: list[str],
        destination: str,
        callback: ProgressCallback | None = None,
        overwrite: bool = False,
    ) -> None: ...

    @abstractmethod
    async def move(
        self,
        sources: list[str],
        destination: str,
        callback: ProgressCallback | None = None,
        overwrite: bool = False,
    ) -> None: ...

    @abstractmethod
    async def delete(
        self,
        paths: list[str],
        use_trash: bool = True,
        callback: ProgressCallback | None = None,
    ) -> None: ...

    @abstractmethod
    async def rename(self, old_path: str, new_path: str) -> None: ...

    @abstractmethod
    async def mkdir(self, path: str) -> None: ...

    @abstractmethod
    async def create_file(self, path: str) -> None: ...

    # ------------------------------------------------------------------ search

    @abstractmethod
    async def search(
        self, root: str, query: SearchQuery
    ) -> AsyncGenerator[FileEntry, None]:
        """Async generator that yields matching FileEntry objects."""
        # This is a stub; concrete class must implement properly.
        return
        yield  # make this an async generator

    # ------------------------------------------------------------------ read / write

    async def read_bytes(self, path: str) -> bytes:
        raise NotImplementedError

    async def write_bytes(self, path: str, data: bytes) -> None:
        raise NotImplementedError

    async def read_text(self, path: str, encoding: str = "utf-8") -> str:
        data = await self.read_bytes(path)
        return data.decode(encoding)

    async def write_text(
        self, path: str, text: str, encoding: str = "utf-8"
    ) -> None:
        await self.write_bytes(path, text.encode(encoding))

    # ------------------------------------------------------------------ metadata

    async def get_total_size(self, paths: list[str]) -> int:
        """Recursively calculate total size for *paths*."""
        return 0

    def supports_attributes(self) -> bool:
        return False

    def supports_permissions(self) -> bool:
        return False
