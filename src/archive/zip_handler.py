"""ZIP archive handler."""

from __future__ import annotations

import zipfile
from datetime import datetime
from pathlib import Path

from .base import ArchiveEntry, ArchiveHandler


class ZipHandler(ArchiveHandler):

    @property
    def extensions(self) -> list[str]:
        return ["zip", "ZIP"]

    def can_handle(self, path: Path) -> bool:
        return zipfile.is_zipfile(str(path))

    def list_contents(self, archive_path: Path) -> list[ArchiveEntry]:
        entries: list[ArchiveEntry] = []
        with zipfile.ZipFile(str(archive_path), "r") as zf:
            for info in zf.infolist():
                dt = datetime(*info.date_time)
                entries.append(
                    ArchiveEntry(
                        name=Path(info.filename).name or info.filename,
                        path=self._normalise_path(info.filename),
                        size=info.file_size,
                        compressed_size=info.compress_size,
                        modified=dt,
                        is_dir=info.is_dir(),
                        crc=info.CRC,
                    )
                )
        return entries

    def extract(
        self,
        archive_path: Path,
        destination: Path,
        members: list[str] | None = None,
    ) -> None:
        with zipfile.ZipFile(str(archive_path), "r") as zf:
            if members:
                for m in members:
                    zf.extract(m, str(destination))
            else:
                zf.extractall(str(destination))

    def create(
        self,
        archive_path: Path,
        sources: list[Path],
        base_dir: Path | None = None,
    ) -> None:
        with zipfile.ZipFile(str(archive_path), "w", zipfile.ZIP_DEFLATED) as zf:
            for src in sources:
                if src.is_dir():
                    for f in src.rglob("*"):
                        arcname = (
                            str(f.relative_to(base_dir)) if base_dir else f.name
                        )
                        zf.write(str(f), arcname)
                else:
                    arcname = (
                        str(src.relative_to(base_dir)) if base_dir else src.name
                    )
                    zf.write(str(src), arcname)

    def add_files(
        self, archive_path: Path, sources: list[Path], base_dir: Path | None = None
    ) -> None:
        mode = "a" if archive_path.exists() else "w"
        with zipfile.ZipFile(str(archive_path), mode, zipfile.ZIP_DEFLATED) as zf:
            for src in sources:
                arcname = str(src.relative_to(base_dir)) if base_dir else src.name
                zf.write(str(src), arcname)

    def read_member(self, archive_path: Path, member_path: str) -> bytes:
        with zipfile.ZipFile(str(archive_path), "r") as zf:
            return zf.read(member_path)
