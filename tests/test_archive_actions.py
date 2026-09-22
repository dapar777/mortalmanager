"""Archive commands of the main window, without opening a real MainWindow.

MainWindow reads the user's real config, so the tests drive the mixin on a small
stand-in that records the jobs it would submit.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.archive import archive_manager as am
from src.gui.archive_actions import ArchiveActionsMixin, _archive_stem
from src.jobs.job import JobSpec, JobType


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


class _Panel:
    """Enough of PanelWidget for the commands under test."""

    def __init__(self, path: str, location=None, entries=None) -> None:
        self.current_path = path
        self._location = location
        self._entries = entries or []

    @property
    def archive_location(self):
        return self._location

    @property
    def in_archive(self) -> bool:
        return self._location is not None

    def selected_entries(self):
        return self._entries

    def current_entry(self):
        return self._entries[0] if self._entries else None


class _Host(ArchiveActionsMixin):
    """Records submitted jobs and toast messages instead of showing them."""

    def __init__(self, active: _Panel, inactive: _Panel) -> None:
        self._active, self._inactive = active, inactive
        self.jobs: list[JobSpec] = []
        self.toasts: list[tuple[str, str]] = []

    @property
    def _active_panel_widget(self):
        return self._active

    @property
    def _inactive_panel_widget(self):
        return self._inactive

    def _submit(self, spec: JobSpec, message: str = "") -> str:
        self.jobs.append(spec)
        return spec.job_id


@pytest.fixture(autouse=True)
def _quiet_toasts(monkeypatch):
    from src.solarqt import widgets

    monkeypatch.setattr(widgets.Toast, "show_message",
                        staticmethod(lambda *a, **k: None))


@pytest.fixture
def answer_yes(monkeypatch):
    """Answer every confirmation with Yes (overwrite / delete prompts)."""
    from PySide6.QtWidgets import QMessageBox

    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))


def _entry(name: str, is_dir: bool = False):
    from datetime import datetime

    from src.core.file_model import FileEntry

    now = datetime.now()
    return FileEntry(name=name, path=Path("C:/x") / name, size=1, modified=now, created=now,
                     is_dir=is_dir, is_symlink=False, attributes=0x20)


# ------------------------------------------------------------------ extract

def test_extract_here_from_a_selected_archive(archive: Path, tmp_path: Path, answer_yes):
    panel = _Panel(str(tmp_path), entries=[_entry(archive.name)])
    panel._entries[0].path = archive
    host = _Host(panel, _Panel(str(tmp_path)))
    host._extract_here()
    assert len(host.jobs) == 1
    spec = host.jobs[0]
    assert spec.job_type == JobType.EXTRACT
    assert spec.destination == str(archive.parent)
    assert spec.options["members"] is None
    assert spec.options["overwrite"] is True


def test_extract_into_a_busy_folder_can_keep_existing_files(archive: Path, tmp_path: Path,
                                                            monkeypatch):
    """X6: answering No unpacks only what is not there yet."""
    from PySide6.QtWidgets import QMessageBox

    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.StandardButton.No))
    panel = _Panel(str(tmp_path), entries=[_entry(archive.name)])
    panel._entries[0].path = archive
    host = _Host(panel, _Panel(str(tmp_path)))
    host._extract_here()
    assert host.jobs[0].options["overwrite"] is False


def test_extract_can_be_cancelled_at_the_overwrite_prompt(archive: Path, tmp_path: Path,
                                                          monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Cancel))
    panel = _Panel(str(tmp_path), entries=[_entry(archive.name)])
    panel._entries[0].path = archive
    host = _Host(panel, _Panel(str(tmp_path)))
    host._extract_here()
    assert not host.jobs


def test_extract_to_subfolder_names_it_after_the_archive(archive: Path, tmp_path: Path, answer_yes):
    panel = _Panel(str(tmp_path), entries=[_entry(archive.name)])
    panel._entries[0].path = archive
    host = _Host(panel, _Panel(str(tmp_path)))
    host._extract_to_subfolder()
    assert host.jobs[0].destination == str(archive.parent / "pack")


def test_extract_selection_into_the_other_panel(archive: Path, tmp_path: Path, answer_yes):
    from src.archive.vfs import ArchiveLocation

    location = ArchiveLocation(archive, "src")
    other_dir = tmp_path / "target"
    other_dir.mkdir()
    panel = _Panel(str(location), location, entries=[_entry("readme.txt")])
    host = _Host(panel, _Panel(str(other_dir)))
    host._extract_to_other_panel()
    spec = host.jobs[0]
    assert spec.job_type == JobType.EXTRACT
    assert spec.destination == str(other_dir)
    assert spec.options["members"] == ["src/readme.txt"]
    assert spec.options["strip_prefix"] == "src"        # X3


def test_extract_refuses_a_non_archive(tmp_path: Path):
    plain = tmp_path / "notes.txt"
    plain.write_text("x")
    entry = _entry(plain.name)
    entry.path = plain
    host = _Host(_Panel(str(tmp_path), entries=[entry]), _Panel(str(tmp_path)))
    host._extract_here()
    assert not host.jobs


# ------------------------------------------------------------------ add / delete

def test_archive_add_targets_the_current_inner_directory(archive: Path, tmp_path: Path):
    from src.archive.vfs import ArchiveLocation

    extra = tmp_path / "note.txt"
    extra.write_text("x")
    panel = _Panel(str(archive / "src"), ArchiveLocation(archive, "src"))
    host = _Host(panel, _Panel(str(tmp_path)))
    host._archive_add([str(extra)], panel)
    spec = host.jobs[0]
    assert spec.job_type == JobType.ARCHIVE_ADD
    assert spec.options["prefix"] == "src" and spec.options["archive"] == str(archive)


def test_archive_add_refuses_read_only_formats(archive: Path, tmp_path: Path, monkeypatch):
    from src.archive.vfs import ArchiveLocation

    monkeypatch.setattr(am, "is_writable", lambda p: False)
    monkeypatch.setattr(am, "format_name", lambda p: "RAR")
    panel = _Panel(str(archive), ArchiveLocation(archive))
    host = _Host(panel, _Panel(str(tmp_path)))
    host._archive_add([str(tmp_path / "x.txt")], panel)
    assert not host.jobs                                 # F7


def test_archive_delete_asks_and_submits(archive: Path, tmp_path: Path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    from src.archive.vfs import ArchiveLocation

    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    panel = _Panel(str(archive / "src"), ArchiveLocation(archive, "src"),
                   entries=[_entry("readme.txt")])
    host = _Host(panel, _Panel(str(tmp_path)))
    host._archive_delete()
    spec = host.jobs[0]
    assert spec.job_type == JobType.ARCHIVE_DELETE
    assert spec.sources == ["src/readme.txt"]


def test_archive_delete_respects_a_no(archive: Path, tmp_path: Path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    from src.archive.vfs import ArchiveLocation

    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.StandardButton.No))
    panel = _Panel(str(archive), ArchiveLocation(archive), entries=[_entry("src", True)])
    host = _Host(panel, _Panel(str(tmp_path)))
    host._archive_delete()
    assert not host.jobs


# ------------------------------------------------------------------ helpers

@pytest.mark.parametrize("name,stem", [
    ("src.zip", "src"), ("src.tar.gz", "src"), ("a.b.7z", "a.b"), ("plain", "plain"),
])
def test_archive_stem(name: str, stem: str):
    assert _archive_stem(Path("C:/x") / name) == stem


def test_pack_dialog_defaults(qapp, tree: Path, tmp_path: Path):
    from src.solarqt import theme
    from src.gui.dialogs.pack_dialog import PackDialog

    theme.apply(qapp, "light", zoom=1.0)
    dlg = PackDialog([str(tree)], str(tmp_path))
    try:
        opts = dlg.options()
        assert opts.target == tmp_path / "src.zip"      # C2: named after the single item
        assert opts.store_paths and not opts.move_to_archive

        dlg._format.setCurrentIndex([e for _l, e in dlg._formats].index("tar.gz"))
        assert dlg.options().target.name == "src.tar.gz"   # extension follows the format
    finally:
        dlg.deleteLater()


def test_pack_dialog_names_after_the_folder_for_many(qapp, tree: Path, tmp_path: Path):
    from src.solarqt import theme
    from src.gui.dialogs.pack_dialog import PackDialog

    theme.apply(qapp, "light", zoom=1.0)
    files = [str(tree / "readme.txt"), str(tree / "docs")]
    dlg = PackDialog(files, str(tmp_path))
    try:
        assert dlg.options().target.name == "src.zip"   # the folder holding them
    finally:
        dlg.deleteLater()
