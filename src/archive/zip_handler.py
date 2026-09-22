"""ZIP archive handler (stdlib zipfile) – read and write."""

from __future__ import annotations

import zipfile
from datetime import datetime
from pathlib import Path

from .base import ArchiveEntry, ArchiveError, ArchiveHandler, PasswordRequired

_LEVELS = {0: zipfile.ZIP_STORED}     # level 0 = store, everything else deflates


class ZipHandler(ArchiveHandler):
    """ZIP: full read / write support.

    Deleting or replacing a member means rewriting the archive – zipfile cannot
    remove an entry in place – so both go through ``rebuilt_archive``.
    """

    writable = True
    creatable = True

    @property
    def format_name(self) -> str:
        return "ZIP"

    @property
    def extensions(self) -> list[str]:
        return ["zip"]

    def can_handle(self, path: Path) -> bool:
        try:
            return zipfile.is_zipfile(str(path))       # reads the central directory, not the name
        except OSError:
            return False

    # ------------------------------------------------------------------ reading

    def list_contents(self, archive_path: Path) -> list[ArchiveEntry]:
        entries: list[ArchiveEntry] = []
        try:
            with zipfile.ZipFile(str(archive_path), "r") as zf:
                for info in zf.infolist():
                    entries.append(self._entry(info))
        except zipfile.BadZipFile as exc:
            raise ArchiveError(f"Damaged ZIP archive: {exc}") from exc
        except OSError as exc:
            raise ArchiveError(str(exc)) from exc
        return entries

    def _entry(self, info: zipfile.ZipInfo) -> ArchiveEntry:
        try:
            dt = datetime(*info.date_time)
        except ValueError:
            dt = datetime.fromtimestamp(0)
        name = self.normalise(info.filename)
        return ArchiveEntry(
            name=name.rsplit("/", 1)[-1],
            path=name,
            size=info.file_size,
            compressed_size=info.compress_size,
            modified=dt,
            is_dir=info.is_dir(),
            crc=info.CRC,
        )

    def read_member(self, archive_path: Path, member_path: str) -> bytes:
        try:
            with zipfile.ZipFile(str(archive_path), "r") as zf:
                return zf.read(self._real_name(zf, member_path))
        except RuntimeError as exc:
            if "password" in str(exc).lower():
                raise PasswordRequired(f"{archive_path.name} is password protected") from exc
            raise ArchiveError(str(exc)) from exc
        except (KeyError, zipfile.BadZipFile) as exc:
            raise ArchiveError(str(exc)) from exc

    @staticmethod
    def _real_name(zf: zipfile.ZipFile, member_path: str) -> str:
        """Name as stored in the archive; our paths are normalised, the stored
        ones may differ in separators or a trailing slash."""
        wanted = ArchiveHandler.normalise(member_path)
        for name in zf.namelist():
            if ArchiveHandler.normalise(name) == wanted:
                return name
        raise KeyError(member_path)

    def extract(
        self,
        archive_path: Path,
        destination: Path,
        members: list[str] | None = None,
    ) -> None:
        from .extract import safe_target

        try:
            with zipfile.ZipFile(str(archive_path), "r") as zf:
                for info in zf.infolist():
                    name = self.normalise(info.filename)
                    if members is not None and not self.is_below(name, members):
                        continue
                    target = safe_target(destination, name)
                    if target is None:
                        continue                              # path traversal attempt
                    if info.is_dir():
                        target.mkdir(parents=True, exist_ok=True)
                        continue
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with zf.open(info) as src, open(target, "wb") as dst:
                        while chunk := src.read(1 << 20):
                            dst.write(chunk)
        except RuntimeError as exc:
            if "password" in str(exc).lower():
                raise PasswordRequired(f"{archive_path.name} is password protected") from exc
            raise ArchiveError(str(exc)) from exc
        except zipfile.BadZipFile as exc:
            raise ArchiveError(f"Damaged ZIP archive: {exc}") from exc

    # ------------------------------------------------------------------ writing

    @staticmethod
    def _compression(level: int | None) -> tuple[int, int | None]:
        if level is not None and level in _LEVELS:
            return _LEVELS[level], None
        return zipfile.ZIP_DEFLATED, level

    def create(
        self,
        archive_path: Path,
        sources: list[Path],
        base_dir: Path | None = None,
        level: int | None = None,
    ) -> None:
        compression, complevel = self._compression(level)
        with zipfile.ZipFile(str(archive_path), "w", compression, compresslevel=complevel) as zf:
            for src in self.walk_sources(sources):
                arc = self.arc_name(src, base_dir)
                if src.is_dir():
                    zf.writestr(arc.rstrip("/") + "/", b"")
                else:
                    zf.write(str(src), arc)

    def add_files(
        self,
        archive_path: Path,
        sources: list[Path],
        base_dir: Path | None = None,
        prefix: str = "",
    ) -> None:
        if not archive_path.exists():
            self.create(archive_path, sources, base_dir)
            return
        added = {self.arc_name(src, base_dir, prefix) for src in self.walk_sources(sources)}
        # a name already in the archive would become a duplicate entry: rebuild without it
        with zipfile.ZipFile(str(archive_path), "r") as zf:
            clashes = {self.normalise(n) for n in zf.namelist()} & {self.normalise(a) for a in added}
        if clashes:
            self.delete_members(archive_path, sorted(clashes))
        with zipfile.ZipFile(str(archive_path), "a", zipfile.ZIP_DEFLATED) as zf:
            for src in self.walk_sources(sources):
                arc = self.arc_name(src, base_dir, prefix)
                if src.is_dir():
                    zf.writestr(arc.rstrip("/") + "/", b"")
                else:
                    zf.write(str(src), arc)

    def delete_members(self, archive_path: Path, members: list[str]) -> None:
        self._rebuild(archive_path, drop=members)

    def write_member(self, archive_path: Path, member_path: str, data: bytes) -> None:
        self._rebuild(archive_path, drop=[], replace={self.normalise(member_path): data})

    def _rebuild(
        self,
        archive_path: Path,
        drop: list[str],
        replace: dict[str, bytes] | None = None,
    ) -> None:
        """Copy the archive without *drop*, with *replace* swapped in.

        The temporary file is swapped in by ``rebuilt_archive`` **after** this
        block – Windows cannot replace a file while the original is still open.
        """
        replace = replace or {}
        if drop and not replace:
            with zipfile.ZipFile(str(archive_path), "r") as probe:
                if not any(self.is_below(i.filename, drop) for i in probe.infolist()):
                    return                                   # nothing matched
        with self.rebuilt_archive(archive_path) as tmp:
            with zipfile.ZipFile(str(archive_path), "r") as src:
                infos = src.infolist()
                written: set[str] = set()
                with zipfile.ZipFile(str(tmp), "w", zipfile.ZIP_DEFLATED) as dst:
                    dst.comment = src.comment
                    for info in infos:
                        name = self.normalise(info.filename)
                        if drop and self.is_below(name, drop):
                            continue
                        if name in replace:
                            dst.writestr(info.filename, replace[name])   # keeps its position
                            written.add(name)
                        else:
                            dst.writestr(info, src.read(info.filename))
                    for name, data in replace.items():
                        if name not in written:
                            dst.writestr(name, data)
