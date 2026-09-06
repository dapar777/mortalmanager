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
from PySide6.QtWidgets import QApplication, QProxyStyle, QStyle

from src.gui.main_window import MainWindow

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s – %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

def _build_dark_qss(pt: int) -> str:
    return f"""
    QMainWindow, QDialog {{ background-color: #1e1e1e; }}
    QWidget {{ background-color: #1e1e1e; font-size: {pt}pt; }}
    QTableView {{
        background-color: #1a1a1a; color: #dcdcdc;
        gridline-color: transparent;
        selection-background-color: #1565C0; selection-color: #ffffff;
    }}
    QTableView::item:selected {{ background-color: #1565C0; color: #ffffff; }}
    QTableView::item:hover:!selected {{ background-color: #252535; }}
    QHeaderView::section {{
        background-color: #2d2d2d; color: #aaaaaa;
        border: none; border-right: 1px solid #3a3a3a;
        border-bottom: 1px solid #3a3a3a;
        padding: 3px 6px; font-weight: bold;
    }}
    QLineEdit {{
        background-color: #2a2a2a; color: #dcdcdc;
        border: 1px solid #555; border-radius: 3px; padding: 2px 4px;
    }}
    QLineEdit:focus {{ border-color: #3a7bca; }}
    QPushButton {{
        background-color: #3a3a3a; color: #dcdcdc;
        border: 1px solid #555; border-radius: 3px; padding: 4px 10px;
    }}
    QPushButton:hover {{ background-color: #4a4a4a; }}
    QPushButton:pressed {{ background-color: #2a2a2a; }}
    QToolButton {{
        background-color: #3a3a3a; color: #dcdcdc;
        border: 1px solid #555; border-radius: 3px; padding: 2px 4px;
    }}
    QToolButton:hover {{ background-color: #4a4a4a; }}
    QTabBar::tab {{
        background-color: #2d2d2d; color: #aaaaaa;
        border: 1px solid #444; border-bottom: none;
        padding: 3px 10px; min-width: 60px;
    }}
    QTabBar::tab:selected {{
        background-color: #1a1a1a; color: #dcdcdc;
        border-top: 2px solid #3a7bca;
    }}
    QTabBar::tab:hover {{ background-color: #3a3a3a; }}
    QMenuBar {{ background-color: #2d2d2d; color: #dcdcdc; }}
    QMenuBar::item:selected {{ background-color: #3a7bca; }}
    QMenu {{ background-color: #2d2d2d; color: #dcdcdc; border: 1px solid #555; }}
    QMenu::item:selected {{ background-color: #3a7bca; }}
    QScrollBar:vertical {{ background: #2a2a2a; width: 10px; }}
    QScrollBar::handle:vertical {{ background: #555; min-height: 20px; border-radius: 4px; }}
    QScrollBar:horizontal {{ background: #2a2a2a; height: 10px; }}
    QScrollBar::handle:horizontal {{ background: #555; min-width: 20px; border-radius: 4px; }}
    QStatusBar {{ background-color: #1a4a6b; color: #dcdcdc; }}
    QComboBox {{
        background-color: #2a2a2a; color: #dcdcdc;
        border: 1px solid #555; border-radius: 3px; padding: 2px 4px;
    }}
    QComboBox QAbstractItemView {{ background-color: #2d2d2d; color: #dcdcdc; }}
    QGroupBox {{ color: #aaaaaa; border: 1px solid #444; margin-top: 8px; padding-top: 6px; }}
    QGroupBox::title {{ subcontrol-origin: margin; left: 8px; }}
    QListWidget {{ background-color: #1a1a1a; color: #dcdcdc; }}
    QListWidget::item:selected {{ background-color: #1565C0; }}
    QTreeWidget {{ background-color: #1a1a1a; color: #dcdcdc; }}
    QTreeWidget::item:selected {{ background-color: #1565C0; }}
    QPlainTextEdit {{ background-color: #1a1a1a; color: #dcdcdc; }}
    QSplitter::handle {{ background-color: #3a3a3a; }}
    QFrame[frameShape="5"] {{ color: #555; }}
    QPlainTextEdit#TerminalOutput, QLineEdit#TerminalInput, QLabel#TerminalPrompt {{
        font-family: Consolas; font-size: {pt}pt;
    }}
"""


def _build_light_qss(pt: int) -> str:
    return f"""
    QWidget {{ font-size: {pt}pt; }}
    QTableView {{
        gridline-color: transparent;
        selection-background-color: #0078d4; selection-color: #ffffff;
    }}
    QTableView::item:selected {{ background-color: #0078d4; color: #ffffff; }}
    QTableView::item:hover:!selected {{ background-color: #e5f0fb; }}
    QStatusBar {{ background-color: #0078d4; color: #ffffff; }}
    QScrollBar:vertical {{ width: 10px; }}
    QScrollBar:horizontal {{ height: 10px; }}
    QFrame[frameShape="5"] {{ color: #999; }}
    QPlainTextEdit#TerminalOutput, QLineEdit#TerminalInput, QLabel#TerminalPrompt {{
        font-family: Consolas; font-size: {pt}pt;
    }}
"""


def apply_theme(app: QApplication, theme: str = "dark", font_pt: int = 9) -> None:
    """Apply dark or light theme to the application. Safe to call at runtime."""
    from PySide6.QtGui import QColor, QPalette
    if theme == "light":
        app.setPalette(QPalette())   # reset to system default
        app.setStyleSheet(_build_light_qss(font_pt))
    else:
        palette = QPalette()
        dark = QColor(30, 30, 30)
        mid_dark = QColor(45, 45, 45)
        text = QColor(220, 220, 220)
        highlight = QColor(58, 123, 202)
        disabled_text = QColor(120, 120, 120)
        palette.setColor(QPalette.ColorRole.Window, dark)
        palette.setColor(QPalette.ColorRole.WindowText, text)
        palette.setColor(QPalette.ColorRole.Base, QColor(25, 25, 25))
        palette.setColor(QPalette.ColorRole.AlternateBase, mid_dark)
        palette.setColor(QPalette.ColorRole.ToolTipBase, mid_dark)
        palette.setColor(QPalette.ColorRole.ToolTipText, text)
        palette.setColor(QPalette.ColorRole.Text, text)
        palette.setColor(QPalette.ColorRole.Button, mid_dark)
        palette.setColor(QPalette.ColorRole.ButtonText, text)
        palette.setColor(QPalette.ColorRole.BrightText, QColor(255, 80, 80))
        palette.setColor(QPalette.ColorRole.Link, highlight)
        palette.setColor(QPalette.ColorRole.Highlight, highlight)
        palette.setColor(QPalette.ColorRole.HighlightedText, QColor(255, 255, 255))
        palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, disabled_text)
        palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, disabled_text)
        app.setPalette(palette)
        app.setStyleSheet(_build_dark_qss(font_pt))


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("MortalManager")
    app.setOrganizationName("MortalManager")
    app.setApplicationVersion("1.0.0")

    # Force mnemonic underlines to always be visible (Win10/11 hides them by default)
    class _UnderlineStyle(QProxyStyle):
        def styleHint(self, hint, option=None, widget=None, returnData=None):
            if hint == QStyle.StyleHint.SH_UnderlineShortcut:
                return 1
            return super().styleHint(hint, option, widget, returnData)
    app.setStyle(_UnderlineStyle())

    from src.settings.config import ConfigManager
    theme = ConfigManager.get_instance().config.theme
    apply_theme(app, theme)

    # Set up asyncio event loop that integrates with Qt
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

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
