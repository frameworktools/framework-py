"""Merging rules into an existing ``.gitignore``, for ``adopt()`` and ``upgrade()``.

The rules, shared with R (``R/gitignore_merge.R``) and fw:

- A rule is a line that is neither blank nor a comment, trimmed. Lines are
  compared exactly after trimming, so ``/.quarto/`` and ``.quarto/`` are
  different rules (Quarto looks for its own exact lines).
- Lines are only ever appended, in one block under a comment that names the
  tool, never removed, reordered, or rewritten.
- A rule that would ignore a file git already tracks is left out and
  reported, so the merge never hides files the project keeps in git (a
  tracked ``.Rprofile``, rendered ``*_files/`` on GitHub Pages).
- Negations (``!...``) are left out and reported when the ``.gitignore``
  already has rules: appended after the project's own rules they would win
  over them, and could make files in folders the project ignores committable
  (fore's ``inputs/**`` with ``!inputs/*/``, where the template's
  ``!/inputs/**/README.md`` re-included ``inputs/raw/README.md``). Into a new
  or empty ``.gitignore`` the template goes whole, negations included.
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
    """The rules to append, in order; those left out because git tracks files they match
    (rule -> one such file); and the negations left out because the ``.gitignore``
    already has rules."""

    add: list[str] = field(default_factory=list)
    skipped: dict[str, str] = field(default_factory=dict)
    negations: list[str] = field(default_factory=list)


def missing_rules(root: Path, wanted: list[str] | str, text: str | None = None) -> MissingRules:
    """The rules in ``wanted`` that the project's ``.gitignore`` (or ``text``) lacks."""
    have = set(gitignore_rules(_read(root) if text is None else text))
    wanted_text = wanted if isinstance(wanted, str) else "\n".join(wanted)
    add = [rule for rule in gitignore_rules(wanted_text) if rule not in have]
    negations = [rule for rule in add if rule.startswith("!")] if have else []
    add = [rule for rule in add if rule not in negations]
    skipped: dict[str, str] = {}
    if add and in_work_tree(root):
        for rule in add:
            if rule.startswith("!"):
                continue
            tracked = tracked_matching(root, rule)
            if tracked:
                skipped[rule] = tracked[0]
        add = [rule for rule in add if rule not in skipped]
    return MissingRules(add, skipped, negations)


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
    the templates' ``**/private/**`` and ``**/*_private/**`` rules don't cover. In a git
    work tree git decides, so every rule counts as git reads it (fore's ``inputs/**``
    with ``!inputs/*/`` ignores what is inside ``inputs/raw``); elsewhere, or when
    ``text`` isn't the ``.gitignore`` on disk, R's matcher reads ``text``."""
    from framework.security import _matches_gitignore

    cleaned = [f.removeprefix("./").rstrip("/") for f in folders if f]
    cleaned = list(dict.fromkeys(f for f in cleaned if f))
    candidates = [
        folder
        for folder in cleaned
        if not any(is_private_folder_name(part) for part in folder.split("/"))
    ]
    if not candidates:
        return []
    on_disk = _read(root)
    if (text is None or text == on_disk) and in_work_tree(root):
        probes = [f"{folder}/.framework-probe" for folder in candidates]
        result = subprocess.run(
            ["git", "-C", str(root), "check-ignore", "--stdin", "-z"],
            input="\0".join(probes),
            capture_output=True,
            text=True,
            check=False,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode in (0, 1):
            ignored = set(result.stdout.split("\0"))
            return [f for f, probe in zip(candidates, probes, strict=True) if probe not in ignored]
    lines = (on_disk if text is None else text).splitlines()
    return [f for f in candidates if not _matches_gitignore(f"{f}/.framework-probe", lines)]


def rules_summary(rules: list[str], n: int = 6) -> str:
    """A short list of rules for messages: the first few, then "and N more"."""
    shown = ", ".join(rules[:n])
    return f"{shown}, and {len(rules) - n} more" if len(rules) > n else shown
