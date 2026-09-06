"""External editor settings: the command line used by F4."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
)

from src.editor.external import DEFAULT_EDITOR, resolve_program, split_command
from src.solarqt import theme
from src.solarqt.widgets import ActionButton, Heading, PrimaryButton, QuietButton


class EditorSettingsDialog(QDialog):
    """Edits the external editor command; empty = built-in editor."""

    def __init__(self, command: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("External Editor")
        self.setMinimumWidth(theme.px(560))

        layout = QVBoxLayout(self)
        layout.setSpacing(theme.px(10))
        layout.addWidget(Heading("External editor (F4)", 2))
        hint = QLabel("Command line of the editor. A bare program name is looked up on PATH; "
                      "put {file} where the file paths belong, otherwise they are appended. "
                      "Leave it empty to use the built-in editor.")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        row = QHBoxLayout()
        self._cmd = QLineEdit(command)
        self._cmd.setPlaceholderText(f"{DEFAULT_EDITOR}   (built-in editor when empty)")
        self._cmd.textChanged.connect(self._update_status)
        row.addWidget(self._cmd, 1)
        browse = ActionButton("Browse…", "folder")
        browse.clicked.connect(self._browse)
        row.addWidget(browse)
        reset = QuietButton("Default")
        reset.clicked.connect(lambda: self._cmd.setText(DEFAULT_EDITOR))
        row.addWidget(reset)
        layout.addLayout(row)

        self._status = QLabel("")
        self._status.setObjectName("faintLabel")
        self._status.setWordWrap(True)
        layout.addWidget(self._status)
        self._update_status(command)

        buttons = QDialogButtonBox()
        ok = PrimaryButton("Save")
        cancel = QuietButton("Cancel")
        buttons.addButton(ok, QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton(cancel, QDialogButtonBox.ButtonRole.RejectRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Choose editor", "", "Programs (*.exe *.cmd *.bat);;All files (*)")
        if path:
            self._cmd.setText(f'"{path}"' if " " in path else path)

    def _update_status(self, text: str) -> None:
        tokens = split_command(text)
        if not tokens:
            self._status.setText("Built-in editor will be used.")
            return
        prog = resolve_program(tokens[0])
        self._status.setText(f"Found: {prog}" if prog else f"Not found: {tokens[0]}  –  F4 falls back to the built-in editor")

    def command(self) -> str:
        return self._cmd.text().strip()
