"""Archive manager – auto-detects format and delegates to the right handler."""

from __future__ import annotations

import logging
from pathlib import Path

from .base import ArchiveEntry, ArchiveHandler
from .zip_handler import ZipHandler
from .sevenzip_handler import SevenZipHandler
from .tar_handler import TarHandler

logger = logging.getLogger(__name__)

_HANDLERS: list[ArchiveHandler] = [
    ZipHandler(),
    SevenZipHandler(),
    TarHandler(),
]


def get_handler(path: Path) -> ArchiveHandler | None:
    """Return the appropriate handler for *path*, or None if unsupported."""
    for handler in _HANDLERS:
        try:
            if handler.can_handle(path):
                return handler
        except Exception:
            pass
    return None


def is_archive(path: Path) -> bool:
    return get_handler(path) is not None


def list_archive(path: Path) -> list[ArchiveEntry]:
    handler = get_handler(path)
    if handler is None:
        raise ValueError(f"Unsupported archive format: {path.suffix}")
    return handler.list_contents(path)


def extract_archive(
    path: Path, destination: Path, members: list[str] | None = None
) -> None:
    handler = get_handler(path)
    if handler is None:
        raise ValueError(f"Unsupported archive format: {path.suffix}")
    destination.mkdir(parents=True, exist_ok=True)
    handler.extract(path, destination, members)


def create_archive(
    path: Path, sources: list[Path], base_dir: Path | None = None
) -> None:
    ext = path.suffix.lstrip(".").lower()
    for handler in _HANDLERS:
        if ext in [e.lower() for e in handler.extensions]:
            handler.create(path, sources, base_dir)
            return
    raise ValueError(f"Cannot create archive with extension: {path.suffix}")
