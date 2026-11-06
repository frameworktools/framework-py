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


def output_directory(
    config: dict[str, Any], kind: str, public: bool, root: Path | None = None
) -> Path:
    """Where outputs of one kind (tables, figures, ...) go.

    A project with private and public output folders (sensitive data on, or
    ``outputs_private_*`` or ``outputs_public_*`` directories) splits by
    ``public``. Any other project has one folder per kind. This follows R's
    ``save_*()`` functions.
    """
    directories = config.get("directories")
    directories = directories if isinstance(directories, dict) else {}
    security = config.get("security")
    split = (
        (isinstance(security, dict) and security.get("sensitive") is True)
        or f"outputs_private_{kind}" in directories
        or f"outputs_public_{kind}" in directories
    )
    if not split:
        return directory(config, f"outputs_{kind}", f"outputs/{kind}", root)
    side = "public" if public else "private"
    return directory(config, f"outputs_{side}_{kind}", f"outputs/{side}/{kind}", root)


def rendered_page(out: Path, stem: str, extension: str = ".html") -> Path | None:
    """The page ``quarto render --output-dir out`` wrote for a source named ``stem``.

    Inside a Quarto project, such as any project ``new()`` makes, the output keeps the
    source's folder under the output folder: ``notebooks/x.qmd`` becomes
    ``out/notebooks/x.html``. The shallowest page named for the source wins. Otherwise
    the one page outside ``_files`` folders is used. None when there is no single page.
    """
    named = sorted(out.rglob(f"{stem}{extension}"), key=lambda p: (len(p.parts), p))
    if named:
        return named[0]
    pages = [
        p
        for p in sorted(out.rglob(f"*{extension}"))
        if not any(part.endswith("_files") for part in p.relative_to(out).parts[:-1])
    ]
    return pages[0] if len(pages) == 1 else None
