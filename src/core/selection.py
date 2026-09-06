"""Core – selection management."""

from __future__ import annotations

import fnmatch
import re
from pathlib import Path

from .file_model import FileEntry


class SelectionManager:
    """Manages the set of selected file entries in a panel."""

    def __init__(self) -> None:
        self._selected: set[str] = set()  # stores full path strings

    # ------------------------------------------------------------------ basics

    def select(self, entry: FileEntry) -> None:
        if not entry.is_parent:
            self._selected.add(entry.full_path)

    def deselect(self, entry: FileEntry) -> None:
        self._selected.discard(entry.full_path)

    def toggle(self, entry: FileEntry) -> None:
        if entry.full_path in self._selected:
            self.deselect(entry)
        else:
            self.select(entry)

    def is_selected(self, entry: FileEntry) -> bool:
        return entry.full_path in self._selected

    def clear(self) -> None:
        self._selected.clear()

    @property
    def count(self) -> int:
        return len(self._selected)

    @property
    def selected_paths(self) -> list[str]:
        return list(self._selected)

    # ------------------------------------------------------------------ bulk ops

    def select_all(self, entries: list[FileEntry]) -> None:
        for e in entries:
            if not e.is_parent:
                self._selected.add(e.full_path)

    def deselect_all(self) -> None:
        self._selected.clear()

    def invert(self, entries: list[FileEntry]) -> None:
        for e in entries:
            if e.is_parent:
                continue
            if e.full_path in self._selected:
                self._selected.discard(e.full_path)
            else:
                self._selected.add(e.full_path)

    # ------------------------------------------------------------------ pattern

    def select_by_mask(
        self,
        entries: list[FileEntry],
        pattern: str,
        use_regex: bool = False,
        case_sensitive: bool = False,
    ) -> None:
        """Select entries matching a glob mask or regex pattern."""
        for e in entries:
            if e.is_parent:
                continue
            name = e.name if case_sensitive else e.name.lower()
            pat = pattern if case_sensitive else pattern.lower()
            if use_regex:
                try:
                    if re.search(pat, name):
                        self._selected.add(e.full_path)
                except re.error:
                    pass
            else:
                if fnmatch.fnmatch(name, pat):
                    self._selected.add(e.full_path)

    def deselect_by_mask(
        self,
        entries: list[FileEntry],
        pattern: str,
        use_regex: bool = False,
        case_sensitive: bool = False,
    ) -> None:
        for e in entries:
            if e.is_parent:
                continue
            name = e.name if case_sensitive else e.name.lower()
            pat = pattern if case_sensitive else pattern.lower()
            match = False
            if use_regex:
                try:
                    match = bool(re.search(pat, name))
                except re.error:
                    pass
            else:
                match = fnmatch.fnmatch(name, pat)
            if match:
                self._selected.discard(e.full_path)

    # ------------------------------------------------------------------ helpers

    def total_size(self, entries: list[FileEntry]) -> int:
        """Sum of sizes for selected entries."""
        path_map = {e.full_path: e for e in entries}
        return sum(
            path_map[p].size
            for p in self._selected
            if p in path_map and not path_map[p].is_dir
        )

    def selected_entries(self, entries: list[FileEntry]) -> list[FileEntry]:
        path_set = set(self._selected)
        return [e for e in entries if e.full_path in path_set]
