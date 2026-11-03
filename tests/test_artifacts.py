"""Structured Framework Cloud exports for published objects.

Optional libraries are skipped when they aren't installed, so the normal test run
covers pandas and numpy only.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

import framework as fw
from framework import _artifacts, storage

PRIVATE = "private-observation"


def export(source: Any, tmp_path: Path) -> tuple[Path, Any]:
    path = storage._artifact_file(source, tmp_path, cloud=True)
    text = path.read_text(encoding="utf-8")
    return path, json.loads(text) if path.suffix == ".json" else text


def columns(document: dict[str, Any], index: int = 0) -> dict[str, dict[str, Any]]:
    return {c["name"]: c for c in document["tables"][index]["columns"]}


def metrics(document: dict[str, Any]) -> dict[str, Any]:
    return {m["name"]: m["value"] for m in document["metrics"]}


def check_model(path: Path, document: dict[str, Any]) -> None:
    assert path.name == "model.json"
    assert document["schema"] == "framework.artifact/v1"
    assert document["kind"] == "model"
    assert document["source"]["language"] == "python"
    assert document["model"]["type"] in {
        "linear",
        "glm",
        "mixed",
        "survival",
        "ordinal",
        "multinomial",
        "additive",
        "fixed_effects",
        "quantile",
        "linear_ml",
        "tree_ensemble",
        "other",
    }
    text = path.read_text(encoding="utf-8")
    assert PRIVATE not in text
    for key in ("resid", "residuals", "fittedvalues", "fitted", "endog", "exog", "data"):
        assert f'"{key}"' not in text
    # Model tables are per term, group, or feature: never one row per observation.
    assert all(table["row_count"] < 100 for table in document["tables"])


def check_test(path: Path, document: dict[str, Any]) -> dict[str, Any]:
    assert path.name == "test.json"
    assert document["schema"] == "framework.artifact/v1"
    assert document["kind"] == "test"
    test: dict[str, Any] = document["test"]
    assert isinstance(test["method"], str) and test["method"]
    assert 0 <= test["p_value"] <= 1
    assert isinstance(test["statistic"]["value"], float | int)
    return test


# --------------------------------------------------------------------------- tables


def test_table_summaries(tmp_path: Path) -> None:
    long = "x" * 120
    frame = pd.DataFrame(
        {
            "site": pd.Categorical(["North", "South", "North", None]),
            "visit": pd.to_datetime(["2026-01-02", None, "2026-03-04", "2026-02-01"]),
            "day": [date(2026, 1, 2), date(2026, 1, 1), None, date(2026, 1, 5)],
            "enrolled": [True, False, True, True],
            "note": [long, "b", long, None],
            "visits": pd.Series([1, 2, pd.NA, 4], dtype="Int64"),
            "score": [1.0, np.nan, 3.0, 4.0],
            "empty": [np.nan] * 4,
        }
    )
    path, document = export(frame, tmp_path)
    assert path.name == "table.json"
    assert document["kind"] == "table"
    table = document["tables"][0]
    assert "role" not in table
    cols = columns(document)
    assert [c["type"] for c in cols.values()] == [
        "string",
        "datetime",
        "date",
        "boolean",
        "string",
        "number",
        "number",
        "number",
    ]
    site = cols["site"]["summary"]
    assert site == {
        "count": 4,
        "missing": 1,
        "distinct": 2,
        "top": [{"value": "North", "count": 2}, {"value": "South", "count": 1}],
    }
    assert cols["visit"]["summary"]["min"] == "2026-01-02T00:00:00"
    assert cols["visit"]["summary"]["max"] == "2026-03-04T00:00:00"
    assert cols["day"]["summary"]["min"] == "2026-01-01"
    assert cols["enrolled"]["summary"]["top"][0] == {"value": True, "count": 3}
    assert cols["note"]["summary"]["top"][0] == {"value": "x" * 80, "count": 2}
    score = cols["score"]["summary"]
    assert score["count"] == 4 and score["missing"] == 1 and score["distinct"] == 3
    assert score["mean"] == pytest.approx(8 / 3)
    assert score["median"] == 3.0 and score["min"] == 1.0 and score["max"] == 4.0
    histogram = score["histogram"]
    assert len(histogram["edges"]) == len(histogram["counts"]) + 1
    assert sum(histogram["counts"]) == 3
    assert cols["visits"]["summary"]["sd"] == pytest.approx(np.std([1, 2, 4], ddof=1))
    # Statistics that can't be computed are left out.
    assert cols["empty"]["summary"] == {"count": 4, "missing": 4, "distinct": 0}


def test_histogram_bins_and_summary_column_limit(tmp_path: Path) -> None:
    frame = pd.DataFrame({"x": np.arange(1000, dtype=float)})
    _, document = export(frame, tmp_path)
    histogram = columns(document)["x"]["summary"]["histogram"]
    assert len(histogram["counts"]) == 20
    assert sum(histogram["counts"]) == 1000
    wide = pd.DataFrame(np.zeros((2, 205)), columns=[f"c{i}" for i in range(205)])
    _, document = export(wide, tmp_path)
    summarized = [c for c in document["tables"][0]["columns"] if "summary" in c]
    assert len(summarized) == 200


def test_summary_uses_every_row(tmp_path: Path) -> None:
    frame = pd.DataFrame({"x": np.arange(5000)})
    _, document = export(frame, tmp_path)
    assert columns(document)["x"]["summary"]["count"] == 5000
    assert document["tables"][0]["row_count"] == 5000


def test_index_and_column_names(tmp_path: Path) -> None:
    frame = pd.DataFrame({"mean": [1.5, 2.5]}, index=pd.Index(["a", "b"], name="group"))
    _, document = export(frame, tmp_path)
    assert list(columns(document)) == ["group", "mean"]
    assert document["tables"][0]["rows"] == [["a", 1.5], ["b", 2.5]]

    unnamed = pd.DataFrame({"mean": [1.5]}, index=["a"])
    _, document = export(unnamed, tmp_path)
    assert list(columns(document)) == ["index", "mean"]

    shifted = pd.DataFrame({"mean": [1.5, 2.5]}).iloc[1:]
    _, document = export(shifted, tmp_path)
    assert list(columns(document)) == ["index", "mean"]

    wide = pd.DataFrame(
        [[1, 2, 3]], columns=pd.MultiIndex.from_tuples([("a", "x"), ("a", "y"), ("b", "")])
    )
    _, document = export(wide, tmp_path)
    assert list(columns(document)) == ["a_x", "a_y", "b"]


def test_series_numpy_and_crosstab(tmp_path: Path) -> None:
    path, document = export(pd.Series([1, 2, 3], name="score"), tmp_path)
    assert path.name == "table.json" and list(columns(document)) == ["score"]
    _, document = export(pd.Series([1, 2]), tmp_path)
    assert list(columns(document)) == ["value"]

    _, document = export(np.array([1.0, 2.0, np.nan]), tmp_path)
    assert list(columns(document)) == ["value"]
    assert document["tables"][0]["rows"] == [[1.0], [2.0], [None]]
    _, document = export(np.arange(6).reshape(3, 2), tmp_path)
    assert list(columns(document)) == ["V1", "V2"]
    structured = np.array([(1, "a")], dtype=[("id", "i4"), ("tag", "U1")])
    _, document = export(structured, tmp_path)
    assert list(columns(document)) == ["id", "tag"]
    with pytest.raises(fw.FrameworkError, match="1 or 2 dimensions"):
        export(np.zeros((2, 2, 2)), tmp_path)

    frame = pd.DataFrame({"arm": ["a", "a", "b"], "outcome": ["yes", "no", "yes"]})
    _, document = export(pd.crosstab(frame["arm"], frame["outcome"]), tmp_path)
    assert list(columns(document)) == ["arm", "no", "yes"]
    assert document["tables"][0]["rows"] == [["a", 1, 1], ["b", 0, 1]]


def test_bucket_table_exports_csv_with_index(tmp_path: Path) -> None:
    frame = pd.DataFrame({"mean": [1.5]}, index=pd.Index(["a"], name="group"))
    path = storage._artifact_file(frame, tmp_path)
    assert path.read_text() == "group,mean\na,1.5\n"
    path = storage._artifact_file(np.array([1, 2]), tmp_path)
    assert path.read_text() == "value\n1\n2\n"


def test_polars(tmp_path: Path) -> None:
    pl = pytest.importorskip("polars")
    frame = pl.DataFrame({"day": [date(2026, 1, 1), None], "count": [1, None], "group": ["a", "b"]})
    _, document = export(frame, tmp_path)
    cols = columns(document)
    assert [c["type"] for c in cols.values()] == ["date", "number", "string"]
    assert document["tables"][0]["rows"] == [["2026-01-01", 1, "a"], [None, None, "b"]]
    assert cols["count"]["summary"]["missing"] == 1
    _, document = export(pl.Series("rating", [1, 2, 3]), tmp_path)
    assert list(columns(document)) == ["rating"]
    _, document = export(pl.Series([1, 2]), tmp_path)
    assert list(columns(document)) == ["value"]


def test_styler(tmp_path: Path) -> None:
    pytest.importorskip("jinja2")
    styler = pd.DataFrame({"estimate": [1.234]}).style.format("{:.1f}")
    path, text = export(styler, tmp_path)
    assert path.name == "table.html"
    assert text.startswith("<!DOCTYPE html>") and "1.2" in text


def test_unsupported_objects(tmp_path: Path) -> None:
    for cloud in (True, False):
        with pytest.raises(fw.FrameworkError, match=r"publish\(\) accepts .* altair chart"):
            storage._artifact_file(object(), tmp_path, cloud=cloud)


# --------------------------------------------------------------------------- statsmodels


def test_anova_lm_role(tmp_path: Path) -> None:
    smf = pytest.importorskip("statsmodels.formula.api")
    anova_lm = pytest.importorskip("statsmodels.stats.anova").anova_lm
    rng = np.random.default_rng(1)
    frame = pd.DataFrame({"g": np.repeat(["a", "b", "c"], 20), "x": rng.normal(size=60)})
    frame["y"] = frame["x"] + (frame["g"] == "b") + rng.normal(size=60)
    table = anova_lm(smf.ols("y ~ C(g) + x", frame).fit(), typ=2)
    _, document = export(table, tmp_path)
    assert document["kind"] == "table"
    assert document["tables"][0]["role"] == "anova"
    assert next(iter(columns(document))) == "index"
    assert [row[0] for row in document["tables"][0]["rows"]] == ["C(g)", "x", "Residual"]


def _frame(n: int = 200, seed: int = 4) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    frame = pd.DataFrame({"x": rng.normal(size=n), "z": rng.integers(0, 2, n)})
    frame["y"] = 1 + 0.5 * frame["x"] + rng.normal(size=n)
    frame["binary"] = (frame["y"] + rng.normal(size=n) > 1).astype(int)
    frame["count"] = rng.poisson(np.exp(0.3 + 0.4 * frame["x"]))
    frame["school"] = rng.integers(0, 10, n)
    frame["y_mixed"] = frame["y"] + rng.normal(0, 1, 10)[frame["school"]]
    frame.index = [f"{PRIVATE}-{i}" for i in range(n)]
    return frame


def test_statsmodels_linear_and_glm(tmp_path: Path) -> None:
    sm = pytest.importorskip("statsmodels.api")
    smf = pytest.importorskip("statsmodels.formula.api")
    frame = _frame()
    cases = [
        (smf.ols("y ~ x + z", frame).fit(), {"type": "linear", "family": "gaussian"}, None),
        (smf.wls("y ~ x", frame).fit(), {"type": "linear", "link": "identity"}, None),
        (
            smf.glm("binary ~ x", frame, family=sm.families.Binomial()).fit(),
            {"type": "glm", "family": "binomial", "link": "logit"},
            "Odds ratio",
        ),
        (
            smf.glm("count ~ x", frame, family=sm.families.Poisson()).fit(),
            {"type": "glm", "family": "poisson", "link": "log"},
            "Rate ratio",
        ),
        (
            smf.glm(
                "binary ~ x",
                frame,
                family=sm.families.Binomial(link=sm.families.links.Probit()),
            ).fit(),
            {"type": "glm", "family": "binomial", "link": "probit"},
            None,
        ),
        (
            smf.glm("y ~ x", frame).fit(),
            {"type": "glm", "family": "gaussian", "link": "identity"},
            None,
        ),
    ]
    for fit, expected, exponentiate in cases:
        path, document = export(fit, tmp_path)
        check_model(path, document)
        model = document["model"]
        assert model.items() >= expected.items()
        assert model.get("exponentiate") == exponentiate
        assert model["response"] in ("y", "binary", "count")
        table = document["tables"][0]
        assert table["role"] == "coefficients"
        assert list(columns(document)) == [
            "term",
            "estimate",
            "std_error",
            "statistic",
            "p_value",
            "conf_low",
            "conf_high",
        ]
        assert table["rows"][0][0] == "(Intercept)"
        assert table["rows"][1][1] == pytest.approx(fit.params.iloc[1])
        assert metrics(document)["nobs"] == 200
        assert document["formula"]
        assert "summary" not in table["columns"][0]


def test_statsmodels_discrete_models(tmp_path: Path) -> None:
    smf = pytest.importorskip("statsmodels.formula.api")
    frame = _frame()
    cases = [
        (smf.logit("binary ~ x", frame).fit(disp=0), "binomial", "logit", "Odds ratio"),
        (smf.probit("binary ~ x", frame).fit(disp=0), "binomial", "probit", None),
        (smf.poisson("count ~ x", frame).fit(disp=0), "poisson", "log", "Rate ratio"),
        (
            smf.negativebinomial("count ~ x", frame).fit(disp=0, maxiter=200),
            "negative binomial",
            "log",
            "Rate ratio",
        ),
    ]
    for fit, family, link, exponentiate in cases:
        path, document = export(fit, tmp_path)
        check_model(path, document)
        assert document["model"]["type"] == "glm"
        assert document["model"]["family"] == family
        assert document["model"]["link"] == link
        assert document["model"].get("exponentiate") == exponentiate
        assert "pseudo_rsquared" in metrics(document)
        terms = [row[0] for row in document["tables"][0]["rows"]]
        assert terms == ["(Intercept)", "x"]
    assert metrics(document)["theta"] == pytest.approx(1 / fit.params["alpha"])


@pytest.mark.filterwarnings("ignore::Warning")
def test_statsmodels_mixed_and_quantile(tmp_path: Path) -> None:
    smf = pytest.importorskip("statsmodels.formula.api")
    frame = _frame(400)
    fit = smf.mixedlm("y_mixed ~ x", frame, groups="school").fit()
    path, document = export(fit, tmp_path)
    check_model(path, document)
    assert document["model"] == {
        "type": "mixed",
        "family": "gaussian",
        "link": "identity",
        "response": "y_mixed",
    }
    assert [row[0] for row in document["tables"][0]["rows"]] == ["(Intercept)", "x"]
    random = document["tables"][1]
    assert random["role"] == "random_effects"
    assert list(columns(document, 1)) == ["group", "term", "variance", "std_dev"]
    assert random["rows"][0][:3] == ["school", "(Intercept)", pytest.approx(fit.cov_re.iloc[0, 0])]
    assert random["rows"][-1][0] == "Residual"
    assert random["rows"][-1][2] == pytest.approx(fit.scale)
    assert metrics(document)["n_groups.school"] == 10

    slopes = smf.mixedlm("y_mixed ~ x", frame, groups="school", re_formula="~x").fit()
    _, document = export(slopes, tmp_path)
    assert "correlation" in columns(document, 1)
    assert [row[1] for row in document["tables"][1]["rows"]] == ["(Intercept)", "x", ""]

    fit = smf.quantreg("y ~ x", frame).fit(q=0.25)
    path, document = export(fit, tmp_path)
    check_model(path, document)
    assert document["model"]["type"] == "quantile"
    assert metrics(document)["quantile"] == 0.25


def test_statsmodels_multinomial(tmp_path: Path) -> None:
    smf = pytest.importorskip("statsmodels.formula.api")
    frame = _frame(300)
    frame["level"] = pd.cut(frame["y"], 3, labels=False)
    fit = smf.mnlogit("level ~ x", frame).fit(disp=0)
    path, document = export(fit, tmp_path)
    check_model(path, document)
    assert document["model"]["type"] == "multinomial"
    terms = [row[0] for row in document["tables"][0]["rows"]]
    assert terms == ["1: (Intercept)", "1: x", "2: (Intercept)", "2: x"]
    assert document["tables"][0]["rows"][1][1] == pytest.approx(fit.params.iloc[1, 0])
    assert document["tables"][0]["rows"][1][5] == pytest.approx(fit.conf_int().iloc[1, 0])


# --------------------------------------------------------------------------- scipy


def test_scipy_tests(tmp_path: Path) -> None:
    stats = pytest.importorskip("scipy.stats")
    rng = np.random.default_rng(7)
    a, b = rng.normal(0, 1, 30), rng.normal(0.5, 2, 25)
    paired = a + rng.normal(0.2, 0.5, 30)
    cases = {
        "Welch's t-test": stats.ttest_ind(a, b, equal_var=False),
        "t-test": stats.ttest_rel(a, paired),
        "Pearson correlation": stats.pearsonr(a, paired),
        "Rank correlation": stats.spearmanr(a, paired),
        "Mann-Whitney U test": stats.mannwhitneyu(a, b),
        "Wilcoxon signed-rank test": stats.wilcoxon(a, paired),
        "Kruskal-Wallis rank sum test": stats.kruskal(a, b),
        "One-way ANOVA": stats.f_oneway(a, b),
        "Shapiro-Wilk normality test": stats.shapiro(a),
        "Kolmogorov-Smirnov test": stats.ks_2samp(a, b),
    }
    for method, result in cases.items():
        path, document = export(result, tmp_path)
        test = check_test(path, document)
        assert test["method"] == method
        assert test["p_value"] == pytest.approx(float(result.pvalue))
        assert test["statistic"]["value"] == pytest.approx(float(result.statistic))
        assert document["tables"] == [] and document["metrics"] == []
        # Result objects keep the input samples privately; none may leak.
        assert str(round(float(a[0]), 6)) not in path.read_text()

    _, document = export(stats.kendalltau(a, paired), tmp_path)
    assert document["test"]["estimates"][0]["name"] == "correlation"

    welch = stats.ttest_ind(a, b, equal_var=False)
    test = export(welch, tmp_path)[1]["test"]
    assert test["alternative"] == "two.sided"
    assert test["parameters"] == [{"name": "df", "value": pytest.approx(float(welch.df))}]
    interval = welch.confidence_interval()
    assert test["conf_int"] == {
        "level": 0.95,
        "low": pytest.approx(float(interval.low)),
        "high": pytest.approx(float(interval.high)),
    }
    assert test["estimates"][0]["name"] == "difference in means"
    test = export(stats.ttest_ind(a, b), tmp_path)[1]["test"]
    assert test["method"] == "t-test" and test["parameters"][0]["value"] == 53

    one_sided = export(stats.ttest_1samp(a, 0, alternative="greater"), tmp_path)[1]["test"]
    assert one_sided["alternative"] == "greater"
    assert one_sided["conf_int"]["high"] is None  # infinite bound

    pearson = export(stats.pearsonr(a, paired), tmp_path)[1]["test"]
    assert pearson["estimates"][0]["value"] == pytest.approx(float(stats.pearsonr(a, paired)[0]))
    assert pearson["conf_int"]["low"] < pearson["conf_int"]["high"]

    chi = stats.chi2_contingency([[10, 20], [30, 15]])
    path, document = export(chi, tmp_path)
    test = check_test(path, document)
    assert test["method"] == "Chi-squared test of independence"
    assert test["parameters"] == [{"name": "df", "value": 1}]
    expected = document["tables"][0]
    assert expected["title"] == "Expected frequencies"
    assert expected["rows"][0] == pytest.approx(list(chi.expected_freq[0]))


def test_generic_test_results(tmp_path: Path) -> None:
    class SomethingResult:
        statistic = 2.5
        pvalue = 0.04

    path, document = export(SomethingResult(), tmp_path)
    test = check_test(path, document)
    assert test == {
        "method": "Something",
        "statistic": {"name": "statistic", "value": 2.5},
        "p_value": 0.04,
    }

    class ArrayResult:
        statistic = np.array([1.0, 2.0])
        pvalue = np.array([0.1, 0.2])

    with pytest.raises(fw.FrameworkError, match="single statistic"):
        export(ArrayResult(), tmp_path)


# --------------------------------------------------------------------------- machine learning


def test_sklearn_linear_models(tmp_path: Path) -> None:
    pytest.importorskip("sklearn")
    from sklearn.linear_model import Lasso, LinearRegression, LogisticRegression

    frame = _frame()
    features = frame[["x", "z"]]
    for model in (LinearRegression(), Lasso(alpha=0.01)):
        fit = model.fit(features, frame["y"])
        path, document = export(fit, tmp_path)
        check_model(path, document)
        assert document["model"] == {"type": "linear_ml"}
        assert document["tables"][0]["role"] == "coefficients"
        assert list(columns(document)) == ["term", "estimate"]
        rows = document["tables"][0]["rows"]
        assert rows[0] == ["(Intercept)", pytest.approx(fit.intercept_)]
        assert [r[0] for r in rows] == ["(Intercept)", "x", "z"]
        assert metrics(document)["n_features"] == 2

    fit = LogisticRegression().fit(features.to_numpy(), frame["binary"])
    path, document = export(fit, tmp_path)
    check_model(path, document)
    assert document["model"]["exponentiate"] == "Odds ratio"
    assert [r[0] for r in document["tables"][0]["rows"]] == ["(Intercept)", "x0", "x1"]

    labels = np.array(["low", "mid", "high"])[pd.cut(frame["y"], 3, labels=False)]
    fit = LogisticRegression(max_iter=500).fit(features, labels)
    _, document = export(fit, tmp_path)
    assert "exponentiate" not in document["model"]
    terms = [r[0] for r in document["tables"][0]["rows"]]
    assert terms[:3] == ["high: (Intercept)", "high: x", "high: z"]
    assert len(terms) == 9


def test_sklearn_tree_models(tmp_path: Path) -> None:
    pytest.importorskip("sklearn")
    from sklearn.ensemble import HistGradientBoostingRegressor, RandomForestRegressor
    from sklearn.tree import DecisionTreeRegressor

    frame = _frame()
    features = frame[["x", "z"]]
    fit = RandomForestRegressor(n_estimators=20, max_depth=3, random_state=0)
    fit.fit(features, frame["y"])
    path, document = export(fit, tmp_path)
    check_model(path, document)
    assert document["model"] == {"type": "tree_ensemble"}
    table = document["tables"][0]
    assert table["role"] == "importance"
    assert list(columns(document)) == ["feature", "importance"]
    values = [r[1] for r in table["rows"]]
    assert values == sorted(values, reverse=True)
    assert table["rows"][0][0] == "x"
    assert metrics(document) == {
        "importance_type": "impurity",
        "n_estimators": 20,
        "max_depth": 3,
        "min_samples_leaf": 1,
        "n_features": 2,
    }
    _, document = export(DecisionTreeRegressor().fit(features, frame["y"]), tmp_path)
    assert document["tables"][0]["role"] == "importance"
    with pytest.raises(fw.FrameworkError, match="permutation_importance"):
        export(HistGradientBoostingRegressor().fit(features, frame["y"]), tmp_path)
    with pytest.raises(fw.FrameworkError, match="fitted"):
        export(RandomForestRegressor(), tmp_path)


def test_xgboost(tmp_path: Path) -> None:
    xgboost = pytest.importorskip("xgboost")
    frame = _frame()
    features = frame[["x", "z"]]
    fit = xgboost.XGBRegressor(n_estimators=10, max_depth=2, learning_rate=0.2)
    fit.fit(features, frame["y"])
    for source in (fit, fit.get_booster()):
        path, document = export(source, tmp_path)
        check_model(path, document)
        assert document["model"] == {"type": "tree_ensemble"}
        assert document["tables"][0]["role"] == "importance"
        assert {r[0] for r in document["tables"][0]["rows"]} == {"x", "z"}
        values = metrics(document)
        assert values["importance_type"] == "gain"
        assert values["n_estimators"] == 10
        assert values["max_depth"] == 2
        assert values["learning_rate"] == pytest.approx(0.2)


def test_lightgbm(tmp_path: Path) -> None:
    lightgbm = pytest.importorskip("lightgbm")
    frame = _frame()
    features = frame[["x", "z"]]
    fit = lightgbm.LGBMRegressor(n_estimators=10, learning_rate=0.2, verbose=-1)
    fit.fit(features, frame["y"])
    for source in (fit, fit.booster_):
        path, document = export(source, tmp_path)
        check_model(path, document)
        assert document["tables"][0]["role"] == "importance"
        assert {r[0] for r in document["tables"][0]["rows"]} == {"x", "z"}
        assert metrics(document)["importance_type"] == "gain"
        assert metrics(document)["n_features"] == 2


# --------------------------------------------------------------------------- figures


def test_matplotlib_svg_and_png_fallback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("matplotlib")
    from matplotlib.figure import Figure

    figure = Figure()
    axes = figure.subplots()
    axes.plot([1, 2], [3, 4])
    for source in (figure, axes):
        path = storage._artifact_file(source, tmp_path, cloud=True)
        assert path.name == "figure.svg"
        assert b"<svg" in path.read_bytes()[:2000]
        path.unlink()
    monkeypatch.setattr(_artifacts, "SVG_LIMIT", 10)
    path = storage._artifact_file(figure, tmp_path, cloud=True)
    assert path.name == "figure.png"
    assert path.read_bytes().startswith(b"\x89PNG")
    assert not (tmp_path / "figure.svg").exists()


def test_seaborn_grids(tmp_path: Path) -> None:
    sns = pytest.importorskip("seaborn")
    import matplotlib.pyplot as plt

    frame = pd.DataFrame({"x": [1.0, 2, 3, 4], "y": [2.0, 1, 4, 3], "g": ["a", "a", "b", "b"]})
    try:
        for grid in (
            sns.relplot(data=frame, x="x", y="y", col="g"),
            sns.jointplot(data=frame, x="x", y="y"),
            sns.pairplot(frame[["x", "y"]]),
        ):
            path = storage._artifact_file(grid, tmp_path, cloud=True)
            assert path.name == "figure.svg"
    finally:
        plt.close("all")


def test_plotly(tmp_path: Path) -> None:
    go = pytest.importorskip("plotly.graph_objects")
    path, text = export(go.Figure(go.Scatter(x=[1, 2], y=[3, 4])), tmp_path)
    assert path.name == "figure.html"
    assert "<html>" in text and "cdn.plot.ly" in text


def test_altair(tmp_path: Path) -> None:
    alt = pytest.importorskip("altair")
    frame = pd.DataFrame({"x": range(6000), "y": range(6000)})
    chart = alt.Chart(frame).mark_point().encode(x="x:Q", y="y:Q")
    for source in (chart, chart + chart, alt.hconcat(chart, chart)):
        path, spec = export(source, tmp_path)
        assert path.name == "figure.vl.json"
        assert "vega-lite" in spec["$schema"]
        datasets = spec.get("datasets", {})
        assert datasets and all(len(rows) == 6000 for rows in datasets.values())
    # The row limit is lifted only for the export.
    with pytest.raises(alt.MaxRowsError):
        chart.to_dict()
