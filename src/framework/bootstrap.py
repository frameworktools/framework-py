"""``bootstrap()``: prepare a project after ``git clone``.

Every step is safe to repeat and none contacts the cloud:

1. Make sure ``.gitignore`` ignores ``.env``.
2. Create ``.env`` from ``.env.example`` when it is missing, and list
   variables that are still empty.
3. Create the project folders, which git does not carry when they are empty
   or ignored.
4. Create ``framework.db``, or add tables a newer version needs.
5. Install the packages from ``uv.lock`` (``uv sync``).
6. Install the git hooks enabled in the settings.

R's ``bootstrap()`` follows the same contract, so ``fw bootstrap`` can call
either language.
"""

from __future__ import annotations

import getpass
import json
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from framework import db, dotenv
from framework._log import inform, show_messages
from framework._paths import in_root
from framework.create import ensure_secrets_ignored
from framework.errors import FrameworkError
from framework.hooks import git_hooks_install
from framework.project import _settings_in
from framework.settings import settings_read
from framework.spec.check import spec_check

_SECRET_NAME = re.compile(
    r"PASSWORD|SECRET|TOKEN|ACCESS_KEY|API_KEY|PRIVATE_KEY|CREDENTIAL", re.IGNORECASE
)


@dataclass
class BootstrapResult:
    """What ``bootstrap()`` did, and what is left for you.

    Attributes:
        root: The project folder.
        done: The steps that changed something.
        todo: Things to do by hand, such as filling in empty secrets.
    """

    root: Path
    done: list[str] = field(default_factory=list)
    todo: list[str] = field(default_factory=list)


def _check_spec(root: Path) -> None:
    path = root / ".framework" / "spec.json"
    if not path.is_file():
        return
    try:
        spec = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise FrameworkError(f".framework/spec.json is not valid JSON: {error}") from error
    problems = spec_check(spec) if isinstance(spec, dict) else ["it is not a JSON object"]
    if problems:
        raise FrameworkError(
            ".framework/spec.json is invalid: " + "; ".join(str(p) for p in problems)
        )


def _prepare_env(root: Path, interactive: bool, result: BootstrapResult) -> None:
    env = root / ".env"
    example = root / ".env.example"
    if not env.exists() and example.is_file():
        shutil.copyfile(example, env)
        result.done.append("Created .env from .env.example")
    if not env.is_file():
        return
    empty = [key for key, value in dotenv.read(env).items() if value == ""]
    if interactive and empty:
        values: dict[str, str] = {}
        for key in empty:
            ask = getpass.getpass if _SECRET_NAME.search(key) else input
            answer = ask(f"{key} (leave empty to skip): ").strip()
            if answer:
                values[key] = answer
        if values:
            _fill_env(env, values)
            result.done.append(f"Filled in {', '.join(values)} in .env")
        empty = [key for key in empty if key not in values]
    if empty:
        result.todo.append(f"Fill in .env: {', '.join(empty)}")


def _fill_env(path: Path, values: dict[str, str]) -> None:
    """Set empty ``KEY=`` lines in a .env file, keeping the other lines as they are."""
    lines = path.read_text(encoding="utf-8").splitlines()
    for number, line in enumerate(lines):
        match = re.match(r"^(\s*(?:export\s+)?)([A-Za-z_][A-Za-z0-9_]*)\s*=\s*$", line)
        if match and match.group(2) in values:
            value = values[match.group(2)]
            quoted = json.dumps(value) if re.search(r"\s|#|\"|'", value) else value
            lines[number] = f"{match.group(1)}{match.group(2)}={quoted}"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _folders(config: dict[str, Any]) -> list[str]:
    folders: list[str] = []
    directories = config.get("directories")
    if isinstance(directories, dict):
        folders += [value for value in directories.values() if isinstance(value, str) and value]
    extra = config.get("extra_directories")
    if isinstance(extra, list):
        folders += [
            entry["path"] for entry in extra if isinstance(entry, dict) and entry.get("path")
        ]
    return folders


def _create_folders(root: Path, config: dict[str, Any], result: BootstrapResult) -> None:
    created = []
    for folder in _folders(config):
        if Path(folder).expanduser().is_absolute():
            continue  # Shared drives and other places outside the project.
        path = in_root(folder, root)
        if not path.exists():
            path.mkdir(parents=True)
            created.append(folder)
    if created:
        result.done.append(f"Created folders: {', '.join(created)}")


def _restore_packages(root: Path, result: BootstrapResult) -> None:
    if not (root / "pyproject.toml").is_file():
        return
    uv = shutil.which("uv")
    if uv is None:
        raise FrameworkError(
            "Package restore failed: uv is not installed. "
            "Install it from https://docs.astral.sh/uv/ and run bootstrap again."
        )
    args = [uv, "sync", "--frozen"] if (root / "uv.lock").is_file() else [uv, "sync"]
    completed = subprocess.run(args, cwd=root, check=False)
    if completed.returncode != 0:
        raise FrameworkError(
            f"Package restore failed: {' '.join(args[1:])} exited with status "
            f"{completed.returncode}"
        )
    result.done.append("Installed packages (uv sync)")


def bootstrap(
    path: str | Path = ".", *, interactive: bool = False, packages: bool = True
) -> BootstrapResult:
    """Prepare a cloned project so it is ready to work in.

    Args:
        path: The project folder.
        interactive: Ask for the values of empty ``.env`` variables. Without
            it, bootstrap never prompts and lists them as to-dos instead.
        packages: Install the packages with uv.

    Returns:
        A ``BootstrapResult`` with the steps taken and the to-dos left.

    Raises:
        FrameworkError: The project cannot be prepared: there is no settings
            file, the project is not a Python project, its spec is invalid,
            or the packages could not be installed.
    """
    show_messages()
    root = Path(path).expanduser().resolve()
    settings_file = _settings_in(root) if root.is_dir() else None
    if settings_file is None:
        raise FrameworkError(f"No settings.yml in {root}. Is this a Framework project?")
    config = settings_read(settings_file)
    language = config.get("language") or "r"
    if language != "python":
        raise FrameworkError(
            f"This is a {language} project. Prepare it with that language's Framework "
            "(in R: framework::bootstrap())."
        )
    _check_spec(root)
    result = BootstrapResult(root=root)

    if ensure_secrets_ignored(root):
        result.done.append("Added .env to .gitignore")
    _prepare_env(root, interactive, result)
    _create_folders(root, config, result)
    existed = db.db_path(root).exists()
    db.ensure_db(root)
    result.done.append("Updated framework.db" if existed else "Created framework.db")
    if packages:
        _restore_packages(root, result)
    if (root / ".git").exists() and git_hooks_install(settings_file, verbose=False):
        result.done.append("Installed git hooks")

    for step in result.done:
        inform(f"✓ {step}")
    for item in result.todo:
        inform(f"• To do: {item}")
    inform(f"Project ready: {root}")
    return result
