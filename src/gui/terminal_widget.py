"""Embedded terminal widget – runs commands, shows output inline."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable
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

from src.core.cmdline import CmdContext, expand, has_placeholders, quote
from src.database.db import DatabaseManager
from src.solarqt import theme
from src.solarqt.widgets import IconButton
from .terminal_session import ReplSession, classify, open_in_console


# ------------------------------------------------------------------ runner


class _RunnerSignals(QObject):
    finished = Signal(str, str, int, object)  # stdout, stderr, returncode, token of the issuing command


class _CmdRunner(QRunnable):
    """Runs a shell command in a thread-pool worker."""

    def __init__(
        self,
        cmd: str,
        shell_type: str,
        bash_path: str | None,
        cwd: str,
        token: object = None,
    ) -> None:
        super().__init__()
        self.signals = _RunnerSignals()
        self._token = token
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
            self.signals.finished.emit(result.stdout, result.stderr, result.returncode, self._token)
        except subprocess.TimeoutExpired:
            self.signals.finished.emit("", "[timeout – command ran >30 s]", -1, self._token)
        except Exception as exc:
            self.signals.finished.emit("", str(exc), -1, self._token)


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
        self._session: ReplSession | None = None   # interactive child (python, node…) taking the input lines
        self._context: Callable[[], CmdContext] | None = None   # panels' state for %N %P %T %S %R (main window)
        self._queue: list[str] = []                # remaining commands of a %SI / %RI run
        self._echo_expanded = False
        self._token: object | None = None          # identifies the command whose result is awaited
        self._at_line_start = True
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
        self._at_line_start = True

    def set_context_provider(self, provider: Callable[[], CmdContext]) -> None:
        """Main window supplies the panel state used for %N %P %T %S %R."""
        self._context = provider

    def insert_text(self, text: str) -> None:
        """Ctrl+Enter from a panel: put ``text`` (quoted if needed) at the cursor and focus the line."""
        cur = self._input.text()
        pos = self._input.cursorPosition()
        piece = quote(text)
        if pos > 0 and not cur[pos - 1].isspace():
            piece = " " + piece
        piece += " "
        self._input.setText(cur[:pos] + piece + cur[pos:])
        self._input.setCursorPosition(pos + len(piece))
        self._input.setFocus()

    def session_active(self) -> bool:
        return self._session is not None

    def stop_session(self) -> None:
        """Kill the interactive child (Ctrl+C in the input, palette)."""
        if self._session is not None:
            self._session.kill()

    def shutdown(self) -> None:
        """Window is closing – do not leave a REPL behind."""
        if self._session is not None:
            s, self._session = self._session, None
            s.kill()

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
            "command…   ↑↓ history   Tab complete   Ctrl+Enter file name   %N %P %T %S %R %SI %RI   "
            "Alt+± pane height   Ctrl+Up back to panel")
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
        """One or more whole lines (starts on a fresh line, ends with a newline)."""
        self._write_raw(("" if self._at_line_start else "\n") + text + "\n", kind)

    def _write_raw(self, text: str, kind: str = "") -> None:
        """Verbatim chunk (REPL output may leave the cursor mid-line)."""
        if not text:
            return
        cursor = self._output.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        fmt = cursor.charFormat()
        fmt.setForeground(self._color(kind))
        cursor.setCharFormat(fmt)
        cursor.insertText(text.replace("\r\n", "\n").replace("\r", ""))
        self._output.setTextCursor(cursor)
        self._output.ensureCursorVisible()
        self._at_line_start = text.endswith(("\n", "\r"))

    # ------------------------------------------------------------------ run command

    def _on_return(self) -> None:
        if self._session is not None:
            line = self._input.text()
            self._input.clear()
            self._write(f"{self._prompt_lbl.text()} {line}", "info")
            self._on_session_prompt("")        # cleared until the program prompts again
            lines = [line]
            if has_placeholders(line, strict=True) and self._context is not None:
                # raw values: the user wrote the quotes (print('%SI')); %SI / %RI = one line per entry
                lines = expand(line, self._context(), quoted=False)
                for expanded in lines:
                    self._write(f"  > {expanded}", "muted")
            for expanded in lines:
                self._session.send(expanded)
            return
        raw = self._input.text().strip()
        if not raw:
            return
        self._input.clear()
        self._hist_idx = -1
        self._write(f"{self._prompt_lbl.text()} {raw}", "info")
        if raw.split(None, 1)[0].lower() in ("cls", "clear"):
            self.clear_output()
            return
        self._db.add_command_history(raw, self._cwd, self.current_shell())
        cmds = [raw]
        if has_placeholders(raw) and self._context is not None:
            cmds = expand(raw, self._context())
            if len(cmds) > 1:
                self._write(f"  [{len(cmds)} commands, one per selected entry]", "muted")
        self._echo_expanded = cmds != [raw]      # show what the placeholders became
        self._queue = cmds[1:]
        self._run_one(cmds[0])

    def _run_one(self, cmd: str) -> None:
        """Run one expanded command line (cd / REPL / console / captured)."""
        if not cmd.strip():
            self._next_queued()
            return
        if self._echo_expanded:
            self._write(f"  > {cmd}", "muted")
        parts = cmd.split(None, 1)
        if len(parts) == 1 and len(parts[0]) == 2 and parts[0][1] == ":" and parts[0][0].isalpha():
            self._handle_cd(parts[0].upper() + "\\")     # "d:" alone = change drive (Total Commander / cmd)
            self._next_queued()
            return
        if parts[0].lower() in ("cd", "chdir"):
            target = parts[1].strip() if len(parts) > 1 else ""
            if target.lower().startswith("/d "):          # cmd's "cd /d X:\path"
                target = target[3:].strip()
            self._handle_cd(target.strip('"').strip("'"))
            self._next_queued()
            return

        shell = self.current_shell()
        kind = classify(cmd)
        if kind == "repl":
            self._start_session(cmd)
            return
        if kind == "console":
            try:
                open_in_console(cmd, self._cwd)
                self._write(f"  [{parts[0]} needs a real console – opened in a new window]", "muted")
            except Exception as exc:
                self._write(f"  Error: {exc}", "danger")
            self._next_queued()
            return
        self._token = object()             # a late result of an older command must not drain the queue
        runner = _CmdRunner(cmd, shell, self._find_git_bash(), self._cwd, self._token)
        runner.signals.finished.connect(self._on_finished)
        runner.setAutoDelete(True)
        self._pool.start(runner)

    # ------------------------------------------------------------------ interactive session

    def _start_session(self, cmd: str) -> None:
        try:
            session = ReplSession(cmd, self._cwd, self)
        except Exception as exc:
            self._write(f"  Error: {exc}", "danger")
            return
        self._session = session
        session.output.connect(self._write_raw)
        session.prompt.connect(self._on_session_prompt)
        session.ended.connect(self._on_session_ended)
        self._write(f"  [{session.name} interactive – lines go to it · Ctrl+D = EOF · Ctrl+C = kill]", "muted")
        self._shell_combo.setEnabled(False)
        self._input.setPlaceholderText(f"{session.name} input…   Ctrl+D end   Ctrl+C kill   Ctrl+Up back to panel")
        self._on_session_prompt("")

    def _on_session_prompt(self, prompt: str) -> None:
        if self._session is None:
            return
        self._prompt_lbl.setText(f"{self._session.name} {prompt}".rstrip() if prompt else self._session.name)
        self._prompt_lbl.setToolTip(f"interactive {self._session.name} – Ctrl+D ends it")

    def _on_session_ended(self, rc: int) -> None:
        if self._session is None:
            return
        name = self._session.name
        self._session.deleteLater()
        self._session = None
        self._write(f"  [{name} exited {rc}]", "muted" if rc == 0 else "warning")
        self._shell_combo.setEnabled(True)
        self._input.setPlaceholderText(
            "command…   ↑↓ history   Tab complete   Ctrl+Enter file name   %N %P %T %S %R %SI %RI   "
            "Alt+± pane height   Ctrl+Up back to panel")
        self._update_prompt()

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

    def _on_finished(self, stdout: str, stderr: str, rc: int, token: object = None) -> None:
        if stdout.strip():
            self._write(stdout.rstrip())
        if stderr.strip():
            self._write(stderr.rstrip(), "danger")
        if rc not in (0, -1):
            self._write(f"  [exit {rc}]", "warning")
        if token is self._token:
            self._next_queued()

    def _next_queued(self) -> None:
        """%SI / %RI: run the next expanded command once the previous one is done."""
        if self._queue:
            self._run_one(self._queue.pop(0))

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
            if self._session is not None and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                if key == Qt.Key.Key_D:
                    self._write("  ^D", "muted")
                    self._session.send_eof()
                    return True
                if key == Qt.Key.Key_C and not self._input.hasSelectedText():
                    self._write("  ^C", "muted")
                    self._session.kill()
                    return True
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and \
                    event.modifiers() & Qt.KeyboardModifier.ControlModifier and self._context is not None:
                ctx = self._context()
                if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                    text = ctx.selected_paths[0] if ctx.selected_paths else ""
                else:
                    text = ctx.cursor_name
                if text:
                    self.insert_text(text)
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
