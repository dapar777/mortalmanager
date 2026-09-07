"""Persistent shell behind the embedded terminal.

One cmd.exe / powershell.exe / bash.exe process lives for the whole session,
so ``set X=1``, ``$env:X``, ``export``, ``doskey``, ``pushd``… survive from
one command to the next. Every command is followed by a sentinel line
(``__UC_DONE__ <rc> <cwd>``) that tells us when it finished, its exit code
and the directory the shell is in afterwards; output streams into the pane
as it arrives. Killing the process (toolbar button, Ctrl+C on a stuck
command) and starting a clean one is the widget's job.

Per shell:
* CMD – ``cmd /Q`` with ``PROMPT=__UCP__`` (stripped from the output together
  with the blank line cmd prints before it); both pipes use the OEM code page –
  that is how cmd reads a pipe, and ``chcp 65001`` only breaks the input side.
* PowerShell – ``powershell -Command -`` only runs after EOF, so a tiny driver
  loop reads stdin line by line and ``Invoke-Expression``s each one.
* Git Bash – ``bash --login`` executes stdin line by line by itself.
"""

from __future__ import annotations

import codecs
import os
import subprocess
import threading

from PySide6.QtCore import QObject, Signal

SENTINEL = "__UC_DONE__"
CMD_PROMPT = "__UCP__"

_PS_DRIVER = (
    "[Console]::OutputEncoding=[Text.Encoding]::UTF8; [Console]::InputEncoding=[Text.Encoding]::UTF8; "
    "$OutputEncoding=[Text.Encoding]::UTF8; "
    "while($true){ $l=[Console]::In.ReadLine(); if($null -eq $l){break}; "
    "try { Invoke-Expression $l } catch { Write-Error $_ } }"
)


class ShellSession(QObject):
    output = Signal(str, str)            # raw text chunk, kind ("" stdout / "danger" stderr)
    finished = Signal(int, str, object)  # rc, cwd after the command, token given to run()
    died = Signal(int)                   # the shell process ended (rc)

    def __init__(self, shell: str, cwd: str, bash_path: str | None = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.shell = shell
        self.cwd = cwd
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        env = dict(os.environ)
        if shell == "PowerShell":
            args = ["powershell.exe", "-NoProfile", "-NoLogo", "-Command", _PS_DRIVER]
            self._enc = "utf-8"
        elif shell == "Git Bash" and bash_path:
            args = [bash_path, "--login"]
            self._enc = "utf-8"
        else:
            self.shell = "CMD"
            env["PROMPT"] = CMD_PROMPT
            args = ["cmd.exe", "/Q"]
            self._enc = "oem"
        self._proc = subprocess.Popen(
            args, cwd=cwd, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            creationflags=flags, bufsize=0,
        )
        self._lock = threading.Lock()
        self._token: object = None
        self._busy = False
        self._line_buf = ""            # stdout: partial line that may still turn into the sentinel / prompt
        self._pending_blank = False    # CMD: blank line held back – it belongs to the prompt if that follows
        self._readers = 2
        threading.Thread(target=self._pump, args=(self._proc.stdout, True), daemon=True).start()
        threading.Thread(target=self._pump, args=(self._proc.stderr, False), daemon=True).start()

    # ------------------------------------------------------------------ commands

    def alive(self) -> bool:
        return self._proc.poll() is None

    def busy(self) -> bool:
        return self._busy

    def run(self, cmd: str, cwd: str, token: object) -> None:
        """Run one command line; ``cwd`` is where the widget thinks we are (the
        shell is moved there first when it drifted, e.g. after panel navigation)."""
        self._token = token
        self._busy = True
        lines: list[str] = []
        if os.path.normcase(cwd) != os.path.normcase(self.cwd):
            lines.append(self._cd_line(cwd))
            self.cwd = cwd
        lines.append(cmd)
        lines.append(self._sentinel_line())
        self._send("\n".join(lines) + "\n")

    def _cd_line(self, cwd: str) -> str:
        if self.shell == "PowerShell":
            return f'Set-Location -LiteralPath "{cwd}"'
        if self.shell == "Git Bash":
            return f'cd "{cwd}"'
        return f'cd /d "{cwd}"'

    def _sentinel_line(self) -> str:
        if self.shell == "PowerShell":
            return f'Write-Output "{SENTINEL} $LASTEXITCODE $($PWD.Path)"'
        if self.shell == "Git Bash":
            return f'echo "{SENTINEL} $? $(pwd -W)"'
        return f"echo {SENTINEL} %ERRORLEVEL% %CD%"

    def _send(self, text: str) -> None:
        try:
            if self._proc.stdin is not None:
                self._proc.stdin.write(text.encode(self._enc, errors="replace"))
                self._proc.stdin.flush()
        except OSError:
            pass

    def kill(self) -> None:
        if self._proc.poll() is None:
            self._proc.kill()

    # ------------------------------------------------------------------ readers (worker threads)

    def _pump(self, stream, is_stdout: bool) -> None:
        dec = codecs.getincrementaldecoder(self._enc)(errors="replace")
        fd = stream.fileno()
        while True:
            try:
                data = os.read(fd, 4096)
            except OSError:
                break
            if not data:
                break
            text = dec.decode(data)
            try:
                if is_stdout:
                    self._feed(text)
                elif text:
                    self.output.emit(text, "danger")
            except RuntimeError:
                break                      # QObject deleted underneath us
        if is_stdout:
            self._feed(dec.decode(b"", final=True), final=True)
        with self._lock:
            self._readers -= 1
            last = self._readers == 0
        if last:
            rc = self._proc.wait()
            self._busy = False
            try:
                self.died.emit(rc)
            except RuntimeError:
                pass                       # the widget already dropped the QObject (restart / shutdown)

    def _feed(self, text: str, final: bool = False) -> None:
        """Split stdout into lines, peel the sentinel and cmd's prompt off, forward the rest."""
        buf = self._line_buf + text
        out: list[str] = []
        while "\n" in buf:
            line, buf = buf.split("\n", 1)
            self._handle_line(line.rstrip("\r"), out, True)
        if final:
            if buf:
                self._handle_line(buf.rstrip("\r"), out, False)
            buf = ""
        elif buf:
            probe = buf.lstrip("\r")
            if self.shell == "CMD" and probe.endswith(CMD_PROMPT):
                buf = ""                                   # complete prompt: swallow (and the blank before it)
                self._pending_blank = False
            elif self.shell == "CMD" and CMD_PROMPT.startswith(probe):
                pass                                       # may become the prompt – wait
            elif SENTINEL.startswith(probe) or probe.startswith(SENTINEL):
                pass                                       # may become the sentinel – wait
            else:
                self._flush_blank(out)
                out.append(buf)                            # progress output without newline: show now
                buf = ""
        self._line_buf = buf
        joined = "".join(out)
        if joined:
            self.output.emit(joined, "")

    def _flush_blank(self, out: list[str]) -> None:
        if self._pending_blank:
            out.append("\n")
            self._pending_blank = False

    def _handle_line(self, line: str, out: list[str], had_newline: bool) -> None:
        if self.shell == "CMD" and CMD_PROMPT in line:
            line = line.replace(CMD_PROMPT, "")
            self._pending_blank = False                    # the prompt's own blank line goes with it
            if not line:
                return
        if line.startswith(SENTINEL):
            parts = line[len(SENTINEL):].strip().split(" ", 1)
            try:
                rc = int(parts[0]) if parts and parts[0] else 0
            except ValueError:
                rc = 0
            cwd = parts[1].strip() if len(parts) > 1 else ""
            if cwd:
                self.cwd = cwd
            self._pending_blank = False
            if out:
                self.output.emit("".join(out), "")
                out.clear()
            self._busy = False
            self.finished.emit(rc, self.cwd, self._token)
            return
        if self.shell == "CMD" and not line and had_newline:
            self._flush_blank(out)                         # two blanks in a row: the first was real
            self._pending_blank = True
            return
        self._flush_blank(out)
        out.append(line + ("\n" if had_newline else ""))
