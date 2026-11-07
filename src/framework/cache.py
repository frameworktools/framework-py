"""Caching expensive results between runs.

Values are stored as files in the project's cache folder and tracked in the
``cache`` table of framework.db with a hash and an optional expiry. DataFrames
are stored as Parquet and other values with pickle. R stores its cache values
as ``.rds``, so a name R cached is a miss in Python (with a warning) and is
left in place for R.

Only load caches you trust: reading a pickle can run code.
"""

from __future__ import annotations

import functools
import os
import pickle
import re
import warnings
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any, TypeVar, overload

import pandas as pd

from framework import _depends, db
from framework._log import inform
from framework._paths import in_root, relative_to_root
from framework.errors import CacheWarning, FrameworkError
from framework.integrity import file_hash
from framework.project import project_root
from framework.settings import settings_read

T = TypeVar("T")

_FORMATS = (".parquet", ".pickle")
_R_FORMAT = ".rds"
_UNITS = {
    **dict.fromkeys(("second", "seconds", "sec", "secs", "s"), 1 / 3600),
    **dict.fromkeys(("minute", "minutes", "min", "mins"), 1 / 60),
    **dict.fromkeys(("hour", "hours", "hr", "hrs", "h"), 1),
    **dict.fromkeys(("day", "days", "d"), 24),
    **dict.fromkeys(("week", "weeks", "w"), 24 * 7),
}
_DURATION = re.compile(r"^([0-9]*\.?[0-9]+)\s*(" + "|".join(_UNITS) + r")$")


def normalize_expire_after(value: float | str | None, default: float | None = None) -> float | None:
    """Hours until expiry, from a number of hours or a string like ``"30 minutes"`` or
    ``"2 days"``. Seconds, minutes, hours, days, and weeks are accepted, as in R."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return default
    if isinstance(value, str):
        match = _DURATION.match(value.strip().lower())
        if match is None:
            raise FrameworkError(
                "Could not parse expire_after value. Use numbers (hours) or strings like "
                "'30 minutes', '2 hours', '1 day', '1 week'."
            )
        return float(match.group(1)) * _UNITS[match.group(2)]
    if value < 0:
        raise FrameworkError("expire_after must not be negative")
    return float(value)


def cache_dir() -> Path:
    """The cache folder: ``FW_CACHE_DIR``, then ``directories.cache`` in the settings."""
    custom = os.environ.get("FW_CACHE_DIR")
    root = project_root()
    if custom:
        return in_root(custom, root)
    config = settings_read()
    directories = config.get("directories") or {}
    options = config.get("options") or {}
    data_options = options.get("data") if isinstance(options, dict) else None
    folder = (directories.get("cache") if isinstance(directories, dict) else None) or (
        data_options.get("cache_dir") if isinstance(data_options, dict) else None
    )
    return in_root(folder or "outputs/cache", root)


def _default_expire() -> float | None:
    options = settings_read().get("options") or {}
    data_options = options.get("data") if isinstance(options, dict) else None
    value = data_options.get("cache_default_expire") if isinstance(data_options, dict) else None
    return normalize_expire_after(value)


def _row(name: str) -> dict[str, Any] | None:
    with db.connect() as connection:
        row = connection.execute(
            "SELECT name, file_path, hash, expire_at FROM cache WHERE name = ?", (name,)
        ).fetchone()
    return dict(row) if row is not None else None


def _candidates(name: str, file: str | Path | None, row: dict[str, Any] | None) -> list[Path]:
    if file is not None:
        return [Path(file)]
    root = project_root()
    paths = []
    if row and row.get("file_path"):
        paths.append(in_root(str(row["file_path"]), root))
    folder = cache_dir()
    paths.extend(folder / f"{name}{suffix}" for suffix in (*_FORMATS, _R_FORMAT))
    return paths


def _remove(name: str, file: str | Path | None) -> None:
    for path in _candidates(name, file, _row(name)):
        path.unlink(missing_ok=True)
    with db.connect() as connection:
        connection.execute("DELETE FROM cache WHERE name = ?", (name,))
    _depends.forget(name)


def cache(
    name: str,
    value: T,
    file: str | Path | None = None,
    expire_after: float | str | None = None,
    depends_on: Any = None,
) -> T:
    """Store a value in the cache and return it.

    Args:
        name: The cache name.
        value: The value. DataFrames are stored as Parquet, anything else with pickle.
        file: Store the value in this file instead of the cache folder.
        expire_after: Hours until the value expires, or a string like
            ``"30 minutes"`` or ``"2 days"``. Default: ``options.data.cache_default_expire`` in the
            settings, else never.
        depends_on: What the value was computed from, recorded with it: a catalog name, a
            file or folder path, any other value, or a list of these. See
            ``cache_remember()``.
    """
    dependencies = _depends.resolve(depends_on) if depends_on is not None else None
    return _store(name, value, file, expire_after, dependencies)


def _store(
    name: str,
    value: T,
    file: str | Path | None,
    expire_after: float | str | None,
    dependencies: list[_depends.Dependency] | None,
) -> T:
    is_frame = isinstance(value, pd.DataFrame)
    path = (
        Path(file) if file is not None else cache_dir() / f"{name}{_FORMATS[0 if is_frame else 1]}"
    )
    if not path.parent.is_dir():
        path.parent.mkdir(parents=True, exist_ok=True)
        inform(f"Creating cache directory: {path.parent}")

    if is_frame:
        value.to_parquet(path)  # type: ignore[attr-defined]
    else:
        with path.open("wb") as handle:
            pickle.dump(value, handle)

    hours = normalize_expire_after(expire_after, _default_expire())
    now = db.timestamp()
    expire_at = now + hours * 3600 if hours is not None else None
    root = project_root()
    record = (relative_to_root(path, root), file_hash(path), expire_at, now)
    with db.connect(root) as connection:
        if connection.execute("SELECT 1 FROM cache WHERE name = ?", (name,)).fetchone():
            connection.execute(
                "UPDATE cache SET file_path = ?, hash = ?, expire_at = ?, last_read_at = ?, "
                "updated_at = ? WHERE name = ?",
                (*record, now, name),
            )
        else:
            connection.execute(
                "INSERT INTO cache (name, file_path, hash, expire_at, last_read_at, created_at, "
                "updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (name, *record, now, now),
            )
    _depends.record(name, dependencies)
    return value


_MISSING: Any = object()


def _lookup(
    name: str,
    file: str | Path | None,
    dependencies: list[_depends.Dependency] | None = None,
    carried: list[list[_depends.Dependency]] | None = None,
) -> Any:
    """The cached value, or ``_MISSING``.

    With ``dependencies``, an entry recorded with a different key is out of date. Without
    them, an entry's recorded dependencies are hashed again, and a change is a miss with a
    warning; the fresh list is appended to ``carried`` so the recomputed value keeps it.
    """
    row = _row(name)
    if row is None:
        inform(f"Cache '{name}' not found")
        return _MISSING

    path = next((p for p in _candidates(name, file, row) if p.is_file()), None)
    if path is None:
        inform(f"Cache '{name}' not found")
        return _MISSING
    if path.suffix == _R_FORMAT:
        warnings.warn(
            f"Cache '{name}' was saved by R (.rds), so Python treats it as a miss. "
            "R's entry is left in place.",
            CacheWarning,
            stacklevel=3,
        )
        return _MISSING

    expire_at = db.parse_timestamp(row["expire_at"])
    if expire_at is not None and datetime.now().astimezone() > expire_at:
        inform(f"Cache '{name}' has expired")
        _remove(name, file)
        return _MISSING

    if row["hash"] and file_hash(path) != row["hash"]:
        warnings.warn(
            f"Hash mismatch for cache '{name}' - cache may be corrupted", CacheWarning, stacklevel=3
        )
        return _MISSING

    stored = _depends.recorded(name)
    if dependencies is not None:
        if stored is None or stored[0] != _depends.key(dependencies):
            changed = _depends.changed_between(stored[1] if stored else [], dependencies)
            inform(f"Cache '{name}' is out of date: {_depends.listing(changed)} changed")
            return _MISSING
    elif stored is not None:
        fresh, changed = _depends.recheck(stored[1])
        if carried is not None:
            carried.append(fresh)
        if changed:
            warnings.warn(
                f"Cache '{name}' is older than its dependencies: "
                f"{_depends.listing(changed)} changed since it was stored, so it is treated "
                "as a miss. Pass depends_on= to cache_remember() to recompute it without "
                "this warning.",
                CacheWarning,
                stacklevel=3,
            )
            return _MISSING

    try:
        if path.suffix == ".parquet":
            value = pd.read_parquet(path)
        else:
            with path.open("rb") as handle:
                value = pickle.load(handle)
    except Exception as error:
        warnings.warn(f"Failed to read cache '{name}': {error}", CacheWarning, stacklevel=3)
        return _MISSING

    with db.connect() as connection:
        connection.execute(
            "UPDATE cache SET last_read_at = ? WHERE name = ?", (db.timestamp(), name)
        )
    return value


def cache_get(name: str, file: str | Path | None = None, default: Any = None) -> Any:
    """A cached value, or ``default`` when it is missing, expired, or unreadable.

    An expired value is removed. A value saved by R (``.rds``) or one whose file hash no longer
    matches its record counts as missing. So does a value stored with ``depends_on`` when a
    file or catalog entry it depends on has changed since, with a ``CacheWarning``.

    Args:
        name: The cache name.
        file: Read the value from this file instead of the cache folder.
        default: The value to return when the cache has no usable value.

    Returns:
        The cached value, or ``default``.
    """
    value = _lookup(name, file)
    return default if value is _MISSING else value


@overload
def cache_remember(
    name: str,
    compute: Callable[[], T],
    *,
    file: str | Path | None = ...,
    expire_after: float | str | None = ...,
    refresh: bool | Callable[[], bool] = ...,
    depends_on: Any = ...,
) -> T: ...


@overload
def cache_remember(
    name: str,
    compute: None = ...,
    *,
    file: str | Path | None = ...,
    expire_after: float | str | None = ...,
    refresh: bool | Callable[[], bool] = ...,
    depends_on: Any = ...,
) -> Callable[[Callable[[], T]], Callable[[], T]]: ...


def cache_remember(
    name: str,
    compute: Callable[[], T] | None = None,
    *,
    file: str | Path | None = None,
    expire_after: float | str | None = None,
    refresh: bool | Callable[[], bool] = False,
    depends_on: Any = None,
) -> T | Callable[[Callable[[], T]], Callable[[], T]]:
    """Return the cached value, or compute it, cache it, and return it.

    Call it with a function that takes no arguments::

        model = fw.cache_remember("model", lambda: fit(data), expire_after="1 week")

    or use it as a decorator, which caches the function's result::

        @fw.cache_remember("survey_clean")
        def survey_clean():
            return fw.data_read("inputs.raw.survey").dropna()

    ``depends_on`` ties the value to what it is computed from, so it is recomputed when they
    change::

        fit = fw.cache_remember(
            "lpa_fit",
            lambda: fit_lpa(fw.data_read("inputs.final.cohort"), k=4),
            depends_on=["inputs.final.cohort", "functions/lpa.py", {"k": 4}],
        )

    Args:
        name: The cache name.
        compute: Computes the value on a miss.
        file: Store the value in this file instead of the cache folder.
        expire_after: Hours until the value expires, or a string like ``"30 minutes"`` or
            ``"2 days"``.
        refresh: Recompute even when cached. A function is called to decide.
        depends_on: What the value is computed from. Their hashes are part of the entry's
            key, so the value is recomputed when any of them changes. One item or a list:
            a catalog name (``"inputs.raw.survey"``, hashed as its file), a file or folder
            path from the project root (``"functions/fit.py"``, ``"functions"``), or any
            other value, such as a parameter (``{"cutoff": 0}``). A string must name a
            catalog entry, file, or folder; wrap a string value in a dict to hash it. An
            entry stored with dependencies and read without them (``cache_get()``, or a call
            that leaves ``depends_on`` out) is checked against its files and catalog
            entries, and a change is a miss with a ``CacheWarning``.
    """
    if compute is None:

        def decorator(function: Callable[[], T]) -> Callable[[], T]:
            @functools.wraps(function)
            def wrapper() -> T:
                return cache_remember(
                    name,
                    function,
                    file=file,
                    expire_after=expire_after,
                    refresh=refresh,
                    depends_on=depends_on,
                )

            return wrapper

        return decorator

    should_refresh = refresh() if callable(refresh) else refresh
    if not isinstance(should_refresh, bool):
        warnings.warn("refresh must return a single boolean value", CacheWarning, stacklevel=2)
        should_refresh = False
    # Hashed before computing, so the record describes the inputs the value came from
    dependencies = _depends.resolve(depends_on) if depends_on is not None else None
    carried: list[list[_depends.Dependency]] = []
    if should_refresh:
        inform(f"Cache '{name}' refresh requested")
        _remove(name, file)
    else:
        value = _lookup(name, file, dependencies, carried)
        if value is not _MISSING:
            return value  # type: ignore[no-any-return]
    if dependencies is None and carried:
        # A value stored with dependencies keeps them when it is recomputed without them
        dependencies = carried[0]

    computed = compute()
    try:
        _store(name, computed, file, expire_after, dependencies)
    except Exception as error:
        warnings.warn(f"Failed to cache value: {error}", CacheWarning, stacklevel=2)
    return computed


def cache_forget(name: str, file: str | Path | None = None) -> None:
    """Remove a cached value and its record, whichever language saved it.

    Args:
        name: The cache name.
        file: Remove this file instead of the files in the cache folder and the one in the
            cache record.
    """
    _remove(name, file)


def cache_flush() -> None:
    """Remove every cached value (from R and Python) and every cache record.

    Values stored with ``file=`` outside the cache folder are removed too, as in R, when
    the file has a cache extension (.parquet, .pickle, or .rds).
    """
    suffixes = (*_FORMATS, _R_FORMAT)
    folder = cache_dir()
    if folder.is_dir():
        for path in folder.iterdir():
            if path.is_file() and path.suffix in suffixes:
                path.unlink()
    with db.connect() as connection:
        custom = [
            row["file_path"]
            for row in connection.execute(
                "SELECT file_path FROM cache WHERE file_path IS NOT NULL AND file_path != ''"
            )
        ]
        if custom:
            root = project_root()
            for stored in custom:
                path = in_root(str(stored), root)
                if path.suffix.lower() in suffixes and path.is_file():
                    path.unlink()
        connection.execute("DELETE FROM cache")
    _depends.forget()


def cache_list() -> pd.DataFrame:
    """Every cache record, with its timestamps and status (``active`` or ``expired``)."""
    columns = ["name", "expire_at", "created_at", "updated_at", "last_read_at", "status"]
    with db.connect() as connection:
        rows = connection.execute(
            "SELECT name, expire_at, created_at, updated_at, last_read_at FROM cache ORDER BY name"
        ).fetchall()
    now = datetime.now().astimezone()
    records = []
    for row in rows:
        record = {key: db.parse_timestamp(row[key]) for key in columns[1:5]}
        expire_at = record["expire_at"]
        records.append(
            {
                "name": row["name"],
                **record,
                "status": "expired" if expire_at is not None and now > expire_at else "active",
            }
        )
    return pd.DataFrame(records, columns=columns)
