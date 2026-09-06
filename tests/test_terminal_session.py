"""Embedded terminal: REPL / console classification and a live python session over pipes."""

from __future__ import annotations

import sys
import time

from src.gui.terminal_session import ReplSession, classify


def test_classify():
    assert classify("python") == "repl"
    assert classify("py -i") == "repl"
    assert classify("python -q -u") == "repl"
    assert classify("node") == "repl"
    assert classify("python script.py") == "plain"
    assert classify("python -c \"print(1)\"") == "plain"
    assert classify("python -m pytest") == "plain"
    assert classify("node app.js") == "plain"
    assert classify("cmd") == "console"
    assert classify("powershell") == "console"
    assert classify("vim notes.txt") == "plain"       # only a bare launch is treated as console
    assert classify("ssh") == "console"
    assert classify("cmd /c dir") == "plain"
    assert classify("dir") == "plain"
    assert classify("") == "plain"


def _pump(qapp, seconds: float, until=lambda: False) -> None:
    end = time.time() + seconds
    while time.time() < end and not until():
        qapp.processEvents()
        time.sleep(0.01)


def test_python_repl_session(qapp, tmp_path):
    out: list[tuple[str, str]] = []
    prompts: list[str] = []
    ended: list[int] = []
    s = ReplSession(f'"{sys.executable}"', str(tmp_path))
    s.output.connect(lambda t, k: out.append((t, k)))
    s.prompt.connect(prompts.append)
    s.ended.connect(ended.append)
    _pump(qapp, 10, lambda: ">>>" in prompts)
    assert ">>>" in prompts, (out, prompts)             # prompt came on stderr and was peeled off
    assert not any(">>> " in t for t, _ in out)

    s.send("print(10)")
    _pump(qapp, 10, lambda: any("10" in t for t, _ in out))
    assert any(t.strip() == "10" and k == "" for t, k in out), out

    s.send("for i in range(2):")
    _pump(qapp, 10, lambda: "..." in prompts)
    assert "..." in prompts                             # continuation prompt

    s.send("    print(i)")
    s.send("")
    _pump(qapp, 10, lambda: sum(t.count("\n") for t, k in out if k == "") >= 3)
    s.send("1/0")
    _pump(qapp, 10, lambda: any("ZeroDivisionError" in t for t, _ in out))
    assert any("ZeroDivisionError" in t and k == "warning" for t, k in out)

    s.send_eof()
    _pump(qapp, 10, lambda: bool(ended))
    assert ended == [0]
    assert not s.alive()


def test_session_kill(qapp, tmp_path):
    ended: list[int] = []
    s = ReplSession(f'"{sys.executable}"', str(tmp_path))
    s.ended.connect(ended.append)
    _pump(qapp, 0.5)
    s.kill()
    _pump(qapp, 10, lambda: bool(ended))
    assert ended and ended[0] != 0
