from pathlib import Path
import pythoncom
import win32gui, win32con
from win32com.shell import shell, shellcon

path = Path.home() / '.gitconfig'
if not path.exists():
    path = Path.home()
parent = str(path.parent)
pythoncom.CoInitialize()
try:
    desktop = shell.SHGetDesktopFolder()
    parent_pidl = shell.SHILCreateFromPath(str(path.parent), 0)[0]
    folder = desktop.BindToObject(parent_pidl, None, shell.IID_IShellFolder)
    child_pidl = shell.SHILCreateFromPath(str(path), 0)[0]
    print('child pidl type', type(child_pidl), child_pidl[:10] if isinstance(child_pidl, (bytes, bytearray)) else child_pidl)
    icm = folder.GetUIObjectOf(0, [child_pidl], shell.IID_IContextMenu, 0)
    print('icm type', type(icm))
    if isinstance(icm, tuple):
        icm = icm[-1]
    for flags, name in [
        (shellcon.CMF_NORMAL | shellcon.CMF_CANRENAME, 'NORMAL|CANRENAME'),
        (shellcon.CMF_EXPLORE | shellcon.CMF_CANRENAME, 'EXPLORE|CANRENAME'),
        (shellcon.CMF_DEFAULTONLY, 'DEFAULTONLY'),
    ]:
        hmenu = win32gui.CreatePopupMenu()
        try:
            icm.QueryContextMenu(hmenu, 0, 1, 0x7fff, flags)
            count = win32gui.GetMenuItemCount(hmenu)
            print('flags', name, 'count', count)
            for i in range(count):
                print('  item', i, win32gui.GetMenuString(hmenu, i, win32con.MF_BYPOSITION))
        finally:
            win32gui.DestroyMenu(hmenu)
finally:
    pythoncom.CoUninitialize()
