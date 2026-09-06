"""Unit tests for archive handlers."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from src.archive.zip_handler import ZipHandler
from src.archive.tar_handler import TarHandler
from src.archive.archive_manager import is_archive, get_handler, list_archive, extract_archive


@pytest.fixture
def sample_files(tmp_path: Path) -> list[Path]:
    files = []
    for i in range(3):
        p = tmp_path / f"file{i}.txt"
        p.write_text(f"content of file {i}")
        files.append(p)
    return files


@pytest.fixture
def zip_archive(tmp_path: Path, sample_files: list[Path]) -> Path:
    archive = tmp_path / "test.zip"
    handler = ZipHandler()
    handler.create(archive, sample_files)
    return archive


@pytest.fixture
def tar_archive(tmp_path: Path, sample_files: list[Path]) -> Path:
    archive = tmp_path / "test.tar.gz"
    handler = TarHandler()
    handler.create(archive, sample_files)
    return archive


# ------------------------------------------------------------------ ZIP

def test_zip_create_and_list(zip_archive: Path):
    handler = ZipHandler()
    entries = handler.list_contents(zip_archive)
    names = {e.name for e in entries}
    assert "file0.txt" in names
    assert "file1.txt" in names
    assert "file2.txt" in names


def test_zip_extract(zip_archive: Path, tmp_path: Path):
    dest = tmp_path / "extracted"
    dest.mkdir()
    handler = ZipHandler()
    handler.extract(zip_archive, dest)
    assert (dest / "file0.txt").is_file()


def test_zip_read_member(zip_archive: Path):
    handler = ZipHandler()
    data = handler.read_member(zip_archive, "file0.txt")
    assert b"content" in data


def test_zip_can_handle(zip_archive: Path):
    handler = ZipHandler()
    assert handler.can_handle(zip_archive)


# ------------------------------------------------------------------ TAR

def test_tar_create_and_list(tar_archive: Path):
    handler = TarHandler()
    entries = handler.list_contents(tar_archive)
    names = {e.name for e in entries}
    assert "file0.txt" in names


def test_tar_extract(tar_archive: Path, tmp_path: Path):
    dest = tmp_path / "tar_extracted"
    dest.mkdir()
    handler = TarHandler()
    handler.extract(tar_archive, dest)
    extracted = list(dest.rglob("file0.txt"))
    assert len(extracted) == 1


def test_tar_can_handle(tar_archive: Path):
    handler = TarHandler()
    assert handler.can_handle(tar_archive)


# ------------------------------------------------------------------ archive_manager

def test_is_archive_zip(zip_archive: Path):
    assert is_archive(zip_archive)


def test_is_archive_tar(tar_archive: Path):
    assert is_archive(tar_archive)


def test_is_archive_non_archive(tmp_path: Path):
    p = tmp_path / "plain.txt"
    p.write_text("hello")
    assert not is_archive(p)


def test_get_handler_zip(zip_archive: Path):
    handler = get_handler(zip_archive)
    assert isinstance(handler, ZipHandler)


def test_list_archive(zip_archive: Path):
    entries = list_archive(zip_archive)
    assert len(entries) == 3
