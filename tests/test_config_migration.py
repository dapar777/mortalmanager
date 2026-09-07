"""%APPDATA% folder rename (MortalManager → UltimateCommander) on first start."""

from __future__ import annotations

from pathlib import Path

from src.settings import config


def test_migrate_moves_old_folder(tmp_path: Path, monkeypatch):
    old = tmp_path / "MortalManager"
    new = tmp_path / "UltimateCommander"
    old.mkdir()
    (old / "config.db").write_bytes(b"x")
    monkeypatch.setattr(config, "_OLD_APP_DATA_DIR", old)
    monkeypatch.setattr(config, "_APP_DATA_DIR", new)
    assert config._migrate_data_dir() == new
    assert (new / "config.db").exists() and not old.exists()


def test_migrate_keeps_new_when_both_exist(tmp_path: Path, monkeypatch):
    old = tmp_path / "MortalManager"
    new = tmp_path / "UltimateCommander"
    old.mkdir(); new.mkdir()
    monkeypatch.setattr(config, "_OLD_APP_DATA_DIR", old)
    monkeypatch.setattr(config, "_APP_DATA_DIR", new)
    assert config._migrate_data_dir() == new
    assert old.exists()          # never deleted


def test_migrate_falls_back_when_locked(tmp_path: Path, monkeypatch):
    old = tmp_path / "MortalManager"
    new = tmp_path / "UltimateCommander"
    old.mkdir()
    monkeypatch.setattr(config, "_OLD_APP_DATA_DIR", old)
    monkeypatch.setattr(config, "_APP_DATA_DIR", new)
    with open(old / "config.db", "wb") as fh:      # an open file blocks the rename on Windows
        fh.write(b"x")
        assert config._migrate_data_dir() == old
