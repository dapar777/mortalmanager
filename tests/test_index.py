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
    assert [h[1] for h in idx.search("main window")] == ["main_window.py"]
    hits = idx.search("main win")
    assert [h[1] for h in hits] == ["main_window.py"]
    assert idx.search("md")[0][1] == "notes.md"        # short tokens fall back to LIKE
    assert idx.count() == 5
    idx.close()


def test_words_match_path_one_in_name(tmp_path: Path):
    """"CAR 3x" finds svn/CAR/db/2024_3x: every word somewhere in the path, at
    least one in the name (long words via the trigram index, short via LIKE)."""
    root = tmp_path / "root"
    (root / "svn" / "CAR" / "db" / "2024_3x").mkdir(parents=True)
    (root / "svn" / "CAR" / "db" / "2024_3x" / "data.bin").write_text("x")
    (root / "svn" / "OTHER" / "2024_3x").mkdir(parents=True)
    (root / "svn" / "OTHER" / "car_list.txt").write_text("x")
    idx = FileIndex(tmp_path / "index.db")
    idx.scan_root(str(root), Excluder([], []), idx.next_gen())

    hits = idx.search("CAR 3x", kind="dirs")
    assert [h[1] for h in hits] == ["2024_3x"] and "CAR" in hits[0][0]      # not the OTHER one
    assert len(idx.search("3x", kind="dirs")) == 2                          # single word: name only
    assert [h[1] for h in idx.search("car db", kind="dirs")] == ["db"]      # short word in name, long in path
    # "db" / "2024_3x" have no word in the name; car_list.txt has "car" in the name and "svn" in the path
    assert [h[1] for h in idx.search("svn car")] == ["CAR", "car_list.txt"]
    names = [h[1] for h in idx.search("other car")]
    assert names == ["car_list.txt"]                                        # name matches "car", path "other"
    assert [h[1] for h in idx.search("car bin")] == ["data.bin"]            # long word only in the path
    assert idx.search("car zzz") == []
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


def test_network_roots_are_skipped(tmp_path: Path, monkeypatch):
    from src.index import file_index

    assert file_index.is_network_path("\\\\server\\share\\docs")
    assert not file_index.is_network_path(str(tmp_path))
    local = tmp_path / "local"
    local.mkdir()
    net = tmp_path / "net"
    net.mkdir()
    monkeypatch.setattr(file_index, "is_network_path", lambda p: p.startswith(str(net)))
    cfg = IndexConfig(roots=[str(local), str(net)])
    assert cfg.normalized_roots() == [str(local)]
