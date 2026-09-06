"""Unit tests for filesystem layer."""

from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path

import pytest

from src.filesystem.local_fs import LocalFileSystemProvider


@pytest.fixture
def tmp_dir(tmp_path: Path) -> Path:
    # Create a small test directory tree
    (tmp_path / "subdir").mkdir()
    (tmp_path / "file_a.txt").write_text("hello world")
    (tmp_path / "file_b.py").write_text("print('hi')")
    (tmp_path / "subdir" / "nested.log").write_text("log data")
    return tmp_path


@pytest.fixture
def fs() -> LocalFileSystemProvider:
    return LocalFileSystemProvider()


# ------------------------------------------------------------------ list_directory

@pytest.mark.asyncio
async def test_list_directory_contains_files(fs, tmp_dir):
    entries = await fs.list_directory(str(tmp_dir))
    names = {e.name for e in entries}
    assert "file_a.txt" in names
    assert "file_b.py" in names
    assert "subdir" in names


@pytest.mark.asyncio
async def test_list_directory_parent_entry(fs, tmp_dir):
    entries = await fs.list_directory(str(tmp_dir))
    parent_entries = [e for e in entries if e.is_parent]
    assert len(parent_entries) == 1
    assert parent_entries[0].name == ".."


@pytest.mark.asyncio
async def test_list_directory_not_exists(fs):
    with pytest.raises(FileNotFoundError):
        await fs.list_directory("Z:/does_not_exist_xyzzy")


# ------------------------------------------------------------------ get_entry

@pytest.mark.asyncio
async def test_get_entry_file(fs, tmp_dir):
    path = tmp_dir / "file_a.txt"
    entry = await fs.get_entry(str(path))
    assert entry.name == "file_a.txt"
    assert not entry.is_dir
    assert entry.size > 0


@pytest.mark.asyncio
async def test_get_entry_dir(fs, tmp_dir):
    entry = await fs.get_entry(str(tmp_dir / "subdir"))
    assert entry.is_dir


# ------------------------------------------------------------------ mkdir

@pytest.mark.asyncio
async def test_mkdir(fs, tmp_dir):
    new_dir = tmp_dir / "new_folder"
    await fs.mkdir(str(new_dir))
    assert new_dir.is_dir()


# ------------------------------------------------------------------ create_file

@pytest.mark.asyncio
async def test_create_file(fs, tmp_dir):
    new_file = tmp_dir / "created.txt"
    await fs.create_file(str(new_file))
    assert new_file.is_file()


# ------------------------------------------------------------------ copy

@pytest.mark.asyncio
async def test_copy_file(fs, tmp_dir):
    src = tmp_dir / "file_a.txt"
    dest_dir = tmp_dir / "subdir"
    await fs.copy([str(src)], str(dest_dir))
    assert (dest_dir / "file_a.txt").is_file()
    # Original should still exist
    assert src.is_file()


# ------------------------------------------------------------------ move

@pytest.mark.asyncio
async def test_move_file(fs, tmp_dir):
    src = tmp_dir / "file_b.py"
    dest_dir = tmp_dir / "subdir"
    await fs.move([str(src)], str(dest_dir))
    assert (dest_dir / "file_b.py").is_file()
    assert not src.is_file()


# ------------------------------------------------------------------ rename

@pytest.mark.asyncio
async def test_rename_file(fs, tmp_dir):
    src = tmp_dir / "file_a.txt"
    dst = tmp_dir / "renamed.txt"
    await fs.rename(str(src), str(dst))
    assert dst.is_file()
    assert not src.is_file()


# ------------------------------------------------------------------ delete

@pytest.mark.asyncio
async def test_delete_file(fs, tmp_dir):
    target = tmp_dir / "file_a.txt"
    assert target.is_file()
    await fs.delete([str(target)], use_trash=False)
    assert not target.is_file()


# ------------------------------------------------------------------ get_parent

def test_get_parent(fs, tmp_dir):
    parent = fs.get_parent(str(tmp_dir / "subdir"))
    assert parent == str(tmp_dir)


def test_get_parent_root(fs):
    root = "C:\\"
    result = fs.get_parent(root)
    # On Windows, parent of C:\ is C:\ (no further up)
    assert result is None or result == root


# ------------------------------------------------------------------ path_exists

def test_path_exists(fs, tmp_dir):
    assert fs.path_exists(str(tmp_dir))
    assert not fs.path_exists(str(tmp_dir / "ghost_file.xyz"))


# ------------------------------------------------------------------ total_size

@pytest.mark.asyncio
async def test_get_total_size(fs, tmp_dir):
    files = [str(tmp_dir / "file_a.txt"), str(tmp_dir / "file_b.py")]
    size = await fs.get_total_size(files)
    assert size > 0
