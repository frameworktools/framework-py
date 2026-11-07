"""Merging rules into an existing ``.gitignore``, for ``adopt()`` and ``upgrade()``.

The rules, shared with R (``R/gitignore_merge.R``) and fw:

- A rule is a line that is neither blank nor a comment, trimmed. Lines are
  compared exactly after trimming, so ``/.quarto/`` and ``.quarto/`` are
  different rules (Quarto looks for its own exact lines).
- Lines are only ever appended, in one block under a comment that names the
  tool, never removed, reordered, or rewritten.
- A rule that would ignore a file git already tracks is left out and
  reported, so the merge never hides files the project keeps in git (a
  tracked ``.Rprofile``, rendered ``*_files/`` on GitHub Pages). Negations
  (``!...``) are never left out.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from framework._private import is_private_folder_name


def gitignore_rules(text: str) -> list[str]:
    """The rules in ``.gitignore`` text: lines that are neither blank nor comments,
    trimmed, in order, once each."""
    lines = (line.strip() for line in text.splitlines())
    return list(dict.fromkeys(line for line in lines if line and not line.startswith("#")))


def _read(root: Path) -> str:
    path = root / ".gitignore"
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str] | None:
    if shutil.which("git") is None:
        return None
    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        check=False,
        encoding="utf-8",
        errors="replace",
    )


def in_work_tree(root: Path) -> bool:
    """Whether ``root`` is inside a git work tree (and git is installed)."""
    result = _git(root, "rev-parse", "--is-inside-work-tree")
    return result is not None and result.returncode == 0 and result.stdout.strip() == "true"


def tracked_matching(root: Path, rule: str) -> list[str]:
    """Files under ``root`` that git tracks and ``rule`` would ignore."""
    result = _git(root, "ls-files", "--cached", "--ignored", f"--exclude={rule}")
    if result is None or result.returncode != 0:
        return []
    return [line for line in result.stdout.splitlines() if line]


@dataclass
class MissingRules:
    """The rules to append, in order, and those left out because git tracks files they
    match (rule -> one such file)."""

    add: list[str] = field(default_factory=list)
    skipped: dict[str, str] = field(default_factory=dict)


def missing_rules(root: Path, wanted: list[str] | str, text: str | None = None) -> MissingRules:
    """The rules in ``wanted`` that the project's ``.gitignore`` (or ``text``) lacks."""
    have = set(gitignore_rules(_read(root) if text is None else text))
    wanted_text = wanted if isinstance(wanted, str) else "\n".join(wanted)
    add = [rule for rule in gitignore_rules(wanted_text) if rule not in have]
    skipped: dict[str, str] = {}
    if add and in_work_tree(root):
        for rule in add:
            if rule.startswith("!"):
                continue
            tracked = tracked_matching(root, rule)
            if tracked:
                skipped[rule] = tracked[0]
        add = [rule for rule in add if rule not in skipped]
    return MissingRules(add, skipped)


def merged_text(current: str, add: list[str], header: str) -> str:
    """``.gitignore`` text with ``add`` appended in one block under ``header``."""
    if not add:
        return current
    lines = current.splitlines()
    if lines and lines[-1].strip():
        lines.append("")
    return "\n".join([*lines, header, *add]) + "\n"


def append_rules(root: Path, add: list[str], header: str) -> list[str]:
    """Append ``add`` to the project's ``.gitignore`` under ``header``. Returns the rules
    added."""
    if add:
        (root / ".gitignore").write_text(merged_text(_read(root), add, header), encoding="utf-8")
    return add


def unignored_folders(root: Path, folders: list[str], text: str | None = None) -> list[str]:
    """The folders (paths from the root) that ``.gitignore`` doesn't ignore yet, and that
    the templates' ``**/private/**`` and ``**/*_private/**`` rules don't cover."""
    from framework.security import _matches_gitignore

    cleaned = [f.removeprefix("./").rstrip("/") for f in folders if f]
    cleaned = list(dict.fromkeys(f for f in cleaned if f))
    lines = (_read(root) if text is None else text).splitlines()
    return [
        folder
        for folder in cleaned
        if not any(is_private_folder_name(part) for part in folder.split("/"))
        and not _matches_gitignore(f"{folder}/.framework-probe", lines)
    ]


def rules_summary(rules: list[str], n: int = 6) -> str:
    """A short list of rules for messages: the first few, then "and N more"."""
    shown = ", ".join(rules[:n])
    return f"{shown}, and {len(rules) - n} more" if len(rules) > n else shown
