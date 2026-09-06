"""Bulk rename dialog with preview."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.core.file_model import FileEntry


class BulkRenameDialog(QDialog):
    """Multi-file rename with search/replace, counter, and date injection."""

    def __init__(self, entries: list[FileEntry], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Bulk Rename")
        self.resize(800, 600)
        self._entries = [e for e in entries if not e.is_dir and not e.is_parent]
        self._rename_pairs: list[tuple[str, str]] = []
        self._build_ui()
        self._update_preview()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        # Rules group
        rules_group = QGroupBox("Rename Rules")
        rules_layout = QVBoxLayout(rules_group)

        # Search / Replace
        sr_row = QHBoxLayout()
        sr_row.addWidget(QLabel("Search:"))
        self._search_edit = QLineEdit()
        self._search_edit.textChanged.connect(self._update_preview)
        sr_row.addWidget(self._search_edit, stretch=2)
        sr_row.addWidget(QLabel("Replace:"))
        self._replace_edit = QLineEdit()
        self._replace_edit.textChanged.connect(self._update_preview)
        sr_row.addWidget(self._replace_edit, stretch=2)
        self._regex_cb = QCheckBox("Regex")
        self._regex_cb.toggled.connect(self._update_preview)
        self._case_cb = QCheckBox("Case-sensitive")
        self._case_cb.toggled.connect(self._update_preview)
        sr_row.addWidget(self._regex_cb)
        sr_row.addWidget(self._case_cb)
        rules_layout.addLayout(sr_row)

        # Prefix / Suffix
        ps_row = QHBoxLayout()
        ps_row.addWidget(QLabel("Prefix:"))
        self._prefix_edit = QLineEdit()
        self._prefix_edit.textChanged.connect(self._update_preview)
        ps_row.addWidget(self._prefix_edit)
        ps_row.addWidget(QLabel("Suffix:"))
        self._suffix_edit = QLineEdit()
        self._suffix_edit.textChanged.connect(self._update_preview)
        ps_row.addWidget(self._suffix_edit)
        rules_layout.addLayout(ps_row)

        # Counter
        cnt_row = QHBoxLayout()
        self._counter_cb = QCheckBox("Add counter")
        self._counter_cb.toggled.connect(self._update_preview)
        cnt_row.addWidget(self._counter_cb)
        cnt_row.addWidget(QLabel("Start:"))
        self._cnt_start = QSpinBox()
        self._cnt_start.setRange(0, 99999)
        self._cnt_start.setValue(1)
        self._cnt_start.valueChanged.connect(self._update_preview)
        cnt_row.addWidget(self._cnt_start)
        cnt_row.addWidget(QLabel("Step:"))
        self._cnt_step = QSpinBox()
        self._cnt_step.setRange(1, 100)
        self._cnt_step.setValue(1)
        self._cnt_step.valueChanged.connect(self._update_preview)
        cnt_row.addWidget(self._cnt_step)
        cnt_row.addWidget(QLabel("Width:"))
        self._cnt_width = QSpinBox()
        self._cnt_width.setRange(1, 10)
        self._cnt_width.setValue(3)
        self._cnt_width.valueChanged.connect(self._update_preview)
        cnt_row.addWidget(self._cnt_width)
        cnt_row.addStretch()
        rules_layout.addLayout(cnt_row)

        # Date insert
        date_row = QHBoxLayout()
        self._date_cb = QCheckBox("Insert date (YYYY-MM-DD)")
        self._date_cb.toggled.connect(self._update_preview)
        date_row.addWidget(self._date_cb)
        date_row.addStretch()
        rules_layout.addLayout(date_row)

        layout.addWidget(rules_group)

        # Preview table
        self._preview_table = QTableWidget(0, 2)
        self._preview_table.setHorizontalHeaderLabels(["Original", "New Name"])
        self._preview_table.horizontalHeader().setStretchLastSection(True)
        self._preview_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self._preview_table, stretch=1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _update_preview(self) -> None:
        self._preview_table.setRowCount(0)
        pairs = self._compute_pairs()
        for old_path, new_path in pairs:
            row = self._preview_table.rowCount()
            self._preview_table.insertRow(row)
            old_name = Path(old_path).name
            new_name = Path(new_path).name
            self._preview_table.setItem(row, 0, QTableWidgetItem(old_name))
            item = QTableWidgetItem(new_name)
            if new_name != old_name:
                from PySide6.QtGui import QColor
                item.setForeground(QColor("#6AB0DE"))
            self._preview_table.setItem(row, 1, item)

    def _compute_pairs(self) -> list[tuple[str, str]]:
        pairs: list[tuple[str, str]] = []
        search = self._search_edit.text()
        replace = self._replace_edit.text()
        prefix = self._prefix_edit.text()
        suffix = self._suffix_edit.text()
        use_regex = self._regex_cb.isChecked()
        case_sen = self._case_cb.isChecked()
        use_counter = self._counter_cb.isChecked()
        cnt = self._cnt_start.value()
        step = self._cnt_step.value()
        width = self._cnt_width.value()
        use_date = self._date_cb.isChecked()
        today = datetime.now().strftime("%Y-%m-%d")

        flags = 0 if case_sen else re.IGNORECASE

        for entry in self._entries:
            stem = Path(entry.path).stem
            ext = Path(entry.path).suffix

            new_stem = stem
            # Search/replace
            if search:
                if use_regex:
                    try:
                        new_stem = re.sub(search, replace, new_stem, flags=flags)
                    except re.error:
                        pass
                else:
                    if case_sen:
                        new_stem = new_stem.replace(search, replace)
                    else:
                        new_stem = re.sub(re.escape(search), replace, new_stem, flags=re.IGNORECASE)

            # Date
            if use_date:
                new_stem = f"{new_stem}_{today}"

            # Counter
            if use_counter:
                counter_str = str(cnt).zfill(width)
                new_stem = f"{new_stem}_{counter_str}"
                cnt += step

            new_name = f"{prefix}{new_stem}{suffix}{ext}"
            new_path = str(Path(entry.path).parent / new_name)
            pairs.append((str(entry.path), new_path))

        return pairs

    def _accept(self) -> None:
        self._rename_pairs = self._compute_pairs()
        self.accept()

    @property
    def rename_pairs(self) -> list[tuple[str, str]]:
        return self._rename_pairs
