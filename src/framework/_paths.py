"""Resolving project folders from the settings."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from framework.project import project_root

# A folder named like private, confidential, or sensitive (outputs_private and
# private_raw count too), as the git security audit and R's audit match them.
PRIVATE_SEGMENT = re.compile(r"(^|/)[^/]*(private|confidential|sensitive)[^/]*(/|$)", re.IGNORECASE)


def has_private_segment(path: str | Path) -> bool:
    """Whether a folder path, with ``/`` separators, has a private segment."""
    return bool(PRIVATE_SEGMENT.search(Path(path).as_posix()))


def relative_to_root(path: Path, root: Path) -> str:
    """``path`` relative to the project root when it is inside it, else absolute."""
    resolved = path.resolve()
    try:
        return resolved.relative_to(root.resolve()).as_posix()
    except ValueError:
        return resolved.as_posix()


def in_root(path: str | Path, root: Path) -> Path:
    """A settings path, which is relative to the project root unless absolute."""
    candidate = Path(path).expanduser()
    return candidate if candidate.is_absolute() else root / candidate


def directory(config: dict[str, Any], key: str, default: str, root: Path | None = None) -> Path:
    """A folder from ``directories`` in the settings, or ``default``, under the root."""
    root = root or project_root()
    directories = config.get("directories")
    value = directories.get(key) if isinstance(directories, dict) else None
    return in_root(value if isinstance(value, str) and value else default, root)


def output_directory(config: dict[str, Any], kind: str, public: bool) -> Path:
    """Where outputs of one kind (tables, figures, ...) go.

    Sensitive projects split outputs into public and private folders; other
    projects use one folder per kind. This follows R's ``save_*()`` functions.
    """
    if config.get("project_type") == "project_sensitive":
        key = f"outputs_{'public' if public else 'private'}_{kind}"
    else:
        key = f"outputs_{kind}"
    default = f"outputs/public/{kind}" if public else f"outputs/{kind}"
    return directory(config, key, default)
