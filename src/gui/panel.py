"""Dual-pane file panel widget.

Each panel is a rounded card (QFrame#panel, property ``active``) containing:
- header row: back / forward / up, path field, refresh, favourites
- tab bar (+ new tab)
- FileTableView
- footer with counts and selection summary

Loading is asynchronous on the shared asyncio loop; every navigation gets a
generation number so a slow listing can never overwrite a newer one. VCS
detection (git / svn root + status) runs in the executor and is cached per
repository root, so changing directories inside a repo costs one ``git
status`` at most, never a blocking subprocess on the GUI thread.
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import time
from pathlib import Path

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QSizePolicy,
    QTabBar,
    QVBoxLayout,
    QWidget,
)

from src.core.file_model import FileEntry, SortField, SortOrder, format_size
from src.core.history import NavigationHistory
from src.core.selection import SelectionManager
from src.filesystem.local_fs import LocalFileSystemProvider
from src.filesystem.watcher import DirectoryWatcher
from src.settings.config import ConfigManager
from src.solarqt import icons, theme, widgets
from src.solarqt.widgets import IconButton, SearchField
from .file_table import FileTableView

logger = logging.getLogger(__name__)

_VCS_CACHE_TTL = 2.0  # seconds; a directory refresh inside this window reuses git status


_com_ready = False


def _com_init(pythoncom) -> None:
    """Initialise COM (STA) on the GUI thread once. Qt already holds an OLE
    apartment here; pairing every use with CoUninitialize would tear that
    apartment down under Qt and kill the shell objects the menu still holds."""
    global _com_ready
    if _com_ready:
        return
    try:
        pythoncom.CoInitialize()
    except Exception:
        pass
    _com_ready = True


def fit_menu_on_screen(menu, global_pos) -> None:
    """Show ``menu`` at ``global_pos``; if its natural height exceeds the screen,
    switch to the compact QSS variant (property ``compact``: "true", then
    "dense") first, so Qt does not have to break it into two columns."""
    from PySide6.QtWidgets import QApplication

    screen = QApplication.screenAt(global_pos) or QApplication.primaryScreen()
    if screen is not None:
        avail = screen.availableGeometry().height()
        for level in (None, "true", "dense"):
            if level:
                menu.setProperty("compact", level)
                widgets.repolish(menu)
            if _natural_height(menu) <= avail:
                break
    menu.exec(global_pos)


def _natural_height(menu) -> int:
    """Single-column height of a menu. QMenu.sizeHint() already folds the menu
    into columns to fit the screen, so it never reports the overflow itself."""
    menu.ensurePolished()
    menu.sizeHint()   # forces the action geometries to be computed
    total = sum(menu.actionGeometry(a).height() for a in menu.actions() if a.isVisible())
    m = menu.contentsMargins()
    return total + m.top() + m.bottom() + theme.px(14)


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
        self.filter_text: str = ""     # quick filter (Ctrl+S / '*'), cleared on directory change


class _VcsInfo:
    """Repository root detection + status map, computed off the GUI thread."""

    def __init__(self) -> None:
        self._root_cache: dict[str, tuple[str | None, str | None]] = {}   # dir -> (root, type)
        self._status_cache: dict[str, tuple[float, dict[str, str]]] = {}  # root -> (time, map)

    @staticmethod
    def _run(args: list[str], cwd: str | None = None, timeout: float = 5) -> str | None:
        try:
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            r = subprocess.run(
                args, cwd=cwd, capture_output=True, text=True, shell=False,
                timeout=timeout, creationflags=flags,
            )
            return r.stdout if r.returncode == 0 else None
        except Exception:
            return None

    def root_of(self, directory: str) -> tuple[str | None, str | None]:
        cached = self._root_cache.get(directory)
        if cached is not None:
            return cached
        # walk up: a known ancestor decides; no ".git"/".svn" anywhere = plain dir
        p = Path(directory)
        result: tuple[str | None, str | None] = (None, None)
        for anc in (p, *p.parents):
            if (anc / ".git").exists():
                result = (str(anc), "git")
                break
            if (anc / ".svn").exists():
                out = self._run(["svn", "info", "--show-item", "wc-root"], cwd=directory)
                result = ((out or str(anc)).strip() or str(anc), "svn")
                break
        self._root_cache[directory] = result
        return result

    def status_map(self, root: str, vcs_type: str) -> dict[str, str]:
        now = time.monotonic()
        cached = self._status_cache.get(root)
        if cached and now - cached[0] < _VCS_CACHE_TTL:
            return cached[1]
        status_map: dict[str, str] = {}
        if vcs_type == "git":
            out = self._run(["git", "-C", root, "status", "--short", "--untracked-files=normal"], timeout=10)
            for line in (out or "").splitlines():
                if len(line) < 4:
                    continue
                code = line[:2]
                path = line[3:].strip()
                if " -> " in path:
                    path = path.split(" -> ", 1)[1]
                path = path.strip('"')
                status_map[os.path.normcase(str(Path(root) / path))] = code
        elif vcs_type == "svn":
            out = self._run(["svn", "status"], cwd=root, timeout=10)
            for line in (out or "").splitlines():
                if len(line) < 9:
                    continue
                status_map[os.path.normcase(str(Path(root) / line[8:].strip()))] = line[0]
        self._status_cache[root] = (now, status_map)
        return status_map

    def annotate(self, directory: str, entries: list[FileEntry]) -> None:
        root, vcs_type = self.root_of(directory)
        if not root or not vcs_type:
            return
        status_map = self.status_map(root, vcs_type)
        if not status_map:
            for e in entries:
                if not e.is_parent:
                    e.vcs_type, e.vcs_state = vcs_type, "clean"
            return
        # a directory is "modified" if anything below it has a status
        dir_prefixes: dict[str, str] = {}
        for p, code in status_map.items():
            parent = os.path.dirname(p)
            while parent and len(parent) >= len(root):
                dir_prefixes.setdefault(parent, code)
                nxt = os.path.dirname(parent)
                if nxt == parent:
                    break
                parent = nxt
        for e in entries:
            if e.is_parent:
                continue
            key = os.path.normcase(e.full_path)
            e.vcs_type = vcs_type
            code = status_map.get(key)
            if code is None and e.is_dir:
                code = dir_prefixes.get(key)
                if code == "??":
                    code = "??"
                elif code is not None:
                    code = "M"
            e.vcs_state = code or "clean"

    def invalidate(self, directory: str | None = None) -> None:
        self._status_cache.clear()
        if directory is None:
            self._root_cache.clear()


class PanelWidget(QFrame):
    """One side of the dual-pane manager."""

    path_changed = Signal(str)           # new path
    entry_activated = Signal(object)     # FileEntry – file (directories are handled here)
    status_info = Signal(str)            # status bar text
    request_focus = Signal()             # panel wants keyboard focus
    favorites_requested = Signal()       # star button
    rename_requested = Signal(str, str)  # old full path, new full path (in-place rename)

    def __init__(
        self,
        initial_path: str,
        panel_id: str = "left",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("panel")
        self.setProperty("active", "false")
        self._panel_id = panel_id
        self._fs = LocalFileSystemProvider()
        self._tabs: list[_Tab] = []
        self._current_tab_index = -1
        self._generation = 0
        self._loading = False
        self._vcs = _VcsInfo()
        self._watcher = DirectoryWatcher(self)
        self._watcher.directory_changed.connect(self._on_dir_changed)
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(250)
        self._refresh_timer.timeout.connect(self._refresh_keep_cursor)

        self._build_ui()
        self._open_tab(initial_path)

    # ------------------------------------------------------------------ UI build

    def _build_ui(self) -> None:
        self.setMinimumWidth(theme.px(220))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(theme.px(6), theme.px(4), theme.px(6), theme.px(4))
        layout.setSpacing(theme.px(2))
        self._layout = layout

        # Header: navigation + path
        header = QFrame()
        header.setObjectName("panelHeader")
        hl = QHBoxLayout(header)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(theme.px(2))
        self._btn_back = IconButton("arrow_left", "Back (Alt+Left)")
        self._btn_back.clicked.connect(self._go_back)
        self._btn_forward = IconButton("arrow_right", "Forward (Alt+Right)")
        self._btn_forward.clicked.connect(self._go_forward)
        self._btn_up = IconButton("arrow_up", "Up (Backspace)")
        self._btn_up.clicked.connect(self._go_up)

        self._path_edit = QLineEdit()
        self._path_edit.setObjectName("pathEdit")
        self._path_edit.setClearButtonEnabled(False)
        self._path_edit.returnPressed.connect(self._on_path_entered)
        self._path_edit.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self._path_edit.setMinimumWidth(theme.px(60))
        self._path_edit.setToolTip("Path – type and press Enter")

        self._btn_refresh = IconButton("refresh", "Refresh (Ctrl+R)")
        self._btn_refresh.clicked.connect(self.refresh)
        self._btn_fav = IconButton("star_outline", "Favourites (Ctrl+D)")
        self._btn_fav.clicked.connect(self.favorites_requested.emit)

        for b in (self._btn_back, self._btn_forward, self._btn_up):
            hl.addWidget(b)
        hl.addWidget(self._path_edit, 1)
        hl.addWidget(self._btn_refresh)
        hl.addWidget(self._btn_fav)
        layout.addWidget(header)

        # Tab bar
        tab_row = QHBoxLayout()
        tab_row.setContentsMargins(0, 0, 0, 0)
        tab_row.setSpacing(theme.px(2))
        self._tab_bar = QTabBar()
        self._tab_bar.setObjectName("panelTabs")
        self._tab_bar.setTabsClosable(True)
        self._tab_bar.setMovable(True)
        self._tab_bar.setExpanding(False)
        self._tab_bar.setDocumentMode(True)
        self._tab_bar.setDrawBase(False)
        self._tab_bar.setUsesScrollButtons(True)
        self._tab_bar.setElideMode(Qt.TextElideMode.ElideRight)
        self._tab_bar.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self._tab_bar.currentChanged.connect(self._on_tab_changed)
        self._tab_bar.tabCloseRequested.connect(self._close_tab)
        self._tab_bar.tabMoved.connect(self._on_tab_moved)
        tab_row.addWidget(self._tab_bar, 1)
        self._btn_new_tab = IconButton("plus", "New tab (Ctrl+T)")
        self._btn_new_tab.clicked.connect(self._new_tab_from_current)
        tab_row.addWidget(self._btn_new_tab)
        layout.addLayout(tab_row)

        # File table
        self._table = FileTableView(self)
        self._table.entry_activated.connect(self._on_entry_activated)
        self._table.space_pressed.connect(self._toggle_selection)
        self._table.sort_requested.connect(self._on_sort_requested)
        self._table.filter_requested.connect(self.show_filter)
        self._table.file_model().rename_requested.connect(self._on_rename_committed)
        self._table.mousePressEvent = self._on_table_mouse_press  # type: ignore[method-assign]
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._show_context_menu)
        layout.addWidget(self._table, 1)

        # Quick filter (hidden until Ctrl+S / '*')
        self._filter_edit = SearchField("Filter…   Esc clears   Enter back to list")
        self._filter_edit.setObjectName("search")
        self._filter_edit.setVisible(False)
        self._filter_edit.textChanged.connect(self._on_filter_text)
        self._filter_edit.installEventFilter(self)
        layout.addWidget(self._filter_edit)

        # Footer
        footer = QFrame()
        footer.setObjectName("panelFooter")
        fl = QHBoxLayout(footer)
        fl.setContentsMargins(theme.px(4), theme.px(3), theme.px(4), theme.px(1))
        fl.setSpacing(theme.px(8))
        self._info_label = QLabel("Ready")
        self._info_label.setObjectName("faintLabel")
        self._info_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._sel_label = QLabel("")
        self._sel_label.setObjectName("pathLabel")
        self._sel_label.setVisible(False)
        fl.addWidget(self._info_label, 1)
        fl.addWidget(self._sel_label)
        layout.addWidget(footer)

    def retheme(self) -> None:
        """Zoom / theme changed: margins and metrics outside QSS."""
        self.setMinimumWidth(theme.px(220))
        self._layout.setContentsMargins(theme.px(6), theme.px(4), theme.px(6), theme.px(4))
        self._layout.setSpacing(theme.px(2))
        self._path_edit.setMinimumWidth(theme.px(60))
        self._table.retheme()

    def set_active(self, active: bool) -> None:
        self.setProperty("active", "true" if active else "false")
        widgets.repolish(self)
        widgets.repolish(self._table)   # descendant selector on [active] needs the table re-polished
        self._table.viewport().update()

    # ------------------------------------------------------------------ tab management

    def _open_tab(self, path: str) -> None:
        tab = _Tab(path)
        self._tabs.append(tab)
        idx = len(self._tabs) - 1
        self._tab_bar.blockSignals(True)
        self._tab_bar.addTab(Path(path).name or path)
        self._tab_bar.setTabToolTip(idx, path)
        self._tab_bar.setCurrentIndex(idx)
        self._tab_bar.blockSignals(False)
        self._current_tab_index = idx
        self._navigate_to(path, push_history=False)

    def _new_tab_from_current(self) -> None:
        self._open_tab(self.current_path)

    def _close_tab(self, index: int) -> None:
        if len(self._tabs) <= 1:
            return
        self._tabs.pop(index)
        self._tab_bar.blockSignals(True)
        self._tab_bar.removeTab(index)
        self._tab_bar.blockSignals(False)
        self._current_tab_index = min(self._tab_bar.currentIndex(), len(self._tabs) - 1)
        self._on_tab_changed(self._current_tab_index)

    def _on_tab_moved(self, src: int, dst: int) -> None:
        if 0 <= src < len(self._tabs) and 0 <= dst < len(self._tabs):
            tab = self._tabs.pop(src)
            self._tabs.insert(dst, tab)
            self._current_tab_index = self._tab_bar.currentIndex()

    def _on_tab_changed(self, index: int) -> None:
        if index < 0 or index >= len(self._tabs):
            return
        self._current_tab_index = index
        self._navigate_to(self._tabs[index].path, push_history=False)

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
        try:
            resolved = str(Path(path).resolve())
        except OSError:
            return
        if not Path(resolved).is_dir():
            self._flash_path_invalid()
            return
        tab = self._current_tab
        if push_history:
            tab.history.push(resolved)
        if resolved != tab.path:
            tab.cursor_name = ""
            if tab.filter_text:
                tab.filter_text = ""
                self._filter_edit.blockSignals(True)
                self._filter_edit.clear()
                self._filter_edit.setVisible(False)
                self._filter_edit.blockSignals(False)
        tab.path = resolved
        self._path_edit.setText(resolved)
        self._path_edit.setCursorPosition(0)   # narrow field: show the drive, not the tail
        self._path_edit.setProperty("invalid", "false")
        widgets.repolish(self._path_edit)
        label = Path(resolved).name or resolved
        self._tab_bar.setTabText(self._current_tab_index, label)
        self._tab_bar.setTabToolTip(self._current_tab_index, resolved)
        self._btn_back.setEnabled(tab.history.can_go_back)
        self._btn_forward.setEnabled(tab.history.can_go_forward)
        self._btn_up.setEnabled(self._fs.get_parent(resolved) is not None)
        self._watcher.watch(resolved)
        self._load_directory(resolved)
        self.path_changed.emit(resolved)
        try:
            ConfigManager.get_instance()._db.add_path_history(resolved)
        except Exception:
            pass

    def _flash_path_invalid(self) -> None:
        self._path_edit.setProperty("invalid", "true")
        widgets.repolish(self._path_edit)

    def _load_directory(self, path: str) -> None:
        self._generation += 1
        gen = self._generation
        show_hidden = self._current_tab.show_hidden
        loop = self._get_loop()
        if loop and loop.is_running():
            self._loading = True
            asyncio.ensure_future(self._load_async(path, show_hidden, gen), loop=loop)
        else:
            self._load_sync(path, show_hidden)

    @staticmethod
    def _get_loop() -> asyncio.AbstractEventLoop | None:
        try:
            return asyncio.get_event_loop()
        except RuntimeError:
            return None

    async def _load_async(self, path: str, show_hidden: bool, gen: int) -> None:
        try:
            loop = asyncio.get_event_loop()
            entries = await self._fs.list_directory(path, show_hidden=show_hidden)
            if gen != self._generation:
                return
            await loop.run_in_executor(None, self._vcs.annotate, path, entries)
            if gen != self._generation:
                return
            self._apply_entries(entries, path)
        except Exception as exc:
            if gen == self._generation:
                logger.warning("Failed to load %s: %s", path, exc)
                self._info_label.setText(f"Error: {exc}")
        finally:
            if gen == self._generation:
                self._loading = False

    def _load_sync(self, path: str, show_hidden: bool) -> None:
        try:
            loop = asyncio.new_event_loop()
            entries = loop.run_until_complete(self._fs.list_directory(path, show_hidden=show_hidden))
            loop.close()
            self._vcs.annotate(path, entries)
            self._apply_entries(entries, path)
        except Exception as exc:
            logger.warning("Failed to load %s: %s", path, exc)
            self._info_label.setText(f"Error: {exc}")

    def _apply_entries(self, entries: list[FileEntry], path: str) -> None:
        self._unfiltered = list(entries)
        entries = self._filter_entries(self._sort_entries(entries))
        tab = self._current_tab
        model = self._table.file_model()
        # drop stale selection (files that disappeared)
        present = {e.full_path for e in entries}
        for p in list(tab.selection.selected_paths):
            if p not in present:
                tab.selection.deselect_path(p)
        model.set_entries(entries)
        model.set_selected(set(tab.selection.selected_paths))
        self._table.show_sort_indicator(tab.sort_field, tab.sort_order == SortOrder.DESCENDING)
        row = model.row_of_name(tab.cursor_name) if tab.cursor_name else -1
        if row < 0 and entries:
            row = 0
        if row >= 0:
            self._table.selectRow(row)
            self._table.scrollTo(model.index(row, 0))
        self._update_info(entries)

    def _sort_entries(self, entries: list[FileEntry]) -> list[FileEntry]:
        tab = self._current_tab
        parent_entries = [e for e in entries if e.is_parent]
        dir_entries = [e for e in entries if e.is_dir and not e.is_parent]
        file_entries = [e for e in entries if not e.is_dir]

        key_map = {
            SortField.NAME: lambda e: e.name.lower(),
            SortField.EXTENSION: lambda e: (e.extension.lower(), e.name.lower()),
            SortField.SIZE: lambda e: (e.size, e.name.lower()),
            SortField.MODIFIED: lambda e: e.modified,
            SortField.CREATED: lambda e: e.created,
            SortField.ATTRIBUTES: lambda e: (e.attributes, e.name.lower()),
        }
        key_fn = key_map.get(tab.sort_field, lambda e: e.name.lower())
        rev = tab.sort_order == SortOrder.DESCENDING
        dir_key = key_fn if tab.sort_field in (SortField.NAME, SortField.MODIFIED, SortField.CREATED) \
            else (lambda e: e.name.lower())
        dir_entries.sort(key=dir_key, reverse=rev)
        file_entries.sort(key=key_fn, reverse=rev)
        return parent_entries + dir_entries + file_entries

    def _update_info(self, entries: list[FileEntry]) -> None:
        dirs = sum(1 for e in entries if e.is_dir and not e.is_parent)
        files = sum(1 for e in entries if not e.is_dir)
        total = sum(e.size for e in entries if not e.is_dir and e.size > 0)
        sel_count = self._current_tab.selection.count
        sel_size = self._current_tab.selection.total_size(entries)
        info = f"{dirs} folders, {files} files ({format_size(total) or '0 B'})"
        if self._current_tab.filter_text:
            all_count = sum(1 for e in getattr(self, "_unfiltered", []) if not e.is_parent)
            info = f"Filter „{self._current_tab.filter_text}“: {dirs + files} of {all_count}  ·  " + info
        self._info_label.setText(info)
        if sel_count:
            self._sel_label.setText(f"{sel_count} selected · {format_size(sel_size) or '0 B'}")
            self._sel_label.setVisible(True)
            info += f"  ·  {sel_count} selected ({format_size(sel_size) or '0 B'})"
        else:
            self._sel_label.setVisible(False)
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
            current_name = Path(self.current_path).name
            self._navigate_to(parent, push_history=True)
            self._current_tab.cursor_name = current_name
            row = self._table.file_model().row_of_name(current_name)
            if row >= 0:
                self._table.selectRow(row)

    def show_history_menu(self) -> None:
        """Alt+Down – visited directories of this tab (most recent first), then
        the persistent path history; pops up under the path field."""
        from PySide6.QtWidgets import QMenu

        tab = self._current_tab
        paths = self.history_paths()
        if not paths:
            return
        menu = QMenu(self)
        for p in paths:
            act = menu.addAction(icons.icon("check" if p == tab.path else "folder"), p)
            act.triggered.connect(lambda _=False, target=p: self.navigate_to(target))
        menu.setMinimumWidth(self._path_edit.width())
        fit_menu_on_screen(menu, self._path_edit.mapToGlobal(self._path_edit.rect().bottomLeft()))
        self.give_focus()

    def _on_path_entered(self) -> None:
        path = self._path_edit.text().strip().strip('"')
        if path:
            self._navigate_to(os.path.expandvars(os.path.expanduser(path)))
            self.give_focus()

    def _on_dir_changed(self, path: str) -> None:
        """Auto-refresh when the filesystem notifies of changes (debounced)."""
        if path == self.current_path:
            self._vcs.invalidate(path)
            self._refresh_timer.start()

    def _refresh_keep_cursor(self) -> None:
        cur = self.current_entry()
        if cur:
            self._current_tab.cursor_name = cur.name
        self.refresh()

    def set_pending_cursor(self, name: str) -> None:
        """Name the cursor should land on after the next refresh (rename, mkdir…)."""
        self._pending_cursor = name

    def refresh(self) -> None:
        self._vcs.invalidate(self.current_path)
        pending = getattr(self, "_pending_cursor", "")
        if pending:
            self._current_tab.cursor_name = pending
            self._pending_cursor = ""
        else:
            cur = self.current_entry()
            if cur:
                self._current_tab.cursor_name = cur.name
        self._navigate_to(self.current_path, push_history=False)

    # ------------------------------------------------------------------ entry handling

    def _on_entry_activated(self, entry: FileEntry) -> None:
        if entry.is_parent:
            self._go_up()
        elif entry.is_dir:
            self._navigate_to(entry.full_path)
        else:
            self.entry_activated.emit(entry)

    def _toggle_selection(self, entry: FileEntry) -> None:
        if entry.is_parent:
            return
        sel = self._current_tab.selection
        sel.toggle(entry)
        self._table.file_model().set_selected(set(sel.selected_paths))
        self._update_info(self._table.file_model().get_all_entries())

    def _on_table_mouse_press(self, event) -> None:
        FileTableView.mousePressEvent(self._table, event)
        self.request_focus.emit()

    def start_rename(self) -> None:
        """F2 / Shift+F6 – rename the entry under the cursor in place."""
        self.give_focus()
        self._table.start_rename()

    def _on_rename_committed(self, entry: FileEntry, new_name: str) -> None:
        new_path = str(Path(entry.full_path).parent / new_name)
        self.set_pending_cursor(new_name)
        self.rename_requested.emit(entry.full_path, new_path)

    def tab_labels(self) -> list[str]:
        return [self._tab_bar.tabText(i) for i in range(self._tab_bar.count())]

    def switch_tab(self, index: int) -> None:
        if 0 <= index < self._tab_bar.count():
            self._tab_bar.setCurrentIndex(index)

    def close_current_tab(self) -> None:
        self._close_tab(self._current_tab_index)

    @property
    def sort_state(self) -> tuple[SortField, SortOrder]:
        tab = self._current_tab
        return tab.sort_field, tab.sort_order

    def history_paths(self) -> list[str]:
        paths = self._current_tab.history.all_paths()
        try:
            for p in ConfigManager.get_instance()._db.get_path_history(limit=40):
                if p not in paths:
                    paths.append(p)
        except Exception:
            pass
        return [p for p in paths if p][:30]

    def _on_sort_requested(self, field: SortField) -> None:
        tab = self._current_tab
        if tab.sort_field == field:
            order = SortOrder.DESCENDING if tab.sort_order == SortOrder.ASCENDING else SortOrder.ASCENDING
        else:
            order = SortOrder.ASCENDING
        self.set_sort(field, order)

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

    def _apply_selection(self) -> None:
        sel = self._current_tab.selection
        self._table.file_model().set_selected(set(sel.selected_paths))
        self._update_info(self.all_entries())

    def select_all(self) -> None:
        self._current_tab.selection.select_all([e for e in self.all_entries() if not e.is_parent])
        self._apply_selection()

    def deselect_all(self) -> None:
        self._current_tab.selection.clear()
        self._apply_selection()

    def invert_selection(self) -> None:
        self._current_tab.selection.invert([e for e in self.all_entries() if not e.is_parent])
        self._apply_selection()

    def toggle_hidden(self) -> None:
        self._current_tab.show_hidden = not self._current_tab.show_hidden
        self.refresh()

    @property
    def show_hidden(self) -> bool:
        return self._current_tab.show_hidden if self._tabs else False

    def set_sort(self, field: SortField, order: SortOrder) -> None:
        tab = self._current_tab
        tab.sort_field = field
        tab.sort_order = order
        cur = self.current_entry()
        if cur:
            tab.cursor_name = cur.name
        self._apply_entries(getattr(self, "_unfiltered", self._table.file_model().get_all_entries()), tab.path)

    # ------------------------------------------------------------------ quick filter (Ctrl+S / '*')

    def _filter_entries(self, entries: list[FileEntry]) -> list[FileEntry]:
        text = self._current_tab.filter_text.strip().lower()
        if not text:
            return entries
        import fnmatch
        pattern = text if any(ch in text for ch in "*?[") else f"*{text}*"
        return [e for e in entries if e.is_parent or fnmatch.fnmatchcase(e.name.lower(), pattern)]

    def show_filter(self, initial: str = "") -> None:
        """Ctrl+S / '*': show the filter field under the list and type into it."""
        self._filter_edit.setVisible(True)
        if initial:
            self._filter_edit.setText(initial)
        self._filter_edit.setFocus()
        self._filter_edit.selectAll()

    def clear_filter(self) -> None:
        self._current_tab.filter_text = ""
        self._filter_edit.blockSignals(True)
        self._filter_edit.clear()
        self._filter_edit.blockSignals(False)
        self._filter_edit.setVisible(False)
        self._rerender()
        self.give_focus()

    @property
    def filter_text(self) -> str:
        return self._current_tab.filter_text if self._tabs else ""

    def _on_filter_text(self, text: str) -> None:
        self._current_tab.filter_text = text
        self._rerender()

    def _rerender(self) -> None:
        cur = self.current_entry()
        if cur:
            self._current_tab.cursor_name = cur.name
        self._apply_entries(getattr(self, "_unfiltered", []), self.current_path)

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        from PySide6.QtCore import QEvent
        if obj is self._filter_edit and event.type() == QEvent.Type.KeyPress:
            key = event.key()
            if key == Qt.Key.Key_Escape:
                self.clear_filter()
                return True
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self.give_focus()          # keep the filter, work with the list
                return True
            if key in (Qt.Key.Key_Up, Qt.Key.Key_Down, Qt.Key.Key_PageUp, Qt.Key.Key_PageDown,
                       Qt.Key.Key_Home, Qt.Key.Key_End):
                from PySide6.QtWidgets import QApplication
                QApplication.sendEvent(self._table, event)   # move the cursor while typing
                return True
        return super().eventFilter(obj, event)

    def give_focus(self) -> None:
        self._table.setFocus()

    def set_font_pt(self, pt: int) -> None:  # backwards compatibility
        self._table.apply_metrics()

    # ------------------------------------------------------------------ context menu

    def _show_context_menu(self, _pos: object) -> None:
        """Rich right-click menu: Commander operations + Windows shell menu."""
        from PySide6.QtCore import QPoint
        from PySide6.QtGui import QCursor
        from PySide6.QtWidgets import QApplication, QMenu

        self.request_focus.emit()
        entries = self.selected_entries()
        paths = [e.full_path for e in entries if not e.is_parent]
        mw = self.window()

        def act(menu: QMenu, text: str, icon_name: str | None, handler) -> None:
            a = menu.addAction(icons.icon(icon_name), text) if icon_name else menu.addAction(text)
            a.triggered.connect(handler)

        menu = QMenu(self)
        gp = self._table.viewport().mapToGlobal(_pos) if isinstance(_pos, QPoint) and _pos.x() >= 0 else QCursor.pos()

        if not paths:
            if hasattr(mw, "_mkdir"):
                act(menu, "New &Folder\tF7", "folder_plus", mw._mkdir)
            act(menu, "&Refresh\tCtrl+R", "refresh", self.refresh)
            menu.addSeparator()
            act(menu, "Open &Terminal Here", "terminal", self._open_terminal_here)
            act(menu, "Open in &Explorer", "explorer", lambda: self._show_in_explorer(self.current_path))
            self._add_frequent_section(menu)
            fit_menu_on_screen(menu, gp)
            return

        is_single = len(paths) == 1
        first = paths[0]
        first_entry = entries[0]
        is_dir = first_entry.is_dir and not first_entry.is_parent
        ext = first_entry.extension.lower()

        if is_single:
            if is_dir:
                act(menu, "&Open\tEnter", "folder", lambda: self.navigate_to(first))
            else:
                act(menu, "&Open\tEnter", "external", lambda: self._open_default(first))
                act(menu, "Open &With…", None, lambda: self._open_with(first))
                if hasattr(mw, "_view_file"):
                    act(menu, "&View\tF3", "eye", mw._view_file)
                if hasattr(mw, "_edit_file"):
                    act(menu, "&Edit\tF4", "edit", mw._edit_file)
                menu.addSeparator()
                act(menu, "Run as &Administrator", "shield", lambda: self._run_as_admin(first))
            menu.addSeparator()

        if hasattr(mw, "_copy_files"):
            act(menu, "&Copy\tF5", "copy", mw._copy_files)
        if hasattr(mw, "_move_files"):
            act(menu, "&Move\tF6", "arrow_right", mw._move_files)
        menu.addSeparator()
        if is_single:
            act(menu, "Re&name\tF2", "rename", self.start_rename)
        if hasattr(mw, "_bulk_rename"):
            act(menu, "&Bulk Rename…\tCtrl+M", "rename", mw._bulk_rename)
        if hasattr(mw, "_delete_files"):
            act(menu, "&Delete\tF8", "trash", mw._delete_files)
        if hasattr(mw, "_mkdir"):
            act(menu, "New &Folder\tF7", "folder_plus", mw._mkdir)
        menu.addSeparator()

        if is_single:
            act(menu, "Copy &Path", "clipboard", lambda: QApplication.clipboard().setText(first))
            act(menu, "Copy Na&me", None, lambda: QApplication.clipboard().setText(Path(first).name))
            menu.addSeparator()

        act(menu, "Show in &Explorer", "explorer",
            lambda: self._show_in_explorer(first if is_single else self.current_path))
        act(menu, "Open &Terminal Here", "terminal", self._open_terminal_here)
        menu.addSeparator()

        if not is_dir and hasattr(mw, "_compute_hash"):
            act(menu, "Compute &Hash…", "hash", mw._compute_hash)
        if hasattr(mw, "_show_properties"):
            act(menu, "&Properties\tAlt+Enter", "info", mw._show_properties)
        menu.addSeparator()

        archive_exts = {"zip", "7z", "tar", "gz", "bz2", "xz", "rar"}
        if is_single and ext in archive_exts:
            act(menu, "E&xtract Here", "archive", lambda: self._extract_here(first))
        act(menu, "Compress to &ZIP…", "archive", lambda: self._compress_to_zip(paths))
        menu.addSeparator()

        self._populate_windows_shell_menu(menu, paths)
        self._add_frequent_section(menu)
        fit_menu_on_screen(menu, gp)

    # ---- "frequently used" section (usage counted per menu label, stored in the DB)

    _USAGE_KEY = "context_menu_usage"
    FREQUENT_MAX = 4
    FREQUENT_MIN_USES = 2

    @staticmethod
    def _usage_key(action) -> str:
        return action.text().replace("&", "").split("	")[0].strip()

    def _load_usage(self) -> dict[str, int]:
        try:
            data = ConfigManager.get_instance().get(self._USAGE_KEY, {}) or {}
            return {str(k): int(v) for k, v in data.items()}
        except Exception:
            return {}

    def _bump_usage(self, key: str) -> None:
        usage = self._load_usage()
        usage[key] = usage.get(key, 0) + 1
        if len(usage) > 200:   # keep the table small: drop the rarest entries
            usage = dict(sorted(usage.items(), key=lambda kv: kv[1], reverse=True)[:150])
        try:
            ConfigManager.get_instance().set(self._USAGE_KEY, usage)
        except Exception:
            pass

    def _add_frequent_section(self, menu) -> None:
        """Count every click on a top-level item and show the most used ones on top."""
        from PySide6.QtGui import QAction

        actions = [a for a in menu.actions() if not a.isSeparator() and a.menu() is None and a.text()]
        by_key: dict[str, QAction] = {}
        for a in actions:
            key = self._usage_key(a)
            by_key.setdefault(key, a)
            a.triggered.connect(lambda _=False, k=key: self._bump_usage(k))
        usage = self._load_usage()
        ranked = sorted(
            (k for k in by_key if usage.get(k, 0) >= self.FREQUENT_MIN_USES),
            key=lambda k: usage[k], reverse=True,
        )[: self.FREQUENT_MAX]
        if not ranked:
            return
        first = menu.actions()[0]
        header = menu.addAction("Frequently used")
        header.setEnabled(False)
        menu.insertAction(first, header)
        for key in ranked:
            src = by_key[key]
            clone = QAction(src.icon(), src.text(), menu)
            clone.setToolTip(f"used {usage[key]}×")
            clone.triggered.connect(src.trigger)   # re-uses the original handler (and its usage count)
            menu.insertAction(first, clone)
        menu.insertSeparator(first)

    # ---- context menu helpers (native Windows shell menu via pywin32)
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

            _com_init(pythoncom)
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
                    menu._shell_refs = (folder, child_pidls, icm)   # keep COM objects alive while the menu lives
            finally:
                pass
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
                verb = self._shell_verb(icm, cmd_id)
                if verb in self._SHELL_VERBS_SKIPPED:
                    # our own menu already offers these; the shell "rename" verb
                    # does nothing outside Explorer anyway
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

    # shell verbs duplicated by our own items (open, delete, rename, copy path)
    _SHELL_VERBS_SKIPPED = {"open", "delete", "rename", "copyaspath"}

    @staticmethod
    def _shell_verb(icm, cmd_id: int) -> str:
        """Canonical verb of a shell menu item ("properties", "cut"…), "" if unknown."""
        from win32com.shell import shellcon
        try:
            return str(icm.GetCommandString(cmd_id - 1, shellcon.GCS_VERBW) or "").lower()
        except Exception:
            return ""

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
        # pywin32 CMINVOKECOMMANDINFO = (fMask, hwnd, verb, params, dir, nShow, hotkey, hicon)
        ci = (0, hwnd, cmd - 1, None, parent_path, win32con.SW_SHOWNORMAL, 0, None)
        try:
            icm.InvokeCommand(ci)
        except Exception as exc:
            logger.warning("shell command %s failed: %s", cmd, exc)
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "Windows Shell", f"The shell command failed: {exc}")

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

            _com_init(pythoncom)
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
                    ci = (0, hwnd, cmd - 1, None, parent_path, win32con.SW_SHOWNORMAL, 0, None)
                    icm.InvokeCommand(ci)
            finally:
                pass  # COM stays initialised on the GUI thread (see _com_init)
        except Exception as exc:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "Windows Shell Menu", f"Error: {exc}")
