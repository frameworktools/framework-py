"""Posterior summaries for models fit by sampling.

A sampled model publishes as a ``model`` representation with ``model.inference`` set
to ``"bayesian"``. Each parameter is summarized over its draws: posterior mean and SD,
an equal-tailed credible interval, R-hat, and bulk and tail effective sample sizes.
The draws themselves, the observed data, and any per-observation quantities stay out.

Two sources are supported: an ArviZ ``InferenceData`` (or the ``xarray.DataTree`` that
ArviZ 1.x uses) on its own, summarized variable by variable, and the same object with the
Bambi model that produced it, which adds the family, formula, priors, and variables.
"""

from __future__ import annotations

import contextlib
import importlib
import itertools
import warnings
from collections.abc import Iterator, Sequence
from typing import Any

import numpy as np
import pandas as pd

from framework._artifacts import (
    CONFIDENCE_LEVEL,
    _classes,
    _coding,
    _description,
    _Design,
    _document,
    _from,
    _has_class,
    _labels,
    _metrics,
    _Part,
    _position,
    _referenced,
    _variable,
    _variables_fields,
    table,
)
from framework.errors import FrameworkError

SAMPLE_DIMS = ("chain", "draw")
ELEMENT_LIMIT = 100
ROW_LIMIT = 1000
_INTERCEPT = "(Intercept)"
_LIBRARIES = {
    "pymc": "PyMC",
    "numpyro": "NumPyro",
    "cmdstanpy": "Stan",
    "pystan": "Stan",
    "stan": "Stan",
    "nutpie": "nutpie",
    "blackjax": "BlackJAX",
    "pyro": "Pyro",
}
_NUTS_STATS = ("tree_depth", "n_steps", "step_size", "depth", "treedepth")
_SUMMARY_COLUMNS = (
    "estimate",
    "std_error",
    "conf_low",
    "conf_high",
    "rhat",
    "ess_bulk",
    "ess_tail",
)


class BambiFit:
    """A Bambi model with the InferenceData its ``fit()`` returned."""

    def __init__(self, posterior: Any, model: Any) -> None:
        self.posterior = posterior
        self.model = model


def with_model(source: Any, model: Any) -> BambiFit:
    """Pair a posterior with the model that produced it, for ``publish(model=...)``."""
    if not _has_class(model, "bambi", "Model"):
        raise FrameworkError(
            f"publish(model=...) accepts a Bambi model, not {type(model).__name__}."
        )
    if not is_posterior(source):
        raise FrameworkError(
            "publish(source, model=...) needs the InferenceData that the model's fit() "
            f"returned, not {type(source).__name__}."
        )
    return BambiFit(source, model)


# --------------------------------------------------------------------------- posterior access


def _group(data: Any, name: str) -> Any:
    """Return one group of an InferenceData or DataTree as an xarray Dataset, or None."""
    children = getattr(data, "children", None)
    if children is not None:
        node = children.get(name)
        return None if node is None else node.to_dataset()
    groups = getattr(data, "groups", None)
    if callable(groups) and name in groups():
        return getattr(data, name)
    return None


def is_posterior(source: Any) -> bool:
    """Whether the object is an InferenceData or DataTree with sampled posterior draws."""
    if not (
        _has_class(source, "arviz", "InferenceData") or _has_class(source, "xarray", "DataTree")
    ):
        return False
    with contextlib.suppress(Exception):
        posterior = _group(source, "posterior")
        return posterior is not None and all(d in posterior.dims for d in SAMPLE_DIMS)
    return False


def _elements(array: Any) -> Iterator[tuple[str, dict[str, int]]]:
    """Yield each scalar element's label suffix (``[a, b]``) and its position by dimension."""
    dims = [d for d in array.dims if d not in SAMPLE_DIMS]
    for position in itertools.product(*(range(array.sizes[d]) for d in dims)):
        selection = dict(zip(dims, position, strict=True))
        labels = [
            str(array[d].values[i]) if d in array.coords else str(i) for d, i in selection.items()
        ]
        yield ("[" + ", ".join(labels) + "]" if labels else ""), selection


def _diagnostics(posterior: Any, names: Sequence[str]) -> tuple[Any, Any, Any]:
    """R-hat and bulk and tail ESS for these variables, or Nones without ArviZ."""
    for module in ("arviz_stats", "arviz"):
        try:
            library = importlib.import_module(module)
        except ImportError:
            continue
        rhat, ess = library.rhat, library.ess
        break
    else:
        return None, None, None
    subset = posterior[list(names)]
    results: list[Any] = []
    for function, method in ((rhat, "rank"), (ess, "bulk"), (ess, "tail")):
        with warnings.catch_warnings():
            # Short chains warn; the numbers still describe the draws published.
            warnings.simplefilter("ignore")
            try:
                results.append(function(subset, method=method))
            except Exception:
                results.append(None)
    return results[0], results[1], results[2]


def _value(result: Any, name: str, selection: dict[str, int]) -> float | None:
    if result is None or name not in result:
        return None
    try:
        value = float(result[name].isel(selection).values)
    except (TypeError, ValueError, IndexError, KeyError):
        return None
    return value if np.isfinite(value) else None


def _summarize(draws: np.ndarray[Any, Any]) -> list[float]:
    low, high = (1 - CONFIDENCE_LEVEL) / 2, (1 + CONFIDENCE_LEVEL) / 2
    q_low, q_high = np.quantile(draws, [low, high])
    sd = float(draws.std(ddof=1)) if draws.size > 1 else float("nan")
    return [float(draws.mean()), sd, float(q_low), float(q_high)]


def _rows(
    posterior: Any,
    variables: Sequence[tuple[str, str]],
    ratio: set[str] | None = None,
    group: dict[str, str] | None = None,
) -> pd.DataFrame:
    """Summarize each element of each (variable, term) pair; one row per element.

    ``ratio`` names the variables whose rows also get summaries of the exponentiated
    draws. ``group`` names a grouping factor per variable for group-level tables.
    """
    names = [name for name, _ in variables]
    rhat, bulk, tail = _diagnostics(posterior, names)
    rows = []
    for name, term in variables:
        array = posterior[name]
        for suffix, selection in _elements(array):
            draws = np.asarray(
                array.isel(selection).transpose(*SAMPLE_DIMS).values, dtype="float64"
            ).ravel()
            row: dict[str, Any] = {}
            if group is not None:
                row["group"] = group.get(name)
            row["term"] = term + suffix
            row |= dict(zip(_SUMMARY_COLUMNS[:4], _summarize(draws), strict=True))
            row |= {
                "rhat": _value(rhat, name, selection),
                "ess_bulk": _value(bulk, name, selection),
                "ess_tail": _value(tail, name, selection),
            }
            if ratio is not None:
                values = _summarize(np.exp(draws)) if name in ratio else [None, None, None, None]
                row |= dict(
                    zip(
                        ("ratio_estimate", "ratio_std_error", "ratio_conf_low", "ratio_conf_high"),
                        values,
                        strict=True,
                    )
                )
            rows.append(row)
    frame = pd.DataFrame(rows)
    for column in ("rhat", "ess_bulk", "ess_tail"):
        if column in frame and frame[column].isna().all():
            frame = frame.drop(columns=column)
    return frame


def _observed_dims(data: Any) -> set[str]:
    dims: set[str] = set()
    for name in ("observed_data", "constant_data", "predictions_constant_data"):
        group = _group(data, name)
        if group is not None:
            dims |= {str(d) for d in group.dims}
    return dims


def _nobs(data: Any) -> int | None:
    observed = _group(data, "observed_data")
    if observed is None or len(observed.data_vars) != 1:
        return None
    array = next(iter(observed.data_vars.values()))
    return int(array.shape[0]) if array.ndim else None


def _sampling_metrics(data: Any, posterior: Any) -> list[dict[str, Any]]:
    chains, draws = int(posterior.sizes["chain"]), int(posterior.sizes["draw"])
    attrs = dict(getattr(posterior, "attrs", {}) or {})
    warmup = attrs.get("tuning_steps")
    if warmup is None:
        warm = _group(data, "warmup_posterior")
        warmup = warm.sizes.get("draw") if warm is not None else None
    stats = _group(data, "sample_stats")
    sampler = divergent = treedepth = None
    if stats is not None:
        if "diverging" in stats:
            divergent = int(np.asarray(stats["diverging"].values, dtype=bool).sum())
        if "reached_max_treedepth" in stats:
            treedepth = int(np.asarray(stats["reached_max_treedepth"].values, dtype=bool).sum())
        library = _LIBRARIES.get(str(attrs.get("inference_library", "")).lower())
        if library and "diverging" in stats and any(s in stats for s in _NUTS_STATS):
            sampler = f"NUTS ({library})"
    return _metrics(
        [
            ("chains", chains),
            ("draws", chains * draws),
            ("warmup", int(warmup) if isinstance(warmup, (int, np.integer)) else None),
            ("sampler", sampler),
            ("divergent_transitions", divergent),
            ("max_treedepth_hits", treedepth),
        ]
    )


def _interval_metrics() -> list[dict[str, Any]]:
    return [
        {"name": "confidence_level", "value": CONFIDENCE_LEVEL},
        {"name": "interval_method", "value": "Equal-tailed"},
    ]


def _with_classes(document: dict[str, Any], *sources: Any) -> dict[str, Any]:
    document["source"]["class"] = [
        f"{module}.{name}"
        for source in sources
        for module, name in _classes(source)
        if module.split(".")[0] not in ("builtins", "collections", "typing")
    ]
    return document


# --------------------------------------------------------------------------- InferenceData


def posterior_document(data: Any) -> dict[str, Any]:
    """Summarize every named posterior variable of an InferenceData or DataTree.

    Variables indexed by an observation dimension (one shared with observed or constant
    data) and variables with more than ``ELEMENT_LIMIT`` elements are left out: they
    describe observations or latent values, not parameters.
    """
    posterior = _group(data, "posterior")
    skip = _observed_dims(data)
    names = []
    for name, array in posterior.data_vars.items():
        dims = [str(d) for d in array.dims if d not in SAMPLE_DIMS]
        size = int(np.prod([array.sizes[d] for d in dims])) if dims else 1
        if skip & set(dims) or size > ELEMENT_LIMIT:
            continue
        if not np.issubdtype(array.dtype, np.number) or np.issubdtype(array.dtype, np.bool_):
            continue
        names.append(str(name))
    if not names:
        raise FrameworkError(
            "This InferenceData has no posterior parameters to publish: every variable is "
            "indexed by observation or has more than 100 elements."
        )
    frame = _rows(posterior, [(name, name) for name in names])
    truncated = len(frame) > ROW_LIMIT
    coefficients = table(frame.head(ROW_LIMIT), "Posterior summaries", role="coefficients")
    if truncated:
        coefficients |= {"row_count": len(frame), "truncated": True}
    document = _with_classes(_document(data, "model"), data) | {
        "model": _description("other", inference="bayesian"),
        "tables": [coefficients],
        "metrics": _metrics([("nobs", _nobs(data))])
        + _interval_metrics()
        + _sampling_metrics(data, posterior),
    }
    return document


# --------------------------------------------------------------------------- Bambi

_BAMBI_FAMILIES = {
    "bernoulli": "binomial",
    "binomial": "binomial",
    "gaussian": "gaussian",
    "poisson": "poisson",
    "negativebinomial": "negative binomial",
    "gamma": "gamma",
    "t": "student t",
    "categorical": "multinomial",
    "multinomial": "multinomial",
    "dirichlet_multinomial": "multinomial",
}
_ORDINAL = {"cumulative", "sratio"}


def _quiet(source: Any, *names: str) -> Any:
    """The first attribute present among names, without Bambi's deprecation warnings."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        for name in names:
            value = getattr(source, name, None)
            if value is not None:
                return value
    return None


def _components(model: Any) -> dict[str, Any]:
    components = _quiet(model, "_components", "components")
    return dict(components) if isinstance(components, dict) else {}


def _bambi_description(model: Any, grouped: bool, response: str | None) -> dict[str, Any]:
    family = model.family
    name = str(getattr(family, "name", "")).lower()
    parent = family.likelihood.parent
    link = getattr(family.link.get(parent), "name", None) if isinstance(family.link, dict) else None
    mapped = _BAMBI_FAMILIES.get(name, name or None)
    if name in _ORDINAL:
        model_type = "ordinal"
        mapped = None
    elif mapped == "multinomial":
        model_type = "multinomial"
    elif grouped:
        model_type = "mixed"
    elif mapped == "gaussian" and link == "identity":
        model_type = "linear"
    else:
        model_type = "glm"
    return _description(
        model_type, family=mapped, link=link, response=response, inference="bayesian"
    )


def _prior_text(prior: Any) -> str:
    """Bambi priors as text, with array arguments collapsed when every entry is equal."""
    if not _has_class(prior, "bambi", "Prior"):
        return _number_text(prior)
    args = ", ".join(f"{key}: {_number_text(value)}" for key, value in prior.args.items())
    return f"{prior.name}({args})"


def _number_text(value: Any) -> str:
    if _has_class(value, "bambi", "Prior"):
        return _prior_text(value)
    if isinstance(value, (np.ndarray, list, tuple)):
        values = np.asarray(value).ravel()
        if values.size and np.issubdtype(values.dtype, np.number) and np.all(values == values[0]):
            return _number_text(values[0])
        return "[" + ", ".join(_number_text(v) for v in values) + "]"
    if isinstance(value, (bool, np.bool_)):
        return str(bool(value))
    if isinstance(value, (int, np.integer)):
        return str(int(value))
    if isinstance(value, (float, np.floating)):
        return f"{float(value):.4g}"
    return str(value)


def _formulae_design(terms: dict[str, Any], design: _Design, prefix: str) -> None:
    """Map a formulae design's common terms to variables, as Bambi names their columns."""
    for term in terms.values():
        components = getattr(term, "components", None)
        if not components:
            continue
        names = [prefix + str(label) for label in term.labels]
        factors: list[list[_Part]] = []
        single_numeric: str | None = None
        for component in components:
            expression = str(component.name)
            kind = getattr(component, "kind", None)
            variable = design.predictor(expression)
            if kind == "numeric":
                design.factor(variable)
                factors.append([_Part(expression, variable)])
                single_numeric = variable if len(components) == 1 else None
            elif kind == "categoric" and component.contrast_matrix is not None:
                contrast = component.contrast_matrix
                categories = [str(level) for level in component.levels]
                levels, reference = _coding(np.asarray(contrast.matrix), categories)
                design.factor(variable, categories)
                factors.append(
                    [
                        _Part(
                            f"{expression}[{label}]",
                            variable,
                            level,
                            indicator=level is not None,
                            categories=categories,
                            reference=reference,
                        )
                        for label, level in zip(contrast.labels, levels, strict=True)
                    ]
                )
            else:
                factors = []
                break
        if not factors:
            continue
        design.match(names, factors, prefix=prefix)
        if single_numeric is not None:
            # Multi-column transformations such as poly(x, 2) name columns their own way.
            for name in names:
                if name not in design.terms:
                    design.add(name, [_Part(name, single_numeric)])


def _bambi_variables(
    model: Any, frame: pd.DataFrame, response: str | None, coefficients: dict[str, Any]
) -> dict[str, Any]:
    """Return ``variables`` and ``terms`` for a Bambi model, or {} when unknown."""
    columns = [str(c) for c in frame.columns]
    if len(set(columns)) != len(columns):
        return {}
    frame = frame.set_axis(columns, axis=1)
    design = _Design(columns)
    groups: list[str] = []
    for component in _components(model).values():
        matrices = getattr(component, "design", None)
        if matrices is None:
            continue
        is_parent = bool(getattr(component, "is_parent", False))
        prefix = "" if is_parent else f"{component.prefix}_" if component.prefix else ""
        common = getattr(matrices, "common", None)
        if common is not None:
            _formulae_design(common.terms, design, prefix)
        group = getattr(matrices, "group", None)
        for term in getattr(group, "terms", {}).values():
            for part in getattr(term.expr, "components", []) or []:
                design.factor(design.predictor(str(part.name)))
            for part in getattr(term.factor, "components", []) or []:
                used = _referenced(str(part.name), design.columns)
                groups += [name for name in sorted(used) if name not in groups]
    labels = _labels(model.data)
    formula = str(getattr(model.formula, "main", ""))
    variables = []
    if response is not None and response in frame:
        label = labels.get(response)
        variables.append(_variable(response, frame[response], "response", label=label))
    predictors = sorted(design.predictors, key=lambda name: _position(name, formula))
    variables += [
        _variable(name, frame[name], "predictor", design, labels.get(name))
        for name in predictors
        if name != response and name not in groups
    ]
    variables += [
        _variable(name, frame[name], "group", label=labels.get(name))
        for name in groups
        if name != response
    ]
    return _variables_fields(variables, design.terms, coefficients)


def _used_frame(model: Any, components: dict[str, Any], nobs: int | None) -> pd.DataFrame | None:
    """The rows the model was fit on: formulae's frame after dropping missing values."""
    for component in components.values():
        matrices = getattr(component, "design", None)
        frame = getattr(matrices, "data", None)
        if isinstance(frame, pd.DataFrame) and (nobs is None or len(frame) == nobs):
            return frame
    data = getattr(model, "data", None)
    if isinstance(data, pd.DataFrame) and (nobs is None or len(data) == nobs):
        return data
    return None


def bambi_document(fit: BambiFit) -> dict[str, Any]:
    """Summarize a Bambi model's posterior by its terms, with its priors and variables."""
    model, data = fit.model, fit.posterior
    posterior = _group(data, "posterior")
    available = {str(name) for name in posterior.data_vars}
    components = _components(model)
    family = model.family
    parent = family.likelihood.parent

    common: list[tuple[str, str]] = []  # (posterior variable, term label)
    ratio: set[str] = set()
    grouped: list[tuple[str, str]] = []
    group_of: dict[str, str] = {}
    extra: list[tuple[str, str]] = []
    priors: list[tuple[str, str]] = []
    for name, component in components.items():
        terms = getattr(component, "terms", None)
        if not isinstance(terms, dict):
            # A parameter with no formula of its own, such as sigma or alpha.
            if name in available:
                extra.append((name, name))
            prior = getattr(component, "prior", None)
            if prior is not None:
                priors.append((name, _prior_text(prior)))
            continue
        is_parent = bool(getattr(component, "is_parent", False)) or name == parent
        group_terms = getattr(component, "group_specific_terms", {}) or {}
        for key, term in terms.items():
            variable = str(getattr(term, "name", key))
            label = _INTERCEPT if variable == "Intercept" else variable
            prior = getattr(term, "prior", None)
            if prior is not None:
                priors.append((label, _prior_text(prior)))
            if key in group_terms:
                sigma = f"{variable}_sigma"
                if sigma in available:
                    grouped.append((sigma, sigma))
                    factor = getattr(getattr(term, "term", None), "factor", None)
                    group_of[sigma] = str(getattr(factor, "name", key.split("|", 1)[-1]))
                continue
            if variable in available:
                common.append((variable, label))
                # The exponent of an intercept is a baseline, not a ratio: R leaves it out too
                if is_parent and label != "(Intercept)":
                    ratio.add(variable)

    if not common:
        raise FrameworkError("This Bambi model's posterior has none of its terms.")
    response_name = str(getattr(getattr(model, "formula", None), "main", "")).split("~", 1)[0]
    response_name = response_name.strip()
    nobs = _nobs(data)
    frame = _used_frame(model, components, nobs)
    response = None
    if frame is not None:
        used = _referenced(response_name, {str(c) for c in frame.columns})
        response = next(iter(used)) if len(used) == 1 else None
    description = _bambi_description(model, bool(grouped), response or response_name or None)
    exponentiate = description.get("exponentiate")
    tables = [
        table(
            _rows(posterior, common, ratio=ratio if exponentiate else None),
            "Population-level effects",
            role="coefficients",
        )
    ]
    if grouped:
        tables.append(
            table(
                _rows(posterior, grouped, group=group_of),
                "Group-level effects",
                role="random_effects",
            )
        )
    if extra:
        tables.append(table(_rows(posterior, extra), "Distributional parameters"))
    if priors:
        tables.append(
            table(pd.DataFrame(priors, columns=["parameter", "prior"]), "Priors", role="priors")
        )
    formula = getattr(model, "formula", None)
    formulas = [str(getattr(formula, "main", "") or "")]
    formulas += [str(f) for f in getattr(formula, "additionals", ()) or ()]
    document = _with_classes(_document(model, "model"), model, data) | {
        "model": description,
        "tables": tables,
        "metrics": _metrics([("nobs", nobs)])
        + _interval_metrics()
        + _sampling_metrics(data, posterior),
    }
    if formulas[0]:
        document["formula"] = "; ".join(f for f in formulas if f)
    if frame is not None:
        with contextlib.suppress(Exception):
            document |= _bambi_variables(model, frame, response, tables[0])
    return document


def is_bambi_model(source: Any) -> bool:
    return _from(source, "bambi") and _has_class(source, "bambi", "Model")
