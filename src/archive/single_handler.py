"""Plain .gz / .bz2 / .xz – a single compressed file, not a container.

Total Commander lets you step into ``backup.sql.gz`` and see one entry,
``backup.sql``. That is what this handler provides; ``file.tar.gz`` is a TAR
and is claimed by TarHandler first.
"""

from __future__ import annotations

import bz2
import gzip
import lzma
from datetime import datetime
from pathlib import Path

from .base import ArchiveEntry, ArchiveError, ArchiveHandler

_MAGIC = {
    b"\x1f\x8b": "gz",
    b"BZh": "bz2",
    b"\xfd7zXZ\x00": "xz",
}
_OPEN = {"gz": gzip.open, "bz2": bz2.open, "xz": lzma.open}
_EXT = {".gz": "gz", ".gzip": "gz", ".bz2": "bz2", ".bz": "bz2", ".xz": "xz", ".lzma": "xz"}


class SingleFileHandler(ArchiveHandler):
    """One compressed file. Writable in the sense that saving the member back
    rewrites the whole file – which is all such an archive holds."""

    writable = True
    creatable = True

    @property
    def format_name(self) -> str:
        return "GZ/BZ2/XZ"

    @property
    def extensions(self) -> list[str]:
        return ["gz", "bz2", "xz"]

    @staticmethod
    def compression_of(path: Path) -> str:
        try:
            with open(path, "rb") as f:
                head = f.read(6)
        except OSError:
            return ""
        for magic, comp in _MAGIC.items():
            if head.startswith(magic):
                return comp
        return _EXT.get(path.suffix.lower(), "")

    def can_handle(self, path: Path) -> bool:
        import tarfile

        if not self.compression_of(path):
            return False
        try:
            if tarfile.is_tarfile(str(path)):
                return False                  # a compressed tar belongs to TarHandler
        except (OSError, tarfile.TarError):
            pass
        return True

    def _member_name(self, archive_path: Path) -> str:
        """Name of the single entry: the archive name without its suffix."""
        name = archive_path.name
        for suffix in sorted(_EXT, key=len, reverse=True):
            if name.lower().endswith(suffix):
                return name[: -len(suffix)] or "data"
        return name + ".out"

    def _read_all(self, archive_path: Path) -> bytes:
        comp = self.compression_of(archive_path)
        if not comp:
            raise ArchiveError(f"{archive_path.name} is not a gz / bz2 / xz file")
        try:
            with _OPEN[comp](str(archive_path), "rb") as f:        # type: ignore[operator]
                return f.read()
        except (OSError, EOFError, lzma.LZMAError) as exc:
            raise ArchiveError(f"Damaged archive: {exc}") from exc

    def list_contents(self, archive_path: Path, password: str | None = None) -> list[ArchiveEntry]:
        data = self._read_all(archive_path)
        stat = archive_path.stat()
        return [
            ArchiveEntry(
                name=self._member_name(archive_path),
                path=self._member_name(archive_path),
                size=len(data),
                compressed_size=stat.st_size,
                modified=datetime.fromtimestamp(stat.st_mtime),
                is_dir=False,
            )
        ]

    def read_member(self, archive_path: Path, member_path: str,
                    password: str | None = None) -> bytes:
        if self.normalise(member_path) != self._member_name(archive_path):
            raise ArchiveError(f"{member_path} not found in the archive")
        return self._read_all(archive_path)

    def extract(
        self,
        archive_path: Path,
        destination: Path,
        members: list[str] | None = None,
        password: str | None = None,
    ) -> None:
        from .extract import safe_target

        name = self._member_name(archive_path)
        if members is not None and not self.is_below(name, members):
            return
        target = safe_target(destination, name)
        if target is None:
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(self._read_all(archive_path))

    def _write_all(self, archive_path: Path, data: bytes) -> None:
        comp = self.compression_of(archive_path) or _EXT.get(archive_path.suffix.lower(), "gz")
        with self.rebuilt_archive(archive_path) as tmp:
            with _OPEN[comp](str(tmp), "wb") as f:                 # type: ignore[operator]
                f.write(data)

    def create(
        self,
        archive_path: Path,
        sources: list[Path],
        base_dir: Path | None = None,
        level: int | None = None,
        password: str | None = None,
    ) -> None:
        if password:
            raise ArchiveError("gz / bz2 / xz cannot be encrypted – use 7z for that")
        files = [s for s in sources if s.is_file()]
        if len(files) != 1 or len(sources) != 1:
            raise ArchiveError("gz / bz2 / xz can hold exactly one file – use .tar.gz instead")
        self._write_all(archive_path, files[0].read_bytes())

    def add_files(
        self,
        archive_path: Path,
        sources: list[Path],
        base_dir: Path | None = None,
        prefix: str = "",
        password: str | None = None,
    ) -> None:
        raise ArchiveError("gz / bz2 / xz holds a single file – nothing can be added")

    def write_member(self, archive_path: Path, member_path: str, data: bytes,
                     password: str | None = None) -> None:
        if self.normalise(member_path) != self._member_name(archive_path):
            raise ArchiveError(f"{member_path} is not the file stored in this archive")
        self._write_all(archive_path, data)
