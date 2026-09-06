"""Embedded terminal widget – runs commands, shows output inline."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QRunnable, QThreadPool, Qt, Signal
from PySide6.QtGui import QColor, QTextCursor
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from src.database.db import DatabaseManager
from src.solarqt import theme
from src.solarqt.widgets import IconButton


# ------------------------------------------------------------------ runner


class _RunnerSignals(QObject):
    finished = Signal(str, str, int)  # stdout, stderr, returncode


class _CmdRunner(QRunnable):
    """Runs a shell command in a thread-pool worker."""

    def __init__(
        self,
        cmd: str,
        shell_type: str,
        bash_path: str | None,
        cwd: str,
    ) -> None:
        super().__init__()
        self.signals = _RunnerSignals()
        self._cmd = cmd
        self._shell_type = shell_type
        self._bash = bash_path
        self._cwd = cwd

    def run(self) -> None:  # called in thread pool
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            if self._shell_type == "PowerShell":
                result = subprocess.run(
                    ["powershell.exe", "-NoProfile", "-Command", self._cmd],
                    cwd=self._cwd, capture_output=True, text=True,
                    encoding="utf-8", errors="replace", timeout=30, creationflags=flags,
                )
            elif self._shell_type == "Git Bash" and self._bash:
                result = subprocess.run(
                    [self._bash, "-c", self._cmd],
                    cwd=self._cwd, capture_output=True, text=True,
                    encoding="utf-8", errors="replace", timeout=30, creationflags=flags,
                )
            else:  # CMD
                result = subprocess.run(
                    self._cmd, shell=True, cwd=self._cwd, capture_output=True, text=True,
                    encoding="mbcs", errors="replace", timeout=30, creationflags=flags,
                )
            self.signals.finished.emit(result.stdout, result.stderr, result.returncode)
        except subprocess.TimeoutExpired:
            self.signals.finished.emit("", "[timeout – command ran >30 s]", -1)
        except Exception as exc:
            self.signals.finished.emit("", str(exc), -1)


# ------------------------------------------------------------------ widget


class EmbeddedTerminalWidget(QFrame):
    """Bottom-of-window terminal pane."""

    # Emitted when the user `cd`s to a new directory so panels can follow.
    cwd_changed = Signal(str)
    # Alt++ / Alt+- in the command line: grow / shrink the pane (+1 / -1 step);
    # the owner resizes the splitter and reverts when focus leaves the pane.
    height_step = Signal(int)

    def __init__(
        self,
        initial_cwd: str,
        db: DatabaseManager,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("terminalPane")
        self._db = db
        self._cwd = initial_cwd
        self._hist_idx = -1
        self._hist_saved = ""
        self._pool = QThreadPool.globalInstance()
        self._build_ui()
        self._print_welcome()

    # ------------------------------------------------------------------ public API

    def set_cwd(self, path: str) -> None:
        self._cwd = path
        self._update_prompt()

    def give_focus(self) -> None:
        self._input.setFocus()

    def current_shell(self) -> str:
        return self._shell_combo.currentText()

    def clear_output(self) -> None:
        self._output.clear()

    def run_command(self, cmd: str) -> None:
        """Execute ``cmd`` as if typed."""
        self._input.setText(cmd)
        self._on_return()

    def prefill(self, cmd: str) -> None:
        """Put ``cmd`` into the command line (not executed) and focus it."""
        self._input.setText(cmd)
        self._input.setCursorPosition(len(cmd))
        self._input.setFocus()

    def history(self, limit: int = 60) -> list[str]:
        try:
            return self._db.get_command_history(self._cwd, shell="", limit=limit)
        except Exception:
            return []

    def retheme(self) -> None:
        """Fonts follow the zoom factor; colours are taken per line from the theme."""
        mono = theme.mono_font()
        self._output.setFont(mono)
        self._prompt_lbl.setFont(mono)
        self._input.setFont(mono)
        self._row_layout.setSpacing(theme.px(6))
        self._layout.setContentsMargins(theme.px(6), theme.px(4), theme.px(6), theme.px(4))

    def set_font_size(self, pt: int) -> None:  # backwards compatibility
        self.retheme()

    # ------------------------------------------------------------------ build

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(theme.px(6), theme.px(4), theme.px(6), theme.px(4))
        layout.setSpacing(theme.px(4))
        self._layout = layout

        self._output = QPlainTextEdit()
        self._output.setObjectName("terminalOutput")
        self._output.setReadOnly(True)
        self._output.setMaximumBlockCount(3000)
        self._output.setFrameShape(QFrame.Shape.NoFrame)
        layout.addWidget(self._output, stretch=1)

        row = QWidget()
        rl = QHBoxLayout(row)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(theme.px(6))
        self._row_layout = rl

        self._shell_combo = QComboBox()
        shells = ["CMD", "PowerShell"]
        if self._find_git_bash():
            shells.append("Git Bash")
        self._shell_combo.addItems(shells)
        self._shell_combo.setToolTip("Shell used for commands")
        self._shell_combo.currentTextChanged.connect(self._on_shell_changed)

        self._prompt_lbl = QLabel()
        self._prompt_lbl.setObjectName("terminalPrompt")
        self._update_prompt()

        self._input = QLineEdit()
        self._input.setObjectName("terminalInput")
        self._input.setPlaceholderText(
            "command…   ↑↓ history   Tab complete   Alt+± pane height   Ctrl+Up back to panel")
        self._input.returnPressed.connect(self._on_return)
        self._input.installEventFilter(self)

        btn_clear = IconButton("trash", "Clear output (Ctrl+E)")
        btn_clear.clicked.connect(self.clear_output)

        rl.addWidget(self._shell_combo)
        rl.addWidget(self._prompt_lbl)
        rl.addWidget(self._input, stretch=1)
        rl.addWidget(btn_clear)
        layout.addWidget(row)
        self.retheme()

    # ------------------------------------------------------------------ display

    def _print_welcome(self) -> None:
        self._write(f"MortalManager terminal  [{self.current_shell()}]  {self._cwd}", "muted")

    def _update_prompt(self) -> None:
        p = Path(self._cwd)
        label = f"{p.drive}\\…\\{p.name}>" if len(str(p)) > 32 else f"{p}>"
        self._prompt_lbl.setText(label)
        self._prompt_lbl.setToolTip(self._cwd)

    def _on_shell_changed(self, shell: str) -> None:
        self._write(f"[{shell}]", "muted")
        self._hist_idx = -1

    def _color(self, kind: str) -> QColor:
        t = theme.current()
        if kind == "muted":
            return QColor(t.muted)
        if kind in t.semantic_fg:
            return QColor(t.semantic_fg[kind])
        return QColor(t.text)

    def _write(self, text: str, kind: str = "") -> None:
        cursor = self._output.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        fmt = cursor.charFormat()
        fmt.setForeground(self._color(kind))
        cursor.setCharFormat(fmt)
        cursor.insertText(text + "\n")
        self._output.setTextCursor(cursor)
        self._output.ensureCursorVisible()

    # ------------------------------------------------------------------ run command

    def _on_return(self) -> None:
        cmd = self._input.text().strip()
        if not cmd:
            return
        self._input.clear()
        self._hist_idx = -1
        self._write(f"{self._prompt_lbl.text()} {cmd}", "info")

        parts = cmd.split(None, 1)
        if parts[0].lower() in ("cd", "chdir"):
            self._db.add_command_history(cmd, self._cwd, self.current_shell())
            self._handle_cd(parts[1].strip().strip('"').strip("'") if len(parts) > 1 else "")
            return
        if parts[0].lower() in ("cls", "clear"):
            self.clear_output()
            return

        shell = self.current_shell()
        self._db.add_command_history(cmd, self._cwd, shell)
        runner = _CmdRunner(cmd, shell, self._find_git_bash(), self._cwd)
        runner.signals.finished.connect(self._on_finished)
        runner.setAutoDelete(True)
        self._pool.start(runner)

    def _handle_cd(self, target: str) -> None:
        if not target:
            return
        new = Path(self._cwd) / target if not Path(target).is_absolute() else Path(target)
        try:
            resolved = new.resolve()
            if resolved.is_dir():
                self._cwd = str(resolved)
                self._update_prompt()
                self._write(f"  → {self._cwd}", "success")
                self.cwd_changed.emit(self._cwd)
            else:
                self._write(f"  Not a directory: {resolved}", "danger")
        except Exception as exc:
            self._write(f"  Error: {exc}", "danger")

    def _on_finished(self, stdout: str, stderr: str, rc: int) -> None:
        if stdout.strip():
            self._write(stdout.rstrip())
        if stderr.strip():
            self._write(stderr.rstrip(), "danger")
        if rc not in (0, -1):
            self._write(f"  [exit {rc}]", "warning")

    # ------------------------------------------------------------------ event filter

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802
        if obj is self._input and event.type() == QEvent.Type.KeyPress:
            key = event.key()
            if event.modifiers() & Qt.KeyboardModifier.AltModifier:
                # '+' is unshifted on Czech layouts and Shift+'=' on US ones
                if key in (Qt.Key.Key_Plus, Qt.Key.Key_Equal) or event.text() == "+":
                    self.height_step.emit(+1)
                    return True
                if key == Qt.Key.Key_Minus or event.text() == "-":
                    self.height_step.emit(-1)
                    return True
            if key == Qt.Key.Key_Up:
                self._hist_step(+1)
                return True
            if key == Qt.Key.Key_Down:
                self._hist_step(-1)
                return True
            if key == Qt.Key.Key_Tab:
                self._tab_complete()
                return True
        return False

    def _hist_step(self, direction: int) -> None:
        try:
            shell = self.current_shell()
            history = self._db.get_command_history(self._cwd, shell=shell)
            if not history:
                return
            if self._hist_idx == -1:
                self._hist_saved = self._input.text()
            new_idx = max(-1, min(self._hist_idx + direction, len(history) - 1))
            self._hist_idx = new_idx
            text = self._hist_saved if new_idx == -1 else str(history[new_idx])
            self._input.setText(text)
            self._input.setCursorPosition(len(text))
        except Exception as exc:
            self._write(f"history error: {exc}", "danger")

    def _tab_complete(self) -> None:
        import glob

        text = self._input.text()
        pos = self._input.cursorPosition()
        before = text[:pos]
        ws = max(before.rfind(" ") + 1, before.rfind("\t") + 1)
        word = before[ws:].strip('"').strip("'")
        pat = word + "*" if Path(word).is_absolute() else str(Path(self._cwd) / word) + "*"
        matches = sorted(glob.glob(pat), key=lambda p: (not Path(p).is_dir(), p.lower()))
        if not matches:
            return
        m = Path(matches[0])
        try:
            rel = str(m.relative_to(self._cwd)) if not Path(word).is_absolute() else str(m)
        except ValueError:
            rel = str(m)
        if m.is_dir():
            rel += "\\"
        self._input.setText(text[:ws] + rel + text[pos:])
        self._input.setCursorPosition(ws + len(rel))

    # ------------------------------------------------------------------ static helpers

    @staticmethod
    def _find_git_bash() -> str | None:
        for p in [
            r"C:\Program Files\Git\bin\bash.exe",
            r"C:\Program Files (x86)\Git\bin\bash.exe",
        ]:
            if os.path.isfile(p):
                return p
        return None
