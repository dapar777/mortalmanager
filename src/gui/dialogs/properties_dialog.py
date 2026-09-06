"""Properties dialog – shows file/directory details."""

from __future__ import annotations

import os
import stat
from pathlib import Path

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QVBoxLayout,
)

from src.core.file_model import format_size


class PropertiesDialog(QDialog):
    def __init__(self, paths: list[str], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Properties")
        self.setMinimumWidth(400)
        self._build_ui(paths)

    def _build_ui(self, paths: list[str]) -> None:
        layout = QVBoxLayout(self)
        form = QFormLayout()

        if len(paths) == 1:
            p = Path(paths[0])
            form.addRow("Name:", QLabel(p.name))
            form.addRow("Path:", QLabel(str(p.parent)))
            try:
                st = p.stat()
                size = st.st_size
                if p.is_dir():
                    # Calculate dir size
                    size = sum(
                        f.stat().st_size
                        for f in p.rglob("*")
                        if f.is_file()
                    )
                form.addRow("Size:", QLabel(f"{format_size(size)} ({size:,} bytes)"))
                from datetime import datetime
                form.addRow("Modified:", QLabel(datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S")))
                form.addRow("Created:", QLabel(datetime.fromtimestamp(st.st_ctime).strftime("%Y-%m-%d %H:%M:%S")))
                form.addRow("Type:", QLabel("Directory" if p.is_dir() else p.suffix or "File"))
            except Exception as exc:
                form.addRow("Error:", QLabel(str(exc)))
        else:
            form.addRow("Items:", QLabel(str(len(paths))))
            total = 0
            for path_str in paths:
                p = Path(path_str)
                try:
                    if p.is_file():
                        total += p.stat().st_size
                    elif p.is_dir():
                        total += sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
                except Exception:
                    pass
            form.addRow("Total size:", QLabel(f"{format_size(total)} ({total:,} bytes)"))

        layout.addLayout(form)
        btn = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        btn.accepted.connect(self.accept)
        layout.addWidget(btn)
