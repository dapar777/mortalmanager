"""Core domain models – FileEntry, DriveInfo, SortField, ViewMode."""

from __future__ import annotations

import stat
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto
from pathlib import Path


class SortField(Enum):
    NAME = auto()
    EXTENSION = auto()
    SIZE = auto()
    MODIFIED = auto()
    CREATED = auto()
    ATTRIBUTES = auto()


class SortOrder(Enum):
    ASCENDING = auto()
    DESCENDING = auto()


class ViewMode(Enum):
    DETAILS = auto()
    THUMBNAILS = auto()
    BRIEF = auto()


class FileAttribute(Enum):
    """Windows file attributes."""
    READONLY = 0x0001
    HIDDEN = 0x0002
    SYSTEM = 0x0004
    DIRECTORY = 0x0010
    ARCHIVE = 0x0020
    DEVICE = 0x0040
    NORMAL = 0x0080
    TEMPORARY = 0x0100
    COMPRESSED = 0x0800
    ENCRYPTED = 0x4000
    REPARSE_POINT = 0x0400


_SIZE_UNITS = ("B", "KB", "MB", "GB", "TB", "PB")


def format_size(size: int) -> str:
    """Return human-readable file size string."""
    if size < 0:
        return ""
    if size == 0:
        return "0 B"
    unit_index = 0
    value: float = float(size)
    while value >= 1024.0 and unit_index < len(_SIZE_UNITS) - 1:
        value /= 1024.0
        unit_index += 1
    if unit_index == 0:
        return f"{int(value)} {_SIZE_UNITS[unit_index]}"
    return f"{value:.1f} {_SIZE_UNITS[unit_index]}"


@dataclass(slots=True)
class FileEntry:
    """Represents a single file or directory entry in the virtual filesystem."""

    name: str
    path: Path
    size: int  # -1 for directories (use dir_size for expanded size)
    modified: datetime
    created: datetime
    is_dir: bool
    is_symlink: bool
    attributes: int  # raw Windows attributes bitmask (or stat flags on other FS)
    extension: str = field(default="")
    is_parent: bool = field(default=False)  # ".." entry
    target: str | None = field(default=None)  # symlink target
    icon_key: str | None = field(default=None)  # cached icon key
    vcs_type: str | None = field(default=None)  # 'git' or 'svn'
    vcs_state: str | None = field(default=None)  # status marker (modified, untracked, etc.)

    def __post_init__(self) -> None:
        if not self.extension and not self.is_dir:
            self.extension = self.path.suffix.lstrip(".")

    @property
    def size_display(self) -> str:
        """Human-readable size string; empty for directories."""
        if self.is_dir:
            return "<DIR>"
        return format_size(self.size)

    @property
    def modified_display(self) -> str:
        return self.modified.strftime("%Y-%m-%d %H:%M")

    @property
    def attributes_display(self) -> str:
        """Returns attribute string like 'R H S A'."""
        flags: list[str] = []
        if self.attributes & FileAttribute.READONLY.value:
            flags.append("R")
        if self.attributes & FileAttribute.HIDDEN.value:
            flags.append("H")
        if self.attributes & FileAttribute.SYSTEM.value:
            flags.append("S")
        if self.attributes & FileAttribute.ARCHIVE.value:
            flags.append("A")
        return " ".join(flags)

    @property
    def is_hidden(self) -> bool:
        return bool(self.attributes & FileAttribute.HIDDEN.value)

    @property
    def is_readonly(self) -> bool:
        return bool(self.attributes & FileAttribute.READONLY.value)

    @property
    def full_path(self) -> str:
        return str(self.path)


@dataclass(slots=True)
class DriveInfo:
    """Represents a logical drive / filesystem root."""

    letter: str           # e.g. "C"
    label: str            # e.g. "System"
    drive_type: str       # LOCAL, NETWORK, REMOVABLE, CDROM, RAMDISK
    total: int            # bytes
    free: int             # bytes
    filesystem: str       # NTFS, FAT32, exFAT …
    root: str             # e.g. "C:\\"

    @property
    def used(self) -> int:
        return self.total - self.free

    @property
    def free_display(self) -> str:
        return format_size(self.free)

    @property
    def total_display(self) -> str:
        return format_size(self.total)

    @property
    def display_name(self) -> str:
        label = self.label or "No label"
        return f"{self.letter}: [{label}] {self.free_display} free"


@dataclass
class SearchQuery:
    """Parameters for a file search operation."""

    name_pattern: str = ""           # glob or regex
    use_regex: bool = False
    case_sensitive: bool = False
    min_size: int | None = None
    max_size: int | None = None
    modified_after: datetime | None = None
    modified_before: datetime | None = None
    content_text: str = ""
    content_regex: bool = False
    search_subdirs: bool = True
    include_hidden: bool = False
    attributes_mask: int = 0         # filter by attribute bitmask


@dataclass
class OperationProgress:
    """Snapshot of a running file operation's progress."""

    operation_id: str
    description: str
    current_file: str = ""
    files_done: int = 0
    files_total: int = 0
    bytes_done: int = 0
    bytes_total: int = 0
    errors: list[str] = field(default_factory=list)
    cancelled: bool = False
    finished: bool = False

    @property
    def percent(self) -> float:
        if self.bytes_total == 0:
            return 0.0
        return min(100.0, self.bytes_done / self.bytes_total * 100.0)
