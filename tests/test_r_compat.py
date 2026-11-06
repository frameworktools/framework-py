"""Python reads the R-built fixture project the same way R does.

The fixture and the expected values come from scripts/make_r_fixtures.R.
"""

from __future__ import annotations

import os
import re
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

import framework as fw
from framework import db
from framework.integrity import file_hash
from framework.settings import SettingsWarning
from tests.conftest import as_r_json, r_json


@pytest.fixture(autouse=True)
def fixture_environment(r_expected: dict[str, Any]) -> None:
    os.environ.update(r_expected["environment"])
    os.environ.pop("FW_FIXTURE_UNSET", None)
    os.environ.pop("FW_FIXTURE_DOTENV", None)


def test_settings_match_r_in_default_environment(
    r_project: Path, r_expected: dict[str, Any]
) -> None:
    assert as_r_json(fw.settings_read()) == r_json(r_expected["settings_default"])


def test_settings_match_r_in_another_environment(
    r_project: Path, r_expected: dict[str, Any]
) -> None:
    os.environ["FW_CONFIG_ACTIVE"] = "production"
    assert as_r_json(fw.settings_read()) == r_json(r_expected["settings_production"])


def test_key_order_matches_r(r_project: Path, r_expected: dict[str, Any]) -> None:
    assert list(fw.settings_read()) == list(r_expected["settings_default"])


def test_dotenv_values_resolve_without_staying_in_the_environment(r_project: Path) -> None:
    # Audit 2026-11-05: settings_read() left .env values in os.environ, so one project's
    # token carried into the next. R and Python now share the scoped rule.
    assert fw.settings("fixture.dotenv_value") == "from dotenv"
    assert "FW_FIXTURE_DOTENV" not in os.environ
    fw.scaffold()
    assert os.environ["FW_FIXTURE_DOTENV"] == "from dotenv"


def test_data_info_matches_r(r_project: Path, r_expected: dict[str, Any]) -> None:
    for name, expected in r_expected["data_info"].items():
        info = fw.data_info(name)
        actual = {
            "path": info.path.relative_to(r_project.resolve()).as_posix(),
            "type": info.type,
            "delimiter": info.delimiter,
            "locked": info.locked,
            "private": info.private,
            **({"description": info.description} if info.description else {}),
            **info.extra,
        }
        assert actual == expected, name


def test_data_list_names_every_r_entry(r_project: Path, r_expected: dict[str, Any]) -> None:
    assert list(fw.data_list()["name"]) == list(r_expected["data_info"])


def test_file_hashes_match_r(r_project: Path, r_expected: dict[str, Any]) -> None:
    for path, expected in r_expected["hashes"].items():
        assert file_hash(r_project / path) == expected


def test_python_reads_rows_r_wrote(r_project: Path, r_expected: dict[str, Any]) -> None:
    record = db.get_data_record("inputs.raw.survey")
    expected = r_expected["data_row"]
    assert record is not None
    assert record.hash == expected["hash"]
    assert record.locked is True
    assert record.created_at is not None
    assert expected["created_at_type"] == "real"
    assert db.get_meta("fixture") == "written by R"


def test_python_writes_rows_the_way_r_does(r_project: Path, r_expected: dict[str, Any]) -> None:
    db.set_data_record(
        "inputs.raw.visits",
        path="inputs/raw/visits.tsv",
        type="csv",
        delimiter="tab",
        locked=True,
        hash="0" * 64,
    )
    with closing(sqlite3.connect(r_project / "framework.db")) as connection:
        types = connection.execute(
            "SELECT typeof(created_at), typeof(locked) FROM data WHERE name = 'inputs.raw.visits'"
        ).fetchone()
    expected = r_expected["data_row"]
    assert types == (expected["created_at_type"], expected["locked_type"])


def test_python_schema_matches_r(tmp_path: Path, r_expected: dict[str, Any]) -> None:
    db.ensure_db(tmp_path)
    with closing(sqlite3.connect(tmp_path / "framework.db")) as connection:
        columns = {
            table: [row[1:3] for row in connection.execute(f"PRAGMA table_info({table})")]
            for table in r_expected["tables"]
        }
    with closing(sqlite3.connect(":memory:")) as reference:
        for sql in r_expected["tables"].values():
            reference.execute(sql)
        expected = {
            table: [row[1:3] for row in reference.execute(f"PRAGMA table_info({table})")]
            for table in r_expected["tables"]
        }
    assert columns == expected


def test_missing_environment_warns(r_project: Path) -> None:
    os.environ["FW_CONFIG_ACTIVE"] = "nowhere"
    with pytest.warns(SettingsWarning, match="Environment 'nowhere' not found"):
        fw.settings_read()


# Data, cache, and outputs that R saved -------------------------------------


def test_python_verifies_a_file_r_saved(r_project: Path, r_expected: dict[str, Any]) -> None:
    saved = r_expected["saved_row"]
    assert file_hash(r_project / saved["path"]) == saved["hash"]
    frame = fw.data_read(saved["path"])
    assert list(frame.columns) == ["id", "score", "group"]
    assert frame["score"].isna().tolist() == [False, True, False]
    assert frame["group"].isna().tolist() == [False, False, True]


def test_an_r_cache_entry_is_a_miss_and_is_kept(
    r_project: Path, r_expected: dict[str, Any]
) -> None:
    assert r_expected["cache_row"]["file_path"] == "outputs/cache/r_value.rds"
    with pytest.warns(fw.CacheWarning, match="saved by R"):
        assert fw.cache_get("r_value", default="miss") == "miss"
    assert (r_project / "outputs/cache/r_value.rds").is_file()
    assert list(fw.cache_list()["name"]) == ["r_value"]


def test_python_lists_results_r_saved(r_project: Path, r_expected: dict[str, Any]) -> None:
    listed = fw.result_list().to_dict("records")
    expected = r_expected["result_list"]
    timestamps = ("created_at", "updated_at")
    assert [{k: v for k, v in row.items() if k not in timestamps} for row in listed] == [
        {k: v for k, v in row.items() if k not in timestamps} for row in expected
    ]
    for row in listed:
        for key in timestamps:
            assert re.fullmatch(r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d", row[key])


def test_python_records_results_the_way_r_does(r_project: Path, r_expected: dict[str, Any]) -> None:
    fw.save_table(pd.DataFrame({"x": [1]}), "py_summary")
    with closing(sqlite3.connect(r_project / "framework.db")) as connection:
        row = connection.execute(
            "SELECT type, public, blind, comment, typeof(created_at) FROM results "
            "WHERE name = 'outputs/tables/py_summary.csv'"
        ).fetchone()
    expected = r_expected["result_row"]
    assert row == (
        expected["type"],
        expected["public"],
        expected["blind"],
        expected["comment"],
        expected["created_at_type"],
    )


@pytest.mark.parametrize("file", ["test.dta", "test.sav", "test.xpt"])
def test_other_formats_read_as_in_r(r_project: Path, r_expected: dict[str, Any], file: str) -> None:
    frame = fw.data_read(f"inputs/reference/{file}")
    records = [
        {key: (None if pd.isna(value) else value) for key, value in row.items()}
        for row in frame.to_dict("records")
    ]
    assert records == r_expected["formats"][file]
