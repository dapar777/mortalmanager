"""Smart query interpretation shared by the file index and the palette.

    plain words      → every word must occur as a substring (case-insensitive)
    glob (* or ?)    → fnmatch-style mask on the whole name ("*.txt", "rep?rt*")
    regex            → anything with regex-only metacharacters (^ $ [ ] ( ) | + { } \\
                       or ".*" / ".+"), used as a case-insensitive re.search on the name

For the index the regex cannot use the trigram index directly, so
``literal_phrases()`` extracts top-level literal runs of 3+ characters (e.g.
"main" and "_window" from ``main.*_window\\.py$``) to narrow the candidates
with MATCH before the REGEXP filter runs.
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass
from functools import lru_cache

_REGEX_ONLY = set("^$[](){}|+\\")
_QUANTIFIERS = set("?*+{")


@dataclass(frozen=True)
class SmartQuery:
    kind: str                 # "words" | "glob" | "regex"
    text: str                 # original text
    regex: re.Pattern | None  # compiled for glob / regex
    words: tuple[str, ...]    # for kind == "words"
    phrases: tuple[str, ...]  # literal runs usable for trigram narrowing (glob / regex)

    @property
    def label(self) -> str:
        return {"words": "", "glob": "mask", "regex": "regex"}[self.kind]

    def matches(self, name: str) -> bool:
        if self.regex is not None:
            return self.regex.search(name) is not None
        low = name.casefold()
        return all(w in low for w in self.words)


def looks_like_regex(text: str) -> bool:
    if any(ch in _REGEX_ONLY for ch in text):
        return True
    return ".*" in text or ".+" in text


def literal_phrases(pattern: str, min_len: int = 3) -> tuple[str, ...]:
    """Top-level literal runs of a regex (conservative: none if the pattern has
    a top-level alternation; a run followed by a quantifier loses its last char)."""
    runs: list[str] = []
    cur: list[str] = []
    depth = 0
    i = 0
    n = len(pattern)
    top_level_alt = False

    def flush() -> None:
        if cur:
            runs.append("".join(cur))
            cur.clear()

    while i < n:
        ch = pattern[i]
        if ch == "\\" and i + 1 < n:
            nxt = pattern[i + 1]
            if depth == 0 and not nxt.isalnum():
                cur.append(nxt)            # escaped literal (\. \_ \-)
            else:
                flush()                    # class escape (\d \w …)
            i += 2
            continue
        if ch in "([":
            flush()
            depth += 1
        elif ch in ")]":
            flush()
            depth = max(0, depth - 1)
        elif depth == 0:
            if ch == "|":
                top_level_alt = True
                flush()
            elif ch in _QUANTIFIERS:
                if cur:
                    cur.pop()              # previous char is optional / repeated
                flush()
                if ch == "{":
                    j = pattern.find("}", i)
                    i = j if j > 0 else i
            elif ch in "^$.":
                flush()
            else:
                cur.append(ch)
        i += 1
    flush()
    if top_level_alt:
        return ()
    return tuple(r.casefold() for r in runs if len(r) >= min_len)


@lru_cache(maxsize=256)
def parse(text: str) -> SmartQuery:
    raw = text.strip()
    if not raw:
        return SmartQuery("words", raw, None, (), ())
    if looks_like_regex(raw):
        try:
            rx = re.compile(raw, re.IGNORECASE)
            return SmartQuery("regex", raw, rx, (), literal_phrases(raw))
        except re.error:
            pass                            # not a valid regex → treat as words
    elif "*" in raw or "?" in raw:
        rx = re.compile(fnmatch.translate(raw), re.IGNORECASE)
        pieces = tuple(p.casefold() for p in re.split(r"[*?]+", raw) if len(p) >= 3)
        return SmartQuery("glob", raw, rx, (), pieces)
    words = tuple(w.casefold() for w in raw.split())
    return SmartQuery("words", raw, None, words, ())


@lru_cache(maxsize=256)
def _compiled(pattern: str) -> re.Pattern | None:
    try:
        return re.compile(pattern, re.IGNORECASE)
    except re.error:
        return None


def sqlite_regexp(pattern: str, value: str | None) -> int:
    """REGEXP function for sqlite3.Connection.create_function."""
    if value is None:
        return 0
    rx = _compiled(pattern)
    return 1 if rx is not None and rx.search(value) else 0
