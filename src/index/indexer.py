"""Background indexer: one leader per machine, full scans + live updates.

Leader election
    Every instance runs an Indexer thread, but only the one holding the
    Windows named mutex ``Local\\MortalManager.Indexer`` scans and watches.
    A mutex is released by the OS when its owner dies, so a crashed leader is
    replaced by the next instance that retries (every LEADER_RETRY_S).
    Non-leaders only read the shared database.

Keeping it current
    * full scan of every configured root when the last scan is older than
      ``rescan_hours`` (or on request), in generations (see file_index.py),
    * a ReadDirectoryChangesW watcher per root (recursive) applies created /
      removed / renamed entries within a second; a buffer overflow schedules a
      rescan of that root.

Everything here is plain threads and callbacks – no Qt – so it is testable
and could run in a separate process later.
"""

from __future__ import annotations

import logging
import os
import queue
import sys
import threading
import time
from collections.abc import Callable

from .file_index import Excluder, FileIndex, IndexConfig

logger = logging.getLogger(__name__)

LEADER_RETRY_S = 30.0
EVENT_FLUSH_S = 0.8
MUTEX_NAME = "Local\\MortalManager.Indexer"

# ReadDirectoryChangesW actions
_ADDED, _REMOVED, _MODIFIED, _RENAMED_OLD, _RENAMED_NEW = 1, 2, 3, 4, 5


class _LeaderLock:
    """Windows named mutex; on other platforms a lock file (best effort)."""

    def __init__(self, name: str = MUTEX_NAME) -> None:
        self._name = name
        self._handle = None
        self._owned = False

    def try_acquire(self) -> bool:
        if self._owned:
            return True
        if sys.platform != "win32":
            self._owned = True
            return True
        try:
            import ctypes
            k32 = ctypes.windll.kernel32
            if self._handle is None:
                self._handle = k32.CreateMutexW(None, False, self._name)
                if not self._handle:
                    return False
            r = k32.WaitForSingleObject(self._handle, 0)
            self._owned = r in (0x0, 0x80)      # WAIT_OBJECT_0 or WAIT_ABANDONED (previous owner died)
            return self._owned
        except Exception as exc:
            logger.debug("leader lock error: %s", exc)
            return False

    def release(self) -> None:
        if sys.platform == "win32" and self._handle:
            try:
                import ctypes
                k32 = ctypes.windll.kernel32
                if self._owned:
                    k32.ReleaseMutex(self._handle)
                k32.CloseHandle(self._handle)
            except Exception:
                pass
        self._handle = None
        self._owned = False

    @property
    def owned(self) -> bool:
        return self._owned


class Indexer:
    """Runs in its own thread; ``status()`` is safe from any thread."""

    def __init__(self, db_path: str, config: Callable[[], IndexConfig],
                 on_status: Callable[[dict], None] | None = None) -> None:
        self._db_path = db_path
        self._config_provider = config
        self._on_status = on_status
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._rescan_requested = threading.Event()
        self._reconfigure = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = _LeaderLock()
        self._events: queue.Queue[tuple[str, int, str]] = queue.Queue()   # (root, action, path)
        self._watchers: dict[str, threading.Thread] = {}
        self._overflow: set[str] = set()
        self._state = {"leader": False, "scanning": "", "files": 0, "message": "idle", "last_scan": 0.0}
        self._state_lock = threading.Lock()

    # ------------------------------------------------------------------ public

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="mm-indexer", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=3)
        self._lock.release()

    def request_rescan(self) -> None:
        self._rescan_requested.set()
        self._wake.set()

    def reconfigure(self) -> None:
        self._reconfigure.set()
        self._wake.set()

    @property
    def is_leader(self) -> bool:
        return self._lock.owned

    def status(self) -> dict:
        with self._state_lock:
            return dict(self._state)

    # ------------------------------------------------------------------ status

    def _set(self, **kw) -> None:
        with self._state_lock:
            self._state.update(kw)
            snap = dict(self._state)
        if self._on_status:
            try:
                self._on_status(snap)
            except Exception:
                pass

    # ------------------------------------------------------------------ main loop

    def _run(self) -> None:
        try:
            index = FileIndex(self._db_path)
        except Exception as exc:
            logger.warning("index unavailable: %s", exc)
            self._set(message=f"index unavailable: {exc}")
            return
        try:
            self._set(files=index.count())
        except Exception:
            pass
        cfg = self._config_provider()
        while not self._stop.is_set():
            if self._reconfigure.is_set():
                self._reconfigure.clear()
                cfg = self._config_provider()
                if self._lock.owned:
                    try:
                        index.purge_roots_not_in(cfg.normalized_roots())
                    except Exception:
                        pass
                    self._rescan_requested.set()
            if not cfg.enabled:
                self._set(leader=False, message="indexing disabled")
                self._wait(LEADER_RETRY_S)
                continue
            if not self._lock.try_acquire():
                self._set(leader=False, message="another instance is indexing", files=self._safe_count(index))
                self._wait(LEADER_RETRY_S)
                continue
            self._set(leader=True, message="leader")
            excluder = Excluder(cfg.exclude_names, cfg.exclude_paths)
            roots = cfg.normalized_roots()
            self._ensure_watchers(roots)
            for root in roots:
                if self._stop.is_set():
                    break
                due = time.time() - index.last_scan(root) > cfg.rescan_hours * 3600
                if due or self._rescan_requested.is_set() or root in self._overflow:
                    self._full_scan(index, root, excluder)
                    self._overflow.discard(root)
            self._rescan_requested.clear()
            self._drain_events(index, excluder, roots)
            self._set(files=self._safe_count(index), message="up to date", scanning="")
            self._wait(EVENT_FLUSH_S, until_events=True)
        index.close()

    def _wait(self, seconds: float, until_events: bool = False) -> None:
        end = time.time() + seconds
        while not self._stop.is_set():
            remaining = end - time.time()
            if remaining <= 0:
                return
            if self._wake.wait(timeout=min(remaining, 1.0)):
                self._wake.clear()
                if not until_events or self._rescan_requested.is_set() or self._reconfigure.is_set():
                    return
            if until_events and not self._events.empty():
                # let a burst of changes settle, then apply them together
                time.sleep(EVENT_FLUSH_S)
                return

    @staticmethod
    def _safe_count(index: FileIndex) -> int:
        try:
            return index.count()
        except Exception:
            return 0

    # ------------------------------------------------------------------ scanning

    def _full_scan(self, index: FileIndex, root: str, excluder: Excluder) -> None:
        gen = index.next_gen()
        t0 = time.time()
        self._set(scanning=root, message=f"scanning {root}")
        try:
            n = index.scan_root(root, excluder, gen, stop=self._stop,
                                progress=lambda k: self._set(message=f"scanning {root} · {k:,} entries"))
            if self._stop.is_set():
                return
            removed = index.purge_stale(root, gen)
            index.set_last_scan(root)
            logger.info("index: %s – %d entries, %d removed, %.1f s", root, n, removed, time.time() - t0)
        except Exception as exc:
            logger.warning("index scan of %s failed: %s", root, exc)
        finally:
            self._set(scanning="", last_scan=time.time(), files=self._safe_count(index))

    # ------------------------------------------------------------------ live changes

    def _ensure_watchers(self, roots: list[str]) -> None:
        if sys.platform != "win32":
            return
        for root in roots:
            key = os.path.normcase(root)
            th = self._watchers.get(key)
            if th is not None and th.is_alive():
                continue
            th = threading.Thread(target=self._watch, args=(root,), name=f"mm-watch-{root}", daemon=True)
            th.start()
            self._watchers[key] = th

    def _watch(self, root: str) -> None:
        try:
            import win32con
            import win32file
        except ImportError:
            return
        try:
            handle = win32file.CreateFile(
                root, 0x0001,   # FILE_LIST_DIRECTORY (not exported by win32con)
                win32con.FILE_SHARE_READ | win32con.FILE_SHARE_WRITE | win32con.FILE_SHARE_DELETE,
                None, win32con.OPEN_EXISTING, win32con.FILE_FLAG_BACKUP_SEMANTICS, None,
            )
        except Exception as exc:
            logger.warning("cannot watch %s: %s", root, exc)
            return
        flags = win32con.FILE_NOTIFY_CHANGE_FILE_NAME | win32con.FILE_NOTIFY_CHANGE_DIR_NAME
        while not self._stop.is_set():
            try:
                results = win32file.ReadDirectoryChangesW(handle, 256 * 1024, True, flags, None, None)
            except Exception as exc:
                logger.debug("watch %s ended: %s", root, exc)
                break
            if not results:            # buffer overflow – too many changes at once
                self._overflow.add(root)
                self._rescan_requested.set()
                self._wake.set()
                continue
            for action, rel in results:
                if action == _MODIFIED:
                    continue
                self._events.put((root, action, os.path.join(root, rel)))
            self._wake.set()
        try:
            handle.Close()
        except Exception:
            pass

    def _drain_events(self, index: FileIndex, excluder: Excluder, roots: list[str]) -> None:
        gen = int(index.get_meta("gen", "0") or 0)
        n = 0
        while True:
            try:
                root, action, path = self._events.get_nowait()
            except queue.Empty:
                break
            n += 1
            try:
                if action in (_REMOVED, _RENAMED_OLD):
                    index.delete_prefix(path)
                elif action in (_ADDED, _RENAMED_NEW):
                    index.add_path(path, os.path.normpath(root) if len(os.path.normpath(root)) > 3 else root, excluder, gen)
            except Exception as exc:
                logger.debug("index event %s %s failed: %s", action, path, exc)
        if n:
            logger.debug("index: applied %d change(s)", n)
