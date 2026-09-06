"""Job base classes and data structures."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any


class JobStatus(Enum):
    PENDING = auto()
    RUNNING = auto()
    PAUSED = auto()
    FINISHED = auto()
    FAILED = auto()
    CANCELLED = auto()


class JobPriority(Enum):
    LOW = 0
    NORMAL = 1
    HIGH = 2


class JobType(Enum):
    COPY = "copy"
    MOVE = "move"
    DELETE = "delete"
    RENAME = "rename"
    MKDIR = "mkdir"
    CALCULATE_SIZE = "calc_size"
    HASH = "hash"
    SEARCH = "search"
    EXTRACT = "extract"
    COMPRESS = "compress"
    SYNC = "sync"
    CUSTOM = "custom"


@dataclass
class JobSpec:
    """Specification for a file operation job."""

    job_type: JobType
    sources: list[str] = field(default_factory=list)
    destination: str = ""
    options: dict[str, Any] = field(default_factory=dict)
    priority: JobPriority = JobPriority.NORMAL
    job_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    description: str = ""

    def __post_init__(self) -> None:
        if not self.description:
            self.description = f"{self.job_type.value} ({len(self.sources)} items)"


@dataclass
class JobResult:
    """Result of a completed job."""

    job_id: str
    status: JobStatus
    files_processed: int = 0
    bytes_processed: int = 0
    errors: list[str] = field(default_factory=list)
    undo_pairs: list[tuple[str, str]] = field(default_factory=list)
