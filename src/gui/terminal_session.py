"""Interactive programs in the embedded terminal.

The embedded terminal runs one command at a time with captured output, so a
bare ``python`` used to start a REPL that got EOF on stdin and exited without
a word – and the next line (``print(10)``) went to cmd.exe instead.

Two remedies, both decided by :func:`classify`:

* **REPL session** (``python``, ``py``, ``node``…): the program is started
  with pipes and kept alive; every following input line is written to its
  stdin and its output streams into the pane until it exits (``exit()``,
  Ctrl+D = EOF, Ctrl+C = kill). Prompts (``>>> ``, ``... ``, ``> ``) are
  peeled off the output and shown in the input row instead.
* **console program** (``cmd``, ``powershell``, ``vim``, ``ssh``…): needs a
  real console, so it is opened in a new console window in the current
  directory.
"""

from __future__ import annotations

import codecs
import os
import subprocess
import threading

from PySide6.QtCore import QObject, Signal

# program stem → (args that force an interactive prompt on a pipe, prompts to peel off)
REPL_PROGRAMS: dict[str, tuple[list[str], tuple[str, ...]]] = {
    "python": (["-i", "-q"], (">>> ", "... ")),
    "python3": (["-i", "-q"], (">>> ", "... ")),
    "py": (["-i", "-q"], (">>> ", "... ")),
    "ipython": (["--no-banner", "--simple-prompt"], ("In [", "   ...: ")),
    "node": (["-i"], ("> ", "... ")),
}
# python flags that keep the REPL a REPL (anything else, e.g. -c / -m / a script, runs normally)
_REPL_FLAGS = {"-i", "-q", "-u", "-B", "-E", "-s", "-S", "-I", "-O", "-OO", "-b", "-d", "-v", "-X"}

CONSOLE_PROGRAMS = frozenset({
    "cmd", "powershell", "pwsh", "bash", "sh", "wsl", "zsh", "fish",
    "vim", "vi", "nvim", "nano", "emacs", "less", "more", "top", "htop", "btop",
    "ssh", "telnet", "ftp", "sftp", "nc", "irb", "ghci", "julia", "r", "lua", "perl",
    "gdb", "lldb", "pdb", "diskpart", "nslookup", "sqlite3", "psql", "mysql", "redis-cli",
})


def _tokens(cmd: str) -> list[str]:
    from src.editor.external import split_command
    return split_command(cmd)


def _stem(token: str) -> str:
    return os.path.splitext(os.path.basename(token))[0].lower()


def classify(cmd: str) -> str:
    """'repl' | 'console' | 'plain' for a command line typed in the terminal."""
    toks = _tokens(cmd)
    if not toks:
        return "plain"
    stem = _stem(toks[0])
    args = toks[1:]
    if stem in REPL_PROGRAMS:
        if stem.startswith("py"):
            # python -X dev, python -i script.py … keep it simple: only bare flags
            return "repl" if all(a in _REPL_FLAGS for a in args) else "plain"
        if stem == "node":
            return "repl" if not args or args == ["-i"] else "plain"
        return "repl" if not args else "plain"
    if stem in CONSOLE_PROGRAMS and not args:
        return "console"
    if stem in ("cmd", "powershell", "pwsh") and args and args[0].lower() not in ("/c", "-c", "-command", "-file", "/k"):
        return "console"
    return "plain"


_DIRECT_SHELLS = {"cmd", "powershell", "pwsh", "wsl", "bash"}


def open_in_console(cmd: str, cwd: str) -> None:
    """Run ``cmd`` in a fresh console window in ``cwd`` (closes when the program
    ends). Shells start directly – ``cmd`` typed in the terminal is a plain
    cmd.exe window, not ``cmd /c cmd`` – other programs go through the shell so
    PATH / .bat / .cmd resolution works as on a command line."""
    flags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
    toks = _tokens(cmd)
    if toks and _stem(toks[0]) in _DIRECT_SHELLS:
        subprocess.Popen(toks, cwd=cwd, creationflags=flags)
    else:
        subprocess.Popen(cmd, shell=True, cwd=cwd, creationflags=flags)


class ReplSession(QObject):
    """A long-running interactive child process wired to the terminal pane."""

    output = Signal(str, str)     # text, kind ("" stdout / "warning" stderr) – raw chunks, no newline added
    prompt = Signal(str)          # prompt text peeled off the output (">>>", "...", ">")
    ended = Signal(int)           # return code

    def __init__(self, cmd: str, cwd: str, parent: QObject | None = None) -> None:
        super().__init__(parent)
        toks = _tokens(cmd)
        self.name = _stem(toks[0])
        extra, self._prompts = REPL_PROGRAMS[self.name]
        args = [toks[0], *extra, *[a for a in toks[1:] if a not in extra]]
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1", NODE_NO_READLINE="1",
                   TERM="dumb")
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self._proc = subprocess.Popen(
            args, cwd=cwd, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            creationflags=flags, bufsize=0,
        )
        self._lock = threading.Lock()
        self._held: dict[str, str] = {}   # per stream: suffix that may be the start of a prompt
        self._open_readers = 2
        for stream, kind in ((self._proc.stdout, ""), (self._proc.stderr, "warning")):
            threading.Thread(target=self._pump, args=(stream, kind), daemon=True).start()

    # ------------------------------------------------------------------ child → GUI

    def _pump(self, stream, kind: str) -> None:
        dec = codecs.getincrementaldecoder("utf-8")(errors="replace")
        fd = stream.fileno()
        while True:
            try:
                data = os.read(fd, 4096)
            except OSError:
                break
            if not data:
                break
            self._emit(dec.decode(data), kind)
        self._emit(dec.decode(b"", final=True), kind, final=True)
        with self._lock:
            self._open_readers -= 1
            last = self._open_readers == 0
        if last:
            rc = self._proc.wait()
            self.ended.emit(rc)

    def _emit(self, text: str, kind: str, final: bool = False) -> None:
        """Strip a trailing prompt (shown in the input row) and forward the rest.
        A prompt may arrive split over chunks (">" then ">> "), so a suffix that
        is the start of a prompt is held back until the next chunk."""
        with self._lock:
            text = self._held.pop(kind, "") + text
            if not text:
                return
            for p in self._prompts:
                if text.endswith(p):
                    body = text[: -len(p)]
                    # several lines sent at once (%SI) → ">>> >>> ": drop the stacked prompts too
                    while any(body.endswith(q) for q in self._prompts):
                        body = next(body[: -len(q)] for q in self._prompts if body.endswith(q))
                    if body:
                        self.output.emit(body, kind)
                    self.prompt.emit(p.strip() or p)
                    return
            if not final:
                longest = max((n for n in range(1, min(len(text), max(map(len, self._prompts))) + 1)
                               if any(p.startswith(text[-n:]) for p in self._prompts)), default=0)
                if longest:
                    self._held[kind] = text[-longest:]
                    text = text[:-longest]
            if text:
                self.output.emit(text, kind)   # the prompt stays until the widget sends the next line

    # ------------------------------------------------------------------ GUI → child

    def send(self, line: str) -> None:
        if self._proc.stdin is None or self._proc.poll() is not None:
            return
        try:
            self._proc.stdin.write((line + "\n").encode("utf-8"))
            self._proc.stdin.flush()
        except OSError:
            pass

    def send_eof(self) -> None:
        try:
            if self._proc.stdin:
                self._proc.stdin.close()
        except OSError:
            pass

    def kill(self) -> None:
        if self._proc.poll() is None:
            self._proc.kill()

    def alive(self) -> bool:
        return self._proc.poll() is None
