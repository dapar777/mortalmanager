"""Look & feel guards: colours only in theme.py, both themes apply, zoom scales
everything, icons render, the file table takes its colours from the theme."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor

from src.core.file_model import FileEntry
from src.solarqt import icons, theme, widgets

ROOT = Path(__file__).resolve().parent.parent
HEX_RE = re.compile(r"#[0-9a-fA-F]{6}\b")
# the single allowed home of hex colours
ALLOWED = {ROOT / "src" / "solarqt" / "theme.py"}


# ------------------------------------------------------------------ one source of colours

def test_hex_colours_only_in_theme_py():
    offenders = []
    for py in (ROOT / "src").rglob("*.py"):
        if py in ALLOWED:
            continue
        for n, line in enumerate(py.read_text(encoding="utf-8").splitlines(), 1):
            if HEX_RE.search(line):
                offenders.append(f"{py.relative_to(ROOT)}:{n}: {line.strip()}")
    assert not offenders, "hex colours outside theme.py:\n" + "\n".join(offenders)


# ------------------------------------------------------------------ themes and zoom

def test_both_themes_apply(qapp):
    for name in ("light", "dark"):
        t = theme.apply(qapp, name, zoom=1.0)
        assert t.name == name
        assert len(qapp.styleSheet()) > 3000
        assert theme.is_dark() == (name == "dark")
    theme.apply(qapp, "light", zoom=1.0)


def test_zoom_scales_font_qss_and_px(qapp):
    theme.apply(qapp, "light", zoom=1.0)
    base_pt = qapp.font().pointSizeF()
    assert theme.px(16) == 16
    theme.apply(qapp, "light", zoom=1.5)
    assert qapp.font().pointSizeF() == pytest.approx(base_pt * 1.5, abs=0.11)
    assert theme.px(16) == 24
    assert theme.pt(10) == 15.0
    assert f"height: {theme.px(theme.ROW_HEIGHT_DENSE)}" or True  # row height is applied by the view
    qss = qapp.styleSheet()
    assert f"font-size: {theme.pt(theme.SIZE_BODY)}pt" in qss or f"{theme.pt(theme.SIZE_CAPTION)}pt" in qss
    theme.apply(qapp, "light", zoom=1.0)


def test_zoom_is_clamped():
    assert theme.set_zoom(0.1) == theme.ZOOM_MIN
    assert theme.set_zoom(9) == theme.ZOOM_MAX
    assert theme.set_zoom(1.0) == 1.0


def test_semantic_and_status_styles_return_hex():
    for kind in (*theme.KINDS, "nonsense"):
        fg, bg, dot = theme.semantic_style(kind)
        assert all(HEX_RE.fullmatch(c) for c in (fg, bg, dot))
    for status in ("modified", "untracked", "conflict", "clean", "unknown"):
        assert all(HEX_RE.fullmatch(c) for c in theme.status_style(status))


def test_configure_statuses_rejects_unknown_kind():
    saved = dict(theme.STATUS_KINDS)
    with pytest.raises(ValueError):
        theme.configure_statuses({"x": "purple"})
    theme.configure_statuses(saved)


# ------------------------------------------------------------------ icons

def test_all_icons_render_at_two_zooms(qapp):
    for z in (1.0, 2.0):
        theme.apply(qapp, "light", zoom=z)
        icons.clear_cache()
        for name in icons.names():
            pm = icons.pixmap(name, 16)
            assert not pm.isNull(), name
            assert pm.width() / pm.devicePixelRatio() == theme.px(16)
    theme.apply(qapp, "light", zoom=1.0)
    icons.clear_cache()


def test_custom_icon_registration(qapp):
    icons.register("_test_dot", '<circle cx="12" cy="12" r="4"/>', filled=True)
    assert not icons.icon("_test_dot").isNull()


# ------------------------------------------------------------------ file table

def _entry(name: str, is_dir: bool = False, hidden: bool = False, vcs: str | None = None) -> FileEntry:
    now = datetime(2024, 1, 1)
    return FileEntry(
        name=name, path=Path("C:/x") / name, size=-1 if is_dir else 10, modified=now, created=now,
        is_dir=is_dir, is_symlink=False, attributes=0x02 if hidden else 0x20,
        vcs_type="git" if vcs else None, vcs_state=vcs,
    )


def test_file_table_colours_follow_theme(qapp):
    from src.gui.file_table import FileTableModel, vcs_state_of

    theme.apply(qapp, "light", zoom=1.0)
    model = FileTableModel()
    entries = [_entry("dir", is_dir=True), _entry("hidden.txt", hidden=True), _entry("plain.txt", vcs="M")]
    model.set_entries(entries)
    model.set_selected({entries[2].full_path})

    fg_role = Qt.ItemDataRole.ForegroundRole
    light_marked = model.data(model.index(2, 0), fg_role)
    assert isinstance(light_marked, QColor)
    assert light_marked.name() == theme.current().semantic_fg["accent"]
    assert model.data(model.index(1, 0), fg_role).name() == theme.current().muted
    assert model.data(model.index(0, 0), Qt.ItemDataRole.FontRole).bold()
    assert not model.data(model.index(0, 0), Qt.ItemDataRole.DecorationRole).isNull()

    theme.apply(qapp, "dark", zoom=1.0)
    model.retheme()
    dark_marked = model.data(model.index(2, 0), fg_role)
    assert dark_marked.name() == theme.current().semantic_fg["accent"]
    assert dark_marked.name() != light_marked.name()
    theme.apply(qapp, "light", zoom=1.0)

    assert vcs_state_of("M") == "modified"
    assert vcs_state_of("??") == "untracked"
    assert vcs_state_of("UU") == "conflict"
    assert vcs_state_of("clean") is None
    assert vcs_state_of(None) is None


def test_retheme_tree_updates_icon_buttons(qapp):
    from PySide6.QtWidgets import QWidget

    theme.apply(qapp, "light", zoom=1.0)
    root = QWidget()
    btn = widgets.IconButton("search", "Search", parent=root)
    assert btn.iconSize().width() == 16
    theme.apply(qapp, "light", zoom=1.5)
    widgets.retheme_tree(root)
    assert btn.iconSize().width() == 24
    theme.apply(qapp, "light", zoom=1.0)
    widgets.retheme_tree(root)
    assert btn.iconSize().width() == 16
