"""Drive enumeration helpers."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from src.core.file_model import DriveInfo

logger = logging.getLogger(__name__)

try:
    import psutil
    _HAS_PSUTIL = True
except ImportError:
    _HAS_PSUTIL = False

try:
    import win32api
    import win32con
    import win32file
    _HAS_WIN32 = True
except ImportError:
    _HAS_WIN32 = False


def get_all_drives() -> list[DriveInfo]:
    """Return a list of all available drives / mount points."""
    if not _HAS_PSUTIL:
        return []

    drives: list[DriveInfo] = []
    for part in psutil.disk_partitions(all=False):
        try:
            usage = psutil.disk_usage(part.mountpoint)
        except PermissionError:
            continue
        except Exception:
            continue

        letter = part.device.rstrip(":\\")[:1]
        label = _get_volume_label(part.mountpoint)
        dtype = _get_drive_type(part.device)

        drives.append(
            DriveInfo(
                letter=letter,
                label=label,
                drive_type=dtype,
                total=usage.total,
                free=usage.free,
                filesystem=part.fstype,
                root=part.mountpoint,
            )
        )
    return drives


def _get_volume_label(mountpoint: str) -> str:
    if _HAS_WIN32:
        try:
            info = win32api.GetVolumeInformation(mountpoint)
            return info[0] or ""
        except Exception:
            pass
    return ""


def _get_drive_type(device: str) -> str:
    if _HAS_WIN32:
        type_map = {
            win32con.DRIVE_REMOVABLE: "REMOVABLE",
            win32con.DRIVE_FIXED: "LOCAL",
            win32con.DRIVE_REMOTE: "NETWORK",
            win32con.DRIVE_CDROM: "CDROM",
            win32con.DRIVE_RAMDISK: "RAMDISK",
        }
        try:
            t = win32file.GetDriveType(device)
            return type_map.get(t, "UNKNOWN")
        except Exception:
            pass
    return "LOCAL"
