"""SQLite database manager for persistent application state."""

from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Generator

logger = logging.getLogger(__name__)

_SCHEMA_VERSION = 1

_SCHEMA_SQL = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY NOT NULL,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS bookmarks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    path        TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    sort_order  INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS ftp_sessions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE,
    protocol    TEXT NOT NULL DEFAULT 'FTP',
    host        TEXT NOT NULL,
    port        INTEGER NOT NULL DEFAULT 21,
    username    TEXT NOT NULL DEFAULT '',
    password    TEXT NOT NULL DEFAULT '',
    remote_path TEXT NOT NULL DEFAULT '/',
    passive     INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS operation_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    op_type     TEXT NOT NULL,
    source      TEXT NOT NULL,
    destination TEXT NOT NULL DEFAULT '',
    timestamp   TEXT NOT NULL DEFAULT (datetime('now')),
    status      TEXT NOT NULL DEFAULT 'done',
    error_msg   TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS session_tabs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    panel       INTEGER NOT NULL,
    tab_index   INTEGER NOT NULL,
    path        TEXT NOT NULL,
    locked      INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS path_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    path        TEXT NOT NULL,
    visited_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS favorites (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    path       TEXT NOT NULL,
    alias      TEXT NOT NULL DEFAULT '',
    sort_order INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS command_history (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    command  TEXT NOT NULL,
    cwd      TEXT NOT NULL DEFAULT '',
    ran_at   TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


@dataclass
class BookmarkRow:
    id: int
    name: str
    path: str
    created_at: str
    sort_order: int


@dataclass
class FavoriteRow:
    id: int
    path: str
    alias: str
    sort_order: int


@dataclass
class CommandHistoryRow:
    id: int
    command: str
    cwd: str
    ran_at: str


@dataclass
class FtpSessionRow:
    id: int
    name: str
    protocol: str
    host: str
    port: int
    username: str
    password: str
    remote_path: str
    passive: bool


@dataclass
class OperationHistoryRow:
    id: int
    op_type: str
    source: str
    destination: str
    timestamp: str
    status: str
    error_msg: str


class DatabaseManager:
    """Thread-safe SQLite database manager.

    All write operations use parameterised queries to prevent SQL injection.
    """

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @contextmanager
    def _connect(self) -> Generator[sqlite3.Connection, None, None]:
        conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA secure_delete=ON")   # deleted rows are zeroed, not just unlinked
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(_SCHEMA_SQL)
            # Migrate: add shell column to command_history if not present yet
            try:
                conn.execute(
                    "ALTER TABLE command_history ADD COLUMN shell TEXT NOT NULL DEFAULT ''"
                )
            except Exception:
                pass  # column already exists
            row = conn.execute("SELECT version FROM schema_version").fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO schema_version (version) VALUES (?)",
                    (_SCHEMA_VERSION,),
                )
            logger.debug("Database initialised at %s", self._db_path)

    # ------------------------------------------------------------------ settings

    def get_setting(self, key: str, default: Any = None) -> Any:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT value FROM settings WHERE key = ?", (key,)
            ).fetchone()
        if row is None:
            return default
        try:
            return json.loads(row["value"])
        except (json.JSONDecodeError, TypeError):
            return row["value"]

    def set_setting(self, key: str, value: Any) -> None:
        serialised = json.dumps(value)
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?)"
                " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, serialised),
            )

    def get_all_settings(self) -> dict[str, Any]:
        with self._connect() as conn:
            rows = conn.execute("SELECT key, value FROM settings").fetchall()
        result: dict[str, Any] = {}
        for row in rows:
            try:
                result[row["key"]] = json.loads(row["value"])
            except Exception:
                result[row["key"]] = row["value"]
        return result

    # ------------------------------------------------------------------ bookmarks

    def add_bookmark(self, name: str, path: str) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO bookmarks (name, path) VALUES (?, ?)", (name, path)
            )
            return cur.lastrowid or 0

    def remove_bookmark(self, bookmark_id: int) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM bookmarks WHERE id = ?", (bookmark_id,))

    def get_bookmarks(self) -> list[BookmarkRow]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, name, path, created_at, sort_order"
                " FROM bookmarks ORDER BY sort_order, id"
            ).fetchall()
        return [BookmarkRow(**dict(r)) for r in rows]

    def update_bookmark(self, bookmark_id: int, name: str, path: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE bookmarks SET name = ?, path = ? WHERE id = ?",
                (name, path, bookmark_id),
            )

    # ------------------------------------------------------------------ FTP sessions

    def save_ftp_session(self, session: FtpSessionRow) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                """INSERT INTO ftp_sessions
                   (name, protocol, host, port, username, password, remote_path, passive)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(name) DO UPDATE SET
                     protocol=excluded.protocol, host=excluded.host,
                     port=excluded.port, username=excluded.username,
                     password=excluded.password, remote_path=excluded.remote_path,
                     passive=excluded.passive""",
                (
                    session.name,
                    session.protocol,
                    session.host,
                    session.port,
                    session.username,
                    session.password,
                    session.remote_path,
                    int(session.passive),
                ),
            )
            return cur.lastrowid or 0

    def get_ftp_sessions(self) -> list[FtpSessionRow]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, name, protocol, host, port, username, password,"
                " remote_path, passive FROM ftp_sessions ORDER BY name"
            ).fetchall()
        result: list[FtpSessionRow] = []
        for r in rows:
            result.append(
                FtpSessionRow(
                    id=r["id"],
                    name=r["name"],
                    protocol=r["protocol"],
                    host=r["host"],
                    port=r["port"],
                    username=r["username"],
                    password=r["password"],
                    remote_path=r["remote_path"],
                    passive=bool(r["passive"]),
                )
            )
        return result

    def delete_ftp_session(self, session_id: int) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM ftp_sessions WHERE id = ?", (session_id,))

    # ------------------------------------------------------------------ operation history

    def log_operation(
        self,
        op_type: str,
        source: str,
        destination: str = "",
        status: str = "done",
        error_msg: str = "",
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO operation_history (op_type, source, destination, status, error_msg)"
                " VALUES (?, ?, ?, ?, ?)",
                (op_type, source, destination, status, error_msg),
            )

    def get_operation_history(self, limit: int = 200) -> list[OperationHistoryRow]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, op_type, source, destination, timestamp, status, error_msg"
                " FROM operation_history ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [OperationHistoryRow(**dict(r)) for r in rows]

    def clear_operation_history(self) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM operation_history")

    # ------------------------------------------------------------------ session tabs

    def save_session_tabs(self, tabs: list[dict[str, Any]]) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM session_tabs")
            conn.executemany(
                "INSERT INTO session_tabs (panel, tab_index, path, locked)"
                " VALUES (:panel, :tab_index, :path, :locked)",
                tabs,
            )

    def load_session_tabs(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT panel, tab_index, path, locked FROM session_tabs"
                " ORDER BY panel, tab_index"
            ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------ path history

    def add_path_history(self, path: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO path_history (path) VALUES (?)", (path,)
            )
            # Keep last 500 entries
            conn.execute(
                "DELETE FROM path_history WHERE id NOT IN"
                " (SELECT id FROM path_history ORDER BY id DESC LIMIT 500)"
            )

    def get_path_history(self, limit: int = 100) -> list[str]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT DISTINCT path FROM path_history ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [r["path"] for r in rows]

    # ------------------------------------------------------------------ favorites

    def get_favorites(self) -> list[FavoriteRow]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, path, alias, sort_order FROM favorites ORDER BY sort_order, id"
            ).fetchall()
        return [FavoriteRow(**dict(r)) for r in rows]

    def add_favorite(self, path: str, alias: str = "") -> int:
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO favorites (path, alias) VALUES (?, ?)", (path, alias)
            )
            return cur.lastrowid or 0

    def remove_favorite(self, fav_id: int) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM favorites WHERE id = ?", (fav_id,))

    def save_favorites(self, favorites: list[FavoriteRow]) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM favorites")
            for i, fav in enumerate(favorites):
                conn.execute(
                    "INSERT INTO favorites (path, alias, sort_order) VALUES (?, ?, ?)",
                    (fav.path, fav.alias, i),
                )

    # ------------------------------------------------------------------ command history

    def add_command_history(self, command: str, cwd: str = "", shell: str = "") -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO command_history (command, cwd, shell) VALUES (?, ?, ?)",
                (command, cwd, shell),
            )

    def delete_command_history(self, command: str) -> int:
        """Remove every occurrence of ``command`` (any cwd / shell) and scrub it
        from the file: secure_delete zeroes the row, the WAL is checkpointed and
        truncated, and VACUUM rebuilds the database without the freed pages, so
        the text is gone from config.db and config.db-wal. Returns the row count."""
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM command_history WHERE command = ?", (command,))
            n = cur.rowcount
        self.scrub()
        return n

    def scrub(self) -> None:
        """Physically drop deleted content: fold the WAL into the main file and
        truncate it, then VACUUM (must run outside a transaction)."""
        conn = sqlite3.connect(str(self._db_path), check_same_thread=False, isolation_level=None)
        try:
            conn.execute("PRAGMA secure_delete=ON")
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            conn.execute("VACUUM")
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except sqlite3.Error:
            pass                      # another instance holds a lock: the row is already zeroed anyway
        finally:
            conn.close()

    def get_command_history(
        self, cwd: str = "", shell: str = "", limit: int = 200
    ) -> list[str]:
        """Return recent commands, prioritising those run in *cwd*."""
        with self._connect() as conn:
            if shell:
                same = conn.execute(
                    "SELECT DISTINCT command FROM command_history"
                    " WHERE cwd = ? AND shell = ? ORDER BY id DESC LIMIT ?",
                    (cwd, shell, limit),
                ).fetchall()
                other = conn.execute(
                    "SELECT DISTINCT command FROM command_history"
                    " WHERE (cwd != ? OR cwd IS NULL) AND shell = ?"
                    " ORDER BY id DESC LIMIT ?",
                    (cwd, shell, limit),
                ).fetchall()
            else:
                same = conn.execute(
                    "SELECT DISTINCT command FROM command_history WHERE cwd = ? "
                    "ORDER BY id DESC LIMIT ?",
                    (cwd, limit),
                ).fetchall()
                other = conn.execute(
                    "SELECT DISTINCT command FROM command_history WHERE cwd != ? "
                    "ORDER BY id DESC LIMIT ?",
                    (cwd, limit),
                ).fetchall()
        seen: set[str] = set()
        result: list[str] = []
        for row in same + other:
            cmd = row["command"]
            if cmd not in seen:
                seen.add(cmd)
                result.append(cmd)
        return result[:limit]
