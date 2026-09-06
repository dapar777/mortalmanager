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

Rule for the app: every user-facing command lives in
MainWindow._build_palette_commands – add new features there first.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
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


def _children_of(entry: dict) -> list[dict]:
    children = entry.get("children")
    if callable(children):
        children = children()
    return list(children or [])


class CommandPalette(QDialog):
    def __init__(self, entries: list[dict], parent=None, title: str = "Commands",
                 recent: list[str] | None = None, on_run=None) -> None:
        super().__init__(parent)
        for e in entries:
            e.setdefault("_path", [e["label"]])
        self._root = entries
        self._recent = list(recent or [])
        self._on_run = on_run
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
        self.list.setUniformItemSizes(True)
        self.list.setIconSize(icons.qsize(12))
        hint = QLabel("↑↓ move   Enter run / open   Backspace back   Esc close")
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

        self.search.textChanged.connect(self._filter)
        self.list.itemActivated.connect(lambda _: self._run_current())
        self.search.installEventFilter(self)

        self._enter_level(None)
        self.search.setFocus()

    # ------------------------------------------------------------------ levels

    def _enter_level(self, entry: dict | None) -> None:
        if entry is not None:
            children = _children_of(entry)
            for c in children:
                c["_path"] = [*entry["_path"], c["label"]]
            self._stack.append((self._entries, entry["label"]))
            self._entries = children
        self._refresh_level()

    # ------------------------------------------------------------------ recently used

    def _resolve(self, path: list[str]) -> dict | None:
        """Find the leaf entry for a label path, descending through children."""
        level = self._root
        entry = None
        for i, label in enumerate(path):
            entry = next((e for e in level if e["label"] == label), None)
            if entry is None:
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
        return "  ›  ".join([self._title, *(label for _, label in self._stack)])

    def _refresh_level(self) -> None:
        self.crumb.setText(self._crumb_text())
        self.search.blockSignals(True)
        self.search.clear()
        self.search.blockSignals(False)
        self.search.setPlaceholderText("Type a command…" if not self._stack else f"{self._stack[-1][1]} …")
        self._filter("")

    def _go_back(self) -> bool:
        if not self._stack:
            return False
        self._entries, _ = self._stack.pop()
        if not self._stack:
            self._entries = self._with_recent(self._root)
        self._refresh_level()
        return True

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
            if root.get("children") is not None:
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
        q = q.strip().lower()
        self.list.clear()
        candidates = list(self._entries)
        if q and not self._stack:
            shown = {PATH_SEP.join(e.get("_path", [e["label"]])) for e in candidates}
            candidates += [d for d in self._deep_entries() if PATH_SEP.join(d["_path"]) not in shown]
        for e in candidates:
            if not self._matches(e, q):
                continue
            label = e["label"]
            if e.get("category"):
                label = f"{e['category']}  ·  {label}"
            if e.get("children") is not None:
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
        if self.list.count():
            self.list.setCurrentRow(0)

    def _run_current(self) -> None:
        item = self.list.currentItem()
        if item is None:
            return
        entry = item.data(ENTRY_ROLE)
        if entry.get("children") is not None:
            self._enter_level(entry)
            return
        self.accept()
        if callable(self._on_run):
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
