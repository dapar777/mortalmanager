"""Core – undo/redo stack for reversible file operations."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path

logger = logging.getLogger(__name__)


class UndoActionType(Enum):
    MOVE = auto()
    RENAME = auto()
    DELETE = auto()
    MKDIR = auto()
    COPY = auto()


@dataclass
class UndoAction:
    action_type: UndoActionType
    description: str
    # List of (source, destination) pairs
    pairs: list[tuple[str, str]] = field(default_factory=list)
    # For MKDIR: path created
    created_paths: list[str] = field(default_factory=list)
    # For DELETE: original paths (restore from trash / recycle bin)
    deleted_paths: list[str] = field(default_factory=list)


class UndoManager:
    """Maintains an undo/redo stack of reversible file operations."""

    MAX_UNDO = 50

    def __init__(self) -> None:
        self._undo_stack: list[UndoAction] = []
        self._redo_stack: list[UndoAction] = []

    @property
    def can_undo(self) -> bool:
        return bool(self._undo_stack)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo_stack)

    @property
    def undo_description(self) -> str:
        if self._undo_stack:
            return self._undo_stack[-1].description
        return ""

    @property
    def redo_description(self) -> str:
        if self._redo_stack:
            return self._redo_stack[-1].description
        return ""

    def push(self, action: UndoAction) -> None:
        self._undo_stack.append(action)
        if len(self._undo_stack) > self.MAX_UNDO:
            self._undo_stack.pop(0)
        self._redo_stack.clear()

    def pop_undo(self) -> UndoAction | None:
        if not self._undo_stack:
            return None
        action = self._undo_stack.pop()
        self._redo_stack.append(action)
        return action

    def pop_redo(self) -> UndoAction | None:
        if not self._redo_stack:
            return None
        action = self._redo_stack.pop()
        self._undo_stack.append(action)
        return action

    def clear(self) -> None:
        self._undo_stack.clear()
        self._redo_stack.clear()
