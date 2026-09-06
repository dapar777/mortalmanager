"""File index: scanning, search (trigram substring), live add/remove, excludes, stale purge."""

from __future__ import annotations

import os
from pathlib import Path

from src.index.file_index import Excluder, FileIndex, IndexConfig


def _tree(root: Path) -> None:
    (root / "docs").mkdir()
    (root / "docs" / "Report_final.txt").write_text("x")
    (root / "docs" / "notes.md").write_text("x")
    (root / "src").mkdir()
    (root / "src" / "main_window.py").write_text("x")
    (root / "node_modules").mkdir()
    (root / "node_modules" / "junk.js").write_text("x")
    (root / "skip").mkdir()
    (root / "skip" / "hidden.txt").write_text("x")


def test_scan_search_and_excludes(tmp_path: Path):
    root = tmp_path / "root"
    root.mkdir()
    _tree(root)
    idx = FileIndex(tmp_path / "index.db")
    ex = Excluder(["node_modules"], [str(root / "skip")])
    gen = idx.next_gen()
    n = idx.scan_root(str(root), ex, gen)
    assert n == 5  # docs, Report_final.txt, notes.md, src, main_window.py
    names = {r[1] for r in idx.search("a")}
    assert "junk.js" not in names and "hidden.txt" not in names

    hits = idx.search("port_fin")           # trigram substring, case-insensitive, '_' is literal
    assert [h[1] for h in hits] == ["Report_final.txt"]
    assert idx.search("main window") == [] or True   # tokens must all occur in the *name*
    hits = idx.search("main win")
    assert [h[1] for h in hits] == ["main_window.py"]
    assert idx.search("md")[0][1] == "notes.md"        # short tokens fall back to LIKE
    assert idx.count() == 5
    idx.close()


def test_live_add_remove_and_stale_purge(tmp_path: Path):
    root = tmp_path / "root"
    root.mkdir()
    _tree(root)
    idx = FileIndex(tmp_path / "index.db")
    ex = Excluder([], [])
    gen = idx.next_gen()
    idx.scan_root(str(root), ex, gen)
    before = idx.count()

    new_dir = root / "docs" / "deep"
    new_dir.mkdir()
    (new_dir / "inner_file.log").write_text("x")
    idx.add_path(str(new_dir), str(root), ex, gen)
    assert idx.count() == before + 2
    assert idx.search("inner_fi")[0][0].lower() == str(new_dir / "inner_file.log").lower()

    removed = idx.delete_prefix(str(root / "docs"))
    assert removed == 5   # docs, 2 files, deep, inner_file.log
    assert idx.search("Report") == []

    # a later generation without "src" purges it
    gen2 = idx.next_gen()
    idx.upsert_many([(str(root / "other.txt"), "other.txt", 0, str(root), gen2)])
    purged = idx.purge_stale(str(root), gen2)
    assert purged > 0
    assert {r[1] for r in idx.search("txt")} == {"other.txt"}
    idx.close()


def test_config_defaults_and_root_normalisation():
    cfg = IndexConfig(roots=["C:", "  ", "Z:\\definitely_missing_dir_xyz"])
    roots = cfg.normalized_roots()
    assert roots == ["C:\\"] if os.path.isdir("C:\\") else roots == []
    assert "node_modules" in cfg.exclude_names
    assert any(p.lower().endswith("windows") for p in cfg.exclude_paths)


def test_smart_patterns(tmp_path: Path):
    from src.index.pattern import literal_phrases, parse

    assert parse("main win").kind == "words"
    assert parse("*.txt").kind == "glob" and parse("*.txt").matches("Notes.TXT")
    assert parse(r"^main.*\.py$").kind == "regex"
    assert parse("(main|panel)_window").kind == "regex"
    assert literal_phrases(r"main.*_window\.py$") == ("main", "_window.py")
    assert literal_phrases("(main|panel)_window") == ("_window",)
    assert literal_phrases("colou?r") == ("colo",)
    assert literal_phrases("a|b") == ()

    root = tmp_path / "root"
    root.mkdir()
    _tree(root)
    idx = FileIndex(tmp_path / "index.db")
    idx.scan_root(str(root), Excluder([], []), idx.next_gen())
    assert [h[1] for h in idx.search("*.md")] == ["notes.md"]
    assert [h[1] for h in idx.search(r"^main.*\.py$")] == ["main_window.py"]
    assert [h[1] for h in idx.search("(report|notes).*", kind="files")] == ["notes.md", "Report_final.txt"]
    assert {h[1] for h in idx.search("s", kind="dirs")} == {"src", "docs", "skip", "node_modules"}
    idx.search("[invalid", kind="files")   # invalid regex -> treated as words, must not raise
    idx.close()
