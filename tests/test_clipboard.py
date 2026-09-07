"""Copy naming (" - Kopie") and the file clipboard round trip."""

from __future__ import annotations

import os
from pathlib import Path

from src.core.naming import copy_target, same_folder


def test_copy_target_names(tmp_path: Path):
    f = tmp_path / "report.txt"
    f.write_text("x")
    d = tmp_path / "photos"
    d.mkdir()
    assert copy_target(str(f), str(tmp_path)) == str(tmp_path / "report - Kopie.txt")
    (tmp_path / "report - Kopie.txt").write_text("x")
    assert copy_target(str(f), str(tmp_path)) == str(tmp_path / "report - Kopie (2).txt")
    assert copy_target(str(d), str(tmp_path)) == str(tmp_path / "photos - Kopie")
    assert copy_target(str(f), str(tmp_path), suffix=" - Copy") == str(tmp_path / "report - Copy.txt")
    dot = tmp_path / ".gitignore"
    dot.write_text("x")
    assert copy_target(str(dot), str(tmp_path)) == str(tmp_path / ".gitignore - Kopie")


def test_same_folder(tmp_path: Path):
    assert same_folder(str(tmp_path / "a.txt"), str(tmp_path))
    assert same_folder(str(tmp_path / "a.txt"), str(tmp_path) + os.sep)
    assert not same_folder(str(tmp_path / "sub" / "a.txt"), str(tmp_path))


def test_clipboard_round_trip(qapp, tmp_path: Path):
    from src.gui import file_clipboard
    a = tmp_path / "a.txt"
    b = tmp_path / "b c.txt"
    a.write_text("x")
    b.write_text("x")
    file_clipboard.set_files([str(a), str(b)], cut=False)
    paths, cut = file_clipboard.get_files()
    assert [os.path.normcase(p) for p in paths] == [os.path.normcase(str(a)), os.path.normcase(str(b))]
    assert cut is False
    file_clipboard.set_files([str(a)], cut=True)
    paths, cut = file_clipboard.get_files()
    assert cut is True and len(paths) == 1
    file_clipboard.clear()
    assert file_clipboard.get_files() == ([], False)
