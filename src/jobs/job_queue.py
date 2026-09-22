"""Job queue – manages concurrent file operations with pause/resume/cancel."""

from __future__ import annotations

import asyncio
import functools
import logging
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Callable

from PySide6.QtCore import QObject, Signal

from src.core.file_model import OperationProgress
from .job import JobPriority, JobResult, JobSpec, JobStatus, JobType
from src.filesystem.local_fs import LocalFileSystemProvider

logger = logging.getLogger(__name__)

#: jobs handled by jobs/archive_ops.py in the executor
_ARCHIVE_JOBS = frozenset({
    JobType.EXTRACT, JobType.COMPRESS, JobType.ARCHIVE_ADD, JobType.ARCHIVE_DELETE,
})


@dataclass
class _RunningJob:
    spec: JobSpec
    status: JobStatus = JobStatus.PENDING
    progress: OperationProgress = field(default_factory=lambda: OperationProgress("", ""))
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    pause_event: asyncio.Event = field(default_factory=lambda: _make_set_event())
    task: asyncio.Task | None = field(default=None)


def _make_set_event() -> asyncio.Event:
    e = asyncio.Event()
    e.set()  # starts as "not paused"
    return e


class JobQueue(QObject):
    """Async-aware job queue.

    Signals are emitted on the Qt event loop for UI updates.
    """

    job_started = Signal(str)                         # job_id
    job_progress = Signal(str, OperationProgress)     # job_id, progress
    job_finished = Signal(str, JobResult)             # job_id, result
    job_failed = Signal(str, str)                     # job_id, error_msg
    queue_changed = Signal()

    MAX_CONCURRENT = 3

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._pending: deque[_RunningJob] = deque()
        self._active: dict[str, _RunningJob] = {}
        self._finished: list[JobResult] = []
        self._fs = LocalFileSystemProvider()
        self._loop: asyncio.AbstractEventLoop | None = None

    def set_event_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    # ------------------------------------------------------------------ public API

    def submit(self, spec: JobSpec) -> str:
        """Submit a job spec; returns job_id."""
        rj = _RunningJob(
            spec=spec,
            progress=OperationProgress(
                operation_id=spec.job_id,
                description=spec.description,
                files_total=len(spec.sources),
            ),
        )
        self._pending.append(rj)
        self.queue_changed.emit()
        self._schedule_next()
        return spec.job_id

    def cancel(self, job_id: str) -> None:
        # Cancel active job
        if job_id in self._active:
            rj = self._active[job_id]
            rj.cancel_event.set()
            if rj.task:
                rj.task.cancel()
        else:
            # Remove from pending
            self._pending = deque(j for j in self._pending if j.spec.job_id != job_id)
        self.queue_changed.emit()

    def pause(self, job_id: str) -> None:
        if job_id in self._active:
            self._active[job_id].pause_event.clear()
            self._active[job_id].status = JobStatus.PAUSED
            self.queue_changed.emit()

    def resume(self, job_id: str) -> None:
        if job_id in self._active:
            self._active[job_id].pause_event.set()
            self._active[job_id].status = JobStatus.RUNNING
            self.queue_changed.emit()

    def active_jobs(self) -> list[tuple[str, OperationProgress]]:
        return [
            (jid, rj.progress) for jid, rj in self._active.items()
        ]

    def pending_count(self) -> int:
        return len(self._pending)

    # ------------------------------------------------------------------ scheduling

    def _schedule_next(self) -> None:
        if not self._loop:
            return
        while self._pending and len(self._active) < self.MAX_CONCURRENT:
            rj = self._pending.popleft()
            asyncio.run_coroutine_threadsafe(self._run_job(rj), self._loop)

    async def _run_job(self, rj: _RunningJob) -> None:
        job_id = rj.spec.job_id
        self._active[job_id] = rj
        rj.status = JobStatus.RUNNING
        self.job_started.emit(job_id)
        self.queue_changed.emit()

        try:
            result = await self._execute(rj)
            rj.status = JobStatus.FINISHED
            self._finished.append(result)
            self.job_finished.emit(job_id, result)
        except asyncio.CancelledError:
            result = JobResult(
                job_id=job_id,
                status=JobStatus.CANCELLED,
                errors=["Cancelled by user"],
            )
            rj.status = JobStatus.CANCELLED
            self._finished.append(result)
            self.job_finished.emit(job_id, result)
        except Exception as exc:
            logger.exception("Job %s failed: %s", job_id, exc)
            result = JobResult(
                job_id=job_id,
                status=JobStatus.FAILED,
                errors=[str(exc)],
            )
            rj.status = JobStatus.FAILED
            self._finished.append(result)
            self.job_failed.emit(job_id, str(exc))
        finally:
            self._active.pop(job_id, None)
            self.queue_changed.emit()
            self._schedule_next()

    async def _execute(self, rj: _RunningJob) -> JobResult:
        spec = rj.spec
        result = JobResult(job_id=spec.job_id, status=JobStatus.RUNNING)

        def progress_cb(p: OperationProgress) -> None:
            rj.progress = p
            self.job_progress.emit(spec.job_id, p)

        if spec.job_type == JobType.COPY:
            await self._fs.copy(
                spec.sources, spec.destination,
                callback=progress_cb,
                overwrite=spec.options.get("overwrite", False),
            )
            result.undo_pairs = [(s, spec.destination) for s in spec.sources]

        elif spec.job_type == JobType.MOVE:
            await self._fs.move(
                spec.sources, spec.destination,
                callback=progress_cb,
                overwrite=spec.options.get("overwrite", False),
            )
            result.undo_pairs = [(s, spec.destination) for s in spec.sources]

        elif spec.job_type == JobType.DELETE:
            await self._fs.delete(
                spec.sources,
                use_trash=spec.options.get("use_trash", True),
                callback=progress_cb,
            )

        elif spec.job_type == JobType.MKDIR:
            await self._fs.mkdir(spec.destination)

        elif spec.job_type == JobType.RENAME:
            old_path = spec.sources[0]
            await self._fs.rename(old_path, spec.destination)
            result.undo_pairs = [(spec.destination, old_path)]

        elif spec.job_type in _ARCHIVE_JOBS:
            return await self._execute_archive(rj, result, progress_cb)

        result.files_processed = len(spec.sources)
        result.status = JobStatus.FINISHED
        return result

    async def _execute_archive(
        self,
        rj: _RunningJob,
        result: JobResult,
        progress_cb: Callable[[OperationProgress], None],
    ) -> JobResult:
        """Archive jobs run in the executor: the libraries are synchronous and a
        big archive would otherwise block the asyncio loop (and with it the GUI)."""
        from pathlib import Path

        from . import archive_ops

        spec = rj.spec
        opts = spec.options
        loop = asyncio.get_event_loop()
        cancelled = rj.cancel_event.is_set

        if spec.job_type == JobType.EXTRACT:
            archive = Path(opts.get("archive") or spec.sources[0])
            members = opts.get("members")
            work = functools.partial(
                archive_ops.extract, archive, Path(spec.destination), members,
                progress_cb, cancelled, opts.get("strip_prefix", ""), spec.job_id,
                opts.get("overwrite", True),
            )
        elif spec.job_type == JobType.COMPRESS:
            base = opts.get("base_dir")
            work = functools.partial(
                archive_ops.compress, Path(spec.destination),
                [Path(s) for s in spec.sources], Path(base) if base else None,
                progress_cb, cancelled, opts.get("level"),
                opts.get("store_paths", True), opts.get("append", False), spec.job_id,
            )
        elif spec.job_type == JobType.ARCHIVE_ADD:
            base = opts.get("base_dir")
            work = functools.partial(
                archive_ops.add_to, Path(opts["archive"]),
                [Path(s) for s in spec.sources], Path(base) if base else None,
                opts.get("prefix", ""), progress_cb, cancelled, spec.job_id,
            )
        else:                                    # ARCHIVE_DELETE
            work = functools.partial(
                archive_ops.delete_from, Path(opts["archive"]),
                list(spec.sources), progress_cb, cancelled, spec.job_id,
            )

        files, written, errors = await loop.run_in_executor(None, work)
        result.files_processed = files
        result.bytes_processed = written
        result.errors = errors
        if cancelled():
            result.status = JobStatus.CANCELLED
        else:
            result.status = JobStatus.FINISHED
        return result
