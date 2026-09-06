"""Dual-pane file panel widget.

Each panel contains:
- Address bar (path input + history dropdown)
- Tab bar (multiple directory tabs)
- File table (FileTableView)
- Drive buttons bar
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
from pathlib import Path

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTabBar,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from src.core.file_model import FileEntry, SortField, SortOrder
from src.core.history import NavigationHistory
from src.core.selection import SelectionManager
from src.filesystem.local_fs import LocalFileSystemProvider
from src.filesystem.watcher import DirectoryWatcher
from src.settings.config import ConfigManager
from .file_table import FileTableModel, FileTableView

logger = logging.getLogger(__name__)


class _Tab:
    """State for a single tab within a panel."""

    def __init__(self, path: str) -> None:
        self.path = path
        self.history = NavigationHistory()
        self.selection = SelectionManager()
        self.history.push(path)
        self.locked = False
        self.sort_field = SortField.NAME
        self.sort_order = SortOrder.ASCENDING
        self.show_hidden = False
        self.cursor_name: str = ""


class PanelWidget(QWidget):
    """One side of the dual-pane manager."""

    path_changed = Signal(str)           # new path
    entry_activated = Signal(object)     # FileEntry – directory or file
    status_info = Signal(str)            # status bar text
    request_focus = Signal()             # panel wants keyboard focus

    def __init__(
        self,
        initial_path: str,
        panel_id: str = "left",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._panel_id = panel_id
        self._fs = LocalFileSystemProvider()
        self._tabs: list[_Tab] = []
        self._current_tab_index = -1
        self._loading = False
        self._watcher = DirectoryWatcher(self)
        self._watcher.directory_changed.connect(self._on_dir_changed)
        self._vcs_root: str | None = None
        self._vcs_type: str | None = None
        self._vcs_status: dict[str, str] = {}

        self._build_ui()
        self._open_tab(initial_path)

    # ------------------------------------------------------------------ UI build

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(2)

        # Path bar
        path_bar = QHBoxLayout()
        path_bar.setSpacing(2)

        self._btn_back = QToolButton()
        self._btn_back.setText("◄")
        self._btn_back.setToolTip("Back (Alt+Left)")
        self._btn_back.clicked.connect(self._go_back)

        self._btn_forward = QToolButton()
        self._btn_forward.setText("►")
        self._btn_forward.setToolTip("Forward (Alt+Right)")
        self._btn_forward.clicked.connect(self._go_forward)

        self._btn_up = QToolButton()
        self._btn_up.setText("▲")
        self._btn_up.setToolTip("Up (Backspace)")
        self._btn_up.clicked.connect(self._go_up)

        self._path_edit = QLineEdit()
        self._path_edit.returnPressed.connect(self._on_path_entered)
        self._path_edit.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )

        self._btn_refresh = QToolButton()
        self._btn_refresh.setText("⟳")
        self._btn_refresh.setToolTip("Refresh (Ctrl+R)")
        self._btn_refresh.clicked.connect(self.refresh)

        path_bar.addWidget(self._btn_back)
        path_bar.addWidget(self._btn_forward)
        path_bar.addWidget(self._btn_up)
        path_bar.addWidget(self._path_edit)
        path_bar.addWidget(self._btn_refresh)
        layout.addLayout(path_bar)

        # Tab bar
        self._tab_bar = QTabBar()
        self._tab_bar.setTabsClosable(True)
        self._tab_bar.setMovable(True)
        self._tab_bar.currentChanged.connect(self._on_tab_changed)
        self._tab_bar.tabCloseRequested.connect(self._close_tab)

        tab_row = QHBoxLayout()
        tab_row.setSpacing(2)
        tab_row.addWidget(self._tab_bar)
        btn_new_tab = QToolButton()
        btn_new_tab.setText("+")
        btn_new_tab.setToolTip("New Tab")
        btn_new_tab.clicked.connect(self._new_tab_from_current)
        tab_row.addWidget(btn_new_tab)
        layout.addLayout(tab_row)

        # File table
        self._table = FileTableView(self)
        self._table.entry_activated.connect(self._on_entry_activated)
        self._table.space_pressed.connect(self._toggle_selection)
        self._table.mousePressEvent = self._on_table_mouse_press  # type: ignore[method-assign]
        layout.addWidget(self._table)

        # Info bar
        self._info_label = QLabel("Ready")
        layout.addWidget(self._info_label)

        # Context menu on right-click
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._show_context_menu)

        # Click to focus
        self._table.clicked.connect(lambda: self.request_focus.emit())

    # ------------------------------------------------------------------ tab management

    def _open_tab(self, path: str) -> None:
        tab = _Tab(path)
        self._tabs.append(tab)
        idx = len(self._tabs) - 1
        self._tab_bar.addTab(Path(path).name or path)
        self._tab_bar.setCurrentIndex(idx)
        self._current_tab_index = idx
        self._navigate_to(path, push_history=False)

    def _new_tab_from_current(self) -> None:
        path = self.current_path
        self._open_tab(path)

    def _close_tab(self, index: int) -> None:
        if len(self._tabs) <= 1:
            return
        self._tabs.pop(index)
        self._tab_bar.removeTab(index)
        if self._current_tab_index >= len(self._tabs):
            self._current_tab_index = len(self._tabs) - 1
        self._on_tab_changed(self._tab_bar.currentIndex())

    def _on_tab_changed(self, index: int) -> None:
        if index < 0 or index >= len(self._tabs):
            return
        self._current_tab_index = index
        tab = self._tabs[index]
        self._navigate_to(tab.path, push_history=False)

    @property
    def _current_tab(self) -> _Tab:
        return self._tabs[self._current_tab_index]

    # ------------------------------------------------------------------ navigation

    @property
    def current_path(self) -> str:
        if self._current_tab_index < 0:
            return str(Path.home())
        return self._current_tab.path

    def navigate_to(self, path: str) -> None:
        self._navigate_to(path, push_history=True)

    def _navigate_to(self, path: str, push_history: bool = True) -> None:
        if self._loading:
            return
        resolved = str(Path(path).resolve())
        if not Path(resolved).is_dir():
            return
        if push_history:
            self._current_tab.history.push(resolved)
        self._current_tab.path = resolved
        self._path_edit.setText(resolved)
        # Update tab label
        label = Path(resolved).name or resolved
        self._tab_bar.setTabText(self._current_tab_index, label)
        self._watcher.watch(resolved)
        self._detect_vcs_root(resolved)
        self._load_directory(resolved)
        self.path_changed.emit(resolved)
        try:
            cfg = ConfigManager.get_instance()
            cfg._db.add_path_history(resolved)
        except Exception:
            pass

    def _load_directory(self, path: str) -> None:
        self._loading = True
        show_hidden = self._current_tab.show_hidden
        loop = self._get_loop()
        if loop and loop.is_running():
            asyncio.ensure_future(
                self._load_async(path, show_hidden), loop=loop
            )
        else:
            # Fallback: synchronous load (should not normally happen)
            self._load_sync(path, show_hidden)

    def _get_loop(self) -> asyncio.AbstractEventLoop | None:
        try:
            return asyncio.get_event_loop()
        except RuntimeError:
            return None

    async def _load_async(self, path: str, show_hidden: bool) -> None:
        try:
            entries = await self._fs.list_directory(path, show_hidden=show_hidden)
            await self._annotate_vcs_entries_async(entries)
            self._apply_entries(entries, path)
        except Exception as exc:
            logger.warning("Failed to load %s: %s", path, exc)
            self._info_label.setText(f"Error: {exc}")
        finally:
            self._loading = False

    def _load_sync(self, path: str, show_hidden: bool) -> None:
        try:
            loop = asyncio.new_event_loop()
            entries = loop.run_until_complete(
                self._fs.list_directory(path, show_hidden=show_hidden)
            )
            self._annotate_vcs_entries(entries)
            self._apply_entries(entries, path)
        except Exception as exc:
            logger.warning("Failed to load %s: %s", path, exc)
            self._info_label.setText(f"Error: {exc}")
        finally:
            self._loading = False

    async def _annotate_vcs_entries_async(self, entries: list[FileEntry]) -> None:
        loop = self._get_loop()
        if loop:
            await loop.run_in_executor(None, self._annotate_vcs_entries, entries)
        else:
            self._annotate_vcs_entries(entries)

    def _annotate_vcs_entries(self, entries: list[FileEntry]) -> None:
        if not self._vcs_root or not self._vcs_type:
            return
        root = Path(self._vcs_root)
        status_map: dict[str, str] = {}
        if self._vcs_type == "git":
            status_map = self._git_status_map()
        elif self._vcs_type == "svn":
            status_map = self._svn_status_map()

        for entry in entries:
            if entry.is_parent:
                continue
            try:
                if Path(entry.path).resolve().is_relative_to(root):
                    entry.vcs_type = self._vcs_type
                    entry.vcs_state = status_map.get(str(entry.path), "clean")
            except Exception:
                pass

    def _detect_vcs_root(self, resolved: str) -> None:
        self._vcs_root = None
        self._vcs_type = None
        self._vcs_status = {}
        try:
            git = subprocess.run(
                ["git", "-C", resolved, "rev-parse", "--show-toplevel"],
                capture_output=True,
                text=True,
                shell=False,
                timeout=5,
            )
            if git.returncode == 0:
                self._vcs_root = git.stdout.strip()
                self._vcs_type = "git"
                return
        except Exception:
            pass

        try:
            svn = subprocess.run(
                ["svn", "info", "--show-item", "wc-root"],
                cwd=resolved,
                capture_output=True,
                text=True,
                shell=False,
                timeout=5,
            )
            if svn.returncode == 0 and svn.stdout.strip():
                self._vcs_root = svn.stdout.strip()
                self._vcs_type = "svn"
                return
        except Exception:
            pass

        try:
            svn = subprocess.run(
                ["svn", "info"],
                cwd=resolved,
                capture_output=True,
                text=True,
                shell=False,
                timeout=5,
            )
            if svn.returncode == 0:
                for line in svn.stdout.splitlines():
                    if line.startswith("Working Copy Root Path:"):
                        self._vcs_root = line.partition(":")[2].strip()
                        self._vcs_type = "svn"
                        return
        except Exception:
            pass

    def _git_status_map(self) -> dict[str, str]:
        status_map: dict[str, str] = {}
        if not self._vcs_root:
            return status_map
        try:
            result = subprocess.run(
                ["git", "-C", self._vcs_root, "status", "--short", "--untracked-files=normal"],
                capture_output=True,
                text=True,
                shell=False,
                timeout=10,
            )
            if result.returncode != 0:
                return status_map
            for line in result.stdout.splitlines():
                if not line.strip():
                    continue
                code = line[:2].strip()
                path = line[3:].strip()
                status_map[str(Path(self._vcs_root) / path)] = code
        except Exception:
            pass
        return status_map

    def _svn_status_map(self) -> dict[str, str]:
        status_map: dict[str, str] = {}
        if not self._vcs_root:
            return status_map
        try:
            result = subprocess.run(
                ["svn", "status"],
                cwd=self._vcs_root,
                capture_output=True,
                text=True,
                shell=False,
                timeout=10,
            )
            if result.returncode != 0:
                return status_map
            for line in result.stdout.splitlines():
                if not line.strip():
                    continue
                code = line[0]
                path = line[8:].strip()
                status_map[str(Path(self._vcs_root) / path)] = code
        except Exception:
            pass
        return status_map

    def _apply_entries(self, entries: list[FileEntry], path: str) -> None:
        entries = self._sort_entries(entries)
        self._table.file_model().set_entries(entries)
        # Restore selection overlay
        sel_paths = self._current_tab.selection.selected_paths
        self._table.file_model().set_selected(set(sel_paths))
        # Restore cursor
        cursor_name = self._current_tab.cursor_name
        if cursor_name:
            for row, e in enumerate(entries):
                if e.name == cursor_name:
                    self._table.selectRow(row)
                    break
        else:
            if entries:
                self._table.selectRow(0)
        self._update_info(entries)

    def _sort_entries(self, entries: list[FileEntry]) -> list[FileEntry]:
        tab = self._current_tab
        # Keep ".." always at top
        parent_entries = [e for e in entries if e.is_parent]
        dir_entries = [e for e in entries if e.is_dir and not e.is_parent]
        file_entries = [e for e in entries if not e.is_dir]

        key_map = {
            SortField.NAME: lambda e: e.name.lower(),
            SortField.EXTENSION: lambda e: e.extension.lower(),
            SortField.SIZE: lambda e: e.size,
            SortField.MODIFIED: lambda e: e.modified,
            SortField.CREATED: lambda e: e.created,
        }
        key_fn = key_map.get(tab.sort_field, lambda e: e.name.lower())
        rev = tab.sort_order == SortOrder.DESCENDING

        dir_entries.sort(key=key_fn, reverse=rev)
        file_entries.sort(key=key_fn, reverse=rev)
        return parent_entries + dir_entries + file_entries

    def _update_info(self, entries: list[FileEntry]) -> None:
        dirs = sum(1 for e in entries if e.is_dir and not e.is_parent)
        files = sum(1 for e in entries if not e.is_dir)
        sel_count = self._current_tab.selection.count
        sel_size = self._current_tab.selection.total_size(entries)
        info = f"{dirs} dir(s), {files} file(s)"
        if sel_count:
            from src.core.file_model import format_size
            info += f"  |  {sel_count} selected ({format_size(sel_size)})"
        self._info_label.setText(info)
        self.status_info.emit(info)

    # ------------------------------------------------------------------ navigation helpers

    def _go_back(self) -> None:
        entry = self._current_tab.history.go_back()
        if entry:
            self._navigate_to(entry.path, push_history=False)

    def _go_forward(self) -> None:
        entry = self._current_tab.history.go_forward()
        if entry:
            self._navigate_to(entry.path, push_history=False)

    def _go_up(self) -> None:
        parent = self._fs.get_parent(self.current_path)
        if parent:
            # Remember current dir name to restore cursor
            current_name = Path(self.current_path).name
            self._navigate_to(parent, push_history=True)
            self._current_tab.cursor_name = current_name

    def _on_path_entered(self) -> None:
        path = self._path_edit.text().strip()
        if path:
            self._navigate_to(path)

    def _on_dir_changed(self, path: str) -> None:
        """Auto-refresh when filesystem notifies of changes."""
        if path == self.current_path:
            QTimer.singleShot(300, self.refresh)

    def refresh(self) -> None:
        self._navigate_to(self.current_path, push_history=False)

    # ------------------------------------------------------------------ entry handling

    def _on_entry_activated(self, entry: FileEntry) -> None:
        if entry.is_dir or entry.is_parent:
            self._navigate_to(str(entry.path))
        else:
            self.entry_activated.emit(entry)

    def _toggle_selection(self, entry: FileEntry) -> None:
        sel = self._current_tab.selection
        sel.toggle(entry)
        self._table.file_model().set_selected(set(sel.selected_paths))
        self._update_info(self._table.file_model().get_all_entries())

    def _on_table_mouse_press(self, event) -> None:  # type: ignore[override]
        FileTableView.mousePressEvent(self._table, event)
        self.request_focus.emit()

    # ------------------------------------------------------------------ public helpers

    def current_entry(self) -> FileEntry | None:
        return self._table.current_entry()

    def selected_entries(self) -> list[FileEntry]:
        sel = self._current_tab.selection
        entries = self._table.file_model().get_all_entries()
        selected = sel.selected_entries(entries)
        if not selected:
            cur = self.current_entry()
            if cur and not cur.is_parent:
                return [cur]
        return selected

    def all_entries(self) -> list[FileEntry]:
        return self._table.file_model().get_all_entries()

    def select_all(self) -> None:
        sel = self._current_tab.selection
        sel.select_all(self.all_entries())
        self._table.file_model().set_selected(set(sel.selected_paths))
        self._update_info(self.all_entries())

    def deselect_all(self) -> None:
        self._current_tab.selection.clear()
        self._table.file_model().set_selected(set())
        self._update_info(self.all_entries())

    def invert_selection(self) -> None:
        sel = self._current_tab.selection
        sel.invert(self.all_entries())
        self._table.file_model().set_selected(set(sel.selected_paths))
        self._update_info(self.all_entries())

    def toggle_hidden(self) -> None:
        self._current_tab.show_hidden = not self._current_tab.show_hidden
        self.refresh()

    def set_sort(self, field: SortField, order: SortOrder) -> None:
        self._current_tab.sort_field = field
        self._current_tab.sort_order = order
        entries = self._table.file_model().get_all_entries()
        sorted_entries = self._sort_entries(entries)
        self._table.file_model().set_entries(sorted_entries)

    def give_focus(self) -> None:
        self._table.setFocus()

    def set_font_pt(self, pt: int) -> None:
        """Update row height when global zoom level changes."""
        row_h = max(18, pt + 8)
        self._table.verticalHeader().setDefaultSectionSize(row_h)

    # ------------------------------------------------------------------ context menu

    def _show_context_menu(self, _pos: object) -> None:
        """Rich right-click menu: Commander operations + Windows shell menu."""
        from PySide6.QtGui import QCursor
        from PySide6.QtWidgets import QApplication, QMenu

        self.request_focus.emit()
        entries = self.selected_entries()
        paths = [str(e.path) for e in entries if not e.is_parent]
        mw = QApplication.activeWindow()

        menu = QMenu(self)

        if not paths:
            # right-click on empty space
            if hasattr(mw, '_mkdir'):
                menu.addAction("New &Folder\tF7").triggered.connect(mw._mkdir)
            menu.addAction("&Refresh\tCtrl+R").triggered.connect(self.refresh)
            menu.addSeparator()
            menu.addAction("Open &Terminal Here").triggered.connect(self._open_terminal_here)
            menu.addAction("Open in &Explorer").triggered.connect(
                lambda: self._show_in_explorer(self.current_path)
            )
            from PySide6.QtCore import QPoint
            _gp = self._table.viewport().mapToGlobal(_pos) if isinstance(_pos, QPoint) and _pos.x() >= 0 else QCursor.pos()
            menu.exec(_gp)
            return

        is_single = len(paths) == 1
        first = paths[0]
        first_entry = entries[0]
        is_dir = first_entry.is_dir and not first_entry.is_parent
        ext = first_entry.extension.lower()

        # ---- Open
        if is_single:
            if is_dir:
                menu.addAction("&Open\tEnter").triggered.connect(
                    lambda: self.navigate_to(first)
                )
            else:
                menu.addAction("&Open\tEnter").triggered.connect(
                    lambda: self._open_default(first)
                )
                menu.addAction("Open &With\u2026").triggered.connect(
                    lambda: self._open_with(first)
                )
                if hasattr(mw, '_edit_file'):
                    menu.addAction("&Edit\tF4").triggered.connect(mw._edit_file)
                if hasattr(mw, '_view_file'):
                    menu.addAction("&View\tF3").triggered.connect(mw._view_file)
                menu.addSeparator()
                menu.addAction("Run as &Administrator").triggered.connect(
                    lambda: self._run_as_admin(first)
                )
            menu.addSeparator()

        # ---- Commander file ops
        if hasattr(mw, '_copy_files'):
            menu.addAction("&Copy\tF5").triggered.connect(mw._copy_files)
        if hasattr(mw, '_move_files'):
            menu.addAction("&Move\tF6").triggered.connect(mw._move_files)
        menu.addSeparator()
        if hasattr(mw, '_bulk_rename'):
            menu.addAction("Re&name\u2026\tCtrl+M").triggered.connect(mw._bulk_rename)
        if hasattr(mw, '_delete_files'):
            menu.addAction("&Delete\tF8").triggered.connect(mw._delete_files)
        if hasattr(mw, '_mkdir'):
            menu.addAction("New &Folder\tF7").triggered.connect(mw._mkdir)
        menu.addSeparator()

        # ---- Clipboard
        if is_single:
            menu.addAction("Copy &Path").triggered.connect(
                lambda: QApplication.clipboard().setText(first)
            )
            menu.addAction("Copy Na&me").triggered.connect(
                lambda: QApplication.clipboard().setText(Path(first).name)
            )
            menu.addSeparator()

        # ---- Navigation
        menu.addAction("Show in &Explorer").triggered.connect(
            lambda: self._show_in_explorer(first if is_single else self.current_path)
        )
        menu.addAction("Open &Terminal Here").triggered.connect(self._open_terminal_here)
        menu.addSeparator()

        # ---- Info
        if not is_dir and hasattr(mw, '_compute_hash'):
            menu.addAction("Compute &Hash\u2026\tAlt+H").triggered.connect(mw._compute_hash)
        if hasattr(mw, '_show_properties'):
            menu.addAction("&Properties\tAlt+Enter").triggered.connect(mw._show_properties)
        menu.addSeparator()

        # ---- Archive
        archive_exts = {'.zip', '.7z', '.tar', '.gz', '.bz2', '.xz', '.rar'}
        if is_single and ext in archive_exts:
            menu.addAction("E&xtract Here").triggered.connect(
                lambda: self._extract_here(first)
            )
        menu.addAction("Compress to &ZIP\u2026").triggered.connect(
            lambda: self._compress_to_zip(paths)
        )
        menu.addSeparator()

        # ---- Native Windows shell menu
        self._populate_windows_shell_menu(menu, paths)

        from PySide6.QtCore import QPoint
        _gp = self._table.viewport().mapToGlobal(_pos) if isinstance(_pos, QPoint) and _pos.x() >= 0 else QCursor.pos()
        menu.exec(_gp)

    # ---- context menu helpers

    def _populate_windows_shell_menu(self, menu, paths: list[str]) -> None:
        inserted = False
        try:
            import pythoncom
            import win32con
            import win32gui
            from win32com.shell import shell, shellcon

            if not paths:
                return
            parent_path = str(Path(paths[0]).parent)
            hwnd = int(self.window().winId())

            pythoncom.CoInitialize()
            try:
                desktop = shell.SHGetDesktopFolder()
                parent_pidl = shell.SHILCreateFromPath(parent_path, 0)[0]
                folder = desktop.BindToObject(
                    parent_pidl, None, shell.IID_IShellFolder
                )
                child_pidls = []
                for p in paths:
                    try:
                        result = folder.ParseDisplayName(hwnd, None, Path(p).name)
                        pidl = None
                        if isinstance(result, tuple):
                            pidl = result[1] if len(result) >= 2 else result[0] if result else None
                        else:
                            pidl = result
                        if isinstance(pidl, list) and pidl:
                            pidl = pidl[-1:]
                        if pidl is not None:
                            child_pidls.append(pidl)
                    except Exception:
                        pass
                if child_pidls:
                    icm = folder.GetUIObjectOf(
                        hwnd, child_pidls, shell.IID_IContextMenu, 0
                    )
                    if isinstance(icm, tuple):
                        icm = icm[-1]
                    hmenu = win32gui.CreatePopupMenu()
                    icm.QueryContextMenu(
                        hmenu,
                        0,
                        1,
                        0x7FFF,
                        shellcon.CMF_NORMAL | shellcon.CMF_EXPLORE | shellcon.CMF_CANRENAME,
                    )
                    inserted = self._add_shell_menu_items(menu, hmenu, icm, hwnd, parent_path)
                    win32gui.DestroyMenu(hmenu)
            finally:
                pythoncom.CoUninitialize()
        except Exception:
            inserted = False

        if not inserted:
            from PySide6.QtGui import QCursor
            menu.addSeparator()
            menu.addAction("Windows Shell Menu…", lambda: self._show_windows_shell_menu(paths, QCursor.pos()))

    def _add_shell_menu_items(self, menu, hmenu, icm, hwnd, parent_path: str) -> bool:
        import ctypes
        import win32con
        import win32gui

        MIIM_BITMAP = 0x00000080

        class _MIIW(ctypes.Structure):
            _fields_ = [
                ("cbSize", ctypes.c_uint),
                ("fMask", ctypes.c_uint),
                ("fType", ctypes.c_uint),
                ("fState", ctypes.c_uint),
                ("wID", ctypes.c_uint),
                ("hSubMenu", ctypes.c_void_p),
                ("hbmpChecked", ctypes.c_void_p),
                ("hbmpUnchecked", ctypes.c_void_p),
                ("dwItemData", ctypes.c_size_t),
                ("dwTypeData", ctypes.c_void_p),
                ("cch", ctypes.c_uint),
                ("hbmpItem", ctypes.c_void_p),
            ]

        def _get_menu_string(menu_handle: int, position: int) -> str:
            buf = ctypes.create_unicode_buffer(256)
            length = ctypes.windll.user32.GetMenuStringW(
                menu_handle, position, buf, ctypes.sizeof(buf) // 2, win32con.MF_BYPOSITION
            )
            return buf.value[:length] if length > 0 else ""

        def _get_item_icon(menu_handle: int, position: int):
            mii = _MIIW()
            mii.cbSize = ctypes.sizeof(_MIIW)
            mii.fMask = MIIM_BITMAP
            if ctypes.windll.user32.GetMenuItemInfoW(menu_handle, position, True, ctypes.byref(mii)):
                hbmp = mii.hbmpItem
                if hbmp is not None and 8 < hbmp < (1 << 48):
                    return self._hbitmap_to_qicon(hbmp)
            return None

        count = win32gui.GetMenuItemCount(hmenu)
        inserted = False
        for idx in range(count):
            state = win32gui.GetMenuState(hmenu, idx, win32con.MF_BYPOSITION)
            if state & win32con.MF_SEPARATOR:
                menu.addSeparator()
                inserted = True
                continue
            text = _get_menu_string(hmenu, idx)
            submenu = win32gui.GetSubMenu(hmenu, idx)
            if submenu:
                sub = menu.addMenu(text)
                icon = _get_item_icon(hmenu, idx)
                if icon:
                    sub.setIcon(icon)
                if self._add_shell_menu_items(sub, submenu, icm, hwnd, parent_path):
                    inserted = True
            else:
                cmd_id = win32gui.GetMenuItemID(hmenu, idx)
                if cmd_id == -1:
                    continue
                action = menu.addAction(text)
                icon = _get_item_icon(hmenu, idx)
                if icon:
                    action.setIcon(icon)
                action.triggered.connect(
                    lambda checked=False, cmd=cmd_id, icm=icm, hwnd=hwnd, parent_path=parent_path: self._invoke_shell_command(icm, hwnd, cmd, parent_path)
                )
                inserted = True
        return inserted

    @staticmethod
    def _hbitmap_to_qicon(hbmp: int):
        """Convert a GDI HBITMAP to QIcon via GetDIBits. Returns None on failure."""
        import ctypes
        import ctypes.wintypes as wt
        from PySide6.QtGui import QIcon, QImage, QPixmap

        try:
            class _BM(ctypes.Structure):
                _fields_ = [
                    ("bmType", wt.LONG), ("bmWidth", wt.LONG), ("bmHeight", wt.LONG),
                    ("bmWidthBytes", wt.LONG), ("bmPlanes", wt.WORD), ("bmBitsPixel", wt.WORD),
                    ("bmBits", ctypes.c_void_p),
                ]

            bm = _BM()
            ctypes.windll.gdi32.GetObjectW(hbmp, ctypes.sizeof(bm), ctypes.byref(bm))
            w, h = bm.bmWidth, abs(bm.bmHeight)
            if w <= 0 or h <= 0 or w > 128 or h > 128:
                return None

            class _BMIH(ctypes.Structure):
                _fields_ = [
                    ("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG),
                    ("biPlanes", wt.WORD), ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
                    ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", wt.LONG),
                    ("biYPelsPerMeter", wt.LONG), ("biClrUsed", wt.DWORD),
                    ("biClrImportant", wt.DWORD),
                ]

            bi = _BMIH()
            bi.biSize = ctypes.sizeof(_BMIH)
            bi.biWidth = w
            bi.biHeight = -h  # top-down DIB
            bi.biPlanes = 1
            bi.biBitCount = 32
            bi.biCompression = 0  # BI_RGB

            buf = (ctypes.c_uint8 * (w * h * 4))()
            hdc = ctypes.windll.user32.GetDC(0)
            rows = ctypes.windll.gdi32.GetDIBits(hdc, hbmp, 0, h, buf, ctypes.byref(bi), 0)
            ctypes.windll.user32.ReleaseDC(0, hdc)
            if rows <= 0:
                return None

            raw = bytes(buf)
            # Detect alpha channel: Windows BGRA bytes, Qt ARGB32 layout matches on LE
            has_alpha = any(raw[i + 3] > 0 for i in range(0, len(raw), 4))
            fmt = (QImage.Format.Format_ARGB32_Premultiplied if has_alpha
                   else QImage.Format.Format_RGB32)
            img = QImage(raw, w, h, w * 4, fmt)
            px = QPixmap.fromImage(img)
            return QIcon(px) if not px.isNull() else None
        except Exception:
            return None

    def _invoke_shell_command(self, icm, hwnd: int, cmd: int, parent_path: str) -> None:
        import win32con
        ci = (
            0,
            hwnd,
            cmd - 1,
            None,
            None,
            parent_path,
            win32con.SW_SHOWNORMAL,
            0,
            None,
        )
        try:
            icm.InvokeCommand(ci)
        except Exception:
            pass

    def _open_default(self, path: str) -> None:
        try:
            os.startfile(path)
        except Exception as exc:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "Open", str(exc))

    def _open_with(self, path: str) -> None:
        import ctypes
        ctypes.windll.shell32.ShellExecuteW(  # type: ignore[attr-defined]
            int(self.winId()), "openas", path, None, None, 1
        )

    def _run_as_admin(self, path: str) -> None:
        import ctypes
        ctypes.windll.shell32.ShellExecuteW(  # type: ignore[attr-defined]
            int(self.winId()), "runas", path, None, None, 1
        )

    def _show_in_explorer(self, path: str) -> None:
        try:
            subprocess.Popen(f'explorer.exe /select,"{path}"', shell=True)
        except Exception:
            try:
                subprocess.Popen(['explorer.exe', str(Path(path).parent)])
            except Exception:
                pass

    def _open_terminal_here(self) -> None:
        try:
            subprocess.Popen(
                ['powershell.exe', '-NoExit', '-Command',
                 f'Set-Location "{self.current_path}"'],
                creationflags=subprocess.CREATE_NEW_CONSOLE,
            )
        except Exception:
            pass

    def _extract_here(self, path: str) -> None:
        try:
            import zipfile
            with zipfile.ZipFile(path, 'r') as zf:
                zf.extractall(str(Path(path).parent))
            QTimer.singleShot(500, self.refresh)
        except Exception as exc:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "Extract", str(exc))

    def _compress_to_zip(self, paths: list[str]) -> None:
        if not paths:
            return
        from PySide6.QtWidgets import QFileDialog
        parent_dir = str(Path(paths[0]).parent)
        default = (Path(paths[0]).stem + ".zip") if len(paths) == 1 else "archive.zip"
        out, _ = QFileDialog.getSaveFileName(
            self, "Save ZIP", str(Path(parent_dir) / default), "ZIP files (*.zip)"
        )
        if not out:
            return
        try:
            import zipfile
            with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as zf:
                for p in paths:
                    fp = Path(p)
                    if fp.is_file():
                        zf.write(p, fp.name)
                    elif fp.is_dir():
                        for child in fp.rglob('*'):
                            if child.is_file():
                                zf.write(str(child), str(child.relative_to(fp.parent)))
            QTimer.singleShot(500, self.refresh)
        except Exception as exc:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "Compress", str(exc))

    def _show_windows_shell_menu(self, paths: list[str], pos: object) -> None:
        """Display the native Windows Shell context menu for the selected paths."""
        try:
            import pythoncom
            import win32con
            import win32gui
            from win32com.shell import shell, shellcon

            if not paths:
                return
            parent_path = str(Path(paths[0]).parent)
            hwnd = int(self.window().winId())

            pythoncom.CoInitialize()
            try:
                desktop = shell.SHGetDesktopFolder()
                parent_pidl = shell.SHILCreateFromPath(parent_path, 0)[0]
                folder = desktop.BindToObject(
                    parent_pidl, None, shell.IID_IShellFolder
                )
                child_pidls = []
                for p in paths:
                    try:
                        result = folder.ParseDisplayName(hwnd, None, Path(p).name)
                        pidl = None
                        if isinstance(result, tuple):
                            pidl = result[1] if len(result) >= 2 else result[0] if result else None
                        else:
                            pidl = result
                        if isinstance(pidl, list) and pidl:
                            pidl = pidl[-1:]
                        if pidl is not None:
                            child_pidls.append(pidl)
                    except Exception:
                        pass
                if not child_pidls:
                    return

                icm = folder.GetUIObjectOf(
                    hwnd, child_pidls, shell.IID_IContextMenu, 0
                )
                if isinstance(icm, tuple):
                    icm = icm[-1]
                hmenu = win32gui.CreatePopupMenu()
                icm.QueryContextMenu(
                    hmenu, 0, 1, 0x7FFF,
                    shellcon.CMF_EXPLORE | shellcon.CMF_CANRENAME,
                )
                cmd = win32gui.TrackPopupMenu(
                    hmenu,
                    win32con.TPM_LEFTALIGN
                    | win32con.TPM_RETURNCMD
                    | win32con.TPM_RIGHTBUTTON,
                    pos.x(), pos.y(), 0, hwnd, None,
                )
                win32gui.DestroyMenu(hmenu)

                if cmd >= 1:
                    ci = (
                        0, hwnd, cmd - 1,
                        None, None, parent_path,
                        win32con.SW_SHOWNORMAL, 0, None,
                    )
                    icm.InvokeCommand(ci)
            finally:
                pythoncom.CoUninitialize()
        except Exception as exc:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "Windows Shell Menu", f"Error: {exc}")
