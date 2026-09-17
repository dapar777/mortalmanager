"""Quick filter (Ctrl+S / '*'): substring by default, ``* ? [`` make it a mask.

The mask is matched while it is being typed, so ``a*.txt`` must keep showing
``alpha.txt`` at every keystroke instead of blinking empty at ``a*.``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.core.file_model import FileEntry
from src.gui.panel import PanelWidget


class _Tab:
    def __init__(self, text: str) -> None:
        self.filter_text = text


class _Stub:
    """Only what _filter_entries touches."""

    _filter_entries = PanelWidget._filter_entries

    def __init__(self, text: str) -> None:
        self._tab = _Tab(text)

    @property
    def _current_tab(self) -> _Tab:
        return self._tab


def _entries(*names: str) -> list[FileEntry]:
    from datetime import datetime

    now = datetime.now()
    out = [FileEntry(name="..", path=Path("C:/"), size=-1, modified=now, created=now,
                     is_dir=True, is_symlink=False, attributes=0x10, is_parent=True)]
    for n in names:
        out.append(FileEntry(name=n, path=Path("C:/x") / n, size=1, modified=now, created=now,
                             is_dir="." not in n, is_symlink=False, attributes=0x20))
    return out


ALL = ("subdir", "alpha.txt", "beta.txt", "gamma.py", "notes.md", "test_alpha.py")


def _filtered(text: str) -> list[str]:
    got = _Stub(text)._filter_entries(_entries(*ALL))
    assert got[0].is_parent, "'..' always stays"
    return [e.name for e in got[1:]]


@pytest.mark.parametrize("text,expected", [
    ("", list(ALL)),                                     # no filter
    ("alpha", ["alpha.txt", "test_alpha.py"]),           # plain text = substring
    ("ALPHA", ["alpha.txt", "test_alpha.py"]),           # case-insensitive
    ("*.py", ["gamma.py", "test_alpha.py"]),             # mask
    ("*.PY", ["gamma.py", "test_alpha.py"]),
    ("a*", ["alpha.txt"]),                               # mask is anchored at the start
    ("a*.txt", ["alpha.txt"]),
    ("*alpha*", ["alpha.txt", "test_alpha.py"]),
    ("?amma*", ["gamma.py"]),                            # '?' = one character
    ("[bn]*", ["beta.txt", "notes.md"]),                 # character class
    ("sub*", ["subdir"]),                                # directories too
    ("zzz", []),
])
def test_filter_matching(text: str, expected: list[str]):
    assert _filtered(text) == expected


@pytest.mark.parametrize("target,keeps", [
    ("a*.txt", "alpha.txt"),
    ("*.py", "gamma.py"),
    ("*alpha*", "test_alpha.py"),
])
def test_partial_mask_never_blinks_empty(target: str, keeps: str):
    """Every prefix of the mask still matches the file the finished mask matches."""
    for i in range(1, len(target) + 1):
        assert keeps in _filtered(target[:i]), f"{target[:i]!r} lost {keeps}"


def test_trailing_star_not_doubled():
    assert _filtered("*") == list(ALL)
    assert _filtered("**") == list(ALL)
