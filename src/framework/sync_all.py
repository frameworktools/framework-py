"""Everything that syncs with Framework Cloud, in one call.

A project's packages, its skills, and its data registry. ``pull_all()`` lists every change
from Cloud, asks once, and applies each part; ``push_all()`` does the same in the other
direction. Each part keeps its own revision and lock file: ``.framework/setup.lock.json``
for packages, ``skills.lock.json`` for skills, and ``data.lock.json`` for the data
registry. ``pull_skills()`` / ``push_skills()`` and ``pull_data_registry()`` /
``push_data_registry()`` sync one part. R: ``pull_all()``, ``push_all()``; fw: ``fw pull``,
``fw push``.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from collections.abc import Collection, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from framework.errors import FrameworkError

_LOCK = ".framework/setup.lock.json"
# framework-py itself, and the packages Framework adds for a workflow's tools, are
# not part of the setup when unpinned: every project gets them.
_IMPLIED = {"framework-py", "ipykernel", "nbformat", "nbclient", "jupyterlab"}
_REQUIREMENT = re.compile(
    r"^\s*(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)\s*(\[[^\]]*\])?\s*"
    r"(?:@\s*(?P<url>\S+)|(?P<spec>[^;@]*))?\s*(;.*)?$"
)
_GITHUB = re.compile(r"^git\+https://github\.com/(?P<repo>[^@#\s]+?)(?:\.git)?(?:@(?P<ref>\S+))?$")


def _key(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _row(requirement: str) -> dict[str, Any] | None:
    """A pyproject.toml requirement as a setup package row, or None for one Cloud can't hold."""
    match = _REQUIREMENT.match(requirement)
    if match is None:
        return None
    row: dict[str, Any] = {
        "name": match["name"],
        "source": "pypi",
        "version": None,
        "auto_attach": False,
        "repo": None,
    }
    if match["url"]:
        github = _GITHUB.match(match["url"])
        if github is None:
            return None
        row.update(source="github", repo=github["repo"])
    elif match["spec"] and match["spec"].strip():
        row["version"] = re.sub(r"\s+", "", match["spec"])
    return row


def _version(row: dict[str, Any]) -> str | None:
    """The version as a requirement writes it: Cloud's bare "2.2" means ==2.2."""
    version = row.get("version")
    if not version:
        return None
    return str(version) if re.match(r"^[<>=!~]", str(version)) else f"=={version}"


def _same(local: dict[str, Any], cloud: dict[str, Any]) -> bool:
    if (local.get("source") or "pypi") != (cloud.get("source") or "pypi"):
        return False
    if local.get("source") == "github":
        return local.get("repo") == cloud.get("repo")
    return _version(local) == _version(cloud)


def _describe(row: dict[str, Any]) -> str:
    if row.get("source") == "github":
        return f"github {row.get('repo')}"
    return _version(row) or "any version"


def _local(root: Path, cloud: Collection[str]) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    """The project's packages from pyproject.toml, by normalized name, and their requirements.

    framework-py and the workflow tool packages count only when ``cloud``, the normalized
    names of the setup's packages, lists them too, or when they are pinned.
    """
    from framework.packages import _dependencies

    rows: dict[str, dict[str, Any]] = {}
    requirements: dict[str, str] = {}
    for requirement in _dependencies(root):
        row = _row(requirement)
        if row is None:
            continue
        key = _key(row["name"])
        implied = key in _IMPLIED and row["version"] is None and row["source"] == "pypi"
        if implied and key not in cloud:
            continue
        rows[key] = row
        requirements[key] = requirement
    return rows, requirements


def _other_language(language: str, function: str) -> FrameworkError | None:
    """An error for a project whose packages framework-py doesn't manage, such as an R one."""
    from framework.cloud import CloudError

    if language in ("python", "stata"):
        return None
    name = {"r": "an R"}.get(language, f"a {language}")
    package = "the framework R package's" if language == "r" else "its language package's"
    return CloudError(
        f"This is {name} project on Framework Cloud. Use {package} {function}() "
        f"or fw {function.split('_')[0]}. Nothing changed."
    )


def _lock_read(root: Path) -> dict[str, Any]:
    path = root / _LOCK
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _lock_write(root: Path, revision: str, server: str) -> None:
    path = root / _LOCK
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "schema": 1,
        "revision": revision,
        "server": server,
        "updated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _connection(root: Path) -> tuple[str, str]:
    """The project's server and token, or CloudError when it has no token."""
    from framework.cloud import CloudError, cloud_server, project_token

    token = project_token(root)
    if token is None:
        raise CloudError("no project token in FW_PROJECT_TOKEN or the project's .env")
    return cloud_server(scope="project", project_dir=root).url, token


def _unreachable(error: Exception) -> FrameworkError:
    from framework.cloud import CloudError, _MissingEndpointError

    if isinstance(error, _MissingEndpointError):
        return CloudError("This Framework Cloud server has no setup endpoint yet. Nothing changed.")
    reason = str(error).rstrip(".") or type(error).__name__
    return CloudError(f"Framework Cloud not reached ({reason}). Nothing changed.")


@contextmanager
def _quiet() -> Iterator[None]:
    """Hold back Framework's messages, as when pull_skills() only plans or applies."""
    from framework._log import logger

    level = logger.level
    logger.setLevel(logging.WARNING)
    try:
        yield
    finally:
        logger.setLevel(level)


def _ask(question: str, confirm: bool | None, function: str) -> bool:
    if confirm is not None:
        return confirm
    if not sys.stdin.isatty():
        raise FrameworkError(
            f"{function}() asks before changing anything. "
            "Without a console, pass confirm=True, or dry_run=True to only see the changes."
        )
    return input(f"{question} [y/n] ").strip().lower() in ("y", "yes")


def _project_name(root: Path) -> str:
    from framework.settings import settings_read

    try:
        name = settings_read(root / "settings.yml").get("project_name")
    except Exception:
        name = None
    return str(name) if name else root.resolve().name


def _skills_line(counts: dict[str, int]) -> str | None:
    labels = [
        ("added", "to add"),
        ("updated", "to update"),
        ("replaced", "to replace (backed up)"),
        ("edited", "locally edited (kept)"),
        ("removed", "no longer in the project's setup (kept)"),
    ]
    parts = [f"{counts.get(key, 0)} {label}" for key, label in labels if counts.get(key)]
    return f"  {', '.join(parts)}" if parts else None


def _install(root: Path, requirements: list[str]) -> None:
    """Add or update requirements in pyproject.toml and install them: uv, else pip."""
    from framework.packages import (
        _add_to_pyproject,
        _check,
        _pyproject,
        _refresh_agents_md,
        _run,
        _uv,
    )

    uv = _uv()
    if uv is not None:
        # uv add replaces the requirement of a package already listed. --raw keeps each
        # as Cloud has it: no added lower bound, and a GitHub URL stays in dependencies
        _check(_run([uv, "add", "--raw", *requirements], root), "uv add")
    else:
        path = _pyproject(root)
        for requirement in requirements:
            _replace_requirement(path, requirement)
            _add_to_pyproject(path, requirement)
        _check(_run([sys.executable, "-m", "pip", "install", *requirements], root), "pip install")
    _refresh_agents_md(root)


def _replace_requirement(path: Path, requirement: str) -> None:
    """Without uv: swap an existing requirement for the same package, keeping the layout."""
    from framework.packages import _dependencies, _package_name

    key = _key(_package_name(requirement))
    text = path.read_text(encoding="utf-8")
    for old in _dependencies(path.parent):
        if _key(_package_name(old)) == key and old != requirement:
            text = re.sub(
                r"([\"'])" + re.escape(old) + r"\1", json.dumps(requirement), text, count=1
            )
    path.write_text(text, encoding="utf-8")


# Parts ---------------------------------------------------------------------------------------
#
# Each part's plan is a dict: ``title`` (shown above its lines), ``lines`` (indented
# details), ``warnings`` (what a push affects), ``state`` (shown instead of "up to date"
# when there are no lines), ``quiet`` (nothing worth showing), ``pending`` (something to
# apply), and ``apply``, a function that applies it. A plan that can't be made carries
# ``error`` instead.

_PARTS = (("packages", "Packages"), ("skills", "Skills"), ("data_registry", "Data registry"))


def _strip(message: str) -> str:
    return re.sub(r"\s*Nothing changed\.?$", "", message)


def _plan(label: str, make: Any) -> dict[str, Any]:
    try:
        plan: dict[str, Any] = make()
    except FrameworkError as error:
        plan = {"error": _strip(str(error)), "pending": False, "quiet": False}
    plan["label"] = label
    plan.setdefault("title", label)
    return plan


def _lines(parts: dict[str, dict[str, Any]], heading: str, current: str, refused: str) -> list[str]:
    """The combined change list, by part. ``refused`` describes a part whose plan failed."""
    if all(p.get("quiet") for p in parts.values()):
        return [current]
    lines = [heading]
    for part in parts.values():
        if "error" in part:
            lines.append(f"{part['label']}: {refused}. {part['error']}")
        elif not part.get("lines") and not part.get("warnings"):
            lines.append(f"{part['title']}: {part.get('state') or 'up to date'}")
        else:
            lines.append(f"{part['title']}:")
            lines += part.get("lines") or []
            lines += [f"  {w}" for w in part.get("warnings") or []]
    return lines


def _apply(parts: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Apply each part in turn. A part that fails doesn't stop the others."""
    out: dict[str, Any] = {"applied": [], "current": [], "failed": {}, "values": {}}
    for name, part in parts.items():
        if "error" in part:
            out["failed"][part["label"]] = part["error"]
            continue
        try:
            value = part["apply"]()
        except FrameworkError as error:
            out["failed"][part["label"]] = _strip(str(error))
            continue
        out["values"][name] = value
        out["applied" if part.get("pending") else "current"].append(part["label"])
    return out


def _partial(function: str, done: str, outcome: dict[str, Any]) -> str:
    """Which parts were applied and which weren't, when one failed."""
    reasons = "; ".join(f"{n} ({r.rstrip('.')})" for n, r in outcome["failed"].items())
    applied = ", ".join(outcome["applied"]) or "nothing"
    current = f"Already in step: {', '.join(outcome['current'])}. " if outcome["current"] else ""
    return f"{function} did not finish. {done}: {applied}. {current}Not {done.lower()}: {reasons}."


def _setup(root: Path) -> tuple[str, str, dict[str, Any]]:
    """The project's server, token, and package list; errors change nothing."""
    from framework.cloud import CloudError, _request

    try:
        server, token = _connection(root)
        setup = _request("/api/v1/projects/setup", token, server).json()
    except CloudError as error:
        raise _unreachable(error) from error
    return server, token, setup if isinstance(setup, dict) else {}


def _names(plans: list[dict[str, Any]]) -> str:
    return ", ".join(p["name"] for p in plans)


# Pull ----------------------------------------------------------------------------------------


def _packages_pull_plan(
    root: Path, server: str, setup: dict[str, Any], overwrite: bool
) -> dict[str, Any]:
    """Packages: what Cloud's package list adds or changes here."""
    from framework._log import inform
    from framework.create import _dependency

    revision = str(setup.get("revision") or "")
    language = setup.get("language") or "python"

    def settle() -> None:
        _lock_write(root, revision, server)

    if language != "python":
        return {
            "state": f"not managed for {language.title()} projects",
            "quiet": True,
            "pending": False,
            "revision": revision,
            "packages": {"added": [], "changed": [], "differ_kept": [], "kept_local": []},
            "apply": settle,
        }
    cloud = {_key(p["name"]): p for p in setup.get("packages") or [] if isinstance(p, dict)}
    local, _ = _local(root, cloud)
    added = [cloud[k] for k in cloud if k not in local]
    differ = [(local[k], cloud[k]) for k in cloud if k in local and not _same(local[k], cloud[k])]
    kept_local = [local[k]["name"] for k in local if k not in cloud]

    lines: list[str] = []
    if added:
        lines.append(f"  Packages to add: {', '.join(p['name'] for p in added)}")
    if differ:
        details = ", ".join(
            f"{c['name']} (local {_describe(lo)}, Cloud {_describe(c)})" for lo, c in differ
        )
        tail = (
            "Cloud's will be used" if overwrite else "kept; pull_all(overwrite=True) uses Cloud's"
        )
        lines.append(f"  Packages that differ: {details} — {tail}")
    if kept_local:
        lines.append(f"  Only in this project (kept): {', '.join(kept_local)}")
    requirements = [_dependency(p["name"], p) for p in added]
    if overwrite:
        requirements += [_dependency(c["name"], c) for _, c in differ]

    def apply() -> None:
        if requirements:
            _install(root, requirements)
            inform(f"✓ Packages: {len(requirements)} added or updated")
        settle()

    return {
        "title": f"Packages (revision {revision})",
        "lines": lines,
        # Packages only in this project are listed, but don't make it out of step
        "quiet": not added and not differ,
        "pending": bool(requirements),
        "revision": revision,
        "packages": {
            "added": [p["name"] for p in added],
            "changed": [c["name"] for _, c in differ] if overwrite else [],
            "differ_kept": [] if overwrite else [c["name"] for _, c in differ],
            "kept_local": kept_local,
        },
        "apply": apply,
    }


def _skills_pull_plan(root: Path, overwrite: bool) -> dict[str, Any]:
    """Skills: what pull_skills() would add, update, or keep."""
    from framework._log import inform
    from framework.skill_pull import _BACKUPS, pull_skills

    with _quiet():
        plan = pull_skills(root, overwrite=overwrite, dry_run=True)
    lines: list[str] = []
    if line := _skills_line(plan["counts"]):
        lines.append(line)
    for skill in plan["skills"]:
        name, status = skill["name"], skill["status"]
        if status == "edited":
            lines.append(f"  Kept (locally edited): {name} — pull_all(overwrite=True) replaces it")
        elif status == "replaced":
            lines.append(f"  Replaced (old copy in {_BACKUPS}): {name}")
        elif status == "removed":
            lines.append(f"  No longer in the project's setup (kept): {name}")
        elif status == "unavailable":
            lines.append(f"  Not in this version of framework-py: {name}")
    if plan["source"] == "package":
        lines.append(
            "  Framework Cloud's skill list not read; using the package skills for this "
            "project type"
        )
    if lines:
        lines += [f"  {note}" for note in plan.get("notes") or []]
    pending = any(plan["counts"].get(k) for k in ("added", "updated", "replaced"))

    def apply() -> dict[str, Any] | None:
        if not pending:
            return None
        with _quiet():
            result = pull_skills(root, overwrite=overwrite)
        inform(f"✓ {result['message']}")
        return result

    return {"lines": lines, "quiet": not lines, "pending": pending, "plan": plan, "apply": apply}


def _data_pull_plan(root: Path, server: str, token: str, overwrite: bool) -> dict[str, Any]:
    from framework import data_registry

    plan = data_registry._pull_plan(root, server, token, overwrite, fn="pull_all()")
    entries = plan["entries"]
    plan["title"] = f"Data registry (revision {plan['revision']})"
    plan["quiet"] = not entries["added"] and not (entries["changed"] or entries["differ_kept"])
    plan["apply"] = lambda: data_registry._pull_apply(root, plan, server)
    return plan


def pull_all(
    project_path: str | Path = ".",
    overwrite: bool = False,
    confirm: bool | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Bring everything that syncs up to date with the project on Framework Cloud.

    One call pulls the project's packages, its skills, and its data registry. Each part is
    read with the project token, and the changes are listed together, grouped by part
    (Packages, Skills, Data registry). Then asks "Apply these changes?" once.

    Packages: new ones are added to ``pyproject.toml`` and installed (uv, else pip). Local
    packages are never removed. Skills: missing ones are added and unedited ones updated, as
    ``pull_skills()`` does. Data registry: new entries are written into the settings' data
    section, as ``pull_data_registry()`` does; data files never move. Local edits win
    everywhere: a package or entry whose local settings differ, and a locally edited skill,
    are kept unless ``overwrite=True`` (edited skills are backed up first).

    The revisions pulled are kept in ``.framework/setup.lock.json`` (packages) and
    ``.framework/data.lock.json`` (data registry) for ``push_all()``. When a part can't be
    read or applied, the others still are, and the error names which parts were applied
    and which weren't.

    Args:
        project_path: The project's root directory. Default: the current directory.
        overwrite: Use Cloud's settings for differing packages and data entries, and
            replace locally edited skills.
        confirm: True applies without asking; False cancels. None asks in a console.
        dry_run: List the changes and write nothing.

    Returns:
        A dict: ``packages`` (``revision``, and ``added``, ``changed``, ``differ_kept``,
        and ``kept_local``, as names), ``skills`` (the ``pull_skills()`` result: the plan,
        or what was applied), ``data_registry`` (``revision``, and ``added``, ``changed``,
        ``differ_kept``, and ``kept_local``), ``applied`` (the parts applied), ``failed``
        (a reason for each part that wasn't), ``dry_run``, and ``message``.

    Raises:
        CloudError: Without a project token, offline, or when the server has no setup
            endpoint (nothing changes then), and when a part couldn't be applied.
        FrameworkError: Without a console and without ``confirm=True`` or ``dry_run=True``.
    """
    from framework._log import inform, show_messages
    from framework.cloud import CloudError
    from framework.skill_pull import _root

    show_messages()
    root = _root(project_path)
    server, token, setup = _setup(root)
    if refusal := _other_language(setup.get("language") or "python", "pull_all"):
        raise refusal

    parts = {
        "packages": _plan("Packages", lambda: _packages_pull_plan(root, server, setup, overwrite)),
        "skills": _plan("Skills", lambda: _skills_pull_plan(root, overwrite)),
        "data_registry": _plan(
            "Data registry", lambda: _data_pull_plan(root, server, token, overwrite)
        ),
    }
    lines = _lines(
        parts,
        "Changes from Framework Cloud:",
        "Project is up to date with Framework Cloud.",
        "not read",
    )
    for line in lines:
        inform(line)

    result: dict[str, Any] = {
        "packages": {
            "revision": parts["packages"].get("revision"),
            **parts["packages"].get("packages", {}),
        },
        "skills": parts["skills"].get("plan"),
        "data_registry": {
            "revision": parts["data_registry"].get("revision"),
            **parts["data_registry"].get("entries", {}),
        },
        "applied": [],
        "failed": {n: p["error"] for n, p in parts.items() if "error" in p},
        "dry_run": dry_run,
        "message": "\n".join(lines),
    }
    if dry_run:
        return result
    if any(p.get("pending") for p in parts.values()) and not _ask(
        "Apply these changes?", confirm, "pull_all"
    ):
        inform("Nothing changed.")
        return result

    outcome = _apply(parts)
    if outcome["values"].get("skills") is not None:
        result["skills"] = outcome["values"]["skills"]
    result["applied"] = [n for n, p in parts.items() if p["label"] in outcome["applied"]]
    result["failed"] = {
        n: outcome["failed"][p["label"]]
        for n, p in parts.items()
        if p["label"] in outcome["failed"]
    }
    if outcome["failed"]:
        raise CloudError(_partial("pull_all()", "Applied", outcome))
    return result


# Push ----------------------------------------------------------------------------------------


def _packages_push_plan(
    root: Path, server: str, token: str, setup: dict[str, Any], remove: bool, force: bool
) -> dict[str, Any]:
    """Packages: what Cloud's dry run says pushing the package list would change."""
    from framework._log import inform
    from framework.cloud import (
        CloudConflictError,
        CloudError,
        CloudOfflineError,
        _MissingEndpointError,
        api,
    )

    language = setup.get("language") or "python"
    none: dict[str, list[Any]] = {"added": [], "changed": [], "removed": [], "kept": []}
    if language != "python":
        return {
            "state": f"not managed for {language.title()} projects",
            "quiet": True,
            "pending": False,
            "revision": setup.get("revision"),
            "changes": none,
            "apply": lambda: None,
        }
    # The base revision for a project that never pulled its package list
    base = _lock_read(root).get("revision") or setup.get("revision")
    cloud = {_key(p["name"]) for p in setup.get("packages") or [] if isinstance(p, dict)}
    local, _ = _local(root, cloud)

    def send(dry: bool) -> dict[str, Any]:
        body = {
            "base_revision": base,
            "packages": [{**row, "auto_attach": False} for row in local.values()],
            "remove": remove,
            "force": force,
            "dry_run": dry,
        }
        try:
            reply = api("/api/v1/projects/setup/packages", token, server, "POST", body)
        except CloudConflictError as error:
            current = error.body.get("revision") or "unknown"
            raise CloudError(
                "Framework Cloud's package list changed since this project last pulled it "
                f"(revision {current}). Pull first: pull_all(). "
                "push_all(force=True) replaces Cloud's changes."
            ) from error
        except (CloudOfflineError, _MissingEndpointError) as error:
            raise _unreachable(error) from error
        return reply if isinstance(reply, dict) else {}

    preview = send(True)
    changes = preview.get("changes") or {}
    added = [p["name"] for p in changes.get("added") or []]
    changed = changes.get("changed") or []
    removed = [p["name"] for p in changes.get("removed") or []]
    kept = [p["name"] for p in changes.get("kept") or []]

    lines: list[str] = []
    if added:
        lines.append(f"  Packages to add: {', '.join(added)}")
    if changed:
        details = ", ".join(
            f"{c['name']} ({_describe(c['from'])} -> {_describe(c['to'])})" for c in changed
        )
        lines.append(f"  Packages to change: {details}")
    if removed:
        lines.append(f"  Packages to remove: {', '.join(removed)}")
    if kept:
        lines.append(f"  On Cloud only (kept): {', '.join(kept)}")
    pending = bool(added or changed or removed)
    warnings: list[str] = []
    if pending:
        warnings.append(
            f"This changes {_project_name(root)}'s package list on Framework Cloud. "
            "New setups, zips, and Update on GitHub will use it."
        )
        beyond = [n for n in [*added, *(c["name"] for c in changed)] if _key(n) not in local]
        if beyond:
            warnings.append(
                "Pushing re-resolves the project from its blueprint's latest version, "
                "which can update a project pinned to an older blueprint version."
            )
        if force and base != setup.get("revision"):
            warnings.append(
                "Cloud's newer package changes will be replaced by this project's packages."
            )

    def apply() -> dict[str, Any] | None:
        if not pending:
            return None
        reply = send(False)
        revision = str(reply.get("revision") or "")
        _lock_write(root, revision, server)
        done = reply.get("changes") or changes
        parts = [
            f"{len(done.get(key) or [])} {key}"
            for key in ("added", "changed", "removed")
            if done.get(key)
        ]
        detail = f" ({', '.join(parts)})" if parts else ""
        inform(f"✓ Packages pushed: revision {revision}{detail}")
        return {"applied": bool(reply.get("applied")), "revision": revision}

    return {
        "lines": lines,
        "warnings": warnings,
        "quiet": not pending,
        "pending": pending,
        "revision": preview.get("revision"),
        "changes": {"added": added, "changed": changed, "removed": removed, "kept": kept},
        "apply": apply,
    }


def _skills_push_plan(root: Path, force: bool) -> dict[str, Any]:
    """Skills: the edited tracked skills push_skills() would send.

    Checking them against Cloud needs your login, so a project with edited skills and no
    login can't push them.
    """
    from framework._log import inform
    from framework.cloud import CloudError, cloud_server, cloud_token, login_hint
    from framework.skill_pull import _push_apply, _push_edited, _push_plan, _warning

    edited = _push_edited(root)
    url = cloud_server().url
    if edited and cloud_token(url) is None:
        names = ", ".join(Path(paths[0]).name for _, paths in edited)
        raise CloudError(
            f"Not signed in, so edited skills ({names}) can't be pushed. {login_hint(url)}"
        )
    plans, untracked = _push_plan(root, force)
    pushes = [p for p in plans if p["status"] == "push"]
    refused = [p for p in plans if p["status"] == "refused"]
    failed = [p for p in plans if p["status"] == "failed"]

    lines: list[str] = []
    if pushes:
        lines.append(f"  Skills to push: {_names(pushes)}")
    for p in refused:
        lines.append(
            f"  Pull first: {p['name']} changed on Cloud since this project pulled it. "
            "push_all(force=True) replaces it."
        )
    for p in failed:
        lines.append(f"  Not checked: {p['name']}: {p.get('reason')}")
    quiet = not lines
    for p in untracked:
        lines.append(f"  Not tracked: {p['name']} — use skills_sync() to add it")

    def apply() -> list[dict[str, Any]]:
        done = _push_apply(root, plans)
        pushed = [p for p in done if p["status"] == "pushed"]
        if pushed:
            inform(f"✓ Skills pushed: {_names(pushed)}")
        bad = [p for p in done if p["status"] in ("refused", "failed")]
        if bad:
            why = "; ".join(
                f"{p['name']} changed on Cloud since this project pulled it; "
                "push_all(force=True) replaces it"
                if p["status"] == "refused"
                else f"{p['name']}: {str(p.get('reason')).rstrip('.')}"
                for p in bad
            )
            first = f"pushed {_names(pushed)}; " if pushed else ""
            raise CloudError(f"{first}not pushed: {why}")
        return done

    return {
        "lines": lines,
        "warnings": [_warning(p) for p in pushes],
        "quiet": quiet,
        "pending": bool(pushes),
        "plans": plans + untracked,
        "apply": apply,
    }


def _data_push_plan(
    root: Path, server: str, token: str, remove: bool, force: bool
) -> dict[str, Any]:
    from framework import data_registry

    plan = data_registry._push_plan(
        root, server, token, remove, force, "pull_all()", "push_all(force=True)"
    )
    plan["apply"] = lambda: (
        data_registry._push_apply(root, plan, server) if plan["pending"] else None
    )
    return plan


def _skill_rows(plans: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"name": p["name"], "status": p["status"], "path": p["path"]} for p in plans]


def push_all(
    project_path: str | Path = ".",
    remove: bool = False,
    force: bool = False,
    confirm: bool | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Send everything that syncs to the project on Framework Cloud.

    One call pushes the project's package list (from ``pyproject.toml``), its locally
    edited skills, and its data registry. Cloud previews each part first (as a dry run),
    and the changes are listed together, grouped by part (Packages, Skills, Data registry),
    with what each push affects: new setups, zips, and Update on GitHub use the package
    list and data registry, and a skill's new version is used wherever it's attached. Then
    asks "Push these changes?" once. framework-py and the unpinned workflow tool packages
    every project gets are not sent.

    Packages and data entries only on Cloud stay there unless ``remove=True``. When a part
    changed on Cloud since this project last pulled it (the revisions in
    ``.framework/setup.lock.json`` and ``.framework/data.lock.json``, and each skill's in
    ``.framework/skills.lock.json``), that part is refused with "Pull first";
    ``force=True`` replaces Cloud's newer changes. Edited skills need ``cloud_login()``;
    packages and the data registry go with the project token. Only data definitions are
    sent, never data files or hashes.

    When a part can't be pushed, the others still are, and the error names which parts
    were pushed and which weren't. A push resolves the project again from its blueprint's
    latest version.

    Args:
        project_path: The project's root directory. Default: the current directory.
        remove: Remove packages and data entries from Cloud that this project doesn't have.
        force: Push even when a part changed on Cloud since the last pull.
        confirm: True pushes without asking; False cancels. None asks in a console.
        dry_run: List the changes and send nothing.

    Returns:
        A dict: ``packages`` and ``data_registry`` (each with ``revision`` and Cloud's
        ``changes``: ``added``, ``changed``, ``removed``, and ``kept``), ``skills`` (each
        skill's ``name``, ``status``, and ``path``), ``applied`` (the parts pushed),
        ``failed`` (a reason for each part that wasn't), ``dry_run``, and ``message``.

    Raises:
        CloudError: Without a project token, offline, or when the server has no setup
            endpoint (nothing changes then), and when a part couldn't be pushed.
        FrameworkError: Without a console and without ``confirm=True`` or ``dry_run=True``.
    """
    from framework._log import inform, show_messages
    from framework.cloud import CloudError
    from framework.skill_pull import _root

    show_messages()
    root = _root(project_path)
    server, token, setup = _setup(root)
    if refusal := _other_language(setup.get("language") or "python", "push_all"):
        raise refusal

    parts = {
        "packages": _plan(
            "Packages", lambda: _packages_push_plan(root, server, token, setup, remove, force)
        ),
        "skills": _plan("Skills", lambda: _skills_push_plan(root, force)),
        "data_registry": _plan(
            "Data registry", lambda: _data_push_plan(root, server, token, remove, force)
        ),
    }
    lines = _lines(
        parts,
        "Changes for Framework Cloud:",
        "Framework Cloud already matches this project.",
        "can't push",
    )
    for line in lines:
        inform(line)

    result: dict[str, Any] = {
        "packages": {
            "revision": parts["packages"].get("revision"),
            "changes": parts["packages"].get("changes"),
        },
        "skills": _skill_rows(parts["skills"].get("plans") or []),
        "data_registry": {
            "revision": parts["data_registry"].get("revision"),
            "changes": parts["data_registry"].get("changes"),
        },
        "applied": [],
        "failed": {n: p["error"] for n, p in parts.items() if "error" in p},
        "dry_run": dry_run,
        "message": "\n".join(lines),
    }
    if dry_run:
        return result
    if any(p.get("pending") for p in parts.values()) and not _ask(
        "Push these changes?", confirm, "push_all"
    ):
        inform("Nothing pushed.")
        return result

    outcome = _apply(parts)
    for name in ("packages", "data_registry"):
        value = outcome["values"].get(name)
        if value and value.get("revision") is not None:
            result[name]["revision"] = value["revision"]
    if outcome["values"].get("skills") is not None:
        result["skills"] = _skill_rows(outcome["values"]["skills"])
    result["applied"] = [n for n, p in parts.items() if p["label"] in outcome["applied"]]
    result["failed"] = {
        n: outcome["failed"][p["label"]]
        for n, p in parts.items()
        if p["label"] in outcome["failed"]
    }
    if outcome["failed"]:
        raise CloudError(_partial("push_all()", "Pushed", outcome))
    return result
