"""The project database, ``framework.db``.

The schema comes from the R package's ``init.sql``, so R and Python read and
write the same tables. Values follow what R stores:

- Timestamps in the ``data``, ``cache``, and ``meta`` tables are seconds since
  the Unix epoch, stored as REAL. RSQLite writes R's date-times that way.
- Booleans are stored as 0 and 1.

Readers accept both epoch seconds and date strings, because older rows and
the ``results`` table hold strings.
"""

from __future__ import annotations

import re
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path

from framework.project import find_settings_file

DB_NAME = "framework.db"


def _schema_statements() -> dict[str, str]:
    """CREATE TABLE statements from init.sql, keyed by table name."""
    sql = resources.files("framework").joinpath("_assets/init.sql").read_text(encoding="utf-8")
    statements: dict[str, str] = {}
    for statement in sql.split(";"):
        statement = statement.strip()
        match = re.match(r"CREATE TABLE\s+(\w+)", statement, re.IGNORECASE)
        if match:
            statements[match.group(1)] = re.sub(
                r"^CREATE TABLE\s+", "CREATE TABLE IF NOT EXISTS ", statement, flags=re.IGNORECASE
            )
    return statements


def db_path(root: str | Path | None = None) -> Path:
    """Where framework.db is: the project root, or the current folder outside a project."""
    if root is not None:
        return Path(root) / DB_NAME
    found = find_settings_file()
    return (found.parent if found else Path.cwd()) / DB_NAME


def ensure_db(root: str | Path | None = None) -> Path:
    """Create framework.db, or add any tables it is missing. Returns its path."""
    path = db_path(root)
    connection = sqlite3.connect(path)
    try:
        existing = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        for table, statement in _schema_statements().items():
            if table not in existing:
                connection.execute(statement)
        connection.commit()
    finally:
        connection.close()
    return path


@contextmanager
def connect(root: str | Path | None = None) -> Iterator[sqlite3.Connection]:
    """A connection to framework.db that commits on success and always closes."""
    path = ensure_db(root)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def timestamp() -> float:
    """The current time as R stores it: seconds since the Unix epoch."""
    return time.time()


def parse_timestamp(value: object) -> datetime | None:
    """A stored timestamp as an aware datetime in local time, or None.

    Accepts epoch seconds and the date strings R and Python have written.
    """
    if value is None or value == "":
        return None
    if isinstance(value, int | float):
        return datetime.fromtimestamp(value, tz=UTC).astimezone()
    text = str(value)
    try:
        return datetime.fromtimestamp(float(text), tz=UTC).astimezone()
    except ValueError:
        pass
    for pattern in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M:%S%z"):
        try:
            return datetime.strptime(text, pattern).astimezone()
        except ValueError:
            continue
    try:
        # A string without an offset is local time, as R writes it.
        return datetime.fromisoformat(text).astimezone()
    except ValueError:
        return None


# The data table -------------------------------------------------------------


@dataclass(frozen=True)
class DataRecord:
    """A row of the ``data`` table: what Framework knows about one data file."""

    name: str
    path: str | None
    type: str | None
    delimiter: str | None
    locked: bool
    hash: str | None
    last_read_at: datetime | None
    created_at: datetime | None
    updated_at: datetime | None


def get_data_record(name: str, root: str | Path | None = None) -> DataRecord | None:
    with connect(root) as connection:
        row = connection.execute(
            "SELECT name, path, type, delimiter, locked, hash, last_read_at, created_at, "
            "updated_at FROM data WHERE name = ?",
            (name,),
        ).fetchone()
    if row is None:
        return None
    return DataRecord(
        name=row["name"],
        path=row["path"],
        type=row["type"],
        delimiter=row["delimiter"],
        locked=bool(row["locked"]),
        hash=row["hash"],
        last_read_at=parse_timestamp(row["last_read_at"]),
        created_at=parse_timestamp(row["created_at"]),
        updated_at=parse_timestamp(row["updated_at"]),
    )


def set_data_record(
    name: str,
    *,
    path: str | None,
    type: str | None,
    delimiter: str | None,
    locked: bool,
    hash: str | None,
    root: str | Path | None = None,
) -> None:
    """Insert or update a ``data`` row, as R's ``.set_data()`` does."""
    now = timestamp()
    with connect(root) as connection:
        exists = connection.execute("SELECT 1 FROM data WHERE name = ?", (name,)).fetchone()
        if exists:
            connection.execute(
                "UPDATE data SET path = ?, type = ?, delimiter = ?, locked = ?, hash = ?, "
                "last_read_at = ?, updated_at = ? WHERE name = ?",
                (path, type, delimiter, int(locked), hash, now, now, name),
            )
        else:
            connection.execute(
                "INSERT INTO data (name, path, type, delimiter, locked, hash, last_read_at, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (name, path, type, delimiter, int(locked), hash, now, now, now),
            )


def update_data_hash(name: str, hash: str, root: str | Path | None = None) -> None:
    """Record a new hash for a data file, as R's ``.update_data_with_hash()`` does."""
    now = timestamp()
    with connect(root) as connection:
        exists = connection.execute("SELECT 1 FROM data WHERE name = ?", (name,)).fetchone()
        if exists:
            connection.execute(
                "UPDATE data SET hash = ?, last_read_at = ?, updated_at = ? WHERE name = ?",
                (hash, now, now, name),
            )
        else:
            connection.execute(
                "INSERT INTO data (name, hash, last_read_at, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (name, hash, now, now, now),
            )


# The meta table -------------------------------------------------------------


def get_meta(key: str, root: str | Path | None = None) -> str | None:
    with connect(root) as connection:
        row = connection.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return None if row is None else row["value"]


def set_meta(key: str, value: str, root: str | Path | None = None) -> None:
    now = timestamp()
    with connect(root) as connection:
        exists = connection.execute("SELECT 1 FROM meta WHERE key = ?", (key,)).fetchone()
        if exists:
            connection.execute(
                "UPDATE meta SET value = ?, updated_at = ? WHERE key = ?", (value, now, key)
            )
        else:
            connection.execute(
                "INSERT INTO meta (key, value, created_at, updated_at) VALUES (?, ?, ?, ?)",
                (key, value, now, now),
            )
