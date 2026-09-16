"""Mnemonic clashes in menus: cycle through the items, never pop a submenu open.

QMenu handles an underlined letter shared by several items by moving to the
next match *with popup delay 0*: when that item has a submenu, the submenu
opens at once and keyboard focus moves into it, so the letter cannot be
pressed again to reach the further matches (Windows menus just move the
highlight). ``step_mnemonic`` does the moving itself: it walks the highlight
with synthetic Down presses – Down never opens the submenu of the item it
lands on – so Enter / Right opens the submenu only when the user says so.
A letter matching exactly one item keeps Qt's behaviour (trigger / open).
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QAction, QKeyEvent
from PySide6.QtWidgets import QMenu


def mnemonic_of(text: str) -> str:
    """Underlined letter of a menu text, casefolded ("P&ipnout" -> "i"), "" if none."""
    i = 0
    while i < len(text) - 1:
        if text[i] == "&":
            if text[i + 1] == "&":
                i += 2                  # literal ampersand
                continue
            return text[i + 1].casefold()
        i += 1
    return ""


def step_mnemonic(menu: QMenu, event: QKeyEvent) -> bool:
    """Handle a key press in ``menu``; True if it was a clashing mnemonic and the
    highlight was moved to the next matching item (the caller consumes the event)."""
    if event.type() != QEvent.Type.KeyPress:
        return False
    if event.modifiers() & ~(Qt.KeyboardModifier.ShiftModifier | Qt.KeyboardModifier.KeypadModifier):
        return False
    text = event.text()
    if len(text) != 1 or not text.isprintable() or text.isspace():
        return False
    key = text.casefold()
    matches: list[QAction] = [
        a for a in menu.actions()
        if a.isVisible() and a.isEnabled() and not a.isSeparator() and mnemonic_of(a.text()) == key
    ]
    if len(matches) < 2:
        return False                    # none, or unique: Qt triggers / opens it
    current = menu.activeAction()
    if current in matches:
        target = matches[(matches.index(current) + 1) % len(matches)]
    else:
        target = matches[0]
    down = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Down, Qt.KeyboardModifier.NoModifier)
    for _ in range(2 * len(menu.actions()) + 2):
        if menu.activeAction() is target:
            break
        QMenu.keyPressEvent(menu, down)
    return True
