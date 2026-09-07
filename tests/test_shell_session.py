"""Persistent terminal shell: variables survive, cwd follows cd, exit codes, stderr, Czech text."""

from __future__ import annotations

import os
import time

import pytest

from src.gui.shell_session import ShellSession


def _pump(qapp, seconds: float, until=lambda: False) -> None:
    end = time.time() + seconds
    while time.time() < end and not until():
        qapp.processEvents()
        time.sleep(0.01)


class _Rec:
    def __init__(self, s: ShellSession) -> None:
        self.out: list[tuple[str, str]] = []
        self.done: list[tuple[int, str, object]] = []
        s.output.connect(lambda t, k: self.out.append((t, k)))
        s.finished.connect(lambda rc, cwd, tok: self.done.append((rc, cwd, tok)))

    def text(self, kind: str = "") -> str:
        return "".join(t for t, k in self.out if k == kind)


def _run(qapp, s: ShellSession, rec: _Rec, cmd: str, cwd: str, timeout: float = 15) -> tuple[int, str]:
    n = len(rec.done)
    tok = object()
    s.run(cmd, cwd, tok)
    _pump(qapp, timeout, lambda: len(rec.done) > n)
    assert len(rec.done) > n, f"no sentinel for {cmd!r}: {rec.out}"
    rc, new_cwd, got_tok = rec.done[-1]
    assert got_tok is tok
    return rc, new_cwd


@pytest.mark.skipif(os.name != "nt", reason="cmd.exe")
def test_cmd_session_keeps_environment(qapp, tmp_path):
    s = ShellSession("CMD", str(tmp_path))
    rec = _Rec(s)
    _run(qapp, s, rec, "set UC_TEST_VAR=hello ěšč", str(tmp_path))
    rec.out.clear()
    rc, cwd = _run(qapp, s, rec, "echo [%UC_TEST_VAR%]", str(tmp_path))
    assert rc == 0 and "[hello ěšč]" in rec.text()
    assert "__UCP__" not in rec.text() and "__UC_DONE__" not in rec.text()
    # cwd follows a cd typed into the shell
    sub = tmp_path / "sub"
    sub.mkdir()
    rc, cwd = _run(qapp, s, rec, "cd sub", str(tmp_path))
    assert os.path.normcase(cwd) == os.path.normcase(str(sub))
    # the widget's cwd wins on the next command (panel navigation)
    rec.out.clear()
    rc, cwd = _run(qapp, s, rec, "cd", str(tmp_path))
    assert os.path.normcase(rec.text().strip()) == os.path.normcase(str(tmp_path))
    # exit code and stderr of a missing program
    rc, _ = _run(qapp, s, rec, "definitely_missing_program_xyz", str(tmp_path))
    assert rc != 0 and "definitely_missing_program_xyz" in rec.text("danger")
    s.kill()
    _pump(qapp, 5, lambda: not s.alive())
    assert not s.alive()


@pytest.mark.skipif(os.name != "nt", reason="powershell.exe")
def test_powershell_session(qapp, tmp_path):
    s = ShellSession("PowerShell", str(tmp_path))
    rec = _Rec(s)
    _run(qapp, s, rec, "$env:UC_PS_VAR = 'psěšč'", str(tmp_path), timeout=30)
    rec.out.clear()
    rc, cwd = _run(qapp, s, rec, 'Write-Output "[$env:UC_PS_VAR]"', str(tmp_path), timeout=30)
    assert rc == 0 and "[psěšč]" in rec.text()
    rc, _ = _run(qapp, s, rec, "cmd /c exit 7", str(tmp_path), timeout=30)
    assert rc == 7
    s.kill()
    _pump(qapp, 5, lambda: not s.alive())
