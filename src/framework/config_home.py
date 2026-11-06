"""The global config folder and syncing it with the R package's folder.

Python and the ``fw`` CLI share one folder. The R package keeps its own,
because CRAN does not allow a package to write outside
``tools::R_user_dir()``. ``config_sync()`` copies settings and saved logins
from R's folder when someone uses both languages.
"""

from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

from framework import _yaml
from framework.errors import SettingsError

# Files config_sync() copies. Token files hold a secret and are written 0600.
_SYNC_FILES = ("settings.yml", "cloud.yml")
_TOKEN_PREFIX = "cloud-token"


def config_dir() -> Path:
    """The global Framework config folder for Python and ``fw``.

    ``FW_CONFIG_HOME`` when set, otherwise ``$XDG_CONFIG_HOME/framework``,
    otherwise ``~/.config/framework`` (on Windows too). The folder is not
    created until Framework writes to it.
    """
    custom = os.environ.get("FW_CONFIG_HOME")
    if custom:
        return Path(custom).expanduser()
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg).expanduser() if xdg else Path.home() / ".config"
    return base / "framework"


def r_config_dir() -> Path:
    """The R package's config folder, ``tools::R_user_dir("framework", "config")``.

    This ignores ``FW_CONFIG_HOME``, which points both tools at the same
    folder when it is set.
    """
    custom = os.environ.get("R_USER_CONFIG_DIR") or os.environ.get("XDG_CONFIG_HOME")
    if custom:
        base = Path(custom).expanduser()
    elif sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", str(Path.home()))) / "R" / "config"
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Preferences" / "org.R-project.R"
    else:
        base = Path.home() / ".config"
    return base / "R" / "framework"


def ensure_config_dir() -> Path:
    """Create the config folder if needed and return it."""
    folder = config_dir()
    folder.mkdir(parents=True, exist_ok=True)
    return folder


@dataclass(frozen=True)
class SyncResult:
    """What ``config_sync()`` copied, or would copy with ``dry_run=True``."""

    source: Path
    target: Path
    copied: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    dry_run: bool = False

    def __str__(self) -> str:
        verb = "Would copy" if self.dry_run else "Copied"
        if not self.copied and not self.source.is_dir():
            return f"Nothing to copy: {self.source} does not exist."
        if not self.copied:
            return f"Nothing to copy from {self.source} (Python's folder is up to date)."
        return f"{verb} {', '.join(self.copied)} from {self.source} to {self.target}."


def _syncable(path: Path) -> bool:
    name = path.name
    if name.startswith(".") or not path.is_file():
        return False
    return name in _SYNC_FILES or name.startswith(_TOKEN_PREFIX)


def config_sync(from_dir: str | Path | None = None, *, dry_run: bool = False) -> SyncResult:
    """Copy settings and saved logins from the R package's config folder.

    A file is copied when Python's folder does not have it or R's copy is
    newer. The files are ``settings.yml``, ``cloud.yml``, and the
    ``cloud-token*`` files used when no system keyring is available.
    Modification times are kept, token files are made readable only by you,
    and nothing is written to R's folder.

    Args:
        from_dir: The folder to copy from. Default: ``r_config_dir()``.
        dry_run: Report what would be copied without copying.
    """
    source = Path(from_dir).expanduser() if from_dir is not None else r_config_dir()
    target = config_dir()
    copied: list[str] = []
    skipped: list[str] = []

    if source.resolve() == target.resolve() or not source.is_dir():
        return SyncResult(source, target, copied, skipped, dry_run)

    for path in sorted(source.iterdir()):
        if not _syncable(path):
            continue
        destination = target / path.name
        if destination.exists() and destination.stat().st_mtime >= path.stat().st_mtime:
            skipped.append(path.name)
            continue
        copied.append(path.name)
        if dry_run:
            continue
        target.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination)
        if path.name.startswith(_TOKEN_PREFIX):
            destination.chmod(0o600)

    return SyncResult(source, target, copied, skipped, dry_run)


# The project registry -----------------------------------------------------------


def _global_settings_path() -> Path:
    return config_dir() / "settings.yml"


def global_settings() -> dict[str, Any]:
    """The user's global settings (``settings.yml`` in the config folder), or ``{}``."""
    path = _global_settings_path()
    if not path.is_file():
        return {}
    try:
        data = _yaml.read(path)
    except Exception as error:
        raise SettingsError(f"Failed to parse global settings '{path}': {error}") from error
    return data if isinstance(data, dict) else {}


def _save_global_settings(settings: dict[str, Any]) -> None:
    ensure_config_dir()
    _yaml.write(_global_settings_path(), settings)


def project_register(path: str | Path) -> int:
    """Add a project to the registry in the global settings. Returns its id.

    Args:
        path: The project folder. It is expanded and resolved to an absolute path before it is
            stored.

    Returns:
        The project's id. A project already in the registry keeps its id and is not added
        again.
    """
    settings = global_settings()
    projects = settings.get("projects") or []
    resolved = str(Path(path).expanduser().resolve())
    for project in projects:
        if project.get("path") == resolved:
            return int(project.get("id", 0))
    next_id = max((int(p.get("id", 0)) for p in projects), default=0) + 1
    projects.append({"id": next_id, "path": resolved, "created": f"{date.today():%Y-%m-%d}"})
    settings["projects"] = projects
    _save_global_settings(settings)
    return next_id


def project_unregister(project: int | str | Path) -> bool:
    """Remove a project from the registry by id or path. Returns whether it was there.

    Args:
        project: The project's id as an integer, or its folder as a path or string. A string is
            always read as a path, so pass ids as integers.

    Returns:
        True when a project was removed, False when none matched.
    """
    settings = global_settings()
    projects = settings.get("projects") or []
    if isinstance(project, int):
        kept = [p for p in projects if int(p.get("id", 0)) != project]
    else:
        resolved = str(Path(project).expanduser().resolve())
        kept = [p for p in projects if p.get("path") != resolved]
    if len(kept) == len(projects):
        return False
    settings["projects"] = kept
    _save_global_settings(settings)
    return True


def project_list() -> pd.DataFrame:
    """Registered projects, with columns id, path, and created."""
    projects = global_settings().get("projects") or []
    rows = [
        {
            "id": int(p.get("id", 0)),
            "path": str(p.get("path", "")),
            "created": str(p.get("created", "")),
        }
        for p in projects
    ]
    return pd.DataFrame(rows, columns=["id", "path", "created"])
