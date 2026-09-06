"""SFTP client using paramiko."""

from __future__ import annotations

import logging
import stat as stat_module
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Callable

from src.core.file_model import FileEntry

logger = logging.getLogger(__name__)

try:
    import paramiko
    _HAS_PARAMIKO = True
except ImportError:
    _HAS_PARAMIKO = False
    logger.warning("paramiko not installed; SFTP support disabled")


class SftpClient:
    """SFTP client built on paramiko."""

    def __init__(
        self,
        host: str,
        port: int = 22,
        username: str = "",
        password: str = "",
        key_path: str = "",
        timeout: int = 30,
    ) -> None:
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._key_path = key_path
        self._timeout = timeout
        self._transport: paramiko.Transport | None = None
        self._sftp: paramiko.SFTPClient | None = None

    def connect(self) -> None:
        if not _HAS_PARAMIKO:
            raise RuntimeError("paramiko is required for SFTP support")
        self._transport = paramiko.Transport((self._host, self._port))
        self._transport.connect(
            username=self._username,
            password=self._password if not self._key_path else None,
            pkey=(
                paramiko.RSAKey.from_private_key_file(self._key_path)
                if self._key_path
                else None
            ),
        )
        self._sftp = paramiko.SFTPClient.from_transport(self._transport)
        logger.info("Connected to SFTP %s:%d", self._host, self._port)

    def disconnect(self) -> None:
        if self._sftp:
            self._sftp.close()
            self._sftp = None
        if self._transport:
            self._transport.close()
            self._transport = None

    def is_connected(self) -> bool:
        return self._sftp is not None

    def _sftp_client(self) -> paramiko.SFTPClient:
        if not self._sftp:
            raise ConnectionError("Not connected to SFTP server")
        return self._sftp

    # ------------------------------------------------------------------ listing

    def list_directory(self, path: str = "/") -> list[FileEntry]:
        sftp = self._sftp_client()
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

        try:
            for attr in sftp.listdir_attr(path):
                full = str(PurePosixPath(path) / attr.filename)
                is_dir = stat_module.S_ISDIR(attr.st_mode or 0)
                is_link = stat_module.S_ISLNK(attr.st_mode or 0)
                size = attr.st_size or 0
                mtime = attr.st_mtime or 0
                modified = datetime.fromtimestamp(mtime)
                entries.append(
                    FileEntry(
                        name=attr.filename,
                        path=Path(full),
                        size=-1 if is_dir else size,
                        modified=modified,
                        created=modified,
                        is_dir=is_dir,
                        is_symlink=is_link,
                        attributes=0x10 if is_dir else 0x20,
                    )
                )
        except IOError as exc:
            logger.warning("SFTP list error: %s", exc)

        return entries

    # ------------------------------------------------------------------ operations

    def download(
        self,
        remote_path: str,
        local_path: Path,
        callback: Callable[[int, int], None] | None = None,
    ) -> None:
        sftp = self._sftp_client()
        local_path.parent.mkdir(parents=True, exist_ok=True)
        sftp.get(remote_path, str(local_path), callback=callback)

    def upload(
        self,
        local_path: Path,
        remote_path: str,
        callback: Callable[[int, int], None] | None = None,
    ) -> None:
        sftp = self._sftp_client()
        sftp.put(str(local_path), remote_path, callback=callback)

    def mkdir(self, path: str) -> None:
        self._sftp_client().mkdir(path)

    def remove(self, path: str) -> None:
        self._sftp_client().remove(path)

    def rmdir(self, path: str) -> None:
        self._sftp_client().rmdir(path)

    def rename(self, old_path: str, new_path: str) -> None:
        self._sftp_client().rename(old_path, new_path)

    def get_current_dir(self) -> str:
        return self._sftp_client().getcwd() or "/"

    def __enter__(self) -> SftpClient:
        self.connect()
        return self

    def __exit__(self, *args: object) -> None:
        self.disconnect()
