"""Finding the project root and its settings file."""

from __future__ import annotations

from pathlib import Path

from framework.errors import ProjectNotFoundError

SETTINGS_FILES = ("settings.yml", "config.yml")

# R stops after ten parent folders, so Python does too.
_MAX_DEPTH = 10


def _settings_in(folder: Path) -> Path | None:
    for name in SETTINGS_FILES:
        candidate = folder / name
        if candidate.is_file():
            return candidate
    return None


def find_settings_file(start: str | Path | None = None) -> Path | None:
    """The project's settings file, searching ``start`` and up to ten parent folders.

    ``settings.yml`` is preferred over the older ``config.yml``.
    """
    current = Path(start or Path.cwd()).resolve()
    for _ in range(_MAX_DEPTH + 1):
        found = _settings_in(current)
        if found is not None:
            return found
        if current.parent == current:
            return None
        current = current.parent
    return None


def project_root(start: str | Path | None = None) -> Path:
    """The root folder of the Framework project containing ``start`` (default: the current folder).

    Args:
        start: The folder to search from. The search moves up through its parents. Default: the
            current directory.

    Returns:
        The folder that holds the project's settings.yml or config.yml.

    Raises:
        ProjectNotFoundError: No settings.yml or config.yml was found.
    """
    found = find_settings_file(start)
    if found is None:
        where = Path(start or Path.cwd()).resolve()
        raise ProjectNotFoundError(
            f"No Framework project found at or above {where}. "
            "A project has settings.yml (or config.yml) in its root folder."
        )
    return found.parent
