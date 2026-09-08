"""Keyboard drag & drop (Ctrl+.): start an OS drag of the marked files without
touching the mouse, Alt+Tab to the target window, arrows nudge the cursor,
Enter drops, Esc cancels.

How it works (all Win32, no Qt drag):

* The drag is ``pythoncom.DoDragDrop`` with our own ``IDropSource`` – Qt's
  source ends a drag as soon as no mouse button is held.
* The left mouse button is held down through ``SendInput`` for the whole
  drag (pressed over our own status bar, released there again): Windows
  keeps the mouse capture across an Alt+Tab only while a button is down,
  and OLE cancels the drag the moment the capture is lost. Qt must process
  that press *before* DoDragDrop starts, otherwise its own SetCapture inside
  the loop steals the capture from OLE (= cancel).
* OLE's loop only wakes on input, so a watcher thread injects a zero-length
  mouse move every 80 ms (a posted thread message is read as "capture lost"
  and cancels). OLE then re-targets from the cursor position and asks
  ``QueryContinueDrag``.
* The watcher thread also owns a low-level keyboard hook: Enter (drop), Esc
  (cancel) and the arrow keys (move the cursor by a tenth of the monitor)
  are handled there and swallowed, so the application in front does not
  see them (Enter would otherwise also "open" something there). Without the
  hook (install failed) the keys are polled with ``GetAsyncKeyState``.
* The watcher polls the foreground window too: when it changes, the cursor
  is moved (``SendInput``, physical pixels) to the centre of the new window.
* Other applications draw their own cursor while they are in front, so for
  the duration of the drag the system arrow / I-beam / hand cursors are
  replaced (``SetSystemCursor``) with a big custom "dragging N files" cursor
  and restored afterwards (``SystemParametersInfo(SPI_SETCURSORS)``).
* The data object is the shell's own (``SHCreateShellItemArrayFromIDLists``
  → ``BHID_DataObject``): CF_HDROP + IDList, exactly what Explorer offers.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import logging
import sys
import threading
import time

from PySide6.QtCore import QCoreApplication, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QGuiApplication, QImage, QPainter, QPainterPath, QPen, QPolygonF

from src.solarqt import theme

logger = logging.getLogger(__name__)

_user32 = ctypes.windll.user32 if sys.platform == "win32" else None
_gdi32 = ctypes.windll.gdi32 if sys.platform == "win32" else None
_kernel32 = ctypes.windll.kernel32 if sys.platform == "win32" else None

MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP = 0x0002, 0x0004
MOUSEEVENTF_ABSOLUTE = 0x8000
MOUSEEVENTF_VIRTUALDESK = 0x4000
INPUT_MOUSE = 0
VK_RETURN, VK_ESCAPE = 0x0D, 0x1B
VK_LEFT, VK_UP, VK_RIGHT, VK_DOWN = 0x25, 0x26, 0x27, 0x28
ARROWS = {VK_LEFT: (-1, 0), VK_RIGHT: (1, 0), VK_UP: (0, -1), VK_DOWN: (0, 1)}
SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN, SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 76, 77, 78, 79
DROPEFFECT_NONE, DROPEFFECT_COPY, DROPEFFECT_MOVE = 0, 1, 2
S_OK = 0
DRAGDROP_S_DROP = 0x00040100
DRAGDROP_S_CANCEL = 0x00040101
OCR_NORMAL, OCR_IBEAM, OCR_HAND, OCR_APPSTARTING = 32512, 32513, 32649, 32650
SPI_SETCURSORS = 0x0057
WH_KEYBOARD_LL = 13
WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP = 0x0100, 0x0101, 0x0104, 0x0105
QS_ALLINPUT = 0x04FF
PM_REMOVE = 0x0001
MONITOR_DEFAULTTONEAREST = 2
WAKE_MS = 80
ARROW_FRACTION = 10          # one arrow press = 1/10 of the monitor


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wt.LONG), ("dy", wt.LONG), ("mouseData", wt.DWORD), ("dwFlags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]


class _INPUT(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [("mi", _MOUSEINPUT)]
    _anonymous_ = ("u",)
    _fields_ = [("type", wt.DWORD), ("u", _U)]


class _ICONINFO(ctypes.Structure):
    _fields_ = [("fIcon", wt.BOOL), ("xHotspot", wt.DWORD), ("yHotspot", wt.DWORD),
                ("hbmMask", wt.HBITMAP), ("hbmColor", wt.HBITMAP)]


class _KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", wt.DWORD), ("scanCode", wt.DWORD), ("flags", wt.DWORD), ("time", wt.DWORD),
                ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]


class _MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("rcMonitor", wt.RECT), ("rcWork", wt.RECT), ("dwFlags", wt.DWORD)]


_HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, wt.WPARAM, wt.LPARAM)


# ------------------------------------------------------------------ input / windows

def _send_mouse(flags: int, dx: int = 0, dy: int = 0) -> None:
    inp = _INPUT()
    inp.type = INPUT_MOUSE
    inp.mi = _MOUSEINPUT(dx, dy, 0, flags, 0, None)
    _user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(_INPUT))


def move_cursor(x: int, y: int) -> None:
    """Move the mouse cursor (physical screen pixels) with a real input event."""
    vx, vy = _user32.GetSystemMetrics(SM_XVIRTUALSCREEN), _user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
    vw, vh = _user32.GetSystemMetrics(SM_CXVIRTUALSCREEN), _user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)
    _send_mouse(MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK,
                int((x - vx) * 65535 / max(1, vw - 1)), int((y - vy) * 65535 / max(1, vh - 1)))


def cursor_pos() -> tuple[int, int]:
    pt = wt.POINT()
    _user32.GetCursorPos(ctypes.byref(pt))
    return pt.x, pt.y


def foreground_window() -> int:
    return int(_user32.GetForegroundWindow())


def root_window(hwnd: int) -> int:
    GA_ROOT = 2
    return int(_user32.GetAncestor(wt.HWND(hwnd), GA_ROOT)) or hwnd


def window_center(hwnd: int) -> tuple[int, int] | None:
    rect = wt.RECT()
    if not _user32.GetWindowRect(wt.HWND(hwnd), ctypes.byref(rect)):
        return None
    return (rect.left + rect.right) // 2, (rect.top + rect.bottom) // 2


def client_bottom_center(hwnd: int) -> tuple[int, int] | None:
    """Physical point inside the client area near its bottom (the status bar) –
    where the synthetic button press / release lands so it clicks nothing.
    Off the frame on purpose: a press on the frame starts Windows' resize loop."""
    rect = wt.RECT()
    if not _user32.GetClientRect(wt.HWND(hwnd), ctypes.byref(rect)):
        return None
    pt = wt.POINT(rect.right // 2, max(0, rect.bottom - 16))
    _user32.ClientToScreen(wt.HWND(hwnd), ctypes.byref(pt))
    return pt.x, pt.y


def monitor_rect(x: int, y: int) -> tuple[int, int, int, int]:
    """(left, top, right, bottom) of the monitor containing the point."""
    hmon = _user32.MonitorFromPoint(wt.POINT(x, y), MONITOR_DEFAULTTONEAREST)
    info = _MONITORINFO()
    info.cbSize = ctypes.sizeof(_MONITORINFO)
    if hmon and _user32.GetMonitorInfoW(hmon, ctypes.byref(info)):
        r = info.rcMonitor
        return r.left, r.top, r.right, r.bottom
    return 0, 0, _user32.GetSystemMetrics(0), _user32.GetSystemMetrics(1)


def nudge_cursor(dx_steps: int, dy_steps: int) -> None:
    """Arrow key: move by a tenth of the current monitor, clamped to it."""
    x, y = cursor_pos()
    left, top, right, bottom = monitor_rect(x, y)
    nx = min(right - 1, max(left, x + dx_steps * (right - left) // ARROW_FRACTION))
    ny = min(bottom - 1, max(top, y + dy_steps * (bottom - top) // ARROW_FRACTION))
    move_cursor(nx, ny)


def key_down(vk: int) -> bool:
    return bool(_user32.GetAsyncKeyState(vk) & 0x8000)


# ------------------------------------------------------------------ custom cursor

def _cursor_image(count: int, size: int = 64) -> QImage:
    """Big pointer with a document stack and a count badge (theme colours)."""
    t = theme.current()
    img = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(0)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    s = size / 64.0
    arrow = QPolygonF([QPointF(2 * s, 2 * s), QPointF(2 * s, 40 * s), QPointF(12 * s, 31 * s),
                       QPointF(19 * s, 46 * s), QPointF(26 * s, 43 * s), QPointF(19 * s, 28 * s), QPointF(31 * s, 28 * s)])
    p.setPen(QPen(QColor(t.canvas), 2.5 * s))
    p.setBrush(QColor(t.text))
    p.drawPolygon(arrow)
    for i, off in enumerate((8, 4, 0)):
        r = QRectF((28 + off) * s, (30 + off) * s, 24 * s, 28 * s)
        path = QPainterPath()
        path.addRoundedRect(r, 3 * s, 3 * s)
        p.setPen(QPen(QColor(t.text), 1.5 * s))
        p.setBrush(QColor(t.card if i == 2 else t.panel))
        p.drawPath(path)
    label = str(count) if count < 100 else "99+"
    badge = QRectF(38 * s, 4 * s, 24 * s, 20 * s) if len(label) < 3 else QRectF(30 * s, 4 * s, 32 * s, 20 * s)
    p.setPen(QPen(QColor(t.canvas), 2 * s))
    p.setBrush(QColor(t.accent))
    p.drawRoundedRect(badge, 10 * s, 10 * s)
    f = theme.ui_font(10, QFont.Weight.Bold)
    f.setPixelSize(int(13 * s))
    p.setFont(f)
    p.setPen(QColor(t.accent_fg))
    p.drawText(badge, Qt.AlignmentFlag.AlignCenter, label)
    p.end()
    return img.convertToFormat(QImage.Format.Format_ARGB32)


def _make_cursor(count: int) -> int:
    """HCURSOR from the painted image (32-bit BGRA colour bitmap = alpha cursor)."""
    img = _cursor_image(count)
    w, h = img.width(), img.height()
    bits = bytes(img.constBits())
    hbm_color = _gdi32.CreateBitmap(w, h, 1, 32, bits)
    hbm_mask = _gdi32.CreateBitmap(w, h, 1, 1, None)
    info = _ICONINFO(False, 2, 2, hbm_mask, hbm_color)
    hcur = _user32.CreateIconIndirect(ctypes.byref(info))
    _gdi32.DeleteObject(hbm_color)
    _gdi32.DeleteObject(hbm_mask)
    return int(hcur)


class _SystemCursorOverride:
    """Replace the system arrow / I-beam / hand with ``hcur`` for the drag and put
    the user's scheme back afterwards (also covers cursors drawn by other apps)."""

    IDS = (OCR_NORMAL, OCR_IBEAM, OCR_HAND, OCR_APPSTARTING)

    def __init__(self, hcur: int) -> None:
        self._hcur = hcur

    def __enter__(self) -> "_SystemCursorOverride":
        for cid in self.IDS:
            copy = _user32.CopyIcon(wt.HANDLE(self._hcur))     # SetSystemCursor destroys what it gets
            if copy:
                _user32.SetSystemCursor(wt.HANDLE(copy), cid)
        return self

    def __exit__(self, *exc) -> None:
        _user32.SystemParametersInfoW(SPI_SETCURSORS, 0, None, 0)
        _user32.DestroyCursor(wt.HANDLE(self._hcur))


# ------------------------------------------------------------------ keys, watcher, drop source

class _Keys:
    """Enter / Esc flags, set by the hook and – as a backup, e.g. when the hook was
    silently removed – by polling GetAsyncKeyState in the watcher (bit 0 = "pressed
    since the last call", so a short tap between two polls is not lost)."""

    def __init__(self) -> None:
        self.enter = False
        self.escape = False
        self.hooked = False

    def poll(self) -> None:
        if _user32.GetAsyncKeyState(VK_RETURN) & 0x8001:
            self.enter = True
        if _user32.GetAsyncKeyState(VK_ESCAPE) & 0x8001:
            self.escape = True

    def drop_requested(self) -> bool:
        return self.enter

    def cancel_requested(self) -> bool:
        return self.escape


class _DropSource:
    _public_methods_ = ["QueryContinueDrag", "GiveFeedback"]
    _com_interfaces_ = []                     # set to [IID_IDropSource] at run time

    def __init__(self, hcur: int, keys: _Keys) -> None:
        self.hcur = hcur
        self.keys = keys

    def QueryContinueDrag(self, escape_pressed: int, key_state: int) -> int:  # noqa: N802
        if escape_pressed or self.keys.cancel_requested():
            return DRAGDROP_S_CANCEL
        if self.keys.drop_requested():
            return DRAGDROP_S_DROP
        return S_OK

    def GiveFeedback(self, effect: int) -> int:  # noqa: N802
        _user32.SetCursor(wt.HANDLE(self.hcur))  # our own window: same cursor as everywhere else
        return S_OK


class _Watcher(threading.Thread):
    """Keeps OLE's loop awake, follows Alt+Tab, owns the keyboard hook (see the module docstring)."""

    def __init__(self, own_hwnd: int, keys: _Keys) -> None:
        super().__init__(daemon=True)
        self._own = root_window(own_hwnd)
        self._last_fg = self._own
        self._keys = keys
        self.stop = threading.Event()
        self._hook = None
        self._proc = None                    # keep the callback alive while hooked

    # ---- low-level keyboard hook: swallow Enter / Esc / arrows while dragging
    def _hook_proc(self, code: int, wparam: int, lparam: int) -> int:
        if code >= 0 and wparam in (WM_KEYDOWN, WM_SYSKEYDOWN, WM_KEYUP, WM_SYSKEYUP):
            vk = ctypes.cast(lparam, ctypes.POINTER(_KBDLLHOOKSTRUCT)).contents.vkCode
            down = wparam in (WM_KEYDOWN, WM_SYSKEYDOWN)
            if vk == VK_RETURN:
                if down:
                    self._keys.enter = True
                return 1
            if vk == VK_ESCAPE:
                if down:
                    self._keys.escape = True
                return 1
            if vk in ARROWS:
                if down:
                    nudge_cursor(*ARROWS[vk])
                return 1
        return _user32.CallNextHookEx(None, code, wt.WPARAM(wparam), wt.LPARAM(lparam))

    def _install_hook(self) -> None:
        try:
            # 64-bit handles: ctypes' default int return type truncates HMODULE / HHOOK
            _kernel32.GetModuleHandleW.restype = wt.HMODULE
            _user32.SetWindowsHookExW.argtypes = [ctypes.c_int, _HOOKPROC, wt.HMODULE, wt.DWORD]
            _user32.SetWindowsHookExW.restype = wt.HHOOK
            _user32.CallNextHookEx.argtypes = [wt.HHOOK, ctypes.c_int, wt.WPARAM, wt.LPARAM]
            _user32.CallNextHookEx.restype = ctypes.c_ssize_t
            _user32.UnhookWindowsHookEx.argtypes = [wt.HHOOK]
            self._proc = _HOOKPROC(self._hook_proc)
            self._hook = _user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._proc,
                                                   _kernel32.GetModuleHandleW(None), 0)
            self._keys.hooked = bool(self._hook)
        except Exception:
            logger.exception("keyboard drag: hook install failed")
            self._hook = None

    def run(self) -> None:
        self._install_hook()
        logger.info("keyboard drag: hook %s", "installed" if self._keys.hooked else "NOT installed – polling keys")
        _user32.GetAsyncKeyState(VK_RETURN)          # consume "pressed since last call" bits left over
        _user32.GetAsyncKeyState(VK_ESCAPE)          # from before the drag (e.g. the Enter that opened a folder)
        msg = wt.MSG()
        try:
            while not self.stop.is_set():
                # the hook is delivered through this thread's message queue: wait for it (or WAKE_MS)
                _user32.MsgWaitForMultipleObjects(0, None, False, WAKE_MS, QS_ALLINPUT)
                while _user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_REMOVE):
                    _user32.TranslateMessage(ctypes.byref(msg))
                    _user32.DispatchMessageW(ctypes.byref(msg))
                self._keys.poll()
                fg = root_window(foreground_window())
                if fg and fg != self._last_fg:
                    self._last_fg = fg
                    if fg != self._own:
                        c = window_center(fg)
                        if c:
                            move_cursor(*c)
                # wake DoDragDrop the way a real drag does: an injected mouse move (to the same spot)
                # reaches OLE's capture window, it re-targets and asks QueryContinueDrag
                move_cursor(*cursor_pos())
        finally:
            if self._hook:
                _user32.UnhookWindowsHookEx(self._hook)
                self._hook = None


def run_keyboard_drag(paths: list[str], own_hwnd: int) -> str:
    """Blocking OLE drag of ``paths``; returns "copy", "move" or "none" (cancelled)."""
    if sys.platform != "win32" or not paths:
        return "none"
    import pythoncom
    from win32com.server import util
    from win32com.shell import shell

    _DropSource._com_interfaces_ = [pythoncom.IID_IDropSource]
    pidls = []
    for p in paths:
        try:
            pidls.append(shell.SHParseDisplayName(p, 0)[0])
        except Exception:
            logger.debug("keyboard drag: cannot parse %s", p)
    if not pidls:
        return "none"
    items = shell.SHCreateShellItemArrayFromIDLists(pidls)
    data = items.BindToHandler(None, shell.BHID_DataObject, pythoncom.IID_IDataObject)
    hcur = _make_cursor(len(pidls))
    keys = _Keys()
    source = util.wrap(_DropSource(hcur, keys), pythoncom.IID_IDropSource)
    watcher = _Watcher(own_hwnd, keys)
    # Windows drops the mouse capture – and OLE cancels the drag – when the foreground window
    # changes, unless a mouse button is physically down. So the left button is pressed through
    # SendInput for the whole drag (over our own status bar, where the click does nothing) and
    # released back over the status bar afterwards, so no stray click reaches the target window.
    # The cursor itself stays where the user left it: it only visits the status bar for the
    # press / release (a few ms) and is put back, and moves for real only after Alt+Tab.
    anchor = client_bottom_center(own_hwnd)
    with _SystemCursorOverride(hcur):
        origin = cursor_pos()
        if anchor:
            move_cursor(*anchor)
            time.sleep(0.03)
        _send_mouse(MOUSEEVENTF_LEFTDOWN)
        # Qt must handle that press *before* the drag starts: it takes the mouse capture on a
        # press, and doing so inside DoDragDrop would steal the capture from OLE (= cancel).
        deadline = time.monotonic() + 0.3
        while time.monotonic() < deadline:
            QCoreApplication.processEvents()
            if QGuiApplication.mouseButtons() & Qt.MouseButton.LeftButton:
                break
            time.sleep(0.01)
        if anchor:
            move_cursor(*origin)
            time.sleep(0.02)
        watcher.start()
        try:
            effect = pythoncom.DoDragDrop(data, source, DROPEFFECT_COPY | DROPEFFECT_MOVE)
        finally:
            watcher.stop.set()
            watcher.join(1.0)
            end_pos = cursor_pos()
            if anchor:
                move_cursor(*anchor)
                time.sleep(0.03)
            _send_mouse(MOUSEEVENTF_LEFTUP)
            if anchor:
                time.sleep(0.02)
                move_cursor(*end_pos)
    if isinstance(effect, tuple):
        effect = effect[-1]
    if not effect:
        return "none"
    return "move" if effect & DROPEFFECT_MOVE else "copy"
