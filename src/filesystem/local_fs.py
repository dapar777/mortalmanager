"""Local filesystem provider – Windows-optimised."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import stat
import uuid
from collections.abc import AsyncGenerator
from datetime import datetime
from pathlib import Path

from src.core.file_model import (
    DriveInfo,
    FileEntry,
    OperationProgress,
    SearchQuery,
    format_size,
)
from .base import FileSystemProvider, ProgressCallback

logger = logging.getLogger(__name__)

# Try to import Windows-specific modules; gracefully degrade on non-Windows
try:
    import win32api
    import win32con
    import win32file
    _HAS_WIN32 = True
except ImportError:
    _HAS_WIN32 = False

try:
    import send2trash
    _HAS_SEND2TRASH = True
except ImportError:
    _HAS_SEND2TRASH = False


def _get_win_attributes(path: Path) -> int:
    if _HAS_WIN32:
        try:
            return win32api.GetFileAttributes(str(path))
        except Exception:
            pass
    # Fallback: derive minimal attributes from stat
    try:
        s = path.stat()
        attrs = 0x20  # ARCHIVE bit
        if not os.access(str(path), os.W_OK):
            attrs |= 0x01  # READONLY
        if path.name.startswith("."):
            attrs |= 0x02  # HIDDEN (Unix convention)
        if path.is_dir():
            attrs |= 0x10  # DIRECTORY
        return attrs
    except Exception:
        return 0


def _entry_from_path(path: Path, is_parent: bool = False) -> FileEntry:
    try:
        st = path.stat()
        modified = datetime.fromtimestamp(st.st_mtime)
        created = datetime.fromtimestamp(st.st_ctime)
        size = st.st_size if not path.is_dir() else -1
    except OSError:
        modified = datetime.now()
        created = datetime.now()
        size = -1

    is_dir = path.is_dir()
    is_symlink = path.is_symlink()
    attrs = _get_win_attributes(path)

    return FileEntry(
        name=path.name or str(path),
        path=path,
        size=size,
        modified=modified,
        created=created,
        is_dir=is_dir,
        is_symlink=is_symlink,
        attributes=attrs,
        extension=path.suffix.lstrip(".") if not is_dir else "",
        is_parent=is_parent,
        target=str(os.readlink(path)) if is_symlink else None,
    )


def _entry_from_direntry(de: os.DirEntry) -> FileEntry:
    """FileEntry from a scandir entry without extra syscalls where possible."""
    is_symlink = de.is_symlink()
    is_dir = de.is_dir()
    try:
        st = de.stat(follow_symlinks=False)
        modified = datetime.fromtimestamp(st.st_mtime)
        created = datetime.fromtimestamp(getattr(st, "st_birthtime", st.st_ctime))
        size = -1 if is_dir else st.st_size
        attrs = getattr(st, "st_file_attributes", None)
    except OSError:
        now = datetime.now()
        modified, created, size, attrs = now, now, -1, None
    path = Path(de.path)
    if attrs is None:
        attrs = _get_win_attributes(path)
    target: str | None = None
    if is_symlink:
        try:
            target = os.readlink(de.path)
        except OSError:
            target = None
    name = de.name
    return FileEntry(
        name=name,
        path=path,
        size=size,
        modified=modified,
        created=created,
        is_dir=is_dir,
        is_symlink=is_symlink,
        attributes=attrs,
        extension="" if is_dir else path.suffix.lstrip("."),
        target=target,
    )


class LocalFileSystemProvider(FileSystemProvider):
    """Full-featured local filesystem provider."""

    @property
    def provider_id(self) -> str:
        return "local"

    @property
    def root(self) -> str:
        return str(Path(Path.home().anchor))

    # ------------------------------------------------------------------ listing

    async def list_directory(
        self, path: str, show_hidden: bool = False
    ) -> list[FileEntry]:
        directory = Path(path)
        if not directory.is_dir():
            raise FileNotFoundError(f"Not a directory: {path}")

        loop = asyncio.get_event_loop()
        entries = await loop.run_in_executor(
            None, self._list_sync, directory, show_hidden
        )
        return entries

    def _list_sync(self, directory: Path, show_hidden: bool) -> list[FileEntry]:
        """One ``os.scandir`` pass: on Windows the directory enumeration already
        carries size, times and attributes, so no per-file stat() is needed."""
        entries: list[FileEntry] = []

        # Add parent entry if not at root
        parent = directory.parent
        if parent != directory:
            now = datetime.now()
            entries.append(
                FileEntry(
                    name="..",
                    path=parent,
                    size=-1,
                    modified=now,
                    created=now,
                    is_dir=True,
                    is_symlink=False,
                    attributes=0x10,
                    is_parent=True,
                )
            )

        try:
            with os.scandir(directory) as it:
                for de in it:
                    try:
                        entry = _entry_from_direntry(de)
                    except Exception as exc:
                        logger.debug("Skipping %s: %s", de.path, exc)
                        continue
                    if not show_hidden and entry.is_hidden:
                        continue
                    entries.append(entry)
        except PermissionError as exc:
            logger.warning("Permission denied listing %s: %s", directory, exc)

        return entries

    async def get_entry(self, path: str) -> FileEntry:
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(path)
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _entry_from_path, p)

    # ------------------------------------------------------------------ navigation

    def get_parent(self, path: str) -> str | None:
        p = Path(path)
        parent = p.parent
        if parent == p:
            return None
        return str(parent)

    def path_exists(self, path: str) -> bool:
        return Path(path).exists()

    def is_directory(self, path: str) -> bool:
        return Path(path).is_dir()

    # ------------------------------------------------------------------ drives

    def get_drives(self) -> list[DriveInfo]:
        """Every logical drive letter, mapped network drives included
        (psutil.disk_partitions(all=False) drops DRIVE_REMOTE). A drive whose
        usage cannot be read (empty CD, disconnected share) is listed with zeros."""
        drives: list[DriveInfo] = []
        if _HAS_WIN32:
            try:
                roots = [r for r in win32api.GetLogicalDriveStrings().split("\x00") if r]
            except Exception:
                roots = []
            for root in roots:
                try:
                    dtype = self._win_drive_type(root)
                    if dtype in ("UNKNOWN", "NO_ROOT"):
                        continue
                    total = free = 0
                    fs = ""
                    try:
                        import psutil
                        usage = psutil.disk_usage(root)
                        total, free = usage.total, usage.free
                    except Exception:
                        if dtype != "NETWORK":
                            continue          # empty card reader / CD tray: not worth a button
                    try:
                        fs = win32api.GetVolumeInformation(root)[4] or ""
                    except Exception:
                        pass
                    drives.append(
                        DriveInfo(
                            letter=root.rstrip("\\").rstrip(":"),
                            label=self._win_volume_label(root),
                            drive_type=dtype,
                            total=total,
                            free=free,
                            filesystem=fs,
                            root=root,
                        )
                    )
                except Exception:
                    pass
        else:
            import psutil
            for part in psutil.disk_partitions(all=False):
                try:
                    usage = psutil.disk_usage(part.mountpoint)
                    drives.append(
                        DriveInfo(
                            letter=part.device[:1],
                            label="",
                            drive_type="LOCAL",
                            total=usage.total,
                            free=usage.free,
                            filesystem=part.fstype,
                            root=part.mountpoint,
                        )
                    )
                except Exception:
                    pass
        return drives

    def _win_drive_type(self, device: str) -> str:
        if not _HAS_WIN32:
            return "LOCAL"
        type_map = {
            win32con.DRIVE_REMOVABLE: "REMOVABLE",
            win32con.DRIVE_FIXED: "LOCAL",
            win32con.DRIVE_REMOTE: "NETWORK",
            win32con.DRIVE_CDROM: "CDROM",
            win32con.DRIVE_RAMDISK: "RAMDISK",
            1: "NO_ROOT",                       # DRIVE_NO_ROOT_DIR – letter without a mounted volume
        }
        t = win32file.GetDriveType(device)
        return type_map.get(t, "UNKNOWN")

    def _win_volume_label(self, mountpoint: str) -> str:
        if not _HAS_WIN32:
            return ""
        try:
            info = win32api.GetVolumeInformation(mountpoint)
            return info[0] or ""
        except Exception:
            return ""

    # ------------------------------------------------------------------ operations

    async def copy(
        self,
        sources: list[str],
        destination: str,
        callback: ProgressCallback | None = None,
        overwrite: bool = False,
    ) -> None:
        op_id = str(uuid.uuid4())
        progress = OperationProgress(
            operation_id=op_id,
            description=f"Copying {len(sources)} item(s) to {destination}",
            files_total=len(sources),
        )
        dest = Path(destination)
        loop = asyncio.get_event_loop()

        for i, src_str in enumerate(sources):
            src = Path(src_str)
            progress.current_file = src.name
            progress.files_done = i
            if callback:
                callback(progress)

            await loop.run_in_executor(
                None, self._copy_one, src, dest, overwrite
            )

        progress.files_done = len(sources)
        progress.finished = True
        if callback:
            callback(progress)

    def _copy_one(self, src: Path, dest: Path, overwrite: bool) -> None:
        target = dest / src.name if dest.is_dir() else dest
        if target.exists() and not overwrite:
            # Generate unique name
            stem = target.stem
            suffix = target.suffix
            n = 1
            while target.exists():
                target = target.parent / f"{stem}_copy{n}{suffix}"
                n += 1
        if src.is_dir():
            shutil.copytree(str(src), str(target), dirs_exist_ok=overwrite)
        else:
            shutil.copy2(str(src), str(target))

    async def move(
        self,
        sources: list[str],
        destination: str,
        callback: ProgressCallback | None = None,
        overwrite: bool = False,
    ) -> None:
        op_id = str(uuid.uuid4())
        progress = OperationProgress(
            operation_id=op_id,
            description=f"Moving {len(sources)} item(s) to {destination}",
            files_total=len(sources),
        )
        dest = Path(destination)
        loop = asyncio.get_event_loop()

        for i, src_str in enumerate(sources):
            src = Path(src_str)
            progress.current_file = src.name
            progress.files_done = i
            if callback:
                callback(progress)
            await loop.run_in_executor(None, self._move_one, src, dest, overwrite)

        progress.files_done = len(sources)
        progress.finished = True
        if callback:
            callback(progress)

    def _move_one(self, src: Path, dest: Path, overwrite: bool) -> None:
        target = dest / src.name if dest.is_dir() else dest
        if target.exists():
            if overwrite:
                if target.is_dir():
                    shutil.rmtree(str(target))
                else:
                    target.unlink()
        shutil.move(str(src), str(target))

    async def delete(
        self,
        paths: list[str],
        use_trash: bool = True,
        callback: ProgressCallback | None = None,
    ) -> None:
        op_id = str(uuid.uuid4())
        progress = OperationProgress(
            operation_id=op_id,
            description=f"Deleting {len(paths)} item(s)",
            files_total=len(paths),
        )
        loop = asyncio.get_event_loop()

        for i, path_str in enumerate(paths):
            progress.current_file = Path(path_str).name
            progress.files_done = i
            if callback:
                callback(progress)
            await loop.run_in_executor(
                None, self._delete_one, path_str, use_trash
            )

        progress.files_done = len(paths)
        progress.finished = True
        if callback:
            callback(progress)

    def _delete_one(self, path_str: str, use_trash: bool) -> None:
        p = Path(path_str)
        if use_trash and _HAS_SEND2TRASH:
            import send2trash
            send2trash.send2trash(str(p))
        elif use_trash and _HAS_WIN32:
            try:
                import win32com.shell.shell as shell
                import win32com.shell.shellcon as shellcon
                shell.SHFileOperation(
                    (0, shellcon.FO_DELETE, str(p), None,
                     shellcon.FOF_ALLOWUNDO | shellcon.FOF_NOCONFIRMATION, None, None)
                )
                return
            except Exception:
                pass
            # Final fallback
            if p.is_dir():
                shutil.rmtree(str(p))
            else:
                p.unlink(missing_ok=True)
        else:
            if p.is_dir():
                shutil.rmtree(str(p))
            else:
                p.unlink(missing_ok=True)

    async def rename(self, old_path: str, new_path: str) -> None:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, Path(old_path).rename, new_path)

    async def mkdir(self, path: str) -> None:
        Path(path).mkdir(parents=True, exist_ok=False)

    async def create_file(self, path: str) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.touch(exist_ok=False)

    # ------------------------------------------------------------------ search

    async def search(
        self, root: str, query: SearchQuery
    ) -> AsyncGenerator[FileEntry, None]:
        root_path = Path(root)
        loop = asyncio.get_event_loop()
        queue: asyncio.Queue[FileEntry | None] = asyncio.Queue()

        async def producer() -> None:
            await loop.run_in_executor(
                None, self._search_sync, root_path, query, queue
            )
            await queue.put(None)  # sentinel

        asyncio.create_task(producer())

        while True:
            item = await queue.get()
            if item is None:
                break
            yield item

    def _search_sync(
        self,
        root: Path,
        query: SearchQuery,
        queue: asyncio.Queue[FileEntry | None],
    ) -> None:
        pattern = query.name_pattern
        flags = 0 if query.case_sensitive else re.IGNORECASE
        regex: re.Pattern[str] | None = None
        if query.use_regex and pattern:
            try:
                regex = re.compile(pattern, flags)
            except re.error:
                logger.warning("Invalid regex: %s", pattern)
                return

        walk_iter = root.rglob("*") if query.search_subdirs else root.iterdir()
        for path in walk_iter:
            try:
                name = path.name
                if not query.include_hidden:
                    attrs = _get_win_attributes(path)
                    if attrs & 0x02:  # HIDDEN
                        continue

                # Name filter
                if pattern:
                    if regex:
                        if not regex.search(name):
                            continue
                    else:
                        import fnmatch
                        pat = pattern if query.case_sensitive else pattern.lower()
                        n = name if query.case_sensitive else name.lower()
                        if not fnmatch.fnmatch(n, pat):
                            continue

                entry = _entry_from_path(path)

                # Size filter
                if not path.is_dir():
                    if query.min_size is not None and entry.size < query.min_size:
                        continue
                    if query.max_size is not None and entry.size > query.max_size:
                        continue

                # Date filter
                if query.modified_after and entry.modified < query.modified_after:
                    continue
                if query.modified_before and entry.modified > query.modified_before:
                    continue

                # Content search
                if query.content_text and not path.is_dir():
                    try:
                        text = path.read_text(errors="ignore")
                        if query.content_regex:
                            if not re.search(query.content_text, text, flags):
                                continue
                        else:
                            needle = (
                                query.content_text
                                if query.case_sensitive
                                else query.content_text.lower()
                            )
                            haystack = text if query.case_sensitive else text.lower()
                            if needle not in haystack:
                                continue
                    except Exception:
                        continue

                import asyncio as _asyncio
                future = _asyncio.run_coroutine_threadsafe(
                    queue.put(entry), _asyncio.get_event_loop()
                )
                future.result(timeout=5)

            except Exception as exc:
                logger.debug("Search error for %s: %s", path, exc)

    # ------------------------------------------------------------------ read / write

    async def read_bytes(self, path: str) -> bytes:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, Path(path).read_bytes)

    async def write_bytes(self, path: str, data: bytes) -> None:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, Path(path).write_bytes, data)

    async def get_total_size(self, paths: list[str]) -> int:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._calc_size, paths)

    def _calc_size(self, paths: list[str]) -> int:
        total = 0
        for p_str in paths:
            p = Path(p_str)
            if p.is_file():
                try:
                    total += p.stat().st_size
                except Exception:
                    pass
            elif p.is_dir():
                for f in p.rglob("*"):
                    if f.is_file():
                        try:
                            total += f.stat().st_size
                        except Exception:
                            pass
        return total

    def supports_attributes(self) -> bool:
        return True
