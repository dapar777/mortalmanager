"""TAR handler (stdlib tarfile), plain and gz / bz2 / xz compressed."""

from __future__ import annotations

import tarfile
from datetime import datetime
from pathlib import Path

from .base import ArchiveEntry, ArchiveError, ArchiveHandler

_READ_MODES = {"gz": "r:gz", "bz2": "r:bz2", "xz": "r:xz", "": "r:*"}
_WRITE_MODES = {"gz": "w:gz", "bz2": "w:bz2", "xz": "w:xz", "": "w"}
_SUFFIXES = {
    ".tar.gz": "gz", ".tgz": "gz", ".taz": "gz",
    ".tar.bz2": "bz2", ".tbz2": "bz2", ".tbz": "bz2",
    ".tar.xz": "xz", ".txz": "xz",
}


class TarHandler(ArchiveHandler):
    """TAR: reading, creating and appending.

    tarfile can only append to an *uncompressed* tar, so adding to or deleting
    from a compressed one rebuilds the whole archive through ``rebuilt_archive``.
    """

    writable = True
    creatable = True

    @property
    def format_name(self) -> str:
        return "TAR"

    @property
    def extensions(self) -> list[str]:
        return ["tar", "tar.gz", "tgz", "taz", "tar.bz2", "tbz2", "tbz", "tar.xz", "txz"]

    def can_handle(self, path: Path) -> bool:
        try:
            return tarfile.is_tarfile(str(path))       # sniffs the content, including compression
        except (OSError, tarfile.TarError):
            return False

    @staticmethod
    def compression_of(path: Path) -> str:
        name = path.name.lower()
        for suffix, comp in _SUFFIXES.items():
            if name.endswith(suffix):
                return comp
        return ""

    def _read_mode(self, path: Path) -> str:
        return _READ_MODES[self.compression_of(path)]

    # ------------------------------------------------------------------ reading

    def list_contents(self, archive_path: Path) -> list[ArchiveEntry]:
        entries: list[ArchiveEntry] = []
        try:
            with tarfile.open(str(archive_path), self._read_mode(archive_path)) as tf:
                for member in tf.getmembers():
                    name = self.normalise(member.name)
                    if not name:
                        continue
                    entries.append(
                        ArchiveEntry(
                            name=name.rsplit("/", 1)[-1],
                            path=name,
                            size=member.size,
                            compressed_size=member.size,   # tar compresses the stream, not members
                            modified=datetime.fromtimestamp(member.mtime or 0),
                            is_dir=member.isdir(),
                        )
                    )
        except tarfile.TarError as exc:
            raise ArchiveError(f"Damaged TAR archive: {exc}") from exc
        except OSError as exc:
            raise ArchiveError(str(exc)) from exc
        return entries

    def read_member(self, archive_path: Path, member_path: str) -> bytes:
        wanted = self.normalise(member_path)
        try:
            with tarfile.open(str(archive_path), self._read_mode(archive_path)) as tf:
                for member in tf.getmembers():
                    if self.normalise(member.name) == wanted:
                        f = tf.extractfile(member)
                        if f is None:
                            raise ArchiveError(f"{member_path} is not a regular file")
                        return f.read()
        except tarfile.TarError as exc:
            raise ArchiveError(str(exc)) from exc
        raise ArchiveError(f"{member_path} not found in the archive")

    def extract(
        self,
        archive_path: Path,
        destination: Path,
        members: list[str] | None = None,
    ) -> None:
        from .extract import safe_target

        try:
            with tarfile.open(str(archive_path), self._read_mode(archive_path)) as tf:
                for member in tf.getmembers():
                    name = self.normalise(member.name)
                    if not name or (members is not None and not self.is_below(name, members)):
                        continue
                    target = safe_target(destination, name)
                    if target is None:
                        continue
                    if member.isdir():
                        target.mkdir(parents=True, exist_ok=True)
                        continue
                    if not (member.isfile() or member.islnk()):
                        continue                      # no symlinks / devices outside the archive
                    src = tf.extractfile(member)
                    if src is None:
                        continue
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with src, open(target, "wb") as dst:
                        while chunk := src.read(1 << 20):
                            dst.write(chunk)
        except tarfile.TarError as exc:
            raise ArchiveError(f"Damaged TAR archive: {exc}") from exc

    # ------------------------------------------------------------------ writing

    def create(
        self,
        archive_path: Path,
        sources: list[Path],
        base_dir: Path | None = None,
        level: int | None = None,
    ) -> None:
        mode = _WRITE_MODES[self.compression_of(archive_path)]
        kwargs = {"compresslevel": level} if level is not None and mode in ("w:gz", "w:bz2") else {}
        with tarfile.open(str(archive_path), mode, **kwargs) as tf:   # type: ignore[call-overload]
            for src in sources:
                tf.add(str(src), arcname=self.arc_name(src, base_dir), recursive=True)

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
        comp = self.compression_of(archive_path)
        names = {self.arc_name(src, base_dir, prefix) for src in sources}
        if comp:
            # compressed: rewrite, keeping everything the new files do not replace
            self._rebuild(archive_path, drop=names, extra=[(s, self.arc_name(s, base_dir, prefix))
                                                           for s in sources])
            return
        self.delete_members(archive_path, sorted(names))
        with tarfile.open(str(archive_path), "a") as tf:
            for src in sources:
                tf.add(str(src), arcname=self.arc_name(src, base_dir, prefix), recursive=True)

    def delete_members(self, archive_path: Path, members: list[str]) -> None:
        self._rebuild(archive_path, drop=set(members), extra=[])

    def write_member(self, archive_path: Path, member_path: str, data: bytes) -> None:
        import io

        member = self.normalise(member_path)
        info = tarfile.TarInfo(member)
        info.size = len(data)
        info.mtime = int(datetime.now().timestamp())
        self._rebuild(archive_path, drop={member}, extra=[], raw=[(info, data)])

    def _rebuild(
        self,
        archive_path: Path,
        drop: set[str],
        extra: list[tuple[Path, str]],
        raw: list[tuple[tarfile.TarInfo, bytes]] | None = None,
    ) -> None:
        """Rewrite the archive without *drop*, then append *extra* files / *raw* blobs."""
        import io

        comp = self.compression_of(archive_path)
        try:
            # the swap happens when this block is left, with both archives closed:
            # Windows refuses to replace a file that is still open
            with self.rebuilt_archive(archive_path) as tmp:
                with tarfile.open(str(archive_path), _READ_MODES[comp]) as src:
                    keep = [m for m in src.getmembers()
                            if not self.is_below(m.name, sorted(drop))]
                    with tarfile.open(str(tmp), _WRITE_MODES[comp]) as dst:
                        for m in keep:
                            f = src.extractfile(m) if m.isfile() else None
                            dst.addfile(m, f)
                        for path, arc in extra:
                            dst.add(str(path), arcname=arc, recursive=True)
                        for info, data in raw or []:
                            dst.addfile(info, io.BytesIO(data))
        except tarfile.TarError as exc:
            raise ArchiveError(str(exc)) from exc
