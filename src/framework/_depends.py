"""Cache dependencies: what ``cache_remember(depends_on=...)`` hashes into an entry's key.

Each dependency is one of:

- ``data``: a catalog name, hashed as its file (SHA-256, as data integrity hashes it).
- ``file``: a file path, relative to the project root (then the current folder).
- ``folder``: a folder path, hashed as the sorted list of its files' paths and hashes.
  Hidden files and folders and ``__pycache__`` are skipped.
- ``value``: any other value, hashed in this language only.

A string is always a reference (a catalog name, file, or folder) and must resolve, so a
misspelled name is an error rather than a silently hashed string. To hash a string value,
wrap it: ``{"rule": ">= 0"}``.

The entry's key is the SHA-256 of its dependencies' ``kind``, ``ref``, and ``hash``, one
line each, sorted. It is stored in framework.db's ``meta`` table under
``cache_depends:<name>``, as JSON with the key and the dependency list, so R can read and
write the same record.
"""

from __future__ import annotations

import hashlib
import json
import os
import pickle
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from framework import db
from framework.errors import DataNotFoundError, FrameworkError
from framework.integrity import file_hash
from framework.project import find_settings_file

META_PREFIX = "cache_depends:"
_SKIPPED_FOLDERS = frozenset({"__pycache__"})
_REFERENCES = ("data", "file", "folder")


@dataclass(frozen=True)
class Dependency:
    kind: str
    ref: str
    hash: str


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def folder_hash(folder: Path) -> str:
    """The SHA-256 of ``<path>\\t<file hash>\\n`` for every file below ``folder``, sorted by
    path (``/`` separators, relative to the folder)."""
    lines = []
    for current, folders, files in os.walk(folder):
        folders[:] = [f for f in folders if not f.startswith(".") and f not in _SKIPPED_FOLDERS]
        for name in files:
            if name.startswith("."):
                continue
            path = Path(current) / name
            lines.append(f"{path.relative_to(folder).as_posix()}\t{file_hash(path)}\n")
    return _sha256("".join(sorted(lines)).encode("utf-8"))


def value_hash(value: Any) -> str:
    """A value's hash. Only meaningful within one language."""
    if isinstance(value, pd.DataFrame | pd.Series):
        frame = value.to_frame() if isinstance(value, pd.Series) else value
        digest = hashlib.sha256()
        digest.update(json.dumps([str(c) for c in frame.columns]).encode("utf-8"))
        digest.update(json.dumps([str(t) for t in frame.dtypes]).encode("utf-8"))
        digest.update(pd.util.hash_pandas_object(frame, index=True).to_numpy().tobytes())
        return digest.hexdigest()
    try:
        text = json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=True)
        return _sha256(f"json:{text}".encode())
    except (TypeError, ValueError):
        return _sha256(b"pickle:" + pickle.dumps(value, protocol=4))


def _root() -> Path | None:
    found = find_settings_file()
    return found.parent if found is not None else None


def _resolve_reference(ref: str) -> Dependency:
    from framework.catalog import data_info

    root = _root()
    candidate = Path(ref).expanduser()
    bases = [root] if root is not None else []
    if not candidate.is_absolute():
        for base in [*bases, Path.cwd()]:
            if (base / candidate).exists():
                candidate = base / candidate
                break
    if candidate.is_absolute() and candidate.is_dir():
        return Dependency("folder", ref, folder_hash(candidate))
    if candidate.is_absolute() and candidate.is_file():
        return Dependency("file", ref, file_hash(candidate))
    if "/" not in ref and "\\" not in ref:
        try:
            info = data_info(ref)
        except DataNotFoundError:
            info = None
        if info is not None and info.name is not None:
            if not info.path.is_file():
                raise FrameworkError(f"depends_on: the file for '{ref}' is missing: {info.path}")
            return Dependency("data", ref, file_hash(info.path))
    raise FrameworkError(
        f"depends_on: '{ref}' is not a catalog name, file, or folder. Strings name what the "
        'entry depends on; to hash a string value, wrap it, as in {"rule": ">= 0"}.'
    )


def resolve(depends_on: Any) -> list[Dependency]:
    """The dependencies of ``depends_on``: one item, or a list or tuple of items."""
    items = list(depends_on) if isinstance(depends_on, list | tuple) else [depends_on]
    return [
        _resolve_reference(item)
        if isinstance(item, str)
        else Dependency("value", f"value {position}", value_hash(item))
        for position, item in enumerate(items, start=1)
    ]


def key(dependencies: list[Dependency]) -> str:
    lines = sorted(f"{d.kind}\t{d.ref}\t{d.hash}\n" for d in dependencies)
    return _sha256("".join(lines).encode("utf-8"))


def recorded(name: str) -> tuple[str, list[Dependency]] | None:
    """The key and dependencies stored for a cache entry, or None."""
    text = db.get_meta(META_PREFIX + name)
    if not text:
        return None
    try:
        data = json.loads(text)
        dependencies = [Dependency(**item) for item in data["dependencies"]]
        return str(data["key"]), dependencies
    except (ValueError, KeyError, TypeError):
        return None


def record(name: str, dependencies: list[Dependency] | None) -> None:
    if dependencies is None:
        forget(name)
        return
    document = {"key": key(dependencies), "dependencies": [asdict(d) for d in dependencies]}
    db.set_meta(META_PREFIX + name, json.dumps(document))


def forget(name: str | None = None) -> None:
    """Remove the stored dependencies of one entry, or of every entry."""
    with db.connect() as connection:
        if name is None:
            connection.execute("DELETE FROM meta WHERE key LIKE ?", (META_PREFIX + "%",))
        else:
            connection.execute("DELETE FROM meta WHERE key = ?", (META_PREFIX + name,))


def recheck(dependencies: list[Dependency]) -> tuple[list[Dependency], list[str]]:
    """The stored dependencies hashed again, and the references that changed.

    Values cannot be hashed again without the caller, so they keep their stored hash. A
    reference that no longer resolves counts as changed.
    """
    current: list[Dependency] = []
    changed: list[str] = []
    for dependency in dependencies:
        if dependency.kind not in _REFERENCES:
            current.append(dependency)
            continue
        try:
            fresh = _resolve_reference(dependency.ref)
        except FrameworkError:
            changed.append(dependency.ref)
            continue
        current.append(fresh)
        if fresh.hash != dependency.hash:
            changed.append(dependency.ref)
    return current, changed


def changed_between(old: list[Dependency], new: list[Dependency]) -> list[str]:
    """Names for what differs between two dependency lists, for messages."""
    before = {(d.kind, d.ref): d.hash for d in old}
    after = {(d.kind, d.ref): d.hash for d in new}
    names = [ref for (kind, ref), digest in after.items() if before.get((kind, ref)) != digest]
    names += [ref for (kind, ref) in before if (kind, ref) not in after]
    return list(dict.fromkeys(names)) or ["the dependency list"]
