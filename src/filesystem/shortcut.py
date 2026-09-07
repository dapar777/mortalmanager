"""Windows .lnk shortcuts: read the target so Enter on a folder shortcut can
navigate into the folder (Total Commander) instead of opening Explorer."""

from __future__ import annotations

import os


def shortcut_target(path: str) -> str | None:
    """Target path of a ``.lnk`` file (environment variables expanded), or
    None when it cannot be read. Uses IShellLink through pywin32; COM is
    initialised for the calling thread if needed and never uninitialised."""
    try:
        import pythoncom
        from win32com.shell import shell
    except ImportError:
        return None
    try:
        pythoncom.CoInitialize()
    except Exception:
        pass
    try:
        link = pythoncom.CoCreateInstance(
            shell.CLSID_ShellLink, None, pythoncom.CLSCTX_INPROC_SERVER, shell.IID_IShellLink
        )
        link.QueryInterface(pythoncom.IID_IPersistFile).Load(path)
        target, _ = link.GetPath(shell.SLGP_RAWPATH)
    except Exception:
        return None
    target = os.path.expandvars(target or "").strip()
    return target or None


def shortcut_folder(path: str) -> str | None:
    """Folder the shortcut points at (local or UNC), or None if it targets a file / nothing."""
    target = shortcut_target(path)
    if target and os.path.isdir(target):
        return target
    return None
