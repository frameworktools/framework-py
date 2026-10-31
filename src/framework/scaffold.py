"""Starting work in a project: ``scaffold()``, ``status()``, and ``project_info()``."""

from __future__ import annotations

import json
import os
import random
import runpy
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from framework import db, dotenv
from framework._log import inform, show_messages
from framework._paths import in_root
from framework.errors import ProjectNotFoundError, SettingsError
from framework.project import find_settings_file
from framework.settings import settings_read

_DEFAULT_SEED = 123


def _section(config: dict[str, Any], name: str) -> dict[str, Any]:
    value = config.get(name)
    return value if isinstance(value, dict) else {}


def _first(config: dict[str, Any], key: str) -> Any:
    """A scaffold option from ``scaffold:``, then ``options:``, then the top level."""
    for section in (_section(config, "scaffold"), _section(config, "options"), config):
        if section.get(key) is not None:
            return section[key]
    return None


def _load_dotenv(config: dict[str, Any], root: Path) -> None:
    location = config.get("dotenv_location")
    if location is None:
        return
    path = in_root(str(location), root)
    if path.is_dir():
        path = path / ".env"
    if not path.is_file():
        raise SettingsError(f"Dotenv file not found at '{path}'")
    dotenv.load(path)


def _mark_scaffolded(root: Path) -> dict[str, str]:
    """Record the first and latest scaffold times in framework.db, as R does."""
    stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    history: dict[str, str] = {}
    raw = db.get_meta("scaffold_history", root)
    if raw:
        try:
            history = json.loads(raw)
        except json.JSONDecodeError:
            history = {}
    updated = {"first": history.get("first") or stamp, "last": stamp}
    db.set_meta("scaffold_history", json.dumps(updated, separators=(",", ":")), root)
    return updated


def _as_int(value: Any) -> int | None:
    try:
        return int(str(value).strip())
    except ValueError:
        return None


def _set_seed(config: dict[str, Any]) -> int | None:
    """Seed ``random`` and NumPy with R's rules.

    An explicit ``seed_on_scaffold: false`` skips seeding even when
    ``FW_SEED`` is set. Without a project setting, ``FW_SEED`` turns seeding
    on. The project's seed wins over ``FW_SEED``, and the fallback is 123.
    """
    scaffold, options = _section(config, "scaffold"), _section(config, "options")
    enabled = next(
        (
            s["seed_on_scaffold"]
            for s in (scaffold, options, config)
            if s.get("seed_on_scaffold") is not None
        ),
        None,
    )
    project_seed = next(
        (_as_int(s["seed"]) for s in (scaffold, config, options) if s.get("seed") is not None),
        None,
    )
    global_seed = _as_int(os.environ.get("FW_SEED", "x"))
    if enabled is False or (enabled is None and global_seed is None):
        return None
    value = project_seed if project_seed is not None else global_seed
    if value is None:
        value = _DEFAULT_SEED
    random.seed(value)
    try:
        import numpy as np

        np.random.seed(value)
    except ImportError:  # pragma: no cover (numpy comes with pandas)
        pass
    inform(f"Random seed set to {value}.")
    return value


def _add_function_folders(config: dict[str, Any], root: Path) -> list[Path]:
    """Put the project's function folders on the import path."""
    # New projects write source_all_functions under scaffold:. R reads only
    # options:, so it misses that setting; Python reads both, as for the seed.
    options = _section(config, "options")
    if _first(config, "source_all_functions") is False:
        return []
    folders = options.get("functions_dir") or _section(config, "directories").get("functions")
    if folders is None:
        folders = "functions"
    added = []
    for folder in folders if isinstance(folders, list) else [folders]:
        path = in_root(str(folder), root).resolve()
        if path.is_dir() and str(path) not in sys.path:
            sys.path.insert(0, str(path))
            added.append(path)
            inform(f"Added {folder} to the import path")
    return added


def scaffold(settings_file: str | Path | None = None) -> Path:
    """Prepare the current Python session for work in the project.

    Call it at the top of every notebook and script. It:

    - finds the project root and loads its ``.env`` and settings,
    - creates framework.db if needed and records the scaffold time,
    - sets the random seed when ``seed_on_scaffold`` is on (or ``FW_SEED`` is set),
    - puts the project's ``functions`` folder on the import path, and
    - runs ``scaffold.py`` from the project root, if there is one.

    The working directory is not changed: Framework resolves project paths
    from the root wherever the notebook runs.

    Args:
        settings_file: The project's settings file. Default: the settings.yml or config.yml
            found in the current directory or its parents.

    Returns:
        The project root.

    Raises:
        ProjectNotFoundError: When no settings file is found.
    """
    show_messages()
    path = Path(settings_file).expanduser() if settings_file is not None else find_settings_file()
    if path is None or not path.is_file():
        raise ProjectNotFoundError(
            "Could not locate a Framework project: no settings.yml or config.yml in "
            f"{Path.cwd()} or its parent folders. Create one with framework.new()."
        )
    root = path.resolve().parent
    config = settings_read(path)
    _load_dotenv(config, root)
    db.ensure_db(root)
    _mark_scaffolded(root)
    _set_seed(config)
    _add_function_folders(config, root)

    script = root / "scaffold.py"
    if script.is_file():
        runpy.run_path(str(script), run_name="__scaffold__")
    return root


def _git(*args: str) -> str | None:
    if shutil.which("git") is None:
        return None
    result = subprocess.run(["git", *args], capture_output=True, text=True, check=False)
    return result.stdout.strip() if result.returncode == 0 else None


def status() -> None:
    """Print an overview of the project: settings, git, AI files, and folders."""
    from framework import __version__

    settings_file = find_settings_file()
    if settings_file is None:
        raise ProjectNotFoundError(
            "Not in a Framework project directory (settings.yml or config.yml not found)"
        )
    root = settings_file.parent
    config = settings_read(settings_file)
    lines = ["Framework project status", "", "Framework:", f"  Version: {__version__}", ""]

    lines.append("Project:")
    if config.get("project_type"):
        lines.append(f"  Type: {config['project_type']}")
    author = _section(config, "author").get("name")
    if author:
        lines.append(f"  Author: {author}")
    lines.append("")

    lines.append("Git:")
    if shutil.which("git") is None:
        lines.append("  Git not installed")
    elif _git("-C", str(root), "rev-parse", "--git-dir") is None:
        lines.append("  Not a git repository")
    elif _git("-C", str(root), "rev-parse", "HEAD") is None:
        lines.append("  No commits yet")
    else:
        lines.append(f"  Branch: {_git('-C', str(root), 'branch', '--show-current')}")
        lines.append(f"  Commits: {_git('-C', str(root), 'rev-list', '--count', 'HEAD')}")
        changes = (_git("-C", str(root), "status", "--porcelain") or "").splitlines()
        lines.append(
            f"  Uncommitted changes: {len(changes)}" if changes else "  Working tree clean"
        )
    lines.append("")

    ai_files = [
        name
        for name in ("AGENTS.md", "CLAUDE.md", ".github/copilot-instructions.md")
        if (root / name).is_file()
    ]
    lines.append("AI Assistants:")
    lines.append(
        f"  Files: {', '.join(ai_files)}" if ai_files else "  No AI instruction files found"
    )
    lines.append("")

    lines.append("Package Management:")
    if (root / "uv.lock").is_file():
        lines.append("  uv: uv.lock present")
    elif (root / "pyproject.toml").is_file():
        lines.append("  pyproject.toml present (no lock file)")
    else:
        lines.append("  No pyproject.toml")
    lines.append("")

    directories = _section(config, "directories")
    if directories:
        lines.append("Directories:")
        for key, value in directories.items():
            if isinstance(value, str):
                mark = "ok" if in_root(value, root).is_dir() else "x "
                lines.append(f"  {mark} {key} -> {value}")
    print("\n".join(lines))


def project_info(verbose: bool = False) -> pd.DataFrame:
    """The project's configured folders and whether each exists yet.

    Folders that do not exist are created the first time something is saved
    there. With ``verbose``, the project name and type are printed too.

    Args:
        verbose: Print the project name and type.

    Returns:
        A DataFrame with columns key, path, and exists. Its ``attrs`` hold the
        project ``name`` and ``type``.

    Raises:
        ProjectNotFoundError: When no settings file is found.
    """
    settings_file = find_settings_file()
    if settings_file is None:
        raise ProjectNotFoundError(
            "No project configuration found. Are you in a Framework project?"
        )
    root = settings_file.parent
    config = settings_read(settings_file)
    rows = [
        {"key": key, "path": value, "exists": in_root(value, root).is_dir()}
        for key, value in _section(config, "directories").items()
        if isinstance(value, str)
    ]
    frame = pd.DataFrame(rows, columns=["key", "path", "exists"])
    frame.attrs["name"] = config.get("project_name") or root.name
    frame.attrs["type"] = config.get("project_type") or "project"
    if verbose:
        print(f"Project: {frame.attrs['name']}\nType: {frame.attrs['type']}")
    return frame
