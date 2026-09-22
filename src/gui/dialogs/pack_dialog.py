"""Pack files (Alt+F5) – target archive, format and options, as in Total Commander."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
)

from src.archive import archive_manager as am
from src.solarqt import theme
from src.solarqt.widgets import ActionButton, Heading, PrimaryButton, QuietButton

#: offered formats – label, extension; only formats we can actually write
FORMATS: list[tuple[str, str]] = [
    ("ZIP", "zip"),
    ("TAR", "tar"),
    ("TAR + gzip", "tar.gz"),
    ("TAR + bzip2", "tar.bz2"),
    ("TAR + xz", "tar.xz"),
]

_LEVELS = [("Store (no compression)", 0), ("Fast", 1), ("Normal", 6), ("Best", 9)]


@dataclass
class PackOptions:
    """What the user chose."""

    target: Path
    level: int | None = None
    store_paths: bool = True
    move_to_archive: bool = False
    append: bool = False          # target exists and should be added to
    password: str | None = None   # only formats with can_encrypt (7z) offer this


def available_formats() -> list[tuple[str, str]]:
    """FORMATS plus 7z when py7zr is installed."""
    formats = list(FORMATS)
    if am.handler_for_new(Path("x.7z")) is not None:
        formats.insert(1, ("7z", "7z"))
    return formats


class PackDialog(QDialog):
    """Target name, format and packing options."""

    def __init__(self, sources: list[str], default_dir: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Pack Files")
        self.setMinimumWidth(theme.px(620))
        self._sources = [Path(s) for s in sources]
        self._formats = available_formats()

        layout = QVBoxLayout(self)
        layout.setSpacing(theme.px(10))
        count = len(self._sources)
        layout.addWidget(Heading(
            f"Pack {count} item{'s' if count != 1 else ''} into an archive", 2))

        row = QHBoxLayout()
        self._target = QLineEdit(str(Path(default_dir) / self._default_name("zip")))
        row.addWidget(self._target, 1)
        browse = ActionButton("Browse…", "folder")
        browse.clicked.connect(self._browse)
        row.addWidget(browse)
        layout.addLayout(row)

        opts = QHBoxLayout()
        opts.setSpacing(theme.px(10))
        self._format = QComboBox()
        for label, _ext in self._formats:
            self._format.addItem(label)
        self._format.currentIndexChanged.connect(self._format_changed)
        opts.addWidget(QLabel("Format:"))
        opts.addWidget(self._format)
        self._level = QComboBox()
        for label, _value in _LEVELS:
            self._level.addItem(label)
        self._level.setCurrentIndex(2)                      # Normal
        opts.addWidget(QLabel("Compression:"))
        opts.addWidget(self._level)
        opts.addStretch(1)
        layout.addLayout(opts)

        self._store_paths = QCheckBox("Store folder names (keep the directory structure)")
        self._store_paths.setChecked(True)
        layout.addWidget(self._store_paths)
        self._move = QCheckBox("Move to archive (delete the originals afterwards)")
        layout.addWidget(self._move)

        self._encrypt = QCheckBox("Protect with a password…")
        self._encrypt.toggled.connect(self._ask_password)
        layout.addWidget(self._encrypt)
        self._password: str | None = None

        self._status = QLabel("")
        self._status.setObjectName("faintLabel")
        self._status.setWordWrap(True)
        layout.addWidget(self._status)
        self._target.textChanged.connect(self._update_status)
        self._update_status(self._target.text())
        self._update_encrypt_state()

        buttons = QDialogButtonBox()
        ok = PrimaryButton("Pack")
        ok.clicked.connect(self.accept)
        cancel = QuietButton("Cancel")
        cancel.clicked.connect(self.reject)
        buttons.addButton(ok, QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton(cancel, QDialogButtonBox.ButtonRole.RejectRole)
        layout.addWidget(buttons)

    # ------------------------------------------------------------------ helpers

    def _default_name(self, ext: str) -> str:
        """One item: its own name. Several: the name of their folder (C2)."""
        if len(self._sources) == 1:
            base = self._sources[0].stem if self._sources[0].is_file() else self._sources[0].name
        elif self._sources:
            base = self._sources[0].parent.name or "archive"
        else:
            base = "archive"
        return f"{base}.{ext}"

    def _current_ext(self) -> str:
        return self._formats[max(0, self._format.currentIndex())][1]

    def _ask_password(self, on: bool) -> None:
        """Ask for the password right away: a checkbox alone would leave the user
        wondering when it will be asked, and an empty one would be a silent no-op."""
        from .password_dialog import NewPasswordDialog

        if not on:
            self._password = None
            return
        password = NewPasswordDialog.ask(self)
        if password:
            self._password = password
        else:
            self._encrypt.setChecked(False)          # cancelled: leave it unchecked

    def _update_encrypt_state(self) -> None:
        """Only 7z can be encrypted here; the box says why when it cannot."""
        can = am.can_encrypt(Path(self._target.text().strip() or "x.zip"))
        self._encrypt.setEnabled(can)
        if not can:
            self._encrypt.setChecked(False)
            self._password = None
            self._encrypt.setToolTip("Only 7z archives can be encrypted")
        else:
            self._encrypt.setToolTip("")

    def _format_changed(self) -> None:
        """Swap the extension of the typed name, keep the folder and the stem."""
        current = Path(self._target.text())
        name = current.name
        for _label, ext in self._formats:
            if name.lower().endswith("." + ext):
                name = name[: -(len(ext) + 1)]
                break
        else:
            name = current.stem or name
        self._target.setText(str(current.with_name(f"{name}.{self._current_ext()}")))

    def _browse(self) -> None:
        ext = self._current_ext()
        chosen, _ = QFileDialog.getSaveFileName(
            self, "Pack to", self._target.text(), f"Archive (*.{ext});;All files (*)")
        if chosen:
            self._target.setText(chosen)

    def _update_status(self, text: str) -> None:
        self._update_encrypt_state()
        target = Path(text.strip())
        if not text.strip():
            self._status.setText("Enter the archive name.")
            return
        if am.handler_for_new(target) is None:
            self._status.setText(f"Unknown archive format: {target.name}")
            return
        if target.exists():
            self._status.setText(f"{target.name} exists – you will be asked to overwrite or add to it.")
        else:
            self._status.setText("")

    # ------------------------------------------------------------------ result

    def options(self) -> PackOptions:
        level = _LEVELS[max(0, self._level.currentIndex())][1]
        return PackOptions(
            target=Path(self._target.text().strip()),
            level=level,
            store_paths=self._store_paths.isChecked(),
            move_to_archive=self._move.isChecked(),
            password=self._password if self._encrypt.isChecked() else None,
        )
