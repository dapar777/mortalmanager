"""File index settings: what to index, what to skip, how often to rescan."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QVBoxLayout,
)

from src.index import IndexConfig
from src.solarqt import theme
from src.solarqt.widgets import ActionButton, Heading, PrimaryButton, QuietButton


class IndexSettingsDialog(QDialog):
    """Edits an IndexConfig; the caller applies it through IndexService."""

    def __init__(self, config: IndexConfig, status_text: str, parent=None, on_rescan=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("File Index")
        self.setMinimumWidth(theme.px(560))
        self._on_rescan = on_rescan

        layout = QVBoxLayout(self)
        layout.setSpacing(theme.px(10))
        layout.addWidget(Heading("File index", 2))
        hint = QLabel("The palette finds files by name from this index. One running instance keeps it "
                      "up to date for all the others (full scan, then live changes).")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self._status = QLabel(status_text)
        self._status.setObjectName("faintLabel")
        layout.addWidget(self._status)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)
        self._enabled = QCheckBox("Keep the index up to date")
        self._enabled.setChecked(config.enabled)
        form.addRow("", self._enabled)

        self._roots = QPlainTextEdit("\n".join(config.roots))
        self._roots.setPlaceholderText("C:\\")
        self._roots.setFixedHeight(theme.px(60))
        form.addRow("Index (one per line)", self._roots)

        self._names = QPlainTextEdit("\n".join(config.exclude_names))
        self._names.setFixedHeight(theme.px(90))
        form.addRow("Skip folder names", self._names)

        self._paths = QPlainTextEdit("\n".join(config.exclude_paths))
        self._paths.setFixedHeight(theme.px(110))
        form.addRow("Skip paths", self._paths)

        self._hours = QDoubleSpinBox()
        self._hours.setRange(0.5, 24 * 30)
        self._hours.setDecimals(1)
        self._hours.setSuffix(" h")
        self._hours.setValue(config.rescan_hours)
        form.addRow("Full rescan every", self._hours)
        layout.addLayout(form)

        row = QHBoxLayout()
        rescan = ActionButton("Rescan now", "refresh")
        rescan.clicked.connect(self._rescan)
        row.addWidget(rescan)
        row.addStretch(1)
        layout.addLayout(row)

        buttons = QDialogButtonBox()
        ok = PrimaryButton("Save")
        cancel = QuietButton("Cancel")
        buttons.addButton(ok, QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton(cancel, QDialogButtonBox.ButtonRole.RejectRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def set_status(self, text: str) -> None:
        self._status.setText(text)

    def _rescan(self) -> None:
        if callable(self._on_rescan):
            self._on_rescan()
            self._status.setText("rescan requested…")

    @staticmethod
    def _lines(edit: QPlainTextEdit) -> list[str]:
        return [ln.strip() for ln in edit.toPlainText().splitlines() if ln.strip()]

    def result_config(self) -> IndexConfig:
        return IndexConfig(
            enabled=self._enabled.isChecked(),
            roots=self._lines(self._roots) or ["C:\\"],
            exclude_names=self._lines(self._names),
            exclude_paths=self._lines(self._paths),
            rescan_hours=float(self._hours.value()),
        )
