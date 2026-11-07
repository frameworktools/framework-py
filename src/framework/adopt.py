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
    or None for a folder that is left out. ``private`` lists every scanned
    folder whose path contains "private".
    """

    roles: dict[str, str | None] = field(default_factory=dict)
    private: list[str] = field(default_factory=list)

    @property
    def directories(self) -> dict[str, str]:
        return {role: path for path, role in self.roles.items() if role not in (None, EXTRA)}

    @property
    def extra_directories(self) -> dict[str, dict[str, str]]:
        extra: dict[str, dict[str, str]] = {}
        taken = set(self.directories)
        for path, role in self.roles.items():
            if role == EXTRA:
                key = _extra_key(path, taken)
                taken.add(key)
                extra[key] = {"label": path.rsplit("/", 1)[-1], "path": path}
        return extra


@cache
def adopt_rules() -> dict[str, Any]:
    """The shared mapping rules, as synced from the R package."""
    path = resources.files("framework").joinpath("_assets/adopt/rules.json")
    rules: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return rules


def _kept(path: str, rules: Mapping[str, Any]) -> bool:
    skip = {name.lower() for name in rules.get("skip") or []}
    parts = path.split("/")
    return len(parts) <= int(rules.get("max_depth") or 3) and not any(
        part.startswith(".") or part.lower() in skip for part in parts
    )


def scan_folders(root: Path, rules: Mapping[str, Any] | None = None) -> list[str]:
    """Subfolders of ``root``, as relative POSIX paths in byte order.

    Hidden folders, the rules' ``skip`` folders (version control,
    environments, caches), and everything under them are left out, and so are
    folders more than ``max_depth`` levels deep. Only folder names are read.
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
    folders: list[str], language: str = "python", rules: Mapping[str, Any] | None = None
) -> AdoptPlan:
    """Propose a role for each folder with the shared rules.

    The algorithm is the one in the R package's ``inst/conformance/README.md``
    (adopt/): rules apply in order, each giving its key to the first folder
    that equals one of its patterns, ignoring case. Folders inside an
    assigned folder are covered by it. Each other folder with no assigned
    folder inside it gets a role of its own (``"extra"``), unless its parent
    does too.
    """
    rules = rules or adopt_rules()
    kept = sorted((f for f in folders if _kept(f, rules)), key=lambda p: p.encode("utf-8"))
    by_lower: dict[str, str] = {}
    for folder in kept:
        by_lower.setdefault(folder.lower(), folder)
    assigned: dict[str, str] = {}
    for rule in rules.get("rules") or []:
        if rule.get("languages") and language not in rule["languages"]:
            continue
        for pattern in rule.get("patterns") or []:
            match = by_lower.get(pattern.lower())
            if match is not None and match not in assigned:
                assigned[match] = rule["key"]
                break

    def qualifies(folder: str) -> bool:
        return (
            folder not in assigned
            and not any(_inside(folder, a) for a in assigned)
            and not any(_inside(a, folder) for a in assigned)
        )

    plan = AdoptPlan(private=[f for f in kept if "private" in f.lower()])
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


# Review --------------------------------------------------------------------------------


def _table(plan: AdoptPlan) -> str:
    width = max((len(p) for p in plan.roles), default=0)
    lines = []
    for number, (path, role) in enumerate(plan.roles.items(), start=1):
        shown = "left out" if role is None else ("a folder of its own" if role == EXTRA else role)
        lines.append(f"  {number:>2}. {path:<{width}}  -> {shown}")
    return "\n".join(lines)


def _review(plan: AdoptPlan, ask: Callable[[str], str], cancelled: str) -> AdoptPlan:
    """Let the person accept the plan, change a folder's role, leave a folder out, or cancel."""
    paths = list(plan.roles)
    while True:
        print("Proposed folders:\n" + _table(plan), file=sys.stderr)
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
        if not number.isdigit() or not 1 <= int(number) <= len(paths):
            print(f"Type a number from 1 to {len(paths)}.", file=sys.stderr)
            continue
        path = paths[int(number) - 1]
        if out:
            plan.roles[path] = None
            continue
        role = ask(f"Role for {path} (a directory key such as inputs_raw, or {EXTRA}): ").strip()
        if role != EXTRA and not _KEY.match(role):
            print(f"'{role}' isn't a directory key.", file=sys.stderr)
        elif role != EXTRA and role in plan.directories and plan.directories[role] != path:
            print(f"{plan.directories[role]} already has the role {role}.", file=sys.stderr)
        else:
            plan.roles[path] = role


def _yes_no(ask: Callable[[str], str], question: str) -> bool:
    return ask(f"{question} [y/N] ").strip().lower() in ("y", "yes")


# Public API ----------------------------------------------------------------------------


def adopt(path: str | Path = ".", yes: bool = False) -> Path:
    """Make an existing folder a Framework project, in place.

    The folder's subfolders are scanned (names only, at most three levels) and
    each gets a proposed role: a Framework directory key such as
    ``inputs_raw``, a folder of its own (``extra_directories``), or none.
    You review the proposal, then ``settings.yml`` and ``.framework/spec.json``
    are written for the folders that are there. Nothing is moved, and nothing
    else is created.

    A folder path that contains "private" gets a warning that suggests the
    Privacy Sensitive layout. The sensitive setting is turned on only when you
    say yes in the review.

    Args:
        path: The project folder.
        yes: Accept the proposal without asking, as a script must. The sensitive
            setting stays off.

    Returns:
        The project folder.

    Raises:
        FrameworkError: When the folder doesn't exist or is already a Framework project,
            or when the review needs a terminal and ``yes`` is False.
    """
    show_messages()
    root = Path(path).expanduser().resolve()
    if not root.is_dir():
        raise FrameworkError(f"Not a folder: {root}")
    from framework.project import settings_files

    existing = [path.name for path in settings_files(root)]
    if (root / ".framework/spec.json").exists():
        existing.append(".framework/spec.json")
    if existing:
        raise FrameworkError(
            f"{root} is already a Framework project (it has {', '.join(existing)})."
        )
    if not yes and not sys.stdin.isatty():
        raise FrameworkError("adopt() needs a terminal to review the folders. Pass yes=True.")

    plan = adopt_plan(scan_folders(root))
    sensitive = False
    if not yes:
        plan = _review(plan, input, "Cancelled. Nothing was written.")
    if plan.private:
        # The same wording as R's adopt()
        for folder in plan.private:
            print(
                f"'{folder}' looks private. Keep it out of git and away from AI assistants.",
                file=sys.stderr,
            )
        print(
            "The Privacy Sensitive layout keeps private and public data in separate "
            "folders and adds safeguards for AI assistants.",
            file=sys.stderr,
        )
        if not yes:
            sensitive = _yes_no(input, "Turn on the sensitive-data setting for this project?")

    spec = adopt_spec(root.name, plan, sensitive)
    from framework.create import _record, _write_settings

    _write_settings(root, spec)
    _record(root, spec)
    inform(f"Adopted {root} as a Framework project")
    if not yes:
        _offer_cloud(root, spec, plan)
    return root


def adopt_spec(name: str, plan: AdoptPlan, sensitive: bool = False) -> dict[str, Any]:
    """The resolved spec for an adopted folder: bare, flat settings, no tools."""
    from framework.spec.resolve import spec_resolve

    layer = {
        "type": "bare",
        "language": "python",
        "core_files": False,
        "settings_layout": "flat",
        "tools": {"quarto": False, "uv": False},
        "directories": plan.directories,
        "extra_directories": plan.extra_directories,
        "security": {"sensitive": sensitive},
    }
    spec = spec_resolve([layer], name).spec
    spec["source"] = {"origin": "python"}
    return spec


def _offer_cloud(root: Path, spec: Mapping[str, Any], plan: AdoptPlan) -> None:
    from framework.cloud import CloudError, cloud_server, cloud_token, host, register_project

    try:
        server = cloud_server().url
    except CloudError:
        return
    if cloud_token(server) is None:
        return
    if _yes_no(input, f"Register this project on {host(server)}?"):
        register_project(root, str(spec["name"]), "bare", spec)
    if _yes_no(input, f"Save this folder layout as a structure on {host(server)}?"):
        _send_structure(str(spec["name"]), plan)


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
