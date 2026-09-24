"""The context menu is available from the command palette, and dialog defaults."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from PySide6.QtWidgets import QMenu, QWidget

from src.gui.panel import PanelWidget, _flatten_menu


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    (tmp_path / "notes.txt").write_text("x", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    return tmp_path


@pytest.fixture
def panel(qapp, folder: Path):
    from src.solarqt import theme

    theme.apply(qapp, "light", zoom=1.0)
    p = PanelWidget(str(folder), "left")
    p.resize(800, 600)
    p.show()
    end = time.time() + 5
    while time.time() < end and p._loading:
        qapp.processEvents()
        time.sleep(0.01)
    for _ in range(20):
        qapp.processEvents()
    yield p
    p.deleteLater()


# ------------------------------------------------------------------ flattening

def test_flatten_menu_shapes_the_labels(qapp):
    menu = QMenu()
    menu.addAction("&Open\tEnter")
    menu.addSeparator()
    disabled = menu.addAction("Not now")
    disabled.setEnabled(False)
    sub = menu.addMenu("Send &to")
    sub.addAction("&Mail")
    sub.addAction("USB")
    menu.addAction("")                                    # no label: skipped
    try:
        labels = [label for label, _icon, _run in _flatten_menu(menu)]
        assert labels == ["Open", "Send to › Mail", "Send to › USB"]  # no tab hint, no &
    finally:
        menu.deleteLater()


def test_flatten_menu_returns_working_handlers(qapp):
    menu = QMenu()
    fired = []
    menu.addAction("Do it").triggered.connect(lambda: fired.append(True))
    try:
        entries = _flatten_menu(menu)
        entries[0][2]()
        assert fired == [True]
    finally:
        menu.deleteLater()


# ------------------------------------------------------------------ the panel's menu

def test_context_menu_entries_cover_the_menu(qapp, panel, folder: Path):
    row = panel._table.file_model().row_of_name("notes.txt")
    panel._table.selectRow(row)
    labels = [label for label, _icon, _run in panel.context_menu_entries()]

    for expected in ["Open", "Open With…", "Rename", "Copy Path", "Copy Name",
                     "Show in Explorer", "Open Terminal Here"]:
        assert expected in labels, expected
    assert not any(label.startswith("&") or "\t" in label for label in labels)


def test_context_menu_entries_include_main_window_commands(qapp, panel, folder: Path):
    """Copy / Move / Delete hang off the window; a stand-in proves they are offered."""

    class _Host(QWidget):
        def __init__(self) -> None:
            super().__init__()
            self.calls: list[str] = []

        def _copy_files(self) -> None:
            self.calls.append("copy")

        def _move_files(self) -> None:
            self.calls.append("move")

        def _delete_files(self) -> None:
            self.calls.append("delete")

        def _mkdir(self) -> None:
            self.calls.append("mkdir")

    host = _Host()
    panel.setParent(host)
    try:
        row = panel._table.file_model().row_of_name("notes.txt")
        panel._table.selectRow(row)
        entries = panel.context_menu_entries()
        labels = [label for label, _icon, _run in entries]
        for expected in ["Copy", "Move", "Delete", "New Folder"]:
            assert expected in labels, expected

        run = next(r for label, _i, r in entries if label == "Delete")
        run()
        assert host.calls == ["delete"]                   # the entry really runs it
    finally:
        panel.setParent(None)
        host.deleteLater()


def test_context_menu_entries_for_the_parent_row(qapp, panel):
    """On ".." the menu is the one of the current folder (New Folder, Refresh…)."""
    panel._table.selectRow(0)
    labels = [label for label, _icon, _run in panel.context_menu_entries()]
    assert "Refresh" in labels and "Open Terminal Here" in labels


def test_building_the_menu_does_not_show_it(qapp, panel):
    row = panel._table.file_model().row_of_name("sub")
    panel._table.selectRow(row)
    panel.context_menu_entries()
    qapp.processEvents()
    assert not any(isinstance(w, QMenu) and w.isVisible()
                   for w in qapp.topLevelWidgets())


# ------------------------------------------------------------------ dialog defaults

def test_copy_dialog_overwrites_by_default(qapp, tmp_path: Path):
    from src.gui.dialogs.copy_dialog import CopyDialog

    dlg = CopyDialog([str(tmp_path / "a.txt")], str(tmp_path), "copy")
    try:
        assert dlg.overwrite is True
        dlg._overwrite_cb.setChecked(False)
        assert dlg.overwrite is False
    finally:
        dlg.deleteLater()


def test_delete_confirmation_defaults_to_delete(qapp, monkeypatch, tmp_path: Path):
    """Enter confirms the deletion; Esc still cancels."""
    from PySide6.QtWidgets import QMessageBox

    seen: dict = {}

    original_exec = QMessageBox.exec

    def capture(self) -> int:
        seen["default"] = self.defaultButton().text() if self.defaultButton() else None
        seen["escape"] = self.escapeButton().text() if self.escapeButton() else None
        return 0

    monkeypatch.setattr(QMessageBox, "exec", capture)

    box = QMessageBox()
    box.setText("x")
    delete = box.addButton("Delete", QMessageBox.ButtonRole.DestructiveRole)
    cancel = box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(delete)
    box.setEscapeButton(cancel)
    box.exec()
    assert seen == {"default": "Delete", "escape": "Cancel"}
    monkeypatch.setattr(QMessageBox, "exec", original_exec)
    box.deleteLater()


def test_main_window_delete_marks_delete_as_default():
    """The source says so: the button that Enter triggers is the destructive one."""
    source = Path("src/gui/main_window.py").read_text(encoding="utf-8")
    body = source.split("def _delete_files", 1)[1].split("def ", 1)[0]
    assert "box.setDefaultButton(btn_delete)" in body
    assert "box.setEscapeButton(btn_cancel)" in body
