"""Application configuration manager backed by SQLite."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.database.db import DatabaseManager
from src.index.file_index import DEFAULT_EXCLUDE_NAMES, default_exclude_paths


_OLD_APP_DATA_DIR = Path.home() / "AppData" / "Roaming" / "MortalManager"   # name before Sept 2026
_APP_DATA_DIR = Path.home() / "AppData" / "Roaming" / "UltimateCommander"


def _migrate_data_dir() -> Path:
    """First start after the rename: move %APPDATA%\\MortalManager to UltimateCommander.
    If the move fails (an old instance still holds the database) keep using the old folder."""
    if _APP_DATA_DIR.exists() or not _OLD_APP_DATA_DIR.exists():
        return _APP_DATA_DIR
    try:
        _OLD_APP_DATA_DIR.rename(_APP_DATA_DIR)
        return _APP_DATA_DIR
    except OSError:
        return _OLD_APP_DATA_DIR


_APP_DATA_DIR = _migrate_data_dir()
_DB_PATH = _APP_DATA_DIR / "config.db"


@dataclass
class PanelConfig:
    sort_field: str = "NAME"
    sort_order: str = "ASCENDING"
    show_hidden: bool = False
    column_widths: dict[str, int] = field(default_factory=lambda: {
        "name": 250, "ext": 60, "size": 90, "modified": 140, "attributes": 60
    })


@dataclass
class AppConfig:
    theme: str = "light"
    zoom: float = 1.0          # UI zoom factor (0.7–2.0), see solarqt.theme
    language: str = "en"
    confirm_delete: bool = True
    confirm_overwrite: bool = True
    use_trash: bool = True
    show_thumbnails: bool = True
    thumbnail_size: int = 64
    editor_font: str = "Consolas"
    editor_font_size: int = 10
    external_editor: str = "code -n"   # F4 command line ({file} = paths); empty = built-in editor
    copy_suffix: str = " - Kopie"      # Ctrl+V into the same folder: "name - Kopie.ext" (Explorer / TC style)
    left_panel: PanelConfig = field(default_factory=PanelConfig)
    right_panel: PanelConfig = field(default_factory=PanelConfig)
    splitter_ratio: float = 0.5
    window_width: int = 1280
    window_height: int = 800
    window_maximized: bool = False
    command_bar_visible: bool = True
    toolbar_visible: bool = True
    fkeys_bar_visible: bool = True
    cmd_expand_shortcut: str = "Ctrl+E"
    cmd_expanded: bool = False
    # file-name index for the command palette (see src/index)
    index_enabled: bool = True
    index_roots: list[str] = field(default_factory=lambda: ["C:\\"])
    index_exclude_names: list[str] = field(default_factory=lambda: list(DEFAULT_EXCLUDE_NAMES))
    index_exclude_paths: list[str] = field(default_factory=default_exclude_paths)
    index_rescan_hours: float = 24.0


class ConfigManager:
    """High-level configuration API wrapping DatabaseManager."""

    _instance: ConfigManager | None = None

    def __init__(self, db: DatabaseManager | None = None) -> None:
        if db is None:
            db = DatabaseManager(_DB_PATH)
        self._db = db
        self._cache: AppConfig = self._load()

    @classmethod
    def get_instance(cls) -> ConfigManager:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # ------------------------------------------------------------------ load/save

    def _load(self) -> AppConfig:
        cfg = AppConfig()
        d = self._db.get_all_settings()

        cfg.theme = d.get("theme", cfg.theme)
        try:
            cfg.zoom = float(d.get("zoom", cfg.zoom))
        except (TypeError, ValueError):
            cfg.zoom = 1.0
        cfg.language = d.get("language", cfg.language)
        cfg.confirm_delete = d.get("confirm_delete", cfg.confirm_delete)
        cfg.confirm_overwrite = d.get("confirm_overwrite", cfg.confirm_overwrite)
        cfg.use_trash = d.get("use_trash", cfg.use_trash)
        cfg.show_thumbnails = d.get("show_thumbnails", cfg.show_thumbnails)
        cfg.thumbnail_size = d.get("thumbnail_size", cfg.thumbnail_size)
        cfg.editor_font = d.get("editor_font", cfg.editor_font)
        cfg.editor_font_size = d.get("editor_font_size", cfg.editor_font_size)
        cfg.external_editor = str(d.get("external_editor", cfg.external_editor))
        cfg.copy_suffix = str(d.get("copy_suffix", cfg.copy_suffix))
        if cfg.external_editor.strip() == "code":      # old default → open in a new VS Code window
            cfg.external_editor = "code -n"
        cfg.splitter_ratio = d.get("splitter_ratio", cfg.splitter_ratio)
        cfg.window_width = d.get("window_width", cfg.window_width)
        cfg.window_height = d.get("window_height", cfg.window_height)
        cfg.window_maximized = d.get("window_maximized", cfg.window_maximized)
        cfg.command_bar_visible = d.get("command_bar_visible", cfg.command_bar_visible)
        cfg.toolbar_visible = d.get("toolbar_visible", cfg.toolbar_visible)
        cfg.fkeys_bar_visible = d.get("fkeys_bar_visible", cfg.fkeys_bar_visible)
        cfg.cmd_expand_shortcut = d.get("cmd_expand_shortcut", cfg.cmd_expand_shortcut)
        cfg.cmd_expanded = d.get("cmd_expanded", cfg.cmd_expanded)
        cfg.index_enabled = bool(d.get("index_enabled", cfg.index_enabled))
        for key in ("index_roots", "index_exclude_names", "index_exclude_paths"):
            val = d.get(key)
            if isinstance(val, list):
                setattr(cfg, key, [str(x) for x in val])
        try:
            cfg.index_rescan_hours = float(d.get("index_rescan_hours", cfg.index_rescan_hours))
        except (TypeError, ValueError):
            pass

        lp = d.get("left_panel", {})
        if isinstance(lp, dict):
            cfg.left_panel = PanelConfig(**{k: v for k, v in lp.items() if k in PanelConfig.__dataclass_fields__})

        rp = d.get("right_panel", {})
        if isinstance(rp, dict):
            cfg.right_panel = PanelConfig(**{k: v for k, v in rp.items() if k in PanelConfig.__dataclass_fields__})

        return cfg

    def save(self) -> None:
        cfg = self._cache
        self._db.set_setting("theme", cfg.theme)
        self._db.set_setting("zoom", cfg.zoom)
        self._db.set_setting("language", cfg.language)
        self._db.set_setting("confirm_delete", cfg.confirm_delete)
        self._db.set_setting("confirm_overwrite", cfg.confirm_overwrite)
        self._db.set_setting("use_trash", cfg.use_trash)
        self._db.set_setting("show_thumbnails", cfg.show_thumbnails)
        self._db.set_setting("thumbnail_size", cfg.thumbnail_size)
        self._db.set_setting("editor_font", cfg.editor_font)
        self._db.set_setting("editor_font_size", cfg.editor_font_size)
        self._db.set_setting("external_editor", cfg.external_editor)
        self._db.set_setting("copy_suffix", cfg.copy_suffix)
        self._db.set_setting("splitter_ratio", cfg.splitter_ratio)
        self._db.set_setting("window_width", cfg.window_width)
        self._db.set_setting("window_height", cfg.window_height)
        self._db.set_setting("window_maximized", cfg.window_maximized)
        self._db.set_setting("command_bar_visible", cfg.command_bar_visible)
        self._db.set_setting("toolbar_visible", cfg.toolbar_visible)
        self._db.set_setting("fkeys_bar_visible", cfg.fkeys_bar_visible)
        self._db.set_setting("cmd_expand_shortcut", cfg.cmd_expand_shortcut)
        self._db.set_setting("cmd_expanded", cfg.cmd_expanded)
        self._db.set_setting("index_enabled", cfg.index_enabled)
        self._db.set_setting("index_roots", list(cfg.index_roots))
        self._db.set_setting("index_exclude_names", list(cfg.index_exclude_names))
        self._db.set_setting("index_exclude_paths", list(cfg.index_exclude_paths))
        self._db.set_setting("index_rescan_hours", cfg.index_rescan_hours)
        self._db.set_setting("left_panel", {
            "sort_field": cfg.left_panel.sort_field,
            "sort_order": cfg.left_panel.sort_order,
            "show_hidden": cfg.left_panel.show_hidden,
            "column_widths": cfg.left_panel.column_widths,
        })
        self._db.set_setting("right_panel", {
            "sort_field": cfg.right_panel.sort_field,
            "sort_order": cfg.right_panel.sort_order,
            "show_hidden": cfg.right_panel.show_hidden,
            "column_widths": cfg.right_panel.column_widths,
        })

    # ------------------------------------------------------------------ access

    @property
    def config(self) -> AppConfig:
        return self._cache

    def get(self, key: str, default: Any = None) -> Any:
        return self._db.get_setting(key, default)

    def set(self, key: str, value: Any) -> None:
        self._db.set_setting(key, value)

    @property
    def data_dir(self) -> Path:
        return _APP_DATA_DIR

    @staticmethod
    def app_data_dir() -> Path:
        """%APPDATA% folder of the app (settings, index, log) – usable before the singleton exists."""
        return _APP_DATA_DIR
