"""Directory watcher – emits signals when filesystem contents change."""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QFileSystemWatcher, QObject, Signal

logger = logging.getLogger(__name__)


class DirectoryWatcher(QObject):
    """Wraps QFileSystemWatcher to watch one directory per panel."""

    directory_changed = Signal(str)   # emitted with the changed directory path

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._watcher = QFileSystemWatcher(self)
        self._watcher.directoryChanged.connect(self._on_directory_changed)
        self._current: str | None = None

    def watch(self, path: str) -> None:
        """Start watching *path*, stop watching previous path."""
        if self._current:
            self._watcher.removePath(self._current)
        if path and Path(path).is_dir():
            self._watcher.addPath(path)
            self._current = path
        else:
            self._current = None

    def stop(self) -> None:
        if self._current:
            self._watcher.removePath(self._current)
            self._current = None

    def _on_directory_changed(self, path: str) -> None:
        logger.debug("Directory changed: %s", path)
        self.directory_changed.emit(path)

    @property
    def current(self) -> str | None:
        return self._current
