"""Structured exports for objects published to Framework Cloud.

The document format is ``framework.artifact/v1``. Tables carry the rows a person
chose to publish. Models carry coefficients, fit statistics, and other aggregates,
never the observations they were fit on or a serialized model. Optional libraries
(numpy aside) are detected by module and class name and imported only when an object
from them is published.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Iterable, Sequence
from datetime import date, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from pandas.api import types as dtypes

from framework.errors import FrameworkError

SCHEMA = "framework.artifact/v1"
SUMMARY_COLUMNS = 200
HISTOGRAM_BINS = 20
TOP_VALUES = 8
TOP_VALUE_LENGTH = 80
SVG_LIMIT = 5 * 1024 * 1024
CONFIDENCE_LEVEL = 0.95


# --------------------------------------------------------------------------- helpers


def scalar(value: Any) -> Any:
    """Return a JSON scalar while preserving missing values and finite numbers."""
    if value is None or value is pd.NA or value is pd.NaT:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    if pd.api.types.is_scalar(value) and pd.isna(value):
        return None
    return str(value)


def _classes(source: Any) -> list[tuple[str, str]]:
    return [(cls.__module__, cls.__name__) for cls in type(source).__mro__]


def _from(source: Any, *modules: str) -> bool:
    return any(module.split(".")[0] in modules for module, _ in _classes(source))


def _has_class(source: Any, module: str, *names: str) -> bool:
    return any(m.split(".")[0] == module and n in names for m, n in _classes(source))


def _metric(name: str, value: Any) -> dict[str, Any] | None:
    if value is None or isinstance(value, (list, tuple, dict, set)):
        return None
    if not pd.api.types.is_scalar(value):
        return None
    value = scalar(value)
    return None if value is None else {"name": name, "value": value}


def _metrics(pairs: Iterable[tuple[str, Any]]) -> list[dict[str, Any]]:
    return [m for name, value in pairs if (m := _metric(name, value)) is not None]


def _document(source: Any, kind: str) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "kind": kind,
        "source": {
            "language": "python",
            "class": [f"{module}.{name}" for module, name in _classes(source) if name != "object"],
        },
        "tables": [],
        "metrics": [],
    }


# --------------------------------------------------------------------------- tables


def _column_type(series: pd.Series) -> str:
    dtype = series.dtype
    if dtypes.is_bool_dtype(dtype):
        return "boolean"
    if dtypes.is_numeric_dtype(dtype) and not dtypes.is_complex_dtype(dtype):
        # Browser JSON numbers cannot represent large integer identifiers exactly.
        unsafe_integer = dtypes.is_integer_dtype(dtype) and any(
            abs(int(value)) > 2**53 - 1 for value in series.dropna()
        )
        return "string" if unsafe_integer else "number"
    if isinstance(dtype, pd.ArrowDtype) and dtype.kind == "M" and "date" in str(dtype):
        return "date"
    if dtypes.is_datetime64_any_dtype(dtype):
        return "datetime"
    values = series.dropna()
    if len(values) and all(isinstance(v, date) and not isinstance(v, datetime) for v in values):
        return "date"
    if len(values) and all(isinstance(v, datetime) for v in values):
        return "datetime"
    return "string"


def _top(values: pd.Series) -> list[dict[str, Any]]:
    counts = values.value_counts(dropna=True, sort=True).head(TOP_VALUES)
    top = []
    for value, count in counts.items():
        value = scalar(value)
        if isinstance(value, str) and len(value) > TOP_VALUE_LENGTH:
            value = value[:TOP_VALUE_LENGTH]
        top.append({"value": value, "count": int(count)})
    return top


def _histogram(values: np.ndarray[Any, Any]) -> dict[str, list[Any]]:
    low, high = float(values.min()), float(values.max())
    if low == high:
        return {"edges": [low, high], "counts": [len(values)]}
    bins = min(HISTOGRAM_BINS, len(np.unique(values)))
    counts, edges = np.histogram(values, bins=bins, range=(low, high))
    return {"edges": [float(e) for e in edges], "counts": [int(c) for c in counts]}


def _summary(series: pd.Series, kind: str) -> dict[str, Any]:
    present = series.dropna()
    if kind == "string":
        present = present.map(str)
    try:
        distinct = int(present.nunique())
    except TypeError:
        distinct = int(present.map(str).nunique())
    summary: dict[str, Any] = {
        "count": len(series),
        "missing": int(series.isna().sum()),
        "distinct": distinct,
    }
    if kind == "number":
        values = present.to_numpy(dtype="float64")
        values = values[np.isfinite(values)]
        if len(values):
            q1, median, q3 = np.quantile(values, [0.25, 0.5, 0.75])
            summary |= {
                "mean": float(values.mean()),
                **({"sd": float(values.std(ddof=1))} if len(values) > 1 else {}),
                "min": float(values.min()),
                "q1": float(q1),
                "median": float(median),
                "q3": float(q3),
                "max": float(values.max()),
                "histogram": _histogram(values),
            }
    elif kind in ("string", "boolean"):
        summary["top"] = _top(present)
    elif kind in ("date", "datetime") and len(present):
        summary |= {"min": scalar(present.min()), "max": scalar(present.max())}
    return summary


def _unique(names: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    result = []
    for name in names:
        candidate = name
        while candidate in seen:
            candidate += "_"
        seen.add(candidate)
        result.append(candidate)
    return result


def _flat_columns(frame: pd.DataFrame) -> list[str]:
    if isinstance(frame.columns, pd.MultiIndex):
        return [
            "_".join(str(part) for part in key if str(part) != "") or "column"
            for key in frame.columns
        ]
    return [str(name) for name in frame.columns]


def _default_index(index: pd.Index) -> bool:
    return (
        isinstance(index, pd.RangeIndex)
        and index.start == 0
        and index.step == 1
        and index.name is None
    )


def frame_for_export(frame: pd.DataFrame) -> pd.DataFrame:
    """Return the frame with flat column names and any meaningful index as columns."""
    frame = frame.copy(deep=False)
    columns = _flat_columns(frame)
    frame.columns = pd.Index(range(len(columns)))
    if not _default_index(frame.index):
        index = frame.index
        names = [
            str(name) if name is not None else ("index" if index.nlevels == 1 else f"index_{i}")
            for i, name in enumerate(index.names)
        ]
        levels = [index.get_level_values(i) for i in range(index.nlevels)]
        leading = pd.DataFrame({f"i{i}": pd.Series(level) for i, level in enumerate(levels)})
        frame = pd.concat([leading, frame.reset_index(drop=True)], axis=1)
        columns = names + columns
    frame.columns = pd.Index(_unique(columns))
    return frame


def table(
    frame: pd.DataFrame,
    title: str,
    role: str | None = None,
    summarize: bool = False,
) -> dict[str, Any]:
    """Return one ``tables[]`` entry. The frame's index is not exported."""
    if len(frame.columns) == 0:
        raise FrameworkError("Artifact tables require at least one column.")
    columns: list[dict[str, Any]] = []
    for position, name in enumerate(frame.columns):
        series = frame.iloc[:, position]
        kind = _column_type(series)
        column: dict[str, Any] = {"name": str(name), "type": kind}
        if summarize and position < SUMMARY_COLUMNS:
            column["summary"] = _summary(series, kind)
        columns.append(column)
    rows = [
        [
            None
            if scalar(value) is None
            else str(value)
            if columns[i]["type"] == "string"
            else scalar(value)
            for i, value in enumerate(row)
        ]
        for row in frame.itertuples(index=False, name=None)
    ]
    result: dict[str, Any] = {"title": title}
    if role is not None:
        result["role"] = role
    return result | {"columns": columns, "rows": rows, "row_count": len(frame)}


def _numpy_frame(array: np.ndarray[Any, Any]) -> pd.DataFrame:
    if array.dtype.names:
        if array.ndim != 1:
            raise FrameworkError("Structured numpy arrays must have one dimension.")
        return pd.DataFrame({name: array[name] for name in array.dtype.names})
    if array.ndim == 1:
        return pd.DataFrame({"value": array})
    if array.ndim == 2:
        return pd.DataFrame(array, columns=[f"V{i + 1}" for i in range(array.shape[1])])
    raise FrameworkError(
        f"publish() accepts numpy arrays with 1 or 2 dimensions, not {array.ndim}."
    )


def as_frame(source: Any) -> pd.DataFrame | None:
    """Convert table-like objects to pandas, or return None."""
    if isinstance(source, pd.DataFrame):
        return source
    if isinstance(source, pd.Series):
        name = "value" if source.name is None or source.name == "" else str(source.name)
        return source.to_frame(name)
    if isinstance(source, np.ndarray):
        return _numpy_frame(source)
    if _has_class(source, "polars", "DataFrame", "Series"):
        if _has_class(source, "polars", "Series"):
            source = source.to_frame(source.name or "value")
        # Arrow-backed columns keep polars dates as dates.
        frame: pd.DataFrame = source.to_pandas(use_pyarrow_extension_array=True)
        return frame
    return None


def _anova(frame: pd.DataFrame) -> bool:
    names = {str(name) for name in frame.columns}
    return (
        "F" in names
        and bool(names & {"PR(>F)", "Pr(>F)"})
        and bool(names & {"df", "df_resid", "df_diff"})
    )


def _table_document(source: Any, frame: pd.DataFrame) -> dict[str, Any]:
    role = "anova" if _anova(frame) else None
    entry = table(frame_for_export(frame), "Table", role=role, summarize=True)
    return _document(source, "table") | {"tables": [entry]}


# --------------------------------------------------------------------------- tests

_ALTERNATIVES = {"two-sided": "two.sided", "less": "less", "greater": "greater"}

# Class name -> (method, statistic name). scipy result classes don't record the
# function that made them, so names describe the class.
_TESTS: dict[str, tuple[str, str]] = {
    "PearsonRResult": ("Pearson correlation", "r"),
    "MannwhitneyuResult": ("Mann-Whitney U test", "U"),
    "WilcoxonResult": ("Wilcoxon signed-rank test", "W"),
    "KruskalResult": ("Kruskal-Wallis rank sum test", "H"),
    "F_onewayResult": ("One-way ANOVA", "F"),
    "Chi2ContingencyResult": ("Chi-squared test of independence", "X-squared"),
    "ShapiroResult": ("Shapiro-Wilk normality test", "W"),
    "KstestResult": ("Kolmogorov-Smirnov test", "D"),
    "BinomTestResult": ("Exact binomial test", "proportion"),
    "Power_divergenceResult": ("Chi-squared goodness-of-fit test", "X-squared"),
    "AndersonResult": ("Anderson-Darling test", "A-squared"),
    "BartlettResult": ("Bartlett test of homogeneity of variances", "K-squared"),
    "LeveneResult": ("Levene test of homogeneity of variances", "W"),
    "FlignerResult": ("Fligner-Killeen test of homogeneity of variances", "X-squared"),
    "NormaltestResult": ("D'Agostino-Pearson normality test", "K-squared"),
    "FriedmanchisquareResult": ("Friedman rank sum test", "X-squared"),
}


def _readable(name: str) -> str:
    name = name.removesuffix("Result").replace("_", " ")
    words, current = [], ""
    for char in name:
        if char.isupper() and current and not current[-1].isupper():
            words.append(current)
            current = char
        else:
            current += char
    words.append(current)
    text = " ".join(w.strip() for w in words if w.strip())
    return (text[:1].upper() + text[1:].lower()) if text else "Hypothesis test"


def _alternative(result: Any) -> str | None:
    value = getattr(result, "alternative", getattr(result, "_alternative", None))
    if isinstance(value, str):
        return _ALTERNATIVES.get(value)
    if isinstance(value, (int, float, np.number)) and not isinstance(value, bool):
        return {-1: "less", 0: "two.sided", 1: "greater"}.get(int(value))
    return None


def _single(value: Any, what: str) -> Any:
    if np.ndim(value) != 0:
        raise FrameworkError(
            f"publish() accepts tests with a single {what}; this result has several."
        )
    return scalar(value)


def _conf_int(result: Any) -> dict[str, Any] | None:
    method = getattr(result, "confidence_interval", None) or getattr(result, "proportion_ci", None)
    if not callable(method):
        return None
    try:
        interval = method(confidence_level=CONFIDENCE_LEVEL)
    except Exception:
        return None
    low: Any = getattr(interval, "low", None)
    high: Any = getattr(interval, "high", None)
    if np.ndim(low) != 0 or np.ndim(high) != 0:
        return None
    return {"level": CONFIDENCE_LEVEL, "low": scalar(low), "high": scalar(high)}


def _test_document(result: Any) -> dict[str, Any]:
    name = type(result).__name__
    if name == "TtestResult":
        df = scalar(getattr(result, "df", None))
        welch = isinstance(df, float) and not float(df).is_integer()
        method, statistic_name = ("Welch's t-test" if welch else "t-test"), "t"
    elif name == "SignificanceResult" and hasattr(result, "correlation"):
        method, statistic_name = "Rank correlation", "correlation"
    else:
        method, statistic_name = _TESTS.get(name, (_readable(name), "statistic"))
    test: dict[str, Any] = {"method": method}
    alternative = _alternative(result)
    if alternative is not None:
        test["alternative"] = alternative
    test["statistic"] = {"name": statistic_name, "value": _single(result.statistic, "statistic")}
    parameters = _metrics(
        (key, getattr(result, attr, None))
        for key, attr in (("df", "df"), ("df", "dof"))
        if hasattr(result, attr)
    )
    if parameters:
        test["parameters"] = parameters[:1]
    test["p_value"] = _single(result.pvalue, "p-value")
    estimates = []
    if name == "TtestResult" and hasattr(result, "_estimate"):
        label = "difference in means" if test["method"] == "Welch's t-test" else "estimate"
        estimates = _metrics([(label, getattr(result, "_estimate", None))])
    elif name == "PearsonRResult" or hasattr(result, "correlation"):
        estimates = _metrics([("correlation", getattr(result, "correlation", result.statistic))])
    elif name == "BinomTestResult":
        estimates = _metrics([("probability of success", result.proportion_estimate)])
    if estimates:
        test["estimates"] = estimates
    interval = _conf_int(result)
    if interval is not None:
        test["conf_int"] = interval
    document = _document(result, "test") | {"test": test}
    expected = getattr(result, "expected_freq", None)
    if isinstance(expected, np.ndarray) and expected.ndim in (1, 2):
        document["tables"] = [table(_numpy_frame(expected), "Expected frequencies")]
    return document


def _is_test(source: Any) -> bool:
    return hasattr(source, "statistic") and hasattr(source, "pvalue")


# --------------------------------------------------------------------------- statsmodels

_FAMILIES = {
    "gaussian": "gaussian",
    "binomial": "binomial",
    "poisson": "poisson",
    "negativebinomial": "negative binomial",
    "gamma": "gamma",
    "inversegaussian": "inverse gaussian",
    "tweedie": "tweedie",
}
_LINKS = {
    "identity": "identity",
    "logit": "logit",
    "probit": "probit",
    "log": "log",
    "inversepower": "inverse",
    "inversesquared": "inverse squared",
    "cloglog": "cloglog",
    "loglog": "loglog",
    "sqrt": "sqrt",
    "cauchy": "cauchy",
    "power": "power",
    "nbinom": "nbinom",
}
_LINEAR = {"OLS", "WLS", "GLS", "GLSAR", "RecursiveLS"}
_DISCRETE = {
    "Logit": ("binomial", "logit"),
    "Probit": ("binomial", "probit"),
    "Poisson": ("poisson", "log"),
    "NegativeBinomial": ("negative binomial", "log"),
    "NegativeBinomialP": ("negative binomial", "log"),
    "GeneralizedPoisson": ("generalized poisson", "log"),
}


def _exponentiate(family: str | None, link: str | None) -> str | None:
    if link == "logit":
        return "Odds ratio"
    if link == "log" and family in ("poisson", "negative binomial", "generalized poisson"):
        return "Rate ratio"
    return None


def _description(model_type: str, **fields: Any) -> dict[str, Any]:
    family, link = fields.get("family"), fields.get("link")
    exponentiate = fields.pop("exponentiate", None)
    if exponentiate is None:
        exponentiate = _exponentiate(family, link)
    return (
        {"type": model_type}
        | {k: v for k, v in fields.items() if v is not None}
        | ({"exponentiate": exponentiate} if exponentiate else {})
    )


def _statsmodels_description(result: Any) -> dict[str, Any]:
    model = result.model
    name = type(model).__name__
    response = getattr(model, "endog_names", None)
    response = response if isinstance(response, str) else None
    if name in _LINEAR:
        return _description("linear", family="gaussian", link="identity", response=response)
    if name == "GLM":
        family = _FAMILIES.get(type(model.family).__name__.lower(), type(model.family).__name__)
        link_name = type(model.family.link).__name__.lower()
        return _description(
            "glm", family=family.lower(), link=_LINKS.get(link_name, link_name), response=response
        )
    if name in _DISCRETE:
        family, link = _DISCRETE[name]
        return _description("glm", family=family, link=link, response=response)
    if name == "MixedLM":
        return _description("mixed", family="gaussian", link="identity", response=response)
    if name == "QuantReg":
        return _description("quantile", response=response)
    if name == "MNLogit":
        return _description("multinomial", family="multinomial", link="logit", response=response)
    if name == "OrderedModel":
        distr = getattr(model, "distr", None)
        links = {"logistic": "logit", "norm": "probit"}
        return _description(
            "ordinal", link=links.get(getattr(distr, "name", "")), response=response
        )
    if name == "PHReg":
        return _description("survival", response=response, exponentiate="Hazard ratio")
    return _description("other", response=response)


_INTERCEPTS = {"Intercept": "(Intercept)", "const": "(Intercept)"}


def _values(value: Any) -> list[Any]:
    return list(np.asarray(value).ravel(order="F"))


def _coefficients(result: Any, description: dict[str, Any]) -> pd.DataFrame:
    params = result.params
    model = result.model
    if getattr(params, "ndim", 1) == 2:
        # Multi-equation models: one row per equation and term, column by column.
        frame = pd.DataFrame(params)
        labels = getattr(model, "_ynames_map", None) or {}
        names = [
            f"{labels.get(j + 1, frame.columns[j])}: {_INTERCEPTS.get(str(term), term)}"
            for j in range(frame.shape[1])
            for term in frame.index
        ]
    else:
        names = [str(n) for n in getattr(params, "index", getattr(model, "exog_names", []))]
        if len(names) != len(params):
            names = [f"term_{i + 1}" for i in range(len(params))]
    confidence = np.asarray(result.conf_int())
    if confidence.ndim == 3:
        confidence = confidence.reshape(-1, 2)
    frame = pd.DataFrame(
        {
            "term": names,
            "estimate": _values(params),
            "std_error": _values(result.bse),
            "statistic": _values(result.tvalues),
            "p_value": _values(result.pvalues),
            "conf_low": confidence[:, 0] if len(confidence) == len(names) else np.nan,
            "conf_high": confidence[:, 1] if len(confidence) == len(names) else np.nan,
        }
    )
    frame["term"] = frame["term"].replace(_INTERCEPTS)
    if type(model).__name__ == "MixedLM":
        frame = frame.iloc[: model.k_fe]
    if description.get("family") == "negative binomial" and type(model).__name__ != "GLM":
        frame = frame[frame["term"] != "alpha"]
    return frame.reset_index(drop=True)


def _random_effects(result: Any) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    model = result.model
    cov = pd.DataFrame(result.cov_re)
    names = [str(n) for n in cov.columns]
    exog_re = np.asarray(getattr(model, "exog_re", np.empty((0, 0))))
    intercept = bool(exog_re.size) and bool(np.all(exog_re[:, 0] == 1))
    group = names[0] if intercept and names else "Group"
    matrix = cov.to_numpy(dtype="float64")
    variances = np.diag(matrix)
    rows = []
    for i, name in enumerate(names):
        correlation = None
        if i > 0 and variances[0] > 0 and variances[i] > 0:
            correlation = matrix[0, i] / math.sqrt(variances[0] * variances[i])
        rows.append(
            {
                "group": group,
                "term": "(Intercept)" if i == 0 and intercept else name,
                "variance": variances[i],
                "std_dev": math.sqrt(variances[i]) if variances[i] >= 0 else np.nan,
                "correlation": correlation,
            }
        )
    vc_names = list(getattr(getattr(model, "exog_vc", None), "names", []) or [])
    for name, variance in zip(vc_names, np.ravel(getattr(result, "vcomp", [])), strict=False):
        rows.append(
            {
                "group": str(name),
                "term": "(Intercept)",
                "variance": variance,
                "std_dev": math.sqrt(variance) if variance >= 0 else np.nan,
                "correlation": None,
            }
        )
    scale = float(result.scale)
    rows.append(
        {
            "group": "Residual",
            "term": "",
            "variance": scale,
            "std_dev": math.sqrt(scale),
            "correlation": None,
        }
    )
    frame = pd.DataFrame(rows)
    if frame["correlation"].isna().all():
        frame = frame.drop(columns="correlation")
    return frame, _metrics([(f"n_groups.{group}", getattr(model, "n_groups", None))])


_STATSMODELS_METRICS = (
    "nobs",
    "df_model",
    "df_resid",
    "rsquared",
    "rsquared_adj",
    "aic",
    "bic",
    "llf",
    "llnull",
    "deviance",
    "null_deviance",
    "scale",
    "converged",
)


def _statsmodels_document(result: Any) -> dict[str, Any]:
    if not all(hasattr(result, key) for key in ("params", "bse", "pvalues", "conf_int")):
        raise FrameworkError("This statsmodels result does not expose coefficient statistics.")
    model = result.model
    name = type(model).__name__
    description = _statsmodels_description(result)
    tables = [table(_coefficients(result, description), "Coefficients", role="coefficients")]
    metrics = _metrics(
        (key, getattr(result, key)) for key in _STATSMODELS_METRICS if hasattr(result, key)
    )
    if name != "MixedLM" and hasattr(result, "prsquared"):
        metrics += _metrics([("pseudo_rsquared", result.prsquared)])
    if description.get("family") == "negative binomial":
        alpha = (
            getattr(model.family, "alpha", None)
            if name == "GLM"
            else dict(zip(result.params.index, result.params, strict=False)).get("alpha")
        )
        if alpha:
            metrics += _metrics([("alpha", alpha), ("theta", 1 / float(alpha))])
    if name == "MixedLM":
        random, group_metrics = _random_effects(result)
        tables.append(table(random, "Random effects", role="random_effects"))
        metrics += group_metrics
    if name == "QuantReg":
        metrics += _metrics([("quantile", getattr(result, "q", None))])
    use_t = bool(getattr(result, "use_t", False))
    metrics += [
        {"name": "confidence_level", "value": CONFIDENCE_LEVEL},
        {"name": "interval_method", "value": "wald-t" if use_t else "wald-normal"},
    ]
    document = _document(result, "model") | {
        "model": description,
        "tables": tables,
        "metrics": metrics,
    }
    formula = getattr(model, "formula", None)
    if isinstance(formula, str):
        document["formula"] = formula
    return document


# --------------------------------------------------------------------------- machine learning


def _feature_names(model: Any, count: int) -> list[str]:
    names = getattr(model, "feature_names_in_", None)
    if names is not None and len(names) == count:
        return [str(n) for n in names]
    return [f"x{i}" for i in range(count)]


def _params(model: Any, keys: Sequence[str]) -> list[tuple[str, Any]]:
    getter = getattr(model, "get_params", None)
    params = getter(deep=False) if callable(getter) else {}
    return [(key, _hyperparameter(params.get(key))) for key in keys]


def _hyperparameter(value: Any) -> Any:
    """Drop placeholders such as "deprecated" and -1 for "no limit"; trim float32 noise."""
    if isinstance(value, str) and value in ("deprecated", "warn"):
        return None
    if isinstance(value, (int, float, np.number)) and not isinstance(value, bool):
        if value < 0:
            return None
        if isinstance(value, (float, np.floating)):
            return float(f"{float(value):.7g}")
    return value


def _importance_document(
    source: Any, features: Sequence[str], importance: Sequence[float], importance_type: str
) -> dict[str, Any]:
    frame = pd.DataFrame({"feature": list(features), "importance": list(importance)})
    frame = frame.sort_values("importance", ascending=False, kind="stable").reset_index(drop=True)
    document = _document(source, "model") | {
        "model": _description("tree_ensemble"),
        "tables": [table(frame, "Feature importance", role="importance")],
    }
    document["metrics"] = _metrics([("importance_type", importance_type)])
    return document


def _sklearn_document(model: Any) -> dict[str, Any]:
    name = type(model).__name__
    if name.startswith("HistGradientBoosting"):
        raise FrameworkError(
            f"{name} has no feature_importances_. Compute permutation importance with "
            "sklearn.inspection.permutation_importance() and publish the result as a DataFrame."
        )
    if hasattr(model, "coef_"):
        coef = np.atleast_2d(np.asarray(model.coef_, dtype="float64"))
        intercept = np.atleast_1d(np.asarray(getattr(model, "intercept_", 0.0), dtype="float64"))
        features = _feature_names(model, coef.shape[1])
        fit_intercept = bool(getattr(model, "fit_intercept", True))
        classes = list(getattr(model, "classes_", []))
        if len(coef) == 1:
            labels = [""]
        elif len(classes) == len(coef):
            labels = [f"{c}: " for c in classes]
        else:
            labels = [f"y{i}: " for i in range(len(coef))]
        terms, estimates = [], []
        for row, label in enumerate(labels):
            if fit_intercept:
                terms.append(f"{label}(Intercept)")
                estimates.append(float(intercept[row] if len(intercept) > row else intercept[0]))
            terms += [f"{label}{feature}" for feature in features]
            estimates += list(coef[row])
        logistic = name == "LogisticRegression"
        binary = logistic and len(coef) == 1
        description = _description(
            "linear_ml",
            family="binomial" if binary else "multinomial" if logistic else None,
            link="logit" if logistic else None,
            # Softmax coefficients have no reference class, so they aren't odds ratios.
            exponentiate=None if binary else False,
        )
        frame = pd.DataFrame({"term": terms, "estimate": estimates})
        metrics = _metrics(
            [
                *_params(model, ("alpha", "l1_ratio", "C", "penalty", "fit_intercept")),
                ("n_features", getattr(model, "n_features_in_", None)),
                ("n_classes", len(classes) if classes else None),
            ]
        )
        return _document(model, "model") | {
            "model": description,
            "tables": [table(frame, "Coefficients", role="coefficients")],
            "metrics": metrics,
        }
    if hasattr(model, "feature_importances_"):
        importance = np.asarray(model.feature_importances_, dtype="float64")
        document = _importance_document(
            model, _feature_names(model, len(importance)), list(importance), "impurity"
        )
        document["metrics"] += _metrics(
            [
                *_params(model, ("n_estimators", "max_depth", "learning_rate", "min_samples_leaf")),
                ("n_features", getattr(model, "n_features_in_", None)),
            ]
        )
        return document
    raise FrameworkError(
        f"publish() accepts fitted scikit-learn models with coef_ or feature_importances_; "
        f"{name} has neither (is it fitted?)."
    )


def _xgboost_document(source: Any) -> dict[str, Any]:
    booster = source.get_booster() if hasattr(source, "get_booster") else source
    scores = booster.get_score(importance_type="gain")
    features = list(booster.feature_names or []) or [f"f{i}" for i in range(booster.num_features())]
    document = _importance_document(
        source, features, [float(scores.get(f, 0.0)) for f in features], "gain"
    )
    try:
        config = json.loads(booster.save_config())
        learner = config["learner"]
        tree = learner["gradient_booster"].get("tree_train_param", {})
        objective = learner["objective"]["name"]
    except (KeyError, TypeError, ValueError):
        tree, objective = {}, None
    params = dict(_params(source, ("n_estimators", "max_depth", "learning_rate")))
    document["metrics"] += _metrics(
        [
            ("n_estimators", params.get("n_estimators") or booster.num_boosted_rounds()),
            ("max_depth", params.get("max_depth") or _number(tree.get("max_depth"))),
            ("learning_rate", params.get("learning_rate") or _number(tree.get("eta"))),
            ("n_features", booster.num_features()),
            ("objective", objective),
        ]
    )
    return document


def _number(value: Any) -> float | int | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return _hyperparameter(int(number) if number.is_integer() else number)  # type: ignore[no-any-return]


def _lightgbm_document(source: Any) -> dict[str, Any]:
    wrapper = hasattr(source, "booster_")
    booster = source.booster_ if wrapper else source
    importance = [float(v) for v in booster.feature_importance(importance_type="gain")]
    features = _feature_names(source, len(importance)) if wrapper else list(booster.feature_name())
    document = _importance_document(source, features, importance, "gain")
    if wrapper:
        params = _params(
            source,
            ("n_estimators", "max_depth", "learning_rate", "num_leaves", "min_child_samples"),
        )
        objective = source.get_params().get("objective") or getattr(source, "objective_", None)
    else:
        params = [
            (key, _hyperparameter(booster.params.get(key)))
            for key in ("max_depth", "learning_rate", "num_leaves", "min_child_samples")
        ]
        params.insert(0, ("n_estimators", booster.current_iteration()))
        objective = booster.params.get("objective")
    document["metrics"] += _metrics(
        [*params, ("n_features", booster.num_feature()), ("objective", objective)]
    )
    return document


# --------------------------------------------------------------------------- entry points


def structured(source: Any) -> dict[str, Any] | None:
    """Return a typed document for a supported object, or None.

    Model documents carry aggregates only: no observations, residuals, fitted
    values, or serialized model state.
    """
    document = _structured(source)
    if document is None:
        return None
    # Readable key order: what it is, then the description, then tables and metrics.
    order = ("schema", "kind", "source", "formula", "model", "test", "tables", "metrics")
    return {key: document[key] for key in order if key in document} | document


def _structured(source: Any) -> dict[str, Any] | None:
    frame = as_frame(source)
    if frame is not None:
        return _table_document(source, frame)
    if _from(source, "statsmodels") and hasattr(source, "params"):
        return _statsmodels_document(source)
    if _has_class(source, "xgboost", "XGBModel", "Booster"):
        return _xgboost_document(source)
    if _has_class(source, "lightgbm", "LGBMModel", "Booster"):
        return _lightgbm_document(source)
    if _from(source, "sklearn"):
        return _sklearn_document(source)
    if _is_test(source):
        return _test_document(source)
    if _from(source, "statsmodels"):
        raise FrameworkError("This statsmodels result does not expose coefficient statistics.")
    return None


def _figure(source: Any) -> Any:
    """Return the matplotlib Figure behind an Axes, Figure, or seaborn grid."""
    figure = source
    for _ in range(3):
        if callable(getattr(figure, "savefig", None)):
            return figure
        figure = getattr(figure, "figure", None) or getattr(figure, "fig", None)
    return None


def is_figure(source: Any) -> bool:
    return _from(source, "matplotlib", "seaborn") and _figure(source) is not None


def save_figure(source: Any, directory: Path, svg: bool = True) -> Path:
    """Save a matplotlib-backed figure as SVG, or PNG when SVG is too large."""
    figure = _figure(source)
    options: dict[str, Any] = {"bbox_inches": "tight", "facecolor": "white"}
    if svg:
        path = directory / "figure.svg"
        figure.savefig(path, format="svg", **options)
        if path.stat().st_size <= SVG_LIMIT:
            return path
        path.unlink()
    path = directory / "figure.png"
    figure.savefig(path, format="png", dpi=300 if svg else 150, **options)
    return path


def _plotly_html(source: Any, directory: Path) -> Path:
    path = directory / "figure.html"
    source.write_html(path, include_plotlyjs="cdn", full_html=True)
    return path


def _altair_spec(source: Any, directory: Path) -> Path:
    import altair as alt  # type: ignore[import-not-found,unused-ignore]

    # The default transformer inlines data; lift its row limit for this call only.
    with alt.data_transformers.enable("default", max_rows=None):
        spec = source.to_dict()
    path = directory / "figure.vl.json"
    path.write_text(json.dumps(spec, allow_nan=False, default=str), encoding="utf-8")
    return path


def _styler_html(source: Any, directory: Path) -> Path:
    path = directory / "table.html"
    path.write_text(source.to_html(doctype_html=True), encoding="utf-8")
    return path


def rendered(source: Any, directory: Path, cloud: bool) -> Path | None:
    """Write figures, formatted tables, and charts to their file formats, or return None."""
    exporters: list[tuple[Callable[[Any], bool], Callable[[Any, Path], Path]]] = [
        (is_figure, lambda s, d: save_figure(s, d, svg=cloud)),
        (lambda s: _has_class(s, "pandas", "Styler"), _styler_html),
        (lambda s: _has_class(s, "plotly", "BaseFigure"), _plotly_html),
        (lambda s: _has_class(s, "altair", "TopLevelMixin"), _altair_spec),
    ]
    for matches, export in exporters:
        if matches(source):
            return export(source, directory)
    return None
