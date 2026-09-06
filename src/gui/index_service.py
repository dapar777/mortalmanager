"""Qt-side wrapper of the background file indexer (one per window).

Starts the Indexer thread a few seconds after the window is up, exposes a
reader connection for palette searches, relays status to the GUI thread via a
signal and reads its configuration from AppConfig (index_* fields).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal

from src.index import FileIndex, IndexConfig, Indexer
from src.settings.config import ConfigManager

logger = logging.getLogger(__name__)


class IndexService(QObject):
    status_changed = Signal(dict)

    START_DELAY_MS = 3000

    def __init__(self, cfg: ConfigManager, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._cfg = cfg
        self.db_path = str(cfg.data_dir / "index.db")
        self._reader: FileIndex | None = None
        self._indexer = Indexer(self.db_path, self.config, on_status=self._relay)
        self._status: dict = {"message": "starting"}
        QTimer.singleShot(self.START_DELAY_MS, self._indexer.start)

    # ------------------------------------------------------------------ config

    def config(self) -> IndexConfig:
        c = self._cfg.config
        return IndexConfig(
            enabled=bool(c.index_enabled),
            roots=list(c.index_roots),
            exclude_names=list(c.index_exclude_names),
            exclude_paths=list(c.index_exclude_paths),
            rescan_hours=float(c.index_rescan_hours),
        )

    def apply_config(self, config: IndexConfig) -> None:
        c = self._cfg.config
        c.index_enabled = config.enabled
        c.index_roots = list(config.roots)
        c.index_exclude_names = list(config.exclude_names)
        c.index_exclude_paths = list(config.exclude_paths)
        c.index_rescan_hours = float(config.rescan_hours)
        self._cfg.save()
        self._indexer.reconfigure()

    def rescan(self) -> None:
        self._indexer.request_rescan()

    def stop(self) -> None:
        self._indexer.stop()
        if self._reader is not None:
            self._reader.close()
            self._reader = None

    # ------------------------------------------------------------------ status

    def _relay(self, snap: dict) -> None:
        # called from the indexer thread – hop to the GUI thread through the signal
        self._status = snap
        self.status_changed.emit(snap)

    @property
    def status(self) -> dict:
        return dict(self._status)

    def status_text(self) -> str:
        s = self._status
        files = s.get("files") or 0
        parts = [f"{files:,} indexed".replace(",", " ")] if files else []
        if s.get("scanning"):
            parts.append(f"scanning {s['scanning']}…")
        elif s.get("message") and s.get("message") not in ("leader", "up to date"):
            parts.append(str(s["message"]))
        return "  ·  ".join(parts) or "index"

    # ------------------------------------------------------------------ search

    def search(self, query: str, limit: int = 200, kind: str = "all") -> list[tuple[str, str, bool]]:
        if not query.strip():
            return []
        try:
            if self._reader is None:
                if not os.path.exists(self.db_path):
                    return []
                self._reader = FileIndex(self.db_path)
            return self._reader.search(query, limit, kind=kind)
        except Exception as exc:
            logger.debug("index search failed: %s", exc)
            return []

    @staticmethod
    def display_dir(path: str) -> str:
        return str(Path(path).parent)
