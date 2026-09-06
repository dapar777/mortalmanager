"""Mkdir and New File dialogs."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QLineEdit,
    QVBoxLayout,
)


class MkdirDialog(QDialog):
    def __init__(self, parent_path: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Create New Folder")
        self._parent_path = parent_path
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"In: {self._parent_path}"))
        layout.addWidget(QLabel("Folder name:"))
        self._name_edit = QLineEdit("New Folder")
        self._name_edit.selectAll()
        layout.addWidget(self._name_edit)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @property
    def new_path(self) -> str:
        return str(Path(self._parent_path) / self._name_edit.text().strip())


class NewFileDialog(QDialog):
    def __init__(self, parent_path: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Create New File")
        self._parent_path = parent_path
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"In: {self._parent_path}"))
        layout.addWidget(QLabel("File name:"))
        self._name_edit = QLineEdit("new_file.txt")
        self._name_edit.selectAll()
        layout.addWidget(self._name_edit)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @property
    def new_path(self) -> str:
        return str(Path(self._parent_path) / self._name_edit.text().strip())
