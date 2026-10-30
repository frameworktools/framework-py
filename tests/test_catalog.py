from __future__ import annotations

import re
from pathlib import Path

import pytest

import framework as fw
from framework.catalog import type_from_extension


def write_catalog(project: Path, text: str) -> None:
    (project / "settings.yml").write_text("default:\n" + text, encoding="utf-8")


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("a.CSV", ("csv", "comma")),
        ("a.tsv", ("csv", "tab")),
        ("a.parquet", ("parquet", None)),
        ("a.xlsx", ("excel", None)),
        ("a.sav", ("spss", None)),
        ("a.por", ("spss_por", None)),
        ("a.sas7bdat", ("sas", None)),
        ("a.xpt", ("sas_xpt", None)),
        ("a.dta", ("stata", None)),
        ("a.rds", ("rds", None)),
        ("a.txt", ("csv", None)),
    ],
)
def test_type_from_extension(name: str, expected: tuple[str, str | None]) -> None:
    assert type_from_extension(name) == expected


def test_catalog_paths_are_relative_to_the_root(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_catalog(project, "  data:\n    raw:\n      survey: {path: inputs/raw/survey.csv}\n")
    (project / "notebooks").mkdir()
    monkeypatch.chdir(project / "notebooks")
    info = fw.data_info("raw.survey")
    assert info.path == (project / "inputs/raw/survey.csv").resolve()
    assert info.name == "raw.survey"


def test_file_paths_are_relative_to_the_current_folder(project: Path) -> None:
    info = fw.data_info("inputs/private/ids.csv")
    assert info.path == (project / "inputs/private/ids.csv").resolve()
    assert info.private is True
    assert info.name is None
    assert fw.data_info(str(info.path)).path == info.path


def test_unknown_name_lists_the_catalog(project: Path) -> None:
    write_catalog(project, "  data:\n    a: x.csv\n    b: {path: y.csv}\n")
    with pytest.raises(fw.DataNotFoundError, match=re.escape("Available data paths:\n  a\n  b")):
        fw.data_info("c")


def test_long_catalogs_are_shortened_in_errors(project: Path) -> None:
    entries = "".join(f"    d{i}: d{i}.csv\n" for i in range(25))
    write_catalog(project, "  data:\n" + entries)
    with pytest.raises(fw.DataNotFoundError, match=r"\.\.\. and 5 more"):
        fw.data_info("missing")


def test_entry_without_a_path(project: Path) -> None:
    write_catalog(project, "  data:\n    a: {b: {}}\n")
    with pytest.raises(fw.DataNotFoundError, match="has no path"):
        fw.data_info("a.b")


def test_data_list(project: Path) -> None:
    write_catalog(
        project,
        "  data:\n"
        "    raw:\n"
        "      survey: {path: s.csv, type: csv, locked: true, description: Survey}\n"
        "      visits: v.tsv\n",
    )
    frame = fw.data_list()
    assert list(frame["name"]) == ["raw.survey", "raw.visits"]
    assert list(frame["path"]) == ["s.csv", "v.tsv"]
    assert list(frame["locked"]) == [True, False]
    assert frame.loc[0, "description"] == "Survey"
    assert frame["type"].isna().tolist() == [False, True]


def test_empty_data_list(project: Path) -> None:
    frame = fw.data_list()
    assert frame.empty
    assert list(frame.columns) == ["name", "path", "type", "locked", "description"]
