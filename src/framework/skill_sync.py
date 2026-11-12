"""Versioned skill bundles and conservative bidirectional synchronization."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import tempfile
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Any
from urllib.parse import quote

EXCLUDED = {
    ".git",
    ".framework",
    ".framework-skill.json",
    "__pycache__",
    "node_modules",
    ".env",
    "id_rsa",
}


def _excluded(name: str) -> bool:
    return (
        name.lower() in EXCLUDED
        or name.lower().startswith(".env.")
        or name.lower().endswith((".pem", ".key"))
    )


def validate(files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if len(files) > 256:
        raise ValueError("A skill supports at most 256 files")
    result = []
    seen: set[str] = set()
    total = 0
    for file in files:
        path = file["path"]
        if (
            not isinstance(path, str)
            or len(path) > 240
            or not re.fullmatch(r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*", path)
        ):
            raise ValueError("Invalid skill path")
        if (
            any(part in (".", "..") or _excluded(part) for part in path.split("/"))
            or path.lower() in seen
        ):
            raise ValueError("Excluded or duplicate skill path")
        seen.add(path.lower())
        if path == "SKILL.md" and file.get("executable", False):
            raise ValueError("SKILL.md cannot be executable")
        raw = base64.b64decode(file["content"], validate=True)
        total += len(raw)
        if len(raw) > 4 * 1024 * 1024 or total > 16 * 1024 * 1024:
            raise ValueError("Skill bundle exceeds the size limit")
        result.append(
            {
                "path": path,
                "content": base64.b64encode(raw).decode("ascii"),
                "executable": bool(file.get("executable", False)),
            }
        )
    if "SKILL.md" not in [f["path"] for f in result]:
        raise ValueError("SKILL.md is required")
    if any(b.startswith(a + "/") for a in seen for b in seen):
        raise ValueError("A path cannot be both a file and a directory")
    return sorted(result, key=lambda f: f["path"])


def bundle_hash(files: list[dict[str, Any]]) -> str:
    manifest = "framework-skill-bundle-v1\n"
    for file in validate(files):
        raw = base64.b64decode(file["content"])
        manifest += (
            f"{file['path']}\t{hashlib.sha256(raw).hexdigest()}\t"
            f"{755 if file['executable'] else 644}\t{len(raw)}\n"
        )
    return hashlib.sha256(manifest.encode("ascii")).hexdigest()


def scan(path: Path) -> list[dict[str, Any]]:
    if path.is_symlink():
        raise ValueError("Skill symlinks are unsupported")
    files = []
    mode_file = path / ".framework-skill.json"
    executable_paths = (
        set(json.loads(mode_file.read_text(encoding="utf-8")).get("executable_paths", []))
        if os.name == "nt" and mode_file.is_file()
        else set()
    )
    for directory, dirs, names in os.walk(path, followlinks=False):
        for name in dirs + names:
            if (Path(directory) / name).is_symlink():
                raise ValueError("Skill symlinks are unsupported")
        dirs[:] = [name for name in dirs if not _excluded(name)]
        for name in names:
            if _excluded(name):
                continue
            file = Path(directory) / name
            if not file.is_file():
                raise ValueError("Only regular skill files are supported")
            if file.stat().st_size > 4 * 1024 * 1024:
                raise ValueError("Skill file exceeds 4 MB")
            files.append(
                {
                    "path": file.relative_to(path).as_posix(),
                    "content": base64.b64encode(file.read_bytes()).decode("ascii"),
                    "executable": (
                        file.relative_to(path).as_posix() in executable_paths
                        if os.name == "nt"
                        else bool(file.stat().st_mode & 0o111)
                    ),
                }
            )
    return validate(files)


def _path(root: Path, relative: str) -> Path:
    p = Path(relative)
    if p.is_absolute() or not p.parts or any(part in (".", "..") for part in p.parts):
        raise ValueError("Skill paths must be relative to the project")
    path = root
    for part in p.parts:
        path = path / part
        if path.is_symlink():
            raise ValueError("Skill paths cannot contain symlinks")
    return path


def _load(root: Path) -> dict[str, Any]:
    path = _path(root, ".framework/skills.lock.json")
    state = json.loads(path.read_text()) if path.exists() else {"schema": 1, "skills": {}}
    if state.get("schema") != 1 or not isinstance(state.get("skills"), dict):
        raise ValueError("Unsupported skill lock file")
    return state


def _save(root: Path, state: dict[str, Any]) -> None:
    path = _path(root, ".framework/skills.lock.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    _private(root)
    fd, temp = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(state, stream, indent=2)
            stream.write("\n")
        Path(temp).replace(path)
    finally:
        if Path(temp).exists():
            Path(temp).unlink()


@contextmanager
def _lock(root: Path) -> Iterator[None]:
    lock = _path(root, ".framework/skills.sync.lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    try:
        lock.mkdir()
    except FileExistsError:
        raise ValueError(
            "Skill sync is locked. If the previous process stopped, "
            "remove .framework/skills.sync.lock and retry."
        ) from None
    try:
        yield
    finally:
        lock.rmdir()


def _install(
    root: Path, paths: list[str], files: list[dict[str, Any]], expected: str | None
) -> None:
    files = validate(files)
    swaps: list[tuple[Path, Path, Path, bool]] = []
    try:
        for relative in paths:
            target = _path(root, relative)
            if target.exists() and bundle_hash(scan(target)) != expected:
                raise ValueError("Local skill changed during sync")
            target.parent.mkdir(parents=True, exist_ok=True)
            backup = _path(root, ".framework/skills-backups/" + str(uuid.uuid4()))
            stage = Path(tempfile.mkdtemp(prefix=".skill-stage-", dir=target.parent))
            swaps.append((target, stage, backup, target.exists()))
            if target.exists():
                _private(root)
                backup.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                shutil.copytree(target, stage, dirs_exist_ok=True, symlinks=True)
                keep = {f["path"] for f in files}
                for old in scan(target):
                    if old["path"] not in keep:
                        removed = stage / old["path"]
                        removed.unlink()
                        parent = removed.parent
                        while parent != stage and not any(parent.iterdir()):
                            parent.rmdir()
                            parent = parent.parent
            for file in files:
                dest = _path(stage, file["path"])
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(base64.b64decode(file["content"]))
                dest.chmod(0o755 if file["executable"] else 0o644)
            if os.name == "nt":
                mode_file = stage / ".framework-skill.json"
                executable_paths = [file["path"] for file in files if file["executable"]]
                if executable_paths:
                    mode_file.write_text(
                        json.dumps({"executable_paths": executable_paths}), encoding="utf-8"
                    )
                elif mode_file.is_file():
                    mode_file.unlink()
            if bundle_hash(scan(stage)) != bundle_hash(files):
                raise ValueError("Staged skill verification failed")
        for target, stage, backup, existed in swaps:
            if existed and bundle_hash(scan(target)) != expected:
                raise ValueError("Local skill changed during sync")
            if existed:
                target.rename(backup)
            stage.rename(target)
    except BaseException:
        for target, stage, backup, existed in reversed(swaps):
            if backup.exists():
                if target.exists():
                    shutil.rmtree(target)
                backup.rename(target)
            elif not stage.exists() and not existed and target.exists():
                shutil.rmtree(target)
            if stage.exists():
                shutil.rmtree(stage)
        raise
    # Backups retain the replaced bytes for recovery and explicit conflict resolution.


def _project_token(root: Path, server: str) -> str | None:
    """The project's token when the project is on this server, else None.

    A push that names the project lets Cloud list a pushed copy of a built-in
    skill in the project's setup, so the next pull keeps it.
    """
    from framework.cloud import cloud_server, project_token

    token = project_token(root)
    if not token:
        return None
    if cloud_server(scope="project", project_dir=root).url.rstrip("/") != server:
        return None
    return token


def skills_sync(
    path: str,
    project_path: str | Path = ".",
    *,
    direction: str = "auto",
    dry_run: bool = False,
    skill_id: str | None = None,
    variant: str = "python",
    resolve: str | None = None,
) -> dict[str, Any]:
    """Sync one skill directory. Divergent edits stop without changing either side.

    ``path`` is project-relative. ``skill_id`` explicitly adopts an existing Cloud
    skill. An untracked directory without an ID becomes a new private skill.
    ``dry_run`` reports the action without writing files or creating versions.
    ``resolve="local"`` or ``resolve="remote"`` explicitly selects a conflict
    result. Replaced local files are retained in .framework/skills-backups.

    Args:
        path: Project-relative skill directory.
        project_path: Project root directory.
        direction: Sync direction: auto, push, pull, or status.
        dry_run: Report the next action without writing files or Cloud versions.
        skill_id: Explicit Cloud identity for adoption or an initial pull.
        variant: Language variant: r, python, or stata.
        resolve: Optional conflict choice: local or remote.

    Returns:
        The action, skill identity, hashes, paths, and resulting version.
    """
    from framework.cloud import CloudConflictError, _require_token, api, cloud_server

    if (
        direction not in ("auto", "push", "pull", "status")
        or variant not in ("r", "python", "stata")
        or resolve not in (None, "local", "remote")
    ):
        raise ValueError("Invalid skill sync direction or variant")
    root = Path(project_path).expanduser().resolve()
    server = cloud_server().url.rstrip("/")
    token = _require_token(server)
    with _lock(root):
        state = _load(root)
        matches = [(k, e) for k, e in state["skills"].items() if path in e["paths"]]
        entry: dict[str, Any]
        if matches:
            key, entry = matches[0]
        else:
            key = path
            entry = {"paths": [path], "variant": variant}
        if entry.get("server") and entry["server"] != server:
            raise ValueError("This skill is tracked on another server")
        if skill_id and entry.get("skill_id") and skill_id != entry["skill_id"]:
            raise ValueError("This directory already tracks another skill")
        variant = entry.get("variant", variant)
        paths = entry["paths"]
        bundles = [scan(_path(root, p)) if _path(root, p).exists() else None for p in paths]
        hashes = [bundle_hash(b) if b else None for b in bundles]
        if len(set(hashes)) > 1:
            raise ValueError(
                "Assistant skill copies diverged. Reconcile the aliases before syncing."
            )
        local_hash, files = hashes[0], bundles[0]
        identity = entry.get("skill_id") or skill_id
        remote = None
        if identity:
            remote = api(
                f"/api/v1/skills/sync/{quote(identity, safe='')}?variant={variant}", token, server
            )
        elif entry.get("origin"):
            origin = entry["origin"]
            try:
                remote = api(
                    f"/api/v1/skills/sync/{quote(origin['skill_id'], safe='')}"
                    f"?variant={variant}&hash={origin['hash']}",
                    token,
                    server,
                )
            except CloudConflictError:
                # Cloud never published this built-in copy (the package shipped its own
                # text): an edited one is pushed as a new skill of your own
                remote = None
        if remote and bundle_hash(remote["bundle"]) != remote["hash"]:
            raise ValueError("Cloud skill verification failed")
        base = entry.get("hash") if entry.get("skill_id") else entry.get("origin", {}).get("hash")
        if not remote:
            if not files:
                action = "missing"
            elif not entry.get("skill_id") and local_hash == base:
                action = "unchanged"
            else:
                action = "push"
        elif local_hash == remote["hash"]:
            action = "adopt" if not entry.get("skill_id") else "unchanged"
        elif local_hash is None:
            action = "pull" if base is None and skill_id else "conflict"
        elif base is None:
            action = "conflict"
        elif local_hash == base:
            action = "pull"
        elif remote["hash"] == base:
            action = "push"
        else:
            action = "conflict"
        if action == "conflict" and resolve and remote:
            action = (
                "push"
                if resolve == "local" and files
                else "pull"
                if resolve == "remote"
                else "conflict"
            )
        if direction in ("push", "pull") and action in ("push", "pull") and direction != action:
            action = "blocked"
        result = {
            "action": action,
            "skill_id": remote["skill_id"] if remote else None,
            "local_hash": local_hash,
            "remote_hash": remote["hash"] if remote else None,
            "paths": paths,
        }
        if dry_run or direction == "status" or action in ("conflict", "blocked", "missing"):
            return result
        if action == "push":
            expected = (
                remote["version"]
                if resolve == "local" and remote and not remote.get("system")
                else entry.get("version", remote["version"] if remote else 0)
            )
            basis = f"{expected}:{remote['skill_id'] if remote else ''}:{variant}"
            pending = entry.get("pending")
            if not pending or pending["hash"] != local_hash or pending.get("basis") != basis:
                pending = {"request_id": str(uuid.uuid4()), "hash": local_hash, "basis": basis}
            entry["pending"] = pending
            state["skills"][key] = entry
            _save(root, state)  # Retry identity survives a lost HTTP response.
            body = {
                "request_id": pending["request_id"],
                "skill_id": remote["skill_id"] if remote else None,
                "expected_version": expected,
                "variant": variant,
                "bundle": files,
                "client": {"app": "framework-py"},
            }
            if named := _project_token(root, server):
                body["project_token"] = named
            remote = api("/api/v1/skills/sync", token, server, method="POST", body=body)
        if remote:
            if bundle_hash(remote["bundle"]) != remote["hash"]:
                raise ValueError("Cloud skill verification failed")
            if action == "pull":
                _install(root, paths, remote["bundle"], local_hash)
            entry.update(
                server=server,
                skill_id=remote["skill_id"],
                version=remote["version"],
                hash=remote["hash"],
                variant=variant,
            )
            entry.pop("pending", None)
            state["skills"][key] = entry
            _save(root, state)
            result.update(skill_id=remote["skill_id"], version=remote["version"])
        return result


def _source_files(folder: Traversable, prefix: str = "") -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for file in folder.iterdir():
        if _excluded(file.name):
            continue
        path = prefix + file.name
        if file.is_dir():
            out.extend(_source_files(file, path + "/"))
        else:
            out.append(
                {
                    "path": path,
                    "content": base64.b64encode(file.read_bytes()).decode("ascii"),
                    "executable": False,
                }
            )
    return out


def _shipped_history() -> dict[str, list[str]]:
    from importlib import resources

    record = resources.files("framework").joinpath("templates/skill-history.json")
    try:
        data = json.loads(record.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    skills = data.get("skills") if isinstance(data, dict) else None
    return skills if isinstance(skills, dict) else {}


def shipped_release(target: Path, name: str) -> bool:
    """Whether a skill folder is an unedited copy of a SKILL.md framework-py shipped: its
    only file is SKILL.md, and its SHA-256 (as stored, or with CRLF turned into LF) is in
    ``templates/skill-history.json``. Projects made before ``skills.lock.json`` have such
    copies with no lock entry."""
    files = scan(target) if target.is_dir() else []
    if [f["path"] for f in files] != ["SKILL.md"]:
        return False
    raw = base64.b64decode(files[0]["content"])
    known = set(_shipped_history().get(name) or [])
    return any(
        hashlib.sha256(data).hexdigest() in known for data in (raw, raw.replace(b"\r\n", b"\n"))
    )


def package_status(source: Traversable, root: Path, relative: str, name: str) -> str:
    """What installing a package skill would do: ``create`` (missing), ``update`` (a clean
    copy of an older version), ``current``, ``edited`` (changed locally, kept), or
    ``account`` (synced from an account skill, kept)."""
    target = _path(root, relative)
    state = _load(root)
    found = next((e for e in state["skills"].values() if relative in e["paths"]), None)
    if found and found.get("skill_id") and not found["skill_id"].startswith("framework:"):
        return "account"
    fresh = bundle_hash(validate(_source_files(source)))
    current = bundle_hash(scan(target)) if target.exists() else None
    if current is None:
        return "create"
    if current == fresh:
        return "current"
    if found:
        return "update" if current == found.get("hash") else "edited"
    return "update" if shipped_release(target, name) else "edited"


def _install_package(
    source: Traversable, root: Path, relative: str, name: str, *, force: bool = False
) -> bool:
    """Refresh a clean package installation. Unknown or edited copies are preserved; a copy
    of a released SKILL.md with no lock entry is clean. ``force`` replaces an edited copy,
    backing it up first."""
    status = package_status(source, root, relative, name)
    if status in ("account", "edited") and not force:
        return False
    target = _path(root, relative)
    files = validate(_source_files(source))
    fresh = bundle_hash(files)
    current = bundle_hash(scan(target)) if target.exists() else None
    if current != fresh:
        _install(root, [relative], files, current)
    state = _load(root)
    # Exact shipped snapshots identify the canonical source even when SKILL.md has no version.
    key = next((k for k, e in state["skills"].items() if relative in e["paths"]), "package:" + name)
    entry = state["skills"].setdefault(key, {"paths": [], "variant": "python"})
    if relative not in entry["paths"]:
        entry["paths"].append(relative)
    entry.update(hash=fresh, origin={"skill_id": "framework:" + name, "hash": fresh})
    for field in ("skill_id", "version", "server", "pending"):
        entry.pop(field, None)
    _save(root, state)
    return True


def _install_cloud(
    root: Path,
    relative: str,
    entry: dict[str, Any],
    *,
    force: bool = False,
    clean: tuple[str, ...] = (),
) -> bool:
    """Install a Cloud skill bundle. A copy that matches neither the lock nor ``clean``
    (other unedited versions) is kept, unless ``force`` replaces it after a backup."""
    source = entry["sync"]
    files = validate(entry["bundle"])
    if bundle_hash(files) != source["hash"]:
        raise ValueError("Cloud skill verification failed")
    state = _load(root)
    key = next(
        (k for k, e in state["skills"].items() if relative in e["paths"]),
        f"account:{source['server']}:{source['skill_id']}:{source['variant']}",
    )
    tracked = state["skills"].get(key, {"paths": [], "variant": source["variant"]})
    target = _path(root, relative)
    current = bundle_hash(scan(target)) if target.exists() else None
    unedited = current in (tracked.get("hash", source["hash"]), *clean)
    if current and not unedited and not force:
        return False
    if current != source["hash"]:
        _install(root, [relative], files, current)
    if relative not in tracked["paths"]:
        tracked["paths"].append(relative)
    tracked.update(source)
    state["skills"][key] = tracked
    _save(root, state)
    return True


def _private(root: Path) -> None:
    path = _path(root, ".framework/.gitignore")
    path.parent.mkdir(parents=True, exist_ok=True)
    text = path.read_text() if path.exists() else ""
    for line in ("skills-backups/", "skills.sync.lock/"):
        if line not in text.splitlines():
            text = text.rstrip("\n") + "\n" + line + "\n"
    path.write_text(text)


def install_package(source: Traversable, root: Path, relative: str, name: str) -> bool:
    with _lock(root):
        return _install_package(source, root, relative, name)


def install_cloud(root: Path, relative: str, entry: dict[str, Any]) -> bool:
    with _lock(root):
        return _install_cloud(root, relative, entry)
