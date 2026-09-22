"""Browsing an archive in the panel: entering, listing, going up, temp files."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from src.archive import archive_manager as am
from src.gui.archive_browse import TempExtracts, list_location, location_of
from src.archive.vfs import ArchiveLocation


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    root = tmp_path / "src"
    (root / "docs").mkdir(parents=True)
    (root / "readme.txt").write_text("hello", encoding="utf-8")
    (root / "docs" / "index.md").write_text("# index", encoding="utf-8")
    return root


@pytest.fixture
def archive(tmp_path: Path, tree: Path) -> Path:
    out = tmp_path / "pack.zip"
    am.create_archive(out, [tree], base_dir=tree.parent)
    return out


@pytest.fixture
def panel(qapp, tmp_path: Path):
    from src.solarqt import theme
    from src.gui.panel import PanelWidget

    theme.apply(qapp, "light", zoom=1.0)
    p = PanelWidget(str(tmp_path), "left")
    p.resize(700, 500)
    p.show()
    yield p
    p.cleanup_temp()
    p.deleteLater()


def _settle(qapp, panel, expect: int, timeout: float = 5.0) -> list[str]:
    """Pump the event loop until the table holds *expect* rows."""
    end = time.time() + timeout
    while time.time() < end:
        qapp.processEvents()
        model = panel._table.file_model()
        if model.rowCount() == expect:
            break
        time.sleep(0.01)
    model = panel._table.file_model()
    return [model.get_entry(r).name for r in range(model.rowCount())]


# ------------------------------------------------------------------ listing (B2, B4)

def test_list_location_rows(archive: Path):
    rows = list_location(ArchiveLocation(archive))
    assert [r.name for r in rows] == ["..", "src"]
    assert rows[0].is_parent and rows[1].is_dir

    inner = list_location(ArchiveLocation(archive, "src"))
    assert [r.name for r in inner] == ["..", "docs", "readme.txt"]
    readme = inner[2]
    assert readme.size == len("hello") and not readme.is_dir
    assert readme.full_path == str(archive / "src" / "readme.txt")   # TC-style path


def test_parent_of_archive_root_points_at_the_folder(archive: Path):
    rows = list_location(ArchiveLocation(archive))
    assert rows[0].full_path == str(archive.parent)                  # B3


def test_read_only_format_marks_entries_readonly(archive: Path, monkeypatch):
    monkeypatch.setattr(am, "is_writable", lambda p: False)
    rows = list_location(ArchiveLocation(archive, "src"))
    assert rows[2].is_readonly                                        # F7 shows in the panel


# ------------------------------------------------------------------ navigation (B1, B3)

def test_enter_archive_and_walk_it(qapp, panel, archive: Path):
    assert panel.enter_archive(str(archive))
    assert _settle(qapp, panel, 2) == ["..", "src"]
    assert panel.in_archive and panel.archive_location.is_root
    assert panel.current_path == str(archive)

    panel.navigate_to(str(archive / "src"))
    assert _settle(qapp, panel, 3) == ["..", "docs", "readme.txt"]
    assert panel.archive_location.inner == "src"

    panel._go_up()                                                    # back to the archive root
    assert _settle(qapp, panel, 2) == ["..", "src"]
    assert panel.archive_location.is_root

    panel._go_up()                                                    # out onto the disk
    _settle(qapp, panel, 2)
    assert not panel.in_archive
    assert panel.current_path == str(archive.parent)


def test_enter_archive_rejects_plain_files(qapp, panel, tmp_path: Path):
    plain = tmp_path / "notes.txt"
    plain.write_text("x")
    assert not panel.enter_archive(str(plain))
    fake = tmp_path / "fake.zip"
    fake.write_text("not a zip")
    assert not panel.enter_archive(str(fake))                         # F6: content decides


def test_leaving_the_archive_puts_the_cursor_on_it(qapp, panel, archive: Path):
    panel.enter_archive(str(archive))
    _settle(qapp, panel, 2)
    panel._go_up()
    _settle(qapp, panel, 2)
    assert panel._current_tab.cursor_name == archive.name             # B3


def test_missing_inner_directory_reports_an_error(qapp, panel, archive: Path):
    panel.navigate_to(str(archive / "nope"))
    end = time.time() + 5
    while time.time() < end and "Error" not in panel._info_label.text():
        qapp.processEvents()
        time.sleep(0.01)
    assert "Error" in panel._info_label.text()                        # T3: no crash


def test_location_of_helper(archive: Path, tree: Path):
    assert location_of(str(archive / "src")).inner == "src"
    assert location_of(str(tree)) is None


# ------------------------------------------------------------------ temp files (E3, E4, E6)

def test_temp_extract_and_save_back(archive: Path):
    temp = TempExtracts()
    try:
        local = temp.extract(archive, "src/readme.txt")
        assert local.read_text(encoding="utf-8") == "hello"
        assert temp.source_of(local) == (archive, "src/readme.txt")
        assert not temp.changed(local)

        local.write_text("edited", encoding="utf-8")
        assert temp.changed(local)
        assert temp.save_back(local) == (archive, "src/readme.txt")
        assert am.read_member(archive, "src/readme.txt") == b"edited"
        assert not temp.changed(local)
    finally:
        root = temp.root
        temp.cleanup()
        assert not root.exists()                                      # E6


def test_temp_keeps_same_names_apart(tmp_path: Path, tree: Path):
    a = tmp_path / "one.zip"
    b = tmp_path / "two.zip"
    am.create_archive(a, [tree / "readme.txt"])
    am.create_archive(b, [tree / "readme.txt"])
    temp = TempExtracts()
    try:
        assert temp.extract(a, "readme.txt") != temp.extract(b, "readme.txt")
    finally:
        temp.cleanup()


def test_save_back_of_unknown_file_is_ignored(tmp_path: Path):
    temp = TempExtracts()
    try:
        stray = tmp_path / "stray.txt"
        stray.write_text("x")
        assert temp.save_back(stray) is None
        assert not temp.changed(stray)
    finally:
        temp.cleanup()
