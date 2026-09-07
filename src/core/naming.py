"""Names for copies pasted into their own folder (Ctrl+C, Ctrl+V in the same
directory): ``report.txt`` → ``report - Kopie.txt``, then ``report - Kopie (2).txt``…
The suffix is configurable (AppConfig.copy_suffix)."""

from __future__ import annotations

import os

DEFAULT_COPY_SUFFIX = " - Kopie"


def copy_target(src: str, dest_dir: str, suffix: str = DEFAULT_COPY_SUFFIX,
                exists=os.path.exists, is_dir: bool | None = None) -> str:
    """Full path for a copy of ``src`` inside ``dest_dir`` that does not exist yet.
    Files keep their extension after the suffix; folders get the suffix at the end."""
    name = os.path.basename(src.rstrip("\\/"))
    if is_dir is None:
        is_dir = os.path.isdir(src)
    if is_dir or name.startswith(".") and name.count(".") == 1:
        stem, ext = name, ""
    else:
        stem, ext = os.path.splitext(name)
    candidate = os.path.join(dest_dir, f"{stem}{suffix}{ext}")
    n = 2
    while exists(candidate):
        candidate = os.path.join(dest_dir, f"{stem}{suffix} ({n}){ext}")
        n += 1
    return candidate


def same_folder(src: str, dest_dir: str) -> bool:
    return os.path.normcase(os.path.dirname(src.rstrip("\\/"))) == os.path.normcase(dest_dir.rstrip("\\/"))
