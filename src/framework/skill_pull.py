"""A project's skills, kept in step with its setup on Framework Cloud.

``pull_skills()`` installs and updates the skills the project's Cloud setup lists.
``push_skills()`` sends locally edited skills back as new versions. ``skills_copy()``
copies skills from one assistant's folder to the others, so diverged copies agree
again. All build on the per-skill machinery in ``framework.skill_sync``.
R: ``pull_skills()``, ``push_skills()``, ``skills_copy()``.
"""

from __future__ import annotations

import base64
import os
import stat
import sys
from contextlib import nullcontext
from pathlib import Path
from typing import Any
from urllib.parse import quote

from framework.errors import FrameworkError
from framework.skill_sync import same_identity

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
# Which status names a skill when its copies in several folders differ
_PRECEDENCE = (
    "failed",
    "diverged",
    "missing",
    "unavailable",
    "edited",
    "kept",
    "replaced",
    "restored",
    "updated",
    "added",
    "current",
)
_PROTECTED = "tied to a Cloud skill the project's setup doesn't name at this version"
_GONE = "Every tracked copy is gone."
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


def _hash_at(root: Path, relative: str) -> str | None:
    """The bundle hash of the copy at a path, or None when there is none. A file or
    anything else that isn't a folder there is an error."""
    from framework.skill_sync import _path, bundle_hash, scan

    kind = _kind(root, relative)
    if kind == "other":
        raise FrameworkError(
            f"{relative} exists but is not a skill folder. Move it, then pull again."
        )
    return bundle_hash(scan(_path(root, relative))) if kind == "dir" else None


def _last_pushed(entry: dict[str, Any]) -> dict[str, Any]:
    """The Cloud skill a missing entry was last pushed as, for its message."""
    return {"skill_id": entry["skill_id"]} if entry.get("skill_id") else {}


def row_text(row: dict[str, Any], overwrite: str = "pull_skills(overwrite=True)") -> str:
    """A skill row's message, from its reason and hint, in the shared wording. Empty for
    a row that needs none."""
    status = str(row["status"])
    reason, hint = str(row.get("reason") or ""), row.get("hint")
    if not reason:
        return ""
    if status == "diverged":
        if not hint:
            return (
                f"{reason} Their folders have different names, so skills_copy() can't copy "
                "between them: copy the one to keep over the others by hand."
            )
        if reason.endswith("missing."):
            return f"{reason} Copy it over: {hint}"
        return f"{reason} Pick one and copy it over the other: {hint}"
    if status == "missing":
        last = (
            f" Its last pushed version is Cloud skill {row['skill_id']}."
            if row.get("skill_id")
            else ""
        )
        return f"{reason}{last}" + (f" {hint}" if hint else "")
    if status == "untracked":
        return f"{reason} To track it with this skill: {hint}" if hint else reason
    if status == "edited" and hint:
        return f"{reason} {overwrite} replaces them with the Cloud version."
    if status == "restored" and hint:
        return f"{reason} To restore it there: {hint}"
    return reason


def _setup_key(state: dict[str, Any], name: str, entry: dict[str, Any]) -> str | None:
    """The lock key the setup's entry for a skill installs under: ``package:<name>`` for a
    package skill, the Cloud skill's key for a synced one, none for inline content."""
    from framework.skill_sync import cloud_key

    if entry.get("package") is True:
        return "package:" + name
    if entry.get("sync"):
        return cloud_key(state, entry["sync"])
    return None


def _setup_entry(
    root: Path, state: dict[str, Any], name: str, entry: dict[str, Any], dirs: list[str]
) -> tuple[str | None, dict[str, Any]]:
    """The lock entry that is the setup's skill, and its key: the entry under the setup's
    key; else the entry tracking the skill's path in an assistant folder. Never matched
    by folder name or bytes. ``(None, {})`` when the skill isn't tracked."""
    from framework.skill_sync import entry_for

    key = _setup_key(state, name, entry)
    if key is not None and isinstance(state["skills"].get(key), dict):
        return key, state["skills"][key]
    from framework.skill_sync import on_disk

    for folder in dirs:
        # As the lock reader spells it (on disk), so a case-only rename still matches
        try:
            path = on_disk(root, f"{folder}/{name}")
        except (ValueError, OSError):
            path = f"{folder}/{name}"
        found, tracked = entry_for(state, path)
        if found is not None:
            return found, tracked
    return None, {}


def _own_restore(
    root: Path, name: str, entry: dict[str, Any], tracked: dict[str, Any]
) -> dict[str, Any] | None:
    """When every copy of your own Cloud skill is gone and the setup names something else
    under its name, that skill's latest version from Cloud, as a setup entry to restore
    from. None when a copy exists, the setup names this skill, or Cloud can't be read
    (no login, offline, or a failed check)."""
    from urllib.parse import quote

    from framework.cloud import api, cloud_token
    from framework.skill_sync import bundle_hash

    if same_identity(tracked, entry.get("sync") or {}):
        return None
    if any(_hash_at(root, p) is not None for p in tracked["paths"]):
        return None
    server = str(tracked.get("server") or "")
    token = cloud_token(server) if server else None
    if token is None:
        return None
    try:
        remote = api(
            f"/api/v1/skills/sync/{quote(tracked['skill_id'], safe='')}"
            f"?variant={tracked.get('variant', 'python')}",
            token,
            server,
        )
        if bundle_hash(remote["bundle"]) != remote["hash"]:
            return None
    except Exception:
        return None
    return {
        "bundle": remote["bundle"],
        "sync": {
            "skill_id": remote["skill_id"],
            "version": remote["version"],
            "hash": remote["hash"],
            "variant": tracked.get("variant", "python"),
            "server": server,
        },
    }


def _plan(
    root: Path, name: str, entry: dict[str, Any], relative: str, overwrite: bool
) -> tuple[str, str | None, str | None]:
    """What pulling one skill into one folder does, the copy's hash it was decided on, and
    why the copy is protected (kept even with ``overwrite``), or None."""
    current = _hash_at(root, relative)
    status, protected = _decide(root, name, entry, relative, overwrite, current)
    return status, current, protected


def _decide(
    root: Path,
    name: str,
    entry: dict[str, Any],
    relative: str,
    overwrite: bool,
    current: str | None,
) -> tuple[str, str | None]:
    """A folder's status, and why its copy is protected (None when it isn't).

    Pull never replaces a skill's Cloud identity or moves it to an older version, whatever
    the setup's source (package, inline, or Cloud): a copy whose lock entry is tied to a
    Cloud skill is protected unless the setup names that same skill at the same or a newer
    version. With ``overwrite``, an edited copy of it is reset to that same skill's Cloud
    version (same or newer), after a backup. A protected copy is kept even with
    ``overwrite``; a missing one is not put back (unavailable). A missing copy the lock
    tracks is ``restored``, not ``added``.
    """
    from framework.ai import _shipped
    from framework.skill_sync import (
        _load,
        bundle_hash,
        entry_for,
        identified,
        package_status,
        validate,
    )

    _, tracked = entry_for(_load(root), relative)
    sync = entry.get("sync") or {}
    same = identified(tracked) and same_identity(tracked, sync)
    level = same and int(sync.get("version") or 0) == int(tracked.get("version") or 0)
    newer = same and int(sync.get("version") or 0) > int(tracked.get("version") or 0)
    protected = _PROTECTED if identified(tracked) and not (level or newer) else None
    other = protected is not None and not same  # Tied to another skill than the setup's
    if entry.get("package") is True:
        if not _shipped(name).is_dir():
            return "unavailable", None
        status = package_status(_shipped(name), root, relative, name)
        status = {"create": "added", "update": "updated", "current": "current"}.get(
            status, "edited"
        )
        unchanged = status == "current"
    else:
        files = validate(entry["bundle"] if sync else _inline_bundle(entry))
        fresh = bundle_hash(files)
        if sync and fresh != sync["hash"]:
            raise ValueError(f"Cloud skill verification failed: {name}")
        clean = {tracked.get("hash"), entry.get("_setup_hash")} - {None}
        if current is None:
            status = "added"
        elif current == fresh or _newer(entry, tracked, current):
            status = "current"
        elif sync and current in clean:
            status = "updated"
        else:
            status = "edited"
        unchanged = status == "current" or current == tracked.get("hash")
    if protected:
        if current is None:
            return "missing", _GONE
        if other:
            # Not the setup's skill: nothing was checked against it, so never "current"
            return "kept", (
                f"{relative} belongs to a different skill ({tracked.get('skill_id')}), "
                "so pull left it alone."
            )
        if unchanged:
            return "current", None
        return "edited", f"{relative} has local edits, which were kept."
    if status == "added" and relative in tracked.get("paths", []):
        status = "restored"
    return ("replaced" if status == "edited" and overwrite else status), None


def _entry_problems(root: Path, state: dict[str, Any]) -> dict[str, tuple[str, str, str | None]]:
    """Each lock entry pull must leave alone, by key: ``("diverged", hint)`` when its
    copies differ or one of them is missing, ``("failed", reason)`` when one can't be
    read. Judged per entry: entries never pool paths, whatever their folder names."""
    from framework.skill_sync import diverged_parts

    problems: dict[str, tuple[str, str, str | None]] = {}
    for key, entry in state["skills"].items():
        paths = list(entry["paths"])
        try:
            hashes = [_hash_at(root, p) for p in paths]
        except (ValueError, OSError, FrameworkError) as error:
            problems[key] = ("failed", str(error), None)
            continue
        existing = [h for h in hashes if h is not None]
        if len(paths) > 1 and existing and (None in hashes or len(set(existing)) > 1):
            problems[key] = ("diverged", *diverged_parts(paths, hashes))
        elif paths and not existing:
            problems[key] = ("missing", _GONE, None)
    return problems


def _new_folder_hint(
    root: Path,
    state: dict[str, Any],
    name: str,
    wanted: dict[str, Any],
    key: str | None,
    tracked: dict[str, Any],
    new: list[str],
    dirs: list[str],
    overwrite: bool,
) -> tuple[str, str | None] | None:
    """The reason and command when pull would add a skill to a new assistant folder
    (``new``) while its existing copy is locally edited or your own Cloud skill (or newer
    than the setup's): the setup's copy there would split the skill. None when the new
    folders can be filled. With ``overwrite``, an edited copy that isn't your own Cloud skill is
    replaced, so the new folders are filled with the setup's copy too."""
    from framework.ai import _shipped
    from framework.skill_sync import (
        bundle_hash,
        diverged_parts,
        identified,
        package_status,
        validate,
    )

    if not new:
        return None
    entry = wanted[name]
    if key is not None:
        copies = [(p, h) for p in tracked["paths"] if (h := _hash_at(root, p)) is not None]
        if not copies:
            return None
        owned = identified(tracked)
        edited = any(h != tracked.get("hash") for _, h in copies)
        sync = entry.get("sync") or {}
        if (
            owned
            and not edited
            and same_identity(tracked, sync)
            and int(sync.get("version") or 0) >= int(tracked.get("version") or 0)
        ):
            owned = False  # The setup's copy of the same Cloud skill
        if owned or (edited and not overwrite):
            return diverged_parts([copies[0][0], new[0]], [copies[0][1], None])
        return None
    # Not tracked: a copy in another assistant folder counts, when it isn't the setup's
    for folder in dirs if not overwrite else []:
        there = f"{folder}/{name}"
        if there in new or (h := _hash_at(root, there)) is None:
            continue
        if entry.get("package") is True:
            edited = _shipped(name).is_dir() and (
                package_status(_shipped(name), root, there, name) == "edited"
            )
        else:
            files = entry["bundle"] if entry.get("sync") else _inline_bundle(entry)
            edited = h != bundle_hash(validate(files))
        if edited:
            return diverged_parts([there, new[0]], [h, None])
    return None


def _apply(
    root: Path, name: str, entry: dict[str, Any], relative: str, status: str, planned: str | None
) -> str:
    """Carry out one planned install, and return the status it ends with.

    The plan was made for every folder against the lock as it was before the pull, so
    the installers are forced: an install into one folder advances the lock entry the
    other folders share, and must not make them decline. Right before installing, the
    copy is hashed again; one that changed since planning is kept, as edited.
    """
    from framework.ai import _shipped
    from framework.skill_sync import (
        _install,
        _install_cloud,
        _install_package,
        _load,
        entry_for,
        identified,
    )

    now = _hash_at(root, relative)
    if now != planned:
        return "edited"
    if status == "current":
        if entry.get("sync") and now == entry["sync"]["hash"]:
            # The same bytes: record the identity and version the setup names. Nothing
            # is rewritten; an older version or another skill's identity is declined.
            clean = (entry["_setup_hash"],) if entry.get("_setup_hash") else ()
            if _install_cloud(root, relative, entry, force=True, clean=clean):
                return status
        _, tracked = entry_for(_load(root), relative)
        if relative in tracked.get("paths", []) or identified(tracked):
            # Already recorded, or a copy of a Cloud skill the lock tracks elsewhere: an
            # untracked path never joins an entry because its bytes match
            return status
    if entry.get("package") is True:
        done = _install_package(_shipped(name), root, relative, name, force=True)
    elif entry.get("sync"):
        if status == "current" and now is not None and now != entry["sync"]["hash"]:
            # Current with other files: a newer version than the setup's, kept as it is
            return status
        clean = (entry["_setup_hash"],) if entry.get("_setup_hash") else ()
        done = _install_cloud(root, relative, entry, force=True, clean=clean)
    else:
        if status != "current":
            _install(root, [relative], _inline_bundle(entry), now)
        done = True
    if not done:
        raise FrameworkError(f"pull_skills() could not install {relative}; nothing was replaced.")
    return status


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
    never deleted, only listed. Every skill and folder is checked before anything is
    written, and a copy edited while the pull runs is kept, as edited. A skill in several
    assistant folders is updated in all of them. A skill whose tracked copies differ, or
    with one of them missing, is diverged: it is not touched, and the message gives the
    ``skills_copy()`` call that fixes it. So is a skill pull would add to a new assistant
    folder while its existing copy is locally edited or your own Cloud skill. Pull works
    per lock entry: a copy the lock doesn't track is left alone, and an entry that is
    diverged or can't be read (failed) never changes another entry's plan. Pull never
    changes a skill's Cloud identity or moves it to an older version. The Skills table
    in AGENTS.md is refreshed. To make a skill's copies in several assistant folders
    agree again, use ``skills_copy()``.

    Offline, signed out, or without a project token, the package skills in the local
    settings are used instead, and the message says so.

    Args:
        project_path: The project's root directory. Default: the current directory.
        overwrite: Replace locally edited skills too, after backing them up.
        dry_run: Report what would change and write nothing.

    Returns:
        A dict: ``source`` ("cloud" or "package"), ``dry_run``, ``skills`` (each with
        ``name``, ``status``, and ``paths``), ``counts`` by status (added, updated,
        current, edited, replaced, diverged, failed, removed, unavailable; a diverged
        skill also has ``hint``, a failed one ``reason``; a problem in a lock entry that
        isn't the setup's skill is listed with its ``entry`` key), ``message`` (the summary
        line), and ``notes``.

    Raises:
        ProjectNotFoundError: When the folder is not a Framework project.
        ProjectServerMissingError: When ``.env`` has ``FW_PROJECT_TOKEN`` but no
            ``FW_CLOUD_URL``.
    """
    from framework._agents_md import refresh_skills
    from framework._log import inform, show_messages
    from framework.ai import _installed_spec
    from framework.cloud import CloudError, ProjectServerMissingError
    from framework.skill_sync import _load, _lock, copy_call, entry_for, identified, on_disk
    from framework.spec.merge import SpecError

    show_messages()
    root = _root(project_path)
    spec = _installed_spec(root)
    dirs = _skill_dirs(root, spec)
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
    # A Cloud skill's version in the setup counts as unedited when a newer one replaces it
    for entry in wanted.values():
        if isinstance(entry, dict) and entry.get("sync"):
            entry.setdefault("_setup_hash", entry["sync"]["hash"])
    wanted = {n: e for n, e in wanted.items() if isinstance(e, dict)}

    skills: list[dict[str, Any]] = []
    with _lock(root) if not dry_run else nullcontext():
        for folder in dirs:
            if _kind(root, folder) == "other":
                raise FrameworkError(f"Can't install skills into {folder}: it is not a folder.")
        # Planned per lock entry, against the lock as it is now, and checked (Cloud
        # verification, bundle limits, folders) before anything is written. The setup's
        # skill is one entry: its tracked paths (custom folders too) move together, and
        # it can be added to new assistant folders. A path another entry tracks, or an
        # untracked copy beside a tracked skill, is left alone. A diverged or unreadable
        # entry isn't touched, and never changes another entry's plan.
        from framework.skill_sync import bundle_hash, validate

        for name, entry in wanted.items():
            # A Cloud bundle that fails verification stops the whole pull, before any write
            sync = entry.get("sync")
            if sync and bundle_hash(validate(entry["bundle"])) != sync["hash"]:
                raise ValueError(f"Cloud skill verification failed: {name}")
        state = _load(root)
        problems = _entry_problems(root, state)
        tracked_paths = {p for e in state["skills"].values() for p in e["paths"]}
        claimed: set[str] = set()
        targets: dict[str, list[str]] = {}
        left_out: dict[str, list[str]] = {}
        plans: dict[str, list[tuple[str, str, str | None, str | None]]] = {}
        sources: dict[str, dict[str, Any]] = {}
        for name, entry in wanted.items():
            key, tracked = _setup_entry(root, state, name, entry, dirs)
            if key is not None:
                claimed.add(key)
            if key in problems and problems[key][0] != "missing":
                status, reason, hint = problems[key]
                skills.append(
                    {
                        "name": name,
                        "status": status,
                        "paths": list(tracked["paths"]),
                        "reason": reason,
                        "hint": hint,
                    }
                )
                continue
            if source == "package" and identified(tracked):
                # Package content never replaces Cloud content, and nothing here can check it
                skills.append(
                    {
                        "name": name,
                        "status": "unavailable",
                        "paths": list(tracked["paths"]),
                        "reason": f"Framework Cloud wasn't reached, so {name} was left as it is.",
                        "hint": None,
                    }
                )
                continue
            try:
                if key is not None and identified(tracked):
                    entry = _own_restore(root, name, entry, tracked) or entry
                # Each assistant folder's path for the skill, as spelled on disk
                at = {d: on_disk(root, f"{d}/{name}") for d in dirs}
                paths: list[str] = []
                for path in tracked.get("paths", []):
                    if _kind(root, Path(path).parent.as_posix()) == "dir" or path in at.values():
                        paths.append(path)
                    else:
                        left_out.setdefault(name, []).append(path)
                new = [
                    at[d]
                    for d in dirs
                    if at[d] not in tracked_paths and _kind(root, at[d]) == "missing"
                ]
                if key is None:
                    # Nothing tracked yet: each assistant folder's copy, as before the lock
                    paths = [at[d] for d in dirs if at[d] not in tracked_paths]
                else:
                    # A copy the lock doesn't track, beside the tracked skill, is left alone
                    for d in dirs:
                        there = at[d]
                        if there in tracked_paths or _kind(root, there) == "missing":
                            continue
                        home = (tracked.get("paths") or [there])[0]
                        back = copy_call(there, home)
                        again = copy_call(home, there)
                        skills.append(
                            {
                                "name": name,
                                "status": "untracked",
                                "paths": [there],
                                "entry": None,
                                "reason": f"{there} isn't tracked, so pull left it alone.",
                                # Copy it where the lock tracks the skill, then record it
                                "hint": f"{back}; {again}" if back and again else None,
                            }
                        )
                    if not paths:
                        # Every tracked copy is gone with its folder: never refilled
                        # elsewhere, so the lock can't move while a tracked copy is absent
                        gone = left_out.pop(name, [])
                        skills.append(
                            {
                                "name": name,
                                "status": "missing",
                                "paths": gone,
                                "reason": _GONE,
                                "hint": None,
                                **_last_pushed(tracked),
                            }
                        )
                        continue
                split = _new_folder_hint(
                    root, state, name, wanted, key, tracked, new, dirs, overwrite
                )
                if split:
                    skills.append(
                        {
                            "name": name,
                            "status": "diverged",
                            "paths": paths + new,
                            "reason": split[0],
                            "hint": split[1],
                        }
                    )
                    continue
                if (
                    key is not None
                    and identified(tracked)
                    and entry is wanted[name]
                    and not same_identity(tracked, entry.get("sync") or {})
                    and all(_hash_at(root, p) is None for p in paths)
                ):
                    # Your own Cloud skill, every copy gone, and Cloud not reached
                    skills.append(
                        {
                            "name": name,
                            "status": "missing",
                            "paths": paths,
                            "reason": _GONE,
                            "hint": None,
                            **_last_pushed(tracked),
                        }
                    )
                    continue
                paths += [p for p in new if p not in paths]
                targets[name] = paths
                sources[name] = entry  # The setup's entry, or the Cloud skill it restores from
                plans[name] = [(path, *_plan(root, name, entry, path, overwrite)) for path in paths]
            except (ValueError, OSError, FrameworkError) as error:
                skills.append(
                    {
                        "name": name,
                        "status": "failed",
                        "paths": list(tracked.get("paths", [])),
                        "reason": str(error),
                        "hint": None,
                    }
                )
                plans.pop(name, None)
        # Problems in entries that aren't the setup's skills: listed on their own
        for key, (status, reason, hint) in problems.items():
            if key in claimed:
                continue  # The setup's skill: planned above (a missing one is restored)
            paths = list(state["skills"][key]["paths"])
            skills.append(
                {
                    "name": Path(paths[0]).name,
                    "status": status,
                    "paths": paths,
                    "entry": key,
                    "reason": reason,
                    "hint": hint,
                    **(_last_pushed(state["skills"][key]) if status == "missing" else {}),
                }
            )
        writes = ("added", "restored", "updated", "replaced", "current")
        for name, planned in plans.items():
            if not planned:
                # Nowhere to install: each folder's path belongs to another skill
                there = next((at for at in (f"{d}/{name}" for d in dirs)), name)
                owner = next(
                    (e.get("skill_id") for e in state["skills"].values() if there in e["paths"]),
                    None,
                )
                skills.append(
                    {
                        "name": name,
                        "status": "kept",
                        "paths": [],
                        "reason": f"{there} belongs to a different skill ({owner}), so pull "
                        "left it alone.",
                        "hint": None,
                    }
                )
                continue
            found: list[tuple[str, str]] = []
            for relative, status, current, _ in planned:
                if not dry_run and status in writes:
                    # A current one is recorded in the lock
                    status = _apply(root, name, sources[name], relative, status, current)
                found.append((relative, status))
            status = min((s for _, s in found), key=_PRECEDENCE.index)
            skill: dict[str, Any] = {
                "name": name,
                "status": status,
                "paths": [p for p, _ in found],
            }
            reasons = [protected for *_, protected in planned if protected]
            if reasons and status in ("edited", "missing", "kept"):
                # Protected: overwrite doesn't change it, so there is no command to give
                skill["reason"], skill["hint"] = reasons[0], None
                if status == "missing":
                    skill.update(_last_pushed(entry_for(state, skill["paths"][0])[1]))
            elif status == "edited":
                path = next(p for p, s in found if s == "edited")
                skill["reason"] = f"{path} has local edits, which were kept."
                skill["hint"] = "pull_skills(overwrite=True)"
            elif status == "unavailable":
                skill["reason"] = f"This version of framework-py doesn't include {name}."
                skill["hint"] = None
            restored = [p for p, s in found if s == "restored"]
            if restored:
                skill["restored"] = restored
                left = left_out.get(name, [])
                if left:
                    calls = [copy_call(restored[0], p) for p in left]
                    skill["reason"] = (
                        f"Restored {', '.join(restored)}; {', '.join(left)} is tracked too, "
                        "and its folder is gone."
                    )
                    skill["hint"] = "; ".join(c for c in calls if c) if all(calls) else None
            skills.append(skill)
    if source == "cloud":
        others: dict[str, list[str]] = {}
        for folder in dirs:
            for name in _folders(root, folder):
                if name in wanted:
                    continue
                here = root / folder / name
                # The same folder under another spelling is a wanted skill, not removed
                if any(
                    (root / folder / w).exists() and (root / folder / w).samefile(here)
                    for w in wanted
                ):
                    continue
                others.setdefault(name, []).append(f"{folder}/{name}")
        skills += [
            {
                "name": n,
                "status": "removed",
                "paths": p,
                "reason": "No longer in the project's setup, so pull left it alone.",
                "hint": None,
            }
            for n, p in sorted(others.items())
        ]

    if not dry_run and dirs:
        present = _folders(root, dirs[0])
        listed = [n for n in wanted if n in present] + [n for n in present if n not in wanted]
        refresh_skills(root, listed, dirs[0])

    counts = {key: sum(s["status"] == key for s in skills) for key in STATUSES}
    message = _summary(counts, dry_run)
    inform(message)
    for skill in skills:
        if skill["status"] == "replaced":
            inform(f"  replaced: {skill['name']} (old copy in {_BACKUPS})")
        elif text := row_text(skill):
            inform(f"  {skill['status']}: {skill['name']}. {text}")
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


_REFUSED = "Cloud has a newer version than this project pulled."


def push_text(row: dict[str, Any], force: str = "push_skills(force=True)") -> str:
    """A push row's message, from its reason and hint, in the shared wording."""
    status, reason, hint = row["status"], str(row.get("reason") or ""), row.get("hint")
    if status == "refused":
        return f"{reason} {force} pushes it anyway."
    if status == "missing":
        last = row.get("skill_id")
        return reason + (f" Its last pushed version is Cloud skill {last}." if last else "")
    if status == "untracked":
        return f"{reason} To add it as its own skill: {hint}"
    if status == "failed" and hint:
        if reason.endswith("missing."):
            return f"{reason} Copy it over: {hint}"
        return f"{reason} Pick one and copy it over the other: {hint}"
    return reason


def _push_edited(
    root: Path,
) -> list[tuple[dict[str, Any], list[str], tuple[str, str, str | None] | None]]:
    """Tracked skills whose files changed since the last pull, with their present paths,
    and why each can't be pushed (None when it can be tried).

    A skill whose tracked copies differ, or with one tracked copy missing, is diverged and
    can't be pushed; nor can one whose files can't be read. Reads only the project;
    checking the rest against Cloud needs your login.
    """
    from framework.skill_sync import _load, diverged_parts

    edited: list[tuple[dict[str, Any], list[str], tuple[str, str, str | None] | None]] = []
    for entry in _load(root)["skills"].values():
        paths = list(entry["paths"])
        try:
            hashes = [_hash_at(root, p) for p in paths]
        except (ValueError, OSError, FrameworkError) as error:
            edited.append((entry, paths, ("failed", str(error), None)))
            continue
        present = [p for p, h in zip(paths, hashes, strict=True) if h is not None]
        existing = [h for h in hashes if h is not None]
        if not present:
            if paths:
                edited.append((entry, paths, ("missing", _GONE, None)))
            continue
        if None in hashes or len(set(existing)) > 1:
            edited.append((entry, present, ("failed", *diverged_parts(paths, hashes))))
        elif any(h != entry.get("hash") for h in existing):
            edited.append((entry, present, None))
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
            plan.update(status="failed", reason=str(error), hint=None)
            continue
        if result["action"] == "push":
            plan.update(status="pushed", version=result.get("version"))
        else:
            plan.update(status="refused", reason=_REFUSED, hint="push_skills(force=True)")
    return plans


def _push_plan(root: Path, force: bool) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Locally edited tracked skills and what pushing each would do, and untracked folders."""
    from framework.ai import _installed_spec
    from framework.skill_sync import _load, skills_sync

    state = _load(root)
    tracked = {p for e in state["skills"].values() for p in e["paths"]}
    plans: list[dict[str, Any]] = []
    for entry, paths, problem in _push_edited(root):
        name = Path(paths[0]).name
        builtin = not entry.get("skill_id") or str(entry["skill_id"]).startswith("framework:")
        if problem:
            status, reason, hint = problem
            plans.append(
                {
                    "name": name,
                    "path": paths[0],
                    "status": status,
                    "reason": reason,
                    "hint": hint,
                    **(_last_pushed(entry) if status == "missing" else {}),
                }
            )
            continue
        try:
            action = skills_sync(paths[0], root, direction="status")["action"]
        except Exception as error:
            plans.append(
                {
                    "name": name,
                    "path": paths[0],
                    "status": "failed",
                    "reason": str(error),
                    "hint": None,
                }
            )
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
            plans.append(
                {
                    "name": name,
                    "path": paths[0],
                    "status": "refused",
                    "reason": _REFUSED,
                    "hint": "push_skills(force=True)",
                }
            )
    untracked = [
        {
            "name": name,
            "path": f"{folder}/{name}",
            "status": "untracked",
            "reason": f"{folder}/{name} isn't tracked, so push left it alone.",
            "hint": f'skills_sync("{folder}/{name}")',
        }
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
    A skill whose copies in several assistant folders differ, or with a tracked copy
    missing, fails with the ``skills_copy()`` call that makes them agree; so does one
    whose files can't be read. The other skills are pushed.

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

    skills = [push_row(p, dry_run) for p in plans + untracked]
    keys = ("pushed", "refused", "untracked", "missing", "cancelled", "failed")
    counts = {key: sum(s["status"] == key for s in skills) for key in keys}
    labels = {
        "pushed": "to push" if dry_run else "pushed",
        "refused": "refused (pull first)",
        "untracked": "not tracked",
        "missing": "missing (every copy gone)",
        "cancelled": "cancelled",
        "failed": "failed",
    }
    parts = [f"{counts[k]} {labels[k]}" for k in keys if counts[k]]
    prefix = "Skills (dry run, nothing sent): " if dry_run else "Skills pushed: "
    message = prefix + (", ".join(parts) or "nothing to push")
    inform(message)
    for skill in skills:
        if skill["status"] not in ("pushed",):
            inform(f"  {skill['status']}: {skill['name']}. {push_text(skill)}")
    return {"dry_run": dry_run, "skills": skills, "counts": counts, "message": message}


def push_row(plan: dict[str, Any], dry_run: bool = True) -> dict[str, Any]:
    """A push plan as a result row: ``push`` is reported as ``pushed`` (a dry run says so
    with dry_run=True), and every other status carries ``reason`` and ``hint``."""
    status = "pushed" if plan["status"] == "push" else plan["status"]
    row = {"name": plan["name"], "status": status, "paths": [plan["path"]]}
    if status != "pushed":
        row["reason"] = plan.get("reason") or (
            "The push was cancelled." if status == "cancelled" else ""
        )
        row["hint"] = plan.get("hint")
        if plan.get("skill_id"):
            row["skill_id"] = plan["skill_id"]
    return row


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


def _copy_plan(
    root: Path,
    source: str,
    targets: list[str],
    names: list[str],
    everything: bool,
    state: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Scan every source and target copy, and what copying does to each, before any write.
    Skills only a target has are listed, not read."""
    from framework.skill_sync import _path, bundle_hash, on_disk, scan

    sources = {name: scan(_path(root, f"{source}/{name}")) for name in names}
    available = set(_skill_names(root, source))
    plans: list[dict[str, Any]] = []
    for folder in targets:
        present = _skill_names(root, folder) if _kind(root, folder) == "dir" else []
        copies: list[Path] = []
        for name in names:
            # An existing target folder keeps its on-disk spelling (alpha for Alpha)
            relative = on_disk(root, f"{folder}/{name}")
            dest = _path(root, relative)
            kind = _kind(root, relative)
            current = None
            if state is not None:
                from framework.skill_sync import entry_for

                theirs = entry_for(state, relative)[0]
                mine = entry_for(state, f"{source}/{name}")[0]
                # A copy another skill tracks is never replaced, nor its path moved. An
                # untracked copy may still fill a tracked path whose copy is gone.
                if theirs is not None and theirs != mine and (mine is not None or kind == "dir"):
                    raise FrameworkError(
                        f"{relative} belongs to another skill in the lock, so skills_copy() "
                        "won't replace it. Nothing was copied."
                    )
            if kind == "other" or (kind == "dir" and not _is_skill(dest, relative)):
                raise FrameworkError(
                    f"{relative} exists but is not a skill folder (no SKILL.md file). "
                    "Move it, then copy again."
                )
            if kind == "dir":
                current = bundle_hash(scan(dest))
                copies.append(dest)
            files = sources[name]
            plans.append(
                {
                    "name": name,
                    "status": "current" if current == bundle_hash(files) else "copied",
                    "from": f"{source}/{name}",
                    "to": relative,
                    "backup": None,
                    "_files": files,
                    "_current": current,
                }
            )
        if everything:
            # Compared by identity, as the copy found them: on a case-insensitive volume
            # alpha and Alpha are one folder, and one row
            plans += [
                {
                    "name": n,
                    "status": "missing",
                    "from": None,
                    "to": f"{folder}/{n}",
                    "backup": None,
                }
                for n in present
                if n not in available
                and not any((root / folder / n).samefile(dest) for dest in copies)
            ]
    return plans


def _copy_apply(root: Path, plans: list[dict[str, Any]], finish: Any = None) -> None:
    """Install each planned copy, then run ``finish`` (recording the joins). When either
    fails, the copies made are undone. When a copy can't be undone or put back, the
    error names it and its backup."""
    import shutil

    from framework.skill_sync import SkillRestoreError, _install, _path

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
        if isinstance(error, SkillRestoreError):
            stuck += error.stuck
        for copy in reversed(done):
            try:
                target = _path(root, copy["to"])
                if _exists(target):
                    shutil.rmtree(target)
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
                f"every change. Not restored: {detail}.",
                stuck,
            ) from error
        undone = f"; the {len(done)} copies made before it were undone" if done else ""
        error.add_note(f"skills_copy() failed copying {failing}{undone}.")
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
    from framework.skill_sync import _lock, assistant_folders, on_disk

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

        available = _skill_names(root, source)
        if skills is None:
            names = available
        else:
            names = list(dict.fromkeys(skills))
            unknown = [n for n in names if n not in available]
            if unknown:
                raise FrameworkError(f"Not skills in {source}: {', '.join(unknown)}.")
        # The lock is read and checked before any write: an unreadable one stops here
        from framework.skill_sync import _load, join_paths

        state = _load(root)
        plans = _copy_plan(root, source, targets, names, everything=skills is None, state=state)
        if not dry_run:

            def record() -> None:
                # The copies are the source's skill: each target joins its lock entry
                # (when it has one), so pull and push treat them as one skill from now on
                for name in names:
                    join_paths(
                        root,
                        f"{source}/{name}",
                        [
                            p["to"]
                            for p in plans
                            if p["name"] == name and p["status"] in ("copied", "current")
                        ],
                    )

            _copy_apply(root, plans, record)

    results = [{k: v for k, v in p.items() if not k.startswith("_")} for p in plans]
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
        "counts": counts,
        "message": message,
    }
