"""Main application window."""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QAction, QFont, QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QMenu,
    QMenuBar,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QStatusBar,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from src.core.file_model import FileEntry, SortField, SortOrder
from src.core.undo import UndoAction, UndoActionType, UndoManager
from src.database.db import DatabaseManager
from src.jobs.job import JobSpec, JobType
from src.jobs.job_queue import JobQueue
from src.settings.config import ConfigManager
from .panel import PanelWidget

logger = logging.getLogger(__name__)


class _CmdInputFilter(QObject):
    """Event filter for the command bar – Tab=path completion, Up/Down=history."""

    def __init__(self, cmd_input: QLineEdit, get_cwd, get_history) -> None:
        super().__init__(cmd_input)
        self._input = cmd_input
        self._get_cwd = get_cwd
        self._get_history = get_history
        self._completions: list[str] = []
        self._comp_idx: int = -1
        self._comp_base: str = ""
        self._hist_idx: int = -1
        self._hist_saved: str = ""   # text before browsing history

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # type: ignore[override]
        if obj is self._input and event.type() == QEvent.Type.KeyPress:
            try:
                key = event.key()
                if key == Qt.Key.Key_Tab:
                    self._complete()
                    return True
                elif key == Qt.Key.Key_Up:
                    self._hist_step(+1)
                    return True
                elif key == Qt.Key.Key_Down:
                    self._hist_step(-1)
                    return True
                else:
                    if key not in (Qt.Key.Key_Shift, Qt.Key.Key_Control, Qt.Key.Key_Alt):
                        self._completions = []
                        self._comp_idx = -1
                        self._hist_idx = -1
            except Exception:
                pass
        return False

    def _hist_step(self, direction: int) -> None:
        """direction=+1 goes older, -1 goes newer."""
        try:
            history = self._get_history()
            if not isinstance(history, list):
                history = list(history)
            if not history:
                return
            if self._hist_idx == -1:
                self._hist_saved = self._input.text()
            new_idx = self._hist_idx + direction
            if new_idx < -1:
                return
            if new_idx >= len(history):
                new_idx = len(history) - 1
            self._hist_idx = new_idx
            if self._hist_idx == -1:
                self._input.setText(self._hist_saved)
            else:
                self._input.setText(str(history[self._hist_idx]))
            self._input.setCursorPosition(len(self._input.text()))
        except Exception as exc:
            print(f"History navigation error: {exc}")

    def _complete(self) -> None:
        import glob
        text = self._input.text()
        cursor = self._input.cursorPosition()
        before = text[:cursor]

        word_start = 0
        for i in range(len(before) - 1, -1, -1):
            if before[i] in (" ", "\t"):
                word_start = i + 1
                break
        word = before[word_start:].strip('"').strip("'")

        if not self._completions or self._comp_base != word:
            self._comp_base = word
            cwd = self._get_cwd()
            if Path(word).is_absolute() or (len(word) >= 2 and word[1] == ":"):
                pattern = word + "*"
                relative_to = None
            else:
                pattern = str(Path(cwd) / word) + "*"
                relative_to = cwd
            matches = sorted(
                glob.glob(pattern),
                key=lambda p: (not Path(p).is_dir(), p.lower()),
            )
            self._completions = []
            for m in matches:
                p = Path(m)
                if relative_to:
                    try:
                        rel = str(p.relative_to(relative_to))
                    except ValueError:
                        rel = m
                else:
                    rel = m
                sep = "\\" if p.is_dir() else ""
                self._completions.append(rel + sep)
            self._comp_idx = -1

        if not self._completions:
            return
        self._comp_idx = (self._comp_idx + 1) % len(self._completions)
        completion = self._completions[self._comp_idx]
        after = text[cursor:]
        self._input.setText(text[:word_start] + completion + after)
        self._input.setCursorPosition(word_start + len(completion))


class MainWindow(QMainWindow):
    """The main dual-pane file manager window."""

    def __init__(self, event_loop: asyncio.AbstractEventLoop) -> None:
        super().__init__()
        self._loop = event_loop
        self._cfg = ConfigManager.get_instance()
        self._job_queue = JobQueue(self)
        self._job_queue.set_event_loop(event_loop)
        self._undo = UndoManager()
        self._active_panel: str = self._cfg.get("active_panel", "left") or "left"

        self._zoom_level: int = 0

        self._build_ui()
        self._build_menus()
        self._build_shortcuts()
        self._restore_geometry()

        # Global event filter for Ctrl+Wheel zoom
        QApplication.instance().installEventFilter(self)

        # Status update timer
        self._status_timer = QTimer(self)
        self._status_timer.timeout.connect(self._update_drive_status)
        self._status_timer.start(5000)
        self._update_drive_status()

    # ------------------------------------------------------------------ UI build

    def _build_ui(self) -> None:
        self.setWindowTitle("MortalManager – Advanced Dual Pane File Manager")
        self.setMinimumSize(800, 500)

        # Central widget
        central = QWidget()
        self.setCentralWidget(central)
        root_layout = QVBoxLayout(central)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # Drive bar
        self._drive_bar = self._build_drive_bar()
        root_layout.addWidget(self._drive_bar)

        # Main splitter (left | right panel)
        self._splitter = QSplitter(Qt.Orientation.Horizontal)

        home = str(Path.home())
        self._left_panel = PanelWidget(home, "left", self)
        self._right_panel = PanelWidget(home, "right", self)

        self._left_panel.request_focus.connect(lambda: self._set_active("left"))
        self._right_panel.request_focus.connect(lambda: self._set_active("right"))
        self._left_panel.entry_activated.connect(self._on_entry_open)
        self._right_panel.entry_activated.connect(self._on_entry_open)
        self._left_panel.path_changed.connect(self._on_left_path_changed)
        self._right_panel.path_changed.connect(self._on_right_path_changed)

        self._splitter.addWidget(self._left_panel)
        self._splitter.addWidget(self._right_panel)
        self._splitter.setSizes([600, 600])

        # F-keys bar
        self._fkeys_bar = self._build_fkeys_bar()
        self._fkeys_bar.setVisible(self._cfg.config.fkeys_bar_visible)

        # Pack panels + fkeys into a container for the vertical splitter
        panels_container = QWidget()
        pc_layout = QVBoxLayout(panels_container)
        pc_layout.setContentsMargins(0, 0, 0, 0)
        pc_layout.setSpacing(0)
        pc_layout.addWidget(self._splitter, stretch=1)
        pc_layout.addWidget(self._fkeys_bar)

        # Embedded terminal (replaces command bar)
        from .terminal_widget import EmbeddedTerminalWidget
        self._terminal = EmbeddedTerminalWidget(home, self._cfg._db, self)
        self._terminal.cwd_changed.connect(
            lambda path: self._active_panel_widget.navigate_to(path)
        )

        # Vertical splitter – drag the handle to resize panels vs terminal
        self._v_splitter = QSplitter(Qt.Orientation.Vertical)
        self._v_splitter.addWidget(panels_container)
        self._v_splitter.addWidget(self._terminal)
        self._v_splitter.setSizes([550, 150])

        root_layout.addWidget(self._v_splitter, stretch=1)

        # Qt status bar
        self._statusbar = QStatusBar()
        self._statusbar.setMaximumHeight(20)
        self.setStatusBar(self._statusbar)

        self._set_active(self._active_panel)
        QTimer.singleShot(0, self._focus_active_panel)

    def _build_drive_bar(self) -> QWidget:
        w = QWidget()
        w.setMaximumHeight(28)
        layout = QHBoxLayout(w)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(2)
        self._drive_buttons: list[QPushButton] = []

        try:
            from src.filesystem.drives import get_all_drives
            drives = get_all_drives()
        except Exception:
            drives = []

        for drive in drives:
            btn = QPushButton(drive.display_name)
            btn.setMaximumHeight(22)
            btn.setFont(QFont("Segoe UI", 8))
            btn.setToolTip(
                f"{drive.drive_type}  {drive.filesystem}\n"
                f"Free: {drive.free_display} / {drive.total_display}"
            )
            root = drive.root
            btn.clicked.connect(lambda checked, r=root: self._active_panel_widget.navigate_to(r))
            layout.addWidget(btn)
            self._drive_buttons.append(btn)

        layout.addStretch()
        return w

    # ------------------------------------------------------------------ menus

    def _build_menus(self) -> None:
        mb = self.menuBar()

        # Files
        file_menu = mb.addMenu("&Files")
        file_menu.addAction("&New File\tShift+F4", self._new_file)
        file_menu.addAction("New &Folder\tF7", self._mkdir)
        file_menu.addSeparator()
        file_menu.addAction("&Favorites\tCtrl+D", self._open_favorites)
        file_menu.addSeparator()
        file_menu.addAction("&Properties\tAlt+Enter", self._show_properties)
        file_menu.addSeparator()
        file_menu.addAction("E&xit\tAlt+F4", self.close)

        # Mark
        mark_menu = mb.addMenu("&Mark")
        mark_menu.addAction("Select &All\tCtrl+A / Num+*", self._active_panel_widget_select_all)
        mark_menu.addAction("&Deselect All\tNum+-", self._deselect_all)
        mark_menu.addAction("&Invert Selection\tNum+/", self._invert_selection)
        mark_menu.addSeparator()
        mark_menu.addAction("Select by &Mask…", self._select_by_mask)

        # Commands
        cmd_menu = mb.addMenu("&Commands")
        cmd_menu.addAction("&Search…\tAlt+F7", self._open_search)
        cmd_menu.addAction("Bulk &Rename…\tCtrl+M", self._bulk_rename)
        cmd_menu.addSeparator()
        cmd_menu.addAction("Calculate Si&ze", self._calc_size)
        cmd_menu.addAction("Compute &Hash…", self._compute_hash)
        cmd_menu.addAction("Find &Duplicates…", self._find_duplicates)
        cmd_menu.addSeparator()
        cmd_menu.addAction("Open &Terminal Here", self._open_terminal)
        cmd_menu.addAction("Open as &Admin", self._relaunch_admin)

        # Network
        net_menu = mb.addMenu("&Network")
        net_menu.addAction("&FTP/SFTP Connect…", self._open_ftp)

        # Show
        show_menu = mb.addMenu("&Show")
        self._act_hidden = show_menu.addAction("Show &Hidden Files\tCtrl+H")
        self._act_hidden.setCheckable(True)
        self._act_hidden.triggered.connect(self._toggle_hidden)
        show_menu.addSeparator()
        self._act_toolbar = show_menu.addAction("F-Keys &Bar")
        self._act_toolbar.setCheckable(True)
        self._act_toolbar.setChecked(self._cfg.config.fkeys_bar_visible)
        self._act_toolbar.triggered.connect(self._toggle_toolbar)
        self._act_cmdbar = show_menu.addAction("&Command Bar")
        self._act_cmdbar.setCheckable(True)
        self._act_cmdbar.setChecked(True)
        self._act_cmdbar.triggered.connect(self._toggle_cmdbar)
        show_menu.addSeparator()
        show_menu.addAction("Zoom &In\tCtrl++", lambda: self._apply_zoom(+1))
        show_menu.addAction("Zoom &Out\tCtrl+-", lambda: self._apply_zoom(-1))
        show_menu.addAction("Reset &Zoom\tCtrl+0", self._reset_zoom)
        show_menu.addSeparator()
        self._act_light_theme = show_menu.addAction("&Light Theme")
        self._act_light_theme.setCheckable(True)
        self._act_light_theme.setChecked(self._cfg.config.theme == "light")
        self._act_light_theme.triggered.connect(self._toggle_theme)

        # Help
        help_menu = mb.addMenu("&Help")
        help_menu.addAction("&About MortalManager", self._show_about)

    def _build_toolbar(self) -> None:
        """Legacy stub – F-keys are now in _build_fkeys_bar (inline widget)."""
        pass

    def _build_fkeys_bar(self) -> QWidget:
        w = QWidget()
        w.setMaximumHeight(26)
        layout = QHBoxLayout(w)
        layout.setContentsMargins(2, 1, 2, 1)
        layout.setSpacing(2)
        self._fkeys_buttons: list[QPushButton] = []
        items: list[tuple[str, object] | None] = [
            ("F3 View", self._view_file),
            ("F4 Edit", self._edit_file),
            ("F5 Copy", self._copy_files),
            ("F6 Move", self._move_files),
            ("F7 Mkdir", self._mkdir),
            ("F8 Del", self._delete_files),
            None,
            ("Refresh", lambda: self._active_panel_widget.refresh()),
            ("Search", self._open_search),
            ("Rename+", self._bulk_rename),
        ]
        for item in items:
            if item is None:
                sep = QFrame()
                sep.setFrameShape(QFrame.Shape.VLine)
                sep.setMaximumHeight(20)
                layout.addWidget(sep)
            else:
                label, handler = item
                btn = QPushButton(label)
                btn.setMaximumHeight(22)
                btn.setFont(QFont("Segoe UI", 8))
                btn.clicked.connect(handler)  # type: ignore[arg-type]
                layout.addWidget(btn)
                self._fkeys_buttons.append(btn)
        layout.addStretch()
        return w

    # ------------------------------------------------------------------ shortcuts

    def _build_shortcuts(self) -> None:
        shortcuts = [
            ("F3",          self._view_file),
            ("F4",          self._edit_file),
            ("F5",          self._copy_files),
            ("F6",          self._move_files),
            ("F7",          self._mkdir),
            ("F8",          self._delete_files),
            ("Alt+F7",      self._open_search),
            ("Ctrl+M",      self._bulk_rename),
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
        ]
        for key, handler in shortcuts:
            sc = QShortcut(QKeySequence(key), self)
            sc.activated.connect(handler)

        # Ctrl+E clears the embedded terminal output
        sc_clear = QShortcut(QKeySequence(self._cfg.config.cmd_expand_shortcut), self)
        sc_clear.activated.connect(self._terminal.clear_output)

        # Zoom shortcuts
        sc_zi = QShortcut(QKeySequence.StandardKey.ZoomIn, self)
        sc_zi.activated.connect(lambda: self._apply_zoom(+1))
        sc_zo = QShortcut(QKeySequence.StandardKey.ZoomOut, self)
        sc_zo.activated.connect(lambda: self._apply_zoom(-1))
        sc_zr = QShortcut(QKeySequence("Ctrl+0"), self)
        sc_zr.activated.connect(self._reset_zoom)

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # type: ignore[override]
        if event.type() == QEvent.Type.Wheel:
            if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                delta = event.angleDelta().y()
                if delta > 0:
                    self._apply_zoom(+1)
                elif delta < 0:
                    self._apply_zoom(-1)
                return True
        return super().eventFilter(obj, event)

    def focusNextPrevChild(self, next: bool) -> bool:  # type: ignore[override]
        # Disable Tab/Shift+Tab focus cycling in the main window.
        # Tab is handled by FileTableView (panel switch) and _CmdTabFilter (completion).
        return False

    # ------------------------------------------------------------------ zoom

    def _apply_zoom(self, delta: int) -> None:
        self._zoom_level = max(-5, min(10, self._zoom_level + delta))
        self._refresh_zoom()

    def _reset_zoom(self) -> None:
        self._zoom_level = 0
        self._refresh_zoom()

    def _refresh_zoom(self) -> None:
        from src.main import apply_theme
        pt = max(6, 9 + self._zoom_level)
        # Re-apply the entire QSS with the new pt baked in – this is the single
        # authoritative source for font sizes across all widget types.
        apply_theme(QApplication.instance(), self._cfg.config.theme, font_pt=pt)
        # Also update the app-wide default for widgets not covered by QSS (e.g. OS dialogs)
        QApplication.instance().setFont(QFont("Segoe UI", pt))
        # Structural: table row height is not a font property in QSS
        self._left_panel.set_font_pt(pt)
        self._right_panel.set_font_pt(pt)

    # ------------------------------------------------------------------ panel helpers

    @property
    def _active_panel_widget(self) -> PanelWidget:
        return self._left_panel if self._active_panel == "left" else self._right_panel

    @property
    def _inactive_panel_widget(self) -> PanelWidget:
        return self._right_panel if self._active_panel == "left" else self._left_panel

    def _set_active(self, side: str) -> None:
        self._active_panel = side
        self._cfg.set("active_panel", side)
        lw = self._left_panel
        rw = self._right_panel
        lw.setStyleSheet(
            "PanelWidget { border: 2px solid #3A7BCA; }" if side == "left"
            else "PanelWidget { border: 1px solid #444; }"
        )
        rw.setStyleSheet(
            "PanelWidget { border: 2px solid #3A7BCA; }" if side == "right"
            else "PanelWidget { border: 1px solid #444; }"
        )
        if hasattr(self, "_terminal"):
            self._terminal.set_cwd(self._active_panel_widget.current_path)
        if self.isVisible():
            self._active_panel_widget.give_focus()

    def _switch_panel(self) -> None:
        new_side = "right" if self._active_panel == "left" else "left"
        self._set_active(new_side)
        self._active_panel_widget.give_focus()

    def _focus_cmdline(self) -> None:
        self._terminal.give_focus()

    def _focus_active_panel(self) -> None:
        """Ctrl+Up – return focus from command bar to the active panel."""
        self._active_panel_widget.give_focus()

    def _open_command_palette(self) -> None:
        dlg = _CommandPaletteDialog(self, self._build_palette_commands())
        dlg.exec()

    def _build_palette_commands(self) -> list[tuple[str, object]]:
        commands: list[tuple[str, object]] = [
            ("Refresh", lambda: self._active_panel_widget.refresh()),
            ("Open Terminal Here", self._open_terminal),
            ("Open as Administrator", self._relaunch_admin),
            ("Search…", self._open_search),
            ("Bulk Rename…", self._bulk_rename),
            ("Toggle Hidden Files", self._toggle_hidden),
            ("Toggle F-Keys Bar", self._toggle_toolbar),
            ("Toggle Command Bar", self._toggle_cmdbar),
            ("Toggle Theme", self._toggle_theme),
            ("Show Properties", self._show_properties),
        ]

        entries = self._active_panel_widget.selected_entries()
        if entries:
            commands.extend([
                ("Copy", self._copy_files),
                ("Move", self._move_files),
                ("Rename…", self._bulk_rename),
                ("Delete", self._delete_files),
                ("Show in Explorer", lambda: self._active_panel_widget._show_in_explorer(str(entries[0].path)) if hasattr(self._active_panel_widget, '_show_in_explorer') else None),
                ("Compute Hash…", self._compute_hash),
                ("Properties", self._show_properties),
            ])
        commands.append(("Windows Shell Menu…", lambda: self._active_panel_widget._show_windows_shell_menu([str(e.path) for e in entries] if entries else [self._active_panel_widget.current_path], self.mapToGlobal(self._active_panel_widget.rect().center()))))
        return commands

    # ------------------------------------------------------------------ file operations

    def _view_file(self) -> None:
        entries = self._active_panel_widget.selected_entries()
        if not entries:
            return
        from src.viewer.file_viewer import FileViewerWindow
        for entry in entries[:3]:  # max 3 viewer windows
            if not entry.is_dir:
                dlg = FileViewerWindow(str(entry.path), self)
                dlg.show()

    def _edit_file(self) -> None:
        entries = self._active_panel_widget.selected_entries()
        paths = [str(e.path) for e in entries if not e.is_dir]
        if not paths:
            return
        from src.editor.file_editor import FileEditorWindow
        dlg = FileEditorWindow(paths, self)
        dlg.exec()

    def _copy_files(self) -> None:
        sources = self._active_panel_widget.selected_entries()
        if not sources:
            return
        dest = self._inactive_panel_widget.current_path
        from .dialogs.copy_dialog import CopyDialog
        dlg = CopyDialog(
            [str(e.path) for e in sources], dest, "copy", self
        )
        if dlg.exec():
            spec = JobSpec(
                job_type=JobType.COPY,
                sources=[str(e.path) for e in sources],
                destination=dlg.destination,
                options={"overwrite": dlg.overwrite},
            )
            jid = self._job_queue.submit(spec)
            self._statusbar.showMessage(f"Copying {len(sources)} item(s)… (job {jid[:8]})")

    def _move_files(self) -> None:
        sources = self._active_panel_widget.selected_entries()
        if not sources:
            return
        dest = self._inactive_panel_widget.current_path
        from .dialogs.copy_dialog import CopyDialog
        dlg = CopyDialog(
            [str(e.path) for e in sources], dest, "move", self
        )
        if dlg.exec():
            spec = JobSpec(
                job_type=JobType.MOVE,
                sources=[str(e.path) for e in sources],
                destination=dlg.destination,
                options={"overwrite": dlg.overwrite},
            )
            jid = self._job_queue.submit(spec)
            self._statusbar.showMessage(f"Moving {len(sources)} item(s)… (job {jid[:8]})")

    def _delete_files(self) -> None:
        entries = self._active_panel_widget.selected_entries()
        if not entries:
            return
        use_trash = self._cfg.config.use_trash
        confirm = self._cfg.config.confirm_delete

        if confirm:
            names = "\n".join(e.name for e in entries[:10])
            if len(entries) > 10:
                names += f"\n… and {len(entries) - 10} more"
            result = QMessageBox.question(
                self,
                "Confirm Delete",
                f"Delete {len(entries)} item(s)?\n\n{names}",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if result != QMessageBox.StandardButton.Yes:
                return

        spec = JobSpec(
            job_type=JobType.DELETE,
            sources=[str(e.path) for e in entries],
            options={"use_trash": use_trash},
        )
        jid = self._job_queue.submit(spec)
        self._job_queue.job_finished.connect(
            lambda jid2, res: self._active_panel_widget.refresh()
            if jid2 == jid else None
        )
        self._statusbar.showMessage(f"Deleting {len(entries)} item(s)…")

    def _mkdir(self) -> None:
        from .dialogs.mkdir_dialog import MkdirDialog
        dlg = MkdirDialog(self._active_panel_widget.current_path, self)
        if dlg.exec():
            spec = JobSpec(
                job_type=JobType.MKDIR,
                destination=dlg.new_path,
            )
            self._job_queue.submit(spec)
            QTimer.singleShot(300, self._active_panel_widget.refresh)

    def _new_file(self) -> None:
        from .dialogs.mkdir_dialog import NewFileDialog
        dlg = NewFileDialog(self._active_panel_widget.current_path, self)
        if dlg.exec():
            import asyncio as _asyncio
            from src.filesystem.local_fs import LocalFileSystemProvider
            fs = LocalFileSystemProvider()
            _asyncio.ensure_future(
                fs.create_file(dlg.new_path), loop=self._loop
            )
            QTimer.singleShot(300, self._active_panel_widget.refresh)

    # ------------------------------------------------------------------ search / bulk rename

    def _open_search(self) -> None:
        from .dialogs.search_dialog import SearchDialog
        dlg = SearchDialog(self._active_panel_widget.current_path, self)
        dlg.exec()

    def _bulk_rename(self) -> None:
        entries = self._active_panel_widget.selected_entries()
        if not entries:
            entries = [
                e for e in self._active_panel_widget.all_entries()
                if not e.is_dir and not e.is_parent
            ]
        from .dialogs.bulk_rename_dialog import BulkRenameDialog
        dlg = BulkRenameDialog(entries, self)
        if dlg.exec():
            for old_path, new_path in dlg.rename_pairs:
                spec = JobSpec(
                    job_type=JobType.RENAME,
                    sources=[old_path],
                    destination=new_path,
                )
                self._job_queue.submit(spec)
            QTimer.singleShot(500, self._active_panel_widget.refresh)

    # ------------------------------------------------------------------ selection helpers

    def _active_panel_widget_select_all(self) -> None:
        self._active_panel_widget.select_all()

    def _deselect_all(self) -> None:
        self._active_panel_widget.deselect_all()

    def _invert_selection(self) -> None:
        self._active_panel_widget.invert_selection()

    def _select_by_mask(self) -> None:
        from .dialogs.select_mask_dialog import SelectMaskDialog
        dlg = SelectMaskDialog(self)
        if dlg.exec():
            self._active_panel_widget._current_tab.selection.select_by_mask(
                self._active_panel_widget.all_entries(),
                dlg.pattern,
                dlg.use_regex,
                dlg.case_sensitive,
            )
            self._active_panel_widget._table.file_model().set_selected(
                set(self._active_panel_widget._current_tab.selection.selected_paths)
            )

    # ------------------------------------------------------------------ misc commands

    def _show_properties(self) -> None:
        entries = self._active_panel_widget.selected_entries()
        if not entries:
            return
        from .dialogs.properties_dialog import PropertiesDialog
        dlg = PropertiesDialog([str(e.path) for e in entries], self)
        dlg.exec()

    def _compute_hash(self) -> None:
        entries = self._active_panel_widget.selected_entries()
        paths = [str(e.path) for e in entries if not e.is_dir]
        if not paths:
            return
        from .dialogs.hash_dialog import HashDialog
        dlg = HashDialog(paths, self)
        dlg.exec()

    def _calc_size(self) -> None:
        entries = self._active_panel_widget.selected_entries()
        if not entries:
            return
        self._statusbar.showMessage("Calculating size…")
        spec = JobSpec(
            job_type=JobType.CALCULATE_SIZE,
            sources=[str(e.path) for e in entries],
        )
        self._job_queue.submit(spec)

    def _find_duplicates(self) -> None:
        from .dialogs.duplicates_dialog import DuplicatesDialog
        dlg = DuplicatesDialog(self._active_panel_widget.current_path, self)
        dlg.exec()

    @staticmethod
    def _find_git_bash() -> str | None:
        """Find Git's bash.exe only – never WSL bash."""
        candidates = [
            r"C:\Program Files\Git\bin\bash.exe",
            r"C:\Program Files (x86)\Git\bin\bash.exe",
            r"C:\Program Files\Git\usr\bin\bash.exe",
        ]
        for p in candidates:
            if os.path.isfile(p):
                return p
        return None  # do NOT fall back to shutil.which('bash') – that could be WSL

    def _open_terminal(self) -> None:
        path = self._active_panel_widget.current_path
        shell = self._terminal.current_shell() if hasattr(self, "_terminal") else "PowerShell"
        try:
            if shell == "CMD":
                subprocess.Popen(
                    ["cmd.exe", "/K", f"cd /d \"{path}\""],
                    creationflags=subprocess.CREATE_NEW_CONSOLE,
                )
            elif shell == "Git Bash":
                bash = self._find_git_bash()
                if bash:
                    subprocess.Popen(
                        [bash, "--login", "-i"],
                        cwd=path,
                        creationflags=subprocess.CREATE_NEW_CONSOLE,
                    )
                else:
                    QMessageBox.warning(self, "Git Bash", "Git Bash not found.")
            else:  # PowerShell
                subprocess.Popen(
                    ["powershell.exe", "-NoExit", "-Command", f"cd '{path}'"],
                    creationflags=subprocess.CREATE_NEW_CONSOLE,
                )
        except Exception as exc:
            QMessageBox.warning(self, "Error", str(exc))

    def _open_ftp(self) -> None:
        from .dialogs.ftp_dialog import FtpDialog
        dlg = FtpDialog(self)
        dlg.exec()

    def _toggle_hidden(self) -> None:
        self._left_panel.toggle_hidden()
        self._right_panel.toggle_hidden()

    def _toggle_toolbar(self) -> None:
        visible = self._act_toolbar.isChecked()
        self._fkeys_bar.setVisible(visible)
        self._cfg.config.fkeys_bar_visible = visible
        self._cfg.save()

    def _toggle_theme(self) -> None:
        from PySide6.QtWidgets import QApplication
        from src.main import apply_theme
        cfg = self._cfg.config
        cfg.theme = "light" if self._act_light_theme.isChecked() else "dark"
        self._cfg.save()
        apply_theme(QApplication.instance(), cfg.theme)

    def _open_favorites(self) -> None:
        from PySide6.QtCore import QPoint
        from .dialogs.favorites_dialog import FavoritesPickerDialog
        panel = self._active_panel_widget
        dlg = FavoritesPickerDialog(
            self._cfg._db,
            panel.current_path,
            self,
        )
        dlg.navigated.connect(panel.navigate_to)
        # Position dialog centred horizontally over the active panel
        dlg.adjustSize()
        panel_geo = panel.rect()
        global_top_left = panel.mapToGlobal(panel_geo.topLeft())
        dlg_x = global_top_left.x() + (panel_geo.width() - dlg.width()) // 2
        dlg_y = global_top_left.y() + max(0, (panel_geo.height() - dlg.height()) // 3)
        dlg.move(dlg_x, dlg_y)
        dlg.exec()

    def _toggle_cmdbar(self) -> None:
        self._terminal.setVisible(self._act_cmdbar.isChecked())

    def _undo_action(self) -> None:
        action = self._undo.pop_undo()
        if not action:
            return
        # Re-apply inverse operation
        if action.action_type == UndoActionType.MOVE:
            for new_path, old_path in action.pairs:
                spec = JobSpec(
                    job_type=JobType.MOVE,
                    sources=[new_path],
                    destination=str(Path(old_path).parent),
                )
                self._job_queue.submit(spec)
        elif action.action_type == UndoActionType.RENAME:
            for new_path, old_path in action.pairs:
                spec = JobSpec(
                    job_type=JobType.RENAME,
                    sources=[new_path],
                    destination=old_path,
                )
                self._job_queue.submit(spec)

    def _relaunch_admin(self) -> None:
        import ctypes
        if ctypes.windll.shell32.IsUserAnAdmin():  # type: ignore[attr-defined]
            QMessageBox.information(self, "Admin", "Already running as administrator.")
        else:
            ctypes.windll.shell32.ShellExecuteW(  # type: ignore[attr-defined]
                None, "runas", sys.executable, " ".join(sys.argv), None, 1
            )

    # ------------------------------------------------------------------ path change handlers

    def _on_left_path_changed(self, path: str) -> None:
        self._statusbar.showMessage(path)
        if self._active_panel == "left" and hasattr(self, "_terminal"):
            self._terminal.set_cwd(path)

    def _on_right_path_changed(self, path: str) -> None:
        self._statusbar.showMessage(path)
        if self._active_panel == "right" and hasattr(self, "_terminal"):
            self._terminal.set_cwd(path)

    def _on_entry_open(self, entry: FileEntry) -> None:
        """Open a non-directory file with appropriate viewer/editor."""
        path = str(entry.path)
        ext = entry.extension.lower()
        # Open in viewer
        from src.viewer.file_viewer import FileViewerWindow
        dlg = FileViewerWindow(path, self)
        dlg.show()

    # ------------------------------------------------------------------ status

    def _update_drive_status(self) -> None:
        try:
            import psutil
            disks = psutil.disk_usage("/")
            self._statusbar.showMessage(
                f"/ Free: {disks.free // (1024**3)} GB"
            )
        except Exception:
            pass

    def _show_about(self) -> None:
        QMessageBox.about(
            self,
            "About MortalManager",
            "MortalManager – Advanced Dual Pane File Manager\n"
            "Version 1.0.0\n\n"
            "Built with Python 3.13 + PySide6\n"
            "Inspired by Total Commander",
        )

    # ------------------------------------------------------------------ geometry

    def _restore_geometry(self) -> None:
        cfg = self._cfg.config
        self.resize(cfg.window_width, cfg.window_height)
        if cfg.window_maximized:
            self.showMaximized()

    def closeEvent(self, event) -> None:  # type: ignore[override]
        cfg = self._cfg.config
        cfg.window_maximized = self.isMaximized()
        if not self.isMaximized():
            cfg.window_width = self.width()
            cfg.window_height = self.height()
        self._cfg.save()
        self._job_queue.deleteLater()
        event.accept()


class _CommandPaletteDialog(QDialog):
    def __init__(self, parent: QWidget, commands: list[tuple[str, object]]) -> None:
        super().__init__(parent)
        self.setWindowTitle("Command Palette")
        self.setMinimumSize(520, 360)
        self._commands = commands

        layout = QVBoxLayout(self)
        self._search = QLineEdit(self)
        self._search.setPlaceholderText("Type a command…")
        self._search.textChanged.connect(self._filter)
        layout.addWidget(self._search)

        self._list = QListWidget(self)
        for name, _ in commands:
            self._list.addItem(name)
        self._list.itemActivated.connect(self._activate_current)
        layout.addWidget(self._list)

        self._search.setFocus()
        if self._list.count() > 0:
            self._list.setCurrentRow(0)

    def _filter(self, text: str) -> None:
        self._list.clear()
        lower = text.lower()
        for name, _ in self._commands:
            if lower in name.lower():
                self._list.addItem(name)
        if self._list.count() > 0:
            self._list.setCurrentRow(0)

    def keyPressEvent(self, event) -> None:  # type: ignore[override]
        if event.key() in (Qt.Key.Key_Escape, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self._activate_current()
                return
        super().keyPressEvent(event)

    def _activate_current(self) -> None:
        item = self._list.currentItem()
        if not item:
            return
        name = item.text()
        for cmd_name, handler in self._commands:
            if cmd_name == name and callable(handler):
                handler()
                break
        self.accept()
