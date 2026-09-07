"""Clickable path breadcrumb ("C: › Users › dapar") shown next to the panel tabs.

Every segment is a flat button that navigates to that ancestor; when the row
is too narrow the leading segments collapse into a "…" button whose menu
lists them. Colours and sizes come from the theme (QSS ``#breadcrumb``,
``retheme()`` for the chevron pixmaps).
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QMenu, QPushButton, QSizePolicy, QWidget

from src.solarqt import icons, theme, widgets


class Breadcrumb(QWidget):
    path_clicked = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("breadcrumb")
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(theme.px(4), 0, 0, 0)
        self._layout.setSpacing(0)
        self._segments: list[tuple[str, str]] = []     # (label, full path)
        self._buttons: list[QPushButton] = []
        self._seps: list[QLabel] = []
        self._more = QPushButton("…")
        self._more.setObjectName("crumbMore")
        self._more.setCursor(Qt.CursorShape.PointingHandCursor)
        self._more.setToolTip("Hidden parent folders")
        self._more.clicked.connect(self._show_hidden_menu)
        self._fit(self._more)
        self._more_sep = self._make_sep()
        self._layout.addWidget(self._more)
        self._layout.addWidget(self._more_sep)
        self._layout.addStretch(1)
        self._hidden_count = 0

    # ------------------------------------------------------------------ public

    def set_path(self, path: str) -> None:
        segs = self._split(path)
        if segs == self._segments:
            self._relayout()
            return
        self._segments = segs
        for w in (*self._buttons, *self._seps):
            self._layout.removeWidget(w)            # deleteLater alone leaves it in the layout for a while
            w.setParent(None)
            w.deleteLater()
        self._buttons, self._seps = [], []
        insert_at = 2                                   # after "…" and its separator
        for i, (label, full) in enumerate(segs):
            btn = QPushButton(label)
            btn.setObjectName("crumb")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setToolTip(full)
            btn.setProperty("current", "true" if i == len(segs) - 1 else "false")
            btn.clicked.connect(lambda _=False, p=full: self.path_clicked.emit(p))
            self._fit(btn)
            self._layout.insertWidget(insert_at, btn)
            self._buttons.append(btn)
            insert_at += 1
            if i < len(segs) - 1:
                sep = self._make_sep()
                self._layout.insertWidget(insert_at, sep)
                self._seps.append(sep)
                insert_at += 1
        self._relayout()

    def retheme(self) -> None:
        self._layout.setContentsMargins(theme.px(4), 0, 0, 0)
        for s in (*self._seps, self._more_sep):
            s.setPixmap(icons.pixmap("chevron_right", theme.ICON_SIZE))
        for b in (*self._buttons, self._more):
            self._fit(b)
        self._relayout()

    @staticmethod
    def _fit(btn: QPushButton) -> None:
        """Exact width for the text (QPushButton's own hint pads to a 75 px minimum
        and the layout would otherwise squeeze it below the text)."""
        fm = btn.fontMetrics()
        btn.setFixedWidth(fm.horizontalAdvance(btn.text()) + 2 * theme.px(5) + theme.px(2))

    # ------------------------------------------------------------------ internals

    @staticmethod
    def _split(path: str) -> list[tuple[str, str]]:
        p = Path(path)
        parts = p.parts
        if not parts:
            return []
        out: list[tuple[str, str]] = []
        root = parts[0]
        root_label = root.rstrip("\\/") or root
        out.append((root_label, root))
        cur = Path(root)
        for part in parts[1:]:
            cur = cur / part
            out.append((part, str(cur)))
        return out

    def _make_sep(self) -> QLabel:
        sep = QLabel()
        sep.setObjectName("crumbSep")
        sep.setPixmap(icons.pixmap("chevron_right", theme.ICON_SIZE))
        return sep

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._relayout()

    def _relayout(self) -> None:
        """Hide leading segments until the rest fits; the last segment always stays."""
        if not self._buttons:
            self._more.setVisible(False)
            self._more_sep.setVisible(False)
            return
        avail = self.width() - self._layout.contentsMargins().left() - theme.px(4)
        # maximumWidth() == the width set by _fit(); width() may already be squeezed by the layout
        widths = [b.maximumWidth() for b in self._buttons]
        sep_w = self._more_sep.sizeHint().width()
        more_w = self._more.maximumWidth() + sep_w
        n = len(self._buttons)
        hidden = 0
        total = sum(widths) + sep_w * (n - 1)
        while hidden < n - 1 and total + (more_w if hidden else 0) > avail:
            total -= widths[hidden] + sep_w
            hidden += 1
        self._hidden_count = hidden
        for i, b in enumerate(self._buttons):
            b.setVisible(i >= hidden)
        for i, s in enumerate(self._seps):
            s.setVisible(i >= hidden)
        self._more.setVisible(hidden > 0)
        self._more_sep.setVisible(hidden > 0)

    def _show_hidden_menu(self) -> None:
        menu = QMenu(self)
        for label, full in self._segments[: self._hidden_count]:
            act = menu.addAction(icons.icon("folder"), label)
            act.setData(full)
        chosen = menu.exec(self._more.mapToGlobal(self._more.rect().bottomLeft()))
        if chosen is not None:
            self.path_clicked.emit(chosen.data())
        widgets.repolish(self._more)
