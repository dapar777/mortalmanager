"""Visual tokens and theme (Solarized, light / dark) for PySide6.

The ONLY place where colours, fonts and QSS are defined. Other modules ask via
functions (current(), semantic_style(), mono_font(), px(), pt()…) and never
hard-code a hex value – tests/test_theme.py enforces that with a regex.

Zoom: every size in the QSS goes through px() / pt(), so ``apply(app, name,
zoom=1.3)`` re-generates a stylesheet where fonts, paddings, control heights
and indicator sizes are all 30 % larger. Widgets that hold sizes outside QSS
(row height, icon size, explicit fonts) read px()/pt() in their retheme().
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass, replace
from pathlib import Path

from PySide6.QtCore import QStandardPaths
from PySide6.QtGui import QColor, QFont, QPalette

# ----------------------------------------------------------------------------
# Solarized base (Ethan Schoonover) – 8 background/text tones + 8 accents
# ----------------------------------------------------------------------------
BASE03, BASE02, BASE01, BASE00 = "#002b36", "#073642", "#586e75", "#657b83"
BASE0, BASE1, BASE2, BASE3 = "#839496", "#93a1a1", "#eee8d5", "#fdf6e3"
YELLOW, ORANGE, RED, MAGENTA = "#b58900", "#cb4b16", "#dc322f", "#d33682"
VIOLET, BLUE, CYAN, GREEN = "#6c71c4", "#268bd2", "#2aa198", "#859900"

# semantic kinds – everything that carries meaning maps onto one of these
KINDS = ("neutral", "info", "warning", "danger", "success", "accent")


def mix(a: str, b: str, t: float) -> str:
    """Blend colour a (share t) with colour b (1 - t); returns hex."""
    ca, cb = QColor(a), QColor(b)
    r = ca.red() * t + cb.red() * (1 - t)
    g = ca.green() * t + cb.green() * (1 - t)
    bl = ca.blue() * t + cb.blue() * (1 - t)
    return "#%02x%02x%02x" % (round(r), round(g), round(bl))


def alpha(hexv: str, a: float) -> str:
    """Colour with transparency for QSS: rgba(r,g,b,a)."""
    c = QColor(hexv)
    return f"rgba({c.red()},{c.green()},{c.blue()},{a:.2f})"


@dataclass(frozen=True)
class Tokens:
    name: str
    # surfaces
    canvas: str      # window / dialog / content background
    panel: str       # side panel, bars, menu bar, status bar
    paper: str       # fields, editor, popups (lightest surface)
    card: str        # cards and lists (creamy, not white)
    cards_bg: str    # ground under cards (one step darker than card)
    # lines
    line: str        # subtle divider
    border: str      # field / button border
    dashed: str      # dashed border (empty state, drop zone)
    # text
    text: str
    text2: str       # secondary text
    muted: str       # captions, placeholder, section headings
    faint: str       # weakest text (hex, ids in monospace)
    # accent and interaction
    accent: str
    accent_fg: str
    accent_hover: str
    hover: str       # hover background
    selection: str   # selected row background in lists
    tint: float      # strength of chip / banner tinting (0–1)
    # semantics
    semantic_dot: dict   # kind -> saturated colour (dot, stripe, icon)
    semantic_fg: dict    # kind -> readable text on the tint
    badge_fg: str
    badge_border: str
    done_text: str       # struck-through finished row
    shadow: str          # rgba shadow for toast / popover


LIGHT = Tokens(
    name="light",
    canvas=BASE3, panel=BASE2, paper="#fffdf6", card=BASE3, cards_bg="#f3ecd6",
    line="#e6dfc8", border="#d9d2bb", dashed="#c9c1a6",
    text=BASE02, text2=BASE01, muted=BASE1, faint=BASE0,
    accent=ORANGE, accent_fg=BASE3, accent_hover="#b3410f",
    hover="#f5eedb", selection="#f6e3d6", tint=0.14,
    semantic_dot={
        "neutral": BASE01, "info": BLUE, "warning": YELLOW,
        "danger": RED, "success": GREEN, "accent": ORANGE,
    },
    semantic_fg={
        "neutral": BASE01, "info": "#1c6fa8", "warning": "#8a6800",
        "danger": "#b8221f", "success": "#667500", "accent": "#a63c10",
    },
    badge_fg="#8a5a00", badge_border="#e0b060",
    done_text=BASE1,
    shadow="rgba(0,43,54,0.18)",
)

DARK = Tokens(
    name="dark",
    canvas=BASE03, panel=BASE02, paper="#0b3a47", card="#0b3a47", cards_bg=BASE03,
    line="#12505f", border="#12505f", dashed="#1f5f6e",
    text=BASE2, text2=BASE1, muted=BASE00, faint=BASE01,
    accent=ORANGE, accent_fg=BASE3, accent_hover="#e0602a",
    hover="#0d4352", selection="#1b4a58", tint=0.25,
    semantic_dot={
        "neutral": BASE1, "info": BLUE, "warning": YELLOW,
        "danger": RED, "success": GREEN, "accent": ORANGE,
    },
    semantic_fg={
        "neutral": BASE1, "info": "#6cb6ea", "warning": "#e0b23a",
        "danger": "#f0645f", "success": "#b5c94a", "accent": "#f0a07a",
    },
    badge_fg="#e0b23a", badge_border="#8a6800",
    done_text=BASE00,
    shadow="rgba(0,0,0,0.45)",
)

THEMES = {"light": LIGHT, "dark": DARK}
_current = LIGHT
_style_set = False

# application states -> semantic kind (file manager: VCS states)
STATUS_KINDS: dict[str, str] = {
    "clean": "neutral", "modified": "warning", "untracked": "info",
    "added": "success", "deleted": "danger", "conflict": "danger", "ignored": "neutral",
}

# directory name for generated SVG/PNG assets (in the user's temp directory)
ASSET_DIR_NAME = "ultimatecommander-theme"


def current() -> Tokens:
    return _current


def is_dark() -> bool:
    return _current.name == "dark"


def register_theme(name: str, tokens: Tokens) -> None:
    """Register a custom theme (e.g. derived: replace(LIGHT, accent=BLUE))."""
    THEMES[name] = replace(tokens, name=name)


def configure_statuses(mapping: dict[str, str]) -> None:
    """Map application states onto semantic kinds."""
    bad = [k for k in mapping.values() if k not in KINDS]
    if bad:
        raise ValueError(f"unknown kind: {bad}; allowed: {KINDS}")
    STATUS_KINDS.clear()
    STATUS_KINDS.update(mapping)


# ----------------------------------------------------------------------------
# Zoom – one factor for the whole UI
# ----------------------------------------------------------------------------
ZOOM_MIN, ZOOM_MAX, ZOOM_STEP = 0.7, 2.0, 0.1
_zoom = 1.0


def zoom() -> float:
    return _zoom


def set_zoom(factor: float) -> float:
    """Clamp and store the zoom factor. Call apply() afterwards."""
    global _zoom
    _zoom = max(ZOOM_MIN, min(ZOOM_MAX, round(factor, 2)))
    return _zoom


def px(n: float) -> int:
    """Logical pixels scaled by zoom (never below 1 for non-zero input)."""
    if not n:
        return 0
    return max(1, int(round(n * _zoom)))


def pt(n: float) -> float:
    """Point size scaled by zoom (one decimal)."""
    return round(n * _zoom, 1)


# ----------------------------------------------------------------------------
# Semantic styles
# ----------------------------------------------------------------------------
def semantic_style(kind: str) -> tuple[str, str, str]:
    """(text, background, dot) of a chip for a semantic kind."""
    return semantic_style_for(_current, kind)


def semantic_style_for(t: Tokens, kind: str) -> tuple[str, str, str]:
    dot = t.semantic_dot.get(kind, t.semantic_dot["neutral"])
    fg = t.semantic_fg.get(kind, t.semantic_fg["neutral"])
    return fg, mix(dot, t.paper, t.tint), dot


def status_style(status: str) -> tuple[str, str, str]:
    """(text, background, dot) of a chip for an application state."""
    return semantic_style(STATUS_KINDS.get(status, "neutral"))


def chip_qss(fg: str, bg: str, radius: int | None = None, padding: str | None = None) -> str:
    radius = px(RADIUS_SM) if radius is None else radius
    padding = padding or f"{px(1)}px {px(7)}px"
    return (
        f"QLabel{{background:{bg}; color:{fg}; border-radius:{radius}px;"
        f" padding:{padding}; font-weight:600;}}"
    )


# ----------------------------------------------------------------------------
# Fonts
# ----------------------------------------------------------------------------
UI_FAMILIES = ["Segoe UI", "Noto Sans", "DejaVu Sans", "sans-serif"]
TITLE_FAMILIES = ["Cambria", "Georgia", "Noto Serif", "DejaVu Serif", "serif"]
MONO_FAMILIES = ["Cascadia Mono", "Consolas", "DejaVu Sans Mono", "monospace"]

# typographic scale (pt, before zoom)
SIZE_CAPTION = 8
SIZE_BODY = 10
SIZE_LEAD = 11.5
SIZE_H2 = 12.5
SIZE_H1 = 16
SIZE_DISPLAY = 22
SIZE_MONO = 9


def _font(families, size: float, weight=QFont.Weight.Normal) -> QFont:
    f = QFont()
    f.setFamilies(families)
    f.setPointSizeF(pt(size))
    f.setWeight(weight)
    return f


def ui_font(size: float = SIZE_BODY, weight=QFont.Weight.Normal) -> QFont:
    """UI font; size is in unzoomed points (zoom applied here)."""
    return _font(UI_FAMILIES, size, weight)


def title_font(size: float = 14, weight=QFont.Weight.DemiBold) -> QFont:
    return _font(TITLE_FAMILIES, size, weight)


def mono_font(size: float = SIZE_MONO) -> QFont:
    return _font(MONO_FAMILIES, size)


# ----------------------------------------------------------------------------
# Dimensions (unzoomed; go through px() when used)
# ----------------------------------------------------------------------------
RADIUS_SM = 6      # chip, menu item, tool button
RADIUS_MD = 8      # field, button, list
RADIUS_LG = 10     # card, menu, group box, dialog panel
SPACE_XS, SPACE_SM, SPACE_MD, SPACE_LG, SPACE_XL = 4, 8, 12, 16, 24
ROW_HEIGHT = 30        # tree / table row with chips (Task Master)
ROW_HEIGHT_DENSE = 24  # file list row (file manager: many rows on screen)
CONTROL_HEIGHT = 30
ICON_SIZE = 16

# narrow window thresholds (px); see MANUAL.md "Responzivita"
HEADER_COMPACT_BELOW = 1100
MIN_WINDOW_WIDTH = 350


# ----------------------------------------------------------------------------
# QSS
# ----------------------------------------------------------------------------
_CHECK_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
    'stroke="{c}" stroke-width="3.2" stroke-linecap="round" stroke-linejoin="round">'
    '<path d="M5 12l5 5L20 7"/></svg>'
)
_CHEVRON_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
    'stroke="{c}" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round">'
    '<path d="M6 9l6 6 6-6"/></svg>'
)
_CHEVRON_UP_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
    'stroke="{c}" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round">'
    '<path d="M6 15l6-6 6 6"/></svg>'
)
_CHEVRON_RIGHT_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
    'stroke="{c}" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round">'
    '<path d="M9 6l6 6-6 6"/></svg>'
)
_CLOSE_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
    'stroke="{c}" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round">'
    '<path d="M18 6L6 18M6 6l12 12"/></svg>'
)
_DOT_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
    '<circle cx="12" cy="12" r="5" fill="{c}"/></svg>'
)


def asset_dir() -> Path:
    base = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.TempLocation) or tempfile.gettempdir()
    d = Path(base) / ASSET_DIR_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


_asset_cache: dict[str, str] = {}


def _write_asset(name: str, content: str) -> str:
    """Write an SVG for QSS url() once per process; returns a posix path."""
    cached = _asset_cache.get(name)
    if cached is not None:
        return cached
    p = asset_dir() / name
    try:
        if not p.exists() or p.read_text(encoding="utf-8") != content:
            p.write_text(content, encoding="utf-8")
    except OSError:
        pass
    _asset_cache[name] = p.as_posix()
    return _asset_cache[name]


def build_qss(t: Tokens) -> str:
    """Complete QSS for the tokens at the current zoom. Selectors by
    objectName / property are documented in MANUAL.md (Components)."""
    check = _write_asset(f"check-{t.name}.svg", _CHECK_SVG.format(c=t.accent_fg))
    chevron = _write_asset(f"chevron-{t.name}.svg", _CHEVRON_SVG.format(c=t.text2))
    chevron_up = _write_asset(f"chevron-up-{t.name}.svg", _CHEVRON_UP_SVG.format(c=t.text2))
    chevron_right = _write_asset(f"chevron-right-{t.name}.svg", _CHEVRON_RIGHT_SVG.format(c=t.text2))
    close = _write_asset(f"close-{t.name}.svg", _CLOSE_SVG.format(c=t.text2))
    radio_dot = _write_asset(f"radio-{t.name}.svg", _DOT_SVG.format(c=t.accent_fg))
    sel_tint = mix(t.accent, t.paper, 0.10)
    mark_bg = mix(t.accent, t.card, t.tint)
    green = t.semantic_dot["success"]
    red = t.semantic_dot["danger"]
    red_fg = t.semantic_fg["danger"]
    r_sm, r_md, r_lg = px(RADIUS_SM), px(RADIUS_MD), px(RADIUS_LG)
    p = px
    f_caption, f_body, f_lead, f_h2, f_h1 = pt(SIZE_CAPTION), pt(SIZE_BODY), pt(SIZE_LEAD), pt(SIZE_H2), pt(SIZE_H1)
    f_small = pt(9)
    ind = p(16)          # checkbox / radio indicator
    arrow = p(12)        # combobox chevron
    arrow_sm = p(10)     # header / spinbox chevron
    sb = p(10)           # scrollbar thickness

    def banner(kind: str) -> str:
        fg, bg, dot = semantic_style_for(t, kind)
        return (
            f'QFrame#banner[kind="{kind}"] {{ background: {bg}; border: 1px solid {mix(dot, t.paper, 0.35)};'
            f' border-left: {p(4)}px solid {dot}; border-radius: {r_md}px; }}\n'
            f'QFrame#banner[kind="{kind}"] QLabel#bannerTitle {{ color: {fg}; font-weight: 700; }}\n'
        )

    banners = "".join(banner(k) for k in KINDS if k != "accent")

    return f"""
/* ---------- surfaces and text ---------- */
QMainWindow, QDialog, QMessageBox, QInputDialog, QFileDialog, QWizard {{ background: {t.canvas}; }}
QWidget {{ color: {t.text}; }}
QLabel {{ background: transparent; }}
QToolTip {{ background: {t.text}; color: {t.canvas}; border: 0; border-radius: {r_sm}px; padding: {p(6)}px {p(8)}px; }}

/* typography via objectName */
QLabel#h1 {{ font-size: {f_h1}pt; font-weight: 600; }}
QLabel#h2 {{ font-size: {f_h2}pt; font-weight: 600; }}
QLabel#lead {{ font-size: {f_lead}pt; color: {t.text2}; }}
QLabel#caption {{ font-size: {f_caption}pt; color: {t.muted}; }}
QLabel#sectionLabel {{ color: {t.muted}; font-size: {f_caption}pt; font-weight: 700; }}
QLabel#groupHeader {{ color: {t.muted}; font-size: {f_caption}pt; font-weight: 700; padding: {p(6)}px {p(4)}px 0 {p(4)}px; }}
QLabel#groupHeader[urgent="true"] {{ color: {red_fg}; }}
QLabel#pathLabel {{ color: {t.text2}; }}
QLabel#faintLabel {{ color: {t.muted}; }}
QLabel#hint {{ color: {t.text2}; }}
QLabel#kbd {{ color: {t.muted}; border: 1px solid {t.line}; border-radius: {p(4)}px; padding: 0 {p(4)}px; font-size: {f_caption}pt; }}
QLabel#link {{ color: {t.accent}; text-decoration: underline; }}
QFrame#hline {{ background: {t.line}; max-height: 1px; min-height: 1px; border: 0; }}
QFrame#vline {{ background: {t.line}; max-width: 1px; min-width: 1px; border: 0; }}

/* ---------- menus, bars ---------- */
QMenuBar {{ background: {t.panel}; border-bottom: 1px solid {t.line}; padding: {p(1)}px {p(6)}px; }}
QMenuBar::item {{ padding: {p(4)}px {p(8)}px; border-radius: {r_sm}px; background: transparent; }}
QMenuBar::item:selected {{ background: {t.hover}; }}
QMenu {{ background: {t.paper}; border: 1px solid {t.border}; border-radius: {r_lg}px; padding: {p(6)}px; }}
QMenu::item {{ padding: {p(6)}px {p(28)}px {p(6)}px {p(10)}px; border-radius: {r_sm}px; }}
QMenu::item:selected {{ background: {sel_tint}; color: {t.text}; }}
/* compact variants: used when a long (context) menu would not fit the screen */
QMenu[compact="true"] {{ padding: {p(3)}px; }}
QMenu[compact="true"]::item {{ padding: {p(2)}px {p(24)}px {p(2)}px {p(8)}px; }}
QMenu[compact="true"]::separator {{ margin: {p(2)}px {p(6)}px; }}
QMenu[compact="dense"] {{ padding: {p(2)}px; }}
QMenu[compact="dense"]::item {{ padding: 0 {p(20)}px 0 {p(6)}px; font-size: {f_small}pt; }}
QMenu[compact="dense"]::separator {{ margin: {p(1)}px {p(4)}px; }}
QMenu::item:disabled {{ color: {t.muted}; font-size: {f_caption}pt; font-weight: 700; }}
QMenu::separator {{ height: 1px; background: {t.line}; margin: {p(6)}px {p(8)}px; }}
QMenu::icon {{ padding-left: {p(6)}px; }}
QMenu::icon:checked {{ background: {sel_tint}; border: 1px solid {t.accent}; border-radius: {p(5)}px; }}
QMenu::indicator {{ width: {p(14)}px; height: {p(14)}px; left: {p(8)}px; }}
QMenu::indicator:non-exclusive:checked, QMenu::indicator:exclusive:checked {{ image: url("{check}"); background: {green}; border-radius: {p(4)}px; }}
QMenu::right-arrow {{ image: url("{chevron_right}"); width: {arrow}px; height: {arrow}px; right: {p(8)}px; }}

QStatusBar {{ background: {t.panel}; border-top: 1px solid {t.line}; color: {t.text2}; }}
QStatusBar::item {{ border: 0; }}
QStatusBar QLabel {{ color: {t.text2}; padding: 0 {p(6)}px; }}

QToolBar {{ background: transparent; border: 0; border-bottom: 1px solid {t.line}; spacing: {p(2)}px; padding: {p(3)}px {p(4)}px; }}
QToolBar QToolButton {{ font-weight: 600; min-width: {p(20)}px; }}
QToolBar::separator {{ width: 1px; background: {t.line}; margin: {p(6)}px {p(5)}px; }}

QSplitter::handle {{ background: {t.line}; }}
QSplitter::handle:horizontal {{ width: {p(3)}px; margin: 0 {p(1)}px; }}
QSplitter::handle:vertical {{ height: {p(3)}px; margin: {p(1)}px 0; }}
QSplitter::handle:hover {{ background: {t.border}; }}

/* ---------- named application frames ---------- */
QFrame#headerBar {{ background: {t.canvas}; border-bottom: 1px solid {t.line}; }}
QFrame#sidePanel {{ background: {t.panel}; }}
QFrame#chipBar {{ background: {t.canvas}; border-bottom: 1px solid {t.line}; }}
QFrame#filterHost {{ background: {t.panel}; border-bottom: 1px solid {t.line}; }}
QScrollArea#cardsArea, QWidget#cardsPage {{ background: {t.cards_bg}; }}

/* file manager: panel, its header / footer, file list, F-key bar, terminal */
QFrame#panel {{ background: {t.card}; border: {p(1)}px solid {t.line}; border-radius: {r_md}px; }}
QFrame#panel[active="true"] {{ border: {p(2)}px solid {t.accent}; }}
QFrame#panelHeader {{ background: transparent; border: 0; }}
QFrame#panelFooter {{ background: transparent; border: 0; border-top: 1px solid {t.line}; }}
QFrame#panel QTabBar {{ background: transparent; }}
QTableView#fileTable {{ background: {t.card}; border: 0; border-radius: 0; padding: 0; outline: 0; }}
QTableView#fileTable::item {{ padding: 0 {p(6)}px; border: 0; }}
QTableView#fileTable::item:hover {{ background: {t.hover}; }}
QTableView#fileTable::item:selected {{ background: {t.selection}; color: {t.text}; }}
QFrame#panel[active="false"] QTableView#fileTable::item:selected {{ background: {mix(t.selection, t.card, 0.55)}; }}
QTableView#fileTable QHeaderView::section {{ background: {t.card}; }}
QFrame#fkeysBar {{ background: {t.panel}; border-top: 1px solid {t.line}; }}
QFrame#fkeysBar QPushButton {{ padding: {p(2)}px {p(8)}px; min-height: {p(16)}px; font-size: {f_small}pt; }}
QFrame#terminalPane {{ background: {t.panel}; border: 0; }}
QPlainTextEdit#terminalOutput {{ border: 0; border-radius: 0; background: {t.paper}; }}
QLabel#terminalPrompt {{ color: {t.text2}; }}
QLabel#markBg {{ background: {mark_bg}; }}

/* segmented switch */
QFrame#segment {{ background: {t.panel}; border: 1px solid {t.border}; border-radius: {r_md}px; }}
QToolButton#segmentBtn {{ border: 0; border-radius: {r_sm}px; padding: {p(3)}px {p(10)}px; font-weight: 600; color: {t.text2}; background: transparent; }}
QToolButton#segmentBtn:hover {{ color: {t.text}; }}
QToolButton#segmentBtn:checked {{ background: {t.paper}; color: {t.text}; }}

/* card */
QFrame#card {{ background: {t.card}; border: 1px solid {t.line}; border-radius: {r_lg}px; }}
QFrame#card:hover {{ border-color: {t.border}; }}
QFrame#card[selected="true"] {{ border: {p(2)}px solid {t.accent}; }}

/* stat tile, empty state, banner, toast */
QFrame#tile {{ background: {t.card}; border: 1px solid {t.line}; border-radius: {r_lg}px; }}
QFrame#tile QLabel#tileValue {{ font-size: {pt(SIZE_DISPLAY)}pt; }}
QFrame#emptyState {{ background: transparent; border: 1px dashed {t.dashed}; border-radius: {r_lg}px; }}
QFrame#emptyState QLabel#h2 {{ color: {t.text2}; }}
QFrame#banner {{ background: {t.panel}; border: 1px solid {t.line}; border-radius: {r_md}px; }}
QFrame#banner QLabel {{ background: transparent; }}
{banners}
QFrame#toast {{ background: {t.text}; border-radius: {r_lg}px; }}
QFrame#toast QLabel {{ color: {t.canvas}; background: transparent; }}
QFrame#toast QToolButton {{ color: {t.canvas}; }}
QFrame#popover {{ background: {t.paper}; border: 1px solid {t.border}; border-radius: {r_lg}px; }}
QFrame#dropZone {{ background: transparent; border: {p(2)}px dashed {t.dashed}; border-radius: {r_lg}px; }}
QFrame#dropZone[active="true"] {{ border-color: {t.accent}; background: {sel_tint}; }}

/* command palette: denser rows than a generic list */
QListWidget#paletteList::item {{ padding: {p(1)}px {p(6)}px; }}

/* side navigation */
QListWidget#nav {{ background: transparent; border: 0; padding: {p(4)}px; }}
QListWidget#nav::item {{ padding: {p(6)}px {p(10)}px; border-radius: {r_sm}px; color: {t.text2}; }}
QListWidget#nav::item:hover {{ background: {t.hover}; color: {t.text}; }}
QListWidget#nav::item:selected {{ background: {t.paper}; color: {t.text}; border-left: {p(3)}px solid {t.accent}; }}

/* ---------- lists, trees, tables ---------- */
QTreeWidget, QTreeView, QListWidget, QListView, QTableView, QTableWidget, QColumnView {{
  background: {t.card}; border: 1px solid {t.line}; border-radius: {r_md}px; outline: 0;
  alternate-background-color: {t.card}; show-decoration-selected: 1; padding: {p(2)}px;
}}
QTableView, QTableWidget {{ gridline-color: {t.line}; alternate-background-color: {mix(t.panel, t.card, 0.5)}; }}
QTreeWidget::item, QListWidget::item, QTreeView::item, QListView::item {{ padding: {p(3)}px {p(4)}px; border-radius: {r_sm}px; }}
QTableView::item, QTableWidget::item {{ padding: {p(3)}px {p(6)}px; border: 0; }}
QTreeWidget::item:hover, QListWidget::item:hover, QTreeView::item:hover, QListView::item:hover, QTableView::item:hover {{ background: {t.hover}; }}
QTreeWidget::item:selected, QListWidget::item:selected, QTreeView::item:selected, QListView::item:selected, QTableView::item:selected {{ background: {t.selection}; color: {t.text}; }}
QTreeWidget::branch {{ background: transparent; }}
QTreeWidget::branch:selected {{ background: {t.selection}; }}
QTreeWidget::branch:hover {{ background: {t.hover}; }}
QHeaderView {{ background: transparent; }}
QHeaderView::section {{
  background: transparent; color: {t.muted}; font-size: {f_caption}pt; font-weight: 700;
  border: 0; border-bottom: 1px solid {t.line}; padding: {p(5)}px {p(6)}px;
}}
QHeaderView::section:hover {{ color: {t.text2}; }}
QHeaderView::section:vertical {{ border-bottom: 0; border-right: 1px solid {t.line}; }}
QHeaderView::down-arrow {{ image: url("{chevron}"); width: {arrow_sm}px; height: {arrow_sm}px; subcontrol-position: center right; right: {p(4)}px; }}
QHeaderView::up-arrow {{ image: url("{chevron_up}"); width: {arrow_sm}px; height: {arrow_sm}px; subcontrol-position: center right; right: {p(4)}px; }}
QTableCornerButton::section {{ background: transparent; border: 0; border-bottom: 1px solid {t.line}; }}

/* ---------- check boxes and radios ---------- */
QTreeWidget::indicator, QCheckBox::indicator, QListWidget::indicator, QTableView::indicator, QGroupBox::indicator {{
  width: {ind}px; height: {ind}px; border: 1.5px solid {t.muted}; border-radius: {p(5)}px; background: {t.paper};
}}
QTreeWidget::indicator:hover, QCheckBox::indicator:hover {{ border-color: {t.text2}; }}
QTreeWidget::indicator:checked, QCheckBox::indicator:checked, QListWidget::indicator:checked, QTableView::indicator:checked, QGroupBox::indicator:checked {{
  background: {green}; border-color: {green}; image: url("{check}");
}}
QCheckBox::indicator:indeterminate {{ background: {t.muted}; border-color: {t.muted}; }}
QCheckBox::indicator:disabled {{ border-color: {t.line}; background: {t.panel}; }}
QCheckBox {{ spacing: {p(8)}px; background: transparent; }}
QRadioButton {{ spacing: {p(8)}px; background: transparent; }}
QRadioButton::indicator {{ width: {ind}px; height: {ind}px; border: 1.5px solid {t.muted}; border-radius: {p(9)}px; background: {t.paper}; }}
QRadioButton::indicator:hover {{ border-color: {t.text2}; }}
QRadioButton::indicator:checked {{ background: {t.accent}; border-color: {t.accent}; image: url("{radio_dot}"); }}
QRadioButton::indicator:disabled {{ border-color: {t.line}; background: {t.panel}; }}

/* ---------- fields ---------- */
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QTextEdit, QPlainTextEdit, QTextBrowser, QKeySequenceEdit,
QDateEdit, QTimeEdit, QDateTimeEdit, QFontComboBox {{
  background: {t.paper}; border: 1px solid {t.border}; border-radius: {r_md}px; padding: {p(4)}px {p(8)}px;
  selection-background-color: {t.accent}; selection-color: {t.accent_fg};
}}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus, QTextEdit:focus, QPlainTextEdit:focus,
QKeySequenceEdit:focus, QDateEdit:focus, QTimeEdit:focus, QDateTimeEdit:focus {{
  border: 1.5px solid {t.accent};
}}
QLineEdit[invalid="true"], QSpinBox[invalid="true"], QComboBox[invalid="true"] {{ border: 1.5px solid {red}; }}
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QDateEdit:disabled, QTextEdit:disabled {{
  color: {t.muted}; background: {t.panel};
}}
QLineEdit:read-only {{ background: {t.panel}; }}
/* in-place rename in the file table: fits the row, keeps descenders visible */
QLineEdit#renameEditor {{ padding: 0 {p(4)}px; border: 1px solid {t.accent}; border-radius: 0; background: {t.card}; }}
QLineEdit#search {{ padding-left: {p(8)}px; border-radius: {p(14)}px; }}
QLineEdit#pathEdit {{ padding: {p(3)}px {p(8)}px; }}
QComboBox::drop-down {{ border: 0; width: {p(22)}px; subcontrol-origin: padding; subcontrol-position: center right; }}
QComboBox::down-arrow {{ image: url("{chevron}"); width: {arrow}px; height: {arrow}px; }}
QComboBox QAbstractItemView {{
  background: {t.paper}; color: {t.text}; border: 1px solid {t.border}; border-radius: {r_md}px; padding: {p(4)}px; outline: 0;
  selection-background-color: {t.hover}; selection-color: {t.text};
}}
QAbstractSpinBox::up-button, QAbstractSpinBox::down-button {{ width: {p(16)}px; border: 0; background: transparent; }}
QAbstractSpinBox::up-arrow {{ image: url("{chevron_up}"); width: {arrow_sm}px; height: {arrow_sm}px; }}
QAbstractSpinBox::down-arrow {{ image: url("{chevron}"); width: {arrow_sm}px; height: {arrow_sm}px; }}
QDateEdit::drop-down, QDateTimeEdit::drop-down {{ border: 0; width: {p(22)}px; subcontrol-origin: padding; subcontrol-position: center right; }}
QDateEdit::down-arrow, QDateTimeEdit::down-arrow {{ image: url("{chevron}"); width: {arrow}px; height: {arrow}px; }}
QCalendarWidget QWidget {{ alternate-background-color: {t.panel}; }}
QCalendarWidget QAbstractItemView {{ background: {t.paper}; selection-background-color: {t.accent}; selection-color: {t.accent_fg}; outline: 0; }}
QCalendarWidget QToolButton {{ color: {t.text}; font-weight: 600; }}
QCalendarWidget QWidget#qt_calendar_navigationbar {{ background: {t.panel}; border-bottom: 1px solid {t.line}; }}

/* ---------- buttons ---------- */
QPushButton {{
  background: {t.paper}; color: {t.text}; border: 1px solid {t.border}; border-radius: {r_md}px;
  padding: {p(5)}px {p(12)}px; font-weight: 500; min-height: {p(20)}px;
}}
QPushButton:hover {{ background: {t.hover}; border-color: {t.muted}; }}
QPushButton:pressed {{ background: {t.panel}; }}
QPushButton:disabled {{ color: {t.muted}; border-color: {t.line}; }}
QPushButton:default {{ border-color: {t.accent}; }}
QPushButton:checked {{ background: {t.panel}; border-color: {t.muted}; }}
QPushButton[primary="true"] {{ background: {t.accent}; color: {t.accent_fg}; border-color: {t.accent}; font-weight: 600; }}
QPushButton[primary="true"]:hover {{ background: {t.accent_hover}; border-color: {t.accent_hover}; }}
QPushButton[primary="true"]:disabled {{ background: {mix(t.accent, t.panel, 0.4)}; border-color: transparent; color: {t.canvas}; }}
QPushButton[danger="true"] {{ background: transparent; color: {red_fg}; border-color: {mix(red, t.paper, 0.5)}; }}
QPushButton[danger="true"]:hover {{ background: {red}; color: {t.accent_fg}; border-color: {red}; }}
QPushButton[quiet="true"] {{ background: transparent; border-color: transparent; color: {t.text2}; }}
QPushButton[quiet="true"]:hover {{ background: {t.hover}; color: {t.text}; }}
QPushButton[size="sm"] {{ padding: {p(2)}px {p(8)}px; min-height: {p(16)}px; font-size: {f_small}pt; }}
QPushButton::menu-indicator {{ image: url("{chevron}"); width: {arrow_sm}px; height: {arrow_sm}px; subcontrol-position: center right; right: {p(8)}px; }}

QToolButton {{ border: 1px solid transparent; border-radius: {r_sm}px; padding: {p(3)}px {p(6)}px; background: transparent; color: {t.text2}; }}
QToolButton:hover {{ background: {t.hover}; color: {t.text}; }}
QToolButton:pressed {{ background: {t.panel}; }}
QToolButton:checked {{ background: {t.panel}; color: {t.text}; border-color: {t.border}; }}
QToolButton:disabled {{ color: {t.muted}; }}
QToolButton[framed="true"] {{ border-color: {t.border}; background: {t.paper}; color: {t.text}; padding: {p(4)}px {p(10)}px; }}
QToolButton[framed="true"]:hover {{ background: {t.hover}; }}
QToolButton[framed="true"]:checked {{ background: {sel_tint}; border-color: {t.accent}; color: {t.text}; }}
QToolButton::menu-indicator {{ image: none; }}
QToolButton[popupMode="1"]::menu-button {{ border: 0; width: {p(14)}px; }}

/* ---------- containers ---------- */
QGroupBox {{
  border: 1px solid {t.line}; border-radius: {r_lg}px; margin-top: {p(10)}px; padding-top: {p(8)}px;
  font-weight: 600; color: {t.text2}; background: transparent;
}}
QGroupBox::title {{ subcontrol-origin: margin; left: {p(10)}px; padding: 0 {p(4)}px; color: {t.muted}; font-size: {f_caption}pt; font-weight: 700; }}

QTabWidget::pane {{ border: 1px solid {t.line}; border-radius: {r_md}px; top: -1px; background: {t.card}; }}
QTabBar {{ background: transparent; }}
/* path breadcrumb next to the panel tabs: flat segments, current one in full text colour */
QWidget#breadcrumb QPushButton#crumb, QWidget#breadcrumb QPushButton#crumbMore {{ border: 0; background: transparent; padding: {p(3)}px {p(5)}px; min-width: 0; color: {t.text2}; font-weight: 600; border-radius: {r_sm}px; }}
QWidget#breadcrumb QPushButton#crumb[current="true"] {{ color: {t.text}; }}
QWidget#breadcrumb QPushButton#crumb:hover, QWidget#breadcrumb QPushButton#crumbMore:hover {{ background: {t.hover}; color: {t.text}; }}
QWidget#breadcrumb QLabel#crumbSep {{ padding: 0; margin: 0 -{p(2)}px; }}
QTabBar::tab {{ padding: {p(5)}px {p(12)}px; margin-right: {p(2)}px; color: {t.text2}; border: 0; border-bottom: {p(2)}px solid transparent; background: transparent; font-weight: 600; }}
QTabBar::tab:hover {{ color: {t.text}; }}
QTabBar::tab:selected {{ color: {t.text}; border-bottom: {p(2)}px solid {t.accent}; }}
QTabBar::close-button {{ image: url("{close}"); width: {arrow_sm}px; height: {arrow_sm}px; subcontrol-position: right; margin: {p(2)}px; }}
QTabBar::close-button:hover {{ background: {t.hover}; border-radius: {p(4)}px; }}
QTabBar QToolButton {{ border: 0; background: {t.panel}; }}
QTabWidget[pill="true"] QTabBar::tab {{ border: 1px solid transparent; border-radius: {r_sm}px; padding: {p(4)}px {p(12)}px; }}
QTabWidget[pill="true"] QTabBar::tab:selected {{ background: {t.paper}; border-color: {t.border}; }}
QTabWidget[pill="true"]::pane {{ border: 0; background: transparent; }}

QToolBox::tab {{ background: {t.panel}; border: 1px solid {t.line}; border-radius: {r_sm}px; padding: {p(4)}px {p(8)}px; color: {t.text2}; font-weight: 600; }}
QToolBox::tab:selected {{ color: {t.text}; background: {t.paper}; }}
QDockWidget {{ titlebar-close-icon: none; titlebar-normal-icon: none; }}
QDockWidget::title {{ background: {t.panel}; padding: {p(6)}px {p(10)}px; border-bottom: 1px solid {t.line}; font-weight: 600; }}
QMdiArea {{ background: {t.cards_bg}; }}

/* ---------- scrollbars, sliders, progress ---------- */
QScrollBar:vertical {{ background: transparent; width: {sb}px; margin: {p(2)}px; }}
QScrollBar::handle:vertical {{ background: {t.border}; border-radius: {p(4)}px; min-height: {p(30)}px; }}
QScrollBar::handle:vertical:hover {{ background: {t.muted}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
QScrollBar:horizontal {{ background: transparent; height: {sb}px; margin: {p(2)}px; }}
QScrollBar::handle:horizontal {{ background: {t.border}; border-radius: {p(4)}px; min-width: {p(30)}px; }}
QScrollBar::handle:horizontal:hover {{ background: {t.muted}; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{ background: transparent; }}
QScrollArea {{ border: 0; background: transparent; }}

QSlider {{ min-height: {p(22)}px; }}
QSlider::groove:horizontal {{ height: {p(4)}px; background: {t.line}; border-radius: {p(2)}px; margin: 0 {p(9)}px; }}
QSlider::sub-page:horizontal {{ background: {t.accent}; border-radius: {p(2)}px; }}
QSlider::handle:horizontal {{ background: {t.paper}; border: {p(2)}px solid {t.accent}; width: {p(14)}px; height: {p(14)}px; margin: -{p(7)}px -{p(9)}px; border-radius: {p(9)}px; }}
QSlider::handle:horizontal:hover {{ background: {t.hover}; }}
QSlider::groove:vertical {{ width: {p(4)}px; background: {t.line}; border-radius: {p(2)}px; margin: {p(9)}px 0; }}
QSlider::add-page:vertical {{ background: {t.accent}; border-radius: {p(2)}px; }}
QSlider::handle:vertical {{ background: {t.paper}; border: {p(2)}px solid {t.accent}; width: {p(14)}px; height: {p(14)}px; margin: -{p(9)}px -{p(7)}px; border-radius: {p(9)}px; }}

QProgressBar {{ background: {t.line}; border: 0; border-radius: {p(4)}px; height: {p(8)}px; max-height: {p(8)}px; text-align: right; color: {t.text2}; font-size: {f_caption}pt; }}
QProgressBar::chunk {{ background: {t.accent}; border-radius: {p(4)}px; }}
QProgressBar[kind="success"]::chunk {{ background: {green}; }}
QProgressBar[kind="danger"]::chunk {{ background: {red}; }}
QProgressBar[kind="info"]::chunk {{ background: {t.semantic_dot["info"]}; }}
QProgressBar[kind="warning"]::chunk {{ background: {t.semantic_dot["warning"]}; }}
QProgressBar[labeled="true"] {{ height: {p(18)}px; max-height: {p(18)}px; padding-right: {p(36)}px; background: transparent; }}

/* ---------- dialogs ---------- */
QDialogButtonBox QPushButton {{ min-width: {p(84)}px; }}
QWizard QFrame {{ background: transparent; }}
QMessageBox QLabel {{ min-width: {p(260)}px; }}
"""


def palette(t: Tokens) -> QPalette:
    p = QPalette()
    roles = {
        QPalette.ColorRole.Window: t.canvas,
        QPalette.ColorRole.WindowText: t.text,
        QPalette.ColorRole.Base: t.paper,
        QPalette.ColorRole.AlternateBase: t.canvas,
        QPalette.ColorRole.Text: t.text,
        QPalette.ColorRole.Button: t.paper,
        QPalette.ColorRole.ButtonText: t.text,
        QPalette.ColorRole.Highlight: t.accent,
        QPalette.ColorRole.HighlightedText: t.accent_fg,
        QPalette.ColorRole.ToolTipBase: t.text,
        QPalette.ColorRole.ToolTipText: t.canvas,
        QPalette.ColorRole.PlaceholderText: t.muted,
        QPalette.ColorRole.Mid: t.border,
        QPalette.ColorRole.Dark: t.muted,
        QPalette.ColorRole.Light: t.paper,
        QPalette.ColorRole.Link: t.accent,
        QPalette.ColorRole.BrightText: t.accent_fg,
    }
    for role, hexv in roles.items():
        p.setColor(role, QColor(hexv))
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.WindowText, QPalette.ColorRole.ButtonText):
        p.setColor(QPalette.ColorGroup.Disabled, role, QColor(t.muted))
    return p


def apply(app, name: str = "light", zoom: float | None = None) -> Tokens:
    """Set the theme for the whole application (Fusion + palette + QSS + font).

    Safe to call repeatedly (theme toggle, zoom); only the stylesheet and the
    font are regenerated, the style object is created once.
    """
    global _current, _style_set
    _current = THEMES.get(name, LIGHT)
    if zoom is not None:
        set_zoom(zoom)
    if not _style_set:
        # once only – the app may later wrap the style in a QProxyStyle
        app.setStyle("Fusion")
        _style_set = True
    app.setPalette(palette(_current))
    app.setFont(ui_font(SIZE_BODY))
    app.setStyleSheet(build_qss(_current))
    return _current


def toggle(app) -> Tokens:
    """Switch light <-> dark. The caller then runs widgets.retheme_tree(window)."""
    return apply(app, "light" if is_dark() else "dark")
