"""Safe extraction target: an archive must never write outside its destination.

A crafted archive can carry members like ``../../evil.exe``, an absolute path
(``/etc/passwd``, ``C:\\Windows\\x.dll``) or, on Windows, a drive-relative path
(``C:x``). ``safe_target`` maps a member name onto a path inside *destination*
and returns None when that is impossible, so the caller skips the member.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path, PurePosixPath, PureWindowsPath

logger = logging.getLogger(__name__)


def safe_target(destination: Path, member: str) -> Path | None:
    """Path *member* extracts to inside *destination*, or None if it escapes."""
    name = member.replace("\\", "/").strip("/")
    if not name or name in (".", ".."):
        return None
    # a drive letter or UNC prefix makes the name absolute on Windows; the leading
    # "/" was stripped above, so compare against the original member instead
    if PureWindowsPath(name).drive or member.replace("\\", "/").startswith("/"):
        logger.warning("Skipping absolute path in archive: %s", member)
        return None
    parts = [p for p in name.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        logger.warning("Skipping path traversal in archive: %s", member)
        return None
    if not parts:
        return None
    target = destination.joinpath(*parts)
    # symlinked or short (8.3) components could still point elsewhere: compare the real paths
    try:
        base = os.path.realpath(str(destination))
        resolved = os.path.realpath(str(target))
    except OSError:
        return None
    if not (resolved == base or resolved.startswith(base + os.sep)):
        logger.warning("Skipping member escaping the destination: %s", member)
        return None
    return target
