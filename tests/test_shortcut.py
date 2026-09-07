"""Windows .lnk resolution (folder shortcut -> navigate, file shortcut -> run)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from src.filesystem.shortcut import shortcut_folder, shortcut_target

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows shortcuts")


def _make_lnk(lnk: Path, target: Path) -> None:
    ps = (f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{lnk}');"
          f"$s.TargetPath='{target}';$s.Save()")
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True, capture_output=True)


def test_folder_and_file_shortcuts(tmp_path: Path):
    folder = tmp_path / "target dir"
    folder.mkdir()
    file = tmp_path / "prog.txt"
    file.write_text("x")
    _make_lnk(tmp_path / "dir.lnk", folder)
    _make_lnk(tmp_path / "file.lnk", file)
    assert os.path.normcase(shortcut_target(str(tmp_path / "dir.lnk"))) == os.path.normcase(str(folder))
    assert os.path.normcase(shortcut_folder(str(tmp_path / "dir.lnk"))) == os.path.normcase(str(folder))
    assert shortcut_folder(str(tmp_path / "file.lnk")) is None      # a file: not entered, run instead
    assert shortcut_target(str(tmp_path / "missing.lnk")) is None


def test_unc_shortcut_target(tmp_path: Path):
    # localhost admin share: a real network-style (UNC) target
    unc = "\\\\localhost\\C$\\Windows"
    _make_lnk(tmp_path / "unc.lnk", Path(unc))
    target = shortcut_target(str(tmp_path / "unc.lnk"))
    assert target and target.lower().startswith("\\\\localhost")
    folder = shortcut_folder(str(tmp_path / "unc.lnk"))
    assert folder is None or folder.lower().startswith("\\\\localhost")   # None only if the share is not reachable
