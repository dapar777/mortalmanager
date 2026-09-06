"""Unit tests for core domain models."""

from __future__ import annotations

import pytest
from datetime import datetime
from pathlib import Path

from src.core.file_model import FileEntry, DriveInfo, format_size, SortField, SortOrder
from src.core.selection import SelectionManager
from src.core.history import NavigationHistory
from src.core.undo import UndoAction, UndoActionType, UndoManager


# ------------------------------------------------------------------ format_size

def test_format_size_bytes():
    assert format_size(512) == "512 B"

def test_format_size_kb():
    assert format_size(1024) == "1.0 KB"

def test_format_size_mb():
    assert format_size(1024 * 1024) == "1.0 MB"

def test_format_size_gb():
    assert format_size(1024 ** 3) == "1.0 GB"

def test_format_size_zero():
    assert format_size(0) == "0 B"


# ------------------------------------------------------------------ FileEntry

def _make_entry(name: str, is_dir: bool = False, size: int = 1024) -> FileEntry:
    return FileEntry(
        name=name,
        path=Path(f"C:/test/{name}"),
        size=-1 if is_dir else size,
        modified=datetime(2025, 1, 1, 12, 0),
        created=datetime(2024, 1, 1),
        is_dir=is_dir,
        is_symlink=False,
        attributes=0x10 if is_dir else 0x20,
    )


def test_file_entry_extension():
    e = _make_entry("report.pdf")
    assert e.extension == "pdf"


def test_dir_entry_size_display():
    e = _make_entry("Documents", is_dir=True)
    assert e.size_display == "<DIR>"


def test_file_entry_size_display():
    e = _make_entry("file.txt", size=2048)
    assert e.size_display == "2.0 KB"


def test_file_entry_modified_display():
    e = _make_entry("x.txt")
    assert e.modified_display == "2025-01-01 12:00"


# ------------------------------------------------------------------ SelectionManager

def test_selection_toggle():
    sel = SelectionManager()
    e = _make_entry("a.txt")
    sel.select(e)
    assert sel.is_selected(e)
    sel.toggle(e)
    assert not sel.is_selected(e)


def test_selection_select_all():
    sel = SelectionManager()
    entries = [_make_entry(f"f{i}.txt") for i in range(5)]
    sel.select_all(entries)
    assert sel.count == 5


def test_selection_invert():
    sel = SelectionManager()
    entries = [_make_entry(f"f{i}.txt") for i in range(4)]
    sel.select(entries[0])
    sel.select(entries[1])
    sel.invert(entries)
    assert not sel.is_selected(entries[0])
    assert not sel.is_selected(entries[1])
    assert sel.is_selected(entries[2])
    assert sel.is_selected(entries[3])


def test_selection_by_mask_glob():
    sel = SelectionManager()
    entries = [
        _make_entry("file.txt"),
        _make_entry("image.jpg"),
        _make_entry("notes.txt"),
    ]
    sel.select_by_mask(entries, "*.txt")
    assert sel.is_selected(entries[0])
    assert not sel.is_selected(entries[1])
    assert sel.is_selected(entries[2])


def test_selection_by_mask_regex():
    sel = SelectionManager()
    entries = [_make_entry("IMG_001.jpg"), _make_entry("doc.pdf")]
    sel.select_by_mask(entries, r"IMG_\d+\.jpg", use_regex=True)
    assert sel.is_selected(entries[0])
    assert not sel.is_selected(entries[1])


def test_selection_parent_not_selectable():
    sel = SelectionManager()
    parent = _make_entry("..", is_dir=True)
    parent = FileEntry(
        name="..",
        path=Path("C:/"),
        size=-1,
        modified=datetime.now(),
        created=datetime.now(),
        is_dir=True,
        is_symlink=False,
        attributes=0x10,
        is_parent=True,
    )
    sel.select(parent)
    assert sel.count == 0


# ------------------------------------------------------------------ NavigationHistory

def test_history_back_forward():
    h = NavigationHistory()
    h.push("/a")
    h.push("/b")
    h.push("/c")
    assert h.current.path == "/c"
    assert h.can_go_back

    entry = h.go_back()
    assert entry.path == "/b"

    entry = h.go_forward()
    assert entry.path == "/c"


def test_history_new_push_clears_forward():
    h = NavigationHistory()
    h.push("/a")
    h.push("/b")
    h.go_back()
    assert h.can_go_forward

    h.push("/c")
    assert not h.can_go_forward


def test_history_no_back_at_start():
    h = NavigationHistory()
    h.push("/a")
    assert not h.can_go_back


# ------------------------------------------------------------------ UndoManager

def test_undo_push_and_pop():
    um = UndoManager()
    action = UndoAction(
        action_type=UndoActionType.MOVE,
        description="Move test",
        pairs=[("/src/a.txt", "/dst/a.txt")],
    )
    um.push(action)
    assert um.can_undo
    popped = um.pop_undo()
    assert popped is action
    assert not um.can_undo
    assert um.can_redo


def test_undo_redo_cycle():
    um = UndoManager()
    a1 = UndoAction(UndoActionType.RENAME, "Rename 1")
    a2 = UndoAction(UndoActionType.RENAME, "Rename 2")
    um.push(a1)
    um.push(a2)
    um.pop_undo()
    um.pop_undo()
    assert not um.can_undo
    assert um.can_redo
    um.pop_redo()
    assert um.can_undo
