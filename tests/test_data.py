from __future__ import annotations

from pathlib import Path
from typing import Any

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


def test_save_by_absolute_path_records_the_path_from_the_root(data_project: Path) -> None:
    fw.data_save(FRAME, str(data_project / "inputs/raw/abs.csv"))
    record = db.get_data_record("inputs/raw/abs.csv")
    assert record is not None and record.path == "inputs/raw/abs.csv"
    assert db.get_data_record(str(data_project / "inputs/raw/abs.csv")) is None


def test_save_and_verify_share_one_record_for_spellings_of_a_path(data_project: Path) -> None:
    # Shared with R: "./x.csv", "x.csv", and "a/../x.csv" inside the project are one row,
    # recorded in the clean project-relative form
    for spelling in ("./inputs/raw/same.csv", "inputs/raw/../raw/same.csv", "inputs//raw/same.csv"):
        fw.data_save(FRAME, spelling)
    with db.connect() as connection:
        rows = connection.execute("SELECT name, path FROM data").fetchall()
    assert [tuple(row) for row in rows] == [("inputs/raw/same.csv", "inputs/raw/same.csv")]
    assert fw.data_verify("./inputs/raw/same.csv")
    assert fw.data_verify("inputs/raw/same.csv")


def test_save_adds_a_missing_extension(data_project: Path) -> None:
    fw.data_save(FRAME, "inputs/raw/raw", type="parquet")
    assert (data_project / "inputs/raw/raw.parquet").is_file()


@pytest.mark.parametrize(
    ("path", "kwargs", "message"),
    [
        ("inputs.intermediate.x", {"type": "rds"}, "R-only"),
        ("inputs/raw/x.rds", {}, "R-only"),
        ("inputs.nowhere.x", {}, r"Unknown directory in 'inputs\.nowhere\.x'"),
        ("inputs.raw", {}, r"Unknown directory in 'inputs\.raw'"),
        ("plain", {}, "has no directory"),
        ("plain.csv", {}, "'plain' has no directory"),
        ("inputs.raw.x.csv", {"type": "parquet"}, r"ends in \.csv, but type is 'parquet'"),
        ("outputs/new/x.csv", {}, "does not exist. Use force=True"),
        ("inputs/raw/x.txt", {}, "Cannot auto-detect"),
    ],
)
def test_save_errors(data_project: Path, path: str, kwargs: dict[str, str], message: str) -> None:
    with pytest.raises(fw.FrameworkError, match=message):
        fw.data_save(FRAME, path, **kwargs)  # type: ignore[arg-type]


def test_save_by_name_with_a_one_word_folder_key(project: Path) -> None:
    # A course keeps its data in directories: data
    (project / "settings.yml").write_text(
        "default:\n  directories:\n    data: data\n", encoding="utf-8"
    )
    (project / "data").mkdir()
    fw.data_save(FRAME, "data.week1_visits", type="csv")
    assert (project / "data/week1_visits.csv").is_file()
    pd.testing.assert_frame_equal(fw.data_read("data.week1_visits"), FRAME)


def test_dot_name_with_an_extension_is_a_catalog_name(
    data_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    notebooks = data_project / "notebooks"
    notebooks.mkdir()
    monkeypatch.chdir(notebooks)
    fw.data_save(FRAME, "inputs.intermediate.x.csv")
    assert (data_project / "inputs/intermediate/x.csv").is_file()
    assert not list(notebooks.iterdir())
    record = db.get_data_record("inputs.intermediate.x")
    assert record is not None and record.type == "csv"
    pd.testing.assert_frame_equal(fw.data_read("inputs.intermediate.x"), FRAME)


def test_relative_paths_start_at_the_project_root(
    data_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    notebooks = data_project / "notebooks"
    notebooks.mkdir()
    monkeypatch.chdir(notebooks)
    fw.data_save(FRAME, "inputs/raw/by_path.csv")
    assert (data_project / "inputs/raw/by_path.csv").is_file()
    assert not list(notebooks.iterdir())
    pd.testing.assert_frame_equal(fw.data_read("inputs/raw/by_path.csv"), FRAME)
    entry = fw.data_add("inputs/raw/by_path.csv", name="by_path")
    assert entry["path"] == "inputs/raw/by_path.csv"


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


def test_add_names_files_in_private_and_public_inputs(project: Path) -> None:
    (project / "settings.yml").write_text(
        "default:\n  directories:\n"
        "    inputs: inputs\n"
        "    inputs_private_raw: inputs/private/raw\n"
        "    inputs_public_final: inputs/public/final\n",
        encoding="utf-8",
    )
    for folder in ("inputs/private/raw", "inputs/public/final"):
        (project / folder).mkdir(parents=True)
    FRAME.to_csv(project / "inputs/private/raw/participants.csv", index=False)
    FRAME.to_csv(project / "inputs/public/final/summary.csv", index=False)
    FRAME.to_csv(project / "inputs/loose.csv", index=False)
    fw.data_add(project / "inputs/private/raw/participants.csv")
    fw.data_add(project / "inputs/public/final/summary.csv")
    fw.data_add(project / "inputs/loose.csv")
    names = set(fw.data_list()["name"])
    assert {"inputs.private.raw.participants", "inputs.public.final.summary"} <= names
    assert "inputs.loose" in names


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


def test_save_by_name_uses_the_longest_directory_key(project: Path) -> None:
    (project / "settings.yml").write_text(
        "default:\n"
        "  directories:\n"
        "    inputs_private_raw: inputs/private/raw\n"
        "    outputs_private: outputs/private\n"
        "    outputs_private_tables: outputs/private/tables\n",
        encoding="utf-8",
    )
    fw.data_save(FRAME, "inputs.private.raw.patients", force=True)
    assert (project / "inputs/private/raw/patients.parquet").is_file()
    assert fw.data_read("inputs.private.raw.patients").shape == FRAME.shape
    fw.data_save(FRAME, "outputs.private.tables.summary", force=True)
    assert (project / "outputs/private/tables/summary.parquet").is_file()
    fw.data_save(FRAME, "outputs.private.results", force=True)
    assert (project / "outputs/private/results.parquet").is_file()


@pytest.mark.parametrize(("name", "expected"), [("a.por", "spss_por"), ("b.xpt", "sas_xpt")])
def test_add_records_the_reader_type(data_project: Path, name: str, expected: str) -> None:
    (data_project / "inputs/raw" / name).write_bytes(b"x")
    assert fw.data_add(data_project / "inputs/raw" / name)["type"] == expected


@pytest.mark.parametrize(
    ("name", "recorded", "reader"), [("a.por", "spss", "spss_por"), ("b.xpt", "sas", "sas_xpt")]
)
def test_generic_types_from_r_use_the_extension(
    data_project: Path, monkeypatch: pytest.MonkeyPatch, name: str, recorded: str, reader: str
) -> None:
    # R's data_add() records .por as spss and .xpt as sas.
    (data_project / "inputs/raw" / name).write_bytes(b"x")
    (data_project / "settings/data.yml").write_text(
        f"data:\n  old: {{path: inputs/raw/{name}, type: {recorded}}}\n", encoding="utf-8"
    )
    used = []

    def fake_reader(file_type: str, r_args: Any = None) -> Any:
        used.append(file_type)
        return lambda *args: pd.DataFrame({"a": [1]})

    monkeypatch.setattr("framework.data._reader", fake_reader)
    fw.data_read("old")
    assert used == [reader]


def test_add_updates_an_entry_written_with_dotted_keys(data_project: Path) -> None:
    (data_project / "settings/data.yml").write_text(
        "data:\n  inputs:\n    raw.survey: inputs/raw/old.csv\n", encoding="utf-8"
    )
    (data_project / "inputs/raw/survey.csv").write_text("a\n1\n", encoding="utf-8")
    fw.data_add(data_project / "inputs/raw/survey.csv", name="inputs.raw.survey")
    catalog = _yaml.read(data_project / "settings/data.yml")["data"]
    assert catalog == {
        "inputs": {
            "raw.survey": {
                "path": "inputs/raw/survey.csv",
                "type": "csv",
                "delimiter": "comma",
                "locked": True,
            }
        }
    }


def test_reads_a_sas_file_that_r_haven_wrote(project: Path) -> None:
    # Audit 2026-11-05: pandas.read_sas gave blank names and wrong values for this file
    pytest.importorskip("pyreadstat")
    fixture = Path(__file__).parent / "fixtures" / "haven.sas7bdat"
    frame = fw.data_read(str(fixture))
    assert list(frame.columns) == ["id", "species", "mass"]
    assert frame["species"].tolist() == ["Adelie", "Gentoo", "Chinstrap"]
    assert frame["mass"].tolist() == [3700.5, 5000.0, 3600.0]


def test_xls_without_xlrd_names_the_package(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import importlib.util

    real = importlib.util.find_spec
    monkeypatch.setattr(
        importlib.util, "find_spec", lambda name, *a: None if name == "xlrd" else real(name, *a)
    )
    (project / "old.xls").write_bytes(b"not really excel")
    with pytest.raises(fw.FrameworkError, match="pip install xlrd"):
        fw.data_read("old.xls")


def test_csv_missing_values_follow_readr(data_project: Path) -> None:
    path = data_project / "inputs/raw/states.csv"
    path.write_text("state,group,n\nMD,None,1\nVA,N/A,2\nDC,NA,3\nPA,,4\n", encoding="utf-8")
    frame = fw.data_read("inputs/raw/states.csv")
    assert frame["group"].tolist()[:2] == ["None", "N/A"]
    assert frame["group"].isna().tolist() == [False, False, True, True]
    assert len(frame.dropna(subset=["state"])) == 4


def test_csv_na_argument_replaces_the_missing_strings(data_project: Path) -> None:
    path = data_project / "inputs/raw/codes.csv"
    path.write_text("code\n.\nNA\n7\n", encoding="utf-8")
    frame = fw.data_read("inputs/raw/codes.csv", na=".")
    assert frame["code"].isna().tolist() == [True, False, False]
    assert fw.data_read("inputs/raw/codes.csv", na=[]).loc[0, "code"] == "."
    assert fw.data_read("inputs/raw/codes.csv", keep_default_na=True)["code"].isna().sum() == 1
    with pytest.raises(fw.FrameworkError, match="not both"):
        fw.data_read("inputs/raw/codes.csv", na=".", na_values=["."])


def test_csv_whitespace_is_trimmed_as_readr_does(data_project: Path) -> None:
    path = data_project / "inputs/raw/padded.tsv"
    path.write_text(
        ' id \t value\t label\n    1 \t          NA\t  hi \n   22\t   3.5 \t"  in  "\n',
        encoding="utf-8",
    )
    frame = fw.data_read("inputs/raw/padded.tsv")
    assert list(frame.columns) == ["id", "value", "label"]
    assert frame["id"].tolist() == [1, 22]
    assert frame["value"].dtype == "float64"
    assert frame["value"].isna().tolist() == [True, False]
    assert frame["label"].tolist() == ["hi", "in"]

    kept = fw.data_read("inputs/raw/padded.tsv", trim_ws=False)
    assert kept[" label"].tolist() == ["  hi ", "  in  "]


def test_csv_columns_typed_with_dtype_are_trimmed_but_not_retyped(data_project: Path) -> None:
    path = data_project / "inputs/raw/ids.csv"
    path.write_text("id,n\n 007 , 1\n008,2\n", encoding="utf-8")
    frame = fw.data_read("inputs/raw/ids.csv", dtype={"id": str})
    assert frame["id"].tolist() == ["007", "008"]
    assert frame["n"].tolist() == [1, 2]


def test_r_reader_arguments_are_refused_for_other_formats(data_project: Path) -> None:
    FRAME.to_parquet(data_project / "inputs/raw/x.parquet")
    with pytest.raises(fw.FrameworkError, match="na= does not apply to parquet"):
        fw.data_read("inputs/raw/x.parquet", na="")


@pytest.fixture
def workbook(data_project: Path) -> Path:
    openpyxl = pytest.importorskip("openpyxl")
    book = openpyxl.Workbook()
    first = book.active
    first.title = "visits"
    for row in (["id", " name ", "note"], [1, "  bob ", "NA"], [2, "None", None], [3, "  ", "x"]):
        first.append(row)
    second = book.create_sheet("labs")
    for row in (["title row"], ["code", "value"], ["a", 5]):
        second.append(row)
    path = data_project / "inputs/raw/book.xlsx"
    book.save(path)
    return path


def test_excel_missing_values_and_whitespace_follow_readxl(workbook: Path) -> None:
    frame = fw.data_read("inputs/raw/book.xlsx")
    assert list(frame.columns) == ["id", "name", "note"]
    # readxl: "NA" and "None" are text, empty and blank cells are missing, text is trimmed
    assert frame["name"].tolist()[:2] == ["bob", "None"]
    assert frame["name"].isna().tolist() == [False, False, True]
    assert frame["note"].tolist()[0] == "NA"


def test_excel_takes_r_argument_names(workbook: Path) -> None:
    labs = fw.data_read("inputs/raw/book.xlsx", sheet="labs", skip=1)
    assert labs.to_dict("list") == {"code": ["a"], "value": [5]}
    assert fw.data_read("inputs/raw/book.xlsx", sheet=2, skip=1).equals(labs)

    raw = fw.data_read("inputs/raw/book.xlsx", sheet=2, col_names=False, n_max=1)
    assert list(raw.columns) == ["...1", "...2"]
    assert raw["...1"].tolist() == ["title row"]

    named = fw.data_read("inputs/raw/book.xlsx", skip=1, col_names=["a", "b", "c"], n_max=1)
    assert named.to_dict("list") == {"a": [1], "b": ["bob"], "c": ["NA"]}

    typed = fw.data_read("inputs/raw/book.xlsx", col_types=["text", "text", "skip"])
    assert typed.to_dict("list")["id"] == ["1", "2", "3"]
    assert list(typed.columns) == ["id", "name"]

    with pytest.raises(fw.FrameworkError, match="positions start at 1"):
        fw.data_read("inputs/raw/book.xlsx", sheet=0)
    with pytest.raises(fw.FrameworkError, match="not both"):
        fw.data_read("inputs/raw/book.xlsx", sheet=1, sheet_name=0)


def test_csv_takes_readr_argument_names(data_project: Path) -> None:
    path = data_project / "inputs/raw/export.csv"
    path.write_text(
        "# exported 2026-11-07\nid,zip,flag,drop\n7,02139,TRUE,x\n8,21205,F,y\n",
        encoding="utf-8",
    )
    frame = fw.data_read("inputs/raw/export.csv", skip=1, col_types="ccl_")
    assert frame.to_dict("list") == {
        "id": ["7", "8"],
        "zip": ["02139", "21205"],
        "flag": [True, False],
    }
    assert str(frame["flag"].dtype) == "boolean"

    mapped = fw.data_read("inputs/raw/export.csv", skip=1, col_types={"zip": "c", "id": "i"})
    assert mapped["zip"].tolist() == ["02139", "21205"]
    assert str(mapped["id"].dtype) == "Int64"

    headless = fw.data_read("inputs/raw/export.csv", skip=2, col_names=False, n_max=1)
    assert list(headless.columns) == ["X1", "X2", "X3", "X4"]
    assert headless["X1"].tolist() == [7]

    with pytest.raises(fw.FrameworkError, match="3 types, but the file has 4"):
        fw.data_read("inputs/raw/export.csv", skip=1, col_types="ccl")
    with pytest.raises(fw.FrameworkError, match="sheet= does not apply to csv"):
        fw.data_read("inputs/raw/export.csv", sheet=1)
