"""Diagnostic: build the Windows shell context menu of a path the way
PanelWidget._populate_windows_shell_menu does and print the HMENU tree.

    python tools/dump_shell_menu.py "C:\\svn\\CAR\\db\\2024_3x"

Use it when a shell extension (TortoiseSVN, 7-Zip…) shows up in Explorer or
Total Commander but not in our context menu: the dump tells whether the item
is missing from the HMENU (extension / flags problem) or lost in the QMenu
conversion. For every item: position, command id, submenu handle, MENUITEMINFO
type flags, GetMenuState (high byte = number of entries of a popup!) and text.
"""

from __future__ import annotations

import ctypes
import sys
from pathlib import Path

import pythoncom
import win32con
import win32gui
from win32com.shell import shell, shellcon

MIIM_STATE, MIIM_ID, MIIM_SUBMENU, MIIM_FTYPE = 0x1, 0x2, 0x4, 0x100
MFT_OWNERDRAW, MFT_SEPARATOR = 0x100, 0x800


class _MIIW(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_uint), ("fMask", ctypes.c_uint), ("fType", ctypes.c_uint),
        ("fState", ctypes.c_uint), ("wID", ctypes.c_uint), ("hSubMenu", ctypes.c_void_p),
        ("hbmpChecked", ctypes.c_void_p), ("hbmpUnchecked", ctypes.c_void_p),
        ("dwItemData", ctypes.c_size_t), ("dwTypeData", ctypes.c_wchar_p), ("cch", ctypes.c_uint),
        ("hbmpItem", ctypes.c_void_p),
    ]


def dump(hmenu: int, depth: int = 0) -> None:
    for i in range(win32gui.GetMenuItemCount(hmenu)):
        mii = _MIIW()
        mii.cbSize = ctypes.sizeof(_MIIW)
        mii.fMask = MIIM_FTYPE | MIIM_ID | MIIM_SUBMENU | MIIM_STATE
        ctypes.windll.user32.GetMenuItemInfoW(hmenu, i, True, ctypes.byref(mii))
        buf = ctypes.create_unicode_buffer(256)
        n = ctypes.windll.user32.GetMenuStringW(hmenu, i, buf, 256, win32con.MF_BYPOSITION)
        text = buf.value[:n]
        sub = win32gui.GetSubMenu(hmenu, i)
        kind = "SEP" if mii.fType & MFT_SEPARATOR else ("OWNERDRAW" if mii.fType & MFT_OWNERDRAW else "")
        gms = win32gui.GetMenuState(hmenu, i, win32con.MF_BYPOSITION)
        print("  " * depth + f"[{i}] id={mii.wID} sub={sub or 0} ftype=0x{mii.fType:x} {kind}"
              f" GetMenuState=0x{gms:x} text={text!r}")
        if sub:
            dump(sub, depth + 1)


def main(path: str) -> None:
    pythoncom.CoInitialize()
    hwnd = win32gui.GetDesktopWindow()
    desktop = shell.SHGetDesktopFolder()
    p = Path(path)
    if p.name == "":
        folder, pidl = desktop, shell.SHILCreateFromPath(path, 0)[0]
    else:
        parent_pidl = shell.SHILCreateFromPath(str(p.parent), 0)[0]
        folder = desktop.BindToObject(parent_pidl, None, shell.IID_IShellFolder)
        result = folder.ParseDisplayName(hwnd, None, p.name)
        pidl = result[1] if isinstance(result, tuple) and len(result) >= 2 else result
        if isinstance(pidl, list) and pidl:
            pidl = pidl[-1:]
    icm = folder.GetUIObjectOf(hwnd, [pidl], shell.IID_IContextMenu, 0)
    if isinstance(icm, tuple):
        icm = icm[-1]
    hmenu = win32gui.CreatePopupMenu()
    flags = shellcon.CMF_NORMAL | shellcon.CMF_EXPLORE | shellcon.CMF_CANRENAME
    icm.QueryContextMenu(hmenu, 0, 1, 0x7FFF, flags)
    print(f"== {path}   top-level items: {win32gui.GetMenuItemCount(hmenu)}")
    dump(hmenu)
    win32gui.DestroyMenu(hmenu)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
