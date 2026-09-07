"""Files on the system clipboard (Ctrl+C / Ctrl+X / Ctrl+V), Explorer-compatible.

Paths go on the clipboard as ``file:`` URLs (CF_HDROP on Windows through Qt)
plus Explorer's ``Preferred DropEffect`` (1 = copy, 2 = move), so a cut in
Ultimate Commander pastes as a move in Explorer and vice versa.
"""

from __future__ import annotations

import struct

from PySide6.QtCore import QMimeData, QUrl
from PySide6.QtWidgets import QApplication

DROP_EFFECT_FORMAT = "Preferred DropEffect"
_COPY, _MOVE = 1, 2


def set_files(paths: list[str], cut: bool = False) -> None:
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(p) for p in paths])
    mime.setData(DROP_EFFECT_FORMAT, struct.pack("<I", _MOVE if cut else _COPY))
    QApplication.clipboard().setMimeData(mime)


def get_files() -> tuple[list[str], bool]:
    """(local paths, cut) from the clipboard; ([], False) when it holds no files."""
    mime = QApplication.clipboard().mimeData()
    if mime is None or not mime.hasUrls():
        return [], False
    paths = [u.toLocalFile() for u in mime.urls() if u.isLocalFile()]
    cut = False
    if mime.hasFormat(DROP_EFFECT_FORMAT):
        raw = bytes(mime.data(DROP_EFFECT_FORMAT))
        if len(raw) >= 4:
            cut = bool(struct.unpack("<I", raw[:4])[0] & _MOVE)
    return paths, cut


def clear() -> None:
    QApplication.clipboard().clear()
