"""File-name index in SQLite – storage, scanning and search (no Qt).

Schema (``%APPDATA%\\MortalManager\\index.db``, WAL so readers never block the
indexer):

    files(id, path UNIQUE NOCASE, name, is_dir, root, gen)
    files_fts  – FTS5 external-content table over files.name with the
                 *trigram* tokenizer: ``MATCH '"rep"'`` is a substring index
                 lookup, not a table scan (SQLite >= 3.34)
    meta(key, value)

``gen`` is the scan generation: a full scan upserts every entry with the new
generation and then deletes rows of that root with an older one, which is how
deleted files disappear without a diff.
"""

from __future__ import annotations

import os
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_EXCLUDE_NAMES = [
    "$Recycle.Bin", "System Volume Information", "node_modules", ".git", ".svn",
    "__pycache__", ".venv", "venv", ".mypy_cache", ".pytest_cache", ".ruff_cache",
]


def default_exclude_paths() -> list[str]:
    local = Path.home() / "AppData" / "Local"
    return [
        r"C:\Windows", r"C:\ProgramData", r"C:\Program Files\WindowsApps",
        str(local / "Temp"), str(local / "Packages"), str(local / "Microsoft"),
        str(local / "pip"), str(local / "NuGet"),
    ]


@dataclass
class IndexConfig:
    enabled: bool = True
    roots: list[str] = field(default_factory=lambda: ["C:\\"])
    exclude_names: list[str] = field(default_factory=lambda: list(DEFAULT_EXCLUDE_NAMES))
    exclude_paths: list[str] = field(default_factory=default_exclude_paths)
    rescan_hours: float = 24.0

    def normalized_roots(self) -> list[str]:
        out: list[str] = []
        for r in self.roots:
            r = r.strip()
            if not r:
                continue
            if len(r) == 2 and r[1] == ":":
                r += "\\"
            if os.path.isdir(r):
                out.append(os.path.normpath(r) + ("\\" if r.endswith(("\\", "/")) and not os.path.normpath(r).endswith("\\") else ""))
        return out


def default_config() -> IndexConfig:
    return IndexConfig()


class Excluder:
    """Fast exclusion test: directory names (anywhere) and path prefixes."""

    def __init__(self, names: list[str], paths: list[str]) -> None:
        self._names = {n.casefold() for n in names if n.strip()}
        self._prefixes = tuple(os.path.normcase(os.path.normpath(p)) for p in paths if p.strip())

    def dir_name_excluded(self, name: str) -> bool:
        return name.casefold() in self._names

    def path_excluded(self, path: str) -> bool:
        np = os.path.normcase(os.path.normpath(path))
        for pre in self._prefixes:
            if np == pre or np.startswith(pre + os.sep):
                return True
        for part in np.split(os.sep):
            if part.casefold() in self._names:
                return True
        return False


class FileIndex:
    """One SQLite connection (use one instance per thread)."""

    BATCH = 4000

    def __init__(self, db_path: str | os.PathLike) -> None:
        self.db_path = str(db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.db_path, timeout=30, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA temp_store=MEMORY")
        from .pattern import sqlite_regexp
        self._conn.create_function("REGEXP", 2, sqlite_regexp, deterministic=True)
        self._init_schema()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _init_schema(self) -> None:
        c = self._conn
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS files(
                id INTEGER PRIMARY KEY,
                path TEXT NOT NULL UNIQUE COLLATE NOCASE,
                name TEXT NOT NULL,
                is_dir INTEGER NOT NULL DEFAULT 0,
                root TEXT NOT NULL,
                gen INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS files_root_gen ON files(root, gen);
            CREATE VIRTUAL TABLE IF NOT EXISTS files_fts USING fts5(
                name, content='files', content_rowid='id', tokenize='trigram case_sensitive 0'
            );
            CREATE TRIGGER IF NOT EXISTS files_ai AFTER INSERT ON files BEGIN
                INSERT INTO files_fts(rowid, name) VALUES (new.id, new.name);
            END;
            CREATE TRIGGER IF NOT EXISTS files_ad AFTER DELETE ON files BEGIN
                INSERT INTO files_fts(files_fts, rowid, name) VALUES('delete', old.id, old.name);
            END;
            CREATE TRIGGER IF NOT EXISTS files_au AFTER UPDATE OF name ON files BEGIN
                INSERT INTO files_fts(files_fts, rowid, name) VALUES('delete', old.id, old.name);
                INSERT INTO files_fts(rowid, name) VALUES (new.id, new.name);
            END;
            CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
            """
        )
        c.commit()

    # ------------------------------------------------------------------ meta

    def get_meta(self, key: str, default: str | None = None) -> str | None:
        with self._lock:
            row = self._conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row[0] if row else default

    def set_meta(self, key: str, value: str) -> None:
        with self._lock:
            self._conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES(?, ?)", (key, str(value)))
            self._conn.commit()

    def last_scan(self, root: str) -> float:
        try:
            return float(self.get_meta(f"last_scan:{os.path.normcase(root)}", "0") or 0)
        except ValueError:
            return 0.0

    def set_last_scan(self, root: str, when: float | None = None) -> None:
        self.set_meta(f"last_scan:{os.path.normcase(root)}", str(when or time.time()))

    # ------------------------------------------------------------------ writes

    def upsert_many(self, rows: list[tuple[str, str, int, str, int]]) -> None:
        """rows: (path, name, is_dir, root, gen)"""
        if not rows:
            return
        with self._lock:
            self._conn.executemany(
                "INSERT INTO files(path, name, is_dir, root, gen) VALUES(?, ?, ?, ?, ?)"
                " ON CONFLICT(path) DO UPDATE SET name=excluded.name, is_dir=excluded.is_dir,"
                " root=excluded.root, gen=excluded.gen",
                rows,
            )
            self._conn.commit()

    def delete_prefix(self, path: str) -> int:
        """Remove a path and, if it was a directory, everything below it."""
        p = os.path.normpath(path)
        esc = p.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM files WHERE path = ? COLLATE NOCASE OR path LIKE ? ESCAPE '\\'",
                (p, esc + os.sep.replace("\\", "\\\\") + "%"),   # separator escaped too
            )
            self._conn.commit()
        return cur.rowcount

    def purge_stale(self, root: str, gen: int) -> int:
        with self._lock:
            cur = self._conn.execute("DELETE FROM files WHERE root = ? COLLATE NOCASE AND gen < ?", (root, gen))
            self._conn.commit()
        return cur.rowcount

    def purge_roots_not_in(self, roots: list[str]) -> int:
        keep = [os.path.normcase(r) for r in roots]
        with self._lock:
            rows = self._conn.execute("SELECT DISTINCT root FROM files").fetchall()
            gone = [r[0] for r in rows if os.path.normcase(r[0]) not in keep]
            n = 0
            for r in gone:
                n += self._conn.execute("DELETE FROM files WHERE root = ?", (r,)).rowcount
            self._conn.commit()
        return n

    def next_gen(self) -> int:
        gen = int(self.get_meta("gen", "0") or 0) + 1
        self.set_meta("gen", str(gen))
        return gen

    # ------------------------------------------------------------------ reads

    def count(self) -> int:
        with self._lock:
            return int(self._conn.execute("SELECT count(*) FROM files").fetchone()[0])

    def search(self, query: str, limit: int = 200, kind: str = "all",
               dirs_only: bool = False) -> list[tuple[str, str, bool]]:
        """Name search; ``kind`` is "all", "files" or "dirs". Returns
        (path, name, is_dir), shortest names first.

        The query is interpreted by index.pattern.parse(): words (every word a
        substring), a glob mask, or a regex. Words and the literal runs of a
        mask / regex of 3+ characters go through the trigram index as MATCH
        phrases; a mask / regex is then verified with the REGEXP function.
        Do NOT use LIKE … ESCAPE here: ESCAPE disables the trigram
        optimisation and turns a 5 ms lookup into a 500 ms table scan.
        Tokens shorter than 3 characters are plain LIKE filters; if nothing
        can use the index the scan is unordered and stops at ``limit``."""
        from .pattern import parse

        if dirs_only:
            kind = "dirs"
        sq = parse(query)
        if sq.kind == "words":
            long_toks = [t for t in sq.words if len(t) >= 3]
            short_toks = [t for t in sq.words if len(t) < 3]
            regex = None
        else:
            long_toks = list(sq.phrases)
            short_toks = []
            regex = sq.regex.pattern if sq.regex is not None else None
        if not long_toks and not short_toks and regex is None:
            return []
        params: list[object] = []
        where: list[str] = []
        if long_toks:
            where.append("files_fts MATCH ?")
            params.append(" AND ".join('"' + t.replace('"', '""') + '"' for t in long_toks))
        for t in short_toks:
            where.append("f.name LIKE ?")
            params.append(f"%{t}%")
        if regex is not None:
            where.append("f.name REGEXP ?")
            params.append(regex)
        if kind == "dirs":
            where.append("f.is_dir = 1")
        elif kind == "files":
            where.append("f.is_dir = 0")
        cond = " AND ".join(where)
        if long_toks:
            sql = (f"SELECT f.path, f.name, f.is_dir FROM files_fts t JOIN files f ON f.id = t.rowid"
                   f" WHERE {cond} ORDER BY length(f.name), f.name LIMIT ?")
        else:
            sql = f"SELECT f.path, f.name, f.is_dir FROM files f WHERE {cond} LIMIT ?"
        params.append(limit)
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [(r[0], r[1], bool(r[2])) for r in rows]

    # ------------------------------------------------------------------ scanning

    def scan_root(self, root: str, excluder: Excluder, gen: int,
                  stop=None, progress=None) -> int:
        """Walk ``root`` with os.scandir and upsert everything; returns the
        number of entries. ``stop`` is a threading.Event, ``progress(n)`` is
        called after every batch."""
        root_norm = os.path.normpath(root)
        if not root_norm.endswith("\\") and len(root_norm) == 2:
            root_norm += "\\"
        rows: list[tuple[str, str, int, str, int]] = []
        total = 0
        stack = [root_norm]
        while stack:
            if stop is not None and stop.is_set():
                break
            d = stack.pop()
            try:
                it = os.scandir(d)
            except OSError:
                continue
            with it:
                for de in it:
                    try:
                        is_dir = de.is_dir(follow_symlinks=False)
                    except OSError:
                        continue
                    if is_dir:
                        if excluder.dir_name_excluded(de.name) or excluder.path_excluded(de.path):
                            continue
                        stack.append(de.path)
                    rows.append((de.path, de.name, 1 if is_dir else 0, root_norm, gen))
                    if len(rows) >= self.BATCH:
                        self.upsert_many(rows)
                        total += len(rows)
                        rows = []
                        if progress:
                            progress(total)
        if rows:
            self.upsert_many(rows)
            total += len(rows)
        return total

    def add_path(self, path: str, root: str, excluder: Excluder, gen: int) -> None:
        """Index a newly appeared path (and its subtree if it is a directory)."""
        if excluder.path_excluded(path):
            return
        try:
            is_dir = os.path.isdir(path) and not os.path.islink(path)
        except OSError:
            return
        if not os.path.lexists(path):
            return
        self.upsert_many([(os.path.normpath(path), os.path.basename(path), 1 if is_dir else 0, root, gen)])
        if is_dir:
            self.scan_root_subtree(path, root, excluder, gen)

    def scan_root_subtree(self, start: str, root: str, excluder: Excluder, gen: int) -> None:
        rows: list[tuple[str, str, int, str, int]] = []
        stack = [start]
        while stack:
            d = stack.pop()
            try:
                it = os.scandir(d)
            except OSError:
                continue
            with it:
                for de in it:
                    try:
                        is_dir = de.is_dir(follow_symlinks=False)
                    except OSError:
                        continue
                    if is_dir:
                        if excluder.dir_name_excluded(de.name) or excluder.path_excluded(de.path):
                            continue
                        stack.append(de.path)
                    rows.append((de.path, de.name, 1 if is_dir else 0, root, gen))
                    if len(rows) >= self.BATCH:
                        self.upsert_many(rows)
                        rows = []
        if rows:
            self.upsert_many(rows)
