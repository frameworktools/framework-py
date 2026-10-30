"""Git hooks: ``git_hooks_install()`` and friends, and the pre-commit checks.

The installed hook is the shell script shared with R and ``fw``. It only finds
a runtime and calls ``python -m framework hook pre-commit`` (or R's
equivalent), which runs the checks enabled under ``git: hooks:`` in the
settings:

- ``data_security``: ``git_security_audit()`` must not fail.
- ``check_sensitive_dirs`` (formerly ``warn_unignored_sensitive``): warn about
  folders named like private, confidential, sensitive, cache, or scratch that
  git does not ignore, and ask before committing when a terminal is attached.
- ``ai_sync`` is gone: projects keep AI instructions in AGENTS.md and skills,
  so there is nothing to regenerate. An ``ai_sync`` setting in an older
  project is ignored.
"""

from __future__ import annotations

import fnmatch
import os
import shutil
import stat
import subprocess
import sys
from importlib.resources import files
from pathlib import Path

import pandas as pd

from framework import _yaml
from framework._log import inform, show_messages
from framework.errors import FrameworkError
from framework.project import find_settings_file
from framework.settings import _has_environment_sections, settings_read

HOOKS = ("data_security", "check_sensitive_dirs")
_ALIASES = {"warn_unignored_sensitive": "check_sensitive_dirs"}
_DESCRIPTIONS = {
    "data_security": "Check for private data and secrets in git",
    "check_sensitive_dirs": "Warn about unignored sensitive directories",
}
# Headers of the shared hook and of R 1.x's generated hook.
_MARKERS = ("Framework pre-commit hook", "Framework Git Hooks")
_SENSITIVE_PATTERNS = ("*private*", "*confidential*", "*sensitive*", "*cache*", "*scratch*")


def _say(verbose: bool, message: str) -> None:
    if verbose:
        show_messages()
        inform(message)


def hook_script() -> str:
    """The shared pre-commit script Framework installs (synced from the R package)."""
    return files("framework").joinpath("_assets/hooks/pre-commit").read_text(encoding="utf-8")


# Settings -------------------------------------------------------------------


def _settings_path(settings_file: str | Path | None) -> Path:
    path = Path(settings_file) if settings_file is not None else find_settings_file()
    if path is None or not path.is_file():
        raise FrameworkError("Settings file not found (expected settings.yml or config.yml)")
    return path.resolve()


def _hook_settings(path: Path) -> dict[str, bool]:
    git = settings_read(path).get("git")
    hooks = git.get("hooks") if isinstance(git, dict) else None
    hooks = hooks if isinstance(hooks, dict) else {}
    enabled = {name: hooks.get(name) is True for name in HOOKS}
    for alias, name in _ALIASES.items():
        enabled[name] = enabled[name] or hooks.get(alias) is True
    return enabled


def _set_hook_setting(path: Path, name: str, value: bool) -> None:
    """Set ``git: hooks: <name>`` in the settings file, or in its split git file."""
    raw = _yaml.read(path) or {}
    env = raw.setdefault("default", {}) if _has_environment_sections(raw) else raw
    if env is None:
        env = raw["default"] = {}
    reference = env.get("git")
    if isinstance(reference, str) and reference.startswith("settings/"):
        split_path = path.parent / reference
        split = (_yaml.read(split_path) if split_path.is_file() else None) or {}
        git = split.setdefault("git", {}) if "git" in split or not split else split
        git.setdefault("hooks", {})[name] = value
        _yaml.write(split_path, split)
        return
    git = env.get("git") if isinstance(env.get("git"), dict) else None
    if git is None:
        git = env["git"] = {}
    hooks = git.get("hooks") if isinstance(git.get("hooks"), dict) else None
    if hooks is None:
        hooks = git["hooks"] = {}
    hooks[name] = value
    _yaml.write(path, raw)


def _hook_name(name: str) -> str:
    if name == "ai_sync":
        raise ValueError(
            "The ai_sync hook was removed: AI instructions live in AGENTS.md and skills, "
            "so there is nothing to sync."
        )
    valid = (*HOOKS, *_ALIASES)
    if name not in valid:
        raise ValueError(f"Invalid hook name. Must be one of: {', '.join(valid)}")
    return _ALIASES.get(name, name)


# Git ------------------------------------------------------------------------


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, check=False
    )


def _hook_path(root: Path) -> Path | None:
    """Where git looks for the pre-commit hook, or None outside a repository."""
    if shutil.which("git") is None:
        return None
    result = _git(root, "rev-parse", "--git-path", "hooks/pre-commit")
    if result.returncode != 0:
        return None
    path = Path(result.stdout.strip())
    return path if path.is_absolute() else root / path


def _is_framework_hook(path: Path) -> bool:
    try:
        head = path.read_text(encoding="utf-8", errors="replace")[:500]
    except OSError:
        return False
    return any(marker in head for marker in _MARKERS)


# Public API -----------------------------------------------------------------


def git_hooks_install(
    settings_file: str | Path | None = None, *, force: bool = False, verbose: bool = True
) -> bool:
    """Install Framework's pre-commit hook when a hook is enabled in the settings.

    A pre-commit hook Framework wrote earlier (by R or Python) is replaced. Any
    other pre-commit hook is kept unless ``force`` is True.

    Returns:
        Whether the hook was installed.
    """
    path = _settings_path(settings_file)
    root = path.parent
    hook = _hook_path(root)
    if hook is None:
        _say(verbose, "✗ Not a git repository")
        return False
    enabled = _hook_settings(path)
    if not any(enabled.values()):
        _say(verbose, "No hooks enabled in settings.yml")
        _say(verbose, '  Enable one with: git_hooks_enable("data_security")')
        return False
    if hook.exists() and not force and not _is_framework_hook(hook):
        _say(verbose, "✗ A pre-commit hook that Framework did not write already exists")
        _say(verbose, f"  Use force=True to replace it, or edit {hook} yourself")
        return False
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_bytes(hook_script().encode("utf-8"))
    hook.chmod(hook.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    _say(verbose, "✓ Installed pre-commit hook")
    for name, on in enabled.items():
        if on:
            _say(verbose, f"  • {_DESCRIPTIONS[name]}")
    return True


def git_hooks_uninstall(settings_file: str | Path | None = None, *, verbose: bool = True) -> bool:
    """Remove Framework's pre-commit hook. A hook Framework did not write is kept.

    Returns:
        Whether a hook was removed.
    """
    root = _settings_path(settings_file).parent if settings_file else _root_or_cwd()
    hook = _hook_path(root)
    if hook is None or not hook.exists():
        _say(verbose, "No pre-commit hook found")
        return False
    if not _is_framework_hook(hook):
        _say(verbose, "⚠ The pre-commit hook was not written by Framework, so it was kept")
        return False
    hook.unlink()
    _say(verbose, "✓ Removed pre-commit hook")
    return True


def _root_or_cwd() -> Path:
    found = find_settings_file()
    return found.parent if found is not None else Path.cwd()


def git_hooks_enable(
    hook_name: str, settings_file: str | Path | None = None, *, verbose: bool = True
) -> bool:
    """Turn on one hook check in the settings and (re)install the hook.

    Returns:
        Whether the hook is installed.
    """
    name = _hook_name(hook_name)
    path = _settings_path(settings_file)
    _set_hook_setting(path, name, True)
    _say(verbose, f"✓ Enabled {name} hook")
    return git_hooks_install(path, force=False, verbose=verbose)


def git_hooks_disable(
    hook_name: str, settings_file: str | Path | None = None, *, verbose: bool = True
) -> bool:
    """Turn off one hook check. The hook is removed when no check is left.

    ``"ai_sync"`` is accepted so older settings can be cleaned up: it is set
    to false.

    Returns:
        Whether the hook is still installed.
    """
    name = "ai_sync" if hook_name == "ai_sync" else _hook_name(hook_name)
    path = _settings_path(settings_file)
    _set_hook_setting(path, name, False)
    if name == "check_sensitive_dirs":
        raw_git = settings_read(path).get("git") or {}
        if isinstance(raw_git, dict) and (raw_git.get("hooks") or {}).get(
            "warn_unignored_sensitive"
        ):
            _set_hook_setting(path, "warn_unignored_sensitive", False)
    _say(verbose, f"✓ Disabled {name} hook")
    if not any(_hook_settings(path).values()):
        git_hooks_uninstall(path, verbose=verbose)
        return False
    return git_hooks_install(path, force=False, verbose=verbose)


def git_hooks_list(settings_file: str | Path | None = None) -> pd.DataFrame:
    """The hook checks and whether each is enabled.

    Returns:
        A DataFrame with columns hook, enabled, and description. Its
        ``attrs["installed"]`` says whether the pre-commit hook is installed.
    """
    path = _settings_path(settings_file)
    enabled = _hook_settings(path)
    hook = _hook_path(path.parent)
    frame = pd.DataFrame(
        {
            "hook": list(HOOKS),
            "enabled": [enabled[name] for name in HOOKS],
            "description": [_DESCRIPTIONS[name] for name in HOOKS],
        }
    )
    frame.attrs["installed"] = bool(hook and hook.exists() and _is_framework_hook(hook))
    return frame


# Running the hook -----------------------------------------------------------


def _unignored_sensitive_dirs(root: Path) -> list[str]:
    found: list[str] = []
    for current, dirnames, _ in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in (".git", ".venv", "node_modules")]
        for name in dirnames:
            if any(fnmatch.fnmatch(name.lower(), pattern) for pattern in _SENSITIVE_PATTERNS):
                found.append((Path(current) / name).relative_to(root).as_posix())
    if not found:
        return []
    # A folder is covered when it or an ancestor is ignored.
    candidates = {
        ancestor
        for folder in found
        for ancestor in [folder, *(p.as_posix() for p in Path(folder).parents if str(p) != ".")]
    }
    # -z separates paths with NUL, since text pipes on Windows turn "\n" into "\r\n".
    result = subprocess.run(
        ["git", "-C", str(root), "check-ignore", "--no-index", "--stdin", "-z"],
        input="\0".join(sorted(candidates)),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    ignored = {path for path in result.stdout.split("\0") if path}
    return [
        folder
        for folder in found
        if not ({folder} | {p.as_posix() for p in Path(folder).parents}) & ignored
    ]


def _confirm(question: str) -> bool | None:
    """Ask on the terminal, even inside a git hook. None when there is no terminal."""
    device = "CON" if sys.platform == "win32" else "/dev/tty"
    try:
        with open(device, "r+", encoding="utf-8") as tty:  # noqa: PTH123
            tty.write(question)
            tty.flush()
            return tty.readline().strip().lower() in ("y", "yes")
    except OSError:
        return None


def _check_sensitive_dirs(root: Path) -> bool:
    folders = _unignored_sensitive_dirs(root)
    if not folders:
        return True
    lines = ["⚠ Warning: Found sensitive directories that are NOT gitignored:"]
    lines += [f"  - {folder}" for folder in folders]
    lines += [
        "",
        "These directories may contain sensitive data and should be added to .gitignore.",
        "Pattern-based .gitignore entries like '**/private/**' will automatically catch them.",
        "",
    ]
    print("\n".join(lines), file=sys.stderr)
    answer = _confirm("Continue with commit anyway? (y/N) ")
    if answer is None:
        print("(Non-interactive mode: continuing with warning)", file=sys.stderr)
        return True
    if not answer:
        print("Commit aborted. Add directories to .gitignore and try again.", file=sys.stderr)
    return answer


def hook_run(name: str, settings_file: str | Path | None = None) -> int:
    """Run the checks for one git hook. Returns the exit status for git.

    Only ``pre-commit`` exists. A project without a settings file passes, so
    the hook never blocks commits in a folder that is not a Framework project.
    """
    if name != "pre-commit":
        print(f"Framework hook: unknown hook '{name}'", file=sys.stderr)
        return 1
    try:
        path = _settings_path(settings_file)
    except FrameworkError:
        return 0
    root = path.parent
    enabled = _hook_settings(path)

    if enabled["check_sensitive_dirs"] and not _check_sensitive_dirs(root):
        return 1
    if enabled["data_security"]:
        from framework.security import _report, git_security_audit

        result = git_security_audit(path, verbose=False)
        if not result.passed:
            print(_report(result), file=sys.stderr)
            print("✗ Data security check failed", file=sys.stderr)
            return 1
    return 0
