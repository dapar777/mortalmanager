"""File viewer window – F3 equivalent.

Supports: text, log, csv, json, xml, html, markdown, hex dump, images.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QFont, QKeySequence, QPixmap, QTextOption
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStatusBar,
    QTabWidget,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

logger = logging.getLogger(__name__)

_TEXT_EXTENSIONS = {
    "txt", "log", "csv", "ini", "cfg", "conf", "md", "rst",
    "json", "xml", "html", "htm", "yaml", "yml", "toml",
    "py", "js", "ts", "css", "sh", "bat", "ps1", "sql",
}
_IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "bmp", "webp", "svg", "tiff", "ico"}


class _LoadThread(QThread):
    loaded = Signal(bytes)
    error = Signal(str)

    def __init__(self, path: str) -> None:
        super().__init__()
        self._path = path

    def run(self) -> None:
        try:
            self.loaded.emit(Path(self._path).read_bytes())
        except Exception as exc:
            self.error.emit(str(exc))


class HexView(QPlainTextEdit):
    """Read-only hex dump widget."""

    BYTES_PER_ROW = 16

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setReadOnly(True)
        self.setFont(QFont("Consolas", 9))
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)

    def load_bytes(self, data: bytes) -> None:
        lines: list[str] = []
        for i in range(0, len(data), self.BYTES_PER_ROW):
            chunk = data[i : i + self.BYTES_PER_ROW]
            hex_part = " ".join(f"{b:02X}" for b in chunk)
            ascii_part = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
            lines.append(f"{i:08X}  {hex_part:<{self.BYTES_PER_ROW * 3}}  {ascii_part}")
        self.setPlainText("\n".join(lines))


class ImageView(QScrollArea):
    """Scrollable image viewer."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._label = QLabel()
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setWidget(self._label)
        self.setWidgetResizable(True)

    def load_bytes(self, data: bytes) -> None:
        pixmap = QPixmap()
        if pixmap.loadFromData(data):
            max_w = self.width() - 20
            max_h = self.height() - 20
            if pixmap.width() > max_w or pixmap.height() > max_h:
                pixmap = pixmap.scaled(
                    max_w, max_h,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            self._label.setPixmap(pixmap)
        else:
            self._label.setText("Cannot load image.")


class FileViewerWindow(QDialog):
    """F3 file viewer dialog."""

    def __init__(
        self, file_path: str, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self._file_path = file_path
        self._path = Path(file_path)
        self.setWindowTitle(f"Viewer – {self._path.name}")
        self.resize(900, 650)
        self._build_ui()
        self._load_file()

    # ------------------------------------------------------------------ UI build

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        # Toolbar
        tb = QToolBar()
        tb.setMovable(False)
        self._btn_text = tb.addAction("Text")
        self._btn_hex = tb.addAction("Hex")
        self._btn_image = tb.addAction("Image")
        self._btn_text.triggered.connect(lambda: self._tabs.setCurrentIndex(0))
        self._btn_hex.triggered.connect(lambda: self._tabs.setCurrentIndex(1))
        self._btn_image.triggered.connect(lambda: self._tabs.setCurrentIndex(2))
        layout.addWidget(tb)

        # Tab widget
        self._tabs = QTabWidget()

        # Text tab
        self._text_view = QPlainTextEdit()
        self._text_view.setReadOnly(True)
        self._text_view.setFont(QFont("Consolas", 10))
        self._text_view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self._tabs.addTab(self._text_view, "Text")

        # Hex tab
        self._hex_view = HexView()
        self._tabs.addTab(self._hex_view, "Hex")

        # Image tab
        self._image_view = ImageView()
        self._tabs.addTab(self._image_view, "Image")

        layout.addWidget(self._tabs)

        # Status
        self._status = QStatusBar()
        layout.addWidget(self._status)
        self._status.showMessage(str(self._path))

        # Close shortcut
        from PySide6.QtGui import QShortcut
        QShortcut(QKeySequence("Escape"), self, self.close)
        QShortcut(QKeySequence("F3"), self, self.close)

    # ------------------------------------------------------------------ loading

    def _load_file(self) -> None:
        self._status.showMessage(f"Loading {self._path.name}...")
        self._thread = _LoadThread(self._file_path)
        self._thread.loaded.connect(self._on_loaded)
        self._thread.error.connect(self._on_error)
        self._thread.start()

    def _on_loaded(self, data: bytes) -> None:
        size = len(data)
        ext = self._path.suffix.lstrip(".").lower()

        # Determine default tab
        if ext in _IMAGE_EXTENSIONS:
            self._tabs.setCurrentIndex(2)
            self._image_view.load_bytes(data)
        else:
            self._tabs.setCurrentIndex(0)

        # Always load hex
        self._hex_view.load_bytes(data)

        # Load image
        if ext in _IMAGE_EXTENSIONS:
            self._image_view.load_bytes(data)

        # Load text
        if ext in _TEXT_EXTENSIONS or size < 2 * 1024 * 1024:
            try:
                import chardet
                detected = chardet.detect(data)
                enc = detected.get("encoding") or "utf-8"
            except ImportError:
                enc = "utf-8"
            try:
                text = data.decode(enc, errors="replace")
                self._text_view.setPlainText(text)
            except Exception:
                self._text_view.setPlainText("<binary content – use Hex tab>")

        self._status.showMessage(
            f"{self._path.name}  |  {size:,} bytes  |  {self._path.suffix or 'no ext'}"
        )

    def _on_error(self, msg: str) -> None:
        self._text_view.setPlainText(f"Error loading file:\n{msg}")
        self._status.showMessage(f"Error: {msg}")
