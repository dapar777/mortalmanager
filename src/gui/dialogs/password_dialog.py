"""Ask for an archive password (opening) or set one (packing)."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QLineEdit,
    QVBoxLayout,
)

from src.solarqt import theme
from src.solarqt.widgets import Heading, PrimaryButton, QuietButton


class PasswordDialog(QDialog):
    """One password field, hidden by default, with a "show" toggle.

    ``retry`` turns the hint into a warning: the previous attempt was wrong.
    """

    def __init__(self, archive: Path | str, parent=None, retry: bool = False) -> None:
        super().__init__(parent)
        name = Path(archive).name
        self.setWindowTitle("Password")
        self.setMinimumWidth(theme.px(440))

        layout = QVBoxLayout(self)
        layout.setSpacing(theme.px(10))
        layout.addWidget(Heading("Encrypted archive", 2))

        hint = QLabel(f"Wrong password for {name}. Try again:" if retry
                      else f"{name} is password protected.")
        hint.setObjectName("warningLabel" if retry else "hint")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self._edit = QLineEdit()
        self._edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._edit.setPlaceholderText("Password")
        self._edit.returnPressed.connect(self.accept)
        layout.addWidget(self._edit)

        self._show = QCheckBox("Show password")
        self._show.toggled.connect(self._toggle_echo)
        layout.addWidget(self._show)

        self._remember = QCheckBox("Remember while the program runs")
        self._remember.setChecked(True)
        self._remember.setToolTip("The password is kept in memory only, never written to disk")
        layout.addWidget(self._remember)

        buttons = QDialogButtonBox()
        ok = PrimaryButton("OK")
        ok.clicked.connect(self.accept)
        cancel = QuietButton("Cancel")
        cancel.clicked.connect(self.reject)
        buttons.addButton(ok, QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton(cancel, QDialogButtonBox.ButtonRole.RejectRole)
        layout.addWidget(buttons)
        self._edit.setFocus()

    def _toggle_echo(self, shown: bool) -> None:
        self._edit.setEchoMode(
            QLineEdit.EchoMode.Normal if shown else QLineEdit.EchoMode.Password)

    @property
    def password(self) -> str:
        return self._edit.text()

    @property
    def remember(self) -> bool:
        return self._remember.isChecked()

    @staticmethod
    def ask(archive: Path | str, parent=None, retry: bool = False) -> tuple[str | None, bool]:
        """Show the dialog; returns (password or None when cancelled, remember)."""
        dlg = PasswordDialog(archive, parent, retry)
        try:
            if dlg.exec() != QDialog.DialogCode.Accepted or not dlg.password:
                return None, False
            return dlg.password, dlg.remember
        finally:
            dlg.deleteLater()


class NewPasswordDialog(QDialog):
    """Password for a *new* archive: typed twice so a typo cannot lock it."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Encrypt Archive")
        self.setMinimumWidth(theme.px(440))

        layout = QVBoxLayout(self)
        layout.setSpacing(theme.px(10))
        layout.addWidget(Heading("Protect the archive with a password", 2))
        hint = QLabel("7z archives are encrypted with AES-256. Lose the password and the "
                      "content is gone – there is no way back.")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self._first = QLineEdit()
        self._first.setEchoMode(QLineEdit.EchoMode.Password)
        self._first.setPlaceholderText("Password")
        self._first.textChanged.connect(self._check)
        layout.addWidget(self._first)

        self._again = QLineEdit()
        self._again.setEchoMode(QLineEdit.EchoMode.Password)
        self._again.setPlaceholderText("Password again")
        self._again.textChanged.connect(self._check)
        self._again.returnPressed.connect(self._accept_if_valid)
        layout.addWidget(self._again)

        show = QCheckBox("Show password")
        show.toggled.connect(self._toggle_echo)
        layout.addWidget(show)

        self._status = QLabel("")
        self._status.setObjectName("faintLabel")
        layout.addWidget(self._status)

        buttons = QDialogButtonBox()
        self._ok = PrimaryButton("OK")
        self._ok.clicked.connect(self._accept_if_valid)
        self._ok.setEnabled(False)
        cancel = QuietButton("Cancel")
        cancel.clicked.connect(self.reject)
        buttons.addButton(self._ok, QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton(cancel, QDialogButtonBox.ButtonRole.RejectRole)
        layout.addWidget(buttons)
        self._first.setFocus()

    def _toggle_echo(self, shown: bool) -> None:
        mode = QLineEdit.EchoMode.Normal if shown else QLineEdit.EchoMode.Password
        self._first.setEchoMode(mode)
        self._again.setEchoMode(mode)

    def _check(self) -> None:
        first, again = self._first.text(), self._again.text()
        if not first:
            self._status.setText("")
        elif again and first != again:
            self._status.setText("The two passwords differ.")
        else:
            self._status.setText("")
        self._ok.setEnabled(bool(first) and first == again)

    def _accept_if_valid(self) -> None:
        if self._ok.isEnabled():
            self.accept()

    @property
    def password(self) -> str:
        return self._first.text()

    @staticmethod
    def ask(parent=None) -> str | None:
        dlg = NewPasswordDialog(parent)
        try:
            if dlg.exec() != QDialog.DialogCode.Accepted or not dlg.password:
                return None
            return dlg.password
        finally:
            dlg.deleteLater()
