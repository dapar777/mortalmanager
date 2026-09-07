"""Command palette (Ctrl+Shift+P) – search and run commands, VS Code style.

Entries are dicts: {label, category, shortcut, run(callable)} or, for a
multi-level command, {label, category, children} where ``children`` is a list
of entries or a callable returning one (built lazily, e.g. the list of drives
or tabs). Choosing such an entry descends one level: the breadcrumb shows the
path ("Sort by › Name"), the search field is cleared, Backspace on an empty
field or Esc goes back up.

Matching is token based (every typed token must occur in "category label");
arrows move the list while the search field keeps focus; Enter runs.

Recently used (VS Code style): the palette gets ``recent`` – a list of
command paths ("Navigate|Sort by|Size|Descending", newest first) – and shows
those commands first at the top level, sub-level leaves flattened into one
item ("Sort by › Size › Descending"). Every run reports its path through
``on_run`` so the caller can persist the list.

Dynamic levels: an entry with ``search`` (callable(query) -> list[dict])
instead of static children is a search level – the list is produced from the
typed text (file index). ``extra_search`` given to the palette adds a few such
hits at the top level once the query is 3+ characters (files, terminal
history), VS Code style. ``status`` (callable() -> str) is shown in the crumb.

Prefix modes at the top level (Total Commander / VS Code style):
    "␣text"  only application commands (no files, no terminal history)
    "c text" only command-line history
    "dc text" terminal history entries to DELETE (Enter removes one, the palette stays open)
    "a text" files and folders from the index
    "f text" files only
    "d text" folders only
Queries for files / terminal history may be a glob mask ("*.txt") or a regex
(anything with regex metacharacters); see index/pattern.py.
The palette gets ``mode_search(mode, query)`` for the last three; the mode
is shown in the crumb.

Rule for the app: every user-facing command lives in
MainWindow._build_palette_commands – add new features there first.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
)

from src.solarqt import icons, theme

ENTRY_ROLE = Qt.ItemDataRole.UserRole
PATH_SEP = "|"
RECENT_MAX = 8
MODES = {"c": "terminal", "a": "all_entries", "f": "files", "d": "dirs", "dc": "terminal_delete"}
MODE_LABELS = {"commands": "commands only", "terminal": "terminal history",
               "terminal_delete": "delete from terminal history – Enter removes, palette stays open",
               "all_entries": "files & folders", "files": "files", "dirs": "folders"}
MODE_HINT = "␣ commands   c␣ terminal   dc␣ delete command from history   a␣ files+folders   f␣ files   d␣ folders   ·   *? mask, regex ok"
INDEX_MODES = ("all_entries", "files", "dirs")
SEARCH_MODES = (*INDEX_MODES, "terminal", "terminal_delete")   # list comes from mode_search


def parse_mode(text: str) -> tuple[str, str]:
    """(mode, query) from the raw search text; mode "all" when no prefix."""
    if text.startswith(" "):
        return "commands", text.strip()
    for n in (2, 1):                       # "dc " before "d "
        if len(text) > n and text[n] == " " and text[:n].lower() in MODES:
            return MODES[text[:n].lower()], text[n + 1:].strip()
    return "all", text.strip()


def _children_of(entry: dict) -> list[dict]:
    children = entry.get("children")
    if callable(children):
        children = children()
    return list(children or [])


class CommandPalette(QDialog):
    def __init__(self, entries: list[dict], parent=None, title: str = "Commands",
                 recent: list[str] | None = None, on_run=None, extra_search=None, mode_search=None) -> None:
        super().__init__(parent)
        for e in entries:
            e.setdefault("_path", [e["label"]])
        self._root = entries
        self._recent = list(recent or [])
        self._on_run = on_run
        self._extra_search = extra_search
        self._mode_search = mode_search
        self._mode = "all"
        self._pattern_note = ""
        self._search_fn = None                       # search callable of the current dynamic level
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(120)
        self._search_timer.timeout.connect(lambda: self._filter(self.search.text()))
        self._stack: list[tuple[list[dict], str]] = []   # (entries, breadcrumb title) of parent levels
        self._deep: list[dict] | None = None             # flattened sub-level leaves, built on first search
        self._entries = self._with_recent(entries)
        self._title = title
        self.setWindowTitle(title)
        self.setModal(True)
        self.resize(theme.px(580), theme.px(440))

        self.crumb = QLabel()
        self.crumb.setObjectName("pathLabel")
        self.search = QLineEdit()
        self.search.setObjectName("search")
        self.search.setClearButtonEnabled(True)
        self.list = QListWidget()
        self.list.setObjectName("paletteList")
        self.list.setUniformItemSizes(True)
        self.list.setIconSize(icons.qsize(12))
        hint = QLabel(f"{MODE_HINT}      ·      ↑↓ move   Enter run   Backspace back   Esc close")
        hint.setObjectName("caption")
        hint.setAlignment(Qt.AlignmentFlag.AlignRight)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(theme.px(8), theme.px(8), theme.px(8), theme.px(8))
        layout.setSpacing(theme.px(6))
        top = QHBoxLayout()
        top.setSpacing(theme.px(6))
        top.addWidget(self.crumb)
        layout.addLayout(top)
        layout.addWidget(self.search)
        layout.addWidget(self.list, 1)
        layout.addWidget(hint)

        self.search.textChanged.connect(self._on_text)
        self.list.itemActivated.connect(lambda _: self._run_current())
        self.search.installEventFilter(self)

        self._enter_level(None)
        self.search.setFocus()

    # ------------------------------------------------------------------ levels

    def _enter_level(self, entry: dict | None) -> None:
        if entry is not None:
            self._stack.append((self._entries, entry["label"]))
            if callable(entry.get("search")):
                self._search_fn = entry["search"]
                self._status_fn = entry.get("status")
                self._entries = []
            else:
                children = _children_of(entry)
                for c in children:
                    c["_path"] = [*entry["_path"], c["label"]]
                self._entries = children
        self._refresh_level()

    # ------------------------------------------------------------------ recently used

    def _resolve(self, path: list[str]) -> dict | None:
        """Find the leaf entry for a label path, descending through children."""
        level = self._root
        entry = None
        for i, label in enumerate(path):
            entry = next((e for e in level if e["label"] == label), None)
            if entry is None or callable(entry.get("search")):
                return None
            if i < len(path) - 1:
                level = _children_of(entry)
                for c in level:
                    c["_path"] = [*entry["_path"], c["label"]]
        return entry if entry is not None and entry.get("children") is None else None

    def _with_recent(self, entries: list[dict]) -> list[dict]:
        """Top level: recently used commands first (newest first), then the rest."""
        recent: list[dict] = []
        seen: set[str] = set()
        for key in self._recent[:RECENT_MAX]:
            path = key.split(PATH_SEP)
            leaf = self._resolve(path)
            if leaf is None or key in seen:
                continue
            seen.add(key)
            root = next((e for e in entries if e["label"] == path[0]), leaf)
            recent.append({
                **leaf,
                "label": "  ›  ".join(path),
                "category": root.get("category", ""),
                "children": None,
                "_path": path,
                "_recent": True,
            })
        rest = [e for e in entries if PATH_SEP.join(e["_path"]) not in seen]
        return recent + rest

    def _crumb_text(self) -> str:
        text = "  ›  ".join([self._title, *(label for _, label in self._stack)])
        if not self._stack and self._mode != "all":
            text += f"      [{MODE_LABELS.get(self._mode, self._mode)}{self._pattern_note}]"
        status = getattr(self, "_status_fn", None)
        if self._search_fn is not None and callable(status):
            try:
                text += f"      {status()}"
            except Exception:
                pass
        return text

    def _refresh_level(self) -> None:
        self.crumb.setText(self._crumb_text())
        self.search.blockSignals(True)
        self.search.clear()
        self.search.blockSignals(False)
        if self._search_fn is not None:
            self.search.setPlaceholderText(f"{self._stack[-1][1]}: type a name…")
        else:
            self.search.setPlaceholderText("Type a command…" if not self._stack else f"{self._stack[-1][1]} …")
        self._filter("")

    def _go_back(self) -> bool:
        if not self._stack:
            return False
        self._entries, _ = self._stack.pop()
        self._search_fn = None
        self._status_fn = None
        if not self._stack:
            self._entries = self._with_recent(self._root)
        self._refresh_level()
        return True

    def _on_text(self, text: str) -> None:
        # dynamic / extra searches hit the index: debounce them, static levels filter instantly
        mode, q = parse_mode(text) if not self._stack else ("all", text.strip())
        if self._search_fn is not None or mode in SEARCH_MODES or (
                mode == "all" and callable(self._extra_search) and not self._stack and len(q) >= 3):
            self._search_timer.start()
        else:
            self._filter(text)

    def _fill_mode(self, mode: str, q: str) -> None:
        """Prefix mode: the list is produced by mode_search only."""
        entries = []
        if callable(self._mode_search) and (q or mode in ("terminal", "terminal_delete")):
            try:
                entries = self._mode_search(mode, q) or []
            except Exception:
                entries = []
        for e in entries:
            e["_dynamic"] = True
            e.setdefault("_path", [e.get("category", ""), e["label"]])
            it = QListWidgetItem(f"{e.get('category', '')}  ·  {e['label']}")
            if e.get("icon"):
                it.setIcon(icons.icon(e["icon"], 12))
            if e.get("tooltip"):
                it.setToolTip(e["tooltip"])
            it.setData(ENTRY_ROLE, e)
            self.list.addItem(it)
        if self.list.count():
            self.list.setCurrentRow(0)

    # ------------------------------------------------------------------ deep search

    DEEP_MAX_DEPTH = 3

    def _deep_entries(self) -> list[dict]:
        """All leaves of sub-levels, flattened ("Sort by › Name"), so a query at
        the top level finds them directly (VS Code shows sub-commands the same way)."""
        if self._deep is not None:
            return self._deep
        out: list[dict] = []

        def walk(entry: dict, depth: int, category: str) -> None:
            if depth > self.DEEP_MAX_DEPTH:
                return
            for c in _children_of(entry):
                c["_path"] = [*entry["_path"], c["label"]]
                if c.get("children") is not None:
                    walk(c, depth + 1, category)
                else:
                    out.append({**c, "label": "  ›  ".join(c["_path"]), "category": category, "_deep": True})

        for root in self._root:
            if root.get("children") is not None and not callable(root.get("search")):
                walk(root, 1, root.get("category", ""))
        self._deep = out
        return out

    # ------------------------------------------------------------------ list

    @staticmethod
    def _matches(e: dict, q: str) -> bool:
        if not q:
            return True
        hay = (e.get("category", "") + " " + e.get("label", "")).lower()
        return all(tok in hay for tok in q.split())

    def _filter(self, q: str) -> None:
        mode, q = parse_mode(q) if not self._stack else ("all", q.strip())
        q = q.lower()
        note = ""
        if mode in SEARCH_MODES and q:
            from src.index.pattern import parse
            lab = parse(q).label
            note = f" · {lab}" if lab else ""
        if mode != self._mode or note != self._pattern_note:
            self._mode, self._pattern_note = mode, note
            self.crumb.setText(self._crumb_text())
        self.list.clear()
        if not self._stack and mode in SEARCH_MODES:
            self._fill_mode(mode, q)
            return
        if self._search_fn is not None:
            candidates = self._search_fn(q) if len(q) >= 2 else []
            for c in candidates:
                c.setdefault("_path", [*(label for _, label in self._stack), c["label"]])
                c["_dynamic"] = True
        else:
            candidates = list(self._entries)
            if q and not self._stack:
                shown = {PATH_SEP.join(e.get("_path", [e["label"]])) for e in candidates}
                candidates += [d for d in self._deep_entries() if PATH_SEP.join(d["_path"]) not in shown]
        for e in candidates:
            if not e.get("_dynamic") and not self._matches(e, q):
                continue
            label = e["label"]
            if e.get("category"):
                label = f"{e['category']}  ·  {label}"
            if e.get("children") is not None or callable(e.get("search")):
                label += "  ›"
            it = QListWidgetItem(label)
            if e.get("_recent"):
                it.setIcon(icons.icon("clock", 12))
                it.setToolTip("recently used")
            elif e.get("_deep"):
                it.setIcon(icons.icon("subtasks", 12))
            elif e.get("icon"):
                it.setIcon(icons.icon(e["icon"], 12))
            if e.get("checked") and not e.get("_recent"):
                it.setIcon(icons.icon("check", 12, theme.current().semantic_dot["success"]))
            sc = e.get("shortcut") or ""
            suffix = "      [recently used]" if e.get("_recent") else ""
            if sc:
                it.setToolTip(sc)
                suffix = f"      [{sc}]" + ("  · recently used" if e.get("_recent") else "")
            it.setText(label + suffix)
            it.setData(ENTRY_ROLE, e)
            self.list.addItem(it)
        if q and not self._stack and mode == "all" and callable(self._extra_search) and len(q) >= 3:
            try:
                extra = self._extra_search(q)
            except Exception:
                extra = []
            for e in extra:
                e["_dynamic"] = True
                e.setdefault("_path", [e.get("category", ""), e["label"]])
                it = QListWidgetItem(f"{e.get('category', '')}  ·  {e['label']}")
                if e.get("icon"):
                    it.setIcon(icons.icon(e["icon"], 12))
                if e.get("tooltip"):
                    it.setToolTip(e["tooltip"])
                it.setData(ENTRY_ROLE, e)
                self.list.addItem(it)
        if self.list.count():
            self.list.setCurrentRow(0)

    def _run_current(self) -> None:
        item = self.list.currentItem()
        if item is None:
            return
        entry = item.data(ENTRY_ROLE)
        if entry.get("children") is not None or callable(entry.get("search")):
            self._enter_level(entry)
            return
        if entry.get("keep_open"):
            # e.g. deleting history entries: run, then rebuild the list at the same cursor row
            row = self.list.currentRow()
            run = entry.get("run")
            if callable(run):
                run()
            self._filter(self.search.text())
            if self.list.count():
                self.list.setCurrentRow(min(row, self.list.count() - 1))
            return
        self.accept()
        if callable(self._on_run) and not entry.get("_dynamic"):
            try:
                self._on_run(PATH_SEP.join(entry.get("_path", [entry["label"]])))
            except Exception:
                pass
        run = entry.get("run")
        if callable(run):
            run()

    # ------------------------------------------------------------------ keys

    def eventFilter(self, obj, event):  # noqa: N802
        if obj is self.search and event.type() == event.Type.KeyPress:
            key = event.key()
            if key in (Qt.Key.Key_Down, Qt.Key.Key_Up, Qt.Key.Key_PageDown, Qt.Key.Key_PageUp):
                row = self.list.currentRow()
                step = 1 if key in (Qt.Key.Key_Down, Qt.Key.Key_PageDown) else -1
                if key in (Qt.Key.Key_PageDown, Qt.Key.Key_PageUp):
                    step *= 8
                self.list.setCurrentRow(max(0, min(self.list.count() - 1, row + step)))
                return True
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self._run_current()
                return True
            if key == Qt.Key.Key_Backspace and not self.search.text() and self._go_back():
                return True
            if key == Qt.Key.Key_Escape and self._go_back():
                return True
        return super().eventFilter(obj, event)

    def keyPressEvent(self, event):  # noqa: N802
        if event.key() == Qt.Key.Key_Escape and self._go_back():
            return
        super().keyPressEvent(event)

    def showEvent(self, event):  # noqa: N802
        super().showEvent(event)
        par = self.parent()
        if par is not None:
            geo = par.window().frameGeometry()
            self.move(geo.center().x() - self.width() // 2,
                      geo.center().y() - self.height() // 2)
