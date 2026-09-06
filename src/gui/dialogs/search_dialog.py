"""Search dialog – async file search with criteria."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from src.core.file_model import FileEntry, SearchQuery
from src.filesystem.local_fs import LocalFileSystemProvider

logger = logging.getLogger(__name__)


class _SearchThread(QThread):
    found = Signal(object)  # FileEntry
    finished = Signal(int)  # count

    def __init__(self, root: str, query: SearchQuery) -> None:
        super().__init__()
        self._root = root
        self._query = query
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        fs = LocalFileSystemProvider()
        loop = asyncio.new_event_loop()
        count = 0
        try:
            async def collect() -> None:
                nonlocal count
                async for entry in fs.search(self._root, self._query):
                    if self._cancelled:
                        break
                    self.found.emit(entry)
                    count += 1
            loop.run_until_complete(collect())
        finally:
            loop.close()
            self.finished.emit(count)


class SearchDialog(QDialog):
    """Alt+F7 search dialog."""

    def __init__(self, start_path: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Find Files")
        self.resize(700, 550)
        self._start_path = start_path
        self._thread: _SearchThread | None = None
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        # Criteria group
        crit = QGroupBox("Search Criteria")
        crit_layout = QVBoxLayout(crit)

        # Name
        name_row = QHBoxLayout()
        name_row.addWidget(QLabel("Name / mask:"))
        self._name_edit = QLineEdit("*")
        name_row.addWidget(self._name_edit, stretch=2)
        self._regex_cb = QCheckBox("Regex")
        self._case_cb = QCheckBox("Case-sensitive")
        name_row.addWidget(self._regex_cb)
        name_row.addWidget(self._case_cb)
        crit_layout.addLayout(name_row)

        # Path
        path_row = QHBoxLayout()
        path_row.addWidget(QLabel("Search in:"))
        self._path_edit = QLineEdit(self._start_path)
        path_row.addWidget(self._path_edit, stretch=2)
        btn_browse = QPushButton("…")
        btn_browse.setMaximumWidth(30)
        btn_browse.clicked.connect(self._browse)
        path_row.addWidget(btn_browse)
        self._subdirs_cb = QCheckBox("Subdirectories")
        self._subdirs_cb.setChecked(True)
        path_row.addWidget(self._subdirs_cb)
        crit_layout.addLayout(path_row)

        # Content
        content_row = QHBoxLayout()
        content_row.addWidget(QLabel("Content contains:"))
        self._content_edit = QLineEdit()
        content_row.addWidget(self._content_edit, stretch=2)
        self._content_regex_cb = QCheckBox("Regex")
        content_row.addWidget(self._content_regex_cb)
        crit_layout.addLayout(content_row)

        layout.addWidget(crit)

        # Buttons
        btn_row = QHBoxLayout()
        self._btn_start = QPushButton("Search")
        self._btn_start.clicked.connect(self._start_search)
        self._btn_stop = QPushButton("Stop")
        self._btn_stop.clicked.connect(self._stop_search)
        self._btn_stop.setEnabled(False)
        btn_row.addStretch()
        btn_row.addWidget(self._btn_start)
        btn_row.addWidget(self._btn_stop)
        layout.addLayout(btn_row)

        # Results
        self._results_list = QListWidget()
        self._results_list.setFont(__import__("PySide6.QtGui", fromlist=["QFont"]).QFont("Consolas", 9))
        self._results_list.itemDoubleClicked.connect(self._open_result)
        layout.addWidget(self._results_list, stretch=1)

        # Status
        self._status_label = QLabel("Ready.")
        layout.addWidget(self._status_label)

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.close)
        layout.addWidget(close_btn)

    def _browse(self) -> None:
        from PySide6.QtWidgets import QFileDialog
        path = QFileDialog.getExistingDirectory(
            self, "Search Root", self._path_edit.text()
        )
        if path:
            self._path_edit.setText(path)

    def _build_query(self) -> SearchQuery:
        return SearchQuery(
            name_pattern=self._name_edit.text(),
            use_regex=self._regex_cb.isChecked(),
            case_sensitive=self._case_cb.isChecked(),
            content_text=self._content_edit.text(),
            content_regex=self._content_regex_cb.isChecked(),
            search_subdirs=self._subdirs_cb.isChecked(),
        )

    def _start_search(self) -> None:
        self._results_list.clear()
        self._btn_start.setEnabled(False)
        self._btn_stop.setEnabled(True)
        query = self._build_query()
        self._thread = _SearchThread(self._path_edit.text(), query)
        self._thread.found.connect(self._on_found)
        self._thread.finished.connect(self._on_finished)
        self._thread.start()
        self._status_label.setText("Searching…")

    def _stop_search(self) -> None:
        if self._thread:
            self._thread.cancel()

    def _on_found(self, entry: FileEntry) -> None:
        item = QListWidgetItem(str(entry.path))
        item.setData(Qt.ItemDataRole.UserRole, entry)
        self._results_list.addItem(item)
        count = self._results_list.count()
        self._status_label.setText(f"Found {count} item(s)…")

    def _on_finished(self, count: int) -> None:
        self._btn_start.setEnabled(True)
        self._btn_stop.setEnabled(False)
        self._status_label.setText(f"Search complete. {count} item(s) found.")

    def _open_result(self, item: QListWidgetItem) -> None:
        entry: FileEntry | None = item.data(Qt.ItemDataRole.UserRole)
        if entry and not entry.is_dir:
            from src.viewer.file_viewer import FileViewerWindow
            dlg = FileViewerWindow(str(entry.path), self)
            dlg.show()

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self._stop_search()
        super().closeEvent(event)
