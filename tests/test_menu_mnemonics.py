"""Menu mnemonic clashes cycle through the matches without opening a submenu."""

from __future__ import annotations

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QMenu

from src.gui.menu_mnemonics import mnemonic_of, step_mnemonic


def _key(ch: str) -> QKeyEvent:
    return QKeyEvent(QEvent.Type.KeyPress, ord(ch.upper()), Qt.KeyboardModifier.NoModifier, ch)


def test_mnemonic_of():
    assert mnemonic_of("&Otevřít") == "o"
    assert mnemonic_of("P&ipnout") == "i"
    assert mnemonic_of("Fish && Chips &Now") == "n"
    assert mnemonic_of("no mnemonic") == ""
    assert mnemonic_of("trailing &") == ""


def test_clash_cycles_without_opening_submenu(qapp):
    menu = QMenu()
    alpha = menu.addAction("&Alpha")
    beta = menu.addMenu("&Beta")               # submenu first among the "b" matches
    beta.addAction("Inside")
    menu.addSeparator()
    bravo = menu.addAction("&Bravo")
    bar = menu.addAction("Ba&r")               # mnemonic "r", not a "b" match
    boat = menu.addAction("&Boat")
    menu.popup(menu.pos())
    qapp.processEvents()

    assert step_mnemonic(menu, _key("b"))
    assert menu.activeAction() is beta.menuAction()
    assert not beta.isVisible()                # highlighted, not popped open
    assert step_mnemonic(menu, _key("b"))
    assert menu.activeAction() is bravo
    assert step_mnemonic(menu, _key("B"))      # case-insensitive, wraps
    assert menu.activeAction() is boat
    assert step_mnemonic(menu, _key("b"))
    assert menu.activeAction() is beta.menuAction()

    assert not step_mnemonic(menu, _key("a"))  # unique match: left to Qt (triggers Alpha)
    assert not step_mnemonic(menu, _key("x"))  # no match
    assert bar.text() == "Ba&r" and alpha.text() == "&Alpha"
    menu.close()
