"""``git_security_audit()``: checks that private data stays out of git.

The checks match R's ``git_security_audit()``:

- gitignore_coverage: private folders and the data files in them are ignored.
- private_data_exposure: git tracks no file in a private folder.
- git_history: no commit added a private data file.
- orphaned_files: data files sit inside the configured folders. Files git
  ignores are not counted.
- secrets_exposure: git tracks no ``.env`` file.

Where git is available, ``git check-ignore`` decides what is ignored. Without
git, a port of R's ``.gitignore`` matcher does.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
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
)

_EXCLUDED_DIRS = (
    ".git", ".Rproj.user", "renv", "packrat", ".framework_cache", ".quarto", "_cache",
    "_files", ".venv", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    # Framework's and editors' own configuration, which is JSON but not data.
    ".framework", ".claude", ".github", ".vscode", ".idea", ".positron", "node_modules",
)  # fmt: skip
_EXCLUDED_FILES = {"framework.db", ".framework_renv_enabled", ".framework_scaffolded", ".initiated"}
_SECRETS_FILE = re.compile(r"^\.env(\..+)?$")
_SECRETS_EXAMPLE = re.compile(r"\.(example|template|sample)$")
_HISTORY_ACTIONS = {"A": "added", "M": "modified", "D": "deleted"}
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
    """The folders the audit watches: the settings' data folders, then R's fallbacks."""
    folders: dict[str, str] = {}
    directories = config.get("directories")
    for key, value in (directories if isinstance(directories, dict) else {}).items():
        if not isinstance(value, str) or not value:
            continue
        if re.match(r"^(inputs|outputs)_", key) or key in ("cache", "scratch", "logs"):
            folders[key] = value.removeprefix("./").rstrip("/")

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
    for key, folder in private.items():
        if not (root / folder).is_dir():
            continue
        if not rules.folder_ignored(folder):
            rows.append(
                {
                    "file": folder,
                    "directory": key,
                    "severity": "critical",
                    "reason": "Private data directory not in .gitignore",
                }
            )
        data_files = [f for f in _files_under(root, folder) if _is_data_file(f, extensions)]
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
    rows = [
        {"file": name, "directory": key, "git_status": "tracked"}
        for key, folder in private.items()
        if (root / folder).is_dir()
        for name in tracked
        if _inside(name, folder)
    ]
    # Also data files in any folder named private, confidential, or sensitive,
    # such as inputs/private itself, which no setting names.
    reported = {row["file"] for row in rows}
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


def _check_history(
    root: Path,
    private: dict[str, str],
    extensions: tuple[str, ...],
    depth: int | str,
) -> tuple[pd.DataFrame, list[str]]:
    args = ["log", "--all", "--pretty=format:%H|%ad|%s", "--date=iso", "--name-status"]
    if isinstance(depth, int):
        args.append(f"-{depth}")
    elif depth == "shallow":
        args.append("-100")
    result = _git(root, *args)
    rows: list[dict[str, str]] = []
    commit = date = None
    for line in result.stdout.splitlines() if result.returncode == 0 else []:
        if re.match(r"^[0-9a-f]{40}\|", line):
            sha, date, *_ = line.split("|")
            commit = sha[:8]
            continue
        parts = line.split("\t")
        if commit is None or len(parts) < 2 or parts[0] not in _HISTORY_ACTIONS:
            continue
        name = parts[1]
        if not _is_data_file(name, extensions):
            continue
        in_private = any(_inside(name, folder) for folder in private.values())
        if in_private or re.search("private|secret|confidential", name, re.IGNORECASE):
            rows.append(
                {
                    "commit": commit,
                    "file": name,
                    "action": _HISTORY_ACTIONS[parts[0]],
                    "date": date or "",
                }
            )
    advice = (
        [
            "CRITICAL: Private data files found in git history!",
            "Consider using git-filter-repo or BFG Repo-Cleaner to remove sensitive data",
            "Consult: https://docs.github.com/en/authentication/keeping-your-account-and-data-"
            "secure/removing-sensitive-data-from-a-repository",
        ]
        if rows
        else []
    )
    return _frame(rows, ["commit", "file", "action", "date"]), advice


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


def _walk_files(root: Path) -> list[Path]:
    """Every file under the root, skipping excluded top-level folders, and at any depth
    the ``*_files`` and ``*_cache`` folders Quarto and Jupyter write beside a document."""
    found: list[Path] = []
    for current, dirnames, filenames in os.walk(root):
        if Path(current) == root:
            dirnames[:] = [d for d in dirnames if d not in _EXCLUDED_DIRS]
        dirnames[:] = [
            d
            for d in dirnames
            if d not in (".git", "__pycache__") and not d.endswith(("_files", "_cache"))
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


def _report(result: AuditResult) -> str:
    lines = ["", "=== Security Audit Summary ===", ""]
    for row in result.summary.itertuples():
        label = str(row.check).replace("_", " ")
        lines.append(f"{_ICONS[str(row.status)]}: {label} ({row.count} issues)")

    sections = {
        "gitignore_coverage": "Gitignore Coverage Issues",
        "private_data_exposure": "Private Data Exposure (Git Tracked)",
        "git_history": "Git History Leaks",
        "secrets_exposure": "Tracked Secrets Files",
        "orphaned_files": "Orphaned Data Files",
    }
    for check, title in sections.items():
        frame = result.findings[check]
        if frame.empty:
            continue
        lines += ["", f"--- {title} ---"]
        for number, row in enumerate(frame.head(10).itertuples(), start=1):
            if check == "gitignore_coverage":
                lines.append(f"  {number}. [{str(row.severity).upper()}] {row.file}")
                lines.append(f"     Directory: {row.directory}")
                lines.append(f"     Reason: {row.reason}")
            elif check == "private_data_exposure":
                lines.append(f"  {number}. {row.file} (tracked in git, {row.directory})")
            elif check == "git_history":
                lines.append(
                    f"  {number}. {row.file} ({row.action} in commit {row.commit}, {row.date})"
                )
            elif check == "secrets_exposure":
                lines.append(f"  {number}. {row.file} (tracked in git)")
            else:
                lines.append(f"  {number}. {row.path} ({round(float(str(row.size)) / 1024, 1)} KB)")
        if len(frame) > 10:
            lines.append(f"     ... and {len(frame) - 10} more")

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
    extensions: tuple[str, ...] | list[str] = DATA_EXTENSIONS,
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
        extensions: File extensions that count as data files.

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
    exts = tuple(e.lower().removeprefix(".") for e in extensions)

    use_git = _git_available(root)
    if not use_git and verbose:
        inform("Git not available. Skipping git-specific checks.")
    folders = _data_directories(config, root)
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
        record("git_history", _check_history(root, private, exts, history_depth))
    else:
        findings["git_history"] = _frame([], ["commit", "file", "action", "date"])
    record("secrets_exposure", _check_secrets(tracked))
    record("orphaned_files", _check_orphans(root, folders, exts, rules))

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
