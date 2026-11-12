"""A project's skills, kept in step with its setup on Framework Cloud.

``pull_skills()`` installs and updates the skills the project's Cloud setup lists.
``push_skills()`` sends locally edited skills back as new versions. Both build on the
per-skill machinery in ``framework.skill_sync``. R: ``pull_skills()``, ``push_skills()``.
"""

from __future__ import annotations

import base64
import sys
from contextlib import nullcontext
from pathlib import Path
from typing import Any
from urllib.parse import quote

from framework.errors import FrameworkError

STATUSES = ("added", "updated", "current", "edited", "replaced", "removed", "unavailable")
# Which status names a skill when its copies in several folders differ
_PRECEDENCE = ("edited", "replaced", "updated", "added", "unavailable", "current")
_BACKUPS = ".framework/skills-backups/"


def _root(project_path: str | Path) -> Path:
    from framework.project import _settings_in

    root = Path(project_path).expanduser()
    if not (root / ".framework/spec.json").is_file() and _settings_in(root) is None:
        from framework.errors import ProjectNotFoundError

        raise ProjectNotFoundError(
            f"{root} is not a Framework project: it has no .framework/spec.json or settings file."
        )
    return root


def _skill_dirs(root: Path, spec: dict[str, Any]) -> list[str]:
    """The assistants' skills folders, then .agents/skills and .claude/skills if present."""
    from framework.ai import assistant_targets

    folders, _ = assistant_targets(spec)
    for folder in (".agents/skills", ".claude/skills"):
        if (root / folder).is_dir():
            folders.append(folder)
    return list(dict.fromkeys(folders))


def _folders(root: Path, skills_dir: str) -> list[str]:
    path = root / skills_dir
    if not path.is_dir():
        return []
    return sorted(p.name for p in path.iterdir() if p.is_dir() and not p.name.startswith("."))


def _package_skills(spec: dict[str, Any]) -> dict[str, Any]:
    return {
        name: entry
        for name, entry in (spec.get("skills") or {}).items()
        if isinstance(entry, dict) and entry.get("package") is True
    }


def _cloud_skills(root: Path, notes: list[str]) -> dict[str, Any]:
    """The skills the project's current Cloud setup lists, merged over the package defaults.

    Raises CloudError (or another error) when the setup can't be read.
    """
    from framework.cloud import CloudError, _request, cloud_server, project_token
    from framework.spec.resolve import spec_resolve

    token = project_token(root)
    if token is None:
        raise CloudError("no project token")
    server = cloud_server(scope="project", project_dir=root).url
    envelope = _request("/api/v1/projects/pull", token, server).json()
    if not isinstance(envelope, dict) or envelope.get("spec_version") != 1:
        raise CloudError("the server sent a project setup this framework-py can't read")
    layers = envelope.get("layers") or []
    spec = spec_resolve(layers, name=envelope.get("name") or "project", check=False).spec
    skills = dict(spec.get("skills") or {})
    _latest(skills, server, notes)
    return skills


def _latest(skills: dict[str, Any], server: str, notes: list[str]) -> None:
    """Swap in newer Cloud versions of synced skills. Reading them needs your login; a
    project token can't. Without it, the versions in the project's setup are used."""
    from framework.cloud import api, cloud_token
    from framework.skill_sync import bundle_hash

    synced = {n: e for n, e in skills.items() if isinstance(e, dict) and e.get("sync")}
    if not synced:
        return
    token = cloud_token(server)
    if token is None:
        notes.append(
            "Not signed in, so Cloud skills use the versions in the project's setup. "
            "fw.cloud_login() lets pull_skills() get their latest versions."
        )
        return
    for name, entry in synced.items():
        source = entry["sync"]
        try:
            remote = api(
                f"/api/v1/skills/sync/{quote(source['skill_id'], safe='')}"
                f"?variant={source['variant']}",
                token,
                server,
            )
            if bundle_hash(remote["bundle"]) != remote["hash"]:
                raise ValueError("Cloud skill verification failed")
        except Exception as error:
            notes.append(f"Latest version of {name} not checked ({error}); using the setup's.")
            continue
        if int(remote["version"]) > int(source["version"]):
            skills[name] = {
                **entry,
                "bundle": remote["bundle"],
                "sync": {**source, "version": remote["version"], "hash": remote["hash"]},
            }


def _inline_bundle(entry: dict[str, Any]) -> list[dict[str, Any]]:
    files = {"SKILL.md": entry["content"], **(entry.get("files") or {})}
    return [
        {"path": path, "content": base64.b64encode(text.encode()).decode(), "executable": False}
        for path, text in files.items()
    ]


def _newer(entry: dict[str, Any], tracked: dict[str, Any], current: str | None) -> bool:
    """Whether the project holds a newer version of a Cloud skill than its setup names.

    After push_skills(), the lock tracks the pushed version while the setup still names
    the one it was resolved with. Without your login, that older version is all a pull
    has, and it must not replace the newer one.
    """
    sync = entry.get("sync") or {}
    return (
        bool(sync)
        and tracked.get("skill_id") == sync.get("skill_id")
        and current == tracked.get("hash")
        and int(tracked.get("version") or 0) > int(sync.get("version") or 0)
    )


def _plan(root: Path, name: str, entry: dict[str, Any], relative: str, overwrite: bool) -> str:
    """What pulling one skill into one folder does."""
    from framework.ai import _shipped
    from framework.skill_sync import _load, _path, bundle_hash, package_status, scan, validate

    target = _path(root, relative)
    current = bundle_hash(scan(target)) if target.exists() else None
    if entry.get("package") is True:
        if not _shipped(name).is_dir():
            return "unavailable"
        status = package_status(_shipped(name), root, relative, name)
        status = {"create": "added", "update": "updated", "current": "current"}.get(
            status, "edited"
        )
    else:
        files = validate(entry["bundle"] if entry.get("sync") else _inline_bundle(entry))
        fresh = bundle_hash(files)
        if entry.get("sync") and fresh != entry["sync"]["hash"]:
            raise ValueError(f"Cloud skill verification failed: {name}")
        entries = _load(root)["skills"].values()
        tracked: dict[str, Any] = next((e for e in entries if relative in e["paths"]), {})
        clean = {tracked.get("hash"), entry.get("_setup_hash")} - {None}
        if current is None:
            status = "added"
        elif current == fresh or _newer(entry, tracked, current):
            status = "current"
        elif entry.get("sync") and current in clean:
            status = "updated"
        else:
            status = "edited"
    return "replaced" if status == "edited" and overwrite else status


def _apply(root: Path, name: str, entry: dict[str, Any], relative: str, status: str) -> None:
    from framework.ai import _shipped
    from framework.skill_sync import (
        _install,
        _install_cloud,
        _install_package,
        _path,
        bundle_hash,
        scan,
    )

    force = status == "replaced"
    if entry.get("package") is True:
        _install_package(_shipped(name), root, relative, name, force=force)
    elif entry.get("sync"):
        target = _path(root, relative)
        if (
            status == "current"
            and target.exists()
            and bundle_hash(scan(target)) != entry["sync"]["hash"]
        ):
            # Current with other files: a newer version than the setup's, kept as it is
            return
        clean = (entry["_setup_hash"],) if entry.get("_setup_hash") else ()
        _install_cloud(root, relative, entry, force=force, clean=clean)
    elif status != "current":
        target = _path(root, relative)
        _install(
            root,
            [relative],
            _inline_bundle(entry),
            bundle_hash(scan(target)) if target.exists() else None,
        )


def _summary(counts: dict[str, int], dry_run: bool) -> str:
    labels = [
        ("added", "added"),
        ("updated", "updated"),
        ("current", "up to date"),
        ("edited", "locally edited (kept)"),
        ("replaced", "replaced (backed up)"),
        ("removed", "no longer in the project's setup"),
        ("unavailable", "not in this version of framework-py"),
    ]
    parts = [f"{counts[key]} {label}" for key, label in labels if counts[key]]
    prefix = "Skills (dry run, nothing written): " if dry_run else "Skills: "
    return prefix + (", ".join(parts) or "none")


def pull_skills(
    project_path: str | Path = ".", overwrite: bool = False, dry_run: bool = False
) -> dict[str, Any]:
    """Bring a project's skills up to date with its setup on Framework Cloud.

    Reads the project's current skill list from Framework Cloud with its project token
    (``GET /api/v1/projects/pull``), as edited there with Edit project. Missing skills
    are added. Unedited skills are updated to their latest versions: Framework's from
    this framework-py, and Cloud skills from Cloud (their latest versions need
    ``fw.cloud_login()``; otherwise the versions in the project's setup are used).
    Locally edited skills are kept and listed; ``overwrite=True`` replaces them too,
    backing them up to ``.framework/skills-backups/``. Skills no longer in the setup are
    never deleted, only listed. The Skills table in AGENTS.md is refreshed.

    Offline, signed out, or without a project token, the package skills in the local
    settings are used instead, and the message says so.

    Args:
        project_path: The project's root directory. Default: the current directory.
        overwrite: Replace locally edited skills too, after backing them up.
        dry_run: Report what would change and write nothing.

    Returns:
        A dict: ``source`` ("cloud" or "package"), ``dry_run``, ``skills`` (each with
        ``name``, ``status``, and ``paths``), ``counts`` by status (added, updated,
        current, edited, replaced, removed, unavailable), ``message`` (the summary
        line), and ``notes``.

    Raises:
        ProjectNotFoundError: When the folder is not a Framework project.
    """
    from framework._agents_md import refresh_skills
    from framework._log import inform, show_messages
    from framework.ai import _installed_spec
    from framework.cloud import CloudError
    from framework.skill_sync import _lock
    from framework.spec.merge import SpecError

    show_messages()
    root = _root(project_path)
    spec = _installed_spec(root)
    dirs = _skill_dirs(root, spec)
    notes: list[str] = []
    try:
        wanted = _cloud_skills(root, notes)
        source = "cloud"
    except (CloudError, SpecError) as error:
        reason = str(error).rstrip(".") or type(error).__name__
        inform(
            f"Framework Cloud not reached ({reason}); "
            "using the package skills for this project type."
        )
        wanted = _package_skills(spec)
        source = "package"
        notes = []
    # A Cloud skill's version in the setup counts as unedited when a newer one replaces it
    for entry in wanted.values():
        if isinstance(entry, dict) and entry.get("sync"):
            entry.setdefault("_setup_hash", entry["sync"]["hash"])
    wanted = {n: e for n, e in wanted.items() if isinstance(e, dict)}

    skills: list[dict[str, Any]] = []
    with _lock(root) if not dry_run else nullcontext():
        for name, entry in wanted.items():
            found: list[tuple[str, str]] = []
            for folder in dirs:
                relative = f"{folder}/{name}"
                status = _plan(root, name, entry, relative, overwrite)
                if not dry_run and status in ("added", "updated", "replaced"):
                    _apply(root, name, entry, relative, status)
                elif not dry_run and status == "current":
                    _apply(root, name, entry, relative, status)  # records it in the lock
                found.append((relative, status))
            status = min((s for _, s in found), key=_PRECEDENCE.index)
            skills.append({"name": name, "status": status, "paths": [p for p, _ in found]})
    if source == "cloud":
        others: dict[str, list[str]] = {}
        for folder in dirs:
            for name in _folders(root, folder):
                if name not in wanted:
                    others.setdefault(name, []).append(f"{folder}/{name}")
        skills += [{"name": n, "status": "removed", "paths": p} for n, p in sorted(others.items())]

    if not dry_run and dirs:
        present = _folders(root, dirs[0])
        listed = [n for n in wanted if n in present] + [n for n in present if n not in wanted]
        refresh_skills(root, listed, dirs[0])

    counts = {key: sum(s["status"] == key for s in skills) for key in STATUSES}
    message = _summary(counts, dry_run)
    inform(message)
    for skill in skills:
        if skill["status"] == "edited":
            inform(
                f"  Kept (locally edited): {skill['name']} — "
                "pull_skills(overwrite=True) replaces it"
            )
        elif skill["status"] == "replaced":
            inform(f"  Replaced (old copy in {_BACKUPS}): {skill['name']}")
        elif skill["status"] == "removed":
            inform(f"  No longer in the project's setup (kept): {skill['name']}")
        elif skill["status"] == "unavailable":
            inform(f"  Not in this version of framework-py: {skill['name']}")
    for note in notes:
        inform(note)
    return {
        "source": source,
        "dry_run": dry_run,
        "skills": skills,
        "counts": counts,
        "message": message,
        "notes": notes,
    }


def _push_edited(root: Path) -> list[tuple[dict[str, Any], list[str]]]:
    """Tracked skills whose files changed since the last pull, with their present paths.

    Reads only the project; checking them against Cloud needs your login.
    """
    from framework.skill_sync import _load, _path, bundle_hash, scan

    edited = []
    for entry in _load(root)["skills"].values():
        paths = [p for p in entry["paths"] if _path(root, p).exists()]
        if not paths:
            continue
        if all(bundle_hash(scan(_path(root, p))) == entry.get("hash") for p in paths):
            continue
        edited.append((entry, paths))
    return edited


def _push_apply(root: Path, plans: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Push each planned skill; its status becomes pushed, refused, or failed."""
    from framework.skill_sync import skills_sync

    for plan in plans:
        if plan["status"] != "push":
            continue
        try:
            result = skills_sync(
                plan["path"],
                root,
                direction="push",
                resolve="local" if plan["forced"] else None,
            )
        except Exception as error:
            plan.update(status="failed", reason=str(error))
            continue
        if result["action"] == "push":
            plan.update(status="pushed", version=result.get("version"))
        else:
            plan["status"] = "refused"
    return plans


def _push_plan(root: Path, force: bool) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Locally edited tracked skills and what pushing each would do, and untracked folders."""
    from framework.ai import _installed_spec
    from framework.skill_sync import _load, skills_sync

    state = _load(root)
    tracked = {p for e in state["skills"].values() for p in e["paths"]}
    plans: list[dict[str, Any]] = []
    for entry, paths in _push_edited(root):
        name = Path(paths[0]).name
        builtin = not entry.get("skill_id") or str(entry["skill_id"]).startswith("framework:")
        try:
            action = skills_sync(paths[0], root, direction="status")["action"]
        except Exception as error:
            plans.append({"name": name, "path": paths[0], "status": "failed", "reason": str(error)})
            continue
        if action == "push" or (action == "conflict" and force):
            plans.append(
                {
                    "name": name,
                    "path": paths[0],
                    "status": "push",
                    "builtin": builtin,
                    "forced": action == "conflict",
                }
            )
        elif action == "conflict":
            plans.append({"name": name, "path": paths[0], "status": "refused"})
    untracked = [
        {"name": name, "path": f"{folder}/{name}", "status": "untracked"}
        for folder in _skill_dirs(root, _installed_spec(root))
        for name in _folders(root, folder)
        if f"{folder}/{name}" not in tracked
    ]
    return plans, untracked


def _warning(plan: dict[str, Any]) -> str:
    name = plan["name"]
    if plan["builtin"]:
        return f"This saves {name} as your own skill; the built-in Framework skill stays as it is."
    if plan["forced"]:
        return (
            f"Cloud has a newer version of {name} than this project pulled. Pushing replaces it "
            "as the latest version; the Cloud changes stay in its history."
        )
    return f"This replaces the current version of {name}, used wherever it's attached."


def push_skills(
    project_path: str | Path = ".",
    force: bool = False,
    confirm: bool | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Send locally edited skills to Framework Cloud as new versions of your own skills.

    Each skill tracked in ``.framework/skills.lock.json`` whose files changed since the
    last pull goes up through ``skills_sync()``. A skill that changed on Cloud since then
    is refused with "Pull first"; ``force=True`` pushes it anyway, as the latest version.
    A built-in Framework skill is never changed: an edited one is saved as your own copy.
    Skills not tracked are listed; ``skills_sync()`` adds one. Needs ``fw.cloud_login()``.

    Before pushing, a warning names what each new version affects, then asks to continue.
    Without a console, pass ``confirm=True``.

    Args:
        project_path: The project's root directory. Default: the current directory.
        force: Push skills that changed on Cloud since the last pull.
        confirm: True pushes without asking; False cancels. None asks in a console.
        dry_run: Report what would be pushed and send nothing.

    Returns:
        A dict: ``dry_run``, ``skills`` (each with ``name``, ``status``, and ``paths``;
        status is pushed, refused, untracked, cancelled, or failed), ``counts`` by status,
        and ``message`` (the summary line).

    Raises:
        CloudError: When not signed in to Framework Cloud.
        FrameworkError: Without a console and without ``confirm=True``.
    """
    from framework._log import inform, show_messages
    from framework.cloud import _require_token, cloud_server

    show_messages()
    root = _root(project_path).resolve()
    _require_token(cloud_server().url)
    plans, untracked = _push_plan(root, force)
    pushes = [p for p in plans if p["status"] == "push"]

    if pushes and not dry_run:
        for plan in pushes:
            inform(_warning(plan))
        if confirm is None:
            if not sys.stdin.isatty():
                raise FrameworkError(
                    "push_skills() asks before pushing. Without a console, pass confirm=True."
                )
            confirm = input("Continue? [y/n] ").strip().lower() in ("y", "yes")
        if not confirm:
            for plan in pushes:
                plan["status"] = "cancelled"
        else:
            _push_apply(root, plans)
    elif pushes:
        for plan in pushes:
            inform(_warning(plan))

    skills = [
        {
            "name": p["name"],
            "status": "pushed" if dry_run and p["status"] == "push" else p["status"],
            "paths": [p["path"]],
            **({"reason": p["reason"]} if "reason" in p else {}),
        }
        for p in plans + untracked
    ]
    keys = ("pushed", "refused", "untracked", "cancelled", "failed")
    counts = {key: sum(s["status"] == key for s in skills) for key in keys}
    labels = {
        "pushed": "to push" if dry_run else "pushed",
        "refused": "refused (pull first)",
        "untracked": "not tracked",
        "cancelled": "cancelled",
        "failed": "failed",
    }
    parts = [f"{counts[k]} {labels[k]}" for k in keys if counts[k]]
    prefix = "Skills (dry run, nothing sent): " if dry_run else "Skills pushed: "
    message = prefix + (", ".join(parts) or "nothing to push")
    inform(message)
    for skill in skills:
        if skill["status"] == "refused":
            inform(
                f"  Pull first: {skill['name']} changed on Cloud since this project pulled it. "
                "push_skills(force=True) replaces it."
            )
        elif skill["status"] == "untracked":
            inform(f"  Not tracked: {skill['name']} — use skills_sync() to add it")
        elif skill["status"] == "failed":
            inform(f"  Failed: {skill['name']}: {skill.get('reason')}")
    return {"dry_run": dry_run, "skills": skills, "counts": counts, "message": message}
