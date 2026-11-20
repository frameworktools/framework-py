"""A project's skills, kept in step with its setup on Framework Cloud.

``pull_skills()`` installs and updates the skills the project's Cloud setup lists.
``push_skills()`` sends locally edited skills back as new versions. ``skills_copy()``
copies skills from one assistant's folder to the others, so diverged copies agree
again, and ``skills_move()`` moves a tracked skill to another folder or identity.
Every command gathers what is on disk and what its sources offer, then calls the one
planner in ``framework._skill_plan`` (skills lock v2) and applies its steps.
R: ``pull_skills()``, ``push_skills()``, ``skills_copy()``, ``skills_move()``.
"""

from __future__ import annotations

import base64
import copy
import json
import os
import posixpath
import stat
import sys
from contextlib import nullcontext
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import quote

from framework import _skill_plan as planner
from framework._skill_plan import (
    ABSENT,
    UNREADABLE,
    Disk,
    Plan,
    SkillPlanError,
    identity_for,
    key_of,
    present,
)
from framework.errors import FrameworkError
from framework.skill_sync import _listed

STATUSES = (
    "added",
    "restored",
    "updated",
    "current",
    "edited",
    "replaced",
    "diverged",
    "failed",
    "kept",
    "missing",
    "untracked",
    "removed",
    "unavailable",
)
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
    """The enabled assistants' skills folders."""
    from framework.ai import assistant_targets
    from framework.skill_sync import on_disk

    # Only enabled assistants' folders: paths the lock tracks are planned from its entries
    folders, _ = assistant_targets(spec)
    # As spelled on disk, so they match the lock's paths
    return list(dict.fromkeys(on_disk(root, f) for f in folders))


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
                "sync": {
                    **source,
                    "version": remote["version"],
                    "hash": remote["hash"],
                    # Every version's hash, from the setup and from the snapshot
                    "known_hashes": sorted(
                        {
                            *(source.get("known_hashes") or []),
                            *(remote.get("known_hashes") or []),
                        }
                    ),
                },
                # The setup's version is a known hash too: a copy of it is unedited
                "_setup_hash": source["hash"],
            }


def _inline_bundle(entry: dict[str, Any]) -> list[dict[str, Any]]:
    files = {"SKILL.md": entry["content"], **(entry.get("files") or {})}
    return [
        {"path": path, "content": base64.b64encode(text.encode()).decode(), "executable": False}
        for path, text in files.items()
    ]


# Sources ---------------------------------------------------------------------------------


def _setup_source(name: str, entry: dict[str, Any]) -> dict[str, Any]:
    """One setup skill as a planner source: a package skill (this framework-py's copy),
    a Cloud skill (its bundle and version), or inline setup text."""
    from framework.skill_sync import VARIANT, bundle_hash, package_source, validate

    sync = entry.get("sync")
    if entry.get("package") is True:
        return {**package_source(name), "name": name}
    if sync and entry.get("bundle"):
        files = validate(entry["bundle"])
        if bundle_hash(files) != sync["hash"]:
            raise ValueError(f"Cloud skill verification failed: {name}")
        server = str(sync.get("server") or "").rstrip("/") or None
        known = {sync["hash"], *(sync.get("known_hashes") or [])}
        if entry.get("_setup_hash"):
            known.add(entry["_setup_hash"])
        return {
            "name": name,
            "identity": identity_for(str(sync["skill_id"]), sync.get("variant") or VARIANT, server),
            "channel": "cloud",
            "version": sync.get("version"),
            "server": server,
            "hash": sync["hash"],
            "known": sorted(known),
            "bundle": files,
        }
    files = validate(_inline_bundle(entry))
    digest = bundle_hash(files)
    return {
        "name": name,
        "identity": {"kind": "inline", "skill_id": f"inline:{name}", "variant": VARIANT},
        "channel": "inline",
        "version": None,
        "server": None,
        "hash": digest,
        "known": [digest],
        "bundle": files,
    }


def _pull_offer(root: Path, spec: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """What pull offers: the skills the project's Cloud setup lists (``source`` cloud),
    else, offline, the package skills for the project type (``source`` package)."""
    from framework._log import inform
    from framework.cloud import CloudError, ProjectServerMissingError
    from framework.spec.merge import SpecError

    notes: list[str] = []
    try:
        wanted = _cloud_skills(root, notes)
        source = "cloud"
    except ProjectServerMissingError:
        raise
    except (CloudError, SpecError) as error:
        reason = str(error).rstrip(".") or type(error).__name__
        inform(
            f"Framework Cloud not reached ({reason}); "
            "using the package skills for this project type."
        )
        wanted = _package_skills(spec)
        source = "package"
        notes = []
    skills = [_setup_source(n, e) for n, e in wanted.items() if isinstance(e, dict)]
    return {"source": source, "skills": skills, "by_identity": {}}, notes


def _fetch_by_identity(
    root: Path, lock: dict[str, Any], disk: Disk, offer: dict[str, Any], notes: list[str]
) -> None:
    """Your own Cloud skills the setup doesn't name, with every copy deleted: their
    latest versions, fetched by identity so pull can restore them. Needs your login;
    without it they are reported missing."""
    from framework.cloud import api, cloud_token
    from framework.skill_sync import bundle_hash, validate

    named = {key_of(s["identity"]) for s in offer["skills"]}
    for key, entry in lock["skills"].items():
        identity = entry["identity"]
        if key in named or identity["kind"] != "cloud" or not entry["copies"]:
            continue
        if any(disk.get(p) != ABSENT for p in entry["copies"]):
            continue
        server = str(identity.get("server") or "")
        token = cloud_token(server) if server else None
        if token is None:
            continue
        try:
            remote = api(
                f"/api/v1/skills/sync/{quote(identity['skill_id'], safe='')}"
                f"?variant={identity['variant']}",
                token,
                server,
            )
            files = validate(remote["bundle"])
            if bundle_hash(files) != remote["hash"]:
                raise ValueError("Cloud skill verification failed")
        except Exception as error:
            notes.append(f"{identity['skill_id']} not fetched from Cloud ({error}).")
            continue
        offer["by_identity"][key] = {
            "name": PurePosixPath(sorted(entry["copies"])[0]).name,
            "identity": dict(identity),
            "channel": "cloud",
            "version": remote["version"],
            "server": server,
            "hash": remote["hash"],
            "known": sorted({remote["hash"], *(remote.get("known_hashes") or [])}),
            "bundle": files,
        }


def _sources(offer: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Each source the offer holds, by the lock key it plans."""
    found = {key_of(s["identity"]): s for s in offer.get("skills") or []}
    found.update(offer.get("by_identity") or {})
    return found


# The project, as the planner sees it ------------------------------------------------------


def _hash_now(root: Path, relative: str) -> tuple[str, str | None]:
    """A copy's bundle hash, ``ABSENT``, or ``UNREADABLE`` (with why)."""
    from framework.skill_sync import _path, bundle_hash, scan

    try:
        kind = _kind(root, relative)
    except (FrameworkError, OSError) as error:
        return UNREADABLE, str(error)
    if kind == "missing":
        return ABSENT, None
    if kind == "other":
        return UNREADABLE, f"{relative} exists but is not a skill folder"
    try:
        return bundle_hash(scan(_path(root, relative))), None
    except (ValueError, OSError) as error:
        return UNREADABLE, str(error)


def _scan(root: Path, paths: Any, dirs: list[str]) -> tuple[Disk, dict[str, str]]:
    """What the planner may ask about: each path's hash (under its on-disk spelling),
    the folders that exist, and each skills folder's child folders. Also why each
    unreadable copy can't be read."""
    from framework.skill_sync import on_disk

    disk, errors = Disk(case_insensitive=_case_insensitive(root)), {}
    for folder in dirs:
        try:
            if _kind(root, folder) == "dir":
                disk.folders.add(folder)
                disk.children[folder] = _folders(root, folder)
        except (FrameworkError, OSError):
            continue
    every = set()
    for relative in set(paths):
        try:
            spelled = on_disk(root, relative)
        except (ValueError, OSError):
            spelled = relative
        if spelled != relative:
            disk.spelling[relative] = spelled
        every.add(spelled)
    every |= {f"{d}/{n}" for d in dirs for n in disk.children.get(d, [])}
    for relative in sorted(every):
        parent = posixpath.dirname(relative)
        while parent and parent not in disk.folders:
            try:
                if _kind(root, parent) != "dir":
                    break
            except (FrameworkError, OSError):
                break
            disk.folders.add(parent)
            parent = posixpath.dirname(parent)
        disk.hashes[relative], why = _hash_now(root, relative)
        if why:
            errors[relative] = why
    return disk, errors


def _case_insensitive(root: Path) -> bool:
    """Whether the project's volume folds case: its root under another case is the same
    folder."""
    other = Path(str(root).swapcase())
    if str(other) == str(root):
        return False
    try:
        return other.exists() and other.samefile(root)
    except OSError:
        return False


def _tracked_paths(lock: dict[str, Any]) -> list[str]:
    return [p for e in lock["skills"].values() for p in e["copies"]]


# Apply -----------------------------------------------------------------------------------


def _apply_moves(root: Path, steps: list[dict[str, Any]], then: Any = None) -> None:
    """Rename each moved folder, then run ``then`` (saving the lock). When a rename or
    ``then`` fails, the renames made are undone, newest first, and the error names any
    folder that couldn't be put back."""
    from framework.skill_sync import _path

    done: list[tuple[Path, Path]] = []
    try:
        for step in steps:
            source, target = _path(root, step["from"]), _path(root, step["path"])
            target.parent.mkdir(parents=True, exist_ok=True)
            source.rename(target)
            done.append((source, target))
        if then is not None:
            then()
    except OSError as error:
        stuck = []
        for source, target in reversed(done):
            try:
                target.rename(source)
            except OSError:
                stuck.append(
                    f"{target.relative_to(root).as_posix()} (belongs at "
                    f"{source.relative_to(root).as_posix()})"
                )
        undone = (
            f" Not moved back: {'; '.join(stuck)}." if stuck else " The moves made were undone."
        )
        raise FrameworkError(f"skills_move() failed ({error}).{undone}") from error


def _apply(
    root: Path,
    plan: Plan,
    lock: dict[str, Any],
    sources: dict[str, dict[str, Any]],
    disk: Disk,
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, str]]:
    """Carry out a plan's steps in path order, and return the steps as they ended, the
    lock to save, and each replaced copy's backup.

    Before each write or copy the copy is hashed again: one that changed since planning
    is left alone (``edited``, ``changed_during_pull``). A step that fails is ``failed``
    and the others go on. A declined copy keeps its old record, and its entry its old
    base; ``known`` still gains the source's hashes."""
    from framework.skill_sync import SkillRestoreError, _install, _path, bundle_hash, scan

    steps = sorted((dict(s) for s in plan.steps), key=lambda s: s["path"].encode())
    declined: list[dict[str, Any]] = []
    backups: dict[str, str] = {}
    _apply_moves(root, [s for s in steps if s["action"] == "move"])
    for step in steps:
        relative, action = step["path"], step["action"]
        if action not in ("write", "copy"):
            continue
        now, _ = _hash_now(root, relative)
        if now != disk.get(relative):
            step.update(
                action="none", status="edited", reason_code="changed_during_pull", hint=None
            )
            declined.append(step)
            continue
        try:
            if action == "write":
                files = sources[step["entry"]]["bundle"]
            else:
                files = scan(_path(root, step["from"]))
                if bundle_hash(files) != disk.get(step["from"]):
                    # The sibling it copies from changed since planning
                    step.update(
                        action="none",
                        status="edited",
                        reason_code="changed_during_pull",
                        hint=None,
                    )
                    declined.append(step)
                    continue
            installed = _install(root, [relative], files, None if now == ABSENT else now)
        except SkillRestoreError:
            raise
        except (ValueError, OSError, FrameworkError) as error:
            step.update(action="none", status="failed", reason_code=None, hint=None)
            step["error"] = str(error)
            declined.append(step)
            continue
        if installed.get(relative):
            backups[relative] = str(installed[relative])
    final = copy.deepcopy(plan.lock_after)
    for step in declined:
        key = step["entry"]
        old, new = lock["skills"].get(key), final["skills"].get(key)
        if new is None:
            continue
        if old is not None and step["path"] in old["copies"]:
            new["copies"][step["path"]] = dict(old["copies"][step["path"]])
        else:
            new["copies"].pop(step["path"], None)
        if old is not None:
            new["base"] = copy.deepcopy(old.get("base"))
            if old.get("pending"):
                new["pending"] = old["pending"]
        elif not new["copies"]:
            del final["skills"][key]
    return steps, final, backups


# Rows and their wording --------------------------------------------------------------------


def _identity_text(identity: dict[str, Any]) -> str:
    if identity["kind"] == "builtin":
        return f"the built-in {str(identity['skill_id']).split(':', 1)[1]} ({identity['variant']})"
    if identity["kind"] == "inline":
        return f"the setup's inline text for {str(identity['skill_id']).split(':', 1)[1]}"
    if identity["kind"] == "cloud":
        return f"Cloud skill {identity['skill_id']} ({identity['variant']})"
    return "a skill not yet pushed to Cloud"


def hint_text(
    hint: dict[str, Any] | None,
    overwrite: str = "pull_skills(overwrite=True)",
    force: str = "push_skills(force=True)",
) -> str | None:
    """A hint as the exact Python call that follows it, or None when no call can (a copy
    between folders of different names is made by hand)."""
    from framework.skill_sync import copy_call

    if not hint:
        return None
    key = hint["key"]
    if key == "overwrite":
        return overwrite
    if key == "force":
        return force
    if key == "login":
        return "cloud_login()"
    if key == "sync":
        return f"skills_sync({json.dumps(hint['path'])})"
    if key == "move":
        return f"skills_move({json.dumps(hint['from'])}, {json.dumps(hint['to'])})"
    if key == "retarget":
        identity, path = hint["identity"], json.dumps(hint["path"])
        if identity["kind"] == "builtin":
            name = json.dumps(str(identity["skill_id"]).split(":", 1)[1])
            return f"skills_move({path}, builtin={name}, variant={json.dumps(identity['variant'])})"
        return (
            f"skills_move({path}, skill_id={json.dumps(identity['skill_id'])}, "
            f"variant={json.dumps(identity['variant'])})"
        )
    if key == "copy":
        targets = list(hint.get("to") or [])
        source = hint.get("from") or (targets[0] if targets else None)
        if source is None:
            return None
        calls = [copy_call(source, t) for t in targets if t != source]
        return "; ".join(c for c in calls if c) if calls and all(calls) else None
    return None


def _reason(row: dict[str, Any], lock: dict[str, Any], errors: dict[str, str], verb: str) -> str:
    code, paths, name = row["reason_code"], row["paths"], row["name"]
    steps = row["steps"]
    path = paths[0] if paths else name
    hint = row.get("hint_args") or {}
    if row["status"] == "failed" and not code:
        return next((s["error"] for s in steps if s.get("error")), "The skill failed.")
    if code == "local_edits":
        edited = [s["path"] for s in steps if s["status"] == "edited"]
        verb_ = "has" if len(edited) == 1 else "have"
        return f"{_listed(edited)} {verb_} local edits, which were kept."
    if code == "replaced_edits":
        replaced = [s["path"] for s in steps if s["status"] == "replaced"]
        return f"{_listed(replaced)} had local edits; the old copy is in {_BACKUPS}."
    if code == "newer_in_project":
        return "The project holds a newer version than the setup names, so it was kept."
    if code == "copies_differ":
        return f"{_listed(paths)} differ."
    if code == "copy_absent":
        gone = list(hint.get("to") or [])
        return (
            f"{hint.get('from')} exists and {_listed(gone)} "
            f"{'is' if len(gone) == 1 else 'are'} missing."
        )
    if code in ("other_identity", "other_variant"):
        owner = (lock["skills"].get(row["entry"]) or {}).get("identity")
        if code == "other_variant" and owner:
            return (
                f"{path} tracks the {owner['variant']} variant of this skill, so {verb} "
                "left it alone."
            )
        theirs = (owner or {}).get("skill_id") or "a skill not yet pushed to Cloud"
        return f"{path} belongs to a different skill ({theirs}), so {verb} left it alone."
    if code == "cloud_tracked":
        return (
            f"{name} was last updated from Framework Cloud, so the package's copy wasn't installed."
        )
    if code == "untracked":
        return f"{path} isn't tracked, so {verb} left it alone."
    if code == "folder_gone":
        gone = [s["path"] for s in steps if s["reason_code"] == "folder_gone"]
        return f"{_listed(gone)} is tracked, and its folder is gone."
    if code == "all_gone":
        return "Every tracked copy is gone."
    if code == "not_in_setup":
        return "No longer in the project's setup, so pull left it alone."
    if code == "offline":
        return f"Framework Cloud wasn't reached, so {name} was left as it is."
    if code == "not_shipped":
        return f"This version of framework-py doesn't include {name}."
    if code == "setup_older":
        return (
            "The setup names an older version than this project holds, and no copy is "
            "left to copy from."
        )
    if code == "unreadable":
        bad = next((p for p in paths if p in errors), path)
        return f"{bad} can't be read as a skill ({errors.get(bad, 'not a valid skill')})."
    if code == "changed_during_pull":
        return f"{path} changed while the {verb} ran, so it was kept."
    if code == "cloud_changed":
        return _REFUSED
    if code == "first_push_pending":
        return (
            f"A first push of {name} stopped before Cloud replied, so it may already be on "
            f"Cloud. Check your Cloud skills: if it's there, skills_sync({json.dumps(path)}, "
            "skill_id=...) tracks it."
        )
    if code == "no_base":
        return f"{name} has no Cloud version this project pulled, since it was retargeted."
    if code == "cancelled":
        return "The push was cancelled."
    if code == "path_moved":
        return f"Moved {steps[0].get('from')} to {path}."
    if code == "lock_moved":
        return f"{path} was already in place; the lock now tracks it there."
    if code == "retargeted":
        identity = (lock["skills"].get(row["entry"]) or {}).get("identity")
        return f"{path} now tracks {_identity_text(identity) if identity else row['entry']}."
    return ""


def _rows(
    steps: list[dict[str, Any]],
    command: str,
    lock: dict[str, Any],
    errors: dict[str, str] | None = None,
    verb: str = "pull",
) -> list[dict[str, Any]]:
    """Result rows: the planner's rows, with each reason in words and each hint as the
    call to run (``hint``), beside their codes (``reason_code``, ``hint_key``, and
    ``hint_args``)."""
    out = []
    for row in planner.rows(steps, command):
        hint = row["hint"]
        shown = {
            "name": row["name"],
            "status": row["status"],
            "paths": row["paths"],
            "entry": row["entry"],
            "reason_code": row["reason_code"],
            "hint_key": hint["key"] if hint else None,
            "hint_args": {k: v for k, v in hint.items() if k != "key"} if hint else None,
            "steps": row["steps"],
        }
        shown["reason"] = _reason(shown, lock, errors or {}, verb)
        shown["hint"] = hint_text(hint)
        owner = (lock["skills"].get(row["entry"]) or {}).get("identity") or {}
        if row["status"] == "missing" and owner.get("kind") == "cloud":
            shown["skill_id"] = owner["skill_id"]
        out.append(shown)
    return out


def _public(row: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in row.items() if k != "steps"}


def row_text(
    row: dict[str, Any],
    overwrite: str = "pull_skills(overwrite=True)",
    force: str = "push_skills(force=True)",
) -> str:
    """A skill row's message, from its reason and hint, in the shared wording. Empty for
    a row that needs none."""
    reason = str(row.get("reason") or "")
    key = row.get("hint_key")
    hint = (
        hint_text({"key": key, **(row.get("hint_args") or {})}, overwrite, force)
        if key
        else row.get("hint")
    )
    if not reason:
        return ""
    if row.get("status") == "missing" and row.get("skill_id"):
        reason += f" Its last pushed version is Cloud skill {row['skill_id']}."
    if key == "copy":
        if hint is None:
            return (
                f"{reason} Their folders have different names, so skills_copy() can't copy "
                "between them: copy the one to keep over the others by hand."
            )
        if row.get("status") == "untracked":
            return f"{reason} To track it with this skill: {hint}"
        if row.get("reason_code") == "copy_absent":
            return f"{reason} Copy it over: {hint}"
        return f"{reason} Pick one and copy it over the other: {hint}"
    if key == "overwrite":
        return f"{reason} {hint} replaces them with the Cloud version."
    if key == "force":
        return f"{reason} {hint} pushes it anyway."
    if key == "move":
        return f"{reason} If it was moved by hand, record the move: {hint}"
    if key == "retarget":
        return f"{reason} To track it as the setup's skill: {hint}"
    if key == "sync":
        return f"{reason} To add it as its own skill: {hint}"
    if key == "login":
        return f"{reason} After {hint}, pull can get it from Cloud."
    if (
        not key
        and row.get("status") in ("missing", "untracked")
        and row.get("reason_code")
        in (
            "folder_gone",
            "untracked",
        )
    ):
        # Several folders could be the moved copy: the person says which
        return (
            f"{reason} If a skill folder was moved or renamed by hand, record the move: "
            "skills_move(old_path, new_path)."
        )
    return reason


push_text = row_text


def _summary(counts: dict[str, int], dry_run: bool) -> str:
    labels = [
        ("added", "added"),
        ("restored", "restored"),
        ("updated", "updated"),
        ("current", "up to date"),
        ("edited", "locally edited (kept)"),
        ("replaced", "replaced (backed up)"),
        ("diverged", "diverged (not pulled)"),
        ("failed", "failed"),
        ("kept", "kept (another skill)"),
        ("missing", "missing"),
        ("untracked", "kept, not tracked"),
        ("removed", "no longer in the project's setup"),
        ("unavailable", "unavailable (kept)"),
    ]
    parts = [f"{counts[key]} {label}" for key, label in labels if counts[key]]
    prefix = "Skills (dry run, nothing written): " if dry_run else "Skills: "
    return prefix + (", ".join(parts) or "none")


# Pull --------------------------------------------------------------------------------------


def _pull_run(
    root: Path,
    offer: dict[str, Any],
    opt: dict[str, Any],
    dry_run: bool,
    notes: list[str] | None = None,
) -> dict[str, Any]:
    """Plan a pull (or upgrade) against the project, and apply it unless ``dry_run``.
    The caller holds the sync lock for a real run."""
    from framework.skill_sync import _read, _save_changed

    lock, _ = _read(root)
    before = copy.deepcopy(lock)
    dirs = list(opt["dirs"])
    wanted = [f"{d}/{s['name']}" for s in offer["skills"] for d in dirs]
    disk, errors = _scan(root, [*_tracked_paths(lock), *wanted], dirs)
    if opt["command"] == "pull" and offer["source"] == "cloud":
        _fetch_by_identity(root, lock, disk, offer, notes if notes is not None else [])
    plan = planner.plan(disk, lock, offer, opt)
    if dry_run:
        return {"steps": plan.steps, "lock": lock, "errors": errors, "backups": {}}
    steps, final, backups = _apply(root, plan, lock, _sources(offer), disk)
    _save_changed(root, before, final)
    return {"steps": steps, "lock": final, "errors": errors, "backups": backups}


def pull_skills(
    project_path: str | Path = ".", overwrite: bool = False, dry_run: bool = False
) -> dict[str, Any]:
    """Bring a project's skills up to date with its setup on Framework Cloud.

    Reads the project's current skill list from Framework Cloud with its project token
    (``GET /api/v1/projects/pull``), as edited there with Edit project. Missing skills
    are added. Unedited skills are updated to their latest versions: Framework's from
    this framework-py, and Cloud skills from Cloud (their latest versions need
    ``fw.cloud_login()``; otherwise the versions in the project's setup are used).
    Skills no longer in the setup are never deleted, only listed.

    ``.framework/skills.lock.json`` records, for each copy of a skill, the hash pull last
    wrote there. A copy that still has it is unedited and is updated; any other copy is
    locally edited, even one reverted to an older version, and is kept and listed.
    ``overwrite=True`` replaces edited copies too, backing them up to
    ``.framework/skills-backups/``, but never moves a skill to an older version, and never
    chooses between copies that differ. A skill in several assistant folders is judged as
    one: when its copies differ, or one is missing beside an edited one, it is diverged
    and the message gives the ``skills_copy()`` call that fixes it. A folder another
    skill tracks is kept and never written. After a folder is renamed by hand, the
    message gives the ``skills_move()`` call that records it. Every copy is checked
    before anything is written, and a copy edited while the pull runs is kept, as
    edited. The Skills table in AGENTS.md is refreshed.

    Offline, signed out, or without a project token, the package skills in the local
    settings are used instead, and the message says so; your own Cloud skills are then
    listed as unavailable.

    Args:
        project_path: The project's root directory. Default: the current directory.
        overwrite: Replace locally edited skills too, after backing them up.
        dry_run: Report what would change and write nothing.

    Returns:
        A dict: ``source`` ("cloud" or "package"), ``dry_run``, ``skills`` (one row per
        skill, plus one per kept, untracked, or removed folder, each with ``name``,
        ``status``, ``paths``, ``reason`` and ``hint`` (words and the call to run), and
        ``reason_code``, ``hint_key``, and ``hint_args``), ``steps`` (one per copy, with
        its ``action``), ``counts`` by status, ``message`` (the summary line), and
        ``notes``.

    Raises:
        ProjectNotFoundError: When the folder is not a Framework project.
        ProjectServerMissingError: When ``.env`` has ``FW_PROJECT_TOKEN`` but no
            ``FW_CLOUD_URL``.
    """
    from framework._agents_md import refresh_skills
    from framework._log import inform, show_messages
    from framework.ai import _installed_spec
    from framework.skill_sync import _lock

    show_messages()
    root = _root(project_path)
    spec = _installed_spec(root)
    dirs = _skill_dirs(root, spec)
    for folder in dirs:
        if _kind(root, folder) == "other":
            raise FrameworkError(f"Can't install skills into {folder}: it is not a folder.")
    offer, notes = _pull_offer(root, spec)
    opt = {
        "command": "pull",
        "dirs": dirs,
        "overwrite": overwrite,
        "dry_run": dry_run,
        "offline": offer["source"] == "package",
        "scope": "all",
    }
    with _lock(root) if not dry_run else nullcontext():
        done = _pull_run(root, offer, opt, dry_run, notes)
    rows = _rows(done["steps"], "pull", done["lock"], done["errors"])

    if not dry_run and dirs:
        present = _folders(root, dirs[0])
        names = [s["name"] for s in offer["skills"]]
        listed = [n for n in names if n in present] + [n for n in present if n not in names]
        refresh_skills(root, listed, dirs[0])

    counts = {key: sum(r["status"] == key for r in rows) for key in STATUSES}
    message = _summary(counts, dry_run)
    inform(message)
    for row in rows:
        if row["status"] == "replaced":
            inform(f"  replaced: {row['name']} (old copy in {_BACKUPS})")
        elif text := row_text(row):
            inform(f"  {row['status']}: {row['name']}. {text}")
    for note in notes:
        inform(note)
    return {
        "source": offer["source"],
        "dry_run": dry_run,
        "skills": [_public(r) for r in rows],
        "steps": [dict(s) for s in done["steps"]],
        "counts": counts,
        "message": message,
        "notes": notes,
    }


# Upgrade and new projects --------------------------------------------------------------------


def upgrade_skills(root: Path, dirs: list[str], names: list[str]) -> dict[str, Any]:
    """What ``upgrade()`` does to the built-in skills: the package's copy of each named
    skill, planned as pull plans it (``scope`` builtin: your own skills are left out,
    and a built-in last updated from Cloud is kept). Returns ``rows``, ``steps``, and
    ``apply`` (carries the plan out; returns the paths it declined)."""
    from framework.skill_sync import _lock, _read, _save_changed, package_source

    offer = {"source": "package", "skills": [package_source(n) for n in names]}
    opt = {
        "command": "upgrade",
        "dirs": list(dirs),
        "overwrite": False,
        "offline": False,
        "scope": "builtin",
    }
    lock, _ = _read(root)
    before = copy.deepcopy(lock)
    wanted = [f"{d}/{n}" for n in names for d in dirs]
    disk, errors = _scan(root, [*_tracked_paths(lock), *wanted], list(dirs))
    plan = planner.plan(disk, lock, offer, opt)
    plan.steps = [s for s in plan.steps if s["status"] != "removed"]
    rows = _rows(plan.steps, "upgrade", lock, errors, verb="upgrade")

    def apply() -> list[str]:
        with _lock(root):
            steps, final, _ = _apply(root, plan, lock, _sources(offer), disk)
            _save_changed(root, before, final)
        return [s["path"] for s in steps if s["status"] in ("edited", "failed")]

    return {"rows": [_public(r) for r in rows], "steps": plan.steps, "apply": apply}


def install_setup_skills(root: Path, skills: dict[str, Any], dirs: list[str]) -> list[str]:
    """Install a new project's setup skills into its assistant folders and record them
    in the lock. Returns the paths that hold the setup's copy (an existing edited copy,
    or one another skill tracks, is left alone)."""
    from framework.skill_sync import _lock

    sources = [_setup_source(n, e) for n, e in skills.items() if isinstance(e, dict)]
    offer: dict[str, Any] = {"source": "cloud", "skills": sources, "by_identity": {}}
    # Planned as pull plans it; folders the setup doesn't name are not listed
    opt = {"command": "upgrade", "dirs": list(dirs), "overwrite": False, "scope": "all"}
    with _lock(root):
        done = _pull_run(root, offer, opt, False)
    held = {
        s["path"]
        for s in done["steps"]
        if s["status"] in ("added", "restored", "updated", "current", "replaced")
    }
    return [f"{d}/{s['name']}" for s in sources for d in dirs if f"{d}/{s['name']}" in held]


# Push --------------------------------------------------------------------------------------

_REFUSED = "Cloud has a newer version than this project pulled."


def _project_server(root: Path) -> str:
    """The server this project's skills sync with, found as pull finds it: the project's
    ``.env`` (FW_CLOUD_URL) when it names a project token or server, else your login."""
    from framework.cloud import _project_env, cloud_server

    if _project_env("FW_PROJECT_TOKEN", root) or _project_env("FW_CLOUD_URL", root):
        return cloud_server(scope="project", project_dir=root).url.rstrip("/")
    return cloud_server().url.rstrip("/")


def _push_login(root: Path) -> tuple[str, str]:
    """The server push sends to (the project's), and your login token for it."""
    from framework.cloud import _require_token

    server = _project_server(root)
    return server, _require_token(server)


def _edited_keys(lock: dict[str, Any], disk: Disk) -> list[str]:
    """Entries with a present copy that differs from its record."""
    return [
        key
        for key, entry in lock["skills"].items()
        if any(
            present(disk.get(p)) and disk.get(p) != (r or {}).get("hash")
            for p, r in entry["copies"].items()
        )
    ]


def _push_remote(
    root: Path, lock: dict[str, Any], keys: list[str], server: str, token: str | None
) -> tuple[dict[str, Any], dict[str, str]]:
    """Cloud's head for each entry about to be pushed (``version``, ``hash``,
    ``skill_id``), and why any couldn't be read. A built-in Cloud never published has
    no head."""
    from framework.cloud import CloudConflictError, api
    from framework.skill_sync import bundle_hash

    heads: dict[str, Any] = {}
    errors: dict[str, str] = {}
    for key in keys:
        entry = lock["skills"][key]
        identity, base = entry["identity"], entry.get("base") or {}
        if identity["kind"] == "cloud" and identity.get("server") != server:
            # Never send your token for this server to another one
            errors[key] = f"It tracks a skill on another server ({identity.get('server')})."
            continue
        if token is None or identity["kind"] not in ("cloud", "builtin"):
            continue
        path = f"{quote(identity['skill_id'], safe='')}?variant={identity['variant']}"
        if identity["kind"] == "builtin" and base.get("channel") == "package":
            # Cloud's copy of the version the package shipped, or 409 when Cloud never
            # published it. A built-in last delivered by Cloud reads Cloud's head.
            path += f"&hash={base['hash']}"
        where = server
        try:
            remote = api(f"/api/v1/skills/sync/{path}", token, where)
            if bundle_hash(remote["bundle"]) != remote["hash"]:
                raise ValueError("Cloud skill verification failed")
        except CloudConflictError:
            continue  # A built-in Cloud never published: an edited one is a new skill
        except Exception as error:
            errors[key] = str(error)
            continue
        heads[key] = {
            "version": remote["version"],
            "hash": remote["hash"],
            "skill_id": remote["skill_id"],
            "system": bool(remote.get("system")),
            "server": where,
        }
    return heads, errors


def _push_prepare(root: Path, force: bool, server: str, token: str | None) -> dict[str, Any]:
    """Plan a push: each tracked skill with local edits, and what pushing it would do."""
    from framework.ai import _installed_spec
    from framework.skill_sync import _read

    lock, _ = _read(root)
    dirs = _skill_dirs(root, _installed_spec(root))
    disk, errors = _scan(root, _tracked_paths(lock), dirs)
    remote, failures = _push_remote(root, lock, _edited_keys(lock, disk), server, token)
    plan = planner.plan(
        disk, lock, {"remote": remote}, {"command": "push", "dirs": dirs, "force": force}
    )
    for step in plan.steps:
        if step["entry"] in failures and step["status"] in ("pushed", "refused"):
            step.update(action="none", status="failed", reason_code=None, hint=None)
            step["error"] = failures[step["entry"]]
    return {
        "lock": lock,
        "disk": disk,
        "plan": plan,
        "remote": remote,
        "errors": errors,
        "server": server,
        "token": token,
    }


def _push_send(root: Path, prep: dict[str, Any]) -> list[dict[str, Any]]:
    """Push each planned skill; its steps become pushed or failed. Saves the lock."""
    from framework._skill_plan import dump
    from framework.skill_sync import (
        _lock,
        _path,
        _read,
        _save_changed,
        bundle_hash,
        post_push,
        scan,
    )

    plan: Plan = prep["plan"]
    steps = [dict(s) for s in plan.steps]
    lock = copy.deepcopy(plan.lock_after)
    with _lock(root):
        if dump(_read(root)[0]) != dump(prep["lock"]):
            raise SkillPlanError(
                "lock_changed",
                ".framework/skills.lock.json changed after this push was planned (another "
                "skill command ran). Nothing was sent. Run the push again.",
            )
        for key in dict.fromkeys(s["entry"] for s in steps if s["action"] == "push"):
            mine = [s for s in steps if s["entry"] == key]
            entry = lock["skills"][key]
            identity, base = entry["identity"], entry.get("base") or {}
            head = prep["remote"].get(key)
            forced = any(s.get("forced") for s in mine) or not base
            if identity["kind"] == "builtin":
                skill_id = head["skill_id"] if head else None
                expected = head["version"] if head else 0
            elif identity["kind"] == "cloud":
                skill_id = identity["skill_id"]
                expected = (head["version"] if head else 0) if forced else base.get("version")
            else:
                skill_id, expected = None, 0
            # Only the copies seen holding the pushed files take the new record
            disk = prep["disk"]
            holding = {p for p in entry["copies"] if disk.get(p) == mine[0]["hash"]}
            try:
                first = sorted(holding)[0]
                files = scan(_path(root, first))
                local = bundle_hash(files)
                if local != mine[0]["hash"]:
                    raise ValueError(f"{first} changed while the push ran")
                server = prep["server"]
                response = post_push(
                    root,
                    lock,
                    key,
                    files,
                    local,
                    int(expected or 0),
                    skill_id,
                    identity["variant"],
                    server,
                    prep["token"],
                )
                if response.get("bundle") is not None and (
                    bundle_hash(response["bundle"]) != response["hash"]
                ):
                    raise ValueError("Cloud skill verification failed")
            except Exception as error:
                for s in mine:
                    s.update(action="none", status="failed", reason_code=None, hint=None)
                    s["error"] = str(error)
                continue
            # Just before saving: the copies holding the pushed bytes now
            holding = {p for p in entry["copies"] if _hash_now(root, p)[0] == response["hash"]}
            lock = planner.after_push(
                lock,
                key,
                {
                    "skill_id": response["skill_id"],
                    "version": response["version"],
                    "hash": response["hash"],
                    "server": response.get("server") or server,
                },
                holding,
            )
            for s in mine:
                s["version"] = response["version"]
        _save_changed(root, prep["lock"], lock)
    return steps


def _warning(row: dict[str, Any]) -> str:
    name = row["name"]
    steps = row["steps"]
    if any(s.get("fork") for s in steps):
        return f"This saves {name} as your own skill; the built-in Framework skill stays as it is."
    if any(s.get("forced") for s in steps):
        return (
            f"Cloud has a newer version of {name} than this project pulled. Pushing replaces it "
            "as the latest version; the Cloud changes stay in its history."
        )
    return f"This replaces the current version of {name}, used wherever it's attached."


PUSH_STATUSES = ("pushed", "current", "refused", "untracked", "missing", "cancelled", "failed")


def push_skills(
    project_path: str | Path = ".",
    force: bool = False,
    confirm: bool | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Send locally edited skills to Framework Cloud as new versions of your own skills.

    Each skill tracked in ``.framework/skills.lock.json`` with a copy that differs from
    the hash the last sync wrote there is pushed. A skill that changed on Cloud since then
    is refused with "Pull first"; ``force=True`` pushes it anyway, as the latest version.
    So is a skill retargeted with ``skills_move()``, which has no version to push onto
    yet. A built-in Framework skill is never changed: an edited one is saved as your own
    skill, and the lock tracks it as that skill from then on. Folders the lock doesn't
    track are listed with the ``skills_sync()`` call that adds one, or, when a tracked
    copy's folder is gone, the ``skills_move()`` call that records a move. Needs
    ``fw.cloud_login()``. A skill whose copies in several assistant folders differ, or
    with a tracked copy missing, fails with the ``skills_copy()`` call that makes them
    agree; so does one whose files can't be read. The other skills are pushed.

    Before pushing, a warning names what each new version affects, then asks to continue.
    Without a console, pass ``confirm=True``.

    Args:
        project_path: The project's root directory. Default: the current directory.
        force: Push skills that changed on Cloud since the last pull.
        confirm: True pushes without asking; False cancels. None asks in a console.
        dry_run: Report what would be pushed and send nothing.

    Returns:
        A dict: ``dry_run``, ``skills`` (each with ``name``, ``status``, ``paths``,
        ``reason``, ``hint``, ``reason_code``, ``hint_key``, and ``hint_args``; status is
        pushed, current (Cloud already has it), refused, untracked, missing, cancelled, or
        failed), ``steps``, ``counts``
        by status, and ``message`` (the summary line).

    Raises:
        CloudError: When not signed in to Framework Cloud.
        FrameworkError: Without a console and without ``confirm=True``.
    """
    from framework._log import inform, show_messages

    show_messages()
    root = _root(project_path).resolve()
    server, token = _push_login(root)
    prep = _push_prepare(root, force, server, token)
    steps = prep["plan"].steps
    planned = _rows(steps, "push", prep["lock"], prep["errors"], verb="push")
    pushes = [r for r in planned if r["status"] == "pushed"]

    if pushes:
        for row in pushes:
            inform(_warning(row))
    if pushes and not dry_run:
        if confirm is None:
            if not sys.stdin.isatty():
                raise FrameworkError(
                    "push_skills() asks before pushing. Without a console, pass confirm=True."
                )
            confirm = input("Continue? [y/n] ").strip().lower() in ("y", "yes")
        if not confirm:
            steps = [dict(s) for s in steps]
            planner.cancel(steps)
        else:
            steps = _push_send(root, prep)
    elif not dry_run and any(s["action"] == "record" for s in steps):
        steps = _push_send(root, prep)  # Cloud already has the edits: record them

    rows = _rows(steps, "push", prep["lock"], prep["errors"], verb="push")
    counts = {key: sum(r["status"] == key for r in rows) for key in PUSH_STATUSES}
    labels = {
        "pushed": "to push" if dry_run else "pushed",
        "current": "already on Framework Cloud",
        "refused": "refused (pull first)",
        "untracked": "not tracked",
        "missing": "missing (every copy gone)",
        "cancelled": "cancelled",
        "failed": "failed",
    }
    parts = [f"{counts[k]} {labels[k]}" for k in PUSH_STATUSES if counts[k]]
    prefix = "Skills (dry run, nothing sent): " if dry_run else "Skills pushed: "
    message = prefix + (", ".join(parts) or "nothing to push")
    inform(message)
    for row in rows:
        if row["status"] not in ("pushed", "current"):
            inform(f"  {row['status']}: {row['name']}. {row_text(row)}")
    return {
        "dry_run": dry_run,
        "skills": [_public(r) for r in rows],
        "steps": steps,
        "counts": counts,
        "message": message,
    }


_RESERVED = ".framework"  # Framework's own state: the skill lock, sync lock, and backups


def _copy_name(name: str | None, directory: str | None, side: str) -> str | None:
    """One side's skills folder from an assistant name or a project-relative folder, checked
    as written. The file system is not looked at yet."""
    from framework.skill_sync import assistant_folders, relative_parts

    if name is not None:
        folders = assistant_folders()
        if name not in folders:
            known = ", ".join(f'"{n}"' for n in sorted(folders))
            raise FrameworkError(f'Unknown assistant folder "{name}". Use one of: {known}.')
        return folders[name]
    if directory is None:
        return None
    try:
        relative = "/".join(relative_parts(directory))
    except ValueError:
        relative = ""
    if not relative:
        raise FrameworkError(
            f"{side}_dir must name a folder inside the project, such as .claude/skills: "
            f"{directory!r}"
        )
    return relative


def _kind(root: Path, relative: str) -> str:
    """What a project-relative path is: "missing", "dir", or "other" (a file, or below one).
    Symlinks on the way are errors; so are lookups that fail for other reasons."""
    from framework.skill_sync import _path, lstat

    try:
        found = lstat(_path(root, relative))
    except NotADirectoryError:
        return "other"
    except ValueError:
        raise FrameworkError(f"Skill folders can't be or contain symlinks: {relative}") from None
    if found is None:
        return "missing"
    return "dir" if stat.S_ISDIR(found.st_mode) else "other"


def _exists(path: Path) -> bool:
    from framework.skill_sync import lstat

    try:
        return lstat(path) is not None
    except NotADirectoryError:
        return False


def _within(root: Path, inner: str, outer: str, fold: bool = False) -> bool:
    """Whether ``inner`` is ``outer`` or inside it: by path (case-folded with ``fold``),
    or by filesystem identity through existing folders, which catches case-insensitive
    volumes and other spellings of the same folder."""
    a, b = inner.split("/"), outer.split("/")
    if fold:
        a, b = [p.casefold() for p in a], [p.casefold() for p in b]
    if a[: len(b)] == b:
        return True
    target = root / outer
    if not _exists(target):
        return False
    path = root / inner
    for candidate in (path, *path.parents):
        if _exists(candidate) and candidate.samefile(target):
            return True
        if candidate == root:
            break
    return False


def _is_skill(path: Path, relative: str) -> bool:
    """Whether a folder holds a regular SKILL.md file. A symlinked one is an error."""
    from framework.skill_sync import lstat

    found = lstat(path / "SKILL.md")
    if found is not None and stat.S_ISLNK(found.st_mode):
        raise FrameworkError(f"Skill symlinks are unsupported: {relative}/SKILL.md")
    return found is not None and stat.S_ISREG(found.st_mode)


def _skill_names(root: Path, folder: str) -> list[str]:
    """The skills in a folder: immediate child folders holding a regular SKILL.md file.
    Other folders are ignored, and so are dot-folders (the installer stages copies in
    them). Unreadable folders, symlinked folders, and broken symlinks are errors."""
    from framework.skill_sync import _path

    names = []
    with os.scandir(_path(root, folder)) as entries:
        for entry in entries:
            if entry.name.startswith("."):
                continue
            relative = f"{folder}/{entry.name}"
            if entry.is_symlink():
                try:
                    pointed = Path(entry.path).stat()
                except FileNotFoundError:
                    raise FrameworkError(f"Broken symlink in a skills folder: {relative}") from None
                if stat.S_ISDIR(pointed.st_mode):
                    raise FrameworkError(f"Skill symlinks are unsupported: {relative}")
                continue
            if entry.is_dir(follow_symlinks=False) and _is_skill(Path(entry.path), relative):
                names.append(entry.name)
    return sorted(names)


def _copy_apply(root: Path, plans: list[dict[str, Any]], finish: Any = None) -> None:
    """Install each planned copy, then run ``finish`` (recording the joins). When either
    fails, the copies made are undone: each is moved to ``.framework/skills-backups/``
    (never deleted) and its old copy put back. When a copy can't be undone or put back,
    the error names it and its backup."""
    from framework.skill_sync import (
        SkillRestoreError,
        _install,
        _path,
        set_aside,
        set_aside_text,
    )

    done: list[dict[str, Any]] = []
    plan: dict[str, Any] = {}
    try:
        for plan in plans:
            if plan["status"] != "copied":
                continue
            installed = _install(root, [plan["to"]], plan["_files"], plan["_current"])
            plan["backup"] = installed[plan["to"]]
            done.append(plan)
        plan = {"from": "the copied skills", "to": "the skill lock"}
        if finish is not None:
            finish()
    except BaseException as error:
        failing = f"{plan['from']} -> {plan['to']}"
        stuck: list[tuple[str, str | None]] = []
        kept: list[tuple[str, str]] = []
        if isinstance(error, SkillRestoreError):
            stuck += error.stuck
        for copy in reversed(done):
            try:
                target = _path(root, copy["to"])
                if _exists(target):
                    kept.append(set_aside(root, target))
                if copy["backup"]:
                    _path(root, copy["backup"]).rename(target)
            except OSError:
                stuck.append((copy["to"], copy["backup"]))
        if stuck:
            detail = "; ".join(
                f"{to} (old copy in {backup})" if backup else f"{to} (new, no old copy)"
                for to, backup in stuck
            )
            raise SkillRestoreError(
                f"skills_copy() failed copying {failing} ({error}) and could not undo "
                f"every change. Not restored: {detail}.{set_aside_text(kept)}",
                stuck,
            ) from error
        undone = f"; the {len(done)} copies made before it were undone" if done else ""
        error.add_note(f"skills_copy() failed copying {failing}{undone}.{set_aside_text(kept)}")
        raise


def skills_copy(
    from_: str | None = None,
    to: str | None = None,
    *,
    from_dir: str | None = None,
    to_dir: str | None = None,
    skills: list[str] | None = None,
    dry_run: bool = False,
    project_path: str | Path = ".",
) -> dict[str, Any]:
    """Copy skills from one assistant's skills folder to the others.

    A project can keep the same skill in several assistant folders: ``.claude/skills``
    for Claude Code and ``.agents/skills`` for Codex and Copilot. ``pull_skills()``
    writes identical copies, but editing one makes them diverge. ``skills_sync()`` then
    stops, and ``push_skills()`` reports the skill as failed and goes on with the others.
    ``skills_copy()`` makes the copies agree: each skill's SKILL.md and its syncable
    companion files, with their executable modes, are copied from the source over each
    target.

    A skill is a folder holding a regular SKILL.md file. Other folders are ignored, and
    so are dot-folders, where the installer stages copies. Excluded
    files (such as ``.env``, keys, and ``.git``) are not copied, and stay in the target.
    Other files only the target had are removed from the active copy and kept in its
    backup: each replaced copy is backed up to ``.framework/skills-backups/`` first.
    Identical copies are left alone. Skills only a target has are left alone and not
    read: they are listed, never deleted. When a copied skill is tracked in
    ``.framework/skills.lock.json``, each target path joins its entry, so pull and push
    treat the copies as one skill; nothing else in the lock changes, so ``push_skills()``
    afterwards sees an edited skill and pushes it as usual. A dry run leaves the lock
    alone, and so does a copy of a skill the lock doesn't track.

    A real run holds the project's skill sync lock, then checks everything before
    anything is written. If a copy fails partway, the copies made before it are undone;
    if one can't be put back, the error names it and the backup holding its old copy.
    The target can't be in, or contain, ``.framework/``.

    Name folders by assistant (``from_``, ``to``) or by project-relative path
    (``from_dir``, ``to_dir``), not both. ``from_`` has a trailing underscore because
    ``from`` is a Python keyword.

    Examples:
        >>> fw.skills_copy(from_="claude", skills=["ggplot2-figures"], dry_run=True)
        >>> fw.skills_copy(from_="claude", to="agents")
        >>> fw.skills_copy(from_dir=".claude/skills", to_dir="some/other/skills")

    Args:
        from_: The source assistant folder: "claude" (``.claude/skills``) or "agents"
            (``.agents/skills``; "codex" and "copilot" are aliases).
        to: The target assistant folder, named the same way. Default: every other
            assistant skills folder in the project.
        from_dir: The source folder, relative to the project, in place of ``from_``.
        to_dir: The target folder, relative to the project, in place of ``to``.
        skills: The skills to copy. Default: every skill in the source.
        dry_run: Report what would be copied and write nothing.
        project_path: The project's root directory. Default: the current directory.

    Returns:
        A dict: ``dry_run``, ``from`` (the source folder), ``to`` (the target folders),
        ``skills`` (one per skill and target, each with ``name``, ``status``, ``from``,
        ``to``, and ``backup``; status is copied, current, or missing, which means not
        in the source, and then ``from`` is None; ``backup`` is the replaced copy's
        backup folder), ``counts`` by status, and ``message`` (the summary line).

    Raises:
        FrameworkError: When the arguments mix names and folders, name an unknown
            assistant or skill, or name folders that overlap each other or
            ``.framework/``; when the source folder is missing or a target isn't a
            folder; when there is no folder to copy to; when a skill folder is a
            symlink or a skills folder holds a broken one; or when a failed copy can't
            be undone (the message names each copy not put back and its backup).
        OSError: When a folder can't be read or looked up, such as a permission error.
        ProjectNotFoundError: When the folder is not a Framework project.
        ValueError: When another skill sync holds the project's skill lock.
    """
    from framework._log import inform, show_messages
    from framework.skill_sync import _lock, _read, _save_changed, assistant_folders, on_disk

    show_messages()
    for name, directory, label in ((from_, from_dir, "from_"), (to, to_dir, "to")):
        if name is not None and directory is not None:
            raise FrameworkError(f"Give {label} or {label.rstrip('_')}_dir, not both.")
    if (from_ is not None or to is not None) and (from_dir is not None or to_dir is not None):
        raise FrameworkError(
            "Name folders by assistant (from_, to) or by path (from_dir, to_dir), not both."
        )
    if skills is not None:
        if isinstance(skills, str):
            skills = [skills]
        if not skills:
            raise FrameworkError("skills is empty. Name a skill, or leave it out to copy all.")
    source = _copy_name(from_, from_dir, "from")
    if source is None:
        raise FrameworkError(
            'Name the folder to copy from: from_="claude", from_="agents", or from_dir=.'
        )
    target = _copy_name(to, to_dir, "to")
    root = _root(project_path)

    # A real run holds the sync lock before it looks at anything in the project
    with _lock(root) if not dry_run else nullcontext():
        # Folders are named as they are spelled on disk, in results and in the lock
        source = on_disk(root, source)
        target = on_disk(root, target) if target is not None else None
        if _within(root, source, _RESERVED, fold=True) or _within(
            root, _RESERVED, source, fold=True
        ):
            raise FrameworkError(
                f"Can't copy from {source}: it overlaps {_RESERVED}/, where Framework "
                "keeps its own state."
            )
        kind = _kind(root, source)
        if kind == "missing":
            raise FrameworkError(f"No skills folder to copy from: {source} does not exist.")
        if kind != "dir":
            raise FrameworkError(f"Can't copy from {source}: it is not a folder.")
        if target is not None:
            targets = [target]
        else:
            # By identity: a case alias of the source (.Claude) is the source, not a target
            targets = sorted(
                {
                    on_disk(root, f)
                    for f in set(assistant_folders().values())
                    if _kind(root, f) != "missing"
                    and not (_within(root, f, source) and _within(root, source, f))
                }
            )
            if not targets:
                raise FrameworkError(
                    f"Nothing to copy to: the project has no assistant skills folder other "
                    f"than {source}. Name one with to= or to_dir=."
                )
        for folder in targets:
            if _within(root, folder, _RESERVED, fold=True) or _within(
                root, _RESERVED, folder, fold=True
            ):
                raise FrameworkError(
                    f"Can't copy into {folder}: it overlaps {_RESERVED}/, where Framework "
                    "keeps its own state."
                )
            if _within(root, folder, source) and _within(root, source, folder):
                raise FrameworkError(f"The source and target are the same folder: {source}.")
            if _within(root, folder, source) or _within(root, source, folder):
                raise FrameworkError(
                    f"The source and target overlap: {source} and {folder}. "
                    "Neither may be inside the other."
                )
            if _kind(root, folder) == "other":
                raise FrameworkError(f"Can't copy into {folder}: it is not a folder.")

        names = _skill_names(root, source) if skills is None else list(dict.fromkeys(skills))
        # The lock is read and checked before any write: an unreadable one stops here
        lock, _ = _read(root)
        before = copy.deepcopy(lock)
        from framework.skill_sync import _path, bundle_hash, scan

        def state(relative: str, side: str) -> str:
            """A folder's hash, ABSENT, or UNREADABLE (not a skill folder). A skill folder
            that can't be read (a symlink, an invalid file, permissions) stops the copy
            with copy_source_unreadable or copy_target_unreadable."""
            kind = _kind(root, relative)
            if kind == "missing":
                return ABSENT
            if kind == "other" or not _is_skill(root / relative, relative):
                return UNREADABLE
            try:
                return bundle_hash(scan(_path(root, relative)))
            except (ValueError, OSError) as error:
                raise SkillPlanError(
                    f"copy_{side}_unreadable", f"{relative} can't be read ({error}).", relative
                ) from error

        disk = Disk(case_insensitive=_case_insensitive(root))
        for name in names:
            relative = on_disk(root, f"{source}/{name}")
            if relative != f"{source}/{name}":
                disk.spelling[f"{source}/{name}"] = relative
            disk.hashes[relative] = state(relative, "source")
        for folder in targets:
            disk.children[folder] = (
                _skill_names(root, folder) if _kind(root, folder) == "dir" else []
            )
            for name in names:
                # An existing target folder keeps its on-disk spelling (alpha for Alpha)
                relative = on_disk(root, f"{folder}/{name}")
                if relative != f"{folder}/{name}":
                    disk.spelling[f"{folder}/{name}"] = relative
                disk.hashes[relative] = state(relative, "target")
        plan = planner.plan(
            disk,
            lock,
            None,
            {
                "command": "copy",
                "from_dir": source,
                "to_dirs": targets,
                "names": names,
                "names_is_all": skills is None,
            },
        )
        copies = [
            {
                "name": s["name"],
                "status": s["status"],
                "from": s.get("from"),
                "to": s["path"],
                "backup": None,
                "_files": scan(_path(root, s["from"])) if s["action"] == "copy" else None,
                "_current": None if disk.get(s["path"]) == ABSENT else disk.get(s["path"]),
            }
            for s in plan.steps
        ]
        if not dry_run:
            _copy_apply(root, copies, lambda: _save_changed(root, before, plan.lock_after))

    results = [
        {
            **{k: v for k, v in c.items() if not k.startswith("_")},
            "paths": [c["to"]],
            "reason_code": None,
            "hint": None,
        }
        for c in copies
    ]
    keys = ("copied", "current", "missing")
    counts = {key: sum(r["status"] == key for r in results) for key in keys}
    labels = {
        "copied": "to copy" if dry_run else "copied",
        "current": "up to date",
        "missing": f"not in {source} (kept)",
    }
    parts = [f"{counts[k]} {labels[k]}" for k in keys if counts[k]]
    prefix = "Skills (dry run, nothing written)" if dry_run else "Skills"
    message = f"{prefix} from {source}: " + (", ".join(parts) or "none")
    inform(message)
    for result in results:
        if result["status"] == "copied":
            inform(f"  {'Would copy' if dry_run else 'Copied'}: {result['from']} -> {result['to']}")
            if result["backup"]:
                inform(f"    Backup: {result['backup']}")
        elif result["status"] == "missing":
            inform(f"  Not in {source} (kept): {result['to']}")
    return {
        "dry_run": dry_run,
        "from": source,
        "to": targets,
        "skills": results,
        "steps": [dict(s) for s in plan.steps],
        "counts": counts,
        "message": message,
    }


# Move --------------------------------------------------------------------------------------


def _move_path(value: str, side: str) -> str:
    """An assistant folder name ("claude", "agents") as its skills folder, else a
    project-relative path, checked as written."""
    from framework.skill_sync import assistant_folders, relative_parts

    folders = assistant_folders()
    if value in folders:
        return folders[value]
    try:
        relative = "/".join(relative_parts(value))
    except ValueError:
        relative = ""
    if not relative:
        raise FrameworkError(
            f'{side} must be an assistant folder ("claude", "agents") or a path inside '
            f"the project: {value!r}"
        )
    return relative


def skills_move(
    from_: str,
    to: str | None = None,
    *,
    skill_id: str | None = None,
    variant: str | None = None,
    builtin: str | None = None,
    project_path: str | Path = ".",
    dry_run: bool = False,
) -> dict[str, Any]:
    """Move a tracked skill to another folder, or switch the skill it tracks.

    ``.framework/skills.lock.json`` records where each copy of a skill lives. After a
    folder is moved (``.claude/skills`` to ``.agents/skills`` when you switch
    assistants), ``skills_move()`` moves the copies and their lock entries together, so
    pull and push keep treating them as the same skill with the same edits. A folder you
    already renamed by hand is recorded where it is now.

    With ``skill_id``, ``variant``, or ``builtin``, the copy is retargeted: the lock
    tracks it as that skill from then on (your fork of a built-in, say, or the r variant
    of a Cloud skill). Its unedited copies stay unedited, so the next ``pull_skills()``
    updates them to that skill's version. Its edited copies stay edited, and
    ``push_skills()`` refuses them until ``force=True``, since the new skill has no
    version this project pulled. ``pull_skills()`` gives the call to make when the
    setup names another skill than the one a folder tracks.

    Everything is checked before anything moves: a target folder that already exists,
    a target another skill tracks, or a copy the lock doesn't track stops the move. A
    dry run moves nothing and leaves the lock alone.

    Examples:
        >>> fw.skills_move("claude", "agents")
        >>> fw.skills_move(".claude/skills/team-style", ".agents/skills/team-style")
        >>> fw.skills_move(".claude/skills/framework-data", skill_id="5d0c2a7e-...")
        >>> fw.skills_move(".claude/skills/mine", variant="r")

    Args:
        from_: The copy to move (a project-relative skill folder), or a skills folder,
            whose tracked copies all move: a path, or an assistant folder name
            ("claude" for ``.claude/skills``, "agents" for ``.agents/skills``).
        to: Where it goes, named the same way. A skills folder keeps each skill's
            folder name. Leave it out to retarget only.
        skill_id: Retarget to this Cloud skill ID on your current server
            (``framework:<name>`` names a built-in).
        variant: Retarget to this variant: "r", "python", or "stata". Default: the
            copy's current variant.
        builtin: Retarget to this built-in Framework skill, such as "framework-data".
        project_path: The project's root directory. Default: the current directory.
        dry_run: Report the move and change nothing.

    Returns:
        A dict: ``dry_run``, ``skills`` (one row per moved or retargeted copy, each with
        ``name``, ``status`` "moved", ``paths``, ``reason``, and ``reason_code``:
        path_moved, lock_moved, or retargeted), ``steps``, and ``message``.

    Raises:
        SkillPlanError: When the move is refused; its ``code`` is move_untracked,
            move_target_owned, move_target_exists, move_nothing, or
            move_identity_exists. Nothing changes then.
        ProjectNotFoundError: When the folder is not a Framework project.
        ValueError: When another skill sync holds the project's skill lock.
    """
    from framework._log import inform, show_messages
    from framework.skill_sync import _lock, _read, _save_changed, on_disk

    show_messages()
    if variant is not None and variant not in ("r", "python", "stata"):
        raise FrameworkError('variant must be "r", "python", or "stata".')
    if skill_id is not None and builtin is not None:
        raise FrameworkError("Give skill_id or builtin, not both.")
    source = _move_path(from_, "from_")
    target = _move_path(to, "to") if to is not None else None
    if target is None and skill_id is None and variant is None and builtin is None:
        raise FrameworkError("Name where to move it (to=), or the skill to track (skill_id=).")
    root = _root(project_path)
    identity: dict[str, Any] | None = None
    if skill_id is not None or variant is not None or builtin is not None:
        identity = {"skill_id": skill_id, "variant": variant, "builtin": builtin}
        if skill_id and not skill_id.startswith(("framework:", "inline:")):
            identity["server"] = _project_server(root)

    with _lock(root) if not dry_run else nullcontext():
        source = on_disk(root, source)
        target = on_disk(root, target) if target is not None else None
        lock, _ = _read(root)
        before = copy.deepcopy(lock)
        tracked = _tracked_paths(lock)
        paths = {*tracked, source}
        if target is not None:
            paths.add(target)
            paths |= {
                f"{target}/{posixpath.basename(p)}"
                for p in tracked
                if posixpath.dirname(p) == source
            }
        disk, _ = _scan(root, paths, [])
        plan = planner.plan(
            disk,
            lock,
            None,
            {"command": "move", "from": source, "to": target, "identity": identity},
        )
        steps = plan.steps
        if not dry_run:
            # Only renames and lock records: the lock is saved right after the renames,
            # and a failed save undoes them
            steps = [dict(s) for s in plan.steps]
            _apply_moves(
                root,
                [s for s in steps if s["action"] == "move"],
                lambda: _save_changed(root, before, plan.lock_after),
            )
    rows = _rows(steps, "move", plan.lock_after)
    prefix = "Skills (dry run, nothing moved): " if dry_run else "Skills: "
    message = prefix + (f"{len(rows)} moved" if rows else "none")
    inform(message)
    for row in rows:
        inform(f"  {row['reason']}")
    return {
        "dry_run": dry_run,
        "skills": [_public(r) for r in rows],
        "steps": [dict(s) for s in steps],
        "message": message,
    }
