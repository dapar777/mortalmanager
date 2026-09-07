"""Drawn icons (inline SVG strokes) coloured by the theme and scaled by zoom.

Instead of emoji: they scale, share one style (2.2 px stroke on a 24×24 grid,
round caps) and take the text colour.

    icons.icon("flag")                  -> QIcon (colour = theme text2)
    icons.pixmap("flag", 14, t.accent)  -> QPixmap in a given colour
    icons.png_file("flag", 12, fg)      -> PNG path for <img> in rich text
    icons.register("git", '<path .../>')  -> custom icon

Sizes are unzoomed logical pixels; theme.px() is applied inside. After a
theme or zoom change call icons.clear_cache() (widgets.retheme_tree does).
"""

from __future__ import annotations

from PySide6.QtCore import QByteArray, QRectF, QSize, Qt
from PySide6.QtGui import QIcon, QImage, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication

from . import theme

# name -> (<svg> body, filled?)
_PATHS: dict[str, tuple[str, bool]] = {
    # navigation and actions
    "search": ('<circle cx="11" cy="11" r="7"/><path d="M20 20l-3.5-3.5"/>', False),
    "plus": ('<path d="M12 5v14M5 12h14"/>', False),
    "minus": ('<path d="M5 12h14"/>', False),
    "close": ('<path d="M18 6L6 18M6 6l12 12"/>', False),
    "check": ('<path d="M5 12l5 5L20 7"/>', False),
    "menu": ('<path d="M4 7h16M4 12h16M4 17h16"/>', False),
    "more": ('<circle cx="5" cy="12" r="1.6" fill="currentColor"/><circle cx="12" cy="12" r="1.6" fill="currentColor"/>'
             '<circle cx="19" cy="12" r="1.6" fill="currentColor"/>', False),
    "chevron_down": ('<path d="M6 9l6 6 6-6"/>', False),
    "chevron_up": ('<path d="M6 15l6-6 6 6"/>', False),
    "chevron_right": ('<path d="M9 6l6 6-6 6"/>', False),
    "chevron_left": ('<path d="M15 6l-6 6 6 6"/>', False),
    "arrow_right": ('<path d="M5 12h14M13 6l6 6-6 6"/>', False),
    "arrow_left": ('<path d="M19 12H5M11 6l-6 6 6 6"/>', False),
    "arrow_up": ('<path d="M12 19V5M6 11l6-6 6 6"/>', False),
    "reply": ('<path d="M4 6v6a4 4 0 004 4h11M15 12l4 4-4 4"/>', False),
    "rotate": ('<path d="M4 12a8 8 0 0114-5.3L21 9M21 4v5h-5"/>', False),
    "undo": ('<path d="M9 14L4 9l5-5"/><path d="M4 9h11a5 5 0 010 10h-2"/>', False),
    "redo": ('<path d="M15 14l5-5-5-5"/><path d="M20 9H9a5 5 0 000 10h2"/>', False),
    "new_window": ('<rect x="3" y="5" width="18" height="14" rx="2"/><path d="M12 9v6M9 12h6"/>', False),
    "external": ('<path d="M21 12v7a2 2 0 01-2 2H5a2 2 0 01-2-2V5a2 2 0 012-2h7"/><path d="M15 3h6v6M10 14L21 3"/>', False),
    "download": ('<path d="M12 3v12M6 11l6 6 6-6M4 21h16"/>', False),
    "upload": ('<path d="M12 21V9M6 13l6-6 6 6M4 3h16"/>', False),
    "edit": ('<path d="M12 20h9M16.5 3.5a2.1 2.1 0 013 3L7 19l-4 1 1-4z"/>', False),
    "trash": ('<path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/>', False),
    "copy": ('<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V5a2 2 0 012-2h10"/>', False),
    "scissors": ('<circle cx="6" cy="6" r="3"/><circle cx="6" cy="18" r="3"/><path d="M20 4L8.5 15.5M8.5 8.5L20 20"/>', False),
    "clipboard": ('<rect x="8" y="3" width="8" height="4" rx="1"/><path d="M16 5h2a2 2 0 012 2v12a2 2 0 01-2 2H6a2 2 0 01-2-2V7a2 2 0 012-2h2"/>', False),
    "save": ('<path d="M19 21H5a2 2 0 01-2-2V5a2 2 0 012-2h11l5 5v11a2 2 0 01-2 2z"/><path d="M17 21v-8H7v8M7 3v5h8"/>', False),
    "filter": ('<path d="M3 5h18l-7 8v6l-4 2v-8z"/>', False),
    "sort": ('<path d="M4 6h16M7 12h10M10 18h4"/>', False),
    "sliders": ('<path d="M4 21v-7M4 10V3M12 21v-9M12 8V3M20 21v-5M20 12V3M1 14h6M9 8h6M17 16h6"/>', False),
    "settings": ('<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 00.3 1.8l.1.1a2 2 0 01-2.8 2.8l-.1-.1a1.7 1.7 0 00-1.8-.3 1.7 1.7 0 00-1 1.5V21a2 2 0 01-4 0v-.1a1.7 1.7 0 00-1.1-1.5 1.7 1.7 0 00-1.8.3l-.1.1a2 2 0 01-2.8-2.8l.1-.1a1.7 1.7 0 00.3-1.8 1.7 1.7 0 00-1.5-1H3a2 2 0 010-4h.1a1.7 1.7 0 001.5-1.1 1.7 1.7 0 00-.3-1.8l-.1-.1a2 2 0 012.8-2.8l.1.1a1.7 1.7 0 001.8.3H9a1.7 1.7 0 001-1.5V3a2 2 0 014 0v.1a1.7 1.7 0 001 1.5 1.7 1.7 0 001.8-.3l.1-.1a2 2 0 012.8 2.8l-.1.1a1.7 1.7 0 00-.3 1.8V9a1.7 1.7 0 001.5 1H21a2 2 0 010 4h-.1a1.7 1.7 0 00-1.5 1z"/>', False),
    "refresh": ('<path d="M21 12a9 9 0 01-15.5 6.3L3 16M3 12a9 9 0 0115.5-6.3L21 8M3 21v-5h5M21 3v5h-5"/>', False),
    "command": ('<path d="M4 17l6-5-6-5M12 19h8"/>', False),
    "keyboard": ('<rect x="2" y="6" width="20" height="12" rx="2"/><path d="M6 10h.01M10 10h.01M14 10h.01M18 10h.01M8 14h8"/>', False),
    "zoom_in": ('<circle cx="11" cy="11" r="7"/><path d="M20 20l-3.5-3.5M11 8v6M8 11h6"/>', False),
    "zoom_out": ('<circle cx="11" cy="11" r="7"/><path d="M20 20l-3.5-3.5M8 11h6"/>', False),
    # views
    "tree": ('<path d="M5 5h6M9 12h10M13 19h6"/><circle cx="5" cy="12" r="1.2" fill="currentColor"/>'
             '<circle cx="9" cy="19" r="1.2" fill="currentColor"/>', False),
    "list": ('<path d="M5 6h14M5 12h14M5 18h14"/>', False),
    "cards": ('<rect x="4" y="4" width="16" height="7" rx="2"/><rect x="4" y="14" width="16" height="6" rx="2"/>', False),
    "grid": ('<rect x="4" y="4" width="6" height="6" rx="1.5"/><rect x="14" y="4" width="6" height="6" rx="1.5"/>'
             '<rect x="4" y="14" width="6" height="6" rx="1.5"/><rect x="14" y="14" width="6" height="6" rx="1.5"/>', False),
    "table": ('<rect x="3" y="5" width="18" height="14" rx="2"/><path d="M3 10h18M9 10v9M15 10v9"/>', False),
    "columns": ('<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M12 4v16"/>', False),
    "chart": ('<path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/>', False),
    "calendar": ('<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18M8 3v4M16 3v4"/>', False),
    "sun": ('<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>', False),
    "moon": ('<path d="M21 12.8A9 9 0 1111.2 3a7 7 0 009.8 9.8z"/>', False),
    "eye": ('<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>', False),
    "eye_off": ('<path d="M3 3l18 18M10.6 5.2A10 10 0 0112 5c6.5 0 10 7 10 7a17 17 0 01-3.2 4M6.6 6.6C3.6 8.6 2 12 2 12s3.5 7 10 7a9.8 9.8 0 004.3-1"/>', False),
    # objects
    "flag": ('<path d="M5 4v17M5 4h11l-2 4 2 4H5"/>', True),
    "flag_outline": ('<path d="M5 4v17M5 4h11l-2 4 2 4H5"/>', False),
    "star": ('<path d="M12 3l2.8 5.9 6.4.9-4.6 4.5 1.1 6.4L12 17.7l-5.7 3 1.1-6.4L2.8 9.8l6.4-.9z"/>', True),
    "star_outline": ('<path d="M12 3l2.8 5.9 6.4.9-4.6 4.5 1.1 6.4L12 17.7l-5.7 3 1.1-6.4L2.8 9.8l6.4-.9z"/>', False),
    "bookmark": ('<path d="M19 21l-7-4-7 4V5a2 2 0 012-2h10a2 2 0 012 2z"/>', False),
    "clock": ('<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>', False),
    "ban": ('<circle cx="12" cy="12" r="9"/><path d="M5.6 5.6l12.8 12.8"/>', False),
    "link": ('<path d="M10 13a5 5 0 007 0l3-3a5 5 0 00-7-7l-1 1"/><path d="M14 11a5 5 0 00-7 0l-3 3a5 5 0 007 7l1-1"/>', False),
    "file": ('<path d="M6 3h8l5 5v13H6z"/><path d="M14 3v5h5"/>', False),
    "doc": ('<path d="M6 3h8l5 5v13H6z"/><path d="M14 3v5h5M9 13h6M9 17h6"/>', False),
    "folder": ('<path d="M3 7a2 2 0 012-2h4l2 2h8a2 2 0 012 2v9a2 2 0 01-2 2H5a2 2 0 01-2-2z"/>', False),
    "folder_plus": ('<path d="M3 7a2 2 0 012-2h4l2 2h8a2 2 0 012 2v9a2 2 0 01-2 2H5a2 2 0 01-2-2z"/><path d="M12 10v6M9 13h6"/>', False),
    "folder_up": ('<path d="M3 7a2 2 0 012-2h4l2 2h8a2 2 0 012 2v9a2 2 0 01-2 2H5a2 2 0 01-2-2z"/><path d="M12 17v-6M9 14l3-3 3 3"/>', False),
    "home": ('<path d="M4 11l8-7 8 7v9a1 1 0 01-1 1h-5v-6h-4v6H5a1 1 0 01-1-1z"/>', False),
    "subtasks": ('<path d="M6 4v8a3 3 0 003 3h9M14 11l4 4-4 4"/>', False),
    "hash": ('<path d="M5 9h14M5 15h14M10 3L8 21M16 3l-2 18"/>', False),
    "tag": ('<path d="M3 12V4h8l10 10-8 8z"/><circle cx="7.5" cy="8.5" r="1.3" fill="currentColor"/>', False),
    "text": ('<path d="M4 6h16M4 12h10M4 18h14"/>', False),
    "user": ('<circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0116 0"/>', False),
    "users": ('<circle cx="9" cy="8" r="3.5"/><path d="M2 20a7 7 0 0114 0M16 4.5a3.5 3.5 0 010 7M22 20a7 7 0 00-5-6.7"/>', False),
    "mail": ('<rect x="3" y="5" width="18" height="14" rx="2"/><path d="M3 7l9 6 9-6"/>', False),
    "bell": ('<path d="M6 16V11a6 6 0 0112 0v5l2 2H4zM10 20a2 2 0 004 0"/>', False),
    "lock": ('<rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V7a4 4 0 018 0v4"/>', False),
    "shield": ('<path d="M12 3l8 3v6c0 4.5-3.4 7.8-8 9-4.6-1.2-8-4.5-8-9V6z"/>', False),
    "pin": ('<path d="M12 21v-6M8 15h8l-1-4V5h1V3H8v2h1v6z"/>', False),
    "dot": ('<circle cx="12" cy="12" r="6"/>', True),
    "terminal": ('<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M7 9l3 3-3 3M12 15h5"/>', False),
    "drive": ('<rect x="3" y="5" width="18" height="14" rx="2"/><path d="M3 13h18"/><circle cx="7" cy="16" r="1" fill="currentColor"/>', False),
    "usb": ('<path d="M12 21V6M12 6l-2 2M12 6l2 2M8 13l4 3 4-3M12 16v1"/><circle cx="12" cy="4" r="1.2" fill="currentColor"/><rect x="6" y="10" width="3" height="3"/><circle cx="16" cy="11.5" r="1.5"/>', False),
    "disc": ('<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="2.5"/>', False),
    "network": ('<rect x="9" y="3" width="6" height="5" rx="1"/><rect x="3" y="16" width="6" height="5" rx="1"/><rect x="15" y="16" width="6" height="5" rx="1"/><path d="M12 8v4M6 16v-4h12v4"/>', False),
    "archive": ('<rect x="3" y="4" width="18" height="5" rx="1"/><path d="M5 9v10a1 1 0 001 1h12a1 1 0 001-1V9M10 13h4"/>', False),
    "explorer": ('<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 9h18M8 4v5"/>', False),
    "rename": ('<path d="M4 7h4M4 17h4M6 7v10M11 5h9a1 1 0 011 1v12a1 1 0 01-1 1h-9"/>', False),
    "scale": ('<path d="M6 3v18M6 3H4M6 3h2M6 21H4M6 21h2M12 3v18M12 3h-2M12 3h2M12 21h-2M12 21h2M18 3v18M18 3h-2M18 3h2M18 21h-2M18 21h2"/>', False),
    # states
    "info": ('<circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8h.01"/>', False),
    "warning": ('<path d="M12 9v4M12 17h.01M10.3 3.9L2.6 17a2 2 0 001.7 3h15.4a2 2 0 001.7-3L13.7 3.9a2 2 0 00-3.4 0z"/>', False),
    "error": ('<circle cx="12" cy="12" r="9"/><path d="M15 9l-6 6M9 9l6 6"/>', False),
    "success": ('<circle cx="12" cy="12" r="9"/><path d="M8 12l3 3 5-6"/>', False),
    "help": ('<circle cx="12" cy="12" r="9"/><path d="M9.5 9.5a2.5 2.5 0 015 0c0 1.7-2.5 2-2.5 3.5M12 17h.01"/>', False),
}

_cache: dict[tuple, QPixmap] = {}


def names() -> list[str]:
    return sorted(_PATHS)


def register(name: str, body: str, filled: bool = False) -> None:
    """Add a custom icon: body = <svg> content on a 24×24 grid (stroke, no colour)."""
    _PATHS[name] = (body, filled)


def svg(name: str, color: str, stroke_width: float = 2.2) -> str:
    body, filled = _PATHS[name]
    fill = color if filled else "none"
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="{fill}" '
        f'stroke="{color}" stroke-width="{stroke_width}" stroke-linecap="round" '
        f'stroke-linejoin="round">{body.replace("currentColor", color)}</svg>'
    )


def logical_size(size: int = theme.ICON_SIZE) -> int:
    """Icon size in logical pixels after zoom (for setIconSize)."""
    return theme.px(size)


def qsize(size: int = theme.ICON_SIZE) -> QSize:
    s = logical_size(size)
    return QSize(s, s)


def pixmap(name: str, size: int = theme.ICON_SIZE, color: str | None = None) -> QPixmap:
    """Pixmap of the icon; ``size`` is unzoomed, zoom and DPR applied here."""
    color = color or theme.current().text2
    app = QApplication.instance()
    dpr = app.devicePixelRatio() if app else 1.0
    logical = theme.px(size)
    key = (name, logical, color, dpr)
    pm = _cache.get(key)
    if pm is not None:
        return pm
    px = int(round(logical * dpr))
    img = QImage(px, px, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    QSvgRenderer(QByteArray(svg(name, color).encode("utf-8"))).render(p, QRectF(0, 0, px, px))
    p.end()
    pm = QPixmap.fromImage(img)
    pm.setDevicePixelRatio(dpr)
    _cache[key] = pm
    return pm


def icon(name: str, size: int = theme.ICON_SIZE, color: str | None = None) -> QIcon:
    return QIcon(pixmap(name, size, color))


def semantic_icon(kind: str, size: int = theme.ICON_SIZE) -> QIcon:
    """Icon of a semantic kind in its colour (info/warning/danger/success)."""
    name = {"info": "info", "warning": "warning", "danger": "error", "success": "success"}.get(kind, "info")
    return icon(name, size, theme.current().semantic_dot.get(kind))


_files: dict[tuple, str] = {}


def png_file(name: str, size: int = 12, color: str | None = None) -> str:
    """Path to a PNG of the icon – for <img> in rich text (QLabel), where QIcon can't be used."""
    color = color or theme.current().text2
    logical = theme.px(size)
    key = (name, logical, color)
    path = _files.get(key)
    if path:
        return path
    p = theme.asset_dir() / f"{name}-{logical}-{color.lstrip('#')}.png"
    if not p.exists():
        pixmap(name, size, color).save(p.as_posix(), "PNG")
    _files[key] = p.as_posix()
    return _files[key]


def clear_cache() -> None:
    """After a theme / zoom change – icons are re-rendered in new colours and sizes."""
    _cache.clear()
