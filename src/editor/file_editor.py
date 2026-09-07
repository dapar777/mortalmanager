"""File editor window – F4 equivalent.

Supports: syntax highlighting, multiple tabs, UTF-8/UTF-16/ANSI, save.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import (
    QColor,
    QKeySequence,
    QShortcut,
    QSyntaxHighlighter,
    QTextCharFormat,
    QTextDocument,
)
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QStatusBar,
    QTabWidget,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from src.solarqt import icons, theme

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------ syntax highlighter

class _PythonHighlighter(QSyntaxHighlighter):
    """Minimal Python syntax highlighter."""

    _KEYWORDS = {
        "False", "None", "True", "and", "as", "assert", "async", "await",
        "break", "class", "continue", "def", "del", "elif", "else", "except",
        "finally", "for", "from", "global", "if", "import", "in", "is",
        "lambda", "nonlocal", "not", "or", "pass", "raise", "return",
        "try", "while", "with", "yield",
    }

    def __init__(self, document: QTextDocument) -> None:
        super().__init__(document)
        # Solarized code colours: keywords green, strings cyan, numbers magenta,
        # comments in the theme's secondary text (readable in both themes)
        kw_fmt = QTextCharFormat()
        kw_fmt.setForeground(QColor(theme.GREEN))
        kw_fmt.setFontWeight(700)

        str_fmt = QTextCharFormat()
        str_fmt.setForeground(QColor(theme.CYAN))

        cmt_fmt = QTextCharFormat()
        cmt_fmt.setForeground(QColor(theme.current().text2))
        cmt_fmt.setFontItalic(True)

        num_fmt = QTextCharFormat()
        num_fmt.setForeground(QColor(theme.MAGENTA))

        self._rules: list[tuple[str, QTextCharFormat]] = [
            (r"#[^\n]*", cmt_fmt),
            (r'"[^"\\]*(?:\\.[^"\\]*)*"', str_fmt),
            (r"'[^'\\]*(?:\\.[^'\\]*)*'", str_fmt),
            (r"\b\d+\.?\d*\b", num_fmt),
        ]
        self._kw_fmt = kw_fmt
        self._kw_pattern = r"\b(" + "|".join(self._KEYWORDS) + r")\b"

    def highlightBlock(self, text: str) -> None:
        import re
        for pattern, fmt in self._rules:
            for m in re.finditer(pattern, text):
                self.setFormat(m.start(), m.end() - m.start(), fmt)
        for m in re.finditer(self._kw_pattern, text):
            self.setFormat(m.start(), m.end() - m.start(), self._kw_fmt)


_HIGHLIGHTERS: dict[str, type[QSyntaxHighlighter]] = {
    "py": _PythonHighlighter,
    "pyw": _PythonHighlighter,
}


def _get_highlighter(ext: str) -> type[QSyntaxHighlighter] | None:
    return _HIGHLIGHTERS.get(ext.lower())


# ------------------------------------------------------------------ editor tab

class _EditorTab(QWidget):
    """A single editor tab with a text area."""

    def __init__(self, file_path: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._file_path = file_path
        self._path = Path(file_path)
        self._encoding = "utf-8"
        self._modified = False
        self._highlighter: QSyntaxHighlighter | None = None
        self._build_ui()
        self._load()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._editor = QPlainTextEdit()
        self._editor.setFont(theme.mono_font(10))
        self._editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self._editor.document().contentsChanged.connect(self._on_changed)
        layout.addWidget(self._editor)

    def _load(self) -> None:
        try:
            raw = self._path.read_bytes()
            try:
                import chardet
                detected = chardet.detect(raw)
                self._encoding = detected.get("encoding") or "utf-8"
            except ImportError:
                self._encoding = "utf-8"
            text = raw.decode(self._encoding, errors="replace")
            self._editor.setPlainText(text)
            # Apply syntax highlighting
            ext = self._path.suffix.lstrip(".")
            cls = _get_highlighter(ext)
            if cls:
                self._highlighter = cls(self._editor.document())
        except Exception as exc:
            self._editor.setPlainText(f"Error loading: {exc}")

    def _on_changed(self) -> None:
        self._modified = True

    def save(self) -> bool:
        try:
            text = self._editor.toPlainText()
            self._path.write_bytes(text.encode(self._encoding, errors="replace"))
            self._modified = False
            return True
        except Exception as exc:
            QMessageBox.critical(self, "Save Error", str(exc))
            return False

    def save_as(self, new_path: str) -> bool:
        self._file_path = new_path
        self._path = Path(new_path)
        return self.save()

    @property
    def is_modified(self) -> bool:
        return self._modified

    @property
    def file_path(self) -> str:
        return self._file_path

    @property
    def encoding(self) -> str:
        return self._encoding


# ------------------------------------------------------------------ main editor window

class FileEditorWindow(QDialog):
    """F4 multi-tab file editor."""

    def __init__(
        self, file_paths: list[str], parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Editor – Ultimate Commander")
        self.resize(1000, 700)
        self._build_ui()
        for fp in file_paths:
            self._open_file(fp)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        # Toolbar
        tb = QToolBar()
        tb.setMovable(False)
        self._act_save = tb.addAction(icons.icon("save"), "Save (Ctrl+S)")
        self._act_save_all = tb.addAction(icons.icon("download"), "Save All")
        self._act_close_tab = tb.addAction(icons.icon("close"), "Close Tab")
        self._act_save.triggered.connect(self._save_current)
        self._act_save_all.triggered.connect(self._save_all)
        self._act_close_tab.triggered.connect(self._close_current_tab)
        layout.addWidget(tb)

        self._tabs = QTabWidget()
        self._tabs.setTabsClosable(True)
        self._tabs.tabCloseRequested.connect(self._close_tab)
        layout.addWidget(self._tabs)

        self._status = QStatusBar()
        layout.addWidget(self._status)

        # Shortcuts
        QShortcut(QKeySequence("Ctrl+S"), self, self._save_current)
        QShortcut(QKeySequence("Escape"), self, self._on_escape)
        QShortcut(QKeySequence("F4"), self, self.close)

    def _open_file(self, file_path: str) -> None:
        tab = _EditorTab(file_path)
        name = Path(file_path).name
        idx = self._tabs.addTab(tab, name)
        self._tabs.setCurrentIndex(idx)
        self._status.showMessage(f"Opened: {file_path}")

    def _save_current(self) -> None:
        tab = self._current_tab()
        if tab and tab.save():
            self._update_tab_title(self._tabs.currentIndex(), tab)
            self._status.showMessage(f"Saved: {tab.file_path}")

    def _save_all(self) -> None:
        for i in range(self._tabs.count()):
            tab = self._tabs.widget(i)
            if isinstance(tab, _EditorTab):
                tab.save()
                self._update_tab_title(i, tab)
        self._status.showMessage("All files saved.")

    def _close_current_tab(self) -> None:
        self._close_tab(self._tabs.currentIndex())

    def _close_tab(self, index: int) -> None:
        tab = self._tabs.widget(index)
        if isinstance(tab, _EditorTab) and tab.is_modified:
            res = QMessageBox.question(
                self,
                "Unsaved changes",
                f"Save changes to {Path(tab.file_path).name}?",
                QMessageBox.StandardButton.Save
                | QMessageBox.StandardButton.Discard
                | QMessageBox.StandardButton.Cancel,
            )
            if res == QMessageBox.StandardButton.Save:
                tab.save()
            elif res == QMessageBox.StandardButton.Cancel:
                return
        self._tabs.removeTab(index)
        if self._tabs.count() == 0:
            self.close()

    def _on_escape(self) -> None:
        if self._tabs.count() == 0:
            self.close()

    def _current_tab(self) -> _EditorTab | None:
        w = self._tabs.currentWidget()
        return w if isinstance(w, _EditorTab) else None

    def _update_tab_title(self, index: int, tab: _EditorTab) -> None:
        name = Path(tab.file_path).name
        suffix = " *" if tab.is_modified else ""
        self._tabs.setTabText(index, name + suffix)
