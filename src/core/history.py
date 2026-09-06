"""Core – navigation history (back / forward)."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field


@dataclass
class HistoryEntry:
    path: str
    cursor_name: str = ""   # filename to restore cursor to after navigation


class NavigationHistory:
    """Per-panel navigation history stack (back / forward)."""

    MAX_SIZE = 200

    def __init__(self) -> None:
        self._back: deque[HistoryEntry] = deque(maxlen=self.MAX_SIZE)
        self._forward: deque[HistoryEntry] = deque(maxlen=self.MAX_SIZE)
        self._current: HistoryEntry | None = None

    @property
    def current(self) -> HistoryEntry | None:
        return self._current

    @property
    def can_go_back(self) -> bool:
        return bool(self._back)

    @property
    def can_go_forward(self) -> bool:
        return bool(self._forward)

    def push(self, path: str, cursor_name: str = "") -> None:
        """Navigate to a new path, clearing forward history."""
        if self._current is not None:
            self._back.append(self._current)
        self._current = HistoryEntry(path=path, cursor_name=cursor_name)
        self._forward.clear()

    def go_back(self) -> HistoryEntry | None:
        """Move back; returns the entry to navigate to, or None."""
        if not self._back:
            return None
        if self._current is not None:
            self._forward.appendleft(self._current)
        self._current = self._back.pop()
        return self._current

    def go_forward(self) -> HistoryEntry | None:
        """Move forward; returns the entry to navigate to, or None."""
        if not self._forward:
            return None
        if self._current is not None:
            self._back.append(self._current)
        self._current = self._forward.popleft()
        return self._current

    def all_paths(self) -> list[str]:
        """All unique visited paths (most recent first)."""
        seen: set[str] = set()
        result: list[str] = []
        entries = list(reversed(self._back)) + (
            [self._current] if self._current else []
        ) + list(self._forward)
        for e in reversed(entries):
            if e.path not in seen:
                seen.add(e.path)
                result.append(e.path)
        return result
