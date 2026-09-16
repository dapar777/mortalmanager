"""Windows shell context menu -> QMenu conversion (PanelWidget._add_shell_menu_items).

Regression: GetMenuState() of a popup item carries the number of its entries in
the high byte, so ``state & MF_SEPARATOR`` (0x800) was true for a submenu with
8-15 or 24-31 entries and TortoiseSVN's ~25-entry submenu turned into a separator.
"""

from __future__ import annotations

import pytest

win32gui = pytest.importorskip("win32gui")
win32con = pytest.importorskip("win32con")

from PySide6.QtWidgets import QMenu

from src.gui.panel import PanelWidget


class _Stub:
    """Just what _add_shell_menu_items needs from a PanelWidget."""

    _SHELL_VERBS_SKIPPED = PanelWidget._SHELL_VERBS_SKIPPED
    _add_shell_menu_items = PanelWidget._add_shell_menu_items      # recurses into submenus
    _shell_verb = staticmethod(lambda icm, cmd_id: "")
    _hbitmap_to_qicon = staticmethod(lambda hbmp: None)

    def _invoke_shell_command(self, *args) -> None:
        pass


def _build(n_sub: int):
    hmenu = win32gui.CreatePopupMenu()
    win32gui.AppendMenu(hmenu, win32con.MF_STRING, 1, "SVN Update")
    win32gui.AppendMenu(hmenu, win32con.MF_STRING, 2, "SVN Commit")
    sub = win32gui.CreatePopupMenu()
    for i in range(n_sub):
        win32gui.AppendMenu(sub, win32con.MF_STRING, 100 + i, f"Entry {i}")
    win32gui.AppendMenu(hmenu, win32con.MF_POPUP, sub, "TortoiseSVN")
    win32gui.AppendMenu(hmenu, win32con.MF_SEPARATOR, 0, "")
    win32gui.AppendMenu(hmenu, win32con.MF_STRING, 3, "Properties")
    return hmenu


@pytest.mark.parametrize("n_sub", [3, 8, 15, 25, 31])
def test_popup_with_any_entry_count_stays_a_submenu(qapp, n_sub: int):
    hmenu = _build(n_sub)
    try:
        menu = QMenu()
        assert PanelWidget._add_shell_menu_items(_Stub(), menu, hmenu, None, 0, "C:\\")
        labels = [a.text() for a in menu.actions()]
        assert labels == ["SVN Update", "SVN Commit", "TortoiseSVN", "", "Properties"]
        subs = [a for a in menu.actions() if a.menu() is not None]
        assert len(subs) == 1 and subs[0].text() == "TortoiseSVN"
        assert len(subs[0].menu().actions()) == n_sub
        seps = [a for a in menu.actions() if a.isSeparator()]
        assert len(seps) == 1
    finally:
        win32gui.DestroyMenu(hmenu)
