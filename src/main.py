"""Application entry point."""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

# Make src importable regardless of how the app is launched
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from PySide6.QtCore import QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QProxyStyle, QStyle

from src.solarqt import theme

_LOG_FORMAT = "%(asctime)s  %(levelname)-8s  %(name)s – %(message)s"
if sys.stderr is None or sys.stdout is None:
    # started with pythonw.exe (no console): log to %APPDATA%\UltimateCommander\ultimatecommander.log
    from src.settings.config import ConfigManager
    _log_dir = ConfigManager.app_data_dir()
    _log_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format=_LOG_FORMAT, datefmt="%H:%M:%S",
                        filename=str(_log_dir / "ultimatecommander.log"), encoding="utf-8")
else:
    logging.basicConfig(level=logging.INFO, format=_LOG_FORMAT, datefmt="%H:%M:%S")
logger = logging.getLogger(__name__)


def _log_uncaught(exc_type, exc, tb) -> None:
    """Exceptions raised inside Qt slots would otherwise vanish under pythonw."""
    logger.error("Uncaught exception", exc_info=(exc_type, exc, tb))


sys.excepthook = _log_uncaught

ASSETS_DIR = _ROOT / "assets" / "icons"
APP_NAME = "Ultimate Commander"
APP_VERSION = "1.0.0"


def app_icon(variant: str = "dark") -> QIcon:
    """Terakota „files" icon (assets/icons) – ``variant`` "light" or "dark"
    matches the theme (light theme = paper background, dark = espresso)."""
    variant = "light" if variant == "light" else "dark"
    ico = ASSETS_DIR / f"ultimatecommander-{variant}.ico"
    png = ASSETS_DIR / f"ultimatecommander-{variant}-512.png"
    icon = QIcon(str(ico)) if ico.exists() else QIcon()
    if png.exists():
        icon.addFile(str(png))
    return icon


def _claim_taskbar_identity() -> None:
    """Without an explicit AppUserModelID Windows groups the window under
    python.exe and shows the Python icon in the taskbar."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("UltimateCommander.App")
    except Exception:
        pass


def apply_theme(app: QApplication, name: str = "light", zoom: float | None = None) -> None:
    """Apply the solarqt theme (Fusion + palette + QSS + font) at a zoom factor.
    Safe to call at runtime; the caller re-themes widgets afterwards."""
    theme.apply(app, name, zoom=zoom)


class _UnderlineStyle(QProxyStyle):
    """Force mnemonic underlines to always be visible (Win10/11 hides them)."""

    def styleHint(self, hint, option=None, widget=None, returnData=None):  # noqa: N802
        if hint == QStyle.StyleHint.SH_UnderlineShortcut:
            return 1
        return super().styleHint(hint, option, widget, returnData)


def main() -> int:
    _claim_taskbar_identity()
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)

    from src.settings.config import ConfigManager
    cfg = ConfigManager.get_instance().config
    theme.apply(app, cfg.theme, zoom=cfg.zoom)
    app.setWindowIcon(app_icon(cfg.theme))
    app.setStyle(_UnderlineStyle(app.style()))

    # Set up asyncio event loop that integrates with Qt
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    from src.gui.main_window import MainWindow
    window = MainWindow(loop)
    window.show()

    # Pump asyncio loop inside Qt event loop
    def _pump_asyncio() -> None:
        loop.call_soon(loop.stop)
        loop.run_forever()

    timer = QTimer()
    timer.timeout.connect(_pump_asyncio)
    timer.start(20)  # 20ms = ~50 Hz

    exit_code = app.exec()
    timer.stop()
    loop.close()
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
