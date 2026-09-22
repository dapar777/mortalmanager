"""7-Zip handler (py7zr, optional dependency)."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from .base import ArchiveEntry, ArchiveError, ArchiveHandler, PasswordRequired, WrongPassword

logger = logging.getLogger(__name__)

try:
    import py7zr
    _HAS_PY7ZR = True
except ImportError:                      # optional: without it 7z simply is not offered
    _HAS_PY7ZR = False


class SevenZipHandler(ArchiveHandler):
    """7z through py7zr. Without the library the format is reported as unsupported
    instead of raising, so the rest of the manager keeps working."""

    writable = True
    creatable = True
    supports_password = True
    can_encrypt = True          # py7zr writes AES-256 encrypted archives

    @property
    def format_name(self) -> str:
        return "7z"

    @property
    def extensions(self) -> list[str]:
        return ["7z"]

    def can_handle(self, path: Path) -> bool:
        if not _HAS_PY7ZR:
            return False
        try:
            return bool(py7zr.is_7zfile(str(path)))
        except Exception:
            return False

    def _check(self) -> None:
        if not _HAS_PY7ZR:
            raise ArchiveError("7z support needs the py7zr package (pip install py7zr)")

    def needs_password(self, archive_path: Path) -> bool:
        """7z can encrypt the header itself, so opening may already fail."""
        if not _HAS_PY7ZR:
            return False
        try:
            with py7zr.SevenZipFile(str(archive_path), mode="r") as zf:
                return bool(zf.needs_password())
        except Exception:
            return True             # header encrypted: it cannot even be opened

    def _open(self, archive_path: Path, mode: str = "r", password: str | None = None):
        self._check()
        try:
            return py7zr.SevenZipFile(str(archive_path), mode=mode, password=password or None)
        except py7zr.PasswordRequired as exc:                       # type: ignore[attr-defined]
            if password:
                raise WrongPassword(f"Wrong password for {archive_path.name}") from exc
            raise PasswordRequired(f"{archive_path.name} is password protected") from exc
        except py7zr.Bad7zFile as exc:
            if password:
                # a wrong password on an encrypted header looks like a damaged file
                raise WrongPassword(f"Wrong password for {archive_path.name}") from exc
            raise ArchiveError(f"Damaged 7z archive: {exc}") from exc

    @staticmethod
    def _decrypt_error(exc: Exception, archive_path: Path, password: str | None) -> Exception:
        """Map py7zr's decryption failures onto ours.

        The library only notices the encryption when it reaches the data, so both
        "no password" and "wrong password" surface here rather than on open: its
        own PasswordRequired for a missing one, a codec error for a wrong one.
        """
        if isinstance(exc, py7zr.PasswordRequired) and not password:     # type: ignore[attr-defined]
            return PasswordRequired(f"{archive_path.name} is password protected")
        if password and exc.__class__.__name__ in (
                "LZMAError", "CrcError", "Bad7zFile", "PasswordRequired",
                "UnsupportedCompressionMethodError"):
            return WrongPassword(f"Wrong password for {archive_path.name}")
        return exc

    # ------------------------------------------------------------------ reading

    def list_contents(self, archive_path: Path, password: str | None = None) -> list[ArchiveEntry]:
        entries: list[ArchiveEntry] = []
        try:
            return self._list(archive_path, password)
        except Exception as exc:
            raise self._decrypt_error(exc, archive_path, password) from exc

    def _list(self, archive_path: Path, password: str | None) -> list[ArchiveEntry]:
        entries: list[ArchiveEntry] = []
        with self._open(archive_path, password=password) as zf:
            for info in zf.list():
                name = self.normalise(info.filename)
                if not name:
                    continue
                entries.append(
                    ArchiveEntry(
                        name=name.rsplit("/", 1)[-1],
                        path=name,
                        size=info.uncompressed or 0,
                        compressed_size=getattr(info, "compressed", 0) or 0,
                        modified=info.creationtime or datetime.fromtimestamp(0),
                        is_dir=bool(info.is_directory),
                        crc=getattr(info, "crc32", 0) or 0,
                    )
                )
        return entries

    def read_member(self, archive_path: Path, member_path: str,
                    password: str | None = None) -> bytes:
        """py7zr has no in-memory read across its versions: extract the one member
        into a temporary directory and read it from there."""
        import tempfile

        wanted = self.normalise(member_path)
        with tempfile.TemporaryDirectory(prefix="uc-7z-") as td:
            work = Path(td)
            try:
                with self._open(archive_path, password=password) as zf:
                    zf.extract(path=str(work), targets=[wanted])
            except Exception as exc:
                raise self._decrypt_error(exc, archive_path, password) from exc
            local = work / wanted
            if not local.is_file():
                raise ArchiveError(f"{member_path} not found in the archive")
            return local.read_bytes()

    def extract(
        self,
        archive_path: Path,
        destination: Path,
        members: list[str] | None = None,
        password: str | None = None,
    ) -> None:
        from .extract import safe_target

        self._check()
        # py7zr writes to disk itself, so filter the member list and verify the
        # targets afterwards rather than trusting names inside the archive
        with self._open(archive_path, password=password) as zf:
            names = [self.normalise(i.filename) for i in zf.list()]
        wanted = [n for n in names
                  if n and (members is None or self.is_below(n, members))
                  and safe_target(destination, n) is not None]
        if not wanted:
            return
        destination.mkdir(parents=True, exist_ok=True)
        try:
            with self._open(archive_path, password=password) as zf:
                zf.extract(path=str(destination), targets=wanted)
        except Exception as exc:
            raise self._decrypt_error(exc, archive_path, password) from exc

    # ------------------------------------------------------------------ writing

    def create(
        self,
        archive_path: Path,
        sources: list[Path],
        base_dir: Path | None = None,
        level: int | None = None,
        password: str | None = None,
    ) -> None:
        self._check()
        filters = None
        if password:
            # AES-256 must be the last filter; py7zr also encrypts the header
            filters = [
                {"id": py7zr.FILTER_LZMA2, "preset": max(0, min(9, level if level is not None else 6))},
                {"id": py7zr.FILTER_CRYPTO_AES256_SHA256},
            ]
        elif level is not None:
            filters = [{"id": py7zr.FILTER_LZMA2, "preset": max(0, min(9, level))}]
        with py7zr.SevenZipFile(str(archive_path), mode="w", filters=filters,
                                password=password or None) as zf:
            for src in sources:
                arc = self.arc_name(src, base_dir)
                if src.is_dir():
                    zf.writeall(str(src), arc)
                else:
                    zf.write(str(src), arc)

    def add_files(
        self,
        archive_path: Path,
        sources: list[Path],
        base_dir: Path | None = None,
        prefix: str = "",
        password: str | None = None,
    ) -> None:
        """py7zr's append mode is unreliable across versions, so rebuild: unpack to
        a temporary directory, drop the new files in and repack (keeping the
        password, so an encrypted archive stays encrypted)."""
        self._check()
        import shutil
        import tempfile

        if not archive_path.exists():
            self.create(archive_path, sources, base_dir, password=password)
            return
        with tempfile.TemporaryDirectory(prefix="uc-7z-") as td:
            work = Path(td)
            self.extract(archive_path, work, None, password=password)
            for src in sources:
                target = work / self.arc_name(src, base_dir, prefix)
                target.parent.mkdir(parents=True, exist_ok=True)
                if src.is_dir():
                    if target.exists():
                        shutil.rmtree(target, ignore_errors=True)
                    shutil.copytree(src, target)
                else:
                    shutil.copy2(src, target)
            self._repack(archive_path, work, password)

    def delete_members(self, archive_path: Path, members: list[str],
                       password: str | None = None) -> None:
        """py7zr cannot remove entries: unpack to a temporary directory, drop the
        members there and repack."""
        self._check()
        import shutil
        import tempfile

        with tempfile.TemporaryDirectory(prefix="uc-7z-") as td:
            work = Path(td)
            self.extract(archive_path, work, None, password=password)
            for m in members:
                target = work / self.normalise(m)
                if target.is_dir():
                    shutil.rmtree(target, ignore_errors=True)
                elif target.exists():
                    target.unlink()
            self._repack(archive_path, work, password)

    def _repack(self, archive_path: Path, work: Path, password: str | None = None) -> None:
        """Write everything under *work* into the archive (swapped in at the end)."""
        filters = None
        if password:
            filters = [
                {"id": py7zr.FILTER_LZMA2, "preset": 6},
                {"id": py7zr.FILTER_CRYPTO_AES256_SHA256},
            ]
        with self.rebuilt_archive(archive_path) as tmp:
            with py7zr.SevenZipFile(str(tmp), mode="w", filters=filters,
                                    password=password or None) as zf:
                for child in sorted(work.iterdir()):
                    if child.is_dir():
                        zf.writeall(str(child), child.name)
                    else:
                        zf.write(str(child), child.name)

    def write_member(self, archive_path: Path, member_path: str, data: bytes,
                     password: str | None = None) -> None:
        self._check()
        import tempfile

        member = self.normalise(member_path)
        with tempfile.TemporaryDirectory(prefix="uc-7z-") as td:
            work = Path(td)
            self.extract(archive_path, work, None, password=password)
            target = work / member
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            self._repack(archive_path, work, password)
