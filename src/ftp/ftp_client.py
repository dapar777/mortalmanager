"""FTP and FTPS client implementation."""

from __future__ import annotations

import ftplib
import logging
from datetime import datetime
from pathlib import Path, PurePosixPath

from src.core.file_model import FileEntry

logger = logging.getLogger(__name__)


class FtpClient:
    """FTP / FTPS client wrapping stdlib ftplib."""

    def __init__(
        self,
        host: str,
        port: int = 21,
        username: str = "anonymous",
        password: str = "",
        use_tls: bool = False,
        passive: bool = True,
        timeout: int = 30,
    ) -> None:
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._use_tls = use_tls
        self._passive = passive
        self._timeout = timeout
        self._ftp: ftplib.FTP | None = None

    # ------------------------------------------------------------------ connection

    def connect(self) -> None:
        if self._use_tls:
            self._ftp = ftplib.FTP_TLS(timeout=self._timeout)
        else:
            self._ftp = ftplib.FTP(timeout=self._timeout)
        self._ftp.connect(self._host, self._port)
        self._ftp.login(self._username, self._password)
        if self._use_tls:
            assert isinstance(self._ftp, ftplib.FTP_TLS)
            self._ftp.prot_p()
        self._ftp.set_pasv(self._passive)
        logger.info("Connected to FTP %s:%d", self._host, self._port)

    def disconnect(self) -> None:
        if self._ftp:
            try:
                self._ftp.quit()
            except Exception:
                pass
            self._ftp = None

    def is_connected(self) -> bool:
        return self._ftp is not None

    def _ensure_connected(self) -> ftplib.FTP:
        if not self._ftp:
            raise ConnectionError("Not connected to FTP server")
        return self._ftp

    # ------------------------------------------------------------------ listing

    def list_directory(self, path: str = "/") -> list[FileEntry]:
        ftp = self._ensure_connected()
        entries: list[FileEntry] = []

        parent = str(PurePosixPath(path).parent)
        if parent != path:
            entries.append(
                FileEntry(
                    name="..",
                    path=Path(parent),
                    size=-1,
                    modified=datetime.now(),
                    created=datetime.now(),
                    is_dir=True,
                    is_symlink=False,
                    attributes=0x10,
                    is_parent=True,
                )
            )

        lines: list[str] = []
        try:
            ftp.retrlines(f"LIST {path}", lines.append)
        except ftplib.error_perm as exc:
            logger.warning("LIST error: %s", exc)
            return entries

        for line in lines:
            entry = self._parse_list_line(line, path)
            if entry:
                entries.append(entry)
        return entries

    def _parse_list_line(self, line: str, parent_path: str) -> FileEntry | None:
        """Parse a Unix-style LIST line."""
        try:
            parts = line.split(None, 8)
            if len(parts) < 9:
                return None
            perms = parts[0]
            size = int(parts[4]) if parts[4].isdigit() else 0
            name = parts[8]
            is_dir = perms.startswith("d")
            is_symlink = perms.startswith("l")
            full = str(PurePosixPath(parent_path) / name)
            return FileEntry(
                name=name,
                path=Path(full),
                size=-1 if is_dir else size,
                modified=datetime.now(),
                created=datetime.now(),
                is_dir=is_dir,
                is_symlink=is_symlink,
                attributes=0x10 if is_dir else 0x20,
            )
        except Exception as exc:
            logger.debug("Parse error for line '%s': %s", line, exc)
            return None

    # ------------------------------------------------------------------ operations

    def download(
        self, remote_path: str, local_path: Path,
        callback: "Callable[[int], None] | None" = None,
    ) -> None:
        ftp = self._ensure_connected()
        local_path.parent.mkdir(parents=True, exist_ok=True)
        with open(local_path, "wb") as f:
            def writer(data: bytes) -> None:
                f.write(data)
                if callback:
                    callback(len(data))
            ftp.retrbinary(f"RETR {remote_path}", writer)

    def upload(
        self, local_path: Path, remote_path: str,
        callback: "Callable[[int], None] | None" = None,
    ) -> None:
        ftp = self._ensure_connected()
        with open(local_path, "rb") as f:
            def reader(block: bytes) -> None:
                if callback:
                    callback(len(block))
            ftp.storbinary(f"STOR {remote_path}", f)

    def mkdir(self, path: str) -> None:
        self._ensure_connected().mkd(path)

    def delete_file(self, path: str) -> None:
        self._ensure_connected().delete(path)

    def delete_dir(self, path: str) -> None:
        self._ensure_connected().rmd(path)

    def rename(self, old_path: str, new_path: str) -> None:
        self._ensure_connected().rename(old_path, new_path)

    def get_current_dir(self) -> str:
        return self._ensure_connected().pwd()

    def change_dir(self, path: str) -> None:
        self._ensure_connected().cwd(path)

    # ------------------------------------------------------------------ context manager

    def __enter__(self) -> FtpClient:
        self.connect()
        return self

    def __exit__(self, *args: object) -> None:
        self.disconnect()
