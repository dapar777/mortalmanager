"""TAR / GZ / BZ2 / XZ archive handler using stdlib tarfile."""

from __future__ import annotations

import tarfile
from datetime import datetime
from pathlib import Path

from .base import ArchiveEntry, ArchiveHandler


class TarHandler(ArchiveHandler):

    @property
    def extensions(self) -> list[str]:
        return ["tar", "tar.gz", "tgz", "tar.bz2", "tbz2", "tar.xz", "txz"]

    def can_handle(self, path: Path) -> bool:
        return tarfile.is_tarfile(str(path))

    def _open_mode(self, path: Path) -> str:
        name = path.name.lower()
        if name.endswith((".tar.gz", ".tgz")):
            return "r:gz"
        if name.endswith((".tar.bz2", ".tbz2")):
            return "r:bz2"
        if name.endswith((".tar.xz", ".txz")):
            return "r:xz"
        return "r:*"

    def list_contents(self, archive_path: Path) -> list[ArchiveEntry]:
        entries: list[ArchiveEntry] = []
        with tarfile.open(str(archive_path), self._open_mode(archive_path)) as tf:
            for member in tf.getmembers():
                dt = datetime.fromtimestamp(member.mtime) if member.mtime else datetime.now()
                entries.append(
                    ArchiveEntry(
                        name=Path(member.name).name or member.name,
                        path=self._normalise_path(member.name),
                        size=member.size,
                        compressed_size=member.size,
                        modified=dt,
                        is_dir=member.isdir(),
                    )
                )
        return entries

    def extract(
        self,
        archive_path: Path,
        destination: Path,
        members: list[str] | None = None,
    ) -> None:
        with tarfile.open(str(archive_path), self._open_mode(archive_path)) as tf:
            if members:
                for m in members:
                    tf.extract(m, str(destination), filter="data")
            else:
                tf.extractall(str(destination), filter="data")

    def create(
        self,
        archive_path: Path,
        sources: list[Path],
        base_dir: Path | None = None,
    ) -> None:
        name = archive_path.name.lower()
        if name.endswith((".tar.gz", ".tgz")):
            mode = "w:gz"
        elif name.endswith((".tar.bz2", ".tbz2")):
            mode = "w:bz2"
        elif name.endswith((".tar.xz", ".txz")):
            mode = "w:xz"
        else:
            mode = "w"

        with tarfile.open(str(archive_path), mode) as tf:
            for src in sources:
                arcname = str(src.relative_to(base_dir)) if base_dir else src.name
                tf.add(str(src), arcname=arcname, recursive=True)

    def add_files(
        self, archive_path: Path, sources: list[Path], base_dir: Path | None = None
    ) -> None:
        mode = "a" if archive_path.exists() else "w"
        with tarfile.open(str(archive_path), mode) as tf:
            for src in sources:
                arcname = str(src.relative_to(base_dir)) if base_dir else src.name
                tf.add(str(src), arcname=arcname)

    def read_member(self, archive_path: Path, member_path: str) -> bytes:
        with tarfile.open(str(archive_path), self._open_mode(archive_path)) as tf:
            f = tf.extractfile(member_path)
            if f is None:
                raise KeyError(member_path)
            return f.read()
