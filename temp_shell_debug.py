from pathlib import Path
import pythoncom
import win32gui, win32con
from win32com.shell import shell, shellcon

path = Path.home()
parent = str(path.parent)
pythoncom.CoInitialize()
try:
    desktop = shell.SHGetDesktopFolder()
    parent_pidl = shell.SHILCreateFromPath(parent, 0)[0]
    folder = desktop.BindToObject(parent_pidl, None, shell.IID_IShellFolder)
    result = folder.ParseDisplayName(0, None, path.name)
    print('parse', result)
    pidl = result[0] if result else None
    print('pidl', pidl)
    icm = folder.GetUIObjectOf(0, [pidl], shell.IID_IContextMenu, 0)
    print('icm type', type(icm))
    if isinstance(icm, tuple):
        icm = icm[-1]
    hmenu = win32gui.CreatePopupMenu()
    icm.QueryContextMenu(hmenu, 0, 1, 0x7fff, shellcon.CMF_NORMAL | shellcon.CMF_CANRENAME)
    count = win32gui.GetMenuItemCount(hmenu)
    print('count', count)
    for i in range(count):
        print('item', i, win32gui.GetMenuString(hmenu, i, win32con.MF_BYPOSITION))
    win32gui.DestroyMenu(hmenu)
finally:
    pythoncom.CoUninitialize()
