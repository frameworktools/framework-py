"""R reads what Python wrote, using the R package from source.

These tests run only where R, pkgload, and an R checkout are available (the
checkout is ``FW_R_REPO``, default ``../framework``). CI runs them in the
r-interop job with ``FW_R_REQUIRED=1``, which makes a missing R a failure
instead of a skip. The committed fixtures cover the other direction.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

import framework as fw

R_REPO = Path(os.environ.get("FW_R_REPO", Path(__file__).parents[2] / "framework"))

_R_MISSING = shutil.which("Rscript") is None or not (R_REPO / "DESCRIPTION").is_file()
if _R_MISSING and os.environ.get("FW_R_REQUIRED") == "1":
    raise RuntimeError(f"FW_R_REQUIRED=1 but Rscript or the R checkout at {R_REPO} is missing")

pytestmark = pytest.mark.skipif(
    _R_MISSING, reason="needs Rscript and an R framework checkout (FW_R_REPO)"
)

R_SCRIPT = """
args <- commandArgs(trailingOnly = TRUE)
suppressMessages(pkgload::load_all(args[[1]], quiet = TRUE))
setwd(args[[2]])
parquet <- suppressMessages(data_read("inputs.intermediate.py_clean"))
csv <- suppressMessages(data_read("inputs/raw/py_raw.csv"))
results <- result_list()
cat(jsonlite::toJSON(list(
  parquet_rows = nrow(parquet),
  parquet_columns = I(names(parquet)),
  csv_rows = nrow(csv),
  csv_missing = sum(is.na(csv$score)),
  catalog = settings_read()$data$inputs$intermediate$py_clean,
  results = I(results$name)
), auto_unbox = TRUE))
"""


def run_r(project: Path) -> dict[str, Any]:
    result = subprocess.run(
        ["Rscript", "-e", R_SCRIPT, str(R_REPO), str(project)],
        capture_output=True,
        text=True,
        check=False,
        timeout=300,
    )
    assert result.returncode == 0, result.stderr
    data: dict[str, Any] = json.loads(result.stdout.strip().splitlines()[-1])
    return data


def test_r_reads_what_python_saved(r_project: Path) -> None:
    frame = pd.DataFrame({"id": [1, 2, 3], "score": [1.5, None, 3.0]})
    fw.data_save(frame, "inputs.intermediate.py_clean")
    fw.data_save(frame, "inputs/raw/py_raw.csv")
    fw.save_table(frame, "py_summary")

    seen = run_r(r_project)
    assert seen["parquet_rows"] == 3
    assert seen["parquet_columns"] == ["id", "score"]
    assert seen["csv_rows"] == 3
    assert seen["csv_missing"] == 1
    assert seen["catalog"] == {
        "path": "inputs/intermediate/py_clean.parquet",
        "type": "parquet",
        "locked": True,
    }
    # Both languages now record results by the path from the project root. The
    # fixture's R row predates that and keeps its file name until it is saved again.
    assert set(seen["results"]) == {"r_summary.csv", "outputs/tables/py_summary.csv"}


SETTINGS_SCRIPT = """
args <- commandArgs(trailingOnly = TRUE)
suppressMessages(pkgload::load_all(args[[1]], quiet = TRUE))
setwd(args[[2]])
cat(jsonlite::toJSON(settings_read("settings.yml"), auto_unbox = TRUE, null = "null"))
"""


@pytest.mark.parametrize("type", ["project", "project_sensitive", "course", "presentation", "bare"])
def test_r_reads_settings_python_created(tmp_path: Path, type: str) -> None:
    from tests.conftest import as_r_json, r_json

    spec = fw.spec_resolve([{"language": "python", "type": type}], "Cross Read").spec
    root = fw.project_create(spec, tmp_path / "project", headless=True)
    result = subprocess.run(
        ["Rscript", "-e", SETTINGS_SCRIPT, str(R_REPO), str(root)],
        capture_output=True,
        text=True,
        check=False,
        timeout=300,
    )
    assert result.returncode == 0, result.stderr
    from_r = json.loads(result.stdout.strip().splitlines()[-1])
    os.chdir(root)
    assert as_r_json(fw.settings_read()) == r_json(from_r)
