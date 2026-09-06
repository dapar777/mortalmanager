"""Drive bar – one framed icon button per drive, refreshed off the GUI thread.

Enumerating drives can block (network shares, empty optical drives), so the
list is produced by a QThreadPool worker and applied through a signal; the
widgets are only rebuilt when the set of drives actually changed.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Signal
from PySide6.QtWidgets import QHBoxLayout, QSizePolicy, QWidget

from src.core.file_model import DriveInfo, format_size
from src.solarqt import theme
from src.solarqt.widgets import IconButton

_TYPE_ICONS = {
    "REMOVABLE": "usb", "CDROM": "disc", "NETWORK": "network", "RAMDISK": "drive",
}


class _Signals(QObject):
    done = Signal(list)


class _DriveWorker(QRunnable):
    def __init__(self, signals: _Signals) -> None:
        super().__init__()
        self._signals = signals
        self.setAutoDelete(True)

    def run(self) -> None:
        try:
            from src.filesystem.drives import get_all_drives
            drives = get_all_drives()
        except Exception:
            drives = []
        self._signals.done.emit(drives)


class DriveBar(QWidget):
    """Horizontal row of drive buttons; ``drive_selected(root)`` on click."""

    drive_selected = Signal(str)
    drives_updated = Signal(list)   # list[DriveInfo]

    REFRESH_MS = 20_000

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(theme.px(2))
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self._buttons: dict[str, IconButton] = {}
        self._drives: list[DriveInfo] = []
        self._fingerprint: tuple = ()
        self._current_root = ""
        self._signals = _Signals()
        self._signals.done.connect(self._apply)
        self._timer = QTimer(self)
        self._timer.setInterval(self.REFRESH_MS)
        self._timer.timeout.connect(self.refresh)
        self._timer.start()
        self.refresh()

    # ------------------------------------------------------------------ public

    def refresh(self) -> None:
        QThreadPool.globalInstance().start(_DriveWorker(self._signals))

    def drives(self) -> list[DriveInfo]:
        return list(self._drives)

    def drive_for(self, path: str) -> DriveInfo | None:
        norm = path.lower()
        best = None
        for d in self._drives:
            root = d.root.lower()
            if norm.startswith(root) and (best is None or len(root) > len(best.root)):
                best = d
        return best

    def set_current_path(self, path: str) -> None:
        d = self.drive_for(path)
        self._current_root = d.root if d else ""
        for root, b in self._buttons.items():
            b.setChecked(root == self._current_root)

    def retheme(self) -> None:
        self._layout.setSpacing(theme.px(2))

    # ------------------------------------------------------------------ internals

    @staticmethod
    def _icon_for(d: DriveInfo) -> str:
        return _TYPE_ICONS.get(d.drive_type, "drive")

    @staticmethod
    def _tooltip(d: DriveInfo) -> str:
        label = d.label or d.drive_type.title()
        return f"{label}  ({d.filesystem})\n{d.free_display} free of {d.total_display}"

    def _apply(self, drives: list[DriveInfo]) -> None:
        self._drives = drives
        fp = tuple((d.root, d.drive_type) for d in drives)
        if fp != self._fingerprint:
            self._fingerprint = fp
            for b in self._buttons.values():
                self._layout.removeWidget(b)
                b.deleteLater()
            self._buttons.clear()
            for d in drives:
                b = IconButton(self._icon_for(d), self._tooltip(d), text=f"{d.letter}:", framed=True)
                b.setCheckable(True)
                b.clicked.connect(lambda _=False, root=d.root: self.drive_selected.emit(root))
                self._layout.addWidget(b)
                self._buttons[d.root] = b
            for root, b in self._buttons.items():
                b.setChecked(root == self._current_root)
        else:
            for d in drives:
                b = self._buttons.get(d.root)
                if b is not None:
                    b.setToolTip(self._tooltip(d))
        self.drives_updated.emit(drives)

    @staticmethod
    def free_text(d: DriveInfo | None) -> str:
        if d is None:
            return ""
        return f"{d.letter}: {format_size(d.free)} free"
