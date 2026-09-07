"""Main application window.

Layout (see solarqt MANUAL.md §8): menu bar · #headerBar (logo, name, drive
bar, search / commands / theme) · vertical splitter (two PanelWidgets side by
side + #fkeysBar | embedded terminal) · status bar (panel info left, zoom and
free space right).

Zoom: Ctrl+wheel / Ctrl+± / Ctrl+0 change one factor (theme.set_zoom) and
re-apply the stylesheet; wheel notches are coalesced by a short timer so a
fast scroll costs one re-style, not ten. The factor is persisted in config.
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, Qt, QTimer, Signal
from PySide6.QtGui import QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QSizePolicy,
    QSplitter,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from src.core.file_model import FileEntry
from src.core.undo import UndoActionType, UndoManager
from src.jobs.job import JobResult, JobSpec, JobStatus, JobType
from src.jobs.job_queue import JobQueue
from src.settings.config import ConfigManager
from src.solarqt import icons, theme, widgets
from src.solarqt.widgets import ActionButton, IconButton, Toast, VLine
from .drive_bar import DriveBar
from .index_service import IndexService
from .panel import PanelWidget

logger = logging.getLogger(__name__)

_ASSETS = Path(__file__).resolve().parent.parent.parent / "assets" / "icons"


class MainWindow(QMainWindow):
    """The main dual-pane file manager window."""

    zoom_changed = Signal(float)

    def __init__(self, event_loop: asyncio.AbstractEventLoop) -> None:
        super().__init__()
        self._loop = event_loop
        self._cfg = ConfigManager.get_instance()
        self._job_queue = JobQueue(self)
        self._job_queue.set_event_loop(event_loop)
        self._job_specs: dict[str, JobSpec] = {}
        self._job_queue.job_finished.connect(self._on_job_finished)
        self._job_queue.job_failed.connect(self._on_job_failed)
        self._undo = UndoManager()
        self._active_panel: str = self._cfg.get("active_panel", "left") or "left"
        self._compact = False

        # zoom: the first wheel notch applies immediately, further notches that
        # arrive while a re-style is fresh are merged into one later apply
        # (a re-style of the whole window costs ~150-300 ms)
        self._zoom_pending: float | None = None
        self._zoom_timer = QTimer(self)
        self._zoom_timer.setSingleShot(True)
        self._zoom_timer.setInterval(220)
        self._zoom_timer.timeout.connect(self._flush_zoom)
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(1000)
        self._save_timer.timeout.connect(self._cfg.save)

        self._build_ui()
        self._build_menus()
        self._build_shortcuts()
        self._restore_geometry()
        self._update_zoom_label()
        self._index = IndexService(self._cfg, self)

        # Global event filter for Ctrl+Wheel zoom
        QApplication.instance().installEventFilter(self)

    # ------------------------------------------------------------------ UI build

    def _build_ui(self) -> None:
        self.setWindowTitle("MortalManager")
        self.setMinimumSize(theme.MIN_WINDOW_WIDTH, 360)

        central = QWidget()
        self.setCentralWidget(central)
        root_layout = QVBoxLayout(central)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        root_layout.addWidget(self._build_header())

        # Panels
        self._splitter = QSplitter(Qt.Orientation.Horizontal)
        self._splitter.setChildrenCollapsible(False)
        home = str(Path.home())
        self._left_panel = PanelWidget(home, "left", self)
        self._right_panel = PanelWidget(home, "right", self)
        for side, panel in (("left", self._left_panel), ("right", self._right_panel)):
            panel.request_focus.connect(lambda s=side: self._set_active(s))
            panel.entry_activated.connect(self._on_entry_open)
            panel.path_changed.connect(lambda p, s=side: self._on_path_changed(s, p))
            panel.status_info.connect(lambda info, s=side: self._on_status_info(s, info))
            panel.favorites_requested.connect(lambda s=side: (self._set_active(s), self._open_favorites()))
            panel.rename_requested.connect(self._on_inline_rename)
        self._splitter.addWidget(self._left_panel)
        self._splitter.addWidget(self._right_panel)
        self._splitter.setSizes([600, 600])

        self._fkeys_bar = self._build_fkeys_bar()
        self._fkeys_bar.setVisible(self._cfg.config.fkeys_bar_visible)

        panels_container = QWidget()
        pc_layout = QVBoxLayout(panels_container)
        pc_layout.setContentsMargins(theme.px(8), theme.px(8), theme.px(8), 0)
        pc_layout.setSpacing(theme.px(6))
        pc_layout.addWidget(self._splitter, stretch=1)
        pc_layout.addWidget(self._fkeys_bar)
        self._panels_layout = pc_layout

        from .terminal_widget import EmbeddedTerminalWidget
        self._terminal = EmbeddedTerminalWidget(home, self._cfg._db, self)
        self._terminal.cwd_changed.connect(lambda path: self._active_panel_widget.navigate_to(path))
        self._terminal.set_context_provider(self._terminal_context)
        for panel in (self._left_panel, self._right_panel):
            panel.cmdline_insert.connect(self._insert_into_terminal)
        self._terminal.height_step.connect(self._terminal_height_step)
        self._terminal.setVisible(self._cfg.config.command_bar_visible)
        self._terminal_base_sizes: list[int] | None = None   # splitter sizes before Alt+± (None = untouched)
        QApplication.instance().focusChanged.connect(self._on_focus_changed)

        self._v_splitter = QSplitter(Qt.Orientation.Vertical)
        self._v_splitter.addWidget(panels_container)
        self._v_splitter.addWidget(self._terminal)
        self._v_splitter.setStretchFactor(0, 1)
        self._v_splitter.setStretchFactor(1, 0)
        self._v_splitter.setSizes([560, 160])
        root_layout.addWidget(self._v_splitter, stretch=1)

        # Status bar: message left, zoom + free space right
        self._statusbar = QStatusBar()
        self._statusbar.setSizeGripEnabled(False)
        self.setStatusBar(self._statusbar)
        self._status_msg = QLabel("")
        self._status_msg.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._status_msg.setMinimumWidth(theme.px(80))
        self._statusbar.addWidget(self._status_msg, 1)
        self._zoom_label = QLabel("")
        self._zoom_label.setObjectName("faintLabel")
        self._zoom_label.setToolTip("Zoom (Ctrl+wheel, Ctrl+0 resets)")
        self._statusbar.addPermanentWidget(self._zoom_label)
        self._free_label = QLabel("")
        self._free_label.setObjectName("faintLabel")
        self._statusbar.addPermanentWidget(self._free_label)

        self._set_active(self._active_panel)
        QTimer.singleShot(0, self._focus_active_panel)

    def _build_header(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("headerBar")
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(theme.px(12), theme.px(6), theme.px(12), theme.px(6))
        lay.setSpacing(theme.px(10))
        self._header_layout = lay

        # no logo / app name in the header: the window title and taskbar carry them
        self._drive_bar = DriveBar()
        self._drive_bar.drive_selected.connect(self._on_drive_clicked)
        self._drive_bar.drives_updated.connect(lambda _d: self._update_free_label())
        lay.addWidget(self._drive_bar)
        lay.addStretch(1)

        self._btn_search = IconButton("search", "Search (Alt+F7)", text="Search", framed=True)
        self._btn_search.clicked.connect(self._open_search)
        self._btn_commands = IconButton("command", "Command palette (Ctrl+Shift+P)", text="Commands", framed=True)
        self._btn_commands.clicked.connect(self._open_command_palette)
        self._btn_theme = IconButton("moon", "Toggle dark theme")
        self._btn_theme.clicked.connect(self._toggle_theme)
        lay.addWidget(self._btn_search)
        lay.addWidget(self._btn_commands)
        lay.addWidget(VLine())
        lay.addWidget(self._btn_theme)
        self._retheme_header()
        return bar

    def _retheme_header(self) -> None:
        variant = "dark" if theme.is_dark() else "light"
        from src.main import app_icon
        icon = app_icon(variant)
        self.setWindowIcon(icon)
        QApplication.instance().setWindowIcon(icon)
        self._apply_taskbar_identity(variant)
        self._btn_theme.set_icon_name("sun" if theme.is_dark() else "moon")
        self._btn_theme.setToolTip("Switch to light theme" if theme.is_dark() else "Switch to dark theme")
        self._header_layout.setContentsMargins(theme.px(12), theme.px(6), theme.px(12), theme.px(6))
        self._header_layout.setSpacing(theme.px(10))

    def _apply_taskbar_identity(self, variant: str) -> None:
        """Taskbar icon and grouping on Windows.

        The author's venv runs on the Microsoft Store Python, which is an MSIX
        package: for packaged processes the taskbar shows the *package* logo
        (Python) and ignores the window icon. Setting AppUserModel properties
        on the window itself (id, relaunch icon, display name) makes the shell
        treat the window as its own app with our icon. Needs the HWND, so it is
        applied once shown and again on every theme change (icon variant).
        """
        if sys.platform != "win32" or not self.isVisible():
            return
        try:
            from win32com.propsys import propsys, pscon
        except Exception:
            return
        ico = _ASSETS / f"mortalmanager-{variant}.ico"
        try:
            ps = propsys.SHGetPropertyStoreForWindow(int(self.winId()))
            ps.SetValue(pscon.PKEY_AppUserModel_ID, propsys.PROPVARIANTType("MortalManager.App.2"))
            ps.SetValue(pscon.PKEY_AppUserModel_RelaunchDisplayNameResource, propsys.PROPVARIANTType("MortalManager"))
            if ico.exists():
                ps.SetValue(pscon.PKEY_AppUserModel_RelaunchIconResource, propsys.PROPVARIANTType(f"{ico},0"))
            pyw = Path(sys.executable).with_name("pythonw.exe")
            exe = pyw if pyw.exists() else Path(sys.executable)
            ps.SetValue(pscon.PKEY_AppUserModel_RelaunchCommand,
                        propsys.PROPVARIANTType(f'"{exe}" -m src.main'))
            ps.Commit()
        except Exception as exc:  # cosmetic – never block startup
            logger.debug("taskbar identity not applied: %s", exc)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if not getattr(self, "_taskbar_done", False):
            self._taskbar_done = True
            self._apply_taskbar_identity("dark" if theme.is_dark() else "light")

    def _build_fkeys_bar(self) -> QWidget:
        w = QFrame()
        w.setObjectName("fkeysBar")
        layout = QHBoxLayout(w)
        layout.setContentsMargins(theme.px(6), theme.px(4), theme.px(6), theme.px(4))
        layout.setSpacing(theme.px(4))
        self._fkeys_layout = layout
        self._fkeys_buttons: list[tuple[ActionButton, str, str]] = []
        items: list[tuple[str, str, str, object] | None] = [
            ("F3", "View", "eye", self._view_file),
            ("F4", "Edit", "edit", self._edit_file),
            ("F5", "Copy", "copy", self._copy_files),
            ("F6", "Move", "arrow_right", self._move_files),
            ("F7", "New folder", "folder_plus", self._mkdir),
            ("F8", "Delete", "trash", self._delete_files),
            None,
            ("Ctrl+R", "Refresh", "refresh", lambda: self._active_panel_widget.refresh()),
            ("Alt+F7", "Search", "search", self._open_search),
            ("Ctrl+M", "Rename", "rename", self._bulk_rename),
        ]
        for item in items:
            if item is None:
                layout.addWidget(VLine())
                continue
            key, label, icon_name, handler = item
            btn = ActionButton(f"{key}  {label}", icon_name, small=True)
            btn.setToolTip(f"{label} ({key})")
            btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            btn.setMinimumWidth(theme.px(40))
            btn.clicked.connect(handler)  # type: ignore[arg-type]
            layout.addWidget(btn, 1)   # equal stretch: the bar spans the full width
            self._fkeys_buttons.append((btn, key, label))
        return w

    def _set_fkeys_compact(self, compact: bool) -> None:
        """Narrow window: only the key on each button, label in the tooltip."""
        for btn, key, label in self._fkeys_buttons:
            btn.setText(key if compact else f"{key}  {label}")

    # ------------------------------------------------------------------ menus

    def _build_menus(self) -> None:
        mb = self.menuBar()

        file_menu = mb.addMenu("&Files")
        file_menu.addAction(icons.icon("file"), "&New File\tShift+F4", self._new_file)
        file_menu.addAction(icons.icon("folder_plus"), "New &Folder\tF7", self._mkdir)
        file_menu.addAction(icons.icon("rename"), "&Rename\tF2", self._rename_inline)
        file_menu.addSeparator()
        file_menu.addAction(icons.icon("edit"), "&Edit\tF4", self._edit_file)
        file_menu.addAction(icons.icon("edit"), "Edit in &Built-in Editor", self._edit_builtin)
        file_menu.addSeparator()
        file_menu.addAction(icons.icon("star_outline"), "&Favorites\tCtrl+D", self._open_favorites)
        file_menu.addSeparator()
        file_menu.addAction(icons.icon("info"), "&Properties\tAlt+Enter", self._show_properties)
        file_menu.addSeparator()
        file_menu.addAction("E&xit\tAlt+F4", self.close)

        mark_menu = mb.addMenu("&Mark")
        mark_menu.addAction("&Toggle Mark\tIns / Space", self._toggle_mark)
        mark_menu.addAction("Select &All\tCtrl+A / Num+*", self._active_panel_widget_select_all)
        mark_menu.addAction("&Deselect All\tNum+-", self._deselect_all)
        mark_menu.addAction("&Invert Selection\tNum+/", self._invert_selection)
        mark_menu.addSeparator()
        mark_menu.addAction(icons.icon("filter"), "Select by &Mask…", self._select_by_mask)
        mark_menu.addSeparator()
        mark_menu.addAction(icons.icon("clipboard"), "Copy &Names to Clipboard\tCtrl+Shift+C", self._copy_names)
        mark_menu.addAction(icons.icon("clipboard"), "Copy Full &Paths to Clipboard\tCtrl+Alt+C", self._copy_paths)

        cmd_menu = mb.addMenu("&Commands")
        cmd_menu.addAction(icons.icon("search"), "&Search…\tAlt+F7", self._open_search)
        cmd_menu.addAction(icons.icon("filter"), "&Quick Filter\tCtrl+S", lambda: self._active_panel_widget.show_filter())
        cmd_menu.addAction(icons.icon("settings"), "File &Index Settings…", self._open_index_settings)
        cmd_menu.addAction(icons.icon("settings"), "External &Editor…", self._open_editor_settings)
        cmd_menu.addAction(icons.icon("rename"), "Bulk &Rename…\tCtrl+M", self._bulk_rename)
        cmd_menu.addSeparator()
        cmd_menu.addAction(icons.icon("scale"), "Calculate Si&ze", self._calc_size)
        cmd_menu.addAction(icons.icon("hash"), "Compute &Hash…", self._compute_hash)
        cmd_menu.addAction(icons.icon("copy"), "Find &Duplicates…", self._find_duplicates)
        cmd_menu.addSeparator()
        cmd_menu.addAction(icons.icon("terminal"), "Open &Terminal Here", self._open_terminal)
        cmd_menu.addAction(icons.icon("shield"), "Open as &Admin", self._relaunch_admin)
        cmd_menu.addSeparator()
        cmd_menu.addAction(icons.icon("command"), "Command &Palette…\tCtrl+Shift+P", self._open_command_palette)

        net_menu = mb.addMenu("&Network")
        net_menu.addAction(icons.icon("network"), "&FTP/SFTP Connect…", self._open_ftp)

        show_menu = mb.addMenu("&Show")
        self._act_hidden = show_menu.addAction("Show &Hidden Files\tCtrl+H")
        self._act_hidden.setCheckable(True)
        self._act_hidden.triggered.connect(self._toggle_hidden)
        show_menu.addSeparator()
        self._act_toolbar = show_menu.addAction("F-Keys &Bar")
        self._act_toolbar.setCheckable(True)
        self._act_toolbar.setChecked(self._cfg.config.fkeys_bar_visible)
        self._act_toolbar.triggered.connect(self._toggle_toolbar)
        self._act_cmdbar = show_menu.addAction("&Terminal Pane")
        self._act_cmdbar.setCheckable(True)
        self._act_cmdbar.setChecked(self._cfg.config.command_bar_visible)
        self._act_cmdbar.triggered.connect(self._toggle_cmdbar)
        show_menu.addSeparator()
        show_menu.addAction(icons.icon("zoom_in"), "Zoom &In\tCtrl++", lambda: self._zoom_step(+1))
        show_menu.addAction(icons.icon("zoom_out"), "Zoom &Out\tCtrl+-", lambda: self._zoom_step(-1))
        show_menu.addAction("Reset &Zoom\tCtrl+0", self._reset_zoom)
        show_menu.addSeparator()
        self._act_dark_theme = show_menu.addAction(icons.icon("moon"), "&Dark Theme")
        self._act_dark_theme.setCheckable(True)
        self._act_dark_theme.setChecked(self._cfg.config.theme == "dark")
        self._act_dark_theme.triggered.connect(self._toggle_theme)

        help_menu = mb.addMenu("&Help")
        help_menu.addAction(icons.icon("help"), "&About MortalManager", self._show_about)

    # ------------------------------------------------------------------ shortcuts

    def _build_shortcuts(self) -> None:
        shortcuts = [
            ("F3",          self._view_file),
            ("F4",          self._edit_file),
            ("Shift+F4",    self._new_file),
            ("F5",          self._copy_files),
            ("F6",          self._move_files),
            ("F7",          self._mkdir),
            ("F8",          self._delete_files),
            ("Delete",      self._delete_files),
            ("Alt+F7",      self._open_search),
            ("Alt+F1",      lambda: self._show_drive_menu("left")),
            ("Alt+F2",      lambda: self._show_drive_menu("right")),
            ("Ctrl+M",      self._bulk_rename),
            ("Shift+F6",    self._rename_inline),
            ("Ctrl+R",      lambda: self._active_panel_widget.refresh()),
            ("Alt+Return",  self._show_properties),
            ("Ctrl+H",      self._toggle_hidden),
            ("Ctrl+Z",      self._undo_action),
            ("Ctrl+T",      lambda: self._active_panel_widget._new_tab_from_current()),
            ("Alt+Left",    lambda: self._active_panel_widget._go_back()),
            ("Alt+Right",   lambda: self._active_panel_widget._go_forward()),
            ("Backspace",   lambda: self._active_panel_widget._go_up()),
            ("Num+*",       self._active_panel_widget_select_all),
            ("Num+-",       self._deselect_all),
            ("Num+/",       self._invert_selection),
            ("Ctrl+D",      self._open_favorites),
            ("Ctrl+Down",   self._focus_cmdline),
            ("Ctrl+Up",     self._focus_active_panel),
            ("Ctrl+Shift+P", self._open_command_palette),
            ("Ctrl+A",      self._active_panel_widget_select_all),
            ("Ctrl+L",      self._focus_path),
            ("Ctrl+S",      lambda: self._active_panel_widget.show_filter()),
            ("Ctrl+Shift+C", self._copy_names),
            ("Ctrl+Alt+C",  self._copy_paths),
            ("Alt+Down",    lambda: self._active_panel_widget.show_history_menu()),
        ]
        for key, handler in shortcuts:
            sc = QShortcut(QKeySequence(key), self)
            sc.activated.connect(handler)

        sc_clear = QShortcut(QKeySequence(self._cfg.config.cmd_expand_shortcut), self)
        sc_clear.activated.connect(self._terminal.clear_output)

        for seq in (QKeySequence.StandardKey.ZoomIn, QKeySequence("Ctrl+="), QKeySequence("Ctrl+Plus")):
            QShortcut(QKeySequence(seq), self).activated.connect(lambda: self._zoom_step(+1))
        QShortcut(QKeySequence.StandardKey.ZoomOut, self).activated.connect(lambda: self._zoom_step(-1))
        QShortcut(QKeySequence("Ctrl+0"), self).activated.connect(self._reset_zoom)

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802
        if event.type() == QEvent.Type.Wheel and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            delta = event.angleDelta().y()
            if delta:
                self._zoom_step(1 if delta > 0 else -1)
            return True
        return super().eventFilter(obj, event)

    def focusNextPrevChild(self, next: bool) -> bool:  # noqa: N802
        # Tab is the panel switch (FileTableView) – no focus cycling in the main window.
        return False

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._apply_compact(self.width() < theme.px(theme.HEADER_COMPACT_BELOW))

    def _apply_compact(self, compact: bool) -> None:
        if compact == self._compact:
            return
        self._compact = compact
        self._btn_search.set_compact(compact)
        self._btn_commands.set_compact(compact)
        self._set_fkeys_compact(compact)

    # ------------------------------------------------------------------ zoom

    def _zoom_step(self, direction: int) -> None:
        base = self._zoom_pending if self._zoom_pending is not None else theme.zoom()
        self._zoom_pending = max(theme.ZOOM_MIN, min(theme.ZOOM_MAX, round(base + direction * theme.ZOOM_STEP, 2)))
        if self._zoom_timer.isActive():
            return          # a re-style just happened; merge into the next one
        self._flush_zoom()

    def _reset_zoom(self) -> None:
        self._zoom_pending = 1.0
        self._zoom_timer.stop()
        self._flush_zoom()

    def _flush_zoom(self) -> None:
        if self._zoom_pending is None:
            return
        factor = self._zoom_pending
        self._zoom_pending = None
        if abs(factor - theme.zoom()) < 1e-6:
            return
        self._apply_zoom(factor)
        self._zoom_timer.start()   # throttle window for notches that follow

    def _apply_zoom(self, factor: float) -> None:
        app = QApplication.instance()
        self.setUpdatesEnabled(False)
        try:
            theme.apply(app, self._cfg.config.theme, zoom=factor)
            self._retheme_all()
        finally:
            self.setUpdatesEnabled(True)
        self._cfg.config.zoom = theme.zoom()
        self._save_timer.start()
        self._update_zoom_label()
        self.zoom_changed.emit(theme.zoom())

    def _update_zoom_label(self) -> None:
        z = theme.zoom()
        self._zoom_label.setText("" if abs(z - 1.0) < 1e-6 else f"{round(z * 100)} %")
        self._zoom_label.setVisible(bool(self._zoom_label.text()))

    def _retheme_all(self) -> None:
        """Theme or zoom changed: re-style every widget that holds sizes/colours.
        (Not named retheme(): retheme_tree would call it on the root and recurse.)"""
        widgets.retheme_tree(self, repolish=False)
        self._retheme_header()
        self._panels_layout.setContentsMargins(theme.px(8), theme.px(8), theme.px(8), 0)
        self._panels_layout.setSpacing(theme.px(6))
        self._fkeys_layout.setContentsMargins(theme.px(6), theme.px(4), theme.px(6), theme.px(4))
        self._fkeys_layout.setSpacing(theme.px(4))
        self._status_msg.setMinimumWidth(theme.px(80))
        self.menuBar().update()
        self._apply_compact(self.width() < theme.px(theme.HEADER_COMPACT_BELOW))

    # ------------------------------------------------------------------ panel helpers

    @property
    def _active_panel_widget(self) -> PanelWidget:
        return self._left_panel if self._active_panel == "left" else self._right_panel

    @property
    def _inactive_panel_widget(self) -> PanelWidget:
        return self._right_panel if self._active_panel == "left" else self._left_panel

    def _set_active(self, side: str) -> None:
        changed = side != self._active_panel
        self._active_panel = side
        if changed:
            self._cfg.set("active_panel", side)
        self._left_panel.set_active(side == "left")
        self._right_panel.set_active(side == "right")
        if hasattr(self, "_terminal"):
            self._terminal.set_cwd(self._active_panel_widget.current_path)
        if hasattr(self, "_drive_bar"):
            self._drive_bar.set_current_path(self._active_panel_widget.current_path)
            self._update_free_label()
        if self.isVisible():
            self._active_panel_widget.give_focus()

    def _switch_panel(self) -> None:
        self._set_active("right" if self._active_panel == "left" else "left")
        self._active_panel_widget.give_focus()

    def _focus_cmdline(self) -> None:
        self._terminal.give_focus()

    # ------------------------------------------------------------------ terminal pane height (temporary)

    TERMINAL_STEP = 48          # px per Alt+± press (before zoom)
    TERMINAL_MIN = 80           # smallest pane height
    PANELS_MIN = 160            # the file panels never shrink below this

    def _terminal_height_step(self, delta: int) -> None:
        """Alt++ / Alt+- while typing in the terminal: grow / shrink the output
        pane. Only lasts while the terminal has focus – `_on_focus_changed`
        restores the original split as soon as focus goes anywhere else."""
        if not self._terminal.isVisible():
            return
        sizes = self._v_splitter.sizes()
        if len(sizes) != 2 or sum(sizes) <= 0:
            return
        if self._terminal_base_sizes is None:
            self._terminal_base_sizes = list(sizes)
        total = sum(sizes)
        term = sizes[1] + delta * theme.px(self.TERMINAL_STEP)
        term = max(theme.px(self.TERMINAL_MIN), min(total - theme.px(self.PANELS_MIN), term))
        self._v_splitter.setSizes([total - term, term])

    def _terminal_height_from_palette(self, delta: int) -> None:
        self._focus_cmdline()
        self._terminal_height_step(delta)

    def _on_focus_changed(self, old, new) -> None:
        """Revert a temporary terminal height once focus leaves the terminal pane.
        ``new`` is None when the whole window deactivates – keep the size then,
        focus comes back to the same widget."""
        if self._terminal_base_sizes is None or new is None:
            return
        if self._terminal.isAncestorOf(new) or new is self._terminal:
            return
        base, self._terminal_base_sizes = self._terminal_base_sizes, None
        if self._terminal.isVisible():
            self._v_splitter.setSizes(base)

    def _focus_active_panel(self) -> None:
        self._active_panel_widget.give_focus()

    def _focus_path(self) -> None:
        edit = self._active_panel_widget._path_edit
        edit.setFocus()
        edit.selectAll()

    def _on_drive_clicked(self, root: str) -> None:
        self._active_panel_widget.navigate_to(root)
        self._active_panel_widget.give_focus()

    def _show_drive_menu(self, side: str) -> None:
        """Alt+F1 / Alt+F2 (Total Commander): drive list above the left / right
        panel; picking one makes that panel active and goes to the drive root."""
        from PySide6.QtWidgets import QMenu
        from .panel import fit_menu_on_screen
        panel = self._left_panel if side == "left" else self._right_panel
        drives = self._drive_bar.drives()
        if not drives:
            Toast.show_message(self, "Drive list is still loading", "info")
            return
        menu = QMenu(self)
        cur = self._drive_bar.drive_for(panel.current_path)
        for d in drives:
            act = menu.addAction(icons.icon("drive"), f"{d.letter}:   {d.label or d.drive_type.title()}   ·   {d.free_display} free")
            act.setData(d.root)
            if cur is not None and d.root == cur.root:
                act.setCheckable(True)
                act.setChecked(True)
        chosen = fit_menu_on_screen(menu, panel.mapToGlobal(panel.rect().topLeft()))
        if chosen is not None:
            self._set_active(side)
            panel.navigate_to(chosen.data())
            panel.give_focus()

    _PALETTE_RECENT_KEY = "palette_recent"

    def _open_command_palette(self) -> None:
        from .command_palette import RECENT_MAX, CommandPalette
        try:
            recent = [str(x) for x in (self._cfg.get(self._PALETTE_RECENT_KEY, []) or [])]
        except Exception:
            recent = []

        def remember(path: str) -> None:
            new = [path] + [r for r in recent if r != path]
            self._cfg.set(self._PALETTE_RECENT_KEY, new[: RECENT_MAX * 2])

        dlg = CommandPalette(self._build_palette_commands(), self, recent=recent, on_run=remember,
                             extra_search=self._palette_extra_search, mode_search=self._palette_mode_search)
        dlg.exec()

    # ---- palette: files from the index, terminal history

    def _file_hit(self, path: str, name: str, is_dir: bool) -> dict:
        p = self._active_panel_widget
        return {
            "category": "File", "label": f"{name}   —   {IndexService.display_dir(path)}",
            "icon": "folder" if is_dir else "file", "tooltip": path,
            "run": lambda path=path: p.reveal(path),
        }

    def _file_search(self, query: str, limit: int = 200, kind: str = "all") -> list[dict]:
        return [self._file_hit(path, name, is_dir)
                for path, name, is_dir in self._index.search(query, limit, kind=kind)]

    def _palette_mode_search(self, mode: str, query: str) -> list[dict]:
        """Prefix modes: 'c ' terminal history, 'a ' files & folders, 'f ' files, 'd ' folders.
        The query may be words, a *? mask or a regex (index.pattern.parse)."""
        from src.index.pattern import parse
        if mode == "terminal":
            sq = parse(query)
            return [self._terminal_entry(c) for c in self._terminal.history(200) if not query or sq.matches(c)]
        kind = {"all_entries": "all", "files": "files", "dirs": "dirs"}.get(mode)
        if kind and len(query) >= 2:
            return self._file_search(query, 200, kind=kind)
        return []

    def _terminal_entry(self, cmd: str) -> dict:
        return {"category": "Terminal", "label": cmd, "icon": "terminal",
                "run": lambda c=cmd: self._prefill_terminal(c)}

    def _terminal_history_entries(self) -> list[dict]:
        return [self._terminal_entry(c) for c in self._terminal.history(60)]

    def _palette_extra_search(self, query: str) -> list[dict]:
        """Top-level extras for a 3+ character query: a few files and matching commands."""
        from src.index.pattern import parse
        out = self._file_search(query, 6)
        sq = parse(query)
        for cmd in self._terminal.history(60):
            if sq.matches(cmd):
                out.append(self._terminal_entry(cmd))
                if len(out) >= 10:
                    break
        return out

    def _terminal_context(self):
        """Panel state for the terminal placeholders %N %P %T %S %R (core.cmdline)."""
        from src.core.cmdline import CmdContext
        p = self._active_panel_widget
        cur = p.current_entry()
        sel = p.selected_entries()
        return CmdContext(
            cursor_name=cur.name if cur and not cur.is_parent else "",
            panel_path=p.current_path,
            other_path=self._inactive_panel_widget.current_path,
            selected_names=[e.name for e in sel],
            selected_paths=[e.full_path for e in sel],
        )

    def _insert_into_terminal(self, text: str) -> None:
        """Ctrl+Enter / Ctrl+Shift+Enter in a panel: file name / path into the command line."""
        if not self._terminal.isVisible():
            self._act_cmdbar.setChecked(True)
            self._toggle_cmdbar()
        self._terminal.insert_text(text)

    def _insert_cursor_into_terminal(self, full: bool) -> None:
        cur = self._active_panel_widget.current_entry()
        if cur and not cur.is_parent:
            self._insert_into_terminal(cur.full_path if full else cur.name)

    def _show_placeholder_help(self) -> None:
        QMessageBox.information(self, "Terminal placeholders",
            "%N   file under the cursor\n%P   active panel folder\n%T   other panel folder\n"
            "%S   selected entries (names)\n%R   selected entries (full paths)\n"
            "%SI  run the command once per selected entry (name)\n"
            "%RI  run the command once per selected entry (full path)\n%%   literal %\n\n"
            "Ctrl+Enter inserts the file name, Ctrl+Shift+Enter the full path into the command line.")

    def _kill_terminal_session(self) -> None:
        if self._terminal.session_active():
            self._terminal.stop_session()
        else:
            Toast.show_message(self, "No interactive program is running in the terminal", "info")

    def _prefill_terminal(self, cmd: str) -> None:
        """Palette pick from the terminal history: put it into the command line
        and focus it – the user edits / confirms with Enter, nothing runs yet."""
        if not self._terminal.isVisible():
            self._act_cmdbar.setChecked(True)
            self._toggle_cmdbar()
        self._terminal.prefill(cmd)

    def _open_index_settings(self) -> None:
        from .dialogs.index_dialog import IndexSettingsDialog
        dlg = IndexSettingsDialog(self._index.config(), self._index.status_text(), self, on_rescan=self._index.rescan)
        self._index.status_changed.connect(lambda _s: dlg.set_status(self._index.status_text()))
        if dlg.exec():
            self._index.apply_config(dlg.result_config())
            Toast.show_message(self, "Index settings saved", "success")

    def _build_palette_commands(self) -> list[dict]:
        """Every user-facing command, for the command palette (multi-level).

        Convention: a new feature is registered here first (category, label,
        shortcut, run) – the menu bar and the F-key bar are subsets of this.
        Entries with ``children`` (list or callable) open a sub-level.
        """
        from src.core.file_model import SortField, SortOrder

        def e(category: str, label: str, run=None, shortcut: str = "", children=None,
              icon: str | None = None, checked: bool = False, search=None, status=None) -> dict:
            d = {"category": category, "label": label, "run": run, "shortcut": shortcut}
            if children is not None:
                d["children"] = children
            if search is not None:
                d["search"] = search
                d["status"] = status
            if icon:
                d["icon"] = icon
            if checked:
                d["checked"] = True
            return d

        p = self._active_panel_widget
        other = self._inactive_panel_widget

        # --- sort: field, then order (two levels)
        fields = [
            (SortField.NAME, "Name"), (SortField.EXTENSION, "Extension"), (SortField.SIZE, "Size"),
            (SortField.MODIFIED, "Date modified"), (SortField.CREATED, "Date created"),
            (SortField.ATTRIBUTES, "Attributes"),
        ]

        def sort_children() -> list[dict]:
            cur_field, cur_order = p.sort_state

            def orders(field: SortField) -> list[dict]:
                return [
                    e("Order", "Ascending", lambda f=field: p.set_sort(f, SortOrder.ASCENDING), "",
                      checked=(field == cur_field and cur_order == SortOrder.ASCENDING)),
                    e("Order", "Descending", lambda f=field: p.set_sort(f, SortOrder.DESCENDING), "",
                      checked=(field == cur_field and cur_order == SortOrder.DESCENDING)),
                ]
            return [e("Sort by", label, children=(lambda f=field: orders(f)), checked=(field == cur_field))
                    for field, label in fields]

        def drive_children() -> list[dict]:
            cur = self._drive_bar.drive_for(p.current_path)
            return [
                e("Navigate", f"Go to drive {d.letter}:", lambda root=d.root: self._on_drive_clicked(root),
                  f"{d.label or d.drive_type.title()}  ·  {d.free_display} free", icon="drive",
                  checked=(cur is not None and d.root == cur.root))
                for d in self._drive_bar.drives()
            ]

        def tab_children() -> list[dict]:
            return [e("Tab", label, lambda i=i: p.switch_tab(i), checked=(i == p._current_tab_index))
                    for i, label in enumerate(p.tab_labels())]

        def favorite_children() -> list[dict]:
            try:
                favs = self._cfg._db.get_favorites()
            except Exception:
                favs = []
            items = [e("Favourite", f"{f.alias or Path(f.path).name}  ·  {f.path}",
                       lambda path=f.path: p.navigate_to(path), icon="star") for f in favs]
            items.append(e("Favourite", "Add current folder to favourites…", self._open_favorites, "Ctrl+D", icon="plus"))
            return items

        def history_children() -> list[dict]:
            return [e("History", path, lambda path=path: p.navigate_to(path), icon="folder")
                    for path in p.history_paths()]

        def zoom_children() -> list[dict]:
            return [e("Zoom", f"{z} %", lambda z=z: self._apply_zoom(z / 100), "",
                      checked=abs(theme.zoom() - z / 100) < 1e-6)
                    for z in (70, 80, 90, 100, 110, 125, 150, 175, 200)]

        def theme_children() -> list[dict]:
            return [
                e("Theme", "Light", lambda: self._set_theme("light"), icon="sun", checked=not theme.is_dark()),
                e("Theme", "Dark", lambda: self._set_theme("dark"), icon="moon", checked=theme.is_dark()),
            ]

        def shell_children() -> list[dict]:
            return [e("Shell", s, lambda s=s: self._terminal._shell_combo.setCurrentText(s),
                      checked=(self._terminal.current_shell() == s))
                    for s in [self._terminal._shell_combo.itemText(i) for i in range(self._terminal._shell_combo.count())]]

        def swap_panels() -> None:
            a, b = p.current_path, other.current_path
            p.navigate_to(b)
            other.navigate_to(a)

        return [
            # files
            e("Files", "New file", self._new_file, "Shift+F4", icon="file"),
            e("Files", "New folder", self._mkdir, "F7", icon="folder_plus"),
            e("Files", "Rename", self._rename_inline, "F2", icon="rename"),
            e("Files", "Bulk rename…", self._bulk_rename, "Ctrl+M", icon="rename"),
            e("Files", "Copy", self._copy_files, "F5", icon="copy"),
            e("Files", "Move", self._move_files, "F6", icon="arrow_right"),
            e("Files", "Delete", self._delete_files, "F8", icon="trash"),
            e("Files", "View", self._view_file, "F3", icon="eye"),
            e("Files", "Edit", self._edit_file, "F4", icon="edit"),
            e("Files", "Edit in built-in editor", self._edit_builtin, icon="edit"),
            e("Files", "Properties", self._show_properties, "Alt+Enter", icon="info"),
            e("Files", "Compute hash…", self._compute_hash, icon="hash"),
            e("Files", "Calculate size", self._calc_size, icon="scale"),
            e("Files", "Find duplicates…", self._find_duplicates, icon="copy"),
            e("Files", "Compress to ZIP…", lambda: p._compress_to_zip([x.full_path for x in p.selected_entries()]), icon="archive"),
            e("Files", "Open with…", lambda: p._open_with(p.selected_entries()[0].full_path) if p.selected_entries() else None),
            e("Files", "Run as administrator", lambda: p._run_as_admin(p.selected_entries()[0].full_path) if p.selected_entries() else None, icon="shield"),
            # mark
            e("Mark", "Toggle mark under cursor", self._toggle_mark, "Ins / Space"),
            e("Mark", "Select all", self._active_panel_widget_select_all, "Ctrl+A"),
            e("Mark", "Deselect all", self._deselect_all, "Num -"),
            e("Mark", "Invert selection", self._invert_selection, "Num /"),
            e("Mark", "Select by mask…", self._select_by_mask, icon="filter"),
            e("Mark", "Copy names to clipboard", self._copy_names, "Ctrl+Shift+C", icon="clipboard"),
            e("Mark", "Copy full paths to clipboard", self._copy_paths, "Ctrl+Alt+C", icon="clipboard"),
            # navigate
            e("Navigate", "Sort by", children=sort_children, icon="sort"),
            # one plain command per drive ("Go to drive C:"), like Total Commander's drive buttons
            *drive_children(),
            e("Navigate", "Drive menu for left panel", lambda: self._show_drive_menu("left"), "Alt+F1", icon="drive"),
            e("Navigate", "Drive menu for right panel", lambda: self._show_drive_menu("right"), "Alt+F2", icon="drive"),
            e("Navigate", "Switch tab", children=tab_children, icon="columns"),
            e("Navigate", "Favourites", children=favorite_children, icon="star_outline"),
            e("Navigate", "History", children=history_children, shortcut="Alt+Down", icon="clock"),
            e("Navigate", "Refresh", lambda: p.refresh(), "Ctrl+R", icon="refresh"),
            e("Navigate", "Go up", lambda: p._go_up(), "Backspace", icon="arrow_up"),
            e("Navigate", "Back", lambda: p._go_back(), "Alt+Left", icon="arrow_left"),
            e("Navigate", "Forward", lambda: p._go_forward(), "Alt+Right", icon="arrow_right"),
            e("Navigate", "Home folder", lambda: p.navigate_to(str(Path.home())), icon="home"),
            e("Navigate", "Edit path", self._focus_path, "Ctrl+L"),
            e("Navigate", "New tab", lambda: p._new_tab_from_current(), "Ctrl+T", icon="plus"),
            e("Navigate", "Close tab", lambda: p.close_current_tab(), icon="close"),
            e("Navigate", "Search…", self._open_search, "Alt+F7", icon="search"),
            e("Navigate", "Quick filter (this panel)", lambda: p.show_filter(), "Ctrl+S or *", icon="filter",
              checked=bool(p.filter_text)),
            e("Navigate", "Clear quick filter", lambda: p.clear_filter(), "Esc in filter", icon="close"),
            e("Navigate", "Show in Explorer", lambda: p._show_in_explorer(
                p.selected_entries()[0].full_path if p.selected_entries() else p.current_path), icon="explorer"),
            e("Navigate", "Windows shell menu", lambda: p._show_windows_shell_menu(
                [x.full_path for x in p.selected_entries()] or [p.current_path],
                self.mapToGlobal(p.rect().center()))),
            # panels
            e("Panels", "Switch active panel", self._switch_panel, "Tab", icon="columns"),
            e("Panels", "Open this folder in the other panel", lambda: other.navigate_to(p.current_path)),
            e("Panels", "Open the other panel's folder here", lambda: p.navigate_to(other.current_path)),
            e("Panels", "Swap panels", swap_panels),
            # files on disk, terminal history
            e("Navigate", "Find file on disk", search=lambda q: self._file_search(q), icon="search",
              status=self._index.status_text),
            e("Tools", "Terminal history", children=self._terminal_history_entries, icon="terminal"),
            e("Tools", "File index settings…", self._open_index_settings, icon="settings"),
            e("Tools", "External editor…", self._open_editor_settings, icon="settings"),
            e("Tools", "Rescan file index now", lambda: (self._index.rescan(), Toast.show_message(self, "Rescan requested", "info")),
              icon="refresh"),
            # tools
            e("Tools", "Open terminal here", self._open_terminal, icon="terminal"),
            e("Tools", "Focus embedded terminal", self._focus_cmdline, "Ctrl+Down", icon="terminal"),
            e("Tools", "Clear embedded terminal", self._terminal.clear_output, self._cfg.config.cmd_expand_shortcut),
            e("Tools", "Kill interactive terminal program", self._kill_terminal_session, "Ctrl+C in terminal",
              icon="terminal"),
            e("Tools", "Insert file name into command line", lambda: self._insert_cursor_into_terminal(False),
              "Ctrl+Enter", icon="terminal"),
            e("Tools", "Insert full path into command line", lambda: self._insert_cursor_into_terminal(True),
              "Ctrl+Shift+Enter", icon="terminal"),
            e("Tools", "Terminal placeholders (%N %P %T %S %R %SI %RI)", self._show_placeholder_help, icon="info"),
            e("Tools", "Terminal shell", children=shell_children, icon="terminal"),
            e("Tools", "Open as administrator", self._relaunch_admin, icon="shield"),
            e("Tools", "FTP / SFTP connect…", self._open_ftp, icon="network"),
            # show
            e("Show", "Toggle hidden files", self._toggle_hidden, "Ctrl+H", icon="eye_off",
              checked=p.show_hidden),
            e("Show", "Toggle F-keys bar", lambda: (self._act_toolbar.toggle(), self._toggle_toolbar()),
              checked=self._act_toolbar.isChecked()),
            e("Show", "Toggle terminal pane", lambda: (self._act_cmdbar.toggle(), self._toggle_cmdbar()),
              checked=self._act_cmdbar.isChecked()),
            e("Show", "Terminal pane taller (until focus leaves it)", lambda: self._terminal_height_from_palette(+1),
              "Alt++", icon="terminal"),
            e("Show", "Terminal pane shorter (until focus leaves it)", lambda: self._terminal_height_from_palette(-1),
              "Alt+-", icon="terminal"),
            e("Show", "Theme", children=theme_children, icon="moon"),
            e("Show", "Zoom", children=zoom_children, icon="zoom_in"),
            e("Show", "Zoom in", lambda: self._zoom_step(+1), "Ctrl++", icon="zoom_in"),
            e("Show", "Zoom out", lambda: self._zoom_step(-1), "Ctrl+-", icon="zoom_out"),
            e("Show", "Reset zoom", self._reset_zoom, "Ctrl+0"),
            # app
            e("App", "About MortalManager", self._show_about, icon="help"),
            e("App", "Exit", self.close, "Alt+F4"),
        ]

    # ------------------------------------------------------------------ jobs

    def _submit(self, spec: JobSpec, message: str = "") -> str:
        jid = self._job_queue.submit(spec)
        self._job_specs[jid] = spec
        if message:
            self._statusbar.showMessage(message, 4000)
        return jid

    def _on_job_finished(self, jid: str, result: JobResult) -> None:
        spec = self._job_specs.pop(jid, None)
        self._left_panel.refresh()
        self._right_panel.refresh()
        if spec is None:
            return
        verb = {
            JobType.COPY: "Copied", JobType.MOVE: "Moved", JobType.DELETE: "Deleted",
            JobType.RENAME: "Renamed", JobType.MKDIR: "Created", JobType.EXTRACT: "Extracted",
            JobType.COMPRESS: "Compressed",
        }.get(spec.job_type)
        if result.status == JobStatus.CANCELLED:
            Toast.show_message(self, f"Cancelled: {spec.description}", "warning")
        elif result.errors:
            Toast.show_message(self, f"{spec.description}: {len(result.errors)} error(s)", "danger", 6000)
        elif verb and spec.job_type != JobType.MKDIR:
            n = result.files_processed or len(spec.sources)
            Toast.show_message(self, f"{verb} {n} item(s)", "success")

    def _on_job_failed(self, jid: str, error: str) -> None:
        spec = self._job_specs.pop(jid, None)
        desc = spec.description if spec else "Operation"
        Toast.show_message(self, f"{desc} failed: {error}", "danger", 6000)
        self._active_panel_widget.refresh()

    # ------------------------------------------------------------------ file operations

    def _view_file(self) -> None:
        entries = self._active_panel_widget.selected_entries()
        if not entries:
            return
        from src.viewer.file_viewer import FileViewerWindow
        for entry in entries[:3]:  # max 3 viewer windows
            if not entry.is_dir:
                FileViewerWindow(entry.full_path, self).show()

    def _edit_file(self) -> None:
        """F4: the configured external editor (AppConfig.external_editor);
        empty command or a program that cannot be found → built-in editor."""
        entries = self._active_panel_widget.selected_entries()
        paths = [e.full_path for e in entries if not e.is_dir]
        if not paths:
            return
        cmd = self._cfg.config.external_editor.strip()
        if cmd:
            from src.editor import external
            try:
                external.launch(cmd, paths, self._active_panel_widget.current_path)
                return
            except FileNotFoundError as exc:
                Toast.show_message(self, f"{exc} – using the built-in editor", "warning")
            except Exception as exc:
                Toast.show_message(self, f"Editor failed: {exc}", "error")
                return
        self._edit_builtin(paths)

    def _edit_builtin(self, paths: list[str] | None = None) -> None:
        if paths is None:
            paths = [e.full_path for e in self._active_panel_widget.selected_entries() if not e.is_dir]
        if not paths:
            return
        from src.editor.file_editor import FileEditorWindow
        FileEditorWindow(paths, self).exec()

    def _open_editor_settings(self) -> None:
        from .dialogs.editor_dialog import EditorSettingsDialog
        dlg = EditorSettingsDialog(self._cfg.config.external_editor, self)
        if dlg.exec():
            self._cfg.config.external_editor = dlg.command()
            self._cfg.save()
            Toast.show_message(self, "Editor settings saved", "success")

    def _copy_or_move(self, job_type: JobType) -> None:
        sources = self._active_panel_widget.selected_entries()
        if not sources:
            return
        dest = self._inactive_panel_widget.current_path
        from .dialogs.copy_dialog import CopyDialog
        op = "copy" if job_type == JobType.COPY else "move"
        dlg = CopyDialog([e.full_path for e in sources], dest, op, self)
        if dlg.exec():
            spec = JobSpec(
                job_type=job_type,
                sources=[e.full_path for e in sources],
                destination=dlg.destination,
                options={"overwrite": dlg.overwrite},
            )
            verb = "Copying" if job_type == JobType.COPY else "Moving"
            self._submit(spec, f"{verb} {len(sources)} item(s)…")
            self._active_panel_widget.deselect_all()

    def _copy_files(self) -> None:
        self._copy_or_move(JobType.COPY)

    def _move_files(self) -> None:
        self._copy_or_move(JobType.MOVE)

    def _delete_files(self) -> None:
        entries = self._active_panel_widget.selected_entries()
        if not entries:
            return
        use_trash = self._cfg.config.use_trash
        if self._cfg.config.confirm_delete:
            names = "\n".join(e.name for e in entries[:10])
            if len(entries) > 10:
                names += f"\n… and {len(entries) - 10} more"
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Icon.NoIcon)
            box.setWindowTitle("Delete")
            where = "to the Recycle Bin" if use_trash else "permanently"
            box.setText(f"<b>Delete {len(entries)} item(s) {where}?</b>")
            box.setInformativeText(names)
            btn_delete = box.addButton("Delete", QMessageBox.ButtonRole.DestructiveRole)
            btn_delete.setProperty("danger", "true")
            btn_cancel = box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
            btn_cancel.setProperty("quiet", "true")
            box.setDefaultButton(btn_cancel)
            box.setEscapeButton(btn_cancel)
            box.exec()
            if box.clickedButton() is not btn_delete:
                return

        spec = JobSpec(
            job_type=JobType.DELETE,
            sources=[e.full_path for e in entries],
            options={"use_trash": use_trash},
        )
        self._submit(spec, f"Deleting {len(entries)} item(s)…")
        self._active_panel_widget.deselect_all()

    def _mkdir(self) -> None:
        from .dialogs.mkdir_dialog import MkdirDialog
        dlg = MkdirDialog(self._active_panel_widget.current_path, self)
        if dlg.exec():
            self._active_panel_widget.set_pending_cursor(Path(dlg.new_path).name)
            self._submit(JobSpec(job_type=JobType.MKDIR, destination=dlg.new_path))

    def _new_file(self) -> None:
        from .dialogs.mkdir_dialog import NewFileDialog
        dlg = NewFileDialog(self._active_panel_widget.current_path, self)
        if dlg.exec():
            from src.filesystem.local_fs import LocalFileSystemProvider
            fs = LocalFileSystemProvider()
            asyncio.ensure_future(fs.create_file(dlg.new_path), loop=self._loop)
            self._active_panel_widget.set_pending_cursor(Path(dlg.new_path).name)
            QTimer.singleShot(300, self._active_panel_widget.refresh)

    # ------------------------------------------------------------------ search / bulk rename

    def _open_search(self) -> None:
        from .dialogs.search_dialog import SearchDialog
        SearchDialog(self._active_panel_widget.current_path, self).exec()

    def _rename_inline(self) -> None:
        self._active_panel_widget.start_rename()

    def _on_inline_rename(self, old_path: str, new_path: str) -> None:
        if Path(new_path).exists():
            Toast.show_message(self, f"{Path(new_path).name} already exists", "warning")
            return
        self._submit(JobSpec(job_type=JobType.RENAME, sources=[old_path], destination=new_path))

    def _bulk_rename(self) -> None:
        entries = self._active_panel_widget.selected_entries()
        if not entries:
            entries = [e for e in self._active_panel_widget.all_entries() if not e.is_dir and not e.is_parent]
        from .dialogs.bulk_rename_dialog import BulkRenameDialog
        dlg = BulkRenameDialog(entries, self)
        if dlg.exec():
            for old_path, new_path in dlg.rename_pairs:
                self._submit(JobSpec(job_type=JobType.RENAME, sources=[old_path], destination=new_path))

    # ------------------------------------------------------------------ selection helpers

    def _toggle_mark(self) -> None:
        self._active_panel_widget.toggle_current()

    def _active_panel_widget_select_all(self) -> None:
        self._active_panel_widget.select_all()

    def _deselect_all(self) -> None:
        self._active_panel_widget.deselect_all()

    def _invert_selection(self) -> None:
        self._active_panel_widget.invert_selection()

    def _copy_names(self) -> None:
        self._copy_to_clipboard([e.name for e in self._active_panel_widget.selected_entries()], "name")

    def _copy_paths(self) -> None:
        self._copy_to_clipboard([e.full_path for e in self._active_panel_widget.selected_entries()], "path")

    def _copy_to_clipboard(self, lines: list[str], what: str) -> None:
        if not lines:
            return
        QApplication.clipboard().setText("\n".join(lines))
        Toast.show_message(self, f"Copied {len(lines)} {what}(s)", "success", 1800)

    def _select_by_mask(self) -> None:
        from .dialogs.select_mask_dialog import SelectMaskDialog
        dlg = SelectMaskDialog(self)
        if dlg.exec():
            panel = self._active_panel_widget
            panel._current_tab.selection.select_by_mask(
                panel.all_entries(), dlg.pattern, dlg.use_regex, dlg.case_sensitive,
            )
            panel._apply_selection()

    # ------------------------------------------------------------------ misc commands

    def _show_properties(self) -> None:
        entries = self._active_panel_widget.selected_entries()
        if not entries:
            return
        from .dialogs.properties_dialog import PropertiesDialog
        PropertiesDialog([e.full_path for e in entries], self).exec()

    def _compute_hash(self) -> None:
        entries = self._active_panel_widget.selected_entries()
        paths = [e.full_path for e in entries if not e.is_dir]
        if not paths:
            return
        from .dialogs.hash_dialog import HashDialog
        HashDialog(paths, self).exec()

    def _calc_size(self) -> None:
        entries = self._active_panel_widget.selected_entries()
        if not entries:
            return
        spec = JobSpec(job_type=JobType.CALCULATE_SIZE, sources=[e.full_path for e in entries])
        self._submit(spec, "Calculating size…")

    def _find_duplicates(self) -> None:
        from .dialogs.duplicates_dialog import DuplicatesDialog
        DuplicatesDialog(self._active_panel_widget.current_path, self).exec()

    @staticmethod
    def _find_git_bash() -> str | None:
        """Find Git's bash.exe only – never WSL bash."""
        for p in (
            r"C:\Program Files\Git\bin\bash.exe",
            r"C:\Program Files (x86)\Git\bin\bash.exe",
            r"C:\Program Files\Git\usr\bin\bash.exe",
        ):
            if os.path.isfile(p):
                return p
        return None

    def _open_terminal(self) -> None:
        path = self._active_panel_widget.current_path
        shell = self._terminal.current_shell() if hasattr(self, "_terminal") else "PowerShell"
        try:
            if shell == "CMD":
                subprocess.Popen(["cmd.exe", "/K", f'cd /d "{path}"'], creationflags=subprocess.CREATE_NEW_CONSOLE)
            elif shell == "Git Bash":
                bash = self._find_git_bash()
                if bash:
                    subprocess.Popen([bash, "--login", "-i"], cwd=path, creationflags=subprocess.CREATE_NEW_CONSOLE)
                else:
                    Toast.show_message(self, "Git Bash not found.", "warning")
            else:
                subprocess.Popen(["powershell.exe", "-NoExit", "-Command", f"cd '{path}'"],
                                 creationflags=subprocess.CREATE_NEW_CONSOLE)
        except Exception as exc:
            QMessageBox.warning(self, "Error", str(exc))

    def _open_ftp(self) -> None:
        from .dialogs.ftp_dialog import FtpDialog
        FtpDialog(self).exec()

    def _toggle_hidden(self) -> None:
        self._left_panel.toggle_hidden()
        self._right_panel.toggle_hidden()
        self._act_hidden.setChecked(self._active_panel_widget.show_hidden)

    def _toggle_toolbar(self) -> None:
        visible = self._act_toolbar.isChecked()
        self._fkeys_bar.setVisible(visible)
        self._cfg.config.fkeys_bar_visible = visible
        self._save_timer.start()

    def _toggle_cmdbar(self) -> None:
        visible = self._act_cmdbar.isChecked()
        self._terminal.setVisible(visible)
        self._cfg.config.command_bar_visible = visible
        self._save_timer.start()

    def _toggle_theme(self) -> None:
        self._set_theme("light" if self._cfg.config.theme == "dark" else "dark")

    def _set_theme(self, name: str) -> None:
        cfg = self._cfg.config
        if name == cfg.theme:
            return
        cfg.theme = name
        self._act_dark_theme.setChecked(cfg.theme == "dark")
        app = QApplication.instance()
        self.setUpdatesEnabled(False)
        try:
            theme.apply(app, cfg.theme)
            self._retheme_all()
        finally:
            self.setUpdatesEnabled(True)
        self._save_timer.start()

    def _open_favorites(self) -> None:
        from .dialogs.favorites_dialog import FavoritesPickerDialog
        panel = self._active_panel_widget
        dlg = FavoritesPickerDialog(self._cfg._db, panel.current_path, self)
        dlg.navigated.connect(panel.navigate_to)
        dlg.adjustSize()
        panel_geo = panel.rect()
        top_left = panel.mapToGlobal(panel_geo.topLeft())
        dlg.move(top_left.x() + (panel_geo.width() - dlg.width()) // 2,
                 top_left.y() + max(0, (panel_geo.height() - dlg.height()) // 3))
        dlg.exec()

    def _undo_action(self) -> None:
        action = self._undo.pop_undo()
        if not action:
            Toast.show_message(self, "Nothing to undo", None, 1500)
            return
        if action.action_type == UndoActionType.MOVE:
            for new_path, old_path in action.pairs:
                self._submit(JobSpec(job_type=JobType.MOVE, sources=[new_path],
                                     destination=str(Path(old_path).parent)))
        elif action.action_type == UndoActionType.RENAME:
            for new_path, old_path in action.pairs:
                self._submit(JobSpec(job_type=JobType.RENAME, sources=[new_path], destination=old_path))

    def _relaunch_admin(self) -> None:
        import ctypes
        if ctypes.windll.shell32.IsUserAnAdmin():  # type: ignore[attr-defined]
            Toast.show_message(self, "Already running as administrator.", "info")
        else:
            ctypes.windll.shell32.ShellExecuteW(  # type: ignore[attr-defined]
                None, "runas", sys.executable, " ".join(sys.argv), None, 1
            )

    # ------------------------------------------------------------------ path / status handlers

    def _on_path_changed(self, side: str, path: str) -> None:
        if side == self._active_panel:
            if hasattr(self, "_terminal"):
                self._terminal.set_cwd(path)
            if hasattr(self, "_drive_bar"):
                self._drive_bar.set_current_path(path)
                self._update_free_label()

    def _on_status_info(self, side: str, info: str) -> None:
        if side == self._active_panel:
            self._status_msg.setText(info)

    def _update_free_label(self) -> None:
        if not hasattr(self, "_drive_bar"):
            return
        d = self._drive_bar.drive_for(self._active_panel_widget.current_path)
        self._free_label.setText(DriveBar.free_text(d))

    # Enter runs these (Total Commander); F3 still views a .bat / .cmd as text
    _EXEC_EXTENSIONS = frozenset({
        "exe", "com", "scr", "pif", "lnk", "msi", "msix", "appx",       # binaries, shortcuts, installers
        "bat", "cmd", "ps1",                                           # shells (new console window)
        "vbs", "vbe", "js", "jse", "wsf", "wsh", "jar", "ahk",          # script hosts (shell association)
    })

    def _on_entry_open(self, entry: FileEntry) -> None:
        """Enter on a file: executables run, known text/images open in the
        viewer, everything else goes to the shell default."""
        from src.viewer.file_viewer import _IMAGE_EXTENSIONS, _TEXT_EXTENSIONS, FileViewerWindow
        ext = entry.extension.lower()
        if ext in self._EXEC_EXTENSIONS:
            self._run_file(entry.full_path)
        elif ext in _TEXT_EXTENSIONS or ext in _IMAGE_EXTENSIONS:
            FileViewerWindow(entry.full_path, self).show()
        else:
            self._active_panel_widget._open_default(entry.full_path)

    def _run_file(self, path: str) -> None:
        """Run an executable / script from its own folder. Batch files get a
        new console window that stays open at the end so the output can be read."""
        folder = str(Path(path).parent)
        try:
            if path.lower().endswith((".bat", ".cmd")):
                subprocess.Popen(["cmd.exe", "/K", path], cwd=folder,
                                 creationflags=subprocess.CREATE_NEW_CONSOLE)
            elif path.lower().endswith(".ps1"):
                subprocess.Popen(["powershell.exe", "-NoExit", "-ExecutionPolicy", "Bypass", "-File", path],
                                 cwd=folder, creationflags=subprocess.CREATE_NEW_CONSOLE)
            else:
                os.startfile(path, cwd=folder)
        except Exception as exc:
            Toast.show_message(self, f"Cannot run {Path(path).name}: {exc}", "error")

    def _show_about(self) -> None:
        box = QMessageBox(self)
        box.setWindowTitle("About MortalManager")
        variant = "dark" if theme.is_dark() else "light"
        box.setIconPixmap(QPixmap(str(_ASSETS / f"mortalmanager-{variant}-512.png")).scaled(
            theme.px(64), theme.px(64), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        box.setText("<b>MortalManager</b><br>Dual pane file manager for Windows")
        box.setInformativeText("Version 1.0.0 · Python + PySide6 · Solarized look (solarqt)\nInspired by Total Commander")
        box.exec()

    # ------------------------------------------------------------------ geometry

    def _restore_geometry(self) -> None:
        cfg = self._cfg.config
        self.resize(cfg.window_width, cfg.window_height)
        if cfg.window_maximized:
            self.showMaximized()

    def closeEvent(self, event) -> None:  # noqa: N802
        cfg = self._cfg.config
        cfg.window_maximized = self.isMaximized()
        if not self.isMaximized():
            cfg.window_width = self.width()
            cfg.window_height = self.height()
        cfg.zoom = theme.zoom()
        self._cfg.save()
        self._index.stop()
        self._terminal.shutdown()
        self._job_queue.deleteLater()
        event.accept()
