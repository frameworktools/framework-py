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
    """Upgrade locked versions, all of them or the named ones, and install them."""
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
    lines = [
        f"Lock file: {_describe(lock_current, 'current', 'out of date (run packages_snapshot())')}",
        f"Environment: {_describe(synced, 'in sync', 'out of sync (run packages_restore())')}",
    ]
    if missing:
        lines.append(f"Not installed here: {', '.join(missing)}")
    print("\n".join(lines))
    return status


def _describe(value: bool | None, good: str, bad: str) -> str:
    return "unknown (uv is not installed)" if value is None else good if value else bad
