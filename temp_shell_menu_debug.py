from pathlib import Path
import pythoncom
import win32gui, win32con
from win32com.shell import shell, shellcon

path = Path.home() / '.gitconfig'
print('test path', path)
if not path.exists():
    path = Path.home()
print('using', path)
parent = str(path.parent)
pythoncom.CoInitialize()
try:
    desktop = shell.SHGetDesktopFolder()
    parent_pidl = shell.SHILCreateFromPath(parent, 0)[0]
    folder = desktop.BindToObject(parent_pidl, None, shell.IID_IShellFolder)
    result = folder.ParseDisplayName(0, None, path.name)
    print('parse result', result)
    pidl = None
    if isinstance(result, tuple):
        pidl = result[1] if len(result) >= 2 else result[0] if result else None
    else:
        pidl = result
    if isinstance(pidl, list) and pidl:
        pidl = pidl[0]
    print('pidl', pidl, type(pidl))
    icm = folder.GetUIObjectOf(0, [pidl], shell.IID_IContextMenu, 0)
    print('icm result', type(icm), repr(icm)[:200])
    if isinstance(icm, tuple):
        icm = icm[-1]
    for flags, name in [
        (shellcon.CMF_NORMAL | shellcon.CMF_CANRENAME, 'NORMAL|CANRENAME'),
        (shellcon.CMF_EXPLORE | shellcon.CMF_CANRENAME, 'EXPLORE|CANRENAME'),
        (shellcon.CMF_DEFAULTONLY, 'DEFAULTONLY'),
        (shellcon.CMF_EXPLORE, 'EXPLORE'),
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
