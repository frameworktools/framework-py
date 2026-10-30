from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

import framework as fw
from framework import _yaml, db
from framework.integrity import file_hash

FRAME = pd.DataFrame({"id": [1, 2], "score": [3.5, None], "group": ["a", "b"]})


@pytest.fixture
def data_project(project: Path) -> Path:
    (project / "settings.yml").write_text(
        "default:\n"
        "  directories:\n"
        "    inputs_raw: inputs/raw\n"
        "    inputs_intermediate: inputs/intermediate\n"
        "  data: settings/data.yml\n",
        encoding="utf-8",
    )
    (project / "settings").mkdir()
    (project / "settings/data.yml").write_text("data: {}\n", encoding="utf-8")
    (project / "inputs/raw").mkdir(parents=True)
    (project / "inputs/intermediate").mkdir(parents=True)
    return project


def test_save_by_name_writes_parquet_and_catalogs_it(data_project: Path) -> None:
    returned = fw.data_save(FRAME, "inputs.intermediate.clean_v2")
    assert returned is FRAME
    target = data_project / "inputs/intermediate/clean_v2.parquet"
    assert target.is_file()

    catalog = _yaml.read(data_project / "settings/data.yml")
    assert catalog == {
        "data": {
            "inputs": {
                "intermediate": {
                    "clean_v2": {
                        "path": "inputs/intermediate/clean_v2.parquet",
                        "type": "parquet",
                        "locked": True,
                    }
                }
            }
        }
    }
    record = db.get_data_record("inputs.intermediate.clean_v2")
    assert record is not None and record.hash == file_hash(target)
    pd.testing.assert_frame_equal(fw.data_read("inputs.intermediate.clean_v2"), FRAME)


def test_save_by_path_writes_csv_without_cataloging(data_project: Path) -> None:
    fw.data_save(FRAME, "inputs/raw/raw.csv", delimiter="semicolon")
    assert (
        (data_project / "inputs/raw/raw.csv")
        .read_text(encoding="utf-8")
        .startswith("id;score;group")
    )
    assert _yaml.read(data_project / "settings/data.yml") == {"data": {}}
    record = db.get_data_record("inputs/raw/raw.csv")
    assert record is not None and record.delimiter == "semicolon"


def test_save_adds_a_missing_extension(data_project: Path) -> None:
    fw.data_save(FRAME, "inputs/raw/raw", type="parquet")
    assert (data_project / "inputs/raw/raw.parquet").is_file()


@pytest.mark.parametrize(
    ("path", "kwargs", "message"),
    [
        ("inputs.intermediate.x", {"type": "rds"}, "R-only"),
        ("inputs/raw/x.rds", {}, "R-only"),
        ("inputs.nowhere.x", {}, "Unknown directory 'inputs.nowhere'"),
        ("inputs.raw", {}, "at least three parts"),
        ("plain", {}, "has no directory"),
        ("outputs/new/x.csv", {}, "does not exist. Use force=True"),
        ("inputs/raw/x.txt", {}, "Cannot auto-detect"),
    ],
)
def test_save_errors(data_project: Path, path: str, kwargs: dict[str, str], message: str) -> None:
    with pytest.raises(fw.FrameworkError, match=message):
        fw.data_save(FRAME, path, **kwargs)  # type: ignore[arg-type]


def test_save_with_force_creates_the_folder(data_project: Path) -> None:
    fw.data_save(FRAME, "outputs/new/x.csv", force=True)
    assert (data_project / "outputs/new/x.csv").is_file()


def test_locked_file_that_changed_is_an_error(data_project: Path) -> None:
    fw.data_save(FRAME, "inputs.raw.survey")
    target = data_project / "inputs/raw/survey.parquet"
    FRAME.head(1).to_parquet(target)
    with pytest.raises(
        fw.IntegrityError, match=r"Hash mismatch for locked data 'inputs\.raw\.survey'"
    ):
        fw.data_read("inputs.raw.survey")


def test_unlocked_file_that_changed_warns_and_updates(data_project: Path) -> None:
    fw.data_save(FRAME, "inputs.raw.survey", locked=False)
    target = data_project / "inputs/raw/survey.parquet"
    FRAME.head(1).to_parquet(target)
    with pytest.warns(fw.DataChangedWarning, match="File hash changed"):
        assert len(fw.data_read("inputs.raw.survey")) == 1
    record = db.get_data_record("inputs.raw.survey")
    assert record is not None and record.hash == file_hash(target)
    assert len(fw.data_read("inputs.raw.survey")) == 1


def test_first_read_records_the_hash(data_project: Path) -> None:
    (data_project / "inputs/raw/v.tsv").write_text("a\tb\n1\t2\n", encoding="utf-8")
    (data_project / "settings/data.yml").write_text(
        "data:\n  visits: inputs/raw/v.tsv\n", encoding="utf-8"
    )
    frame = fw.data_read("visits")
    assert list(frame.columns) == ["a", "b"]
    record = db.get_data_record("visits")
    assert record is not None and record.path == "inputs/raw/v.tsv" and not record.locked


def test_description_is_shown(data_project: Path, caplog: pytest.LogCaptureFixture) -> None:
    (data_project / "inputs/raw/s.csv").write_text("a\n1\n", encoding="utf-8")
    (data_project / "settings/data.yml").write_text(
        "data:\n  s: {path: inputs/raw/s.csv, description: Survey responses}\n", encoding="utf-8"
    )
    with caplog.at_level("INFO", logger="framework"):
        fw.data_read("s")
    assert "Note: s: Survey responses" in caplog.text


def test_read_errors(data_project: Path) -> None:
    (data_project / "inputs/raw/model.rds").write_bytes(b"RDS")
    with pytest.raises(fw.UnsupportedFormatError, match="only R can read"):
        fw.data_read("inputs/raw/model.rds")
    (data_project / "inputs/raw/notes.docx").write_bytes(b"")
    with pytest.raises(fw.UnsupportedFormatError, match="docx"):
        fw.data_read("inputs/raw/notes.docx")
    (data_project / "settings/data.yml").write_text(
        "data:\n  gone: inputs/raw/gone.csv\n", encoding="utf-8"
    )
    with pytest.raises(fw.DataNotFoundError, match="File not found"):
        fw.data_read("gone")


def test_delimiter_override(data_project: Path) -> None:
    path = data_project / "inputs/raw/x.csv"
    path.write_text("a;b\n1;2\n", encoding="utf-8")
    assert list(fw.data_read(str(path), delim="semicolon").columns) == ["a", "b"]


def test_excel_round_trip(data_project: Path) -> None:
    path = data_project / "inputs/raw/book.xlsx"
    FRAME.to_excel(path, index=False)
    assert list(fw.data_read(str(path))["group"]) == ["a", "b"]


def test_add_names_files_by_folder(data_project: Path) -> None:
    path = data_project / "inputs/raw/survey.csv"
    FRAME.to_csv(path, index=False)
    entry = fw.data_add(path)
    assert entry == {
        "path": "inputs/raw/survey.csv",
        "type": "csv",
        "delimiter": "comma",
        "locked": True,
    }
    assert fw.data_info("inputs.raw.survey").path == path.resolve()


def test_add_into_inline_catalog_and_flat_settings(project: Path) -> None:
    (project / "settings.yml").write_text("project_name: Flat\n", encoding="utf-8")
    (project / "x.parquet").write_bytes(b"")
    FRAME.to_parquet(project / "x.parquet")
    fw.data_add("x.parquet", name="group.x", locked=False)
    assert _yaml.read(project / "settings.yml") == {
        "project_name": "Flat",
        "data": {"group": {"x": {"path": "x.parquet", "type": "parquet"}}},
    }


def test_add_missing_file(data_project: Path) -> None:
    with pytest.raises(fw.DataNotFoundError):
        fw.data_add("inputs/raw/none.csv")
