"""RAR handler – read only (rarfile + an unrar binary, both optional).

RAR is a proprietary format: there is no free writer, so ``writable`` stays
False and the GUI hides "add" / "delete" for it (requirement F7).
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from .base import ArchiveEntry, ArchiveError, ArchiveHandler, PasswordRequired

logger = logging.getLogger(__name__)

try:
    import rarfile
    _HAS_RARFILE = True
except ImportError:
    _HAS_RARFILE = False


class RarHandler(ArchiveHandler):
    """RAR: listing, reading and extracting only."""

    writable = False
    creatable = False

    @property
    def format_name(self) -> str:
        return "RAR"

    @property
    def extensions(self) -> list[str]:
        return ["rar"]

    def can_handle(self, path: Path) -> bool:
        if not _HAS_RARFILE:
            return False
        try:
            return bool(rarfile.is_rarfile(str(path)))
        except Exception:
            return False

    def _open(self, archive_path: Path):
        if not _HAS_RARFILE:
            raise ArchiveError("RAR support needs the rarfile package and an unrar tool")
        try:
            return rarfile.RarFile(str(archive_path))
        except rarfile.PasswordRequired as exc:                     # type: ignore[attr-defined]
            raise PasswordRequired(f"{archive_path.name} is password protected") from exc
        except rarfile.NeedFirstVolume as exc:                      # type: ignore[attr-defined]
            raise ArchiveError("This is not the first volume of the archive") from exc
        except rarfile.Error as exc:
            raise ArchiveError(f"Cannot open RAR archive: {exc}") from exc

    def list_contents(self, archive_path: Path) -> list[ArchiveEntry]:
        entries: list[ArchiveEntry] = []
        with self._open(archive_path) as rf:
            for info in rf.infolist():
                name = self.normalise(info.filename)
                if not name:
                    continue
                mtime = getattr(info, "mtime", None)
                entries.append(
                    ArchiveEntry(
                        name=name.rsplit("/", 1)[-1],
                        path=name,
                        size=info.file_size,
                        compressed_size=info.compress_size or 0,
                        modified=mtime or datetime.fromtimestamp(0),
                        is_dir=bool(info.is_dir()),
                        crc=getattr(info, "CRC", 0) or 0,
                    )
                )
        return entries

    def read_member(self, archive_path: Path, member_path: str) -> bytes:
        wanted = self.normalise(member_path)
        with self._open(archive_path) as rf:
            for info in rf.infolist():
                if self.normalise(info.filename) == wanted:
                    return rf.read(info)
        raise ArchiveError(f"{member_path} not found in the archive")

    def extract(
        self,
        archive_path: Path,
        destination: Path,
        members: list[str] | None = None,
    ) -> None:
        from .extract import safe_target

        with self._open(archive_path) as rf:
            for info in rf.infolist():
                name = self.normalise(info.filename)
                if not name or (members is not None and not self.is_below(name, members)):
                    continue
                target = safe_target(destination, name)
                if target is None:
                    continue
                if info.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with rf.open(info) as src, open(target, "wb") as dst:
                    while chunk := src.read(1 << 20):
                        dst.write(chunk)

    def create(self, archive_path: Path, sources: list[Path],
               base_dir: Path | None = None, level: int | None = None) -> None:
        raise ArchiveError("RAR archives cannot be created")

    def add_files(self, archive_path: Path, sources: list[Path],
                  base_dir: Path | None = None, prefix: str = "") -> None:
        raise ArchiveError("RAR archives cannot be modified")
