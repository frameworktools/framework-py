"""Backups of files Framework rewrites, for ``upgrade()`` and ``quarto_regenerate()``.

They go to ``.framework/backups/<timestamp>/<path from the project root>``.
``.framework/.gitignore`` ignores ``backups/``, so they never reach git whatever
the project's ``.gitignore`` says. The layout is R's (``R/backups.R``).
"""

from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path


def backup_dir(root: Path) -> str:
    """A new backup folder's path from the root: ``.framework/backups/<timestamp>``, with
    ``-2``, ``-3``, ... when that folder exists."""
    stamp = f"{datetime.now():%Y%m%d-%H%M%S}"
    relative = f".framework/backups/{stamp}"
    n = 2
    while (root / relative).exists():
        relative = f".framework/backups/{stamp}-{n}"
        n += 1
    return relative


def ignore_in_framework_dir(root: Path, entries: list[str]) -> None:
    """Add entries to ``.framework/.gitignore`` (created if missing), once each."""
    path = root / ".framework" / ".gitignore"
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    missing = [entry for entry in entries if entry not in {line.strip() for line in lines}]
    if missing:
        path.write_text("\n".join([*lines, *missing]) + "\n", encoding="utf-8")


def backup_files(root: Path, paths: list[str], relative: str) -> list[str]:
    """Copy the existing files among ``paths`` (from the root) into the backup folder
    ``relative`` (from ``backup_dir()``), keeping their paths. Returns the paths copied."""
    existing = [p for p in dict.fromkeys(paths) if (root / p).is_file()]
    if not existing:
        return []
    ignore_in_framework_dir(root, ["backups/"])
    for path in existing:
        target = root / relative / path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(root / path, target)
    return existing
