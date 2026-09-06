"""Unit tests for the database manager."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from src.database.db import DatabaseManager, FtpSessionRow


@pytest.fixture
def db(tmp_path: Path) -> DatabaseManager:
    return DatabaseManager(tmp_path / "test.db")


# ------------------------------------------------------------------ settings

def test_set_and_get_setting(db: DatabaseManager):
    db.set_setting("theme", "dark")
    assert db.get_setting("theme") == "dark"


def test_setting_default(db: DatabaseManager):
    assert db.get_setting("nonexistent", "default_value") == "default_value"


def test_setting_overwrite(db: DatabaseManager):
    db.set_setting("key", "v1")
    db.set_setting("key", "v2")
    assert db.get_setting("key") == "v2"


def test_setting_complex_value(db: DatabaseManager):
    value = {"a": 1, "b": [1, 2, 3], "c": True}
    db.set_setting("complex", value)
    result = db.get_setting("complex")
    assert result == value


# ------------------------------------------------------------------ bookmarks

def test_add_and_get_bookmark(db: DatabaseManager):
    bid = db.add_bookmark("Home", "C:\\Users")
    bookmarks = db.get_bookmarks()
    assert len(bookmarks) == 1
    assert bookmarks[0].name == "Home"
    assert bookmarks[0].path == "C:\\Users"


def test_remove_bookmark(db: DatabaseManager):
    bid = db.add_bookmark("Work", "D:\\Work")
    db.remove_bookmark(bid)
    assert len(db.get_bookmarks()) == 0


def test_update_bookmark(db: DatabaseManager):
    bid = db.add_bookmark("Old", "C:\\Old")
    db.update_bookmark(bid, "New", "D:\\New")
    bm = db.get_bookmarks()[0]
    assert bm.name == "New"
    assert bm.path == "D:\\New"


# ------------------------------------------------------------------ FTP sessions

def test_save_and_get_ftp_session(db: DatabaseManager):
    s = FtpSessionRow(
        id=0, name="myserver", protocol="SFTP",
        host="sftp.example.com", port=22,
        username="user", password="pass",
        remote_path="/home/user", passive=True,
    )
    db.save_ftp_session(s)
    sessions = db.get_ftp_sessions()
    assert len(sessions) == 1
    assert sessions[0].name == "myserver"
    assert sessions[0].protocol == "SFTP"


def test_delete_ftp_session(db: DatabaseManager):
    s = FtpSessionRow(
        id=0, name="temp", protocol="FTP",
        host="ftp.example.com", port=21,
        username="", password="",
        remote_path="/", passive=True,
    )
    sid = db.save_ftp_session(s)
    sessions = db.get_ftp_sessions()
    db.delete_ftp_session(sessions[0].id)
    assert len(db.get_ftp_sessions()) == 0


# ------------------------------------------------------------------ operation history

def test_log_and_get_history(db: DatabaseManager):
    db.log_operation("copy", "C:\\src\\a.txt", "D:\\dst\\a.txt")
    db.log_operation("delete", "C:\\old.txt")
    history = db.get_operation_history()
    assert len(history) == 2
    assert history[0].op_type == "delete"  # most recent first


def test_clear_history(db: DatabaseManager):
    db.log_operation("move", "C:\\a.txt", "D:\\a.txt")
    db.clear_operation_history()
    assert len(db.get_operation_history()) == 0


# ------------------------------------------------------------------ path history

def test_path_history(db: DatabaseManager):
    db.add_path_history("C:\\Users")
    db.add_path_history("D:\\Projects")
    db.add_path_history("C:\\Windows")
    paths = db.get_path_history()
    assert "C:\\Users" in paths
    assert len(paths) == 3
