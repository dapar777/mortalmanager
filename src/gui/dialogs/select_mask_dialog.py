"""Selection by mask/regex dialog."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QLineEdit,
    QVBoxLayout,
)


class SelectMaskDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Select by Mask")
        self.setMinimumWidth(350)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Pattern (e.g. *.txt or report_*.pdf):"))
        self._pattern_edit = QLineEdit("*")
        layout.addWidget(self._pattern_edit)
        self._regex_cb = QCheckBox("Regular expression")
        self._case_cb = QCheckBox("Case-sensitive")
        layout.addWidget(self._regex_cb)
        layout.addWidget(self._case_cb)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @property
    def pattern(self) -> str:
        return self._pattern_edit.text()

    @property
    def use_regex(self) -> bool:
        return self._regex_cb.isChecked()

    @property
    def case_sensitive(self) -> bool:
        return self._case_cb.isChecked()
