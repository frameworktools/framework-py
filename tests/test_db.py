from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

import pytest

from framework import db


def test_ensure_db_creates_every_table(project: Path) -> None:
    path = db.ensure_db()
    assert path == project / "framework.db"
    with closing(sqlite3.connect(path)) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master")}
    assert {"results", "data", "cache", "connections", "meta"} <= tables


def test_ensure_db_adds_missing_tables_and_keeps_rows(project: Path) -> None:
    with closing(sqlite3.connect(project / "framework.db")) as connection:
        connection.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
        connection.execute("INSERT INTO meta VALUES ('kept', 'yes')")
        connection.commit()
    db.ensure_db()
    assert db.get_meta("kept") == "yes"
    with closing(sqlite3.connect(project / "framework.db")) as connection:
        assert connection.execute("SELECT count(*) FROM data").fetchone() == (0,)


def test_data_record_round_trip(project: Path) -> None:
    assert db.get_data_record("x") is None
    db.set_data_record("x", path="a.csv", type="csv", delimiter="comma", locked=True, hash="h1")
    first = db.get_data_record("x")
    assert first is not None
    assert (first.path, first.type, first.locked, first.hash) == ("a.csv", "csv", True, "h1")
    assert first.created_at is not None

    db.set_data_record("x", path="b.csv", type="csv", delimiter=None, locked=False, hash="h2")
    second = db.get_data_record("x")
    assert second is not None
    assert (second.path, second.locked, second.hash) == ("b.csv", False, "h2")
    assert second.created_at == first.created_at


def test_update_data_hash(project: Path) -> None:
    db.update_data_hash("new", "h1")
    db.update_data_hash("new", "h2")
    record = db.get_data_record("new")
    assert record is not None and record.hash == "h2" and record.path is None


def test_meta_round_trip(project: Path) -> None:
    db.set_meta("k", "v1")
    db.set_meta("k", "v2")
    assert db.get_meta("k") == "v2"
    assert db.get_meta("missing") is None


@pytest.mark.parametrize(
    "value",
    [
        1793375103.5,
        1793375103,
        "1793375103.5",
        "2026-10-30T15:45:03+0000",
        "2026-10-30 15:45:03+0000",
        "2026-10-30T15:45:03+00:00",
    ],
)
def test_parse_timestamp_formats(value: object) -> None:
    parsed = db.parse_timestamp(value)
    assert parsed is not None
    assert parsed.astimezone(UTC).replace(microsecond=0) == datetime(
        2026, 10, 30, 15, 45, 3, tzinfo=UTC
    )


def test_parse_timestamp_local_string_and_empty() -> None:
    parsed = db.parse_timestamp("2026-10-30 11:05:03")
    assert parsed is not None and parsed.tzinfo is not None
    assert db.parse_timestamp(None) is None
    assert db.parse_timestamp("") is None
    assert db.parse_timestamp("not a date") is None
