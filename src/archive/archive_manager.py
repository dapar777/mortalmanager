"""Format detection, a cached listing and the operations the GUI calls.

Detection asks every handler whether it can open the file (magic bytes), so a
ZIP named ``.dat`` is still an archive; the extension only orders the candidates
and decides the format of a *new* archive.

Listings are cached per (path, mtime, size): walking into a directory inside an
archive must not read the archive again. Any write invalidates the entry.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

from .base import ArchiveEntry, ArchiveError, ArchiveHandler, PasswordRequired
from .rar_handler import RarHandler
from .sevenzip_handler import SevenZipHandler
from .single_handler import SingleFileHandler
from .tar_handler import TarHandler
from .zip_handler import ZipHandler

logger = logging.getLogger(__name__)

# order matters only for equal candidates: tar.gz must win over plain gz
_HANDLERS: list[ArchiveHandler] = [
    ZipHandler(),
    TarHandler(),
    SevenZipHandler(),
    RarHandler(),
    SingleFileHandler(),
]

#: extensions we even try to open – a 4 GB .iso must not be sniffed on every listing
ARCHIVE_EXTENSIONS: frozenset[str] = frozenset(
    ext for h in _HANDLERS for ext in h.extensions
) | {"jar", "war", "ear", "apk", "whl", "epub", "odt", "ods", "odp", "docx", "xlsx", "pptx"}

_CACHE_MAX = 16
_cache: dict[str, tuple[tuple[float, int], list[ArchiveEntry]]] = {}
_cache_lock = threading.Lock()


def looks_like_archive(path: Path | str) -> bool:
    """Cheap name test – does this file's extension suggest an archive?"""
    name = str(path).lower()
    return any(name.endswith("." + ext) for ext in ARCHIVE_EXTENSIONS)


def get_handler(path: Path) -> ArchiveHandler | None:
    """Handler that can open *path*, or None. Decided by content."""
    try:
        if not path.is_file():
            return None
    except OSError:
        return None
    for handler in _HANDLERS:
        try:
            if handler.can_handle(path):
                return handler
        except Exception as exc:
            logger.debug("%s rejected %s: %s", handler.format_name, path, exc)
    return None


def handler_for_new(path: Path) -> ArchiveHandler | None:
    """Handler that creates an archive named *path* (extension decides)."""
    name = path.name.lower()
    best: ArchiveHandler | None = None
    best_len = -1
    for handler in _HANDLERS:
        if not handler.creatable:
            continue
        for ext in handler.extensions:
            if name.endswith("." + ext) and len(ext) > best_len:
                best, best_len = handler, len(ext)
    return best


def is_archive(path: Path | str) -> bool:
    p = Path(path)
    return looks_like_archive(p) and get_handler(p) is not None


def _require(path: Path) -> ArchiveHandler:
    handler = get_handler(path)
    if handler is None:
        raise ArchiveError(f"Unsupported archive format: {path.name}")
    return handler


def _stamp(path: Path) -> tuple[float, int]:
    st = path.stat()
    return (st.st_mtime, st.st_size)


def invalidate(path: Path | str | None = None) -> None:
    """Forget the cached listing of *path* (or everything)."""
    with _cache_lock:
        if path is None:
            _cache.clear()
        else:
            _cache.pop(str(Path(path)).lower(), None)


def list_archive(path: Path, use_cache: bool = True) -> list[ArchiveEntry]:
    """All entries of the archive; cached per file stamp."""
    p = Path(path)
    key = str(p).lower()
    try:
        stamp = _stamp(p)
    except OSError as exc:
        raise ArchiveError(str(exc)) from exc
    if use_cache:
        with _cache_lock:
            hit = _cache.get(key)
        if hit is not None and hit[0] == stamp:
            return hit[1]
    entries = _require(p).list_contents(p)
    with _cache_lock:
        if len(_cache) >= _CACHE_MAX:
            _cache.pop(next(iter(_cache)))
        _cache[key] = (stamp, entries)
    return entries


def extract_archive(
    path: Path,
    destination: Path,
    members: list[str] | None = None,
) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    _require(path).extract(path, destination, members)


def create_archive(
    path: Path,
    sources: list[Path],
    base_dir: Path | None = None,
    level: int | None = None,
) -> None:
    handler = handler_for_new(path)
    if handler is None:
        raise ArchiveError(f"Cannot create an archive named {path.name}")
    handler.create(path, sources, base_dir, level)
    invalidate(path)


def add_to_archive(
    path: Path,
    sources: list[Path],
    base_dir: Path | None = None,
    prefix: str = "",
) -> None:
    handler = get_handler(path) if path.exists() else handler_for_new(path)
    if handler is None:
        raise ArchiveError(f"Unsupported archive format: {path.name}")
    if not handler.writable:
        raise ArchiveError(f"{handler.format_name} archives cannot be modified")
    handler.add_files(path, sources, base_dir, prefix)
    invalidate(path)


def delete_from_archive(path: Path, members: list[str]) -> None:
    handler = _require(path)
    if not handler.writable:
        raise ArchiveError(f"{handler.format_name} archives cannot be modified")
    handler.delete_members(path, members)
    invalidate(path)


def read_member(path: Path, member: str) -> bytes:
    return _require(path).read_member(path, member)


def write_member(path: Path, member: str, data: bytes) -> None:
    handler = _require(path)
    if not handler.writable:
        raise ArchiveError(f"{handler.format_name} archives cannot be modified")
    handler.write_member(path, member, data)
    invalidate(path)


def is_writable(path: Path) -> bool:
    handler = get_handler(path)
    return bool(handler and handler.writable)


def format_name(path: Path) -> str:
    handler = get_handler(path)
    return handler.format_name if handler else ""


__all__ = [
    "ARCHIVE_EXTENSIONS", "ArchiveEntry", "ArchiveError", "PasswordRequired",
    "add_to_archive", "create_archive", "delete_from_archive", "extract_archive",
    "format_name", "get_handler", "handler_for_new", "invalidate", "is_archive",
    "is_writable", "list_archive", "looks_like_archive", "read_member", "write_member",
]
