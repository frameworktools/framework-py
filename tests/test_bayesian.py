"""Posterior summaries for models fit by sampling.

The InferenceData tests build posteriors from fixed arrays, so they need ArviZ but no
sampler. The Bambi tests sample briefly and are skipped without Bambi. To run them:
``uv run --group bayesian pytest tests/test_bayesian.py``.
"""

from __future__ import annotations

import json
import warnings
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

from framework import _artifacts, _bayesian, storage
from framework.errors import FrameworkError
from framework.publish_bundle import build_bundle

PRIVATE = "private-observation"
COLUMNS = ["term", "estimate", "std_error", "conf_low", "conf_high", "rhat", "ess_bulk", "ess_tail"]
RATIO = ["ratio_estimate", "ratio_std_error", "ratio_conf_low", "ratio_conf_high"]


def export(source: Any, tmp_path: Path) -> tuple[Path, dict[str, Any]]:
    path = storage._artifact_file(source, tmp_path, cloud=True)
    return path, json.loads(path.read_text(encoding="utf-8"))


def metrics(document: dict[str, Any]) -> dict[str, Any]:
    return {m["name"]: m["value"] for m in document["metrics"]}


def tables(document: dict[str, Any]) -> dict[str, pd.DataFrame]:
    return {
        t["title"]: pd.DataFrame(t["rows"], columns=[c["name"] for c in t["columns"]])
        for t in document["tables"]
    }


def check_bayesian(path: Path, document: dict[str, Any]) -> None:
    assert path.name == "model.json"
    assert document["kind"] == "model"
    assert document["model"]["inference"] == "bayesian"
    text = path.read_text(encoding="utf-8")
    assert PRIVATE not in text
    for key in ("statistic", "p_value", "posterior", "observed_data", "draws_"):
        assert f'"{key}"' not in text
    # Summaries only: one row per parameter, never per draw or observation.
    assert all(t["row_count"] < 100 for t in document["tables"])


def _posterior(**groups: dict[str, Any]) -> Any:
    """Build an InferenceData (ArviZ 0.x) or DataTree (ArviZ 1.x) from arrays."""
    az = pytest.importorskip("arviz")
    dims = groups.pop("dims", None)
    coords = groups.pop("coords", None)
    attrs = groups.pop("attrs", {})
    if int(az.__version__.split(".")[0]) >= 1:
        data = az.from_dict(groups, dims=dims, coords=coords)
    else:
        data = az.from_dict(**groups, dims=dims, coords=coords)
    for name in ("posterior", "sample_stats"):
        children = getattr(data, "children", None)
        group = children.get(name) if children is not None else getattr(data, name, None)
        if group is not None:
            group.attrs.update(attrs)
    return data


def _draws(seed: int = 3) -> dict[str, np.ndarray[Any, Any]]:
    rng = np.random.default_rng(seed)
    return {
        "alpha": rng.normal(1.0, 0.5, size=(2, 200)),
        "beta": rng.normal([0.2, -0.4, 0.9], 0.1, size=(2, 200, 3)),
        "mu": rng.normal(size=(2, 200, 40)),
    }


# --------------------------------------------------------------------------- InferenceData


def test_posterior_summaries(tmp_path: Path) -> None:
    draws = _draws()
    data = _posterior(
        posterior=draws,
        sample_stats={
            "diverging": np.array([[True] * 3 + [False] * 197, [False] * 200]),
            "reached_max_treedepth": np.zeros((2, 200), dtype=bool),
            "tree_depth": np.full((2, 200), 3),
        },
        observed_data={"y": np.arange(40.0)},
        dims={"beta": ["species"], "mu": ["obs"], "y": ["obs"]},
        coords={"species": ["Adelie", "Chinstrap", "Gentoo"], "obs": np.arange(40)},
        attrs={"inference_library": "pymc", "tuning_steps": 500},
    )
    assert _bayesian.is_posterior(data)
    path, document = export(data, tmp_path)
    check_bayesian(path, document)
    assert document["model"] == {"type": "other", "inference": "bayesian"}
    assert [c["name"] for c in document["tables"][0]["columns"]] == COLUMNS
    frame = tables(document)["Posterior summaries"].set_index("term")
    # mu is indexed by observation, so it is left out.
    assert list(frame.index) == ["alpha", "beta[Adelie]", "beta[Chinstrap]", "beta[Gentoo]"]
    alpha = draws["alpha"].ravel()
    assert frame.loc["alpha", "estimate"] == pytest.approx(alpha.mean())
    assert frame.loc["alpha", "std_error"] == pytest.approx(alpha.std(ddof=1))
    # Equal-tailed 95% interval, not ArviZ's default 94% HDI.
    low, high = np.quantile(alpha, [0.025, 0.975])
    assert frame.loc["alpha", "conf_low"] == pytest.approx(low)
    assert frame.loc["alpha", "conf_high"] == pytest.approx(high)
    gentoo = draws["beta"][:, :, 2].ravel()
    assert frame.loc["beta[Gentoo]", "estimate"] == pytest.approx(gentoo.mean())
    assert frame["rhat"].between(0.95, 1.05).all()
    assert (frame["ess_bulk"] > 100).all() and (frame["ess_tail"] > 100).all()
    assert metrics(document) == {
        "nobs": 40,
        "confidence_level": 0.95,
        "interval_method": "Equal-tailed",
        "chains": 2,
        "draws": 400,
        "warmup": 500,
        "sampler": "NUTS (PyMC)",
        "divergent_transitions": 3,
        "max_treedepth_hits": 0,
    }


def test_posterior_without_sample_stats(tmp_path: Path) -> None:
    data = _posterior(posterior={"theta": np.random.default_rng(1).normal(size=(4, 50))})
    path, document = export(data, tmp_path)
    check_bayesian(path, document)
    assert metrics(document) == {
        "confidence_level": 0.95,
        "interval_method": "Equal-tailed",
        "chains": 4,
        "draws": 200,
    }


def test_posterior_skips_large_variables(tmp_path: Path) -> None:
    rng = np.random.default_rng(2)
    data = _posterior(posterior={"latent": rng.normal(size=(2, 30, 500))})
    with pytest.raises(FrameworkError, match="no posterior parameters"):
        export(data, tmp_path)


def test_posterior_bundle_keeps_no_draws(tmp_path: Path) -> None:
    data = _posterior(posterior=_draws())
    manifest = build_bundle(data, tmp_path / "bundle")
    assert manifest["kind"] == "model"
    assert sorted(p.name for p in (tmp_path / "bundle").iterdir()) == [
        "artifact.json",
        "index.html",
        "model.json",
        "representation.json",
    ]
    assert (tmp_path / "bundle" / "model.json").stat().st_size < 10_000


# --------------------------------------------------------------------------- figures


def test_axes_arrays_publish_as_figures(tmp_path: Path) -> None:
    pytest.importorskip("matplotlib")
    from matplotlib.figure import Figure

    figure = Figure()
    axes = figure.subplots(1, 2)
    assert isinstance(axes, np.ndarray)
    assert _artifacts.as_frame(axes) is None
    assert _artifacts.structured(axes) is None
    path = storage._artifact_file(axes, tmp_path, cloud=True)
    assert path.name == "figure.svg"


def test_arviz_plots_publish_as_figures(tmp_path: Path) -> None:
    az = pytest.importorskip("arviz")
    pytest.importorskip("matplotlib")
    import matplotlib.pyplot as plt

    data = _posterior(posterior=_draws(), dims={"beta": ["k"], "mu": ["obs"]})
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            plots = [az.plot_forest(data, var_names=["alpha", "beta"])]
            plots.append(
                az.plot_posterior(data, var_names=["alpha", "beta"])
                if hasattr(az, "plot_posterior")
                else az.plot_dist(data, var_names=["alpha", "beta"])
            )
        for plot in plots:
            path = storage._artifact_file(plot, tmp_path, cloud=True)
            assert path.name == "figure.svg"
            assert b"<svg" in path.read_bytes()[:2000]
            path.unlink()
            assert build_bundle(plot, tmp_path / "bundle")["kind"] == "image"
    finally:
        plt.close("all")


# --------------------------------------------------------------------------- Bambi


def _fit(formula: Any, frame: pd.DataFrame, **options: Any) -> tuple[Any, Any]:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        bmb = pytest.importorskip("bambi")
        model = bmb.Model(formula, frame, **options)
        idata = model.fit(draws=150, tune=150, chains=2, cores=1, random_seed=11, progressbar=False)
    return model, idata


def _penguins(n: int = 150, seed: int = 9) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    species = rng.choice(["Adelie", "Chinstrap", "Gentoo"], n)
    flipper = rng.normal(200, 14, n).round()
    mass = -5000 + 46 * flipper + np.where(species == "Gentoo", 800, 0) + rng.normal(0, 300, n)
    frame = pd.DataFrame(
        {
            "body_mass_g": mass.round(),
            "flipper_length_mm": flipper,
            "species": species,
            "island": rng.choice(["Biscoe", "Dream", "Torgersen"], n),
        }
    )
    frame["heavy"] = (frame["body_mass_g"] > 4500).astype(int)
    frame.loc[[3, 40], "flipper_length_mm"] = np.nan
    frame.index = [f"{PRIVATE}-{i}" for i in range(n)]
    frame.attrs["column_labels"] = {"heavy": "Body mass above 4.5 kg"}
    return frame


@pytest.fixture(scope="module")
def bambi_logit() -> tuple[Any, Any]:
    return _fit(
        "heavy ~ flipper_length_mm + species + (1|island)",
        _penguins(),
        family="bernoulli",
        dropna=True,
    )


def test_bambi_logit(tmp_path: Path, bambi_logit: tuple[Any, Any]) -> None:
    model, idata = bambi_logit
    path, document = export(_bayesian.with_model(idata, model), tmp_path)
    check_bayesian(path, document)
    assert document["source"]["class"][0] == "bambi.models.Model"
    assert document["formula"] == "heavy ~ flipper_length_mm + species + (1|island)"
    assert document["model"] == {
        "type": "mixed",
        "family": "binomial",
        "link": "logit",
        "response": "heavy",
        "inference": "bayesian",
        "exponentiate": "Odds ratio",
    }
    assert [t["role"] for t in document["tables"]] == ["coefficients", "random_effects", "priors"]
    assert [c["name"] for c in document["tables"][0]["columns"]] == COLUMNS + RATIO
    effects = tables(document)["Population-level effects"].set_index("term")
    assert list(effects.index) == [
        "(Intercept)",
        "flipper_length_mm",
        "species[Chinstrap]",
        "species[Gentoo]",
    ]
    draws = np.asarray(idata.posterior["flipper_length_mm"].values).ravel()
    row = effects.loc["flipper_length_mm"]
    assert row["estimate"] == pytest.approx(draws.mean())
    # Ratios summarize the exponentiated draws, never the exponent of the summaries.
    assert row["ratio_estimate"] == pytest.approx(np.exp(draws).mean())
    assert row["ratio_std_error"] == pytest.approx(np.exp(draws).std(ddof=1))
    assert row["ratio_conf_low"] == pytest.approx(np.exp(row["conf_low"]))
    assert row["ratio_conf_high"] == pytest.approx(np.exp(row["conf_high"]))
    groups = tables(document)["Group-level effects"]
    assert list(groups.columns) == ["group", *COLUMNS]
    assert groups[["group", "term"]].values.tolist() == [["island", "1|island_sigma"]]
    priors = tables(document)["Priors"]
    assert list(priors["parameter"]) == ["(Intercept)", "flipper_length_mm", "species", "1|island"]
    assert priors["prior"].str.match(r"^\w+\(").all()
    assert "HalfNormal" in priors["prior"].iloc[-1]
    stats = metrics(document)
    assert stats["nobs"] == 148
    assert stats["chains"] == 2 and stats["draws"] == 300 and stats["warmup"] == 150
    assert stats["sampler"] == "NUTS (PyMC)"
    assert isinstance(stats["divergent_transitions"], int)
    assert stats["interval_method"] == "Equal-tailed"

    found = {v["name"]: v for v in document["variables"]}
    assert [(v["name"], v["role"]) for v in document["variables"]] == [
        ("heavy", "response"),
        ("flipper_length_mm", "predictor"),
        ("species", "predictor"),
        ("island", "group"),
    ]
    assert found["heavy"]["label"] == "Body mass above 4.5 kg"
    # Summaries cover the rows the model used: two missing flippers are dropped.
    assert found["flipper_length_mm"]["summary"]["count"] == 148
    assert found["species"]["levels"] == ["Adelie", "Chinstrap", "Gentoo"]
    assert found["species"]["reference"] == "Adelie"
    assert {t["term"]: t["parts"] for t in document["terms"]} == {
        "flipper_length_mm": [{"variable": "flipper_length_mm"}],
        "species[Chinstrap]": [{"variable": "species", "level": "Chinstrap"}],
        "species[Gentoo]": [{"variable": "species", "level": "Gentoo"}],
    }


def test_bambi_posterior_alone(tmp_path: Path, bambi_logit: tuple[Any, Any]) -> None:
    _, idata = bambi_logit
    path, document = export(idata, tmp_path)
    check_bayesian(path, document)
    assert document["model"] == {"type": "other", "inference": "bayesian"}
    terms = [row[0] for row in document["tables"][0]["rows"]]
    assert terms[:4] == ["Intercept", "flipper_length_mm", "species[Chinstrap]", "species[Gentoo]"]
    assert "1|island_sigma" in terms
    assert "variables" not in document and "priors" not in json.dumps(document)


def test_bambi_model_needs_its_posterior(bambi_logit: tuple[Any, Any]) -> None:
    model, idata = bambi_logit
    with pytest.raises(FrameworkError, match=r"publish\(idata, model=model\)"):
        _artifacts.structured(model)
    with pytest.raises(FrameworkError, match="InferenceData"):
        _bayesian.with_model(pd.DataFrame({"x": [1]}), model)
    with pytest.raises(FrameworkError, match="Bambi model"):
        _bayesian.with_model(idata, object())


def test_bambi_gaussian_with_distributional_terms(tmp_path: Path) -> None:
    bmb = pytest.importorskip("bambi")
    frame = _penguins().dropna()
    model, idata = _fit(
        bmb.Formula("body_mass_g ~ flipper_length_mm * species", "sigma ~ species"), frame
    )
    path, document = export(_bayesian.with_model(idata, model), tmp_path)
    check_bayesian(path, document)
    assert document["model"] == {
        "type": "linear",
        "family": "gaussian",
        "link": "identity",
        "response": "body_mass_g",
        "inference": "bayesian",
    }
    assert "; sigma ~ species" in document["formula"]
    # No ratio scale for an identity link.
    assert [c["name"] for c in document["tables"][0]["columns"]] == COLUMNS
    effects = tables(document)["Population-level effects"]
    assert "flipper_length_mm:species[Gentoo]" in set(effects["term"])
    assert {"sigma_Intercept", "sigma_species[Gentoo]"} <= set(effects["term"])
    mapped = {t["term"]: t["parts"] for t in document["terms"]}
    assert mapped["flipper_length_mm:species[Gentoo]"] == [
        {"variable": "flipper_length_mm"},
        {"variable": "species", "level": "Gentoo"},
    ]
    assert mapped["sigma_species[Gentoo]"] == [{"variable": "species", "level": "Gentoo"}]


def test_bambi_poisson_rate_ratio_and_constant_parameters(tmp_path: Path) -> None:
    rng = np.random.default_rng(4)
    frame = pd.DataFrame({"x": rng.normal(size=80)})
    frame["visits"] = rng.negative_binomial(5, 5 / (5 + np.exp(0.5 + 0.3 * frame["x"])))
    model, idata = _fit("visits ~ x", frame, family="negativebinomial")
    path, document = export(_bayesian.with_model(idata, model), tmp_path)
    check_bayesian(path, document)
    assert document["model"]["family"] == "negative binomial"
    assert document["model"]["exponentiate"] == "Rate ratio"
    found = tables(document)
    assert list(found["Distributional parameters"]["term"]) == ["alpha"]
    assert "alpha" in set(found["Priors"]["parameter"])
