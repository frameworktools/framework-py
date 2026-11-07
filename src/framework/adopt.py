"""Adopt an existing project folder, or import its layout as a structure.

``adopt()`` scans the folder's subfolders, proposes a Framework directory key
for each, and after your review writes ``settings.yml`` and
``.framework/spec.json`` for the folders that are already there. It moves and
creates nothing else. ``structure_import()`` sends the same layout to
Framework Cloud as a private structure.

The scanner looks at folder names only, never at files or their contents.
The mapping rules are shared with R and ``fw`` (``inst/adopt/rules.json`` in
the R package): the first rule whose path matches a folder wins, and
folders no rule matches become ``extra_directories``.
"""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from functools import cache
from importlib import resources
from pathlib import Path
from typing import Any

from framework._log import inform, show_messages
from framework.errors import FrameworkError

_KEY = re.compile(r"^[a-z][a-z0-9_]*$")
EXTRA = "extra"


@dataclass
class AdoptPlan:
    """The proposed layout: folder -> role, in byte order of path.

    A role is a Framework directory key, ``"extra"`` for a folder of its own,
    or None for a folder that is left out. ``render_dirs`` maps render keys
    to folders that hold rendered notebooks. ``private`` lists every scanned
    folder whose path contains "private". ``type`` is the project type, given
    or inferred. ``reserved`` holds the keys of the rules that applied, which
    extra directories don't take.
    """

    roles: dict[str, str | None] = field(default_factory=dict)
    private: list[str] = field(default_factory=list)
    render_dirs: dict[str, str] = field(default_factory=dict)
    type: str = "bare"
    reserved: list[str] = field(default_factory=list)
    sensitive: bool = False

    @property
    def directories(self) -> dict[str, str]:
        return {role: path for path, role in self.roles.items() if role not in (None, EXTRA)}

    @property
    def extra_directories(self) -> dict[str, dict[str, str]]:
        extra: dict[str, dict[str, str]] = {}
        taken = set(self.directories) | set(self.reserved)
        for path, role in self.roles.items():
            if role == EXTRA:
                key = _extra_key(path, taken)
                taken.add(key)
                extra[key] = {"label": path.rsplit("/", 1)[-1], "path": path}
        return extra

    @property
    def empty(self) -> bool:
        return not self.directories and not self.extra_directories and not self.render_dirs


@cache
def adopt_rules() -> dict[str, Any]:
    """The shared mapping rules, as synced from the R package."""
    path = resources.files("framework").joinpath("_assets/adopt/rules.json")
    rules: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return rules


def _skipped(name: str, skip: list[str]) -> bool:
    """Whether a folder name is in the rules' skip list, ignoring case. An entry with
    ``*`` matches any characters within the name (``*_files``)."""
    lowered = name.lower()
    for entry in skip:
        entry = entry.lower()
        if "*" in entry:
            pattern = ".*".join(re.escape(part) for part in entry.split("*"))
            if re.fullmatch(pattern, lowered):
                return True
        elif lowered == entry:
            return True
    return False


def _kept(path: str, rules: Mapping[str, Any]) -> bool:
    skip = list(rules.get("skip") or [])
    parts = path.split("/")
    return len(parts) <= int(rules.get("max_depth") or 3) and not any(
        part.startswith(".") or _skipped(part, skip) for part in parts
    )


def scan_folders(root: Path, rules: Mapping[str, Any] | None = None) -> list[str]:
    """Subfolders of ``root``, as relative POSIX paths in byte order.

    Hidden folders, the rules' ``skip`` folders (version control,
    environments, caches, Framework's ``settings/``, Quarto's render
    folders), and everything under them are left out, and so are folders
    more than ``max_depth`` levels deep. Symbolic links are not followed.
    Only folder names are read.
    """
    rules = rules or adopt_rules()
    found: list[str] = []

    def walk(folder: Path) -> None:
        for entry in folder.iterdir():
            if entry.is_dir() and not entry.is_symlink():
                relative = entry.relative_to(root).as_posix()
                if _kept(relative, rules):
                    found.append(relative)
                    walk(entry)

    walk(root)
    return sorted(found, key=lambda p: p.encode("utf-8"))


def adopt_plan(
    folders: list[str],
    language: str = "python",
    rules: Mapping[str, Any] | None = None,
    type: str | None = None,
) -> AdoptPlan:
    """Propose a role for each folder with the shared rules.

    The algorithm is the one in the R package's ``inst/conformance/README.md``
    (adopt/): the type comes first (a course when enough top-level folders
    are course signals, unless ``type`` is given). Rules for the language and
    type apply in order, each whose key is still free giving its key to the
    first folder that equals one of its patterns, ignoring case, and is
    neither assigned nor inside an assigned folder. The ``render_dirs`` rules
    follow the same way. Folders inside an assigned folder are covered by it.
    Each other folder with no assigned folder inside it gets a role of its
    own (``"extra"``), unless its parent does too. Without ``type``, the
    type is course, else project_sensitive when a private or public key
    matched, else project when any key matched, else bare.
    """
    rules = rules or adopt_rules()
    kept = sorted(
        {f.strip("/") for f in folders if f.strip("/") and _kept(f.strip("/"), rules)},
        key=lambda p: p.encode("utf-8"),
    )
    types = rules.get("types") or {}
    course = types.get("course") or {}
    top = {f.lower() for f in kept if "/" not in f}
    signals = sum(1 for signal in course.get("signals") or [] if signal.lower() in top)
    is_course = signals >= int(course.get("min") or 2)
    rule_type = type or ("course" if is_course else "project")
    applicable = [
        rule
        for rule in rules.get("rules") or []
        if (not rule.get("languages") or language in rule["languages"])
        and (not rule.get("types") or rule_type in rule["types"])
    ]

    def free(folder: str, assigned: Mapping[str, str]) -> bool:
        taken = set(assigned.values())
        return folder not in taken and not any(_inside(folder, a) for a in taken)

    def assign(
        rule_list: list[dict[str, Any]], assigned: dict[str, str], others: dict[str, str]
    ) -> None:
        for rule in rule_list:
            if rule["key"] in assigned:
                continue
            for pattern in rule.get("patterns") or []:
                hit = next(
                    (
                        f
                        for f in kept
                        if f.lower() == pattern.lower() and free(f, {**others, **assigned})
                    ),
                    None,
                )
                if hit is not None:
                    assigned[rule["key"]] = hit
                    break

    directories: dict[str, str] = {}
    assign(applicable, directories, {})
    render_dirs: dict[str, str] = {}
    assign(list(rules.get("render_dirs") or []), render_dirs, directories)
    assigned = {path: key for key, path in directories.items()}
    covered = list(directories.values()) + list(render_dirs.values())

    def qualifies(folder: str) -> bool:
        return (
            folder not in covered
            and not any(_inside(folder, a) for a in covered)
            and not any(_inside(a, folder) for a in covered)
        )

    if type is None:
        pattern = (types.get("project_sensitive") or {}).get("key_pattern") or "^$"
        if is_course:
            type = "course"
        elif any(re.search(pattern, key) for key in directories):
            type = "project_sensitive"
        else:
            type = "project" if directories else "bare"
    plan = AdoptPlan(
        private=[f for f in kept if "private" in f.lower()],
        render_dirs=render_dirs,
        type=type,
        reserved=list(dict.fromkeys(rule["key"] for rule in applicable)),
    )
    for folder in kept:
        if folder in assigned:
            plan.roles[folder] = assigned[folder]
        elif qualifies(folder):
            parent = folder.rsplit("/", 1)[0] if "/" in folder else None
            if parent is None or not (parent in kept and qualifies(parent)):
                plan.roles[folder] = EXTRA
    return plan


def _inside(path: str, folder: str) -> bool:
    return path.startswith(f"{folder}/")


def _extra_key(path: str, taken: set[str]) -> str:
    key = re.sub(r"[^a-z0-9]+", "_", path.lower()).strip("_") or "folder"
    if not key[0].isalpha():
        key = f"dir_{key}"
    candidate, n = key, 2
    while candidate in taken:
        candidate, n = f"{key}_{n}", n + 1
    return candidate


# Language ------------------------------------------------------------------------------

_PYTHON_CHUNK = re.compile(r"^```\{python", re.MULTILINE)
_R_CHUNK = re.compile(r"^```\{r[ ,}]", re.MULTILINE)


def _qmd_language(path: Path) -> str:
    """A Quarto document's language: python when it has Python chunks and no R chunks,
    otherwise r, Quarto's knitr engine. Only the first 64 KB are read."""
    try:
        with path.open("rb") as handle:
            text = handle.read(64 << 10).decode("utf-8", errors="replace")
    except OSError:
        return "r"
    return "python" if _PYTHON_CHUNK.search(text) and not _R_CHUNK.search(text) else "r"


def detect_language(root: Path) -> tuple[str | None, str | None]:
    """The language a folder's top-level files say, and the file that decided, as fw
    decides it (``fw adopt``).

    Strong signals: ``pyproject.toml`` (Python); ``renv.lock``, ``DESCRIPTION``, or an
    ``.Rproj`` (R); a ``.do`` file (Stata). Weaker ones, used only without a strong
    one: ``setup.py``, ``setup.cfg``, ``requirements*.txt``, or ``.ipynb`` (Python),
    ``.R`` or ``.Rmd`` (R), and ``.qmd`` by its code chunks. Signals for more than one
    language at the same strength decide nothing.
    """
    strong: dict[str, str] = {}
    weak: dict[str, str] = {}
    try:
        entries = sorted(root.iterdir(), key=lambda p: p.name)
    except OSError:
        return None, None
    for entry in entries:
        if entry.is_dir():
            continue
        name, lower = entry.name, entry.name.lower()
        if name == "pyproject.toml":
            strong.setdefault("python", name)
        elif name in ("renv.lock", "DESCRIPTION") or name.endswith(".Rproj"):
            strong.setdefault("r", name)
        elif lower.endswith(".do"):
            strong.setdefault("stata", name)
        elif (
            name in ("setup.py", "setup.cfg")
            or lower.endswith(".ipynb")
            or (lower.startswith("requirements") and lower.endswith(".txt"))
        ):
            weak.setdefault("python", name)
        elif lower.endswith((".r", ".rmd")):
            weak.setdefault("r", name)
        elif lower.endswith(".qmd"):
            weak.setdefault(_qmd_language(entry), name)
    for found in (strong, weak):
        if len(found) == 1:
            return next(iter(found.items()))
        if found:
            return None, None
    return None, None


_LANGUAGE_NAMES = {"r": "an R", "stata": "a Stata", "python": "a Python"}


# Review --------------------------------------------------------------------------------


def _rows(plan: AdoptPlan) -> list[tuple[str, str]]:
    """The table's rows, as R prints them: each folder with a role, then render folders."""
    rows = []
    for path, role in plan.roles.items():
        shown = "(left out)" if role is None else ("(other folder)" if role == EXTRA else role)
        rows.append((path, shown))
    rows += [(path, f"(rendered {key})") for key, path in plan.render_dirs.items()]
    return rows


def _table(plan: AdoptPlan) -> str:
    sensitive = ", sensitive data" if plan.sensitive else ""
    lines = [f"Proposed mapping (type: {plan.type}{sensitive}):"]
    width = max(30, *(len(path) for path, _ in _rows(plan))) if _rows(plan) else 30
    for number, (path, shown) in enumerate(_rows(plan), start=1):
        lines.append(f"  {number:>2}. {path:<{width}} {shown}")
    return "\n".join(lines)


def _review(plan: AdoptPlan, ask: Callable[[str], str], cancelled: str) -> AdoptPlan:
    """Let the person accept the plan, change a folder's role, leave a folder out, or cancel."""
    while True:
        rows = _rows(plan)
        print(_table(plan), file=sys.stderr)
        answer = ask(
            "Press Enter to accept, type a number to change that folder's role, "
            "-number to leave it out, or q to cancel: "
        ).strip()
        if not answer:
            return plan
        if answer.lower() == "q":
            raise FrameworkError(cancelled)
        out = answer.startswith("-")
        number = answer.lstrip("-")
        if not number.isdigit() or not 1 <= int(number) <= len(rows):
            print(f"Type a number from 1 to {len(rows)}.", file=sys.stderr)
            continue
        path = rows[int(number) - 1][0]
        if out:
            _set_role(plan, path, None)
            continue
        role = ask(f"Role for {path} (a directory key such as inputs_raw, or {EXTRA}): ").strip()
        if role != EXTRA and not _KEY.match(role):
            print(f"'{role}' isn't a directory key.", file=sys.stderr)
        elif role != EXTRA and role in plan.directories and plan.directories[role] != path:
            print(f"{plan.directories[role]} already has the role {role}.", file=sys.stderr)
        else:
            _set_role(plan, path, role)


def _set_role(plan: AdoptPlan, path: str, role: str | None) -> None:
    """Give a folder a role. A render folder given one is no longer a render folder, and
    a role keeps the folder's place in byte order, so extra keys are numbered the same way
    whatever order the changes came in."""
    plan.render_dirs = {key: p for key, p in plan.render_dirs.items() if p != path}
    plan.roles[path] = role
    plan.roles = dict(sorted(plan.roles.items(), key=lambda item: item[0].encode("utf-8")))


def _yes_no(ask: Callable[[str], str], question: str) -> bool:
    return ask(f"{question} [y/N] ").strip().lower() in ("y", "yes")


# Public API ----------------------------------------------------------------------------

ADOPT_TYPES = ("project", "project_sensitive", "course", "presentation", "bare")


def adopt(
    path: str | Path = ".",
    yes: bool = False,
    *,
    type: str | None = None,
    sensitive: bool | None = None,
    cloud: bool | None = None,
    slug: str | None = None,
    force: bool = False,
) -> Path:
    """Make an existing folder a Framework project, in place.

    The folder's subfolders are scanned (names only, at most three levels) and
    each gets a proposed role: a Framework directory key such as
    ``inputs_raw``, a folder of its own (``extra_directories``), a folder of
    rendered notebooks (``render_dirs``), or none. Framework's ``settings/``
    folder and Quarto's ``*_files/`` folders are never proposed. You review the
    proposal (with ``yes=True`` it is printed and accepted), then
    ``settings.yml`` and ``.framework/spec.json`` are written for the folders
    that are there. The rules of the type's ``.gitignore`` template that the
    folder's ``.gitignore`` lacks (``.env``, ``framework.db``, private folders,
    Quarto's lines) are appended in one block. No ``.gitignore`` line is
    removed, and a rule that would ignore files git tracks is left out.
    Nothing is moved, and nothing else is created. A folder with no
    subfolders is adopted with no folders.

    The type follows from the folders unless ``type`` is given: ``course``
    when there are course folders such as ``slides/`` and ``modules/``,
    ``project_sensitive`` when private and public folders such as
    ``inputs/private/raw/`` match the Privacy Sensitive layout, ``project``
    when any folder matches a Framework directory, and ``bare`` otherwise.

    The spec records the folder as it is, as R's ``adopt()`` writes it: the
    mapped folders only, no tools, no default packages, AI files off, and git
    on when the folder is a git repository.

    framework-py adopts Python projects. When the folder's files say another
    language (an ``.Rproj``, ``renv.lock``, or ``.R`` files for R), it stops
    unless ``force`` is True.

    Args:
        path: The project folder.
        yes: Accept the proposal without asking, as a script must.
        type: The project type: "project", "project_sensitive", "course",
            "presentation", or "bare". Default: inferred from the folders.
            "project_sensitive" implies ``sensitive=True``.
        sensitive: True when the project holds sensitive data
            (``security: sensitive: true``): Framework treats its outputs as
            private by default and turns on the sensitive-data guards. None (the
            default) asks in the review when folders look private, and is False
            with ``yes=True``.
        cloud: True registers the adopted project on Framework Cloud and links it
            (``cloud_register()``); False never does. None (the default) asks in an
            interactive session when you are signed in. If you don't register, it
            offers to save the layout as a structure.
        slug: With ``cloud``, the project's slug on Framework Cloud. Default: made from
            the folder's name.
        force: Adopt the folder as a Python project even when its files say
            another language.

    Returns:
        The project folder.

    Raises:
        FrameworkError: When the folder doesn't exist or is already a Framework project,
            when its files say another language and ``force`` is False, when the
            arguments disagree, or when the review needs a terminal and ``yes`` is False.
    """
    show_messages()
    root = Path(path).expanduser().resolve()
    if not root.is_dir():
        raise FrameworkError(f"Not a folder: {root}")
    if type is not None and type not in ADOPT_TYPES:
        raise FrameworkError(f"type must be one of {', '.join(ADOPT_TYPES)}, not '{type}'.")
    if type == "project_sensitive":
        if sensitive is False:
            raise FrameworkError(
                'type="project_sensitive" holds sensitive data; leave sensitive out or set '
                "it to True."
            )
        sensitive = True
    from framework.project import settings_files

    existing = [p.name for p in settings_files(root)]
    if (root / ".framework/spec.json").exists():
        existing.append(".framework/spec.json")
    if existing:
        raise FrameworkError(
            f"{root} is already a Framework project (found {existing[0]}). Nothing was changed."
        )
    language, source = detect_language(root)
    if language not in (None, "python"):
        described = f"{_LANGUAGE_NAMES[language]} project (from {source})"
        if not force:
            raise FrameworkError(
                f"{root} looks like {described}, and framework-py adopts Python projects. "
                "Adopt it in its own language (in R: framework::adopt(); or fw adopt), or "
                "pass force=True to adopt it as Python. Nothing was changed."
            )
        print(f"Warning: {root} looks like {described}. Adopting it as Python.", file=sys.stderr)
    if not yes and not sys.stdin.isatty():
        raise FrameworkError("adopt() needs a terminal to review the folders. Pass yes=True.")

    plan = adopt_plan(scan_folders(root), type=type)
    if plan.type == "project_sensitive":
        sensitive = True
    plan.sensitive = sensitive is True
    for folder in plan.private:
        # The same wording as R's adopt()
        print(
            f"Warning: '{folder}' looks private. Keep it out of git and away from AI assistants.",
            file=sys.stderr,
        )
    if plan.empty:
        print(
            "No folders to map: settings.yml will list none. Add folders to it as you make them.",
            file=sys.stderr,
        )
    elif yes:
        print(_table(plan), file=sys.stderr)
    else:
        plan = _review(plan, input, "Cancelled. Nothing was written.")
        if plan.private and sensitive is None:
            plan.sensitive = _yes_no(
                input,
                "Does this project hold sensitive data? Framework will treat its outputs as "
                "private by default.",
            )

    spec = adopt_spec(root.name, plan, use_git=(root / ".git").exists())
    from framework.create import _record, _write_settings

    _write_settings(root, spec)
    # Merge the spec's .gitignore into the folder's, adding lines only, and record the
    # .gitignore the project now has
    spec["git"]["gitignore_content"] = _adopt_gitignore(root, spec, plan).rstrip("\n")
    _record(root, spec)
    inform(f"[ok] {root.name} is a Framework project. Run framework.scaffold() in it to start.")
    if plan.private and not plan.sensitive:
        print(
            "Some folders look private. Keep them out of git. If the project holds sensitive "
            "data, adopt it with sensitive=True (python -m framework adopt --sensitive, or fw "
            "adopt --sensitive), or set security: sensitive: true in settings.yml.",
            file=sys.stderr,
        )
    _register(root, plan, cloud, slug, offer=cloud is None and not yes)
    return root


def _adopt_gitignore(root: Path, spec: Mapping[str, Any], plan: AdoptPlan) -> str:
    """Append the rules the spec's ``.gitignore`` template has and the folder's
    ``.gitignore`` lacks, plus each folder whose path looks private. Returns the
    ``.gitignore`` text afterwards."""
    from framework._gitignore import append_rules, missing_rules, rules_summary, unignored_folders

    wanted = [str(spec["git"]["gitignore_content"])]
    wanted += [f"/{folder}/" for folder in unignored_folders(root, plan.private)]
    missing = missing_rules(root, wanted)
    append_rules(root, missing.add, "# Added by framework.adopt()")
    if missing.add:
        inform(f"  Added to .gitignore ({len(missing.add)} rules): {rules_summary(missing.add)}")
    if missing.skipped:
        shown = ", ".join(f"{rule} ({file})" for rule, file in missing.skipped.items())
        inform(f"  Left out of .gitignore because git tracks files they match: {shown}")
    path = root / ".gitignore"
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def adopt_spec(name: str, plan: AdoptPlan, use_git: bool = False) -> dict[str, Any]:
    """The resolved spec for an adopted folder, as R's ``adopt()`` records it.

    A project of the mapped type with exactly the mapped folders: the type's own
    folders and render folders are not added. No tools, no default packages, no
    skills, and no AI files. Git is on when the folder is a git repository.
    """
    from framework.spec.resolve import spec_resolve

    sensitive = plan.sensitive or plan.type == "project_sensitive"
    layer = {
        "type": plan.type,
        "language": "python",
        "core_files": False,
        "settings_layout": "flat",
        "tools": {"quarto": False, "uv": False},
        "git": {"use_git": use_git},
        "ai": {"enabled": False},
        "security": {"sensitive": sensitive},
    }
    spec = spec_resolve([layer], name, check=False).spec
    spec["directories"] = plan.directories
    spec["extra_directories"] = plan.extra_directories
    spec["render_dirs"] = dict(plan.render_dirs)
    spec["packages"] = {**(spec.get("packages") or {}), "default_packages": {}}
    spec["skills"] = {}
    spec["source"] = {"origin": "python", "adopted": True}
    from framework.spec.check import spec_check
    from framework.spec.merge import SpecError

    problems = spec_check(spec)
    if problems:
        raise SpecError(
            "The adopted project's spec is invalid:\n" + "\n".join(f"  - {p}" for p in problems)
        )
    return spec


def _register(
    root: Path, plan: AdoptPlan, cloud: bool | None, slug: str | None, offer: bool
) -> None:
    """Register on Framework Cloud when asked to, or, signed in and interactive, when the
    person says yes (default No). Declining offers to save the layout as a structure."""
    from framework.cloud import CloudError, cloud_register, cloud_server, cloud_token, host

    where = None
    if offer:
        try:
            server = cloud_server().url
        except CloudError:
            return
        if cloud_token(server) is None or not sys.stdin.isatty():
            return
        where = host(server)
        cloud = _yes_no(input, f"Register this project on {where}?")
    if cloud:
        try:
            cloud_register(root, slug=slug)
        except FrameworkError as error:
            print(f"Not registered on Framework Cloud: {error}", file=sys.stderr)
    elif where is not None and _yes_no(
        input, f"Save this folder layout as a structure on {where}?"
    ):
        _send_structure(root.name, plan)


def structure_import(
    path: str | Path = ".", name: str | None = None, yes: bool = False
) -> dict[str, Any]:
    """Save an existing folder's layout as a private structure on Framework Cloud.

    The folders are scanned and mapped as in ``adopt()``, and you review the
    mapping the same way. Only the confirmed folder paths are sent: never file
    names or contents.

    Args:
        path: The project folder.
        name: The structure's name. Default: the folder's name.
        yes: Send the proposed mapping without asking, as a script must.

    Returns:
        The new structure: ``key`` (``"<owner>/<key>"``, or only ``"<key>"`` when your
        account has no handle yet), ``name``, and ``url``.

    Raises:
        CloudError: When you are not logged in, or the server rejects the layout or
            doesn't support structures.
        FrameworkError: When the review needs a terminal and ``yes`` is False, or when
            you cancel it.
    """
    show_messages()
    root = Path(path).expanduser().resolve()
    if not root.is_dir():
        raise FrameworkError(f"Not a folder: {root}")
    if not yes and not sys.stdin.isatty():
        raise FrameworkError(
            "structure_import() needs a terminal to review the folders. Pass yes=True."
        )
    plan = adopt_plan(scan_folders(root))
    if not yes:
        plan = _review(plan, input, "Cancelled. Nothing was sent.")
    return _send_structure(name or root.name, plan)


def _send_structure(name: str, plan: AdoptPlan) -> dict[str, Any]:
    from framework.cloud import CloudError, _MissingEndpointError, _require_token, api, cloud_server

    server = cloud_server().url
    token = _require_token(server)
    body: dict[str, Any] = {"name": name, "directories": plan.directories}
    if plan.extra_directories:
        body["extra_directories"] = plan.extra_directories
    try:
        created = api("/api/v1/structures", token, server, method="POST", body=body)
    except _MissingEndpointError as error:
        raise CloudError("This Framework Cloud server doesn't support structures yet.") from error
    if not isinstance(created, dict):
        raise CloudError("The server returned an unexpected structure.")
    inform(f"[ok] Saved the structure {created.get('key')}: {created.get('url')}")
    return created
