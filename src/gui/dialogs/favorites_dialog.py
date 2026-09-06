"""Favorites dialogs – quick picker and configuration manager."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QEvent, QObject, Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from src.database.db import DatabaseManager, FavoriteRow
from src.solarqt.widgets import ActionButton, DangerButton


class FavoritesPickerDialog(QDialog):
    """Small popup (Ctrl+D) for navigating to favourite directories.

    Keyboard navigation:
      - Letter keys  → jump / cycle through favourites starting with that letter
      - Enter        → navigate to selected favourite
      - +            → add current path as favourite
      - *            → open FavoritesConfigDialog
      - Escape       → close
    """

    navigated = Signal(str)  # emitted with the path to navigate to

    def __init__(
        self,
        db: DatabaseManager,
        current_path: str,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Favorites  (Ctrl+D)")
        self.setMinimumWidth(380)
        self._db = db
        self._current_path = current_path
        self._build_ui()
        self._load()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 4)
        layout.setSpacing(4)

        self._list = QListWidget()
        self._list.itemActivated.connect(self._navigate)
        self._list.installEventFilter(self)   # catch + / * before list handles them
        layout.addWidget(self._list)

        hint = QLabel("+  Add current dir    *  Configure    Enter  Navigate")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setObjectName("caption")
        layout.addWidget(hint)

    def _load(self) -> None:
        self._list.clear()
        self._favs = self._db.get_favorites()
        for fav in self._favs:
            label = fav.alias if fav.alias else fav.path
            item = QListWidgetItem(label)
            item.setToolTip(fav.path)
            item.setData(Qt.ItemDataRole.UserRole, fav)
            self._list.addItem(item)
        if self._list.count():
            self._list.setCurrentRow(0)

    def _navigate(self, item: QListWidgetItem | None = None) -> None:
        if item is None:
            item = self._list.currentItem()
        if not item:
            return
        fav: FavoriteRow = item.data(Qt.ItemDataRole.UserRole)
        self.accept()
        self.navigated.emit(fav.path)

    def _add_current(self) -> None:
        alias, ok = QInputDialog.getText(
            self,
            "Add Favourite",
            f"Alias for:\n{self._current_path}",
            text=Path(self._current_path).name,
        )
        if ok:
            self._db.add_favorite(self._current_path, alias.strip())
            self._load()

    def _open_config(self) -> None:
        self.hide()
        dlg = FavoritesConfigDialog(self._db, self.parent())
        dlg.exec()
        self.reject()

    # ------------------------------------------------------------------ key nav

    def _handle_key(self, key: int, text: str) -> bool:
        """Central key handler. Returns True if event was consumed."""
        if key == Qt.Key.Key_Escape:
            self.reject()
            return True
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._navigate()
            return True
        if key in (Qt.Key.Key_Plus, Qt.Key.Key_Equal):
            self._add_current()
            return True
        if key == Qt.Key.Key_Asterisk:
            self._open_config()
            return True
        # Letter navigation
        ch = text.lower() if text else ""
        if ch and ch.isalpha():
            matching = [
                i for i in range(self._list.count())
                if self._list.item(i).text().lower().startswith(ch)
            ]
            if matching:
                if len(matching) == 1:
                    self._list.setCurrentRow(matching[0])
                    self._navigate()
                else:
                    current = self._list.currentRow()
                    next_row = next(
                        (i for i in matching if i > current),
                        matching[0],
                    )
                    self._list.setCurrentRow(next_row)
            return True
        return False

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # type: ignore[override]
        """Intercept all key presses on the list widget."""
        if obj is self._list and event.type() == QEvent.Type.KeyPress:
            key = event.key()
            text = event.text()
            # Let Up/Down/PgUp/PgDn through to the list for normal scrolling
            if key in (Qt.Key.Key_Up, Qt.Key.Key_Down,
                       Qt.Key.Key_PageUp, Qt.Key.Key_PageDown,
                       Qt.Key.Key_Home, Qt.Key.Key_End):
                return False
            if self._handle_key(key, text):
                return True
        return super().eventFilter(obj, event)

    def keyPressEvent(self, event) -> None:  # type: ignore[override]
        if not self._handle_key(event.key(), event.text()):
            super().keyPressEvent(event)


class FavoritesConfigDialog(QDialog):
    """Manage favourites: add, remove, rename alias, reorder."""

    def __init__(self, db: DatabaseManager, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Favourites Configuration")
        self.resize(560, 380)
        self._db = db
        self._build_ui()
        self._load()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        self._table = QTableWidget()
        self._table.setColumnCount(2)
        self._table.setHorizontalHeaderLabels(["Alias", "Path"])
        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        layout.addWidget(self._table, stretch=1)

        btn_row = QHBoxLayout()
        btn_up = ActionButton("Up", "chevron_up")
        btn_up.clicked.connect(self._move_up)
        btn_dn = ActionButton("Down", "chevron_down")
        btn_dn.clicked.connect(self._move_down)
        btn_del = DangerButton("Remove", "trash")
        btn_del.clicked.connect(self._remove)
        btn_row.addWidget(btn_up)
        btn_row.addWidget(btn_dn)
        btn_row.addStretch()
        btn_row.addWidget(btn_del)
        layout.addLayout(btn_row)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._save_and_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _load(self) -> None:
        self._orig = self._db.get_favorites()
        self._table.setRowCount(len(self._orig))
        for i, fav in enumerate(self._orig):
            alias_item = QTableWidgetItem(fav.alias)
            path_item = QTableWidgetItem(fav.path)
            path_item.setFlags(path_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self._table.setItem(i, 0, alias_item)
            self._table.setItem(i, 1, path_item)

    def _move_up(self) -> None:
        row = self._table.currentRow()
        if row <= 0:
            return
        self._swap_rows(row, row - 1)
        self._table.setCurrentCell(row - 1, 0)

    def _move_down(self) -> None:
        row = self._table.currentRow()
        if row < 0 or row >= self._table.rowCount() - 1:
            return
        self._swap_rows(row, row + 1)
        self._table.setCurrentCell(row + 1, 0)

    def _swap_rows(self, a: int, b: int) -> None:
        for col in range(2):
            ia = self._table.takeItem(a, col)
            ib = self._table.takeItem(b, col)
            self._table.setItem(a, col, ib)
            self._table.setItem(b, col, ia)

    def _remove(self) -> None:
        row = self._table.currentRow()
        if row >= 0:
            self._table.removeRow(row)

    def _save_and_accept(self) -> None:
        path_to_id = {f.path: f.id for f in self._orig}
        new_favs: list[FavoriteRow] = []
        for i in range(self._table.rowCount()):
            alias = (self._table.item(i, 0) or QTableWidgetItem("")).text()
            path = (self._table.item(i, 1) or QTableWidgetItem("")).text()
            if not path:
                continue
            new_favs.append(
                FavoriteRow(id=path_to_id.get(path, 0), path=path, alias=alias, sort_order=i)
            )
        self._db.save_favorites(new_favs)
        self.accept()
