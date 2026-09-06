"""File table model and view – the core list component of each panel."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from PySide6.QtCore import (
    QFileInfo,
    QAbstractTableModel,
    QTimer,
    QModelIndex,
    QSortFilterProxyModel,
    Qt,
    Signal,
)
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QFileIconProvider, QHeaderView, QTableView

_ICON_PROVIDER = QFileIconProvider()

from src.core.file_model import FileEntry, SortField, SortOrder

logger = logging.getLogger(__name__)

_COLUMNS = ["Name", "Ext", "Size", "Date", "Attr"]
_COL_NAME = 0
_COL_EXT = 1
_COL_SIZE = 2
_COL_DATE = 3
_COL_ATTR = 4


class FileTableModel(QAbstractTableModel):
    """Qt model for a directory listing."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._entries: list[FileEntry] = []
        self._selected: set[str] = set()
        self._icon_cache: dict[str, QIcon] = {}

    # ------------------------------------------------------------------ data API

    def set_entries(self, entries: list[FileEntry]) -> None:
        self._icon_cache.clear()
        self.beginResetModel()
        self._entries = entries
        self.endResetModel()

    def get_entry(self, row: int) -> FileEntry | None:
        if 0 <= row < len(self._entries):
            return self._entries[row]
        return None

    def get_all_entries(self) -> list[FileEntry]:
        return list(self._entries)

    # ------------------------------------------------------------------ selection overlay

    def set_selected(self, paths: set[str]) -> None:
        self._selected = paths
        self.dataChanged.emit(
            self.index(0, 0),
            self.index(len(self._entries) - 1, len(_COLUMNS) - 1),
        )

    def toggle_selected(self, path: str) -> None:
        if path in self._selected:
            self._selected.discard(path)
        else:
            self._selected.add(path)
        self.dataChanged.emit(
            self.index(0, 0),
            self.index(len(self._entries) - 1, len(_COLUMNS) - 1),
        )

    @property
    def selected_paths(self) -> set[str]:
        return set(self._selected)

    # ------------------------------------------------------------------ QAbstractTableModel interface

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return len(self._entries)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return len(_COLUMNS)

    def headerData(
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> Any:
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return _COLUMNS[section]
        return None

    def data(
        self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole
    ) -> Any:
        if not index.isValid():
            return None
        row, col = index.row(), index.column()
        if row >= len(self._entries):
            return None
        entry = self._entries[row]

        if role == Qt.ItemDataRole.DisplayRole:
            return self._display(entry, col)

        if role == Qt.ItemDataRole.DecorationRole and col == _COL_NAME:
            return self._icon_for_entry(entry)

        if role == Qt.ItemDataRole.ForegroundRole:
            path = str(entry.path)
            if path in self._selected:
                return QColor("#FFD700")
            if entry.is_parent:
                return QColor("#AAAAAA")
            if entry.is_hidden:
                return QColor("#888888")
            if entry.is_dir:
                return QColor("#6AB0DE")
            return None

        if role == Qt.ItemDataRole.BackgroundRole:
            path = str(entry.path)
            if path in self._selected:
                return QColor("#3A2800")
            return None

        if role == Qt.ItemDataRole.FontRole:
            if entry.is_dir and not entry.is_parent:
                f = QFont()
                f.setBold(True)
                return f
            return None

        if role == Qt.ItemDataRole.TextAlignmentRole:
            if col == _COL_SIZE:
                return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            return int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        if role == Qt.ItemDataRole.UserRole:
            return entry

        return None

    def _display(self, entry: FileEntry, col: int) -> str:
        if col == _COL_NAME:
            return entry.name
        if col == _COL_EXT:
            return entry.extension if not entry.is_dir else ""
        if col == _COL_SIZE:
            return entry.size_display
        if col == _COL_DATE:
            return entry.modified_display
        if col == _COL_ATTR:
            return entry.attributes_display
        return ""

    def _icon_for_entry(self, entry: FileEntry) -> QIcon | None:
        key = entry.icon_key or f"{entry.path.as_posix()}|{entry.vcs_type or ''}|{entry.vcs_state or ''}"
        entry.icon_key = key
        if key in self._icon_cache:
            return self._icon_cache[key]

        qfi = QFileInfo(str(entry.path))
        icon = _ICON_PROVIDER.icon(qfi)
        if icon.isNull():
            icon = QIcon()

        if entry.vcs_type:
            icon = self._overlay_vcs_badge(icon, entry.vcs_type)

        self._icon_cache[key] = icon
        return icon

    def _overlay_vcs_badge(self, icon: QIcon, vcs_type: str) -> QIcon:
        pix = icon.pixmap(16, 16)
        if pix.isNull():
            return icon

        over = QPixmap(pix)
        painter = QPainter(over)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if vcs_type == "git":
            badge_color = QColor("#F05033")
            letter = "G"
        else:
            badge_color = QColor("#0078D7")
            letter = "S"
        radius = 8
        rect = over.rect().adjusted(over.width() - radius, over.height() - radius, 0, 0)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(badge_color)
        painter.drawEllipse(rect)
        painter.setPen(QColor("#FFFFFF"))
        font = painter.font()
        font.setPointSize(7)
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, letter)
        painter.end()
        return QIcon(over)


class FileTableView(QTableView):
    """QTableView configured for file listing."""

    entry_activated = Signal(object)   # FileEntry
    space_pressed = Signal(object)     # FileEntry – toggle selection

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setModel(FileTableModel(self))
        self._type_ahead = ""
        self._type_ahead_timer = QTimer(self)
        self._type_ahead_timer.setInterval(900)
        self._type_ahead_timer.setSingleShot(True)
        self._type_ahead_timer.timeout.connect(self._reset_type_ahead)
        self._configure()

    def _configure(self) -> None:
        self.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QTableView.SelectionMode.SingleSelection)
        self.setShowGrid(False)
        self.setAlternatingRowColors(False)
        self.verticalHeader().setVisible(False)
        self.verticalHeader().setDefaultSectionSize(18)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        hdr = self.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        hdr.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        self.setColumnWidth(1, 55)
        self.setColumnWidth(2, 90)
        self.setColumnWidth(3, 135)
        self.setColumnWidth(4, 55)
        self.doubleClicked.connect(self._on_double_click)

    def file_model(self) -> FileTableModel:
        return self.model()  # type: ignore[return-value]

    def current_entry(self) -> FileEntry | None:
        idx = self.currentIndex()
        if not idx.isValid():
            return None
        return self.file_model().get_entry(idx.row())

    def _on_double_click(self, index: QModelIndex) -> None:
        entry = self.file_model().get_entry(index.row())
        if entry:
            self.entry_activated.emit(entry)

    def _reset_type_ahead(self) -> None:
        self._type_ahead = ""

    def _select_type_ahead_match(self) -> None:
        prefix = self._type_ahead
        if not prefix:
            return
        current_row = self.currentIndex().row()
        count = self.model().rowCount()
        for offset in range(1, count + 1):
            row = (current_row + offset) % count
            entry = self.file_model().get_entry(row)
            if entry and entry.name.lower().startswith(prefix):
                idx = self.model().index(row, 0)
                self.setCurrentIndex(idx)
                return

    def keyPressEvent(self, event) -> None:  # type: ignore[override]
        key = event.key()
        if key == Qt.Key.Key_Tab:
            mw = QApplication.activeWindow()
            if hasattr(mw, "_switch_panel"):
                mw._switch_panel()
            return
        if key == Qt.Key.Key_Return or key == Qt.Key.Key_Enter:
            entry = self.current_entry()
            if entry:
                self.entry_activated.emit(entry)
            return
        if key == Qt.Key.Key_Space:
            entry = self.current_entry()
            if entry:
                self.space_pressed.emit(entry)
                # Move down one row
                row = self.currentIndex().row()
                next_idx = self.model().index(row + 1, 0)
                if next_idx.isValid():
                    self.setCurrentIndex(next_idx)
            return

        if key == Qt.Key.Key_Home:
            first = self.model().index(0, 0)
            if first.isValid():
                self.setCurrentIndex(first)
                self.scrollToTop()
            return
        if key == Qt.Key.Key_End:
            last_row = self.model().rowCount() - 1
            if last_row >= 0:
                last = self.model().index(last_row, 0)
                self.setCurrentIndex(last)
                self.scrollToBottom()
            return

        text = event.text()
        ctrl_alt = event.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier)
        if text and text.isprintable() and not ctrl_alt:
            self._type_ahead += text.lower()
            self._type_ahead_timer.start()
            self._select_type_ahead_match()
            return

        super().keyPressEvent(event)
