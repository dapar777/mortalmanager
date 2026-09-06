"""Hash computation dialog."""

from __future__ import annotations

import hashlib
from pathlib import Path

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QVBoxLayout,
)


class _HashThread(QThread):
    result = Signal(str, str, str)   # path, algo, hash
    progress = Signal(int)

    def __init__(self, paths: list[str], algos: list[str]) -> None:
        super().__init__()
        self._paths = paths
        self._algos = algos

    def run(self) -> None:
        total = len(self._paths) * len(self._algos)
        done = 0
        for path in self._paths:
            for algo in self._algos:
                h = hashlib.new(algo)
                try:
                    with open(path, "rb") as f:
                        for chunk in iter(lambda: f.read(65536), b""):
                            h.update(chunk)
                    self.result.emit(path, algo, h.hexdigest())
                except Exception as exc:
                    self.result.emit(path, algo, f"ERROR: {exc}")
                done += 1
                self.progress.emit(int(done / total * 100))


class HashDialog(QDialog):
    def __init__(self, paths: list[str], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Compute Hashes")
        self.resize(600, 400)
        self._paths = paths
        self._build_ui()
        self._start()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"Computing hashes for {len(self._paths)} file(s)…"))
        self._progress = QProgressBar()
        layout.addWidget(self._progress)
        self._output = QPlainTextEdit()
        self._output.setReadOnly(True)
        layout.addWidget(self._output, stretch=1)
        btn = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        btn.rejected.connect(self.reject)
        layout.addWidget(btn)

    def _start(self) -> None:
        self._thread = _HashThread(self._paths, ["md5", "sha1", "sha256", "sha512"])
        self._thread.result.connect(self._on_result)
        self._thread.progress.connect(self._progress.setValue)
        self._thread.start()

    def _on_result(self, path: str, algo: str, digest: str) -> None:
        name = Path(path).name
        self._output.appendPlainText(f"[{algo.upper()}] {name}\n  {digest}\n")
