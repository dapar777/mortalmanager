"""solarqt – Solarized look for PySide6: tokens, QSS, drawn icons, widgets.

Vendored from c:/code/solarqt (MANUAL.md there is the design reference) and
extended with a global zoom factor (theme.set_zoom / theme.px / theme.pt) so
the whole UI – fonts, paddings, row heights, icons – scales together.

    from src.solarqt import theme, icons, widgets
    theme.apply(app, "light", zoom=1.2)
"""

from . import icons, theme, widgets  # noqa: F401

__all__ = ["theme", "icons", "widgets"]
__version__ = "0.1.0+mm"
