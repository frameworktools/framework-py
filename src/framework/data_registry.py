"""A project's data registry (the catalog in the settings' ``data`` section), kept in step
with Framework Cloud.

``pull_data_registry()`` brings Cloud's entries into the settings; ``push_data_registry()``
sends the project's entries back. Only the definitions travel: never the data files, and
never their hashes, which stay in framework.db. R: ``pull_data_registry()``,
``push_data_registry()``; fw: ``fw data pull`` / ``fw data push``.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from framework import _yaml
from framework.catalog import _pathless_entry, catalog_locate
from framework.errors import FrameworkError
from framework.spec.check import DATA_FIELDS

_LOCK = ".framework/data.lock.json"
_PATH = "/api/v1/projects/data"


# Reading the local catalog ---------------------------------------------------------------


def _entries(catalog: Any, prefix: str = "") -> dict[str, dict[str, Any]]:
    """Every entry in a settings data section, by dot-notation name, as data_list() finds
    them: nested, mixed, and flat keys, and path shorthands. Entries without a path are
    left out."""
    found: dict[str, dict[str, Any]] = {}
    if not isinstance(catalog, dict):
        return found
    for key, item in catalog.items():
        name = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(item, str):
            found[name] = {"path": item}
        elif isinstance(item, dict) and "path" in item:
            found[name] = dict(item)
        elif isinstance(item, dict) and not _pathless_entry(item):
            found.update(_entries(item, name))
    return found


def _row(name: str, entry: dict[str, Any]) -> dict[str, Any]:
    """An entry as a registry row: its name and the fields that travel."""
    row: dict[str, Any] = {"name": name}
    for field in DATA_FIELDS:
        value: Any = entry.get(field)
        if value is None:
            continue
        if field in ("locked", "private"):
            value = bool(value)
        if field == "stata_names":
            if not value:
                continue
            value = {str(k): v for k, v in dict(value).items()}
        row[field] = value
    row.setdefault("locked", False)
    return row


def _key(row: dict[str, Any]) -> tuple[Any, ...]:
    """What counts as a change: every field that travels."""
    names = row.get("stata_names") or {}
    return (
        row.get("path") or "",
        row.get("type") or "",
        row.get("delimiter") or "",
        bool(row.get("locked")),
        None if row.get("private") is None else bool(row.get("private")),
        row.get("description") or "",
        tuple(sorted((str(k), str(v)) for k, v in dict(names).items())),
    )


def _same(a: dict[str, Any], b: dict[str, Any]) -> bool:
    return _key(a) == _key(b)


def _diff(before: dict[str, Any], after: dict[str, Any]) -> str:
    """How ``after`` differs from ``before``, one item per field: "path a -> b" for the
    short fields, "description changed" or "stata_names changed" for the long ones."""

    def shown(value: Any) -> str:
        if value is None or value == "":
            return "none"
        return str(value).lower() if isinstance(value, bool) else str(value)

    parts = []
    for field, a, b in zip(DATA_FIELDS, _key(before), _key(after), strict=True):
        if a == b:
            continue
        if field in ("description", "stata_names"):
            parts.append(f"{field} changed")
        else:
            parts.append(f"{field} {shown(a)} -> {shown(b)}")
    return ", ".join(parts)


def _names(rows: list[dict[str, Any]]) -> str:
    return ", ".join(str(r.get("name")) for r in rows)


def _location(root: Path) -> tuple[Path, list[str], Any]:
    """The file that holds the data section, the keys to it there, and the catalog.

    Inline (under ``default:`` when the file has environments) or in a split file such as
    ``settings/data.yml``. Empty keys mean the split file holds the catalog itself.
    """
    from framework.project import _settings_in

    settings_file = _settings_in(root)
    if settings_file is None:
        raise FrameworkError(f"Not a Framework project (no settings.yml): {root}")
    raw = _yaml.read(settings_file) or {}
    raw = raw if isinstance(raw, dict) else {}
    environments = isinstance(raw.get("default"), dict)
    config = raw["default"] if environments else raw
    source = config.get("data")
    if isinstance(source, str):
        path = Path(source).expanduser()
        file = path if path.is_absolute() else root / path
        content = _yaml.read(file) if file.is_file() else None
        if (
            isinstance(content, dict)
            and isinstance(content.get("default"), dict)
            and ("data" in content["default"])
        ):
            return file, ["default", "data"], content["default"]["data"]
        if isinstance(content, dict) and "data" in content:
            return file, ["data"], content["data"]
        if isinstance(content, dict) and content:
            return file, [], content
        return file, ["data"], {}
    keys = ["default", "data"] if environments else ["data"]
    return settings_file, keys, source if isinstance(source, dict) else {}


def _local(root: Path) -> dict[str, dict[str, Any]]:
    """The project's entries as registry rows, by name."""
    try:
        catalog = _location(root)[2]
    except Exception:
        return {}
    return {name: _row(name, entry) for name, entry in _entries(catalog).items()}


def _write(root: Path, rows: list[dict[str, Any]]) -> None:
    """Write Cloud's rows into the settings' data section, each where its entry already is
    (nested, mixed, or flat keys), else nested by name. Fields that don't travel stay. The
    file is edited in place, so its comments and layout are kept."""
    file, keys, _ = _location(root)

    def apply(catalog: Any) -> None:
        for row in rows:
            found = catalog_locate(row["name"], catalog)
            if found is not None:
                holder, key = found
            else:
                holder = catalog
                *groups, key = row["name"].split(".")
                for part in groups:
                    if not isinstance(holder.get(part), dict):
                        holder[part] = {}
                    holder = holder[part]
            before = holder.get(key)
            if not isinstance(before, dict):
                holder[key] = before = {}
            for field in DATA_FIELDS:
                value = row.get(field)
                if field == "locked" and not value:
                    value = None
                if value is None:
                    before.pop(field, None)
                else:
                    before[field] = value

    def change(content: Any) -> Any:
        content = content if isinstance(content, dict) else {}
        if not keys:
            apply(content)
            return {"data": content}
        node = content
        for key in keys[:-1]:
            if not isinstance(node.get(key), dict):
                node[key] = {}
            node = node[key]
        if not isinstance(node.get(keys[-1]), dict):
            node[keys[-1]] = {}
        apply(node[keys[-1]])
        return content

    if file.is_file():
        _yaml.update(file, change)
    else:
        _yaml.write(file, change({}))


# The lock and the connection -------------------------------------------------------------


def _lock_read(root: Path) -> dict[str, Any]:
    try:
        data = json.loads((root / _LOCK).read_text(encoding="utf-8"))
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
    from framework.cloud import CloudError, cloud_server, project_token

    token = project_token(root)
    if token is None:
        raise CloudError(
            "Framework Cloud not reached (no project token in FW_PROJECT_TOKEN or the "
            "project's .env). Nothing changed."
        )
    return cloud_server(scope="project", project_dir=root).url, token


def _call(
    token: str, server: str, method: str = "GET", body: dict[str, Any] | None = None
) -> dict[str, Any]:
    """The registry endpoint, with its failures as data registry messages. A 409 is
    raised as it is, for push to explain."""
    from framework.cloud import (
        CloudConflictError,
        CloudError,
        CloudOfflineError,
        _MissingEndpointError,
        api,
    )

    try:
        reply = api(_PATH, token, server, method, body)
    except CloudConflictError:
        raise
    except _MissingEndpointError as error:
        raise CloudError(
            "This Framework Cloud server has no data registry endpoint yet. Nothing changed."
        ) from error
    except CloudError as error:
        reason = str(error).rstrip(".")
        if isinstance(error, CloudOfflineError) and error.status is None:
            reason = reason or type(error).__name__
            raise CloudError(f"Framework Cloud not reached ({reason}). Nothing changed.") from error
        if error.status in (401, 403):
            # A revoked or mistyped project token
            raise CloudError(
                f"Framework Cloud refused the project token ({reason or 'not accepted'}). "
                "Check FW_PROJECT_TOKEN in the project's .env. Nothing changed."
            ) from error
        if error.status == 422:
            raise CloudError(
                f"{reason or 'Framework Cloud refused the data registry'}. Nothing changed."
            ) from error
        # Anything else is reported as Cloud answered it
        status = f"HTTP {error.status}" if error.status else "error"
        raise CloudError(
            f"Framework Cloud answered with an error ({status}): "
            f"{reason or 'no message'}. Nothing changed."
        ) from error
    return reply if isinstance(reply, dict) else {}


def _project_name(root: Path) -> str:
    from framework.sync_all import _project_name as name

    return name(root)


# Plans, shared with pull_all() and push_all() ------------------------------------------


def _pull_plan(
    root: Path, server: str, token: str, overwrite: bool, fn: str = "pull_data_registry()"
) -> dict[str, Any]:
    """What pulling the registry would change. ``fn`` is the call the overwrite advice names."""
    registry = _call(token, server)
    revision = str(registry.get("revision") or "")
    local = _local(root)
    cloud = {
        str(r["name"]): r
        for r in registry.get("entries") or []
        if isinstance(r, dict) and r.get("name")
    }
    added = [cloud[n] for n in cloud if n not in local]
    differ = [n for n in cloud if n in local and not _same(local[n], cloud[n])]
    kept_local = [n for n in local if n not in cloud]

    lines: list[str] = []
    if added:
        lines.append(f"  Entries to add: {_names(added)}")
    if differ:
        details = "; ".join(f"{n} ({_diff(local[n], cloud[n])})" for n in differ)
        advice = fn.replace("()", "(overwrite=True)")
        tail = "Cloud's will be used" if overwrite else f"kept; {advice} uses Cloud's"
        lines.append(f"  Entries that differ (local -> Cloud): {details} — {tail}")
    if kept_local:
        lines.append(f"  Only in this project (kept): {', '.join(kept_local)}")
    rows = added + ([cloud[n] for n in differ] if overwrite else [])
    return {
        "revision": revision,
        "lines": lines,
        "quiet": not lines,
        "pending": bool(rows),
        "rows": rows,
        "entries": {
            "added": [str(r["name"]) for r in added],
            "changed": differ if overwrite else [],
            "differ_kept": [] if overwrite else differ,
            "kept_local": kept_local,
        },
    }


def _pull_apply(root: Path, plan: dict[str, Any], server: str) -> None:
    """Write a pull plan's entries and record the revision pulled."""
    from framework._log import inform

    rows = plan["rows"]
    if rows:
        _write(root, rows)
    _lock_write(root, plan["revision"], server)
    if rows:
        inform(
            f"✓ Data registry: {len(rows)} {'entry' if len(rows) == 1 else 'entries'} "
            "added or updated"
        )


def _push_plan(
    root: Path,
    server: str,
    token: str,
    remove: bool,
    force: bool,
    pull_fn: str = "pull_data_registry()",
    push_fn: str = "push_data_registry(force=True)",
) -> dict[str, Any]:
    """What pushing the registry would change, from Cloud's dry run. A registry that changed
    on Cloud since the last pull raises "Pull first", naming ``pull_fn`` and ``push_fn``."""
    from framework.cloud import CloudConflictError, CloudError

    local = _local(root)
    # The base revision for a project that never pulled its registry
    base = _lock_read(root).get("revision")
    if base is None:
        base = _call(token, server).get("revision")

    def send(dry: bool) -> dict[str, Any]:
        body = {
            "base_revision": base,
            "entries": list(local.values()),
            "remove": remove,
            "force": force,
            "dry_run": dry,
        }
        try:
            return _call(token, server, "POST", body)
        except CloudConflictError as error:
            latest = error.body.get("revision") or "unknown"
            raise CloudError(
                "Framework Cloud's data registry changed since this project last pulled it "
                f"(revision {latest}). Pull first: {pull_fn}. "
                f"{push_fn} replaces Cloud's changes."
            ) from error

    preview = send(True)
    changes = preview.get("changes") or {}
    added = changes.get("added") or []
    changed = changes.get("changed") or []
    removed = changes.get("removed") or []
    kept = changes.get("kept") or []

    lines: list[str] = []
    if added:
        lines.append(f"  Entries to add: {_names(added)}")
    if changed:
        details = "; ".join(f"{c['name']} ({_diff(c['from'], c['to'])})" for c in changed)
        lines.append(f"  Entries to change: {details}")
    if removed:
        lines.append(f"  Entries to remove: {_names(removed)}")
    if kept:
        lines.append(f"  On Cloud only (kept): {_names(kept)}")
    pending = bool(added or changed or removed)
    warnings: list[str] = []
    if pending:
        warnings.append(
            f"This changes {_project_name(root)}'s data registry on Framework Cloud. "
            "New copies of the project, zips, and Update on GitHub will use it. "
            "Only definitions are sent: no data files and no hashes."
        )
        if force and preview.get("revision") is not None and base != preview.get("revision"):
            warnings.append(
                "Cloud's newer data registry changes will be replaced by this project's entries."
            )
    return {
        "revision": preview.get("revision"),
        "lines": lines,
        "warnings": warnings,
        "quiet": not pending,
        "pending": pending,
        "send": send,
        "changes": {"added": added, "changed": changed, "removed": removed, "kept": kept},
    }


def _push_apply(root: Path, plan: dict[str, Any], server: str) -> dict[str, Any]:
    """Push a plan's entries and record the new revision."""
    from framework._log import inform

    reply = plan["send"](False)
    revision = str(reply.get("revision") or "")
    _lock_write(root, revision, server)
    done = reply.get("changes") or plan["changes"]
    if reply.get("applied"):
        parts = [
            f"{len(done.get(key) or [])} {key}"
            for key in ("added", "changed", "removed")
            if done.get(key)
        ]
        inform(f"Data registry pushed: revision {revision} ({', '.join(parts)})")
    else:
        inform("Already up to date.")
    return {"applied": bool(reply.get("applied")), "revision": revision}


# Pull and push -----------------------------------------------------------------------------


def pull_data_registry(
    project_path: str | Path = ".",
    overwrite: bool = False,
    confirm: bool | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Bring the project's data catalog up to date with its data registry on Framework Cloud.

    Reads the registry with the project token (``GET /api/v1/projects/data``) and lists the
    changes first: entries to add, entries whose path, type, or options differ, and entries
    only in this project. Then asks "Apply these changes?".

    Only the definitions move. Data files are never downloaded, and file hashes stay in the
    project's framework.db. Applying writes the new entries into the settings' data section
    (inline, or in its split file such as ``settings/data.yml``), keeping the file's comments.
    Local entries are never removed, and an entry whose local definition differs keeps it
    unless ``overwrite=True``. Fields Framework Cloud doesn't keep stay as they are. The
    revision pulled is kept in ``.framework/data.lock.json`` for ``push_data_registry()``.
    ``pull_all()`` pulls it with the packages and skills.

    Args:
        project_path: The project's root directory. Default: the current directory.
        overwrite: Use Cloud's definitions for entries that differ.
        confirm: True applies without asking; False cancels. None asks in a console.
        dry_run: List the changes and write nothing.

    Returns:
        A dict: ``revision``, ``entries`` (``added``, ``changed``, ``differ_kept``, and
        ``kept_local``, as names), ``applied``, ``dry_run``, and ``message``.

    Raises:
        CloudError: Without a project token, offline, when the token is refused, or when
            the server has no data registry endpoint. Nothing changes then.
        FrameworkError: Without a console and without ``confirm=True`` or ``dry_run=True``.
    """
    from framework._log import inform, show_messages
    from framework.skill_pull import _root
    from framework.sync_all import _ask

    show_messages()
    root = _root(project_path)
    server, token = _connection(root)
    plan = _pull_plan(root, server, token, overwrite)
    revision = plan["revision"]
    if plan["quiet"]:
        lines = [f"Data registry is up to date with Framework Cloud (revision {revision})."]
    else:
        lines = [
            f"Data registry changes from Framework Cloud (revision {revision}):",
            *plan["lines"],
        ]
    for line in lines:
        inform(line)

    result: dict[str, Any] = {
        "revision": revision,
        "entries": plan["entries"],
        "applied": False,
        "dry_run": dry_run,
        "message": "\n".join(lines),
    }
    if dry_run:
        return result
    if not plan["pending"]:
        _lock_write(root, revision, server)
        return result
    if not _ask("Apply these changes?", confirm, "pull_data_registry"):
        inform("Nothing changed.")
        return result
    _pull_apply(root, plan, server)
    result["applied"] = True
    return result


def push_data_registry(
    project_path: str | Path = ".",
    remove: bool = False,
    force: bool = False,
    confirm: bool | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Send the project's data catalog to its data registry on Framework Cloud.

    The entries go to ``POST /api/v1/projects/data``, first as a dry run whose changes are
    listed with a warning: new copies of the project, zips, and Update on GitHub use what is
    pushed. Then asks "Push these changes?". Entries only on Cloud stay there unless
    ``remove=True``.

    Only the definitions move: each entry's name, path, type, delimiter, locked and private
    flags, description, and Stata names. Data files are never uploaded, and file hashes stay
    in the project's framework.db.

    When Cloud's registry changed since this project last pulled it (the revision in
    ``.framework/data.lock.json``), the push is refused with "Pull first"; ``force=True``
    replaces Cloud's newer changes. A push resolves the project again from its blueprint's
    latest version.

    Args:
        project_path: The project's root directory. Default: the current directory.
        remove: Remove entries from Cloud's registry that this project doesn't have.
        force: Push even when Cloud's registry changed since the last pull.
        confirm: True pushes without asking; False cancels. None asks in a console.
        dry_run: List the changes and send nothing.

    Returns:
        A dict: ``revision``, ``changes`` (Cloud's ``added``, ``changed``, ``removed``, and
        ``kept``), ``applied``, ``dry_run``, and ``message``.

    Raises:
        CloudError: Without a project token, offline, when the server has no data registry
            endpoint, or when Cloud's registry changed and ``force`` is False.
        FrameworkError: Without a console and without ``confirm=True`` or ``dry_run=True``.
    """
    from framework._log import inform, show_messages
    from framework.skill_pull import _root
    from framework.sync_all import _ask

    show_messages()
    root = _root(project_path)
    server, token = _connection(root)
    plan = _push_plan(root, server, token, remove, force)
    if plan["pending"]:
        lines = ["Data registry changes for Framework Cloud:", *plan["lines"], *plan["warnings"]]
    else:
        lines = ["Data registry on Framework Cloud already matches this project.", *plan["lines"]]
    for line in lines:
        inform(line)
    result: dict[str, Any] = {
        "revision": plan["revision"],
        "changes": plan["changes"],
        "applied": False,
        "dry_run": dry_run,
        "message": "\n".join(lines),
    }
    if not plan["pending"] or dry_run:
        return result

    if not _ask("Push these changes?", confirm, "push_data_registry"):
        inform("Data registry not pushed.")
        return result
    reply = _push_apply(root, plan, server)
    result.update(applied=reply["applied"], revision=reply["revision"])
    return result
