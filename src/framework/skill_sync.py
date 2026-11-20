"""Versioned skill bundles and conservative bidirectional synchronization."""

from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from importlib.resources.abc import Traversable
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any
from urllib.parse import quote

from framework.errors import FrameworkError

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


def hash_bytes(raw: bytes) -> bytes:
    """Text files hash with LF line endings, so a CRLF checkout (git's
    core.autocrlf on Windows) matches its LF twin. A file with a NUL byte is
    binary and hashes raw. R, Go, and Cloud normalize the same way."""
    return raw if b"\0" in raw else raw.replace(b"\r\n", b"\n")


def bundle_hash(files: list[dict[str, Any]]) -> str:
    manifest = "framework-skill-bundle-v1\n"
    for file in validate(files):
        raw = hash_bytes(base64.b64decode(file["content"]))
        manifest += (
            f"{file['path']}\t{hashlib.sha256(raw).hexdigest()}\t"
            f"{755 if file['executable'] else 644}\t{len(raw)}\n"
        )
    return hashlib.sha256(manifest.encode("ascii")).hexdigest()


def _windows() -> bool:
    return os.name == "nt"


def _executable_paths(mode_file: Path) -> set[str]:
    """The executable files a ``.framework-skill.json`` lists. Written as a JSON array;
    a single string (which older R versions wrote for one file) is read as one path."""
    if not mode_file.is_file():
        return set()
    value = json.loads(mode_file.read_text(encoding="utf-8")).get("executable_paths", [])
    if isinstance(value, str):
        return {value}
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        return set(value)
    raise ValueError(f"Invalid executable_paths in {mode_file}")


def _raise(error: OSError) -> None:
    raise error


def scan(path: Path) -> list[dict[str, Any]]:
    """A skill folder's syncable files. Excluded names (``.env``, keys, ``.git``, ...) are
    skipped; symlinks and unreadable folders are errors."""
    if path.is_symlink():
        raise ValueError("Skill symlinks are unsupported")
    files = []
    windows = _windows()
    executable_paths = _executable_paths(path / ".framework-skill.json") if windows else set()
    for directory, dirs, names in os.walk(path, onerror=_raise, followlinks=False):
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
                        if windows
                        else bool(file.stat().st_mode & 0o111)
                    ),
                }
            )
    return validate(files)


def relative_parts(text: str) -> list[str]:
    """A project-relative path's parts, checked as given: absolute paths, drive-qualified
    paths (``D:skills``), ``..`` components, and any part with a ``:`` (a drive or an
    alternate data stream on Windows) are refused, on every platform, before anything is
    normalized. Both ``/`` and ``\\`` separate parts."""
    windows = PureWindowsPath(text)
    parts = re.split(r"[\\/]", text)
    if (
        not text
        or PurePosixPath(text).is_absolute()
        or windows.drive
        or windows.root
        or any(part == ".." or ":" in part for part in parts)
    ):
        raise ValueError("Skill paths must be relative to the project")
    return [part for part in parts if part not in ("", ".")]


def on_disk(root: Path, relative: str) -> str:
    """A project-relative path in the spelling its folders have on disk. On a
    case-insensitive volume ``.AGENTS/skills`` is ``.agents/skills``. Parts that don't
    exist yet, or sit below a file or a symlink, keep the spelling given: resolution never
    follows links."""
    parts = relative_parts(relative)
    here, out = root, []
    for i, part in enumerate(parts):
        candidate = here / part
        try:
            found = lstat(candidate)
        except NotADirectoryError:
            found = None
        if found is None:
            out += parts[i:]
            break
        real = part
        with os.scandir(here) as entries:
            for entry in entries:
                if entry.name.casefold() == part.casefold():
                    # Not entry.stat(): on Windows its st_ino and st_dev are always 0
                    mine = os.lstat(entry.path)
                    if (mine.st_dev, mine.st_ino) == (found.st_dev, found.st_ino):
                        real = entry.name
                        break
        out.append(real)
        if stat.S_ISLNK(found.st_mode):
            out += parts[i + 1 :]
            break
        here = here / real
    return "/".join(out)


def lstat(path: Path) -> os.stat_result | None:
    """A path's lstat, or None when it doesn't exist. Other failures (permissions, a file
    where a folder should be) raise, so a lookup error is never taken for absence."""
    try:
        return path.lstat()
    except FileNotFoundError as e:
        # Windows says "path not found" (not NotADirectoryError) below a file
        if getattr(e, "winerror", None) == 3:
            for parent in path.parents:
                try:
                    mode = parent.lstat().st_mode
                except FileNotFoundError:
                    continue
                if not stat.S_ISDIR(mode) and not stat.S_ISLNK(mode):
                    raise NotADirectoryError(str(path)) from None
                break
        return None


def _path(root: Path, relative: str) -> Path:
    p = Path(relative)
    relative_parts(relative)
    if p.is_absolute() or not p.parts or any(part in (".", "..") for part in p.parts):
        raise ValueError("Skill paths must be relative to the project")
    path = root
    for part in p.parts:
        path = path / part
        try:
            found = lstat(path)
        except NotADirectoryError:
            found = None  # A file part-way: nothing below it exists
        if found is None:
            return root.joinpath(*p.parts)  # Nothing below a missing part is a symlink
        if stat.S_ISLNK(found.st_mode):
            raise ValueError("Skill paths cannot contain symlinks")
    return path


class SkillRestoreError(FrameworkError):
    """An install failed and a replaced copy could not be put back. ``stuck`` lists each
    (target, backup) pair, relative to the project: the target may be missing or
    partial, and its old copy is in the backup (None when there was no old copy)."""

    def __init__(self, message: str, stuck: list[tuple[str, str | None]]) -> None:
        super().__init__(message)
        self.stuck = stuck


class SkillRollbackError(FrameworkError):
    """An install failed and was rolled back. ``kept`` lists each (target, saved) pair,
    relative to the project: the copy the install had written at target, moved to
    ``saved`` in ``.framework/skills-backups/`` rather than deleted."""

    def __init__(self, message: str, kept: list[tuple[str, str]]) -> None:
        super().__init__(message)
        self.kept = kept


def set_aside(root: Path, target: Path) -> tuple[str, str]:
    """Move a copy into ``.framework/skills-backups/`` and return (target, where it went),
    relative to the project."""
    saved = _path(root, ".framework/skills-backups/" + str(uuid.uuid4()))
    _private(root)
    saved.parent.mkdir(parents=True, exist_ok=True)
    target.rename(saved)
    return target.relative_to(root).as_posix(), saved.relative_to(root).as_posix()


def set_aside_text(kept: list[tuple[str, str]]) -> str:
    if not kept:
        return ""
    moved = "; ".join(f"{t} -> {s}" for t, s in kept)
    return f" What it had written was moved, not deleted: {moved}."


def _is_dir(path: Path) -> bool:
    """Whether a path is a real folder, not a symlink to one."""
    try:
        found = lstat(path)
    except NotADirectoryError:
        return False
    return found is not None and stat.S_ISDIR(found.st_mode)


def _dir_modes(path: Path) -> dict[str, int]:
    """Each real folder's mode bits in a skill (setuid, setgid, and sticky included), by
    path relative to it ("" for itself). Symlinks, even to folders, are left out, so
    restoring the modes never changes what a link points to."""
    modes = {"": stat.S_IMODE(path.lstat().st_mode)}
    for directory, dirs, _ in os.walk(path, onerror=_raise, followlinks=False):
        for name in dirs:
            folder = Path(directory) / name
            found = folder.lstat()
            if stat.S_ISDIR(found.st_mode):
                modes[folder.relative_to(path).as_posix()] = stat.S_IMODE(found.st_mode)
    return modes


LOCK_FILE = ".framework/skills.lock.json"


def _read(root: Path) -> tuple[dict[str, Any], bool]:
    """The skill lock as v2 (a v1 lock is migrated in memory), with its paths in their
    on-disk spelling, and whether the file exists. A lock newer than v2 raises
    ``LockSchemaError``."""
    from framework._skill_plan import empty_lock, migrate

    path = _path(root, LOCK_FILE)
    if not path.exists():
        return empty_lock(), False
    lock = migrate(json.loads(path.read_text(encoding="utf-8")))
    # Lock paths in their on-disk spelling, so every lookup matches by filesystem identity
    # (an older lock may say .CLAUDE/skills/x for .claude/skills/x)
    for entry in lock["skills"].values():
        spelled: dict[str, Any] = {}
        for relative, record in entry["copies"].items():
            with contextlib.suppress(ValueError, OSError):
                relative = on_disk(root, relative)  # Else as stored: it fails on its own later
            spelled.setdefault(relative, record)
        entry["copies"] = spelled
    return lock, True


def _load(root: Path) -> dict[str, Any]:
    """The skill lock as v2. See ``_read()``."""
    return _read(root)[0]


def _save(root: Path, lock: dict[str, Any]) -> None:
    """Write the lock as v2 (see ``_skill_plan.dump()``)."""
    from framework._skill_plan import dump

    path = _path(root, LOCK_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    _private(root)
    fd, temp = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(dump(lock), stream, indent=2)
            stream.write("\n")
        Path(temp).replace(path)
    finally:
        if Path(temp).exists():
            Path(temp).unlink()


def _save_changed(root: Path, before: dict[str, Any], after: dict[str, Any]) -> bool:
    """Save ``after`` when it differs from ``before`` (both as read or planned). A lock
    that comes out unchanged is not rewritten, so a v1 file stays v1 until a command
    changes it. Returns whether the file was written."""
    from framework._skill_plan import dump

    if dump(before) == dump(after):
        return False
    _save(root, after)
    return True


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
) -> dict[str, str | None]:
    """Write a bundle to each path, staging it first. Each replaced copy is moved to
    ``.framework/skills-backups/<uuid>``. Returns each path's backup (None when new)."""
    files = validate(files)
    swaps: list[tuple[Path, Path, Path, bool]] = []
    try:
        for relative in paths:
            target = _path(root, relative)
            existed = lstat(target) is not None
            if existed and bundle_hash(scan(target)) != expected:
                raise ValueError("Local skill changed during sync")
            target.parent.mkdir(parents=True, exist_ok=True)
            backup = _path(root, ".framework/skills-backups/" + str(uuid.uuid4()))
            # Not mkdtemp: its 0700 would become the new skill folder's mode
            stage = target.parent / f".skill-stage-{uuid.uuid4().hex}"
            stage.mkdir()
            swaps.append((target, stage, backup, existed))
            modes: dict[str, int] = {}
            if existed:
                _private(root)
                backup.parent.mkdir(parents=True, exist_ok=True)
                modes = _dir_modes(target)
                shutil.copytree(target, stage, dirs_exist_ok=True, symlinks=True)
                for folder, mode in modes.items():
                    if _is_dir(stage / folder):
                        (stage / folder).chmod(mode | stat.S_IRWXU)  # writable while staging
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
            if _windows():
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
            # Rebuilt folders keep the permissions they had, deepest first
            # A folder the new copy replaced with a file keeps the file's mode
            for folder, mode in sorted(modes.items(), key=lambda m: -m[0].count("/") - len(m[0])):
                if _is_dir(stage / folder):
                    (stage / folder).chmod(mode)
        for target, stage, backup, existed in swaps:
            if existed and bundle_hash(scan(target)) != expected:
                raise ValueError("Local skill changed during sync")
            if existed:
                target.rename(backup)
            stage.rename(target)
    except BaseException as error:
        stuck: list[tuple[str, str | None]] = []
        kept: list[tuple[str, str]] = []
        for target, stage, backup, existed in reversed(swaps):
            try:
                # A copy this install wrote is moved aside, never deleted: it may hold an
                # edit made after the write
                if lstat(backup) is not None:
                    if lstat(target) is not None:
                        kept.append(set_aside(root, target))
                    backup.rename(target)
                elif lstat(stage) is None and not existed and lstat(target) is not None:
                    kept.append(set_aside(root, target))
            except OSError:
                stuck.append(
                    (target.relative_to(root).as_posix(), backup.relative_to(root).as_posix())
                )
            try:
                if lstat(stage) is not None:
                    shutil.rmtree(stage)
            except OSError:
                pass  # A leftover stage folder starts with a dot and is never read as a skill
        if stuck:
            detail = "; ".join(f"{t} (old copy in {b})" for t, b in stuck)
            raise SkillRestoreError(
                f"Installing the skill failed ({error}), and the old copy could not be put "
                f"back: {detail}.{set_aside_text(kept)}",
                stuck,
            ) from error
        if kept:
            raise SkillRollbackError(
                f"Installing the skill failed ({error}). The old copies were put back."
                f"{set_aside_text(kept)}",
                kept,
            ) from error
        raise
    # Backups retain the replaced bytes for recovery and explicit conflict resolution.
    return {
        target.relative_to(root).as_posix(): (
            backup.relative_to(root).as_posix() if existed else None
        )
        for target, _, backup, existed in swaps
    }


def assistant_folders() -> dict[str, str]:
    """Assistant folder names and aliases -> skills folders: claude -> .claude/skills,
    agents (and its assistants, codex and copilot) -> .agents/skills."""
    from framework.spec.check import ASSISTANTS

    names = {folder.split("/")[0].lstrip("."): folder for folder, _ in ASSISTANTS.values()}
    names.update({name: folder for name, (folder, _) in ASSISTANTS.items()})
    return names


def copy_call(source: str, target: str) -> str | None:
    """The skills_copy() call that copies the skill at ``source`` to ``target``, or None
    when no call can: skills_copy() keeps a skill's folder name, so a copy under another
    name (a renamed adoption) is fixed by hand."""
    if PurePosixPath(source).name != PurePosixPath(target).name:
        return None
    short: dict[str, str] = {}
    for alias, skills_dir in assistant_folders().items():
        short.setdefault(skills_dir, alias)
    a, b = PurePosixPath(source).parent.as_posix(), PurePosixPath(target).parent.as_posix()
    skill = f"skills=[{json.dumps(PurePosixPath(source).name)}]"
    if a in short and b in short:
        return f"skills_copy(from_={json.dumps(short[a])}, to={json.dumps(short[b])}, {skill})"
    return f"skills_copy(from_dir={json.dumps(a)}, to_dir={json.dumps(b)}, {skill})"


def diverged_parts(paths: list[str], hashes: list[str | None]) -> tuple[str, str | None]:
    """Why a skill's tracked copies diverged (one sentence), and the skills_copy() calls
    that make them agree (None when the folders' names differ, so no call can). The
    calls copy the first existing copy over every other one, so running them once
    reconciles the entry; a missing copy is never the source."""
    present = [(p, h) for p, h in zip(paths, hashes, strict=True) if h is not None]
    first = present[0][0]
    others = [p for p in paths if p != first]
    calls = [copy_call(first, p) for p in others]
    hint = "; ".join(c for c in calls if c) if all(calls) else None
    gone = [p for p, h in zip(paths, hashes, strict=True) if h is None]
    if len({h for _, h in present}) == 1 and gone:
        verb = "is" if len(gone) == 1 else "are"
        others = gone
        calls = [copy_call(first, p) for p in gone]
        hint = "; ".join(c for c in calls if c) if all(calls) else None
        return f"{first} exists and {_listed(gone)} {verb} missing.", hint
    return f"{_listed([first, *others])} differ.", hint


def _listed(paths: list[str]) -> str:
    return paths[0] if len(paths) == 1 else f"{', '.join(paths[:-1])} and {paths[-1]}"


def _diverged(paths: list[str], hashes: list[str | None]) -> str:
    """The message for a skill whose tracked copies diverged, with the command that fixes
    it: "<path> and <path> differ. Pick one and copy it over the other: <command>"."""
    reason, hint = diverged_parts(paths, hashes)
    if hint is None:
        return (
            f"{reason} Their folders have different names, so skills_copy() can't copy "
            "between them: copy the one to keep over the others by hand."
        )
    existing = {h for h in hashes if h is not None}
    if None in hashes and len(existing) == 1:
        return f"{reason} Copy it over: {hint}"
    return f"{reason} Pick one and copy it over the other: {hint}"


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
    from framework._skill_plan import after_push, identity_for, key_of, owner_of
    from framework.cloud import CloudConflictError, _require_token, api, cloud_server

    if (
        direction not in ("auto", "push", "pull", "status")
        or variant not in ("r", "python", "stata")
        or resolve not in (None, "local", "remote")
    ):
        raise ValueError("Invalid skill sync direction or variant")
    root = Path(project_path).expanduser().resolve()
    _path(root, path)  # Symlinks are refused as typed, before aliases are resolved
    path = on_disk(root, path)  # The lock records each folder as it is spelled on disk
    server = cloud_server().url.rstrip("/")
    token = _require_token(server)
    with _lock(root):
        lock = _load(root)
        before = json.loads(json.dumps(lock))
        key = owner_of(lock, path)
        if key is None and skill_id:
            # skill_id names a skill the lock tracks: this path is one more copy of it
            wanted = key_of(identity_for(skill_id, variant, server))
            if wanted in lock["skills"]:
                key = wanted
                lock["skills"][key]["copies"][path] = {"hash": None}
        if key is None:
            # Never a key already in use: saving must not overwrite another entry
            key, n = f"local:{path}", 2
            while key in lock["skills"]:
                key, n = f"local:{path}#{n}", n + 1
            lock["skills"][key] = {
                "identity": {"kind": "local", "variant": variant},
                "base": None,
                "known": [],
                "copies": {path: {"hash": None}},
            }
        entry = lock["skills"][key]
        identity = entry["identity"]
        base = entry.get("base") or {}
        if identity["kind"] == "cloud" and identity.get("server") != server:
            raise ValueError("This skill is tracked on another server")
        if (
            skill_id
            and (identity["kind"] == "cloud" or base.get("channel") == "cloud")
            and skill_id != identity.get("skill_id")
        ):
            raise ValueError("This directory already tracks another skill")
        variant = identity["variant"]
        # Edited copies first, so a diverged message copies an edit, never over one
        paths = sorted(
            entry["copies"],
            key=lambda p: (
                _path(root, p).exists()
                and bundle_hash(scan(_path(root, p))) == entry["copies"][p].get("hash"),
                p,
            ),
        )
        bundles = [scan(_path(root, p)) if _path(root, p).exists() else None for p in paths]
        hashes = [bundle_hash(b) if b else None for b in bundles]
        if len(set(hashes)) > 1:
            raise ValueError(_diverged(paths, hashes))
        local_hash, files = hashes[0], bundles[0]
        # On Cloud: your own or a team's skill, or a built-in last delivered by Cloud
        on_cloud = identity["kind"] == "cloud" or base.get("channel") == "cloud"
        cloud_id = skill_id or (identity["skill_id"] if identity["kind"] == "cloud" else None)
        remote = None
        if cloud_id:
            remote = api(
                f"/api/v1/skills/sync/{quote(cloud_id, safe='')}?variant={variant}", token, server
            )
        elif identity["kind"] == "builtin" and base.get("hash"):
            # A package base: Cloud's copy of the version the package shipped. A base
            # Cloud delivered: Cloud's head, so a newer Cloud version is seen.
            qualified = f"&hash={base['hash']}" if base.get("channel") == "package" else ""
            try:
                remote = api(
                    f"/api/v1/skills/sync/{quote(identity['skill_id'], safe='')}"
                    f"?variant={variant}{qualified}",
                    token,
                    server,
                )
            except CloudConflictError:
                # Cloud never published this built-in copy (the package shipped its own
                # text): an edited one is pushed as a new skill of your own
                remote = None
        if remote and bundle_hash(remote["bundle"]) != remote["hash"]:
            raise ValueError("Cloud skill verification failed")
        last = base.get("hash")
        if not remote:
            if not files:
                action = "missing"
            elif not on_cloud and local_hash == last:
                action = "unchanged"
            else:
                action = "push"
        elif local_hash == remote["hash"]:
            action = "adopt" if not on_cloud else "unchanged"
        elif local_hash is None:
            action = "pull" if last is None and skill_id else "conflict"
        elif last is None:
            action = "conflict"
        elif local_hash == last:
            action = "pull"
        elif remote["hash"] == last:
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
            assert files is not None and local_hash is not None
            if resolve == "local" and remote and not remote.get("system"):
                expected = remote["version"]
            elif base.get("channel") == "cloud":
                expected = base.get("version") or 0
            else:
                expected = remote["version"] if remote else 0
            remote = post_push(
                root,
                lock,
                key,
                files,
                local_hash,
                int(expected),
                remote["skill_id"] if remote else None,
                variant,
                server,
                token,
            )
        if remote:
            if bundle_hash(remote["bundle"]) != remote["hash"]:
                raise ValueError("Cloud skill verification failed")
            if action == "pull":
                _install(root, paths, remote["bundle"], local_hash)
            lock = after_push(
                lock,
                key,
                {
                    "skill_id": remote["skill_id"],
                    "version": remote["version"],
                    "hash": remote["hash"],
                    "server": server,
                },
                # A pull wrote every copy; otherwise only copies holding these bytes
                None
                if action == "pull"
                else {p for p, h in zip(paths, hashes, strict=True) if h == remote["hash"]},
            )
            result.update(skill_id=remote["skill_id"], version=remote["version"])
        _save_changed(root, before, lock)
        return result


def post_push(
    root: Path,
    lock: dict[str, Any],
    key: str,
    files: list[dict[str, Any]],
    local_hash: str,
    expected: int,
    skill_id: str | None,
    variant: str,
    server: str,
    token: str,
) -> dict[str, Any]:
    """Send one skill's bundle to Cloud as a new version, and return Cloud's response.

    The entry's ``pending`` request is saved first, so a retry after a lost response
    reuses its request ID and Cloud doesn't make a second version. ``skill_id`` None
    makes a new skill of your own (a first push, or an edited built-in)."""
    from framework.cloud import api

    entry = lock["skills"][key]
    basis = f"{expected}:{skill_id or ''}:{variant}"
    pending = entry.get("pending")
    if not pending or pending.get("hash") != local_hash or pending.get("basis") != basis:
        pending = {"request_id": str(uuid.uuid4()), "hash": local_hash, "basis": basis}
    entry["pending"] = pending
    _save(root, lock)  # Retry identity survives a lost HTTP response.
    body = {
        "request_id": pending["request_id"],
        "skill_id": skill_id,
        "expected_version": expected,
        "variant": variant,
        "bundle": files,
        "client": {"app": "framework-py"},
    }
    if named := _project_token(root, server):
        body["project_token"] = named
    response: dict[str, Any] = api("/api/v1/skills/sync", token, server, method="POST", body=body)
    return response


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


def _shipped_history() -> dict[str, dict[str, list[str]]]:
    """``templates/skill-history.json``: the bundle hash of every version of each
    built-in skill framework-py has shipped, per variant."""
    from importlib import resources

    record = resources.files("framework").joinpath("templates/skill-history.json")
    try:
        data = json.loads(record.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    skills = data.get("skills") if isinstance(data, dict) else None
    if not isinstance(data, dict) or data.get("schema") != 2 or not isinstance(skills, dict):
        return {}
    return {n: v for n, v in skills.items() if isinstance(v, dict)}


VARIANT = "python"


def package_source(name: str) -> dict[str, Any]:
    """A built-in skill as this framework-py ships it, as a planner source: its bundle
    (None when it isn't shipped), hash, and known hashes (every shipped version)."""
    from framework.ai import _shipped

    folder = _shipped(name)
    bundle = validate(_source_files(folder)) if folder.is_dir() else None
    digest = bundle_hash(bundle) if bundle else None
    known = set((_shipped_history().get(name) or {}).get(VARIANT) or [])
    return {
        "name": name,
        "identity": {"kind": "builtin", "skill_id": f"framework:{name}", "variant": VARIANT},
        "channel": "package",
        "version": None,
        "server": None,
        "hash": digest,
        "known": sorted(known | ({digest} if digest else set())),
        "bundle": bundle,
    }


def _private(root: Path) -> None:
    path = _path(root, ".framework/.gitignore")
    path.parent.mkdir(parents=True, exist_ok=True)
    text = path.read_text() if path.exists() else ""
    for line in ("skills-backups/", "skills.sync.lock/"):
        if line not in text.splitlines():
            text = text.rstrip("\n") + "\n" + line + "\n"
    path.write_text(text)
