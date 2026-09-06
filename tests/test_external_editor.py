"""External editor command line: tokenizer, {file} placeholder, program resolution, launch fallback."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from src.editor import external


def test_split_command_quotes_and_backslashes():
    assert external.split_command('code') == ["code"]
    assert external.split_command('"C:\\Program Files\\Editor\\ed.exe" -n {file}') == [
        "C:\\Program Files\\Editor\\ed.exe", "-n", "{file}"]
    assert external.split_command('  a   "b c"  d ') == ["a", "b c", "d"]
    assert external.split_command("") == []


def test_build_args_placeholder_and_append():
    assert external.build_args("code", ["a.txt", "b.txt"]) == ["code", "a.txt", "b.txt"]
    assert external.build_args("ed --open={file} -x", ["a", "b"]) == ["ed", "--open=a", "--open=b", "-x"]
    with pytest.raises(ValueError):
        external.build_args("   ", ["a"])


def test_resolve_program(tmp_path: Path, monkeypatch):
    exe = tmp_path / "myed.exe"
    exe.write_bytes(b"")
    assert external.resolve_program(str(exe)) == str(exe)
    assert external.resolve_program(str(tmp_path / "missing.exe")) is None
    assert external.resolve_program("definitely-not-an-editor-xyz") is None
    # python itself is on PATH in the test env
    assert external.resolve_program(os.path.basename(sys.executable)) is not None


def test_launch_missing_program_raises():
    with pytest.raises(FileNotFoundError):
        external.launch("definitely-not-an-editor-xyz", ["a.txt"])


def test_launch_runs_program(tmp_path: Path):
    marker = tmp_path / "ran.txt"
    script = tmp_path / "ed.py"
    script.write_text("import sys, pathlib; pathlib.Path(sys.argv[1]).write_text('|'.join(sys.argv[2:]))")
    external.launch(f'"{sys.executable}" "{script}" "{marker}" {{file}}', ["x.txt", "y z.txt"], str(tmp_path))
    import time
    for _ in range(100):
        if marker.exists() and marker.read_text():
            break
        time.sleep(0.05)
    assert marker.read_text() == "x.txt|y z.txt"
