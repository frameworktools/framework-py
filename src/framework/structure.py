"""Project structure artifacts: a project's folder tree, with notes on its paths.

``structure()`` prints the local tree and uploads nothing. ``publish_structure()`` uploads
it to Framework Cloud as a ``framework.artifact/v1`` document with ``kind: "tree"``.

Privacy rules, shared with R, Stata, and fw:

- ``.git/`` is always left out.
- Ignored paths never publish their names. An ignored folder publishes as a stub with its
  rule and a file count. Ignored files in a folder that isn't ignored publish as one group
  per rule, with the rule and a count. Only the local preview lists them by name.
- A private folder (``private``, ``..._private``, or a settings folder marked private)
  publishes as a stub with a file count, whether or not it is ignored.
- A folder below ``depth`` publishes as a stub with a file count.
- ``exclude`` globs leave paths out entirely.
- Symbolic links are files and are never followed.
- A rule from outside the project root names its source ``global`` or ``info/exclude``.

In a git work tree git decides what is ignored. Elsewhere the root ``.gitignore`` is read
with a small matcher.
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from framework._gitignore import _git, in_work_tree
from framework._log import inform
from framework._private import is_private_folder_name, private_directories
from framework.errors import FrameworkError, ProjectNotFoundError

KIND = "tree"
SCHEMA = "framework.artifact/v1"
# File counts stop here; a count this high reads as "10,000+ files"
COUNT_CAP = 10_000
# Ignored files the local preview lists by name, per folder and rule
LISTED = 20
MAX_NODES = 5_000
MAX_NOTE = 500
# Where the annotation column starts, at the least
COLUMN = 24


# ---------------------------------------------------------------------------
# Rules


@dataclass(frozen=True)
class Rule:
    """The ignore rule that hides a path: its text, the file it is in, and its line."""

    rule: str
    source: str
    line: int | None = None

    def as_json(self) -> dict[str, Any]:
        value: dict[str, Any] = {"rule": self.rule, "source": self.source}
        if self.line is not None:
            value["line"] = self.line
        return value

    def label(self) -> str:
        where = self.source if self.line is None else f"{self.source}:{self.line}"
        return f"ignored by {self.rule} ({where})"


def _glob_regex(pattern: str) -> re.Pattern[str]:
    """A ``.gitignore`` glob (no ``!`` and no trailing ``/``) as a regex for a whole path.

    A pattern with a ``/`` at its start or middle is anchored to the root; one without
    matches a name at any level. ``*`` and ``?`` stay within one name, ``**`` crosses
    folders, and ``[...]`` is a character class.
    """
    anchored = "/" in pattern
    pattern = pattern.removeprefix("/")
    out: list[str] = []
    i, n = 0, len(pattern)
    while i < n:
        c = pattern[i]
        if c == "\\" and i + 1 < n:
            out.append(re.escape(pattern[i + 1]))
            i += 2
        elif pattern.startswith("**", i):
            start = i == 0 or pattern[i - 1] == "/"
            if start and pattern.startswith("**/", i):
                out.append("(?:.*/)?")
                i += 3
            else:
                out.append(".*")
                i += 2
        elif c == "*":
            out.append("[^/]*")
            i += 1
        elif c == "?":
            out.append("[^/]")
            i += 1
        elif c == "[":
            end = pattern.find("]", i + 2)
            if end == -1:
                out.append(re.escape(c))
                i += 1
            else:
                body = pattern[i + 1 : end]
                if body.startswith("!"):
                    body = "^" + body[1:]
                out.append("[" + body.replace("\\", "\\\\") + "]")
                i = end + 1
        else:
            out.append(re.escape(c))
            i += 1
    body = "".join(out)
    return re.compile(body if anchored else f"(?:.*/)?{body}")


@dataclass(frozen=True)
class _Pattern:
    text: str
    line: int
    negated: bool
    directory_only: bool
    regex: re.Pattern[str]

    def matches(self, path: str, is_dir: bool) -> bool:
        if self.directory_only and not is_dir:
            return False
        return self.regex.fullmatch(path) is not None


def _pattern(text: str, line: int) -> _Pattern | None:
    stripped = text.rstrip("\r\n")
    # Trailing spaces are dropped unless escaped
    while stripped.endswith(" ") and not stripped.endswith("\\ "):
        stripped = stripped[:-1]
    if not stripped or stripped.startswith("#"):
        return None
    body = stripped
    negated = body.startswith("!")
    if negated:
        body = body[1:]
    directory_only = body.endswith("/")
    body = body.rstrip("/")
    if not body:
        return None
    return _Pattern(stripped, line, negated, directory_only, _glob_regex(body))


def gitignore_patterns(text: str) -> list[_Pattern]:
    """The patterns in ``.gitignore`` text, with their line numbers."""
    patterns = (_pattern(line, number) for number, line in enumerate(text.splitlines(), 1))
    return [p for p in patterns if p is not None]


def gitignore_match(patterns: Sequence[_Pattern], path: str, is_dir: bool) -> _Pattern | None:
    """The pattern that ignores ``path`` (relative to the ``.gitignore``), or None. The
    last matching pattern wins, and a matching ``!`` pattern re-includes the path."""
    found: _Pattern | None = None
    for pattern in patterns:
        if pattern.matches(path, is_dir):
            found = pattern
    return None if found is None or found.negated else found


# ---------------------------------------------------------------------------
# Deciding what is ignored


class _Ignores:
    """Which paths (relative to the project root) are ignored, and by which rule."""

    method = "none"

    def folder(self, path: str) -> Rule | None:
        return None

    def file(self, path: str) -> Rule | None:
        return None

    def prepare(self, folder: Path, relative: str) -> None:
        """Learn the ignored paths below ``folder`` (``relative`` from the root)."""


class _GitignoreIgnores(_Ignores):
    method = "gitignore"

    def __init__(self, text: str) -> None:
        self.patterns = gitignore_patterns(text)

    def _rule(self, path: str, is_dir: bool) -> Rule | None:
        found = gitignore_match(self.patterns, path, is_dir)
        return None if found is None else Rule(found.text, ".gitignore", found.line)

    def folder(self, path: str) -> Rule | None:
        return self._rule(path, True)

    def file(self, path: str) -> Rule | None:
        return self._rule(path, False)


class _GitIgnores(_Ignores):
    method = "git"

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.folders: dict[str, Rule] = {}
        self.files: dict[str, Rule] = {}
        top = _git(root, "rev-parse", "--show-toplevel", "--absolute-git-dir")
        lines = top.stdout.splitlines() if top is not None and top.returncode == 0 else []
        self.top = Path(lines[0]).resolve() if lines else self.root
        self.git_dir = Path(lines[1]).resolve() if len(lines) > 1 else self.top / ".git"

    def _source(self, source: str) -> str:
        """A rule's file as it publishes: relative to the project root, or ``global`` or
        ``info/exclude`` for a file outside it. Never an absolute path."""
        path = Path(source)
        resolved = (path if path.is_absolute() else self.top / path).resolve()
        if resolved == self.git_dir / "info" / "exclude":
            return "info/exclude"
        try:
            return resolved.relative_to(self.root).as_posix()
        except ValueError:
            return "global"

    def check(self, cwd: Path, paths: list[str]) -> dict[str, Rule]:
        """The rule for each of ``paths`` (relative to ``cwd``) that git ignores."""
        if not paths:
            return {}
        result = subprocess.run(
            ["git", "-C", str(cwd), "check-ignore", "-v", "-z", "--stdin"],
            input="\0".join(paths) + "\0",
            capture_output=True,
            text=True,
            check=False,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode not in (0, 1):
            raise FrameworkError(f"git check-ignore failed: {result.stderr.strip()}")
        fields = result.stdout.split("\0")
        rules: dict[str, Rule] = {}
        for i in range(0, len(fields) - 3, 4):
            source, line, pattern, path = fields[i : i + 4]
            if not pattern:
                continue
            rules[path] = Rule(pattern, self._source(source), int(line) if line else None)
        return rules

    def prepare(self, folder: Path, relative: str) -> None:
        listed = _git(
            folder, "ls-files", "--others", "--ignored", "--exclude-standard", "--directory", "-z"
        )
        if listed is None or listed.returncode != 0:
            raise FrameworkError(
                f"git ls-files failed: {'' if listed is None else listed.stderr.strip()}"
            )
        paths = [p for p in listed.stdout.split("\0") if p and p != "./"]
        prefix = f"{relative}/" if relative else ""
        for path, rule in self.check(folder, paths).items():
            if path.endswith("/"):
                self.folders[prefix + path.rstrip("/")] = rule
            else:
                self.files[prefix + path] = rule

    def folder(self, path: str) -> Rule | None:
        return self.folders.get(path)

    def file(self, path: str) -> Rule | None:
        return self.files.get(path)


def _ignores(root: Path) -> _Ignores:
    if in_work_tree(root):
        return _GitIgnores(root)
    gitignore = root / ".gitignore"
    if gitignore.is_file():
        return _GitignoreIgnores(gitignore.read_text(encoding="utf-8", errors="replace"))
    return _Ignores()


def _folder_rule(ignores: _Ignores, root: Path, relative: str) -> Rule | None:
    """The rule that ignores a chosen folder itself, if any."""
    if not relative:
        return None
    if isinstance(ignores, _GitIgnores):
        parent, name = _parent(relative), _name(relative)
        found = ignores.check(root / parent if parent else root, [f"{name}/"])
        if f"{name}/" in found:
            return found[f"{name}/"]
        # A folder inside an ignored folder is ignored with it
        return _folder_rule(ignores, root, parent) if parent else None
    parts = relative.split("/")
    for depth in range(1, len(parts) + 1):
        rule = ignores.folder("/".join(parts[:depth]))
        if rule is not None:
            return rule
    return None


# ---------------------------------------------------------------------------
# Walking the folders


@dataclass
class _Entry:
    """A row of the local tree. ``path`` is relative to the tree's root."""

    path: str
    type: str  # "dir" or "file"
    ignored: Rule | None = None
    private: bool = False
    depth: bool = False
    hidden: int | None = None
    note: str | None = None

    @property
    def stub(self) -> bool:
        return self.type == "dir" and self.hidden is not None


@dataclass
class _Walk:
    root: Path
    ignores: _Ignores
    private_dirs: list[Path]
    depth: int | None
    exclude: list[re.Pattern[str]]
    hide: bool
    entries: list[_Entry] = field(default_factory=list)

    def excluded(self, path: str) -> bool:
        return any(pattern.fullmatch(path) for pattern in self.exclude)

    def private(self, folder: Path, name: str) -> bool:
        if is_private_folder_name(name):
            return True
        resolved = folder.resolve()
        return any(resolved == p or p in resolved.parents for p in self.private_dirs)

    def count(self, folder: Path, path: str) -> int:
        """Files below ``folder``, never following links, up to ``COUNT_CAP``."""
        total = 0
        stack = [(folder, path)]
        while stack:
            current, prefix = stack.pop()
            try:
                items = list(os.scandir(current))
            except OSError:
                continue
            for item in items:
                if item.name == ".git":
                    continue
                child = f"{prefix}/{item.name}" if prefix else item.name
                if self.excluded(child):
                    continue
                if item.is_dir(follow_symlinks=False):
                    stack.append((Path(item.path), child))
                else:
                    total += 1
                    if total >= COUNT_CAP:
                        return COUNT_CAP
        return total

    def add_folder(
        self, folder: Path, path: str, from_root: str, level: int, rule: Rule | None = None
    ) -> None:
        """Add a folder: a stub, or the folder and what it holds. An ignored folder is
        left out when ignored paths are hidden."""
        rule = rule or self.ignores.folder(from_root)
        private = self.private(folder, folder.name)
        if rule is not None or private:
            if rule is None or not self.hide:
                hidden = self.count(folder, path)
                self.entries.append(_Entry(path, "dir", rule, private, hidden=hidden))
        elif self.depth is not None and level >= self.depth:
            self.entries.append(_Entry(path, "dir", depth=True, hidden=self.count(folder, path)))
        else:
            self.entries.append(_Entry(path, "dir"))
            self.walk(folder, path, from_root, level)

    def walk(self, folder: Path, path: str, from_root: str, level: int) -> None:
        """List what is in ``folder``, which is at ``level`` (0 for a chosen folder)."""
        try:
            items = sorted(os.scandir(folder), key=lambda item: item.name)
        except OSError as error:
            raise FrameworkError(f"Could not read {folder}: {error}") from error
        for item in items:
            if item.name == ".git":
                continue
            child = f"{path}/{item.name}" if path else item.name
            child_from_root = f"{from_root}/{item.name}" if from_root else item.name
            if self.excluded(child):
                continue
            if item.is_dir(follow_symlinks=False):
                self.add_folder(Path(item.path), child, child_from_root, level + 1)
            else:
                rule = self.ignores.file(child_from_root)
                if rule is not None and self.hide:
                    continue
                self.entries.append(_Entry(child, "file", rule))


# ---------------------------------------------------------------------------
# The tree


def _count_text(n: int) -> str:
    if n >= COUNT_CAP:
        return f"{COUNT_CAP:,}+ files"
    return "1 file" if n == 1 else f"{n:,} files"


def _parent(path: str) -> str:
    return path.rsplit("/", 1)[0] if "/" in path else ""


def _name(path: str) -> str:
    return path.rsplit("/", 1)[-1]


@dataclass
class _Row:
    """A line of the printed tree."""

    name: str
    path: str
    folder: bool
    annotation: str
    children: bool  # a listed folder, whose rows come below it


@dataclass(frozen=True)
class Structure:
    """A project's folder tree, as ``structure()`` builds it.

    ``str()`` and ``repr()`` give the local tree, which lists ignored files by name.
    ``published`` is the tree as ``publish_structure()`` prints it, and ``document`` the
    JSON it uploads.

    Attributes:
        root: The name at the top: the folder's name, or the project's for several
            folders.
        base: The folder relative to the project root, ``""`` for the root.
        method: How ignored paths were found: ``git``, ``gitignore``, or ``none``.
        entries: The rows of the local tree, ignored files included.
        open: Folders that start open in an embed.
        highlight: Paths drawn as highlighted.
    """

    root: str
    base: str
    method: str
    entries: tuple[_Entry, ...]
    open: tuple[str, ...] = ()
    highlight: tuple[str, ...] = ()

    @property
    def nodes(self) -> list[dict[str, Any]]:
        """The nodes as they publish: ignored files become one group per folder and rule."""
        nodes: list[dict[str, Any]] = []
        groups: dict[tuple[str, Rule], int] = {}
        for entry in self.entries:
            if entry.type == "file" and entry.ignored is not None:
                key = (_parent(entry.path), entry.ignored)
                groups[key] = groups.get(key, 0) + 1
                continue
            node: dict[str, Any] = {"path": entry.path, "type": entry.type}
            if entry.note is not None:
                node["note"] = entry.note
            if entry.ignored is not None:
                node["ignored"] = entry.ignored.as_json()
            if entry.private:
                node["private"] = True
            if entry.depth:
                node["depth"] = True
            if entry.hidden is not None:
                node["hidden"] = entry.hidden
            nodes.append(node)
        for (folder, rule), hidden in groups.items():
            nodes.append(
                {"path": folder, "type": "ignored", "ignored": rule.as_json(), "hidden": hidden}
            )
        return nodes

    @property
    def document(self) -> dict[str, Any]:
        """The ``kind: "tree"`` document ``publish_structure()`` uploads."""
        document: dict[str, Any] = {
            "schema": SCHEMA,
            "kind": KIND,
            "source": {"language": "python"},
            "root": self.root,
            "base": self.base,
            "method": self.method,
            "nodes": self.nodes,
        }
        if self.open:
            document["open"] = list(self.open)
        if self.highlight:
            document["highlight"] = list(self.highlight)
        return document

    def _rows(self, local: bool) -> dict[str, list[_Row]]:
        """Rows by parent folder, sorted: folders, files, then ignored files or groups,
        each by name, ignoring case."""
        folders: dict[str, list[_Row]] = {}
        files: dict[str, list[_Row]] = {}
        ignored: dict[str, dict[Rule, list[str]]] = {}
        known = {entry.path for entry in self.entries if entry.type == "dir"}
        for entry in self.entries:
            parent = _parent(entry.path)
            if entry.type == "file" and entry.ignored is not None:
                ignored.setdefault(parent, {}).setdefault(entry.ignored, []).append(entry.path)
                continue
            parts: list[str] = []
            if entry.private:
                parts.append("private")
            if entry.ignored is not None:
                parts.append(entry.ignored.label())
            if entry.hidden is not None:
                parts.append(_count_text(entry.hidden))
            if entry.note is not None:
                parts.append(entry.note)
            row = _Row(
                _name(entry.path),
                entry.path,
                entry.type == "dir",
                " · ".join(parts),
                entry.type == "dir" and not entry.stub,
            )
            (folders if row.folder else files).setdefault(parent, []).append(row)
        # Folders above several chosen folders
        for path in sorted(known):
            parent = _parent(path)
            while parent and parent not in known:
                known.add(parent)
                folders.setdefault(_parent(parent), []).append(
                    _Row(_name(parent), parent, True, "", True)
                )
                parent = _parent(parent)
        rows: dict[str, list[_Row]] = {}
        for parent in set(folders) | set(files) | set(ignored):
            listed = sorted(folders.get(parent, []), key=lambda r: (r.name.casefold(), r.name))
            listed += sorted(files.get(parent, []), key=lambda r: (r.name.casefold(), r.name))
            groups = ignored.get(parent, {})
            for rule in sorted(groups, key=lambda r: (r.rule.casefold(), r.rule)):
                paths = sorted(groups[rule], key=lambda p: (_name(p).casefold(), p))
                if not local:
                    count = f"{rule.label()} · {_count_text(len(paths))}"
                    listed.append(_Row(rule.rule.rstrip("/"), parent, False, count, False))
                    continue
                for path in paths[:LISTED]:
                    listed.append(_Row(_name(path), path, False, rule.label(), False))
                if len(paths) > LISTED:
                    more = f"… {len(paths) - LISTED:,} more"
                    listed.append(_Row(more, parent, False, rule.label(), False))
            rows[parent] = listed
        return rows

    def _text(self, local: bool) -> str:
        rows = self._rows(local)
        lines: list[tuple[str, str]] = []

        def draw(parent: str, prefix: str) -> None:
            children = rows.get(parent, [])
            for i, row in enumerate(children):
                last = i == len(children) - 1
                name = f"{row.name}/" if row.folder else row.name
                lines.append((f"{prefix}{'└── ' if last else '├── '}{name}", row.annotation))
                if row.children:
                    draw(row.path, prefix + ("    " if last else "│   "))

        draw("", "")
        widths = [len(label) for label, annotation in lines if annotation]
        column = max([COLUMN, *(w + 2 for w in widths)])
        out = [f"{self.root}/"]
        for label, annotation in lines:
            out.append(f"{label.ljust(column)}{annotation}" if annotation else label)
        return "\n".join(out)

    @property
    def published(self) -> str:
        """The tree as it publishes, with groups in place of ignored file names."""
        return self._text(local=False)

    def __str__(self) -> str:
        return self._text(local=True)

    def __repr__(self) -> str:
        return self._text(local=True)


# ---------------------------------------------------------------------------
# Arguments


def _strings(value: str | Path | Iterable[str | Path] | None, what: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (str, Path)):
        return [str(value)]
    if isinstance(value, Mapping):
        raise FrameworkError(f"{what} must be a path or a list of paths.")
    items = list(value)
    if not all(isinstance(item, (str, Path)) for item in items):
        raise FrameworkError(f"{what} must be a path or a list of paths.")
    return [str(item) for item in items]


def _clean(path: str) -> str:
    """A path from the arguments as the tree names it."""
    cleaned = path.replace("\\", "/").strip()
    while cleaned.startswith("./"):
        cleaned = cleaned[2:]
    return cleaned.rstrip("/")


def _root() -> Path:
    from framework.project import project_root

    try:
        return project_root().resolve()
    except ProjectNotFoundError:
        return Path.cwd().resolve()


def _folders(root: Path, path: Any) -> list[tuple[Path, str]]:
    """The chosen folders, absolute and relative to the project root."""
    chosen = _strings(path, "path") or ["."]
    folders: list[tuple[Path, str]] = []
    for item in chosen:
        candidate = Path(item).expanduser()
        absolute = (candidate if candidate.is_absolute() else root / candidate).resolve()
        try:
            relative = absolute.relative_to(root).as_posix()
        except ValueError:
            raise FrameworkError(f"{item} is outside the project root, {root}.") from None
        relative = "" if relative == "." else relative
        if not absolute.is_dir():
            raise FrameworkError(f"{item} is not a folder in {root}.")
        if ".git" in relative.split("/"):
            raise FrameworkError(f"{item} is inside .git, which never publishes.")
        if (absolute, relative) not in folders:
            folders.append((absolute, relative))
    return folders


def _check_paths(
    entries: list[_Entry],
    notes: Mapping[str, str] | None,
    open_: Any,
    highlight: Any,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Attach notes, and check that notes, ``open``, and ``highlight`` name paths in the
    tree as it publishes."""
    shown = {e.path: e for e in entries if not (e.type == "file" and e.ignored is not None)}
    if notes is not None:
        if not isinstance(notes, Mapping):
            raise FrameworkError('notes must be a dict of paths to text, such as {"R": "Code"}.')
        for key, text in notes.items():
            if not isinstance(key, (str, Path)) or not isinstance(text, str):
                raise FrameworkError("notes must be a dict of paths to text.")
            path = _clean(str(key))
            if path not in shown:
                raise FrameworkError(f'notes names "{key}", which is not in the tree.')
            if len(text) > MAX_NOTE:
                raise FrameworkError(f'The note on "{key}" is over {MAX_NOTE} characters.')
            shown[path].note = text.strip()
    opened: list[str] = []
    for item in _strings(open_, "open"):
        path = _clean(item)
        if path not in shown:
            raise FrameworkError(f'open names "{item}", which is not in the tree.')
        if shown[path].type != "dir":
            raise FrameworkError(f'open names "{item}", which is not a folder.')
        opened.append(path)
    highlighted: list[str] = []
    for item in _strings(highlight, "highlight"):
        path = _clean(item)
        if path not in shown:
            raise FrameworkError(f'highlight names "{item}", which is not in the tree.')
        highlighted.append(path)
    return tuple(dict.fromkeys(opened)), tuple(dict.fromkeys(highlighted))


def _build(
    path: Any,
    depth: int | None,
    exclude: Any,
    notes: Mapping[str, str] | None,
    open_: Any,
    highlight: Any,
    ignored: str,
) -> tuple[Structure, Path]:
    if ignored not in ("rules", "hide"):
        raise FrameworkError('ignored must be "rules" or "hide".')
    if depth is not None and (isinstance(depth, bool) or not isinstance(depth, int) or depth < 1):
        raise FrameworkError("depth must be a whole number of levels, 1 or more.")
    patterns = []
    for glob in _strings(exclude, "exclude"):
        cleaned = glob.strip().removeprefix("./").rstrip("/")
        if cleaned:
            patterns.append(_glob_regex(cleaned))
    root = _root()
    folders = _folders(root, path)
    ignores = _ignores(root)
    walk = _Walk(root, ignores, private_directories(root), depth, patterns, ignored == "hide")
    several = len(folders) > 1
    for folder, relative in folders:
        parts = relative.split("/")[:-1] if relative else []
        above = ["/".join(parts[: i + 1]) for i in range(len(parts))]
        if any(walk.private(root / folder_above, _name(folder_above)) for folder_above in above):
            raise FrameworkError(f"{relative} is inside a private folder, which never publishes.")
        rule = _folder_rule(ignores, root, relative)
        if several:
            if rule is None:
                ignores.prepare(folder, relative)
            walk.add_folder(folder, relative, relative, 0, rule)
            continue
        if rule is not None:
            raise FrameworkError(
                f"{relative} is {rule.label()}, so its names never publish. "
                "Choose the folder above it to show it as a stub."
            )
        if relative and walk.private(folder, folder.name):
            raise FrameworkError(
                f"{relative} is a private folder, so its names never publish. "
                "Choose the folder above it to show it as a stub."
            )
        ignores.prepare(folder, relative)
        walk.walk(folder, "", relative, 0)
    entries = _dedupe(walk.entries)
    opened, highlighted = _check_paths(entries, notes, open_, highlight)
    if several:
        name, base = root.name, ""
    else:
        folder, base = folders[0]
        name = folder.name
    structure = Structure(name, base, ignores.method, tuple(entries), opened, highlighted)
    return structure, root


def _dedupe(entries: list[_Entry]) -> list[_Entry]:
    seen: dict[tuple[str, str], _Entry] = {}
    for entry in entries:
        seen.setdefault((entry.path, entry.type), entry)
    return list(seen.values())


# ---------------------------------------------------------------------------
# Public functions


def structure(
    path: str | Path | Sequence[str | Path] | None = None,
    *,
    depth: int | None = None,
    exclude: str | Sequence[str] | None = None,
    notes: Mapping[str, str] | None = None,
    open: str | Sequence[str] | None = None,
    highlight: str | Sequence[str] | None = None,
    ignored: str = "rules",
) -> Structure:
    """The project's folder tree, to read before publishing it. Nothing is uploaded.

    ::

        fw.structure()
        fw.structure("analysis", depth=2, notes={"models": "One script per model"})

    The tree lists ignored files by name with the rule that ignores them, up to 20 per
    folder and rule, so it shows what the ``.gitignore`` does. Ignored and private folders
    show as stubs with a file count, as they publish. ``publish_structure()`` takes the
    same arguments and publishes the tree without the ignored names.

    Args:
        path: A folder, or a list of folders, relative to the project root. Default: the
            project root. Outside a Framework project, the root is the current folder.
        depth: Levels to list. Folders below it show as stubs with a file count. Default:
            no limit.
        exclude: Globs, as ``.gitignore`` writes them, for paths to leave out entirely.
        notes: Notes by path, such as ``{"R/": "Reusable functions"}``.
        open: Folders that start open in an embed.
        highlight: Paths drawn as highlighted in an embed.
        ignored: ``"rules"`` shows ignored paths with their rules; ``"hide"`` leaves them
            out.

    Returns:
        A ``Structure``. Printing it shows the tree. Its ``published`` is the tree as
        ``publish_structure()`` publishes it, and ``document`` the JSON it uploads.

    Raises:
        FrameworkError: An argument is not valid, or ``notes``, ``open``, or ``highlight``
            names a path that is not in the tree.
    """
    built, _ = _build(path, depth, exclude, notes, open, highlight, ignored)
    return built


def publish_structure(
    path: str | Path | Sequence[str | Path] | None = None,
    *,
    depth: int | None = None,
    exclude: str | Sequence[str] | None = None,
    notes: Mapping[str, str] | None = None,
    open: str | Sequence[str] | None = None,
    highlight: str | Sequence[str] | None = None,
    ignored: str = "rules",
    slug: str | None = None,
    title: str | None = None,
    description: str | None = None,
    visibility: str | None = None,
    password: str | None = None,
    collection: str | int | None = None,
    share_unlisted: bool = False,
    to: str | None = None,
    include_private: bool = False,
    display: Mapping[str, bool] | None = None,
) -> str | list[str]:
    """Publish the project's folder tree to Framework Cloud, with notes on its paths.

    ::

        fw.publish_structure(notes={"R/": "Reusable functions", "README.md": "Start here"},
                             highlight="README.md")

    Framework prints the tree as it publishes, then uploads it. Ignored names never
    publish: an ignored folder is a stub with its rule and a file count, and ignored files
    are one row per folder and rule, with a count. A private folder (``private``,
    ``..._private``, or a settings folder marked private) is a stub with a file count.
    ``.git/`` is left out. ``structure()`` shows the tree with the ignored names, and
    uploads nothing.

    Args:
        path: A folder, or a list of folders, relative to the project root. Several
            folders publish as one tree named for the project. Default: the project root.
        depth: Levels to list. Folders below it publish as stubs with a file count.
        exclude: Globs, as ``.gitignore`` writes them, for paths to leave out entirely.
        notes: Notes by path, up to 500 characters each, such as
            ``{"R/": "Reusable functions"}``. A path not in the tree is an error.
        open: Folders that start open in an embed. Default: the top level.
        highlight: Paths drawn as highlighted.
        ignored: ``"rules"`` publishes ignored paths as stubs and groups with their rules;
            ``"hide"`` leaves them out. Private stubs show either way, unless the private
            folder is ignored too.
        slug: Artifact slug. Default: ``structure``, or ``structure-<folder>`` for a
            subfolder.
        title: Artifact title. Default: ``<root> structure``.
        description: Artifact description.
        visibility: "public", "unlisted", or "private". Default: a new artifact is
            unlisted and an existing one keeps its setting.
        password: Password required to view the tree.
        collection: Project collection ID or slug.
        share_unlisted: Allow collection readers to see an unlisted tree.
        to: A configured destination name, or "cloud".
        include_private: Publish without asking about private folders.
        display: What embeds of the tree show, on Framework Cloud: ``title``,
            ``description``, ``notes``, and ``ignored``. ``description`` starts off and
            the rest start on. True shows an item and False hides it; settings left out
            keep their value.

    Returns:
        The artifact's URL. On Framework Cloud it is a ``Published`` string with
        ``embed_link``, the link to share and embed, and ``artifact_url``, the artifact's
        page for its owner.
    """
    import json

    from framework.cloud import display_settings
    from framework.publish_bundle import artifact_slug
    from framework.storage import publish

    display_settings(display)
    built, _ = _build(path, depth, exclude, notes, open, highlight, ignored)
    document = built.document
    if len(document["nodes"]) > MAX_NODES:
        raise FrameworkError(
            f"The tree has {len(document['nodes']):,} entries; Framework Cloud takes up to "
            f"{MAX_NODES:,}. Narrow it with depth or exclude."
        )
    slug = slug or (f"structure-{artifact_slug(built.base)}" if built.base else "structure")
    title = title or f"{built.root} structure"
    inform(built.published)
    with tempfile.TemporaryDirectory(prefix="fw_structure_") as temp:
        file = Path(temp) / f"{slug}.json"
        file.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
        return publish(
            file,
            kind=KIND,
            slug=slug,
            title=title,
            description=description,
            visibility=visibility,
            password=password,
            collection=collection,
            share_unlisted=share_unlisted,
            to=to,
            include_private=include_private,
            display=display,
        )
