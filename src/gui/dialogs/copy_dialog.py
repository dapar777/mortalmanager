"""Copy/Move dialog – destination selector with overwrite option."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)


class CopyDialog(QDialog):
    """Shown before copy/move operations to confirm destination and options."""

    def __init__(
        self,
        sources: list[str],
        default_dest: str,
        operation: str = "copy",
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._operation = operation
        title = "Copy" if operation == "copy" else "Move"
        self.setWindowTitle(f"{title} Files")
        self.setMinimumWidth(500)
        self._build_ui(sources, default_dest, title)

    def _build_ui(self, sources: list[str], default_dest: str, title: str) -> None:
        layout = QVBoxLayout(self)

        count = len(sources)
        if count == 1:
            desc = f"{title}: {Path(sources[0]).name}"
        else:
            desc = f"{title} {count} items"
        layout.addWidget(QLabel(desc))

        layout.addWidget(QLabel("Destination:"))
        dest_row = QHBoxLayout()
        self._dest_edit = QLineEdit(default_dest)
        dest_row.addWidget(self._dest_edit)
        btn_browse = QPushButton("Browse…")
        btn_browse.clicked.connect(self._browse)
        dest_row.addWidget(btn_browse)
        layout.addLayout(dest_row)

        self._overwrite_cb = QCheckBox("Overwrite existing files")
        layout.addWidget(self._overwrite_cb)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _browse(self) -> None:
        from PySide6.QtWidgets import QFileDialog
        path = QFileDialog.getExistingDirectory(
            self, "Select Destination", self._dest_edit.text()
        )
        if path:
            self._dest_edit.setText(path)

    @property
    def destination(self) -> str:
        return self._dest_edit.text().strip()

    @property
    def overwrite(self) -> bool:
        return self._overwrite_cb.isChecked()
