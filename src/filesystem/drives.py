"""Drive enumeration for the drive bar / palette – one implementation shared
with LocalFileSystemProvider.get_drives() (GetLogicalDriveStrings + drive
type, so mapped network drives are listed; psutil.disk_partitions skips them)."""

from __future__ import annotations

from src.core.file_model import DriveInfo


def get_all_drives() -> list[DriveInfo]:
    """Return every drive letter incl. network drives (see LocalFileSystemProvider.get_drives)."""
    from src.filesystem.local_fs import LocalFileSystemProvider
    return LocalFileSystemProvider().get_drives()
