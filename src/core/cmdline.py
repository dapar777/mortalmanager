"""Command-line placeholders (Total Commander style) for the embedded terminal.

    %N   name of the file under the cursor
    %P   directory of the active panel
    %T   directory of the other panel
    %S   all selected entries, names relative to the panel (space separated)
    %R   all selected entries, full paths
    %SI  like %S but the command runs once per entry
    %RI  like %R but the command runs once per entry
    %%   a literal percent sign

A placeholder must end at a word boundary, so cmd variables (``%PROJEKT%``,
``%PATH%``, ``%TEMP%``) pass through untouched.

Names with spaces or shell metacharacters are double-quoted. Expansion is
pure (no Qt); the terminal widget asks the main window for the context.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# a placeholder ends at a word boundary: %PROJEKT%, %PATH%, %TEMP%, %SystemRoot% are cmd variables, not %P / %T / %S
_TOKEN = re.compile(r"%(SI|RI|[NPTSR])(?![A-Za-z0-9_])|%(%)")
_REAL = re.compile(r"%(SI|RI|[NPTSR])(?![A-Za-z0-9_])")
_NEEDS_QUOTES = re.compile(r'[\s&|<>^()"]')


@dataclass
class CmdContext:
    cursor_name: str = ""             # "" when the cursor is on ".." or the panel is empty
    panel_path: str = ""
    other_path: str = ""
    selected_names: list[str] = field(default_factory=list)
    selected_paths: list[str] = field(default_factory=list)


def quote(value: str) -> str:
    if value and not _NEEDS_QUOTES.search(value):
        return value
    return '"' + value.replace('"', '""') + '"'


def has_placeholders(cmd: str, strict: bool = False) -> bool:
    """``strict`` ignores a lone ``%%`` – used for REPL lines, where "%%" is
    more likely a format string than a request for a percent sign."""
    if strict:
        return _REAL.search(cmd) is not None
    return _TOKEN.search(cmd) is not None


def expand(cmd: str, ctx: CmdContext, quoted: bool = True) -> list[str]:
    """Return the command(s) to run: one, or one per selected entry when
    %SI / %RI is present (both iterate together over the same entries).
    ``quoted=False`` substitutes raw values – for lines going into a REPL,
    where the user already wrote the quotes (``print('%SI')``)."""
    iterative = re.search(r"%(SI|RI)", cmd) is not None
    if not iterative:
        return [_expand_one(cmd, ctx, None, None, quoted)]
    pairs = list(zip(ctx.selected_names, ctx.selected_paths))
    if not pairs:
        return [_expand_one(cmd, ctx, "", "", quoted)]
    return [_expand_one(cmd, ctx, name, path, quoted) for name, path in pairs]


def _expand_one(cmd: str, ctx: CmdContext, it_name: str | None, it_path: str | None, quoted: bool) -> str:
    q = quote if quoted else (lambda v: v)

    def repl(m: re.Match) -> str:
        tok = m.group(1) or m.group(2)
        if tok == "%":
            return "%"
        if tok == "N":
            return q(ctx.cursor_name)
        if tok == "P":
            return q(ctx.panel_path)
        if tok == "T":
            return q(ctx.other_path)
        if tok == "S":
            return " ".join(q(n) for n in ctx.selected_names)
        if tok == "R":
            return " ".join(q(p) for p in ctx.selected_paths)
        if tok == "SI":
            return q(it_name or "")
        return q(it_path or "")     # RI
    return _TOKEN.sub(repl, cmd)
