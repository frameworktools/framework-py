"""Project packages, kept in ``pyproject.toml`` and locked with uv.

R's ``packages_*()`` functions manage renv. In Python the dependencies live in
``pyproject.toml``, ``uv.lock`` pins them, and these functions call uv. When
uv is not installed, ``package_add()`` and ``packages_install()`` fall back to
pip in the current Python environment. Locking needs uv.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tomllib
from dataclasses import dataclass, field
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

import pandas as pd

from framework._log import inform, show_messages
from framework.errors import FrameworkError
from framework.project import project_root

_NAME = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")


def _run(args: list[str], root: Path) -> int:
    """Run a command in the project folder, showing its output. Returns its exit status."""
    return subprocess.run(args, cwd=root, check=False).returncode


def _uv() -> str | None:
    return shutil.which("uv")


def _require_uv(task: str) -> str:
    uv = _uv()
    if uv is None:
        raise FrameworkError(
            f"{task} needs uv, which is not installed. "
            "Install it from https://docs.astral.sh/uv/ and try again."
        )
    return uv


def _pyproject(root: Path) -> Path:
    path = root / "pyproject.toml"
    if not path.is_file():
        raise FrameworkError(f"No pyproject.toml in {root}. Python projects list packages there.")
    return path


def _dependencies(root: Path) -> list[str]:
    data = tomllib.loads(_pyproject(root).read_text(encoding="utf-8"))
    dependencies = (data.get("project") or {}).get("dependencies") or []
    return [str(item) for item in dependencies]


def _package_name(requirement: str) -> str:
    match = _NAME.match(requirement)
    if match is None:
        raise ValueError(f"Not a package requirement: {requirement!r}")
    return match.group(1)


def _normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _installed_version(name: str) -> str | None:
    try:
        return version(name)
    except PackageNotFoundError:
        return None


def _check(status: int, what: str) -> None:
    if status != 0:
        raise FrameworkError(f"{what} failed (exit status {status})")


def _add_to_pyproject(path: Path, requirement: str) -> bool:
    """Add a requirement to ``[project] dependencies`` without uv. Returns whether it changed.

    The file is edited as text so comments and layout stay as they are.
    """
    text = path.read_text(encoding="utf-8")
    name = _normalize(_package_name(requirement))
    if any(_normalize(_package_name(d)) == name for d in _dependencies(path.parent)):
        return False
    project = re.search(r"(?ms)^\[project\]\s*$(.*?)(?=^\[|\Z)", text)
    block = re.search(r"(?ms)^dependencies\s*=\s*\[(.*?)\]", project.group(1)) if project else None
    if project is None or block is None:
        raise FrameworkError(
            "Could not find the dependencies list under [project] in pyproject.toml. "
            f"Add {requirement!r} there yourself, or install uv."
        )
    start = project.start(1) + block.start(1)
    end = project.start(1) + block.end(1)
    items = block.group(1)
    entry = json.dumps(requirement)
    if items.strip():
        body = items.rstrip()
        separator = "" if body.endswith(",") else ","
        updated = f"{body}{separator}\n    {entry},\n"
    else:
        updated = f"\n    {entry},\n"
    path.write_text(text[:start] + updated + text[end:], encoding="utf-8")
    return True


# Public API -----------------------------------------------------------------


def package_add(requirement: str, *, install: bool = True, dev: bool = False) -> None:
    """Add a package to the project's dependencies, and install it.

    Args:
        requirement: A package name, optionally with a version, such as
            ``"pandas"`` or ``"statsmodels>=0.14"``.
        install: Install it now. With uv, False only updates
            ``pyproject.toml`` and ``uv.lock``.
        dev: Add it to the ``dev`` dependency group (uv only).
    """
    show_messages()
    root = project_root()
    path = _pyproject(root)
    _package_name(requirement)
    uv = _uv()
    if uv is not None:
        args = [uv, "add", requirement]
        if dev:
            args.append("--dev")
        if not install:
            args.append("--no-sync")
        _check(_run(args, root), f"uv add {requirement}")
        inform(f"✓ Added {requirement}")
        return
    if dev:
        raise FrameworkError("Dev dependencies need uv. Install it from https://docs.astral.sh/uv/")
    if _add_to_pyproject(path, requirement):
        inform(f"✓ Added {requirement} to pyproject.toml")
    else:
        inform(f"{_package_name(requirement)} is already in pyproject.toml")
    if install:
        _check(_run([sys.executable, "-m", "pip", "install", requirement], root), "pip install")


def _package_set(set_id: str) -> dict[str, Any]:
    """A package set for a Python project: built in, or an account's set from the cloud."""
    from framework.features import _for_python, _sets

    if "/" in set_id and not set_id.startswith("framework/"):
        return _cloud_package_set(set_id)
    set_id = set_id.removeprefix("framework/")
    sets = _sets("packages")
    available = ", ".join(i for i, d in sets.items() if _for_python(d))
    definition = sets.get(set_id)
    if definition is None:
        raise FrameworkError(f"Unknown package set: {set_id}. Available: {available}")
    if not _for_python(definition):
        raise FrameworkError(
            f"The {definition['name']} package set isn't available for Python projects. "
            f"Available: {available}"
        )
    return definition


def _cloud_package_set(handle: str) -> dict[str, Any]:
    """An account's package set, resolved by Framework Cloud as ``new()`` resolves chosen sets."""
    from framework.cloud import _require_token, cloud_server, resolve_choices

    server = cloud_server().url
    token = _require_token(server)
    body = {"language": "python", "package_sets": [handle]}
    envelope, _ = resolve_choices(body, token, server)
    for entry in envelope.get("layers") or []:
        if isinstance(entry, dict) and entry.get("key") == handle:
            return {"id": handle, "name": handle, "layer": entry.get("layer") or {}}
    raise FrameworkError(f"The {handle} package set isn't available for Python projects.")


def package_set_add(set: str, *, install: bool = True) -> dict[str, list[str]]:
    """Add a package set's packages to the project's dependencies, and install them.

    Packages the project already lists are left as they are. The rest are added to
    ``pyproject.toml`` as ``package_add()`` adds them. A GitHub package keeps its repository.
    Sets also stack at creation with ``new(package_sets=[...])``.

    Args:
        set: A built-in package set for Python, such as ``"python-data-analysis"`` or
            ``"fastapi-python"`` (see ``package_sets_list()``). When you are signed in to
            Framework Cloud, a handle such as ``"@yourname/lab-tools"`` adds one of your
            account's sets.
        install: Install the added packages now. With uv, False only updates
            ``pyproject.toml`` and ``uv.lock``.

    Returns:
        ``{"added": [...], "present": [...]}``: the packages added and those the project
        already listed.

    Raises:
        FrameworkError: For an unknown set, a set for another language, or a failed install.
    """
    from framework.create import _dependency

    show_messages()
    root = project_root()
    path = _pyproject(root)
    definition = _package_set(set)
    wanted = ((definition.get("layer") or {}).get("packages") or {}).get("default_packages") or {}
    listed = {_normalize(_package_name(d)) for d in _dependencies(root)}
    present = [name for name in wanted if _normalize(name) in listed]
    added = [name for name in wanted if _normalize(name) not in listed]
    requirements = [_dependency(name, wanted[name] or {}) for name in added]

    if requirements:
        uv = _uv()
        if uv is not None:
            args = [uv, "add", *requirements]
            if not install:
                args.append("--no-sync")
            _check(_run(args, root), "uv add")
        else:
            for requirement in requirements:
                _add_to_pyproject(path, requirement)
            if install:
                args = [sys.executable, "-m", "pip", "install", *requirements]
                _check(_run(args, root), "pip install")
        inform(f"✓ Added {len(added)} package(s) from {definition['name']}: {', '.join(added)}")
    else:
        inform(f"The project already lists every package in {definition['name']}.")
    if present:
        inform(f"Already listed: {', '.join(present)}")
    return {"added": added, "present": present}


def package_sets_list() -> pd.DataFrame:
    """Framework's built-in package sets for Python projects.

    Add one to a project with ``package_set_add()``, or at creation with
    ``new(package_sets=[...])``.

    Returns:
        One row per set with ``id``, ``name``, ``description``, and ``packages`` (a list of
        package names).
    """
    from framework.features import _for_python, _sets

    rows = [
        {
            "id": d["id"],
            "name": d["name"],
            "description": d["description"],
            "packages": list(
                ((d.get("layer") or {}).get("packages") or {}).get("default_packages") or {}
            ),
        }
        for d in _sets("packages").values()
        if _for_python(d)
    ]
    return pd.DataFrame(rows, columns=["id", "name", "description", "packages"])


def packages_list() -> pd.DataFrame:
    """The project's dependencies from ``pyproject.toml``, with installed versions.

    Returns:
        A DataFrame with columns package, requirement, and installed (None
        when the package is not installed in the running environment).
    """
    root = project_root()
    rows = [
        {
            "package": _package_name(requirement),
            "requirement": requirement,
            "installed": _installed_version(_package_name(requirement)),
        }
        for requirement in _dependencies(root)
    ]
    return pd.DataFrame(rows, columns=["package", "requirement", "installed"])


def packages_install() -> None:
    """Install the project's dependencies: ``uv sync``, or pip without uv."""
    show_messages()
    root = project_root()
    uv = _uv()
    if uv is not None:
        _check(_run([uv, "sync"], root), "uv sync")
        return
    dependencies = _dependencies(root)
    if dependencies:
        _check(_run([sys.executable, "-m", "pip", "install", *dependencies], root), "pip install")


def packages_restore() -> None:
    """Install exactly the versions in ``uv.lock`` (``uv sync --frozen``)."""
    show_messages()
    root = project_root()
    uv = _require_uv("Restoring packages")
    if not (root / "uv.lock").is_file():
        raise FrameworkError("No uv.lock to restore from. Run packages_snapshot() first.")
    _check(_run([uv, "sync", "--frozen"], root), "uv sync --frozen")


def packages_snapshot() -> None:
    """Record the resolved versions in ``uv.lock`` (``uv lock``)."""
    show_messages()
    root = project_root()
    _pyproject(root)
    _check(_run([_require_uv("Locking packages"), "lock"], root), "uv lock")


def packages_update(packages: str | list[str] | None = None) -> None:
    """Upgrade locked versions, all of them or the named ones, and install them.

    Runs ``uv lock --upgrade`` (or ``--upgrade-package`` for each name) and then ``uv sync`` in
    the project root.

    Args:
        packages: A package name or a list of names to upgrade. Default: every package.

    Raises:
        FrameworkError: When uv is not installed or a uv command fails.
    """
    show_messages()
    root = project_root()
    uv = _require_uv("Updating packages")
    args = [uv, "lock"]
    if packages is None:
        args.append("--upgrade")
    else:
        for name in [packages] if isinstance(packages, str) else packages:
            args += ["--upgrade-package", name]
    _check(_run(args, root), "uv lock")
    _check(_run([uv, "sync"], root), "uv sync")


@dataclass
class PackagesStatus:
    """Whether the lock file and the environment match ``pyproject.toml``.

    ``lock_current`` and ``synced`` are None when uv is not installed.

    Attributes:
        lock_current: Whether ``uv lock --check`` passes, so ``uv.lock`` matches
            ``pyproject.toml``. False when there is no ``uv.lock``.
        synced: Whether ``uv sync --check --frozen`` passes, so the project environment
            matches ``uv.lock``. None when there is no ``uv.lock``.
        missing: Dependencies in ``pyproject.toml`` that are not installed in the running
            Python environment.
    """

    lock_current: bool | None
    synced: bool | None
    missing: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.lock_current is not False and self.synced is not False and not self.missing


def packages_status() -> PackagesStatus:
    """Check that ``uv.lock`` is current and the environment matches it.

    ``missing`` lists dependencies that are not installed in the running
    Python environment.
    """
    root = project_root()
    missing = [
        _package_name(requirement)
        for requirement in _dependencies(root)
        if _installed_version(_package_name(requirement)) is None
    ]
    uv = _uv()
    lock_current = synced = None
    if uv is not None:

        def quiet(*args: str) -> bool:
            completed = subprocess.run(
                [uv, *args],
                cwd=root,
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return completed.returncode == 0

        if (root / "uv.lock").is_file():
            lock_current = quiet("lock", "--check")
            synced = quiet("sync", "--check", "--frozen")
        else:
            lock_current = False
    status = PackagesStatus(lock_current=lock_current, synced=synced, missing=missing)
    has_lock = (root / "uv.lock").is_file()
    if uv is None:
        lock_line = environment_line = "unknown (uv is not installed)"
    elif not has_lock:
        lock_line = "missing (run packages_snapshot())"
        environment_line = "unknown (no uv.lock to compare with)"
    else:
        lock_line = "current" if lock_current else "out of date (run packages_snapshot())"
        environment_line = "in sync" if synced else "out of sync (run packages_restore())"
    lines = [f"Lock file: {lock_line}", f"Environment: {environment_line}"]
    if missing:
        lines.append(f"Not installed here: {', '.join(missing)}")
        venv = root / ".venv"
        if venv.is_dir() and Path(sys.prefix).resolve() != venv.resolve():
            lines.append(f"(This checks the running Python, {sys.prefix}, not {venv}.)")
    print("\n".join(lines))
    return status
