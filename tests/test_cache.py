from __future__ import annotations

import os
import sqlite3
from contextlib import closing
from pathlib import Path

import pandas as pd
import pytest

import framework as fw
from framework import db
from framework.cache import cache_dir, normalize_expire_after


@pytest.fixture
def cache_project(project: Path) -> Path:
    (project / "settings.yml").write_text(
        "default:\n  directories:\n    cache: outputs/private/cache\n", encoding="utf-8"
    )
    return project


@pytest.mark.parametrize(
    ("value", "hours"),
    [(None, None), ("", None), (2, 2.0), ("2 hours", 2.0), ("1.5d", 36.0), ("1 week", 168.0)],
)
def test_normalize_expire_after(value: float | str | None, hours: float | None) -> None:
    assert normalize_expire_after(value) == hours


def test_bad_expire_after() -> None:
    with pytest.raises(fw.FrameworkError, match="Could not parse"):
        normalize_expire_after("soon")


def test_cache_folder(cache_project: Path) -> None:
    assert cache_dir() == cache_project / "outputs/private/cache"
    os.environ["FW_CACHE_DIR"] = str(cache_project / "elsewhere")
    assert cache_dir() == cache_project / "elsewhere"


def test_values_round_trip(cache_project: Path) -> None:
    frame = pd.DataFrame({"a": [1, 2]})
    assert fw.cache("frame", frame) is frame
    assert fw.cache("value", {"k": [1, 2]}) == {"k": [1, 2]}
    folder = cache_project / "outputs/private/cache"
    assert sorted(p.name for p in folder.iterdir()) == ["frame.parquet", "value.pickle"]
    pd.testing.assert_frame_equal(fw.cache_get("frame"), frame)
    assert fw.cache_get("value") == {"k": [1, 2]}
    assert fw.cache_get("missing", default=0) == 0


def test_record_has_file_path_and_epoch_times(cache_project: Path) -> None:
    fw.cache("value", 1, expire_after="1 day")
    with closing(sqlite3.connect(cache_project / "framework.db")) as connection:
        row = connection.execute(
            "SELECT file_path, typeof(expire_at), typeof(created_at), expire_at - created_at "
            "FROM cache"
        ).fetchone()
    assert row[:3] == ("outputs/private/cache/value.pickle", "real", "real")
    assert row[3] == pytest.approx(86400, abs=1)


def test_expired_values_are_removed(cache_project: Path) -> None:
    fw.cache("old", 1)
    with db.connect() as connection:
        connection.execute("UPDATE cache SET expire_at = ?", (db.timestamp() - 10,))
    assert fw.cache_list()["status"].tolist() == ["expired"]
    assert fw.cache_get("old") is None
    assert fw.cache_list().empty
    assert not (cache_project / "outputs/private/cache/old.pickle").exists()


def test_default_expiry_from_settings(cache_project: Path) -> None:
    (cache_project / "settings.yml").write_text(
        "default:\n  options: {data: {cache_default_expire: 2 hours}}\n", encoding="utf-8"
    )
    fw.cache("value", 1)
    expire_at = fw.cache_list()["expire_at"][0]
    assert expire_at is not None


def test_tampered_file_is_a_miss(cache_project: Path) -> None:
    fw.cache("value", [1, 2])
    path = cache_project / "outputs/private/cache/value.pickle"
    path.write_bytes(path.read_bytes() + b"\0")
    with pytest.warns(fw.CacheWarning, match="Hash mismatch"):
        assert fw.cache_get("value") is None


def test_remember_computes_once(cache_project: Path) -> None:
    calls = []

    def compute() -> int:
        calls.append(1)
        return 42

    assert fw.cache_remember("answer", compute) == 42
    assert fw.cache_remember("answer", compute) == 42
    assert len(calls) == 1
    assert fw.cache_remember("answer", compute, refresh=True) == 42
    assert fw.cache_remember("answer", compute, refresh=lambda: True) == 42
    assert len(calls) == 3


def test_remember_as_decorator(cache_project: Path) -> None:
    calls = []

    @fw.cache_remember("decorated", expire_after=1)
    def compute() -> str:
        calls.append(1)
        return "value"

    assert compute() == "value"
    assert compute() == "value"
    assert len(calls) == 1
    assert compute.__name__ == "compute"


def test_custom_file(cache_project: Path) -> None:
    target = cache_project / "custom" / "thing.pickle"
    fw.cache("thing", 5, file=target)
    assert target.is_file()
    assert fw.cache_get("thing", file=target) == 5
    fw.cache_forget("thing", file=target)
    assert not target.exists()


def test_forget_and_flush(cache_project: Path) -> None:
    fw.cache("a", 1)
    fw.cache("b", pd.DataFrame({"x": [1]}))
    folder = cache_project / "outputs/private/cache"
    (folder / "r_only.rds").write_bytes(b"RDS")
    (folder / "notes.txt").write_text("keep", encoding="utf-8")

    fw.cache_forget("a")
    assert fw.cache_list()["name"].tolist() == ["b"]
    fw.cache_flush()
    assert fw.cache_list().empty
    assert sorted(p.name for p in folder.iterdir()) == ["notes.txt"]
