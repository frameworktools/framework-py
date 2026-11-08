"""``git_security_audit()``: checks that private data stays out of git.

The checks match R's ``git_security_audit()``:

- gitignore_coverage: private folders and the data files in them are ignored.
- private_data_exposure: git tracks no file in a private folder.
- git_history: no commit added, changed, renamed, or copied a data file in a
  private folder, in an inputs folder, or with identifier-like columns in its
  header (read from the committed blob). ``reason`` says which.
- orphaned_files: data files sit inside the folders the settings name
  (``directories``, ``extra_directories``, ``render_dirs``). Files git ignores,
  hidden folders, and JSON, text, and XML files (unless asked for) are not
  counted.
- secrets_exposure: git tracks no ``.env`` file.
- published_outputs: folder publishing destinations inside the project that hold
  files are ignored by git. In projects that are not sensitive, a destination
  with ``visibility: public`` is meant to be shared, such as a GitHub Pages
  ``docs/`` folder, and is left out. Python adds this check; R will follow.

- connection_secrets: no password, secret, token, or key in the settings'
  ``connections`` is written out instead of read from the environment. A key
  matching ``password|passwd|pwd|secret|token|access_?key|api_?key|private_?key|
  credential`` (any case) may hold ``env("VAR")`` or ``env("VAR", "")``;
  ``env("VAR", "value")`` is a finding, as on Framework Cloud. A tracked
  settings file fails; an untracked one warns. Python adds this check;
  R will follow.

- identifier_columns: warns about data files git tracks, outside private
  folders, whose header has columns that look like direct identifiers (dates
  of birth, ZIP codes, SSNs, medical record numbers, names with dates of
  birth). Only the header is read. Python adds this check; R will follow.

Folders configured outside the project are skipped, since git can't commit
them from here, and listed in the report and in ``metadata``.

Where git is available, ``git check-ignore`` decides what is ignored. Without
git, a port of R's ``.gitignore`` matcher does.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from framework import db
from framework._log import inform, show_messages
from framework._paths import has_private_segment
from framework.errors import FrameworkError
from framework.project import find_settings_file
from framework.settings import settings_read

DATA_EXTENSIONS = (
    "csv", "rds", "tsv", "txt", "dat", "xlsx", "xls", "sqlite", "db", "dta", "sav",
    "zsav", "por", "sas7bdat", "sas7bcat", "xpt", "parquet", "feather", "arrow",
    "json", "xml", "h5", "hdf5",
)  # fmt: skip

CHECKS = (
    "gitignore_coverage",
    "private_data_exposure",
    "git_history",
    "orphaned_files",
    "secrets_exposure",
    "published_outputs",
    "connection_secrets",
    "identifier_columns",
)

# Folders the orphan check skips at any depth, besides hidden ones (.git, .venv,
# .framework, .claude, .vite, ...) and Quarto's *_files and *_cache
_EXCLUDED_DIRS = ("renv", "packrat", "node_modules", "_site", "_freeze", "__pycache__")
_EXCLUDED_FILES = {"framework.db", ".framework_renv_enabled", ".framework_scaffolded", ".initiated"}
_SECRETS_FILE = re.compile(r"^\.env(\..+)?$")
_SECRETS_EXAMPLE = re.compile(r"\.(example|template|sample)$")
_HISTORY_ACTIONS = {
    "A": "added",
    "M": "modified",
    "D": "deleted",
    "T": "modified",
    "R": "renamed",
    "C": "copied",
}
_HISTORY_MAX_BYTES = 50 * 1024**2
# Orphan checks leave these out unless asked for: mostly code, cohort definitions,
# manifests, and notes, rarely data
_ORPHAN_SKIPPED_EXTENSIONS = ("json", "txt", "xml")
_AUTO_FIX_HEADER = "# Framework Security Audit - Auto-generated"


@dataclass
class AuditResult:
    """What ``git_security_audit()`` found.

    Attributes:
        summary: One row per check, with columns check, status (pass,
            warning, fail, or skipped), and count.
        findings: A DataFrame of findings per check.
        recommendations: What to do about the findings.
        metadata: When the audit ran, the folders it checked, and whether git
            was available.
    """

    summary: pd.DataFrame
    findings: dict[str, pd.DataFrame]
    recommendations: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def status(self) -> str:
        """The overall result: fail, warning, or pass."""
        statuses = set(self.summary["status"])
        return "fail" if "fail" in statuses else "warning" if "warning" in statuses else "pass"

    @property
    def passed(self) -> bool:
        """Whether no check failed. Warnings still pass."""
        return self.status != "fail"


# Git ------------------------------------------------------------------------


def _git(root: Path, *args: str, input: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        input=input,
        check=False,
        encoding="utf-8",
        errors="replace",
    )


def _git_available(root: Path) -> bool:
    if shutil.which("git") is None:
        return False
    return _git(root, "rev-parse", "--show-toplevel").returncode == 0


def _tracked_files(root: Path) -> list[str]:
    result = _git(root, "ls-files", "-z")
    return [name for name in result.stdout.split("\0") if name] if result.returncode == 0 else []


# Ignore rules ---------------------------------------------------------------


def _gitignore_regex(pattern: str) -> str:
    """R's translation of one ``.gitignore`` pattern to an anchored regex."""
    directory_only = pattern.endswith("/")
    pattern = pattern.removeprefix("/")
    pattern = pattern.replace(".", r"\.")
    pattern = pattern.replace("**/", "\x01")
    pattern = re.sub(r"/\*\*$", "\x02", pattern)
    pattern = pattern.replace("**", "\x03")
    pattern = pattern.replace("*", "[^/]*")
    pattern = pattern.replace("\x01", "(.*/)?").replace("\x02", "/.*").replace("\x03", ".*")
    if directory_only:
        pattern = pattern.removesuffix("/") + "(/.*)?"
    return pattern


def _matches_gitignore(path: str, lines: list[str]) -> bool:
    """Whether ``.gitignore`` lines ignore ``path``, as R checks without git."""
    path = path.removeprefix("./")
    ignored = False
    for line in lines:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        negated = line.startswith("!")
        regex = _gitignore_regex(line[1:] if negated else line)
        if re.fullmatch(regex, path):
            ignored = not negated
    return ignored


class _IgnoreRules:
    """Answers whether paths are ignored, through git when it is available."""

    def __init__(self, root: Path, use_git: bool) -> None:
        self.root = root
        self.use_git = use_git
        gitignore = root / ".gitignore"
        self.lines = (
            gitignore.read_text(encoding="utf-8").splitlines() if gitignore.exists() else []
        )

    def ignored(self, paths: list[str]) -> set[str]:
        if not paths:
            return set()
        if not self.use_git:
            return {path for path in paths if _matches_gitignore(path, self.lines)}
        # --no-index checks the rules even for tracked files. -z separates paths
        # with NUL, since text pipes on Windows turn "\n" into "\r\n".
        result = _git(
            self.root, "check-ignore", "--no-index", "--stdin", "-z", input="\0".join(paths)
        )
        return {path for path in result.stdout.split("\0") if path}

    def folder_ignored(self, folder: str) -> bool:
        """A folder counts as ignored when it, or anything inside it, would be."""
        probe = f"{folder}/.framework-audit-probe"
        return bool(self.ignored([folder, f"{folder}/", probe]))


# Folders --------------------------------------------------------------------


def _data_directories(config: dict[str, Any], root: Path) -> dict[str, str]:
    """The folders the audit watches: the settings' data folders, then R's fallbacks.

    A settings folder counts when its key starts with ``inputs_`` or ``outputs_``, is
    ``cache``, ``scratch``, or ``logs``, or when its key or path is named like private,
    confidential, or sensitive (``original_private_tables``). Folders outside the project
    are left to ``_outside_directories()``: git can't commit them from here.
    """
    folders: dict[str, str] = {}
    for key, value in _configured_directories(config).items():
        if _outside(root, value):
            continue
        if (
            re.match(r"^(inputs|outputs)_", key)
            or key in ("cache", "scratch", "logs")
            or re.search("private|confidential|sensitive", key, re.IGNORECASE)
            or has_private_segment(value)
        ):
            folders[key] = _relative_folder(root, value)

    def fallback(key: str, path: str) -> None:
        if key not in folders and (root / path).is_dir():
            folders[key] = path

    for name in ("raw", "intermediate", "final"):
        fallback(f"inputs_{name}", f"inputs/{name}")
    fallback("docs", "docs")
    fallback("outputs_private", "outputs/private")
    for name in ("tables", "figures", "models", "notebooks", "docs", "final", "cache", "scratch"):
        fallback(f"outputs_{name}", f"outputs/private/{name}")
    fallback("outputs_public", "outputs/public")
    for name in ("tables", "figures", "models", "notebooks", "docs", "final"):
        fallback(f"outputs_{name}_public", f"outputs/public/{name}")
    return folders


def _configured_directories(config: dict[str, Any]) -> dict[str, str]:
    directories = config.get("directories")
    return {
        str(key): value.removeprefix("./").rstrip("/")
        for key, value in (directories if isinstance(directories, dict) else {}).items()
        if isinstance(value, str) and value
    }


def _outside(root: Path, folder: str) -> bool:
    """Whether a settings folder (relative to the root unless absolute) is outside it."""
    path = Path(folder).expanduser()
    if not path.is_absolute():
        path = root / path
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return True
    return False


def _relative_folder(root: Path, folder: str) -> str:
    """An absolute folder inside the project, as a path from the root."""
    path = Path(folder).expanduser()
    if not path.is_absolute():
        return folder
    relative = path.resolve().relative_to(root.resolve()).as_posix()
    return "." if relative == "" else relative


def _outside_directories(config: dict[str, Any], root: Path) -> dict[str, str]:
    """Settings folders outside the project, which the audit skips and reports."""
    return {
        key: value
        for key, value in _configured_directories(config).items()
        if _outside(root, value)
    }


def _private_directories(folders: dict[str, str]) -> dict[str, str]:
    """The folders whose contents must not be committed.

    These are the folders every project type's ``.gitignore`` ignores:
    ``cache``, ``scratch``, and ``logs``, and folders whose key or path is
    marked private (``outputs_private``, ``inputs/private/raw``). R's audit
    also counts every other ``inputs_*`` and ``outputs_*`` folder, which a
    standard project's ``.gitignore`` does not ignore, so its audit always
    failed there. Keys containing ``_public`` are exempt.
    """
    return {
        key: path
        for key, path in folders.items()
        if "_public" not in key
        and ("private" in key or has_private_segment(path) or key in ("cache", "scratch", "logs"))
    }


def _deepest_first(folders: dict[str, str]) -> list[tuple[str, str]]:
    """Folders with the deepest first, keeping settings order among equals."""
    return sorted(folders.items(), key=lambda item: -len(Path(item[1]).parts))


def _is_data_file(path: str, extensions: tuple[str, ...]) -> bool:
    return Path(path).suffix.lower().removeprefix(".") in extensions


def _inside(path: str, folder: str) -> bool:
    return path == folder or path.startswith(folder.rstrip("/") + "/")


def _files_under(root: Path, folder: str) -> list[str]:
    base = root / folder
    return sorted(path.relative_to(root).as_posix() for path in base.rglob("*") if path.is_file())


# Checks ---------------------------------------------------------------------


def _check_gitignore(
    root: Path, private: dict[str, str], rules: _IgnoreRules, extensions: tuple[str, ...]
) -> tuple[pd.DataFrame, list[str]]:
    rows: list[dict[str, str]] = []
    if not (root / ".gitignore").exists():
        return _frame(rows, ["file", "directory", "severity", "reason"]), [
            "Create a .gitignore file to protect sensitive data"
        ]
    reported: set[str] = set()
    for key, folder in _deepest_first(private):
        if not (root / folder).is_dir() or folder in reported:
            continue
        reported.add(folder)
        if not rules.folder_ignored(folder):
            rows.append(
                {
                    "file": folder,
                    "directory": key,
                    "severity": "critical",
                    "reason": "Private data directory not in .gitignore",
                }
            )
        data_files = [
            f
            for f in _files_under(root, folder)
            if _is_data_file(f, extensions) and f not in reported
        ]
        reported.update(data_files)
        ignored = rules.ignored(data_files)
        rows.extend(
            {
                "file": name,
                "directory": key,
                "severity": "critical",
                "reason": "Private data file not in .gitignore",
            }
            for name in data_files
            if name not in ignored
        )
    advice = (
        [
            "Add private data directories to .gitignore",
            "Review all private data files for git tracking",
        ]
        if rows
        else []
    )
    return _frame(rows, ["file", "directory", "severity", "reason"]), advice


def _check_exposure(
    root: Path, private: dict[str, str], tracked: list[str], extensions: tuple[str, ...]
) -> tuple[pd.DataFrame, list[str]]:
    # Each file once, under the deepest folder that holds it: settings keys can name the
    # same folder twice (outputs_private_tables and R's outputs_tables fallback).
    rows: list[dict[str, str]] = []
    reported: set[str] = set()
    for key, folder in _deepest_first(private):
        if not (root / folder).is_dir():
            continue
        for name in tracked:
            if _inside(name, folder) and name not in reported:
                reported.add(name)
                rows.append({"file": name, "directory": key, "git_status": "tracked"})
    # Also data files in any folder named private, confidential, or sensitive,
    # such as inputs/private itself, which no setting names.
    rows += [
        {"file": name, "directory": str(Path(name).parent.as_posix()), "git_status": "tracked"}
        for name in tracked
        if name not in reported
        and _is_data_file(name, extensions)
        and has_private_segment(Path(name).parent)
    ]
    advice = (
        [
            "CRITICAL: Private data files are tracked by git!",
            "Run: git rm --cached <file> to untrack files",
            "Add files to .gitignore before committing",
        ]
        if rows
        else []
    )
    return _frame(rows, ["file", "directory", "git_status"]), advice


def _check_secrets(tracked: list[str]) -> tuple[pd.DataFrame, list[str]]:
    files = [
        name
        for name in tracked
        if _SECRETS_FILE.match(Path(name).name) and not _SECRETS_EXAMPLE.search(name)
    ]
    rows = [{"file": name, "git_status": "tracked"} for name in files]
    advice = (
        [
            "CRITICAL: Secrets files are tracked by git!",
            f"Run: git rm --cached {' '.join(files)}, then commit",
            "Add .env to .gitignore",
            "Rotate any passwords or tokens in those files (including FW_PROJECT_TOKEN): "
            "they stay in git history",
        ]
        if files
        else []
    )
    return _frame(rows, ["file", "git_status"]), advice


def _history_changes(output: str) -> list[dict[str, str]]:
    """The file changes in ``git log --name-status`` output: one row per changed path with
    its commit, date, and action. A rename or copy counts for the path it creates."""
    rows: list[dict[str, str]] = []
    commit = date = None
    for line in output.splitlines():
        if re.match(r"^[0-9a-f]{40}\x1f", line):
            commit, date = line.split("\x1f", 1)
            continue
        parts = line.split("\t")
        if commit is None or len(parts) < 2 or not re.match(r"^[AMDTRC][0-9]*$", parts[0]):
            continue
        rows.append(
            {
                "hash": commit,
                "date": date or "",
                "action": _HISTORY_ACTIONS[parts[0][0]],
                "file": parts[-1],
            }
        )
    return rows


def _history_header_columns(root: Path, commit: str, name: str) -> list[str] | None:
    """A committed file's column names at ``commit``, from its header only. The blob goes
    to a temporary file that is removed right away; files over 50 MB are not read."""
    from framework._identifiers import header_columns

    size = _git(root, "cat-file", "-s", f"{commit}:{name}")
    try:
        if size.returncode != 0 or int(size.stdout.strip()) > _HISTORY_MAX_BYTES:
            return None
    except ValueError:
        return None
    blob = subprocess.run(
        ["git", "-C", str(root), "cat-file", "blob", f"{commit}:{name}"],
        capture_output=True,
        check=False,
    )
    if blob.returncode != 0:
        return None
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / f"blob{Path(name).suffix}"
        path.write_bytes(blob.stdout)
        return header_columns(path)


def _check_history(
    root: Path,
    private: dict[str, str],
    inputs: dict[str, str],
    extensions: tuple[str, ...],
    depth: int | str,
) -> tuple[pd.DataFrame, list[str]]:
    """Data files committed in a private folder, in an inputs folder, or with
    identifier-like columns in their header, at any point in history (added, modified,
    deleted, renamed, or copied)."""
    from framework._identifiers import HEADER_TYPES, identifier_columns

    args = ["log", "--all", "--pretty=format:%H%x1f%ad", "--date=iso", "--name-status"]
    if isinstance(depth, int):
        args.append(f"-{depth}")
    elif depth == "shallow":
        args.append("-100")
    result = _git(root, *args)
    changes = [
        change
        for change in (_history_changes(result.stdout) if result.returncode == 0 else [])
        if _is_data_file(change["file"], extensions)
    ]

    # Why each path is a leak, checked once per path
    reasons: dict[str, str] = {}
    for name in dict.fromkeys(change["file"] for change in changes):
        folders = [part.lower() for part in name.split("/")[:-1]]
        if any(_inside(name, folder) for folder in private.values()) or re.search(
            "private|secret|confidential", name, re.IGNORECASE
        ):
            reasons[name] = "private folder"
        elif any(_inside(name, folder) for folder in inputs.values()) or any(
            part in ("inputs", "input") for part in folders
        ):
            reasons[name] = "inputs folder"
        elif Path(name).suffix.lower() in HEADER_TYPES:
            kept = [c for c in changes if c["file"] == name and c["action"] != "deleted"]
            columns = _history_header_columns(root, kept[0]["hash"], name) if kept else None
            found = identifier_columns(columns or [])
            if found:
                reasons[name] = "identifier columns: " + ", ".join(c for c, _ in found)
    rows = [
        {
            "commit": change["hash"][:8],
            "file": change["file"],
            "action": change["action"],
            "date": change["date"],
            "reason": reasons[change["file"]],
        }
        for change in changes
        if change["file"] in reasons
    ]
    advice = (
        [
            "CRITICAL: Data files in private or inputs folders, or with identifier-like "
            "columns, are in git history!",
            "Consider using git-filter-repo or BFG Repo-Cleaner to remove sensitive data",
            "Consult: https://docs.github.com/en/authentication/keeping-your-account-and-data-"
            "secure/removing-sensitive-data-from-a-repository",
        ]
        if rows
        else []
    )
    return _frame(rows, ["commit", "file", "action", "date", "reason"]), advice


def _project_folders(config: dict[str, Any], root: Path) -> list[str]:
    """Every folder the settings name inside the project: directories,
    extra_directories, and render_dirs, as paths from the root."""
    paths: list[Any] = list(_configured_directories(config).values())
    extra = config.get("extra_directories")
    entries = (
        extra.values() if isinstance(extra, dict) else extra if isinstance(extra, list) else []
    )
    paths += [e.get("path") for e in entries if isinstance(e, dict)]
    render = config.get("render_dirs")
    paths += list(render.values()) if isinstance(render, dict) else []
    cleaned = [p.removeprefix("./").rstrip("/") for p in paths if isinstance(p, str)]
    return [
        _relative_folder(root, p) for p in dict.fromkeys(cleaned) if p and not _outside(root, p)
    ]


def _check_orphans(
    root: Path, folders: dict[str, str], extensions: tuple[str, ...], rules: _IgnoreRules
) -> tuple[pd.DataFrame, list[str]]:
    """Data files outside the configured folders. Files git ignores are left out: they
    cannot be committed, so they are not a risk wherever they sit."""
    rows: list[dict[str, Any]] = []
    configured = list(folders.values())
    candidates = [
        relative
        for relative in (path.relative_to(root).as_posix() for path in _walk_files(root))
        if relative not in _EXCLUDED_FILES
        and _is_data_file(relative, extensions)
        and not any(_inside(relative, folder) for folder in configured)
    ]
    ignored = rules.ignored(candidates)
    for relative in candidates:
        if relative in ignored:
            continue
        path = root / relative
        info = path.stat()
        rows.append(
            {
                "path": relative,
                "size": info.st_size,
                "modified": datetime.fromtimestamp(info.st_mtime).isoformat(
                    sep=" ", timespec="seconds"
                ),
                "extension": path.suffix.removeprefix("."),
            }
        )
    advice = (
        [
            f"Found {len(rows)} data file(s) outside configured directories",
            "Move orphaned files to appropriate data directories",
            "Add data file locations to settings.yml if they represent new data storage",
        ]
        if rows
        else []
    )
    return _frame(rows, ["path", "size", "modified", "extension"]), advice


def folder_destinations(config: dict[str, Any], root: Path) -> dict[str, str]:
    """Folder publishing destinations inside the project that git should ignore, as paths
    from the root: every one in a sensitive project, and in other projects those without
    ``visibility: public`` (or ``private: false``)."""
    publish = config.get("publish")
    destinations = publish.get("destinations") if isinstance(publish, dict) else None
    from framework._private import settings_sensitive

    sensitive = settings_sensitive(config)
    found: dict[str, str] = {}
    for name, options in (destinations if isinstance(destinations, dict) else {}).items():
        if not isinstance(options, dict) or options.get("driver") != "folder":
            continue
        path = options.get("path")
        if not isinstance(path, str) or not path or _outside(root, path):
            continue
        public = options.get("visibility") == "public" or options.get("private") is False
        if public and not sensitive:
            continue
        relative = _relative_folder(root, path.removeprefix("./").rstrip("/"))
        if relative != ".":
            found[str(name)] = relative
    return found


def _check_published(
    root: Path, config: dict[str, Any], rules: _IgnoreRules, tracked: list[str]
) -> tuple[pd.DataFrame, list[str]]:
    """Folder destinations that hold files git doesn't ignore: rendered notebooks and
    exported tables there can be committed."""
    rows: list[dict[str, Any]] = []
    for name, folder in folder_destinations(config, root).items():
        if not (root / folder).is_dir():
            continue
        files = _files_under(root, folder)
        ignored = rules.ignored(files)
        exposed = [f for f in files if f not in ignored]
        if exposed:
            rows.append(
                {
                    "destination": name,
                    "path": folder,
                    "files": len(exposed),
                    "tracked": len(set(exposed) & set(tracked)),
                }
            )
    advice = (
        [
            "Add folder publishing destinations to .gitignore (for example /published/), "
            "or set visibility: public on a destination meant to be committed"
        ]
        if rows
        else []
    )
    return _frame(rows, ["destination", "path", "files", "tracked"]), advice


# The rule Framework Cloud uses for specs (password, secret, token, access_key, api_key,
# private_key, credential), plus passwd and pwd, as R's .fw_credential_key()
_CREDENTIAL_KEY = re.compile(
    r"password|passwd|pwd|secret|token|access_?key|api_?key|private_?key|credential", re.I
)
_ENV_CALL_ANY = re.compile(r"""^\s*env\(\s*(['"])([^'"]*)\1\s*(?:,\s*(['"])(.*?)\3\s*)?\)\s*$""")


def credential_key(key: Any) -> bool:
    """Whether a settings key holds a credential."""
    return _CREDENTIAL_KEY.search(str(key)) is not None


def env_call(value: Any) -> tuple[str, str | None] | None:
    """``(name, default)`` for a value of the form ``env("NAME")`` or ``env("NAME",
    "default")``, single or double quotes; the default is None when there is none.
    None when the value is something else."""
    if not isinstance(value, str):
        return None
    found = _ENV_CALL_ANY.match(value)
    if found is None:
        return None
    return found.group(2), found.group(4) if found.group(3) else None


def _literal_secret(value: Any) -> bool:
    """Whether a credential value is written out: anything but missing, empty, a mapping
    or list, a boolean, ``env("VAR")`` or ``env("VAR", "")`` (single or double quotes),
    or ``!expr Sys.getenv("VAR")``. A non-empty default (``env("VAR", "x")``) is
    written out too, as are numbers. The same rule as Framework Cloud's spec check."""
    from framework.settings import _EXPR, _SYS_GETENV

    if value is None or value == "" or isinstance(value, (dict, list, bool)):
        return False
    if not isinstance(value, str):
        return True
    call = env_call(value)
    if call is not None:
        return bool(call[1])
    if _EXPR.match(value):
        system = _SYS_GETENV.match(_EXPR.sub("", value, count=1).strip())
        return system is None or bool(system.group(4))
    return True


def _secret_keys(node: Any, path: str, inside: bool) -> list[str]:
    """Dot paths of credential keys with written-out values, under ``connections``."""
    found: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            here = f"{path}.{key}" if path else str(key)
            within = inside or str(key) == "connections"
            if within and credential_key(key) and _literal_secret(value):
                found.append(here)
            else:
                found += _secret_keys(value, here, within)
    elif isinstance(node, list):
        for index, item in enumerate(node):
            found += _secret_keys(item, f"{path}[{index}]", inside)
    return found


def _check_connection_secrets(
    root: Path, settings_file: Path, tracked: set[str]
) -> tuple[pd.DataFrame, list[str]]:
    """Passwords and other secrets written into the settings' connections, in the settings
    file and the split files under ``settings/``. Values are never shown."""
    from framework import _yaml

    files = [settings_file]
    folder = root / "settings"
    if folder.is_dir():
        files += sorted(p for p in folder.rglob("*") if p.suffix.lower() in (".yml", ".yaml"))
    rows: list[dict[str, Any]] = []
    for file in files:
        try:
            document = _yaml.read(file)
        except Exception:  # A broken file is the settings reader's to report.
            continue
        # A split file such as settings/connections.yml holds the section at its top.
        relative = file.resolve().relative_to(root.resolve()).as_posix()
        rows += [
            {"file": relative, "key": key, "tracked": relative in tracked}
            for key in _secret_keys(document, "", False)
        ]
    advice = (
        [
            "Move connection passwords and tokens to .env and refer to them with "
            'env("VARIABLE") in the settings',
            "Rotate any secret that was committed: it stays in git history",
        ]
        if rows
        else []
    )
    return _frame(rows, ["file", "key", "tracked"]), advice


def _check_identifiers(root: Path, tracked: list[str]) -> tuple[pd.DataFrame, list[str]]:
    """Tracked data files outside private folders whose header has identifier-like
    columns. Only headers are read."""
    from framework._identifiers import HEADER_TYPES, header_columns, identifier_columns

    rows: list[dict[str, str]] = []
    for name in tracked:
        if Path(name).suffix.lower() not in HEADER_TYPES or has_private_segment(Path(name).parent):
            continue
        path = root / name
        columns = header_columns(path) if path.is_file() else None
        found = identifier_columns(columns or [])
        if found:
            rows.append(
                {
                    "file": name,
                    "columns": ", ".join(column for column, _ in found),
                    "kinds": ", ".join(dict.fromkeys(kind for _, kind in found)),
                }
            )
    advice = (
        [
            "Data files tracked by git have columns that look like direct identifiers. If "
            "they hold real data about people, remove them from git (and its history) and "
            "keep them in a private folder; consider a privacy-sensitive project"
        ]
        if rows
        else []
    )
    return _frame(rows, ["file", "columns", "kinds"]), advice


def _walk_files(root: Path) -> list[Path]:
    """Every file under the root, skipping hidden folders (``.git``, ``.venv``, tool
    caches), environments and site output (``renv``, ``packrat``, ``node_modules``,
    ``_site``, ``_freeze``), and the ``*_files`` and ``*_cache`` folders Quarto and Jupyter
    write beside a document, at any depth."""
    found: list[Path] = []
    for current, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            d
            for d in dirnames
            if not d.startswith(".")
            and d not in _EXCLUDED_DIRS
            and not d.endswith(("_files", "_cache"))
        ]
        found.extend(Path(current) / name for name in filenames)
    return sorted(found)


def _frame(rows: list[dict[str, Any]], columns: list[str]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=columns)


# Auto-fix and reporting -----------------------------------------------------


def _auto_fix(root: Path, findings: dict[str, pd.DataFrame]) -> int:
    """Add the folders and files behind coverage and exposure findings to ``.gitignore``.

    A folder gets ``folder/`` and ``folder/**``. A data file that is not ignored, though
    its folder is, gets its own path, never a folder pattern.
    """
    coverage = findings["gitignore_coverage"]
    is_folder = coverage["reason"].str.contains("directory", case=False)
    folders = list(coverage["file"][is_folder])
    folders += [
        str(Path(name).parent.as_posix()) for name in findings["private_data_exposure"]["file"]
    ]
    folders = list(dict.fromkeys(f.strip("/") for f in folders if f.strip("/") not in ("", ".")))
    files = [
        f
        for f in dict.fromkeys(coverage["file"][~is_folder])
        if f.strip("/") and not any(f.startswith(f"{folder}/") for folder in folders)
    ]
    if not folders and not files:
        return 0
    path = root / ".gitignore"
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    existing = text.splitlines()
    added: list[str] = []
    if not any(line.strip() == _AUTO_FIX_HEADER for line in existing):
        added += ["", _AUTO_FIX_HEADER]
    for folder in folders:
        for entry in (f"{folder}/", f"{folder}/**"):
            if entry not in existing and entry not in added:
                added.append(entry)
    for file in files:
        entry = f"/{file.lstrip('/')}"
        if entry not in existing and entry not in added:
            added.append(entry)
    if not any(line and not line.startswith("#") for line in added):
        return 0
    if text and not text.endswith("\n"):
        text += "\n"
    path.write_text(text + "\n".join(added) + "\n", encoding="utf-8")
    parts = [f"{len(folders)} director{'y' if len(folders) == 1 else 'ies'}"] if folders else []
    parts += [f"{len(files)} file{'' if len(files) == 1 else 's'}"] if files else []
    inform(f"Added {' and '.join(parts)} to .gitignore")
    return len(folders) + len(files)


_ICONS = {"pass": "✓ PASS", "warning": "⚠ WARNING", "fail": "✗ FAIL", "skipped": "- SKIPPED"}


_SECTIONS = {
    "gitignore_coverage": "Gitignore Coverage Issues",
    "private_data_exposure": "Private Data Exposure (Git Tracked)",
    "git_history": "Git History Leaks",
    "secrets_exposure": "Tracked Secrets Files",
    "orphaned_files": "Orphaned Data Files (outside the folders in settings)",
    "published_outputs": "Publishing Folders Not Ignored by Git",
    "connection_secrets": "Secrets Written in Connection Settings",
    "identifier_columns": "Tracked Data With Identifier-Like Columns",
}

# Warnings the pre-commit hook prints even when the audit passes
HOOK_WARNINGS = ("published_outputs", "connection_secrets", "identifier_columns")


def _finding_lines(result: AuditResult, checks: tuple[str, ...]) -> list[str]:
    """The report's section for each of ``checks`` with findings."""
    lines: list[str] = []
    for check in checks:
        title = _SECTIONS[check]
        frame = result.findings[check]
        if frame.empty:
            continue
        lines += ["", f"--- {title} ---"]
        if check == "orphaned_files":
            lines += _orphan_lines(frame)
            continue
        for number, row in enumerate(frame.head(10).itertuples(), start=1):
            if check == "gitignore_coverage":
                lines.append(f"  {number}. [{str(row.severity).upper()}] {row.file}")
                lines.append(f"     Directory: {row.directory}")
                lines.append(f"     Reason: {row.reason}")
            elif check == "private_data_exposure":
                lines.append(f"  {number}. {row.file} (tracked in git, {row.directory})")
            elif check == "git_history":
                lines.append(
                    f"  {number}. {row.file} ({row.action} in commit {row.commit}, {row.date}; "
                    f"{row.reason})"
                )
            elif check == "secrets_exposure":
                lines.append(f"  {number}. {row.file} (tracked in git)")
            elif check == "identifier_columns":
                lines.append(f"  {number}. {row.file} ({row.columns})")
            elif check == "connection_secrets":
                where = "tracked in git" if row.tracked else "not tracked"
                lines.append(f"  {number}. {row.file}: {row.key} ({where})")
            elif check == "published_outputs":
                lines.append(
                    f"  {number}. {row.path}/ (destination {row.destination}: {row.files} "
                    f"file(s) not ignored, {row.tracked} tracked)"
                )
            else:
                lines.append(f"  {number}. {row.path} ({round(float(str(row.size)) / 1024, 1)} KB)")
        if len(frame) > 10:
            lines.append(f"     ... and {len(frame) - 10} more")
    return lines


def _orphan_lines(frame: pd.DataFrame) -> list[str]:
    """Orphaned files grouped by folder, the folders with most files first."""
    counts: dict[str, int] = {}
    for path in frame["path"]:
        folder = str(Path(str(path)).parent.as_posix())
        counts[folder] = counts.get(folder, 0) + 1
    ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    lines = [
        f"  {number}. {folder}/ ({count} file{'' if count == 1 else 's'})"
        for number, (folder, count) in enumerate(ordered[:10], start=1)
    ]
    if len(ordered) > 10:
        lines.append(f"     ... and {len(ordered) - 10} more folders")
    lines.append(
        "     Add a folder to settings.yml (directories or extra_directories) if it holds "
        "project data."
    )
    return lines


def hook_warnings(result: AuditResult) -> str:
    """What the pre-commit hook prints about a passing audit's warnings, or ""."""
    lines = _finding_lines(result, HOOK_WARNINGS)
    if not lines:
        return ""
    return "\n".join(["⚠ Security audit warnings (the commit goes ahead):", *lines, ""])


def _report(result: AuditResult) -> str:
    lines = ["", "=== Security Audit Summary ===", ""]
    for row in result.summary.itertuples():
        label = str(row.check).replace("_", " ")
        lines.append(f"{_ICONS[str(row.status)]}: {label} ({row.count} issues)")
    lines += _finding_lines(result, tuple(_SECTIONS))

    skipped = result.metadata.get("skipped_directories") or {}
    if skipped:
        lines += ["", "--- Skipped: Folders Outside the Project ---"]
        lines += [f"  {key}: {path}" for key, path in skipped.items()]
        lines.append("     Git can't commit them from this project, so they weren't checked.")

    if result.recommendations:
        lines += ["", "=== Recommendations ===", ""]
        lines += [f"  {n}. {text}" for n, text in enumerate(result.recommendations, start=1)]
    lines.append("")
    lines.append(
        {
            "fail": "✗ AUDIT FAILED - Critical security issues found",
            "warning": "⚠ AUDIT PASSED WITH WARNINGS - Review findings",
            "pass": "✓ AUDIT PASSED - No security issues found",
        }[result.status]
    )
    return "\n".join(lines)


def _save(root: Path, result: AuditResult) -> None:
    if not db.db_path(root).exists():
        return
    db.set_meta("last_git_security_audit", result.metadata["timestamp"], root)
    db.set_meta("last_audit_status", result.status, root)
    db.set_meta("last_audit_issues", str(int(result.summary["count"].sum())), root)


# Public API -----------------------------------------------------------------


def git_security_audit(
    settings_file: str | Path | None = None,
    *,
    check_git_history: bool = True,
    history_depth: int | str = "all",
    auto_fix: bool = False,
    verbose: bool = True,
    extensions: tuple[str, ...] | list[str] | None = None,
) -> AuditResult:
    """Check that private data and secrets stay out of git.

    Args:
        settings_file: The project's settings file. Found from the current
            folder when omitted.
        check_git_history: Scan past commits for private data files.
        history_depth: ``"all"``, ``"shallow"`` (the last 100 commits), or a
            number of commits.
        auto_fix: Add private folders and data files that are not ignored to ``.gitignore``.
            Files git already tracks stay tracked: run ``git rm --cached``.
        verbose: Print the report.
        extensions: File extensions that count as data files. Default: ``DATA_EXTENSIONS``,
            except that the orphaned-file check leaves out ``json``, ``txt``, and ``xml``
            (mostly code, cohort definitions, and manifests). Given extensions are used
            as they are by every check.

    Returns:
        An ``AuditResult``. ``result.passed`` is False when a check failed.
        The result is also recorded in ``framework.db`` when it exists.
    """
    if isinstance(history_depth, bool) or (isinstance(history_depth, int) and history_depth < 1):
        raise ValueError("history_depth must be 'all', 'shallow', or a positive number of commits")
    if isinstance(history_depth, str) and history_depth not in ("all", "shallow"):
        raise ValueError("history_depth must be 'all', 'shallow', or a positive number of commits")
    if verbose:
        show_messages()
    path = Path(settings_file).expanduser() if settings_file is not None else find_settings_file()
    if path is None or not path.is_file():
        raise FrameworkError("Settings file not found (expected settings.yml or config.yml)")
    root = path.resolve().parent
    config = settings_read(path)
    exts = tuple(e.lower().removeprefix(".") for e in (extensions or DATA_EXTENSIONS))
    orphan_exts = (
        exts
        if extensions is not None
        else tuple(e for e in exts if e not in _ORPHAN_SKIPPED_EXTENSIONS)
    )

    use_git = _git_available(root)
    if not use_git and verbose:
        inform("Git not available. Skipping git-specific checks.")
    folders = _data_directories(config, root)
    skipped = _outside_directories(config, root)
    private = _private_directories(folders)
    tracked = _tracked_files(root) if use_git else []
    rules = _IgnoreRules(root, use_git)

    findings: dict[str, pd.DataFrame] = {}
    recommendations: list[str] = []

    def record(check: str, outcome: tuple[pd.DataFrame, list[str]]) -> None:
        findings[check] = outcome[0]
        recommendations.extend(outcome[1])

    record("gitignore_coverage", _check_gitignore(root, private, rules, exts))
    record("private_data_exposure", _check_exposure(root, private, tracked, exts))
    if check_git_history and use_git:
        inputs = {k: v for k, v in folders.items() if k.startswith("inputs_")}
        record("git_history", _check_history(root, private, inputs, exts, history_depth))
    else:
        findings["git_history"] = _frame([], ["commit", "file", "action", "date", "reason"])
    record("secrets_exposure", _check_secrets(tracked))
    # Every folder the settings name counts as known, plain folders included
    known = {**folders, **{f"folder:{p}": p for p in _project_folders(config, root)}}
    record("orphaned_files", _check_orphans(root, known, orphan_exts, rules))
    record("published_outputs", _check_published(root, config, rules, tracked))
    record("connection_secrets", _check_connection_secrets(root, path, set(tracked)))
    record("identifier_columns", _check_identifiers(root, tracked))

    coverage = findings["gitignore_coverage"]
    statuses = {
        "gitignore_coverage": "pass"
        if coverage.empty
        else "fail"
        if (coverage["severity"] == "critical").any()
        else "warning",
        "private_data_exposure": "fail" if len(findings["private_data_exposure"]) else "pass",
        "git_history": "skipped"
        if not (check_git_history and use_git)
        else "fail"
        if len(findings["git_history"])
        else "pass",
        "orphaned_files": "warning" if len(findings["orphaned_files"]) else "pass",
        "secrets_exposure": "skipped"
        if not use_git
        else "fail"
        if len(findings["secrets_exposure"])
        else "pass",
        "published_outputs": "warning" if len(findings["published_outputs"]) else "pass",
        "identifier_columns": "warning" if len(findings["identifier_columns"]) else "pass",
        "connection_secrets": "pass"
        if findings["connection_secrets"].empty
        else "fail"
        if findings["connection_secrets"]["tracked"].any()
        else "warning",
    }
    summary = pd.DataFrame(
        {
            "check": list(CHECKS),
            "status": [statuses[c] for c in CHECKS],
            "count": [len(findings[c]) for c in CHECKS],
        }
    )

    exposed = len(coverage) or len(findings["private_data_exposure"])
    if auto_fix and exposed and _auto_fix(root, findings):
        recommendations.append("Run git status to review .gitignore changes")

    from framework import __version__

    result = AuditResult(
        summary=summary,
        findings=findings,
        recommendations=list(dict.fromkeys(recommendations)),
        metadata={
            "timestamp": datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S"),
            "framework_version": __version__,
            "config_directories": folders,
            "skipped_directories": skipped,
            "git_available": use_git,
            "auto_fix_applied": auto_fix,
        },
    )
    if verbose:
        print(_report(result))
    try:
        _save(root, result)
    except Exception:  # Recording the audit is optional, as in R.
        if verbose:
            inform("Note: Could not save audit to framework database")
    return result
