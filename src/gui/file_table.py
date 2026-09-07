"""File table model and view – the core list component of each panel.

Performance notes:
- colours / fonts are cached per theme in ``_Look`` (no QColor per cell),
- shell icons are cached per extension (per path only for exe/lnk/ico/url,
  whose icon is embedded in the file), so a 10 000-file directory asks the
  shell for a handful of icons, not 10 000,
- VCS state is a small semantic dot painted over the icon, cached per
  (icon, state, zoom).
"""

from __future__ import annotations

import logging
from typing import Any

from PySide6.QtCore import (
    QAbstractTableModel,
    QFileInfo,
    QModelIndex,
    QSize,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QFileIconProvider,
    QHeaderView,
    QLineEdit,
    QStyledItemDelegate,
    QTableView,
)

from src.core.file_model import FileEntry, SortField
from src.solarqt import icons, theme

logger = logging.getLogger(__name__)

_COLUMNS = ["Name", "Ext", "Size", "Date", "Attr"]
_COL_NAME = 0
_COL_EXT = 1
_COL_SIZE = 2
_COL_DATE = 3
_COL_ATTR = 4

# unzoomed column widths (px); Name stretches
_COL_WIDTHS = {_COL_EXT: 52, _COL_SIZE: 88, _COL_DATE: 128, _COL_ATTR: 50}
_COL_SORT = {
    _COL_NAME: SortField.NAME, _COL_EXT: SortField.EXTENSION, _COL_SIZE: SortField.SIZE,
    _COL_DATE: SortField.MODIFIED, _COL_ATTR: SortField.ATTRIBUTES,
}

INVALID_NAME_CHARS = '\\/:*?"<>|'

# extensions whose icon lives in the file itself – never share by extension
_PER_FILE_ICON_EXT = {"exe", "lnk", "ico", "url", "scr", "cur", "msi"}

# git / svn status codes -> application state (theme.STATUS_KINDS maps to kind)
_VCS_STATES = {
    "M": "modified", "MM": "modified", "AM": "modified", "RM": "modified", " M": "modified",
    "A": "added", "AA": "added", "R": "added", "C": "added",
    "D": "deleted", "AD": "deleted", "!": "deleted",
    "??": "untracked", "?": "untracked",
    "U": "conflict", "UU": "conflict", "DD": "conflict", "AU": "conflict", "UA": "conflict",
    "DU": "conflict", "UD": "conflict",
    "I": "ignored", "!!": "ignored",
}


def vcs_state_of(code: str | None) -> str | None:
    """Normalise a short status code to an application state (None = clean)."""
    if not code or code == "clean":
        return None
    return _VCS_STATES.get(code, _VCS_STATES.get(code.strip(), "modified"))


class _Look:
    """Colours and fonts for the current theme (rebuilt on retheme)."""

    def __init__(self) -> None:
        t = theme.current()
        self.text = QColor(t.text)
        self.parent = QColor(t.text2)
        self.hidden = QColor(t.muted)
        self.marked_fg = QColor(t.semantic_fg["accent"])
        self.marked_bg = QColor(theme.mix(t.accent, t.card, t.tint))
        self.link = QColor(t.semantic_fg["info"])
        base = QApplication.font()
        # directories bold (700), files demi-bold (600) – readable, still distinct
        self.bold = QFont(base)
        self.bold.setWeight(QFont.Weight.Bold)
        self.file = QFont(base)
        self.file.setWeight(QFont.Weight.DemiBold)
        self.italic = QFont(self.file)
        self.italic.setItalic(True)
        self.marked = QFont(base)
        self.marked.setWeight(QFont.Weight.Bold)
        self.dot: dict[str, QColor] = {
            state: QColor(theme.status_style(state)[2]) for state in theme.STATUS_KINDS
        }


class FileTableModel(QAbstractTableModel):
    """Qt model for a directory listing."""

    rename_requested = Signal(object, str)   # FileEntry, new name (in-place edit committed)

    _provider: QFileIconProvider | None = None   # created lazily (needs a QApplication)
    _ext_icons: dict[str, QIcon] = {}          # shared across panels and listings
    _folder_icon: QIcon | None = None

    @classmethod
    def _prov(cls) -> QFileIconProvider:
        if cls._provider is None:
            cls._provider = QFileIconProvider()
        return cls._provider

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._entries: list[FileEntry] = []
        self._selected: set[str] = set()
        self._path_icons: dict[str, QIcon] = {}   # per-file icons (exe, lnk…), per listing
        self._badged: dict[tuple[str, str, int], QIcon] = {}
        self._look = _Look()
        self._parent_icon = icons.icon("folder_up")

    # ------------------------------------------------------------------ data API

    def set_entries(self, entries: list[FileEntry]) -> None:
        self._path_icons.clear()
        self.beginResetModel()
        self._entries = entries
        self.endResetModel()

    def get_entry(self, row: int) -> FileEntry | None:
        if 0 <= row < len(self._entries):
            return self._entries[row]
        return None

    def get_all_entries(self) -> list[FileEntry]:
        return list(self._entries)

    def row_of_name(self, name: str) -> int:
        for row, e in enumerate(self._entries):
            if e.name == name:
                return row
        return -1

    def retheme(self) -> None:
        """Theme or zoom changed: rebuild colours/fonts and drop scaled icons."""
        self._look = _Look()
        self._badged.clear()
        self._parent_icon = icons.icon("folder_up")
        if self._entries:
            self.dataChanged.emit(
                self.index(0, 0), self.index(len(self._entries) - 1, len(_COLUMNS) - 1)
            )

    # ------------------------------------------------------------------ selection overlay

    def set_selected(self, paths: set[str]) -> None:
        changed = self._selected ^ paths
        self._selected = set(paths)
        if not self._entries:
            return
        if len(changed) <= 8:
            for row, e in enumerate(self._entries):
                if e.full_path in changed:
                    self.dataChanged.emit(self.index(row, 0), self.index(row, len(_COLUMNS) - 1))
        else:
            self.dataChanged.emit(
                self.index(0, 0), self.index(len(self._entries) - 1, len(_COLUMNS) - 1)
            )

    def toggle_selected(self, path: str) -> None:
        new = set(self._selected)
        new.symmetric_difference_update({path})
        self.set_selected(new)

    @property
    def selected_paths(self) -> set[str]:
        return set(self._selected)

    # ------------------------------------------------------------------ QAbstractTableModel interface

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self._entries)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(_COLUMNS)

    def headerData(  # noqa: N802
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> Any:
        if orientation == Qt.Orientation.Horizontal:
            if role == Qt.ItemDataRole.DisplayRole:
                return _COLUMNS[section]
            if role == Qt.ItemDataRole.TextAlignmentRole and section == _COL_SIZE:
                return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        return None

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid():
            return None
        row, col = index.row(), index.column()
        if row >= len(self._entries):
            return None
        entry = self._entries[row]
        look = self._look

        if role == Qt.ItemDataRole.DisplayRole:
            return self._display(entry, col)

        if role == Qt.ItemDataRole.DecorationRole:
            if col == _COL_NAME:
                return self._icon_for_entry(entry)
            return None

        if role == Qt.ItemDataRole.ForegroundRole:
            if entry.full_path in self._selected:
                return look.marked_fg
            if entry.is_parent:
                return look.parent
            if entry.is_hidden:
                return look.hidden
            if entry.is_symlink:
                return look.link
            return None

        if role == Qt.ItemDataRole.BackgroundRole:
            if entry.full_path in self._selected:
                return look.marked_bg
            return None

        if role == Qt.ItemDataRole.FontRole:
            if entry.full_path in self._selected:
                return look.marked
            if entry.is_parent:
                return None
            if entry.is_dir:
                return look.bold
            if entry.is_symlink:
                return look.italic
            return look.file

        if role == Qt.ItemDataRole.TextAlignmentRole:
            if col == _COL_SIZE:
                return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            return int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        if role == Qt.ItemDataRole.ToolTipRole and col == _COL_NAME:
            state = vcs_state_of(entry.vcs_state) if entry.vcs_type else None
            tip = entry.full_path
            if state:
                tip += f"\n{entry.vcs_type}: {state}"
            if entry.target:
                tip += f"\n→ {entry.target}"
            return tip

        if role == Qt.ItemDataRole.EditRole and col == _COL_NAME:
            return entry.name

        if role == Qt.ItemDataRole.UserRole:
            return entry

        return None

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        base = super().flags(index)
        entry = self.get_entry(index.row()) if index.isValid() else None
        if entry is not None and index.column() == _COL_NAME and not entry.is_parent:
            return base | Qt.ItemFlag.ItemIsEditable
        return base

    def setData(self, index: QModelIndex, value, role: int = Qt.ItemDataRole.EditRole) -> bool:  # noqa: N802
        if role != Qt.ItemDataRole.EditRole or index.column() != _COL_NAME:
            return False
        entry = self.get_entry(index.row())
        new_name = str(value).strip()
        if entry is None or entry.is_parent or not new_name or new_name == entry.name:
            return False
        if any(ch in new_name for ch in INVALID_NAME_CHARS):
            return False
        self.rename_requested.emit(entry, new_name)
        return True

    def _display(self, entry: FileEntry, col: int) -> str:
        if col == _COL_NAME:
            return entry.name
        if col == _COL_EXT:
            return entry.extension if not entry.is_dir else ""
        if col == _COL_SIZE:
            return "" if entry.is_parent else entry.size_display
        if col == _COL_DATE:
            return "" if entry.is_parent else entry.modified_display
        if col == _COL_ATTR:
            return "" if entry.is_parent else entry.attributes_display
        return ""

    # ------------------------------------------------------------------ icons

    def _base_icon(self, entry: FileEntry) -> QIcon:
        if entry.is_parent:
            return self._parent_icon
        cls = FileTableModel
        if entry.is_dir:
            if cls._folder_icon is None:
                cls._folder_icon = cls._prov().icon(QFileIconProvider.IconType.Folder)
            return cls._folder_icon
        ext = entry.extension.lower()
        if ext in _PER_FILE_ICON_EXT:
            key = entry.full_path
            icon = self._path_icons.get(key)
            if icon is None:
                icon = cls._prov().icon(QFileInfo(entry.full_path))
                self._path_icons[key] = icon
            return icon
        icon = cls._ext_icons.get(ext)
        if icon is None:
            icon = cls._prov().icon(QFileInfo(entry.full_path))
            if icon.isNull():
                icon = icons.icon("file")
            cls._ext_icons[ext] = icon
        return icon

    def _icon_for_entry(self, entry: FileEntry) -> QIcon:
        icon = self._base_icon(entry)
        state = vcs_state_of(entry.vcs_state) if entry.vcs_type else None
        if not state or state in ("ignored",):
            return icon
        size = icons.logical_size()
        ext = entry.extension.lower()
        key = (entry.full_path if (entry.is_dir or ext in _PER_FILE_ICON_EXT) else ext, state, size)
        badged = self._badged.get(key)
        if badged is None:
            badged = self._overlay_dot(icon, self._look.dot.get(state, self._look.parent), size)
            self._badged[key] = badged
        return badged

    @staticmethod
    def _overlay_dot(icon: QIcon, color: QColor, size: int) -> QIcon:
        """Semantic dot (state colour) in the bottom-right corner of the icon."""
        app = QApplication.instance()
        dpr = app.devicePixelRatio() if app else 1.0
        pix = icon.pixmap(QSize(size, size))
        if pix.isNull():
            return icon
        over = QPixmap(pix)
        over.setDevicePixelRatio(pix.devicePixelRatio() or dpr)
        painter = QPainter(over)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        w = over.width() / over.devicePixelRatio()
        d = max(5.0, w * 0.42)
        rim = QColor(theme.current().card)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(rim)
        painter.drawEllipse(w - d - 0.5, w - d - 0.5, d + 1, d + 1)
        painter.setBrush(color)
        painter.drawEllipse(w - d + 0.5, w - d + 0.5, d - 1, d - 1)
        painter.end()
        return QIcon(over)


class _NameDelegate(QStyledItemDelegate):
    """Editor for in-place rename: pre-selects the stem, not the extension."""

    def setEditorData(self, editor, index) -> None:  # noqa: N802
        super().setEditorData(editor, index)
        if isinstance(editor, QLineEdit):
            name = editor.text()
            entry = index.data(Qt.ItemDataRole.UserRole)
            stem_len = len(name)
            if entry is not None and not entry.is_dir and "." in name.lstrip("."):
                stem_len = len(name.rsplit(".", 1)[0])
            QTimer.singleShot(0, lambda: editor.setSelection(0, stem_len))


_SHIFT_NAV_KEYS = (Qt.Key.Key_Up, Qt.Key.Key_Down, Qt.Key.Key_PageUp, Qt.Key.Key_PageDown,
                   Qt.Key.Key_Home, Qt.Key.Key_End)


class FileTableView(QTableView):
    """QTableView configured for a file listing (keyboard-first)."""

    entry_activated = Signal(object)   # FileEntry
    space_pressed = Signal(object)     # FileEntry – toggle selection (Space / Insert)
    toggle_rows = Signal(list)         # [FileEntry] – toggle each (Shift+cursor keys, Ctrl+click)
    cmdline_insert = Signal(str)       # Ctrl+Enter = name, Ctrl+Shift+Enter = full path → terminal line
    clipboard_requested = Signal(str)  # "copy" | "cut" | "paste" (Ctrl+C / Ctrl+X / Ctrl+V)
    mark_rows = Signal(list)           # [FileEntry] – select each (Shift+click range)
    sort_requested = Signal(object)    # SortField (header click)
    filter_requested = Signal(str)     # '*' typed: open the quick filter (with initial text)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("fileTable")
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
        self.setWordWrap(False)
        self.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.setVerticalScrollMode(QTableView.ScrollMode.ScrollPerPixel)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setFrameShape(QTableView.Shape.NoFrame)
        self.setEditTriggers(QTableView.EditTrigger.NoEditTriggers)   # only F2 / menu, never a slow click
        self.setItemDelegateForColumn(_COL_NAME, _NameDelegate(self))
        vh = self.verticalHeader()
        vh.setVisible(False)
        vh.setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        hdr = self.horizontalHeader()
        hdr.setHighlightSections(False)
        hdr.setSectionsClickable(True)
        hdr.setStretchLastSection(False)
        hdr.setSectionResizeMode(_COL_NAME, QHeaderView.ResizeMode.Stretch)
        for col in _COL_WIDTHS:
            hdr.setSectionResizeMode(col, QHeaderView.ResizeMode.Interactive)
        hdr.setMinimumSectionSize(theme.px(28))
        hdr.sectionClicked.connect(self._on_header_clicked)
        self.doubleClicked.connect(self._on_double_click)
        self.apply_metrics()

    def apply_metrics(self) -> None:
        """Row height, icon size and column widths follow the zoom factor."""
        self.verticalHeader().setDefaultSectionSize(theme.px(theme.ROW_HEIGHT_DENSE))
        self.setIconSize(icons.qsize())
        for col, w in _COL_WIDTHS.items():
            self.setColumnWidth(col, theme.px(w))
        self.horizontalHeader().setMinimumSectionSize(theme.px(28))

    def retheme(self) -> None:
        self.apply_metrics()
        self.file_model().retheme()
        self._fit_columns()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._fit_columns()

    def _fit_columns(self) -> None:
        """Narrow panel: drop Attr first, then Date, so Name keeps ~40 % of the width."""
        w = self.viewport().width()
        self.setColumnHidden(_COL_ATTR, w < theme.px(480))
        self.setColumnHidden(_COL_DATE, w < theme.px(380))

    def file_model(self) -> FileTableModel:
        return self.model()  # type: ignore[return-value]

    def current_entry(self) -> FileEntry | None:
        idx = self.currentIndex()
        if not idx.isValid():
            return None
        return self.file_model().get_entry(idx.row())

    def show_sort_indicator(self, field: SortField, descending: bool) -> None:
        col = next((c for c, f in _COL_SORT.items() if f == field), _COL_NAME)
        hdr = self.horizontalHeader()
        hdr.setSortIndicatorShown(True)
        hdr.setSortIndicator(col, Qt.SortOrder.DescendingOrder if descending else Qt.SortOrder.AscendingOrder)

    def start_rename(self) -> bool:
        """F2: edit the name of the current entry in place."""
        idx = self.currentIndex()
        if not idx.isValid():
            return False
        idx = self.model().index(idx.row(), _COL_NAME)
        if not (self.model().flags(idx) & Qt.ItemFlag.ItemIsEditable):
            return False
        self.edit(idx)
        return True

    def _on_header_clicked(self, section: int) -> None:
        field = _COL_SORT.get(section)
        if field is not None:
            self.sort_requested.emit(field)

    def _on_double_click(self, index: QModelIndex) -> None:
        entry = self.file_model().get_entry(index.row())
        if entry:
            self.entry_activated.emit(entry)

    def _reset_type_ahead(self) -> None:
        self._type_ahead = ""

    # ------------------------------------------------------------------ TC-style marking

    def _page_rows(self) -> int:
        rh = max(1, self.verticalHeader().defaultSectionSize())
        return max(1, self.viewport().height() // rh)

    def _shift_navigate(self, key: int) -> None:
        """Shift + cursor key (Total Commander): toggle the marks of the rows the
        cursor passes over, then move it. Up/Down/PgUp/PgDn toggle the rows
        left behind (the destination row stays as it is), Home/End include the
        edge row because the cursor stops there."""
        count = self.model().rowCount()
        if count == 0:
            return
        row = max(0, self.currentIndex().row())
        last = count - 1
        if key == Qt.Key.Key_Down:
            new = min(row + 1, last)
            rows = range(row, max(new, row + 1))
        elif key == Qt.Key.Key_Up:
            new = max(row - 1, 0)
            rows = range(min(new + 1, row), row + 1)
        elif key == Qt.Key.Key_PageDown:
            new = min(row + self._page_rows(), last)
            rows = range(row, max(new, row + 1))
        elif key == Qt.Key.Key_PageUp:
            new = max(row - self._page_rows(), 0)
            rows = range(min(new + 1, row), row + 1)
        elif key == Qt.Key.Key_End:
            new = last
            rows = range(row, last + 1)
        else:  # Home
            new = 0
            rows = range(0, row + 1)
        self._emit_rows(self.toggle_rows, rows)
        self.setCurrentIndex(self.model().index(new, 0))

    def _emit_rows(self, signal, rows) -> None:
        model = self.file_model()
        entries = [e for e in (model.get_entry(r) for r in rows) if e is not None and not e.is_parent]
        if entries:
            signal.emit(entries)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        """Shift+click marks the range from the cursor to the clicked row,
        Ctrl+click toggles the clicked row; both keep the click as a cursor move."""
        mods = event.modifiers()
        idx = self.indexAt(event.position().toPoint())
        if event.button() == Qt.MouseButton.LeftButton and idx.isValid():
            if mods & Qt.KeyboardModifier.ShiftModifier:
                anchor = max(0, self.currentIndex().row())
                lo, hi = sorted((anchor, idx.row()))
                self._emit_rows(self.mark_rows, range(lo, hi + 1))
                self.setCurrentIndex(self.model().index(idx.row(), 0))
                return
            if mods & Qt.KeyboardModifier.ControlModifier:
                self._emit_rows(self.toggle_rows, (idx.row(),))
                self.setCurrentIndex(self.model().index(idx.row(), 0))
                return
        super().mousePressEvent(event)

    def _select_type_ahead_match(self) -> None:
        prefix = self._type_ahead
        if not prefix:
            return
        current_row = self.currentIndex().row()
        count = self.model().rowCount()
        # first try the current row (the user is extending the prefix)
        entry = self.file_model().get_entry(current_row)
        if entry and entry.name.lower().startswith(prefix):
            return
        for offset in range(1, count + 1):
            row = (current_row + offset) % count
            entry = self.file_model().get_entry(row)
            if entry and entry.name.lower().startswith(prefix):
                self.setCurrentIndex(self.model().index(row, 0))
                return

    def keyPressEvent(self, event) -> None:  # noqa: N802
        key = event.key()
        if key == Qt.Key.Key_Tab:
            mw = self.window()
            if hasattr(mw, "_switch_panel"):
                mw._switch_panel()
            return
        if key == Qt.Key.Key_F2:
            self.start_rename()
            return
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            entry = self.current_entry()
            if entry and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                # Total Commander: Ctrl+Enter puts the name, Ctrl+Shift+Enter the full path into the command line
                if not entry.is_parent:
                    shift = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
                    self.cmdline_insert.emit(entry.full_path if shift else entry.name)
                return
            if entry:
                self.entry_activated.emit(entry)
            return
        if key in (Qt.Key.Key_Space, Qt.Key.Key_Insert):
            # Total Commander: toggle the item under the cursor and step down
            entry = self.current_entry()
            if entry:
                self.space_pressed.emit(entry)
                row = self.currentIndex().row()
                next_idx = self.model().index(row + 1, 0)
                if next_idx.isValid():
                    self.setCurrentIndex(next_idx)
            return
        if (event.modifiers() & Qt.KeyboardModifier.ShiftModifier) and key in _SHIFT_NAV_KEYS:
            self._shift_navigate(key)
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
                self.setCurrentIndex(self.model().index(last_row, 0))
                self.scrollToBottom()
            return

        mods = event.modifiers()
        if mods & Qt.KeyboardModifier.ControlModifier and not mods & (
                Qt.KeyboardModifier.ShiftModifier | Qt.KeyboardModifier.AltModifier):
            action = {Qt.Key.Key_C: "copy", Qt.Key.Key_X: "cut", Qt.Key.Key_V: "paste"}.get(key)
            if action:
                self.clipboard_requested.emit(action)
                return

        text = event.text()
        ctrl_alt = event.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier)
        if text == "*" and not (event.modifiers() & Qt.KeyboardModifier.KeypadModifier):
            # Total Commander: '*' on the main keyboard opens the quick filter
            # (the keypad '*' stays "select all")
            self.filter_requested.emit("")
            return
        if text and text.isprintable() and not ctrl_alt:
            self._type_ahead += text.lower()
            self._type_ahead_timer.start()
            self._select_type_ahead_match()
            return

        super().keyPressEvent(event)
