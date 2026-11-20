"""Skills lock v2: the lock's schema, its migration from v1, and the one planner every
skill command calls.

Everything here is pure: no file is read and no request is made. A command gathers the
planner's inputs (what is on disk, the lock, and what its sources offer), calls
``plan()``, then shows or applies the steps and saves ``lock_after``. The rules are R's
``docs/architecture/skills-lock-v2.md``; ``tests/shared/conformance/skills`` holds the
cases every language runs.
"""

from __future__ import annotations

import copy
import posixpath
from dataclasses import dataclass, field
from typing import Any

from framework.errors import FrameworkError

SCHEMA = 2
ABSENT = "\0absent"
UNREADABLE = "\0unreadable"

# A row's status is the first of its steps' statuses in this order
PRECEDENCE = (
    "failed",
    "diverged",
    "missing",
    "unavailable",
    "kept",
    "untracked",
    "edited",
    "replaced",
    "updated",
    "restored",
    "added",
    "current",
    # Push, copy, and move rows hold one status each
    "refused",
    "cancelled",
    "pushed",
    "copied",
    "moved",
    "removed",
)
# Steps with these statuses are rows of their own, apart from their entry's row
_OWN_ROW = ("untracked", "kept", "removed")


class SkillPlanError(FrameworkError, ValueError):
    """A skill command refused before writing anything. ``code`` is the shared error
    code (``move_target_exists``, ``copy_target_owned``, ...); ``path`` the path it
    names, when it names one."""

    def __init__(self, code: str, message: str, path: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.path = path


# Identities and keys ---------------------------------------------------------------------


def key_of(identity: dict[str, Any], first_path: str | None = None) -> str:
    """An entry's lock key, derived from its identity."""
    kind, variant = identity["kind"], identity["variant"]
    if kind == "builtin" or kind == "inline":
        return f"{identity['skill_id']}@{variant}"
    if kind == "cloud":
        return f"{str(identity['server']).rstrip('/')}/{identity['skill_id']}@{variant}"
    return f"local:{identity.get('path') or first_path}"


def identity_for(skill_id: str, variant: str, server: str | None) -> dict[str, Any]:
    """The identity a skill ID names: ``framework:<name>`` is a built-in,
    ``inline:<name>`` inline setup text, anything else a Cloud skill on ``server``."""
    if skill_id.startswith("framework:"):
        return {"kind": "builtin", "skill_id": skill_id, "variant": variant}
    if skill_id.startswith("inline:"):
        return {"kind": "inline", "skill_id": skill_id, "variant": variant}
    return {
        "kind": "cloud",
        "skill_id": skill_id,
        "variant": variant,
        "server": str(server or "").rstrip("/"),
    }


def _entry(identity: dict[str, Any]) -> dict[str, Any]:
    return {"identity": identity, "base": None, "known": [], "copies": {}}


def owner_of(lock: dict[str, Any], path: str) -> str | None:
    """The key of the entry tracking a path, or None. Paths are compared as spelled;
    commands spell them as they are on disk first, so this is filesystem identity."""
    for key, entry in lock["skills"].items():
        if path in entry["copies"]:
            return str(key)
    return None


def empty_lock() -> dict[str, Any]:
    return {"schema": SCHEMA, "skills": {}}


# Migration -------------------------------------------------------------------------------


def _from_v1(entry: dict[str, Any]) -> dict[str, Any]:
    variant = entry.get("variant") or "r"
    found = entry.get("origin")
    origin: dict[str, Any] = found if isinstance(found, dict) else {}
    paths = [p for p in entry.get("paths") or [] if isinstance(p, str)]
    skill_id = entry.get("skill_id")
    if skill_id:
        server = str(entry.get("server") or "").rstrip("/")
        identity = identity_for(str(skill_id), variant, server)
        if identity["kind"] == "inline":  # Never written by v1; read as a Cloud ID
            identity = {"kind": "cloud", "skill_id": skill_id, "variant": variant, "server": server}
        base: dict[str, Any] | None = {
            "channel": "cloud",
            "hash": entry.get("hash"),
            "version": entry.get("version"),
            "server": server or None,
        }
    elif str(origin.get("skill_id") or "").startswith("framework:"):
        identity = {"kind": "builtin", "skill_id": origin["skill_id"], "variant": variant}
        base = {"channel": "package", "hash": origin.get("hash"), "version": None, "server": None}
    else:
        identity = {"kind": "local", "variant": variant, "path": paths[0] if paths else ""}
        base = None
    if base is not None and base["hash"] is None:
        base = None
    record = entry.get("hash") or origin.get("hash")
    out = _entry(identity)
    out["base"] = base
    out["known"] = sorted({h for h in (entry.get("hash"), origin.get("hash")) if h})
    out["copies"] = {p: {"hash": record} for p in paths}
    if isinstance(entry.get("pending"), dict):
        out["pending"] = entry["pending"]
    return out


def _mergeable(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """Whether two entries for one identity can be one: the same base, and at most one
    with a pending push."""
    return a.get("base") == b.get("base") and not (a.get("pending") and b.get("pending"))


def _merge_v2(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    """Two mergeable entries for one identity: every copy, with its own record, and
    every known hash."""
    out = copy.deepcopy(a)
    for path, record in b["copies"].items():
        out["copies"].setdefault(path, dict(record))
    out["known"] = sorted(set(a["known"]) | set(b["known"]))
    if "pending" not in out and b.get("pending"):
        out["pending"] = b["pending"]
    return out


def _cloud_base(entry: dict[str, Any]) -> bool:
    return (entry.get("base") or {}).get("channel") == "cloud"


def migrate(state: Any) -> dict[str, Any]:
    """A lock as v2, from a v1 or v2 lock (already parsed). Pure; runs on every read.
    Keys are rebuilt from identities. A lock newer than v2 raises."""
    if not isinstance(state, dict) or not isinstance(state.get("skills"), dict):
        raise ValueError("Unsupported skill lock file")
    schema = state.get("schema")
    if isinstance(schema, int) and schema > SCHEMA:
        raise LockSchemaError(schema)
    entries: list[tuple[str | None, dict[str, Any]]]
    if schema == 1:
        entries = [(str(k), _from_v1(e)) for k, e in state["skills"].items() if isinstance(e, dict)]
    elif schema == SCHEMA:
        entries = [
            (str(k), _clean_v2(e)) for k, e in state["skills"].items() if isinstance(e, dict)
        ]
    else:
        raise ValueError("Unsupported skill lock file")
    out = empty_lock()
    sources: dict[str, str] = {}
    for stored, entry in entries:
        identity = entry["identity"]
        if identity["kind"] == "local":
            # Keyed by its first path at creation, which the identity can't rebuild
            path = identity.pop("path", None)
            key = stored if stored and stored.startswith("local:") else f"local:{path}"
        else:
            key = key_of(identity)
        if key in out["skills"]:
            if not _mergeable(out["skills"][key], entry):
                raise SkillPlanError(
                    "lock_migrate_conflict",
                    f"The skills lock has two entries for {key} at different versions: "
                    f"{sources[key]!r} and {stored!r}. Delete the entry for the copy you "
                    "don't keep from .framework/skills.lock.json, then run the command again.",
                )
            entry = _merge_v2(out["skills"][key], entry)
        sources.setdefault(key, str(stored))
        out["skills"][key] = entry
    return out


def _clean_v2(entry: dict[str, Any]) -> dict[str, Any]:
    identity = dict(entry.get("identity") or {})
    if identity.get("kind") not in ("builtin", "cloud", "inline", "local"):
        raise ValueError("Unsupported skill lock file")
    identity.setdefault("variant", "r")
    found = entry.get("copies")
    copies: dict[str, Any] = found if isinstance(found, dict) else {}
    out = _entry(identity)
    out["base"] = entry.get("base") if isinstance(entry.get("base"), dict) else None
    out["known"] = sorted({h for h in entry.get("known") or [] if isinstance(h, str)})
    out["copies"] = {
        str(p): {"hash": (r or {}).get("hash") if isinstance(r, dict) else None}
        for p, r in copies.items()
    }
    if isinstance(entry.get("pending"), dict):
        out["pending"] = entry["pending"]
    return out


class LockSchemaError(FrameworkError, ValueError):
    """The lock file is newer than this framework-py reads."""

    code = "lock_schema_unsupported"

    def __init__(self, schema: int) -> None:
        from framework import __version__

        super().__init__(
            f"This project's skills lock (.framework/skills.lock.json) uses schema {schema}. "
            f"This framework-py ({__version__}) reads schema {SCHEMA} and older: update "
            "framework-py to its latest version."
        )
        self.schema = schema


def dump(lock: dict[str, Any]) -> dict[str, Any]:
    """The lock in the form it is saved: entries sorted by key, copies by path, and
    ``known`` sorted and unique. An entry with no copies is dropped, except a Cloud
    entry, which ``skills_move()`` can attach a copy to later."""
    skills = {}
    for key in sorted(lock["skills"]):
        entry = lock["skills"][key]
        if not entry["copies"] and entry["identity"]["kind"] != "cloud":
            continue  # No copies left; a Cloud entry stays for skills_move()
        out: dict[str, Any] = {
            "identity": dict(entry["identity"]),
            "base": entry.get("base"),
            "known": sorted(set(entry.get("known") or [])),
            "copies": {p: dict(entry["copies"][p]) for p in sorted(entry["copies"])},
        }
        if entry.get("pending"):
            out["pending"] = entry["pending"]
        skills[key] = out
    return {"schema": SCHEMA, "skills": skills}


# Planner inputs ----------------------------------------------------------------------------


@dataclass
class Disk:
    """What the planner may ask about the project. ``hashes``: path -> bundle hash,
    ``ABSENT``, or ``UNREADABLE``. ``folders``: folders that exist. ``children``: each
    skills folder's child folders, by name. ``spelling``: a path the planner builds
    from a name (``.claude/skills/team-style``) -> its spelling on disk
    (``.claude/skills/Team-Style`` on a case-insensitive volume), so paths compare by
    filesystem identity."""

    hashes: dict[str, str] = field(default_factory=dict)
    folders: set[str] = field(default_factory=set)
    children: dict[str, list[str]] = field(default_factory=dict)
    spelling: dict[str, str] = field(default_factory=dict)
    case_insensitive: bool = False

    def spelled(self, path: str) -> str:
        return self.spelling.get(path, path)

    def alias(self, path: str) -> str:
        """The form two paths share when they are one folder on this volume."""
        return path.casefold() if self.case_insensitive else path

    def get(self, path: str) -> str:
        return self.hashes.get(path, ABSENT)

    def folder_exists(self, folder: str) -> bool:
        return folder in ("", ".") or folder in self.folders

    def skill_folders(self, folder: str) -> list[str]:
        return list(self.children.get(folder, []))


def present(value: str) -> bool:
    return value not in (ABSENT, UNREADABLE)


@dataclass
class Copy:
    path: str
    state: str
    disk: str


@dataclass
class Plan:
    steps: list[dict[str, Any]] = field(default_factory=list)
    lock_after: dict[str, Any] = field(default_factory=empty_lock)
    kept_paths: set[str] = field(default_factory=set)
    planned: set[str] = field(default_factory=set)

    def step(
        self,
        path: str,
        entry: str | None,
        action: str,
        status: str,
        reason: str | None = None,
        hint: dict[str, Any] | None = None,
        name: str | None = None,
        **extra: Any,
    ) -> dict[str, Any]:
        step = {
            "path": path,
            "entry": entry,
            "name": name or posixpath.basename(path),
            "action": action,
            "status": status,
            "reason_code": reason,
            "hint": hint,
            **{k.rstrip("_"): v for k, v in extra.items() if v is not None and v is not False},
        }
        self.steps.append(step)
        return step

    def steps_for(self, key: str) -> list[dict[str, Any]]:
        return [s for s in self.steps if s["entry"] == key]


def hint(key: str, **args: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"key": key}
    for name, value in args.items():
        out[name.rstrip("_")] = value
    return out


def _copy_hint(src: str | None, to: list[str]) -> dict[str, Any]:
    return hint("copy", from_=src, to=sorted(to), pick=src is None)


def _record(entry: dict[str, Any] | None, path: str) -> str | None:
    if entry is None:
        return None
    found = entry["copies"].get(path)
    return found.get("hash") if isinstance(found, dict) else None


def _base_of(source: dict[str, Any]) -> dict[str, Any]:
    return {
        "channel": source["channel"],
        "hash": source["hash"],
        "version": source.get("version"),
        "server": source.get("server"),
    }


def is_older(source: dict[str, Any] | None, entry: dict[str, Any]) -> bool:
    """Whether a source is older than what the project holds for its entry."""
    base = entry.get("base")
    if source is None or not base:
        return False
    if source["channel"] == "cloud" and base.get("channel") == "cloud":
        if source.get("server") != base.get("server"):
            return False  # Versions from different servers don't compare
        mine, theirs = base.get("version"), source.get("version")
        return mine is not None and theirs is not None and int(theirs) < int(mine)
    if source["channel"] == "package" and base.get("channel") == "package":
        return base.get("hash") not in set(source.get("known") or [])
    return False


def _parent(path: str) -> str:
    return posixpath.dirname(path)


def state_tracked(
    path: str, entry: dict[str, Any], source: dict[str, Any] | None, disk: Disk
) -> Copy:
    value = disk.get(path)
    if value == UNREADABLE:
        return Copy(path, "unreadable", value)
    if value == ABSENT:
        return Copy(path, "absent" if disk.folder_exists(_parent(path)) else "gone", value)
    if source is not None and value == source["hash"] and not is_older(source, entry):
        return Copy(path, "current", value)
    if value == _record(entry, path):
        return Copy(path, "clean", value)
    return Copy(path, "edited", value)


def state_untracked(path: str, entry: dict[str, Any], source: dict[str, Any], disk: Disk) -> Copy:
    value = disk.get(path)
    if value == UNREADABLE:
        return Copy(path, "unreadable", value)
    if value == ABSENT:
        return Copy(path, "new", value)
    if entry["copies"]:
        return Copy(path, "loose", value)
    if value == source["hash"]:
        return Copy(path, "current", value)
    if value in set(entry.get("known") or []) | set(source.get("known") or []):
        return Copy(path, "clean", value)
    return Copy(path, "edited", value)


def _all_rows(
    plan: Plan,
    key: str,
    copies: list[Copy],
    status: str,
    reason: str | None,
    hint_: dict[str, Any] | None = None,
    name: str | None = None,
) -> None:
    for c in copies:
        plan.step(c.path, key, "none", status, reason, hint_, name)


def _others(copies: list[Copy], src: str | None) -> list[str]:
    return sorted(c.path for c in copies if c.path != src)


# Pull and upgrade --------------------------------------------------------------------------


def _plan_pull(
    disk: Disk, lock: dict[str, Any], offer: dict[str, Any], opt: dict[str, Any]
) -> Plan:
    plan = Plan(lock_after=copy.deepcopy(lock))
    accounted: set[str] = set()
    dirs = list(opt.get("dirs") or [])

    claimed: dict[str, str] = {}
    for source in offer.get("skills") or []:
        key = key_of(source["identity"])
        entry = lock["skills"].get(key)
        extra = []
        for d in dirs:
            # Collisions compare the spellings the setup asks for; the folder's own
            # spelling on disk is what is planned
            asked = f"{d}/{source['name']}"
            if claimed.get(disk.alias(asked), asked) != asked:
                raise SkillPlanError(
                    "paths_alias",
                    f"{claimed[disk.alias(asked)]} and {asked} are one folder on this volume, "
                    "and the setup names both. Rename one of the skills in the setup.",
                    asked,
                )
            claimed[disk.alias(asked)] = asked
            p = disk.spelled(asked)
            owner = owner_of(lock, p)
            if owner == key:
                continue
            if owner is not None:
                accounted.add(p)
                if disk.get(p) == ABSENT:
                    continue  # The owner's entry plans it (restored or missing)
                theirs, wanted = lock["skills"][owner]["identity"], source["identity"]
                other_variant = (
                    theirs["kind"] == wanted["kind"]
                    and theirs.get("skill_id") == wanted.get("skill_id")
                    and theirs.get("server") == wanted.get("server")
                    and theirs.get("variant") != wanted.get("variant")
                )
                # skills_move() switches the owner's whole entry and refuses an identity
                # the lock already tracks: the hint only when it works
                movable = len(lock["skills"][owner]["copies"]) == 1 and entry is None
                plan.step(
                    p,
                    owner,
                    "none",
                    "kept",
                    "other_variant" if other_variant else "other_identity",
                    hint("retarget", path=p, identity=dict(source["identity"]))
                    if movable
                    else None,
                    source["name"],
                )
                plan.kept_paths.add(p)
                accounted.add(p)
                continue
            if p not in extra:
                extra.append(p)
        plan.planned.add(key)
        _plan_entry(plan, key, entry, source, extra, disk, opt)
        accounted |= set(entry["copies"] if entry else []) | set(extra)

    for key, entry in lock["skills"].items():
        if key in plan.planned:
            continue
        if opt.get("scope") == "builtin" and entry["identity"]["kind"] != "builtin":
            continue
        _plan_unlisted(plan, key, entry, (offer.get("by_identity") or {}).get(key), disk, opt)
        accounted |= set(entry["copies"])

    if offer.get("source") == "cloud" and opt.get("command") == "pull":
        for d in dirs:
            for name in sorted(disk.skill_folders(d)):
                p = f"{d}/{name}"
                if p not in accounted:
                    plan.step(p, None, "none", "removed", "not_in_setup", None, name)
    return plan


def _first(copies: list[Copy], states: tuple[str, ...]) -> str | None:
    found = sorted(c.path for c in copies if c.state in states)
    return found[0] if found else None


def _plan_entry(
    plan: Plan,
    key: str,
    entry: dict[str, Any] | None,
    source: dict[str, Any],
    extra: list[str],
    disk: Disk,
    opt: dict[str, Any],
) -> None:
    entry = entry or _entry(source["identity"])
    name = source["name"]
    copies = sorted(
        [state_tracked(p, entry, source, disk) for p in entry["copies"]]
        + [state_untracked(p, entry, source, disk) for p in extra],
        key=lambda c: c.path.encode(),
    )

    if any(c.state == "unreadable" for c in copies):
        return _all_rows(plan, key, copies, "failed", "unreadable", None, name)
    if source.get("bundle") is None:
        return _all_rows(plan, key, copies, "unavailable", "not_shipped", None, name)
    # The package's copy (offline, or upgrade) never writes over a built-in tracked on
    # Cloud. A Cloud setup that names the package copy may.
    if offer_source(opt) == "package" and _cloud_base(entry):
        if opt.get("offline"):
            return _all_rows(plan, key, copies, "unavailable", "offline", None, name)
        return _all_rows(plan, key, copies, "kept", "cloud_tracked", None, name)

    older = is_older(source, entry)
    held = [c for c in copies if c.state in ("current", "clean", "edited")]
    edited = [c for c in held if c.state == "edited"]
    holes = [c for c in copies if c.state in ("absent", "new")]
    for c in copies:
        if c.state == "loose":
            tracked = sorted(c.path for c in held if c.path in entry["copies"])
            src = tracked[0] if tracked else None
            gone = _first(copies, ("gone",))
            plan.step(
                c.path,
                key,
                "none",
                "untracked",
                "untracked",
                _copy_hint(src, [c.path])
                if src
                else hint("move", from_=gone, to=c.path)
                if gone
                else None,
                name,
            )
        if c.state == "gone":
            twin = _first(copies, ("loose",))
            plan.step(
                c.path,
                key,
                "none",
                "missing",
                "folder_gone",
                hint("move", from_=c.path, to=twin) if twin else None,
                name,
            )

    if edited:
        differ = len({c.disk for c in held}) > 1
        if differ:
            src = edited[0].path if len(edited) == 1 else None
            return _all_rows(
                plan,
                key,
                held + holes,
                "diverged",
                "copies_differ",
                _copy_hint(src, _others(held + holes, src)),
                name,
            )
        if holes and (older or not opt.get("overwrite")):
            return _all_rows(
                plan,
                key,
                held + holes,
                "diverged",
                "copy_absent",
                _copy_hint(edited[0].path, [c.path for c in holes]),
                name,
            )
        if older:  # overwrite never moves to an older version
            for c in held:
                plan.step(c.path, key, "none", "edited", "newer_in_project", None, name)
            return None
        if not opt.get("overwrite"):
            for c in held:
                plan.step(c.path, key, "none", "edited", "local_edits", hint("overwrite"), name)
            return None
        for c in held:
            if c.state == "current":
                plan.step(c.path, key, "record", "current", None, None, name)
            else:
                plan.step(
                    c.path,
                    key,
                    "write",
                    "replaced",
                    "replaced_edits",
                    None,
                    name,
                    hash=source["hash"],
                    backup=True,
                )
        for c in holes:
            plan.step(
                c.path, key, "write", _added_or_restored(c), None, None, name, hash=source["hash"]
            )
        return _finish(plan, key, entry, source)

    # Unedited copies at different versions (a pull that wrote only some of them):
    # update only to a source that knows every version they hold
    versions = {c.disk for c in held}
    if len(versions) > 1 and (
        older or not versions <= set(source.get("known") or []) | {source["hash"]}
    ):
        return _all_rows(
            plan,
            key,
            held + holes,
            "diverged",
            "copies_differ",
            _copy_hint(None, [c.path for c in held + holes]),
            name,
        )

    if older:
        # The copies all hold one version here; prefer the one at the base
        base_hash = (entry.get("base") or {}).get("hash")
        fill = next((c for c in held if c.disk == base_hash), held[0] if held else None)
        for c in held:
            plan.step(c.path, key, "none", "current", "newer_in_project", None, name)
        for c in holes:
            if fill is not None:
                plan.step(
                    c.path, key, "copy", _added_or_restored(c), None, None, name, from_=fill.path
                )
            else:
                plan.step(c.path, key, "none", "unavailable", "setup_older", hint("login"), name)
        return _finish(plan, key, entry, None)  # copies recorded; base unchanged

    for c in held:
        if c.state == "current":
            stale = _record(entry, c.path) != source["hash"] or entry.get("base") != _base_of(
                source
            )
            plan.step(c.path, key, "record" if stale else "none", "current", None, None, name)
        else:
            plan.step(
                c.path, key, "write", "updated", None, None, name, hash=source["hash"], backup=True
            )
    for c in holes:
        plan.step(
            c.path, key, "write", _added_or_restored(c), None, None, name, hash=source["hash"]
        )
    return _finish(plan, key, entry, source)


def offer_source(opt: dict[str, Any]) -> str:
    return str(opt.get("source") or "cloud")


def _added_or_restored(c: Copy) -> str:
    return "restored" if c.state == "absent" else "added"


def _finish(plan: Plan, key: str, entry: dict[str, Any], source: dict[str, Any] | None) -> None:
    """lock_after for one entry, when any of its steps writes, copies, or records."""
    steps = plan.steps_for(key)
    if not any(s["action"] in ("write", "copy", "record") for s in steps):
        return
    out = copy.deepcopy(entry)
    for s in steps:
        if s["action"] in ("write", "record") and source is not None:
            out["copies"][s["path"]] = {"hash": source["hash"]}
        if s["action"] == "copy":
            record = _record(entry, s["from"])
            out["copies"][s["path"]] = {"hash": record}
    if source is not None:
        out["base"] = _base_of(source)
        out["known"] = sorted(
            set(entry.get("known") or []) | set(source.get("known") or []) | {source["hash"]}
        )
        out.pop("pending", None)
    plan.lock_after["skills"][key] = out


def _plan_unlisted(
    plan: Plan,
    key: str,
    entry: dict[str, Any],
    source: dict[str, Any] | None,
    disk: Disk,
    opt: dict[str, Any],
) -> None:
    paths = sorted(entry["copies"])
    name = posixpath.basename(paths[0]) if paths else key
    copies = [state_tracked(p, entry, source, disk) for p in paths]
    if not copies:
        return None
    if any(c.state == "unreadable" for c in copies):
        return _all_rows(plan, key, copies, "failed", "unreadable", None, name)
    if opt.get("offline"):
        return _all_rows(plan, key, copies, "unavailable", "offline", None, name)
    held = [c for c in copies if c.state in ("current", "clean", "edited")]
    holes = [c for c in copies if c.state == "absent"]
    if not held:
        if source is not None and holes:  # fetched by its own identity
            for c in copies:
                if c in holes:
                    plan.step(
                        c.path, key, "write", "restored", None, None, name, hash=source["hash"]
                    )
                else:
                    plan.step(c.path, key, "none", "missing", "folder_gone", None, name)
            return _finish(plan, key, entry, source)
        return _all_rows(
            plan,
            key,
            copies,
            "missing",
            "all_gone",
            hint("login") if entry["identity"]["kind"] == "cloud" else None,
            name,
        )
    edited = [c for c in held if c.state == "edited"]
    differ = len({c.disk for c in held}) > 1
    if edited and (differ or holes):
        src = edited[0].path if len(edited) == 1 else None
        return _all_rows(
            plan,
            key,
            held + holes,
            "diverged",
            "copies_differ" if differ else "copy_absent",
            _copy_hint(src, _others(held + holes, src)),
            name,
        )
    for c in copies:
        # A folder a setup skill reported as kept is not also removed
        if _in_dirs(c.path, opt.get("dirs") or []) and c.path not in plan.kept_paths:
            plan.step(c.path, key, "none", "removed", "not_in_setup", None, name)
    return None


def _in_dirs(path: str, dirs: list[str]) -> bool:
    return _parent(path) in dirs


# Copy ------------------------------------------------------------------------------------


def _plan_copy(disk: Disk, lock: dict[str, Any], opt: dict[str, Any]) -> Plan:
    plan = Plan(lock_after=copy.deepcopy(lock))
    from_dir, names = opt["from_dir"], list(opt["names"])
    for t in opt["to_dirs"]:
        for n in names:
            # Both as spelled on disk, so owners match by filesystem identity
            src, dst = disk.spelled(f"{from_dir}/{n}"), disk.spelled(f"{t}/{n}")
            if disk.get(src) == ABSENT:
                raise SkillPlanError("copy_source_missing", f"Not a skill in {from_dir}: {n}.", src)
            if disk.get(src) == UNREADABLE:
                raise SkillPlanError(
                    "copy_source_unreadable",
                    f"{src} is not a skill folder (no SKILL.md file, or files that can't be "
                    "read). Fix it, then copy again.",
                    src,
                )
            if disk.get(dst) == UNREADABLE:
                raise SkillPlanError(
                    "copy_target_unreadable",
                    f"{dst} exists but is not a skill folder (no SKILL.md file). "
                    "Move it, then copy again.",
                    dst,
                )
            mine, theirs = owner_of(lock, src), owner_of(lock, dst)
            if theirs is not None and mine is not None and theirs != mine:
                raise SkillPlanError(
                    "copy_target_owned",
                    f"{dst} belongs to another skill in the lock, so skills_copy() won't "
                    "replace it. Nothing was copied.",
                    dst,
                )
            same = disk.get(dst) == disk.get(src)
            plan.step(
                dst,
                mine,
                "none" if same else "copy",
                "current" if same else "copied",
                None,
                None,
                n,
                from_=src,
                backup=not same and disk.get(dst) != ABSENT,
            )
            if mine is not None:  # tracked source: the target joins its entry
                record = _record(lock["skills"][mine], src)
                plan.lock_after["skills"][mine]["copies"][dst] = {"hash": record}
        if opt.get("names_is_all"):
            copied = {posixpath.basename(disk.spelled(f"{t}/{n}")) for n in names}
            for n in sorted(set(disk.skill_folders(t)) - set(names) - copied):
                plan.step(f"{t}/{n}", None, "none", "missing", None, None, n)
    return plan


# Push ------------------------------------------------------------------------------------


def _untracked_folders(disk: Disk, lock: dict[str, Any], dirs: list[str]) -> list[str]:
    return [
        f"{d}/{n}"
        for d in dirs
        for n in sorted(disk.skill_folders(d))
        if owner_of(lock, f"{d}/{n}") is None
    ]


def _twins(disk: Disk, lock: dict[str, Any], dirs: list[str]) -> list[tuple[str, str, str]]:
    """Hand-moved copies (the twin rule, push only): (entry key, copy, folder) for each
    untracked folder in a configured folder that has the name of an absent copy of any
    entry (moved to another folder), or holds that copy's recorded bytes (renamed, in
    the same folder or another). The entry may still have other copies."""
    folders = _untracked_folders(disk, lock, dirs)
    found = []
    for key, entry in lock["skills"].items():
        for p in sorted(entry["copies"]):
            if disk.get(p) != ABSENT:
                continue
            record = _record(entry, p)
            for q in folders:
                if posixpath.basename(q) == posixpath.basename(p) or (
                    record is not None and disk.get(q) == record
                ):
                    found.append((key, p, q))
    return found


def _plan_push(
    disk: Disk, lock: dict[str, Any], offer: dict[str, Any], opt: dict[str, Any]
) -> Plan:
    plan = Plan(lock_after=copy.deepcopy(lock))
    dirs = list(opt.get("dirs") or [])
    remote = offer.get("remote") or {}
    twins = _twins(disk, lock, dirs)

    def unique(found: list[tuple[str, str, str]]) -> tuple[str, str, str] | None:
        """The match when it is the only match of its entry's absent copies and of its
        folder."""
        if len(found) != 1:
            return None
        key, _, q = found[0]
        alone = [t for t in twins if t[0] == key or t[2] == q]
        return found[0] if len(alone) == 1 else None

    def copy_twin(key: str, path: str) -> tuple[str, str, str] | None:
        """The folder an absent copy was moved to, when the match is unique both ways."""
        mine = [t for t in twins if t[0] == key and t[1] == path]
        if len(mine) != 1:
            return None
        return mine[0] if len([t for t in twins if t[2] == mine[0][2]]) == 1 else None

    for key, entry in lock["skills"].items():
        copies = [state_tracked(p, entry, None, disk) for p in sorted(entry["copies"])]
        if any(c.state == "unreadable" for c in copies):
            _all_rows(plan, key, copies, "failed", "unreadable")
            continue
        held = [c for c in copies if c.state in ("clean", "edited")]
        if not held:
            mine = [t for t in twins if t[0] == key]
            match = unique(mine)
            _all_rows(
                plan,
                key,
                copies,
                "missing",
                "folder_gone" if mine else "all_gone",
                hint("move", from_=match[1], to=match[2]) if match else None,
            )
            continue
        edited = [c for c in held if c.state == "edited"]
        if not edited:
            continue  # nothing to push
        differ = len({c.disk for c in held}) > 1
        holes = [c for c in copies if c.state == "absent"]
        if differ or holes:
            src = edited[0].path if len(edited) == 1 else None
            moved = (
                None if differ else next((t for c in holes if (t := copy_twin(key, c.path))), None)
            )
            _all_rows(
                plan,
                key,
                copies,
                "failed",
                "copies_differ" if differ else "copy_absent",
                # An absent copy moved by hand: record the move, never copy over it
                hint("move", from_=moved[1], to=moved[2])
                if moved
                else _copy_hint(src, _others(held + holes, src)),
            )
            continue
        local = held[0].disk
        head = remote.get(key)
        base = entry.get("base")
        if head is not None and head.get("hash") == local:
            # Cloud already has it: adopt
            for c in copies:
                plan.step(c.path, key, "record", "current")
            out = plan.lock_after["skills"][key]
            server = (
                head.get("server") or (base or {}).get("server") or entry["identity"].get("server")
            )
            out["base"] = {
                "channel": "cloud",
                "hash": local,
                "version": head.get("version"),
                "server": server,
            }
            out["copies"] = {
                p: {"hash": local} if disk.get(p) == local else dict(r)
                for p, r in entry["copies"].items()
            }
            out["known"] = sorted(set(out["known"]) | {local})
            out.pop("pending", None)
        elif entry["identity"]["kind"] == "builtin":
            for c in copies:
                plan.step(c.path, key, "push", "pushed", fork=True, hash=local)
        elif not base and entry.get("pending") and not opt.get("force"):
            # A first push that stopped before Cloud replied may have reached it
            for c in copies:
                plan.step(
                    c.path, key, "none", "refused", "first_push_pending", hint("force"), hash=local
                )
        elif not base:
            force = bool(opt.get("force"))
            for c in copies:
                plan.step(
                    c.path,
                    key,
                    "push" if force else "none",
                    "pushed" if force else "refused",
                    None if force else "no_base",
                    None if force else hint("force"),
                    hash=local,
                )
        elif head is None or head.get("version") == base.get("version"):
            for c in copies:
                plan.step(c.path, key, "push", "pushed", hash=local)
        else:
            force = bool(opt.get("force"))
            for c in copies:
                plan.step(
                    c.path,
                    key,
                    "push" if force else "none",
                    "pushed" if force else "refused",
                    None if force else "cloud_changed",
                    None if force else hint("force"),
                    hash=local,
                    forced=force,
                )
    for p in _untracked_folders(disk, lock, dirs):
        mine = [t for t in twins if t[2] == p]
        match = mine[0] if len(mine) == 1 and copy_twin(mine[0][0], mine[0][1]) else None
        plan.step(
            p,
            None,
            "none",
            "untracked",
            "untracked",
            hint("move", from_=match[1], to=p) if match else None if mine else hint("sync", path=p),
        )
    return plan


# Move ------------------------------------------------------------------------------------


def _expand(lock: dict[str, Any], src: str, dst: str | None) -> list[tuple[str, str]]:
    """The (from, to) pairs a move names: one tracked copy, or every tracked copy in a
    folder."""
    if owner_of(lock, src) is not None:
        return [] if dst is None else [(src, dst)]
    inside = sorted(p for e in lock["skills"].values() for p in e["copies"] if _parent(p) == src)
    if not inside:
        raise SkillPlanError(
            "move_untracked",
            f"{src} is not a skill the lock tracks, nor a folder holding one. "
            "skills_sync() starts tracking a skill.",
            src,
        )
    if dst is None:
        return []
    return [(p, f"{dst}/{posixpath.basename(p)}") for p in inside]


def _plan_move(disk: Disk, lock: dict[str, Any], opt: dict[str, Any]) -> Plan:
    plan = Plan(lock_after=copy.deepcopy(lock))
    src, dst = opt["from"], opt.get("to")
    pairs = _expand(lock, src, dst)
    for a, b in pairs:
        key = owner_of(lock, a)
        if key is None:
            raise SkillPlanError("move_untracked", f"{a} is not a skill the lock tracks.", a)
        # A destination with a record, even in the same entry, is never overwritten
        if owner_of(lock, b) is not None and disk.alias(b) != disk.alias(a):
            raise SkillPlanError(
                "move_target_owned",
                f"{b} belongs to another skill in the lock, so skills_move() won't use it.",
                b,
            )
        if disk.get(a) != ABSENT and disk.get(b) != ABSENT:
            raise SkillPlanError(
                "move_target_exists",
                f"{b} already exists, and so does {a}. Remove one of them, then move again.",
                b,
            )
        if disk.get(a) == ABSENT and disk.get(b) == ABSENT:
            raise SkillPlanError(
                "move_nothing", f"Neither {a} nor {b} exists, so there is nothing to move.", a
            )
    for a, b in pairs:
        key = owner_of(lock, a)
        assert key is not None
        action = "move" if disk.get(a) != ABSENT else "record"
        plan.step(
            b, key, action, "moved", "path_moved" if action == "move" else "lock_moved", from_=a
        )
        copies = plan.lock_after["skills"][key]["copies"]
        copies[b] = copies.pop(a)
    wanted = opt.get("identity")
    if wanted:
        keys = {owner_of(lock, a) for a, _ in pairs} if pairs else set()
        if not pairs:
            found = owner_of(lock, src)
            keys = (
                {found}
                if found
                else {
                    owner_of(lock, p)
                    for e in lock["skills"].values()
                    for p in e["copies"]
                    if _parent(p) == src
                }
            )
        if len(keys) != 1:
            raise SkillPlanError(
                "move_untracked",
                f"{src} holds copies of several skills. Name one skill folder to retarget.",
                src,
            )
        key = str(next(iter(keys)))
        entry = lock["skills"][key]
        identity = _retarget_identity(wanted, entry)
        new_key = key_of(identity)
        if new_key in lock["skills"]:
            raise SkillPlanError(
                "move_identity_exists",
                f"The lock already tracks {new_key}. Move this copy into that skill's folder "
                "instead, or skills_copy() it there.",
            )
        moved = plan.lock_after["skills"].pop(key)
        renamed = {b: a for a, b in pairs}

        def held(p: str) -> str:
            """A copy's bytes where they are now: at its new path, or at its old one when
            the folder hasn't moved yet."""
            found = disk.get(p)
            return disk.get(renamed[p]) if found == ABSENT and p in renamed else found

        clean = {r["hash"] for p, r in moved["copies"].items() if r.get("hash") == held(p)}
        plan.lock_after["skills"][new_key] = {
            "identity": identity,
            "base": None,
            "known": sorted(clean),
            "copies": moved["copies"],
        }
        for p in sorted(moved["copies"]):
            plan.step(p, new_key, "record", "moved", "retargeted")
    return plan


def _retarget_identity(wanted: dict[str, Any], entry: dict[str, Any]) -> dict[str, Any]:
    variant = wanted.get("variant") or entry["identity"]["variant"]
    if wanted.get("builtin"):
        return {"kind": "builtin", "skill_id": f"framework:{wanted['builtin']}", "variant": variant}
    if wanted.get("skill_id"):
        return identity_for(str(wanted["skill_id"]), variant, wanted.get("server"))
    # Only a variant: the same skill in another variant
    identity = dict(entry["identity"])
    identity["variant"] = variant
    if identity["kind"] == "local":
        raise SkillPlanError(
            "move_untracked",
            "This copy has no Cloud identity yet. Name one with skill_id= or builtin=.",
        )
    return identity


# The planner -----------------------------------------------------------------------------


def plan(
    disk: Disk, lock: dict[str, Any], offer: dict[str, Any] | None, opt: dict[str, Any]
) -> Plan:
    """The one planner every skill command calls.

    ``opt["command"]`` is ``pull`` or ``upgrade`` (with ``offer``: ``source``,
    ``skills``, ``by_identity``), ``push`` (``offer["remote"]``), ``copy``
    (``from_dir``, ``to_dirs``, ``names``, ``names_is_all``), or ``move`` (``from``,
    ``to``, ``identity``). Other options: ``dirs``, ``overwrite``, ``force``,
    ``offline``, ``scope`` (``all``, or ``builtin`` for upgrade). Refusals raise
    ``SkillPlanError``. ``dry_run`` doesn't change the plan.
    """
    command = opt["command"]
    offer = offer or {}
    if command in ("pull", "upgrade"):
        return _plan_pull(disk, lock, offer, {**opt, "source": offer.get("source", "cloud")})
    if command == "push":
        return _plan_push(disk, lock, offer, opt)
    if command == "copy":
        return _plan_copy(disk, lock, opt)
    if command == "move":
        return _plan_move(disk, lock, opt)
    raise ValueError(f"Unknown skill command: {command}")


def rows(steps: list[dict[str, Any]], command: str) -> list[dict[str, Any]]:
    """Steps grouped into result rows. Pull, upgrade, and push: one row per lock entry,
    plus one per ``untracked``, ``kept``, or ``removed`` step. Copy and move: one row
    per step. A row's status is the first of its steps' statuses in ``PRECEDENCE``;
    its reason and hint come from the first step (by path) with that status."""
    ordered = sorted(steps, key=lambda s: s["path"].encode())
    groups: list[list[dict[str, Any]]] = []
    by_entry: dict[str, list[dict[str, Any]]] = {}
    for step in ordered:
        if command in ("copy", "move") or step["status"] in _OWN_ROW or step["entry"] is None:
            groups.append([step])
            continue
        if step["entry"] not in by_entry:
            by_entry[step["entry"]] = []
            groups.append(by_entry[step["entry"]])
        by_entry[step["entry"]].append(step)
    out = []
    for group in groups:
        status = min(
            (s["status"] for s in group),
            key=lambda s: PRECEDENCE.index(s) if s in PRECEDENCE else len(PRECEDENCE),
        )
        first = next(s for s in group if s["status"] == status)
        out.append(
            {
                "name": group[0]["name"],
                "status": status,
                "paths": sorted({s["path"] for s in group}, key=str.encode),
                "entry": group[0]["entry"],
                "reason_code": first["reason_code"],
                "hint": first["hint"],
                "steps": group,
            }
        )
    return out


def after_push(
    lock: dict[str, Any],
    key: str,
    response: dict[str, Any],
    holding: set[str] | None = None,
) -> dict[str, Any]:
    """The lock once Cloud accepted a push of entry ``key``: its base is the pushed
    version, and ``pending`` is cleared. Each copy in ``holding`` (the copies whose
    files are the pushed bytes; every copy when None) takes the pushed hash as its
    record; any other copy (one whose folder is gone) keeps its own. A pushed built-in
    (or a local skill's first push) becomes a Cloud entry under the response's
    ``skill_id``, with only the pushed hash known."""
    out = copy.deepcopy(lock)
    entry = out["skills"].pop(key)
    server = str(response.get("server") or "").rstrip("/")
    pushed = response["hash"]
    variant = entry["identity"]["variant"]
    identity = identity_for(str(response["skill_id"]), variant, server)
    fresh = identity != entry["identity"]
    new = {
        "identity": identity,
        "base": {
            "channel": "cloud",
            "hash": pushed,
            "version": response.get("version"),
            "server": server or None,
        },
        "known": sorted({pushed} | (set() if fresh else set(entry.get("known") or []))),
        "copies": {
            p: {"hash": pushed} if holding is None or p in holding else dict(r)
            for p, r in entry["copies"].items()
        },
    }
    new_key = key_of(identity)
    if new_key in out["skills"]:
        new = _merge_v2(new, out["skills"][new_key])
    out["skills"][new_key] = new
    return out


def cancel(steps: list[dict[str, Any]]) -> None:
    """A push not confirmed: every ``pushed`` step becomes ``cancelled``."""
    for step in steps:
        if step["status"] == "pushed":
            step.update(action="none", status="cancelled", reason_code="cancelled", hint=None)
