"""7-Zip archive handler using py7zr."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from .base import ArchiveEntry, ArchiveHandler

logger = logging.getLogger(__name__)

try:
    import py7zr
    _HAS_PY7ZR = True
except ImportError:
    _HAS_PY7ZR = False
    logger.warning("py7zr not installed; 7z support disabled")


class SevenZipHandler(ArchiveHandler):

    @property
    def extensions(self) -> list[str]:
        return ["7z", "7Z"]

    def can_handle(self, path: Path) -> bool:
        if not _HAS_PY7ZR:
            return False
        try:
            return py7zr.is_7zfile(str(path))
        except Exception:
            return False

    def list_contents(self, archive_path: Path) -> list[ArchiveEntry]:
        if not _HAS_PY7ZR:
            return []
        entries: list[ArchiveEntry] = []
        with py7zr.SevenZipFile(str(archive_path), mode="r") as zf:
            for info in zf.list():
                dt: datetime = info.creationtime or datetime.now()  # type: ignore[assignment]
                entries.append(
                    ArchiveEntry(
                        name=Path(info.filename).name,
                        path=self._normalise_path(info.filename),
                        size=info.uncompressed or 0,
                        compressed_size=info.compressed or 0,
                        modified=dt,
                        is_dir=info.is_directory,
                    )
                )
        return entries

    def extract(
        self,
        archive_path: Path,
        destination: Path,
        members: list[str] | None = None,
    ) -> None:
        if not _HAS_PY7ZR:
            raise RuntimeError("py7zr not installed")
        with py7zr.SevenZipFile(str(archive_path), mode="r") as zf:
            if members:
                zf.extract(path=str(destination), targets=members)
            else:
                zf.extractall(path=str(destination))

    def create(
        self,
        archive_path: Path,
        sources: list[Path],
        base_dir: Path | None = None,
    ) -> None:
        if not _HAS_PY7ZR:
            raise RuntimeError("py7zr not installed")
        with py7zr.SevenZipFile(str(archive_path), mode="w") as zf:
            for src in sources:
                if src.is_dir():
                    zf.writeall(str(src), src.name)
                else:
                    zf.write(str(src), src.name)

    def add_files(
        self, archive_path: Path, sources: list[Path], base_dir: Path | None = None
    ) -> None:
        if not _HAS_PY7ZR:
            raise RuntimeError("py7zr not installed")
        mode = "a" if archive_path.exists() else "w"
        with py7zr.SevenZipFile(str(archive_path), mode=mode) as zf:
            for src in sources:
                zf.write(str(src), src.name)

    def read_member(self, archive_path: Path, member_path: str) -> bytes:
        if not _HAS_PY7ZR:
            raise RuntimeError("py7zr not installed")
        with py7zr.SevenZipFile(str(archive_path), mode="r") as zf:
            data = zf.read([member_path])
            bio = data.get(member_path)
            if bio is None:
                raise KeyError(member_path)
            return bio.read()
