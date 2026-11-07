"""save_model() and load_model()."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import framework as fw

DATA = pd.DataFrame({"y": [1.0, 2.0, 3.0, 5.0, 4.0, 6.0], "x": [1, 2, 3, 4, 5, 6]})


class Holder:
    def __init__(self) -> None:
        self.training = DATA


@pytest.fixture
def sensitive(project: Path) -> Path:
    (project / "settings.yml").write_text(
        "default:\n  project_type: project_sensitive\n  security: {sensitive: true}\n",
        encoding="utf-8",
    )
    return project


def test_load_model_round_trips_save_model(project: Path) -> None:
    fw.save_model({"coef": [1.5, 2.0]}, "simple")
    assert fw.load_model("simple") == {"coef": [1.5, 2.0]}
    with pytest.raises(fw.FrameworkError, match="No saved model named 'other'"):
        fw.load_model("other")


def test_load_model_looks_in_private_then_public(sensitive: Path) -> None:
    fw.save_model("private", "m")
    fw.save_model("public", "m", public=True)
    assert fw.load_model("m") == "private"
    assert fw.load_model("m", public=True) == "public"
    fw.save_model("only public", "p", public=True)
    assert fw.load_model("p") == "only public"
    with pytest.raises(fw.FrameworkError, match="No saved model"):
        fw.load_model("p", public=False)


def test_load_model_names_an_r_model(project: Path) -> None:
    folder = project / "outputs/models"
    folder.mkdir(parents=True)
    (folder / "fit.rds").write_bytes(b"X")
    with pytest.raises(fw.FrameworkError, match="model R saved"):
        fw.load_model("fit")


def test_load_model_warns_about_a_changed_file(project: Path) -> None:
    path = fw.save_model([1], "m")
    fw.save_model([1, 2], "other")
    path.write_bytes((project / "outputs/models/other.pkl").read_bytes())
    with pytest.warns(fw.FrameworkWarning, match=r"m\.pkl changed"):
        assert fw.load_model("m") == [1, 2]


def test_statsmodels_formula_result_loads_at_the_top_level_of_a_script(project: Path) -> None:
    smf = pytest.importorskip("statsmodels.formula.api")
    assert np  # the formula below looks np up in this frame
    fit = smf.ols("y ~ np.log(x)", data=DATA).fit()
    fw.save_model(fit, "fit")
    assert fw.load_model("fit").params.round(6).to_dict() == fit.params.round(6).to_dict()

    # A plain pickle.load() fails here: statsmodels looks for the caller's frame
    script = "import framework as fw\nprint(round(fw.load_model('fit').params.iloc[1], 6))\n"
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=project, capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == str(round(fit.params.iloc[1], 6))


def test_public_model_with_data_rows_warns(sensitive: Path) -> None:
    smf = pytest.importorskip("statsmodels.formula.api")
    sm = pytest.importorskip("statsmodels.api")
    fit = smf.ols("y ~ x", data=DATA).fit()
    fw.save_model(fit, "fit")  # private: no warning
    # A formula result keeps its data frame for loading, even after remove_data()
    fit.remove_data()
    with pytest.warns(fw.FrameworkWarning, match="holds 6 data rows.*even after remove_data"):
        fw.save_model(fit, "fit", public=True)

    plain = sm.OLS(DATA["y"], sm.add_constant(DATA["x"])).fit()
    with pytest.warns(fw.FrameworkWarning, match="holds 6 data rows.*Call remove_data"):
        fw.save_model(plain, "plain", public=True)
    plain.remove_data()
    fw.save_model(plain, "plain", public=True)
    loaded = fw.load_model("plain", public=True)
    assert loaded.params.round(6).to_dict() == plain.params.round(6).to_dict()

    with pytest.warns(fw.FrameworkWarning, match="holds 6 data rows.*Remove them"):
        fw.save_model(Holder(), "holder", public=True)
    fw.save_model({"coef": 1}, "coef", public=True)
