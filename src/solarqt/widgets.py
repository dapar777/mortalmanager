"""Building blocks: chips, labels, buttons, banners, toast, search field…

Colours come from solarqt.theme. A widget that keeps colour or size outside
QSS has retheme(); after a theme or zoom change call retheme_tree(window),
which finds it on every descendant.
"""

from __future__ import annotations

import html

from PySide6.QtCore import QEvent, QPoint, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractButton,
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from . import icons, theme


# ----------------------------------------------------------------------------
# Theme / zoom switch
# ----------------------------------------------------------------------------
def retheme_tree(root: QWidget, repolish: bool = True) -> None:
    """Call retheme() on root and every descendant that has one and clear the
    icon cache. With ``repolish`` every widget is un/polished so QSS
    [property] selectors are recomputed – pass False right after
    theme.apply(), which already re-polished the whole application."""
    icons.clear_cache()
    widgets = [root, *root.findChildren(QWidget)]
    for w in widgets:
        hook = getattr(w, "retheme", None)
        if callable(hook):
            hook()
    if repolish:
        st = root.style()
        for w in widgets:
            st.unpolish(w)
            st.polish(w)
    root.update()


def repolish(w: QWidget) -> None:
    """After setProperty() force QSS recomputation."""
    w.style().unpolish(w)
    w.style().polish(w)
    w.update()


# ----------------------------------------------------------------------------
# Chips and labels
# ----------------------------------------------------------------------------
class Chip(QLabel):
    """Rounded label with text, optional dot (state colour) and icon."""

    def __init__(self, text: str = "", fg: str | None = None, bg: str | None = None,
                 dot: str | None = None, icon: str | None = None, mono: bool = False,
                 parent=None):
        super().__init__(parent)
        self._text = text
        self._dot = dot
        self._icon = icon
        self._mono = mono
        self._explicit = (fg, bg)
        self.setTextFormat(Qt.TextFormat.RichText)
        self.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        t = theme.current()
        self.set_colors(fg or t.text2, bg or t.panel)

    def set_colors(self, fg: str, bg: str) -> None:
        self._fg, self._bg = fg, bg
        self.setStyleSheet(theme.chip_qss(fg, bg))
        self._render()

    def plain(self) -> str:
        return self._text

    def set_text(self, text: str, dot: str | None = None, icon: str | None = None) -> None:
        self._text = text
        self._dot = dot
        self._icon = icon
        self._render()

    def retheme(self) -> None:
        fg, bg = self._explicit
        if fg is None or bg is None:
            t = theme.current()
            self.set_colors(fg or t.text2, bg or t.panel)
        else:
            self.set_colors(fg, bg)

    def _render(self) -> None:
        parts = []
        if self._dot:
            parts.append(f'<span style="color:{self._dot}; font-size:{theme.px(9)}px;">&#9679;</span>')
        if self._icon:
            path = icons.png_file(self._icon, 12, self._fg)
            s = theme.px(12)
            parts.append(f'<img src="{path}" width="{s}" height="{s}">')
        txt = self._text
        if self._mono:
            fam = ", ".join(f"'{f}'" for f in theme.MONO_FAMILIES)
            txt = f'<span style="font-family:{fam};">{txt}</span>'
        parts.append(txt)
        self.setText("&nbsp;".join(parts))


class KindChip(Chip):
    """Chip of a semantic kind (info / warning / danger / success / neutral)."""

    def __init__(self, kind: str, text: str, parent=None, mono: bool = False,
                 icon: str | None = None):
        self._kind = kind
        fg, bg, dot = theme.semantic_style(kind)
        super().__init__(text, fg, bg, dot=None if icon else dot, icon=icon, mono=mono, parent=parent)

    def update_kind(self, kind: str, text: str, icon: str | None = None) -> None:
        self._kind = kind
        fg, bg, dot = theme.semantic_style(kind)
        self._text, self._icon, self._dot = text, icon, (None if icon else dot)
        self.set_colors(fg, bg)

    def retheme(self) -> None:
        self.update_kind(self._kind, self._text, self._icon)


class StatusChip(KindChip):
    """Chip of an application state (see theme.configure_statuses)."""

    def __init__(self, status: str, text: str, parent=None, mono: bool = False,
                 icon: str | None = None):
        super().__init__(theme.STATUS_KINDS.get(status, "neutral"), text, parent, mono, icon)

    def update_status(self, status: str, text: str, icon: str | None = None) -> None:
        self.update_kind(theme.STATUS_KINDS.get(status, "neutral"), text, icon)


class Badge(Chip):
    """Discreet outlined badge (e.g. a count)."""

    def __init__(self, text: str, parent=None, tooltip: str = ""):
        super().__init__(text, parent=parent)
        if tooltip:
            self.setToolTip(tooltip)
        self.retheme()

    def retheme(self) -> None:
        t = theme.current()
        self._fg, self._bg = t.badge_fg, "transparent"
        self.setStyleSheet(
            f"QLabel{{color:{t.badge_fg}; border:1px solid {t.badge_border}; border-radius:{theme.px(9)}px;"
            f" padding:0 {theme.px(6)}px; font-size:{theme.pt(8)}pt; font-weight:600; background:transparent;}}"
        )
        self._render()


class CountBadge(QLabel):
    """Filled round badge with a number in the colour of a kind."""

    def __init__(self, count: int = 0, kind: str = "accent", parent=None):
        super().__init__(parent)
        self._kind = kind
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumWidth(theme.px(18))
        self.set_count(count)

    def set_count(self, count: int) -> None:
        self.setText(str(count) if count < 100 else "99+")
        self.setVisible(count > 0)
        self.retheme()

    def retheme(self) -> None:
        t = theme.current()
        bg = t.accent if self._kind == "accent" else t.semantic_dot.get(self._kind, t.accent)
        h = theme.px(18)
        self.setMinimumWidth(h)
        self.setStyleSheet(
            f"QLabel{{background:{bg}; color:{t.accent_fg}; border-radius:{h // 2}px;"
            f" padding:0 {theme.px(5)}px; min-height:{h}px; max-height:{h}px; font-size:{theme.pt(8)}pt; font-weight:700;}}"
        )


# ----------------------------------------------------------------------------
# Text
# ----------------------------------------------------------------------------
class TitleLabel(QLabel):
    """Wrapping title in the serif face (user content, app name)."""

    PAD = 8

    def __init__(self, text: str = "", pt: float = 14.5, parent=None):
        super().__init__(text, parent)
        self._pt = pt
        self.setWordWrap(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.retheme()

    def retheme(self) -> None:
        self.setFont(theme.title_font(self._pt))

    def heightForWidth(self, w: int) -> int:
        h = super().heightForWidth(w)
        return h + self.PAD if h > 0 else h

    def sizeHint(self):
        s = super().sizeHint()
        s.setHeight(s.height() + self.PAD)
        return s

    def minimumSizeHint(self):
        s = super().minimumSizeHint()
        s.setHeight(s.height() + self.PAD)
        return s


class Heading(QLabel):
    """h1 / h2 heading in the UI face (QSS via objectName)."""

    def __init__(self, text: str, level: int = 1, parent=None):
        super().__init__(text, parent)
        self.setObjectName("h1" if level == 1 else "h2")
        self.setWordWrap(True)


class SectionLabel(QLabel):
    """Small upper-case section heading."""

    def __init__(self, text: str, parent=None):
        super().__init__(text.upper(), parent)
        self.setObjectName("sectionLabel")


class Kbd(QLabel):
    """Keyboard shortcut in a frame (Ctrl+F)."""

    def __init__(self, text: str, parent=None):
        super().__init__(text, parent)
        self.setObjectName("kbd")
        self.retheme()

    def retheme(self) -> None:
        self.setFont(theme.mono_font(8))


class MonoLabel(QLabel):
    """Label in the monospace face (paths, ids, sizes)."""

    def __init__(self, text: str = "", pt: float = theme.SIZE_MONO, parent=None):
        super().__init__(text, parent)
        self._pt = pt
        self.retheme()

    def retheme(self) -> None:
        self.setFont(theme.mono_font(self._pt))


class HLine(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("hline")
        self.setFrameShape(QFrame.Shape.NoFrame)


class VLine(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("vline")
        self.setFrameShape(QFrame.Shape.NoFrame)


# ----------------------------------------------------------------------------
# Buttons
# ----------------------------------------------------------------------------
class IconButton(QToolButton):
    """Tool button with a drawn icon (tooltip = label); framed=True adds a border."""

    def __init__(self, icon_name: str, tooltip: str = "", parent=None, size: int = theme.ICON_SIZE,
                 text: str = "", framed: bool = False, color: str | None = None):
        super().__init__(parent)
        self._icon_name = icon_name
        self._size = size
        self._color = color
        self.setAutoRaise(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        if tooltip:
            self.setToolTip(tooltip)
        if text:
            self.setText(text)
            self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        if framed:
            self.setProperty("framed", "true")
        self.retheme()

    def set_icon_name(self, name: str) -> None:
        self._icon_name = name
        self.retheme()

    def set_compact(self, compact: bool) -> None:
        """Narrow window: icon only, text kept in the tooltip."""
        if not self.text():
            return
        if compact and not self.toolTip():
            self.setToolTip(self.text())
        self.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonIconOnly if compact
            else Qt.ToolButtonStyle.ToolButtonTextBesideIcon
        )

    def retheme(self) -> None:
        self.setIconSize(icons.qsize(self._size))
        self.setIcon(icons.icon(self._icon_name, self._size, self._color))


class PrimaryButton(QPushButton):
    """Main action of a view – one per view."""

    def __init__(self, text: str, icon_name: str | None = None, parent=None):
        super().__init__(text, parent)
        self._icon_name = icon_name
        self.setProperty("primary", "true")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.retheme()

    def retheme(self) -> None:
        if self._icon_name:
            self.setIconSize(icons.qsize(14))
            self.setIcon(icons.icon(self._icon_name, 14, theme.current().accent_fg))


class DangerButton(QPushButton):
    """Destructive action (Delete) – outlined, solid red on hover."""

    def __init__(self, text: str, icon_name: str | None = None, parent=None):
        super().__init__(text, parent)
        self._icon_name = icon_name
        self.setProperty("danger", "true")
        self.retheme()

    def retheme(self) -> None:
        if self._icon_name:
            self.setIconSize(icons.qsize(14))
            self.setIcon(icons.icon(self._icon_name, 14, theme.current().semantic_fg["danger"]))


class QuietButton(QPushButton):
    """Secondary action without a frame (Cancel, Show more)."""

    def __init__(self, text: str, icon_name: str | None = None, parent=None):
        super().__init__(text, parent)
        self._icon_name = icon_name
        self.setProperty("quiet", "true")
        self.retheme()

    def retheme(self) -> None:
        if self._icon_name:
            self.setIconSize(icons.qsize(14))
            self.setIcon(icons.icon(self._icon_name, 14))


class ActionButton(QPushButton):
    """Regular button with a drawn icon; ``size="sm"`` for bars."""

    def __init__(self, text: str, icon_name: str | None = None, parent=None, small: bool = False):
        super().__init__(text, parent)
        self._icon_name = icon_name
        if small:
            self.setProperty("size", "sm")
        self.retheme()

    def retheme(self) -> None:
        if self._icon_name:
            self.setIconSize(icons.qsize(14))
            self.setIcon(icons.icon(self._icon_name, 14))


class SegmentedControl(QFrame):
    """View switch (one selected segment)."""

    changed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("segment")
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(2, 2, 2, 2)
        self._layout.setSpacing(2)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons: dict[str, QToolButton] = {}
        self._icons: dict[str, str] = {}
        self._group.buttonClicked.connect(self._on_clicked)

    def add(self, key: str, text: str, icon_name: str | None = None, tooltip: str = "") -> None:
        b = QToolButton(self)
        b.setObjectName("segmentBtn")
        b.setCheckable(True)
        b.setText(text)
        b.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        if tooltip:
            b.setToolTip(tooltip)
        if icon_name:
            self._icons[key] = icon_name
            b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self._buttons[key] = b
        self._group.addButton(b)
        self._layout.addWidget(b)
        self.retheme()

    def set_current(self, key: str) -> None:
        b = self._buttons.get(key)
        if b is not None and not b.isChecked():
            b.setChecked(True)

    def current(self) -> str | None:
        for k, b in self._buttons.items():
            if b.isChecked():
                return k
        return None

    def _on_clicked(self, button) -> None:
        for k, b in self._buttons.items():
            if b is button:
                self.changed.emit(k)
                return

    def set_compact(self, compact: bool) -> None:
        style = (Qt.ToolButtonStyle.ToolButtonIconOnly if compact
                 else Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        for k, b in self._buttons.items():
            if k in self._icons:
                b.setToolButtonStyle(style)
                if compact and not b.toolTip():
                    b.setToolTip(b.text())

    def retheme(self) -> None:
        for k, name in self._icons.items():
            self._buttons[k].setIconSize(icons.qsize(14))
            self._buttons[k].setIcon(icons.icon(name, 14))


class Switch(QAbstractButton):
    """On/off toggle – drawn, 36×20 px (zoomed), accent when on."""

    def __init__(self, parent=None, checked: bool = False):
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(checked)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.retheme()

    def retheme(self) -> None:
        self.setFixedSize(theme.px(36), theme.px(20))

    def sizeHint(self) -> QSize:
        return QSize(theme.px(36), theme.px(20))

    def paintEvent(self, event):
        t = theme.current()
        w, h = self.width(), self.height()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        on = self.isChecked()
        track = QColor(t.accent if on else t.border)
        if not self.isEnabled():
            track = QColor(t.line)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(track)
        p.drawRoundedRect(QRectF(0, 0, w, h), h / 2, h / 2)
        knob = h - 4
        knob_x = w - knob - 2 if on else 2
        p.setBrush(QColor(t.paper if on else t.canvas))
        p.setPen(QPen(QColor(t.accent_hover if on else t.muted), 1))
        p.drawEllipse(QRectF(knob_x, 2, knob, knob))
        p.end()


# ----------------------------------------------------------------------------
# Containers and feedback
# ----------------------------------------------------------------------------
class Card(QFrame):
    """Card with a left stripe in the colour of a kind and a selected state."""

    clicked = Signal()

    def __init__(self, parent=None, kind: str | None = None):
        super().__init__(parent)
        self.setObjectName("card")
        self.setProperty("selected", "false")
        self._kind = kind
        sp = QSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Minimum)
        sp.setHeightForWidth(True)
        self.setSizePolicy(sp)
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(18, 12, 14, 12)
        self.body.setSpacing(6)

    def set_selected(self, on: bool) -> None:
        self.setProperty("selected", "true" if on else "false")
        repolish(self)

    def set_kind(self, kind: str | None) -> None:
        self._kind = kind
        self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)

    def paintEvent(self, event):
        super().paintEvent(event)
        if not self._kind:
            return
        t = theme.current()
        color = t.accent if self._kind == "accent" else t.semantic_dot.get(self._kind, t.semantic_dot["neutral"])
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(color))
        p.drawRoundedRect(QRectF(1.5, 9, 4, max(4, self.height() - 18)), 2, 2)
        p.end()


class Banner(QFrame):
    """Inline notice: kind icon, title, text, optionally closable."""

    closed = Signal()

    def __init__(self, kind: str, title: str, text: str = "", closable: bool = False, parent=None):
        super().__init__(parent)
        self.setObjectName("banner")
        self.setProperty("kind", kind)
        self._kind = kind
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 10, 10, 10)
        lay.setSpacing(10)
        self.icon_label = QLabel()
        lay.addWidget(self.icon_label, 0, Qt.AlignmentFlag.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(2)
        self.title_label = QLabel(title)
        self.title_label.setObjectName("bannerTitle")
        self.title_label.setWordWrap(True)
        col.addWidget(self.title_label)
        self.text_label = QLabel(text)
        self.text_label.setWordWrap(True)
        self.text_label.setVisible(bool(text))
        col.addWidget(self.text_label)
        lay.addLayout(col, 1)
        self.actions = QHBoxLayout()
        self.actions.setSpacing(6)
        lay.addLayout(self.actions)
        if closable:
            close = IconButton("close", "Close")
            close.clicked.connect(self._close)
            lay.addWidget(close, 0, Qt.AlignmentFlag.AlignTop)
        self.retheme()

    def add_action(self, button: QPushButton) -> None:
        self.actions.addWidget(button)

    def _close(self) -> None:
        self.hide()
        self.closed.emit()

    def retheme(self) -> None:
        self.icon_label.setPixmap(icons.pixmap("info" if self._kind == "info" else
                                               {"warning": "warning", "danger": "error", "success": "success"}.get(self._kind, "info"),
                                               18, theme.current().semantic_dot.get(self._kind)))


class Toast(QFrame):
    """Temporary message floating at the bottom centre of its parent."""

    def __init__(self, parent: QWidget, text: str, kind: str | None = None, timeout: int = 3000):
        super().__init__(parent)
        self.setObjectName("toast")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 8, 10, 8)
        lay.setSpacing(10)
        if kind:
            ic = QLabel()
            ic.setPixmap(icons.semantic_icon(kind, 16).pixmap(icons.qsize(16)))
            lay.addWidget(ic)
        self.label = QLabel(text)
        lay.addWidget(self.label, 1)
        close = IconButton("close", "Close", color=theme.current().canvas)
        close.clicked.connect(self.close)
        lay.addWidget(close)
        self.adjustSize()
        self._reposition()
        parent.installEventFilter(self)
        self.raise_()
        self.show()
        if timeout > 0:
            QTimer.singleShot(timeout, self.close)

    def _reposition(self) -> None:
        pw = self.parentWidget()
        if pw is None:
            return
        self.adjustSize()
        x = (pw.width() - self.width()) // 2
        y = pw.height() - self.height() - theme.px(24)
        self.move(QPoint(max(8, x), max(8, y)))

    def eventFilter(self, obj, event):
        if obj is self.parentWidget() and event.type() == QEvent.Type.Resize:
            self._reposition()
        return False

    @staticmethod
    def show_message(parent: QWidget, text: str, kind: str | None = None, timeout: int = 3000) -> "Toast":
        return Toast(parent, text, kind, timeout)


class EmptyState(QFrame):
    """Empty state: icon, heading, explanation and optionally an action."""

    def __init__(self, icon_name: str, title: str, hint: str = "", action: QPushButton | None = None, parent=None):
        super().__init__(parent)
        self.setObjectName("emptyState")
        self._icon_name = icon_name
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 28, 24, 28)
        lay.setSpacing(6)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.icon_label = QLabel()
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.icon_label)
        self.title_label = Heading(title, 2)
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.title_label)
        self.hint_label = QLabel(hint)
        self.hint_label.setObjectName("faintLabel")
        self.hint_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.hint_label.setWordWrap(True)
        self.hint_label.setVisible(bool(hint))
        lay.addWidget(self.hint_label)
        if action is not None:
            lay.addSpacing(8)
            lay.addWidget(action, 0, Qt.AlignmentFlag.AlignCenter)
        self.retheme()

    def retheme(self) -> None:
        self.icon_label.setPixmap(icons.pixmap(self._icon_name, 36, theme.current().muted))


class Breadcrumb(QWidget):
    """Path „Home › Project › Task"; clicking a segment emits navigated(index)."""

    navigated = Signal(int)

    def __init__(self, parts=(), parent=None):
        super().__init__(parent)
        self._lay = QHBoxLayout(self)
        self._lay.setContentsMargins(0, 0, 0, 0)
        self._lay.setSpacing(4)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._parts: list[str] = []
        self.set_parts(parts)

    def set_parts(self, parts) -> None:
        self._parts = list(parts)
        while self._lay.count():
            item = self._lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for i, part in enumerate(self._parts):
            if i:
                sep = QLabel()
                sep.setPixmap(icons.pixmap("chevron_right", 12, theme.current().muted))
                self._lay.addWidget(sep)
            last = i == len(self._parts) - 1
            if last:
                lab = QLabel(html.escape(part))
                lab.setObjectName("pathLabel")
                self._lay.addWidget(lab)
            else:
                b = QuietButton(part)
                b.setProperty("size", "sm")
                b.clicked.connect(lambda _=False, idx=i: self.navigated.emit(idx))
                self._lay.addWidget(b)
        self._lay.addStretch(1)

    def retheme(self) -> None:
        self.set_parts(self._parts)


class SearchField(QLineEdit):
    """Search field with a magnifier on the left and a clear button."""

    def __init__(self, placeholder: str = "Search…", parent=None):
        super().__init__(parent)
        self.setObjectName("search")
        self.setPlaceholderText(placeholder)
        self.setClearButtonEnabled(True)
        self._action = self.addAction(icons.icon("search"), QLineEdit.ActionPosition.LeadingPosition)
        self.setMinimumWidth(60)

    def retheme(self) -> None:
        self._action.setIcon(icons.icon("search"))


class NavList(QListWidget):
    """Side navigation: items with icons, the selected one has an accent stripe."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("nav")
        self._icons: dict[int, str] = {}
        self.setSpacing(1)
        self.retheme()

    def add(self, text: str, icon_name: str | None = None, key=None):
        from PySide6.QtWidgets import QListWidgetItem
        it = QListWidgetItem(text)
        if key is not None:
            it.setData(Qt.ItemDataRole.UserRole, key)
        self.addItem(it)
        if icon_name:
            self._icons[self.count() - 1] = icon_name
            it.setIcon(icons.icon(icon_name))
        return it

    def retheme(self) -> None:
        self.setIconSize(icons.qsize())
        for row, name in self._icons.items():
            self.item(row).setIcon(icons.icon(name))


class DropZone(QFrame):
    """Dashed „drop here" area; highlights while dragging (property active)."""

    dropped = Signal(list)  # list of paths

    def __init__(self, text: str = "Drop files here", parent=None):
        super().__init__(parent)
        self.setObjectName("dropZone")
        self.setProperty("active", "false")
        self.setAcceptDrops(True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 18, 16, 18)
        self.label = QLabel(text)
        self.label.setObjectName("faintLabel")
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.label)

    def _set_active(self, on: bool) -> None:
        self.setProperty("active", "true" if on else "false")
        repolish(self)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
            self._set_active(True)

    def dragLeaveEvent(self, e):
        self._set_active(False)

    def dropEvent(self, e):
        self._set_active(False)
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        if paths:
            self.dropped.emit(paths)
        e.acceptProposedAction()


def font_for(widget: QWidget, mono: bool = False, pt: float | None = None) -> QFont:
    """Convenience: themed font (UI or mono) at the current zoom."""
    return theme.mono_font(pt or theme.SIZE_MONO) if mono else theme.ui_font(pt or theme.SIZE_BODY)
