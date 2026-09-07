"""External editor launcher (F4).

The configured command is a plain command line, e.g. ``code``,
``"C:\\Program Files\\Notepad++\\notepad++.exe" -multiInst`` or
``subl {file}``. ``{file}`` marks where the file paths go; without it they
are appended. A bare program name is resolved through PATH, and the VS Code
launcher (``code`` → ``code.cmd``) is replaced by ``Code.exe`` so no console
window flashes. An empty command means "use the built-in editor".
"""

from __future__ import annotations

import os
import shutil
import subprocess

DEFAULT_EDITOR = "code -n"      # -n = new VS Code window
PLACEHOLDER = "{file}"

_VSCODE_EXE = (
    r"%LOCALAPPDATA%\Programs\Microsoft VS Code\Code.exe",
    r"%ProgramFiles%\Microsoft VS Code\Code.exe",
    r"%ProgramFiles(x86)%\Microsoft VS Code\Code.exe",
)


def split_command(cmd: str) -> list[str]:
    """Windows-style tokenizer: whitespace separates, double quotes group
    (kept out of the token), backslashes are literal."""
    out: list[str] = []
    cur: list[str] = []
    quoted = False
    has_tok = False
    for ch in cmd:
        if ch == '"':
            quoted = not quoted
            has_tok = True
        elif ch.isspace() and not quoted:
            if has_tok:
                out.append("".join(cur))
                cur.clear()
                has_tok = False
        else:
            cur.append(ch)
            has_tok = True
    if has_tok:
        out.append("".join(cur))
    return out


def build_args(cmd: str, files: list[str]) -> list[str]:
    """Expand the command line for ``files``: every token holding {file} is
    repeated once per file, otherwise the files are appended."""
    tokens = split_command(cmd)
    if not tokens:
        raise ValueError("no editor configured")
    out: list[str] = []
    placed = False
    for tok in tokens:
        if PLACEHOLDER in tok:
            out.extend(tok.replace(PLACEHOLDER, f) for f in files)
            placed = True
        else:
            out.append(tok)
    if not placed:
        out.extend(files)
    return out


def resolve_program(name: str) -> str | None:
    """Full path of the program token, or None if it cannot be found."""
    expanded = os.path.expandvars(os.path.expanduser(name))
    if os.path.isabs(expanded) or os.sep in expanded or "/" in expanded:
        return expanded if os.path.exists(expanded) else None
    stem = os.path.splitext(os.path.basename(expanded))[0].lower()
    if stem == "code":
        for cand in _VSCODE_EXE:
            p = os.path.expandvars(cand)
            if os.path.isfile(p):
                return p
    found = shutil.which(expanded)
    if found and found.lower().endswith((".cmd", ".bat")):
        # a launcher script next to the real binary? (VS Code style bin\code.cmd)
        exe = os.path.join(os.path.dirname(os.path.dirname(found)), stem.capitalize() + ".exe")
        if os.path.isfile(exe):
            return exe
    return found


def launch(cmd: str, files: list[str], cwd: str | None = None) -> None:
    """Start the editor detached. Raises FileNotFoundError when the program
    cannot be resolved (caller falls back to the built-in editor)."""
    args = build_args(cmd, files)
    prog = resolve_program(args[0])
    if prog is None:
        raise FileNotFoundError(f"editor not found: {args[0]}")
    args[0] = prog
    flags = 0
    if os.name == "nt":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        if prog.lower().endswith((".cmd", ".bat")):
            flags |= subprocess.CREATE_NO_WINDOW
    subprocess.Popen(args, cwd=cwd or None, creationflags=flags, close_fds=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
