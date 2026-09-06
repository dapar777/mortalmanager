"""Duplicate file finder dialog."""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QDialog,
    QLabel,
    QProgressBar,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

logger = logging.getLogger(__name__)


class _DupThread(QThread):
    progress = Signal(int)
    done = Signal(dict)  # hash -> list[str]

    def __init__(self, root: str) -> None:
        super().__init__()
        self._root = root

    def run(self) -> None:
        files: list[Path] = []
        for p in Path(self._root).rglob("*"):
            if p.is_file():
                files.append(p)

        size_map: dict[int, list[Path]] = {}
        for f in files:
            try:
                s = f.stat().st_size
                size_map.setdefault(s, []).append(f)
            except Exception:
                pass

        # Only hash files with matching sizes
        hash_map: dict[str, list[str]] = {}
        candidates = [v for v in size_map.values() if len(v) > 1]
        total = sum(len(v) for v in candidates)
        done_count = 0

        for group in candidates:
            for f in group:
                try:
                    h = hashlib.sha256()
                    with open(f, "rb") as fp:
                        for chunk in iter(lambda: fp.read(65536), b""):
                            h.update(chunk)
                    digest = h.hexdigest()
                    hash_map.setdefault(digest, []).append(str(f))
                except Exception:
                    pass
                done_count += 1
                if total > 0:
                    self.progress.emit(int(done_count / total * 100))

        duplicates = {k: v for k, v in hash_map.items() if len(v) > 1}
        self.done.emit(duplicates)


class DuplicatesDialog(QDialog):
    def __init__(self, root: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Find Duplicates")
        self.resize(700, 500)
        self._root = root
        self._build_ui()
        self._start()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"Scanning: {self._root}"))
        self._progress = QProgressBar()
        layout.addWidget(self._progress)
        self._tree = QTreeWidget()
        self._tree.setHeaderLabels(["File Path", "Size"])
        self._tree.setColumnWidth(0, 500)
        layout.addWidget(self._tree, stretch=1)
        self._status = QLabel("Scanning…")
        layout.addWidget(self._status)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.close)
        layout.addWidget(close_btn)

    def _start(self) -> None:
        self._thread = _DupThread(self._root)
        self._thread.progress.connect(self._progress.setValue)
        self._thread.done.connect(self._on_done)
        self._thread.start()

    def _on_done(self, duplicates: dict) -> None:
        self._tree.clear()
        total_groups = 0
        for digest, paths in duplicates.items():
            group_item = QTreeWidgetItem([f"SHA-256: {digest[:16]}…", ""])
            for p in paths:
                try:
                    size = Path(p).stat().st_size
                    from src.core.file_model import format_size
                    child = QTreeWidgetItem([p, format_size(size)])
                except Exception:
                    child = QTreeWidgetItem([p, ""])
                group_item.addChild(child)
            self._tree.addTopLevelItem(group_item)
            group_item.setExpanded(True)
            total_groups += 1
        self._status.setText(
            f"Found {total_groups} duplicate group(s)."
            if total_groups else "No duplicates found."
        )
