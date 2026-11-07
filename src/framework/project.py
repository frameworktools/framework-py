"""Finding the project root and its settings file."""

from __future__ import annotations

from os import PathLike
from pathlib import Path, PureWindowsPath

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


def project_path(*parts: str | PathLike[str]) -> Path:
    """An absolute path inside the project, from parts relative to its root.

    Like R's ``project_path()`` and ``here::here()``: the same call gives the same file from a
    notebook in ``notebooks/``, a script in the root, or a subfolder. Python never changes the
    working directory, so ``Path("outputs/tables/x.csv").exists()`` in a notebook looks under
    ``notebooks/``; ``fw.project_path("outputs/tables/x.csv").exists()`` looks under the root::

        if not fw.project_path("outputs", "tables", "incidence.csv").exists():
            ...

    Args:
        *parts: Path parts relative to the project root, joined in order. A part may hold
            slashes (``"outputs/tables"``). No parts gives the root itself.

    Returns:
        The absolute path. The file need not exist.

    Raises:
        ProjectNotFoundError: Not inside a Framework project.
        ValueError: A part is absolute or rooted (``"/etc"``, ``"C:\\data"``, ``"~/x"``).
    """
    for part in parts:
        text = str(part)
        # Rooted on either system: "/etc", "C:\\data", "C:data", "\\data", or "~"
        if Path(part).is_absolute() or PureWindowsPath(part).anchor or text.startswith("~"):
            raise ValueError(
                f"project_path() parts are relative to the project root, but '{text}' is absolute"
            )
    return project_root().joinpath(*parts)


def path(*parts: str | PathLike[str]) -> Path:
    """An alias of ``project_path()``, the name R uses.

    ``fw.path(...)`` and ``fw.project_path(...)`` are the same call, and both stay supported.
    New code can use ``project_path()`` so it reads the same in R and Python.

    Args:
        *parts: Path parts relative to the project root, as for ``project_path()``.

    Returns:
        The absolute path. The file need not exist.
    """
    return project_path(*parts)
