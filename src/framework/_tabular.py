"""Reading delimited text with R's rules.

R's ``data_read()`` reads delimited text with readr. pandas treats more strings as missing
("None", "N/A", "null", and others) and keeps the spaces around values, so the same file
gave different data in the two languages. This reader applies readr's defaults:

- Missing values: only ``""`` and ``"NA"``. ``na=`` replaces the list, as it does in R.
- Whitespace: leading and trailing whitespace is trimmed from every text value (and the
  header), as readr's ``trim_ws = TRUE`` does. A value that becomes a missing-value string
  after trimming is missing, and a text column whose values are all numbers after trimming
  becomes numeric.
"""

from __future__ import annotations

import contextlib
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pandas as pd

from framework.errors import FrameworkError


def _na_tokens(na: str | Sequence[str] | None, default: list[str]) -> list[str]:
    if na is None:
        return default
    if isinstance(na, str):
        return [na]
    return [str(token) for token in na]


def _na_arguments(
    na: str | Sequence[str] | None, default: list[str], kwargs: dict[str, Any]
) -> list[str] | None:
    """Set pandas' missing-value arguments from ``na``. Returns the tokens, or None when the
    caller passed pandas' own ``na_values`` or ``keep_default_na``, which are used as given."""
    if "na_values" in kwargs or "keep_default_na" in kwargs:
        if na is not None:
            raise FrameworkError(
                "Pass missing values as na= (R's name) or as na_values= (pandas'), not both."
            )
        return None
    tokens = _na_tokens(na, default)
    kwargs["keep_default_na"] = False
    kwargs["na_values"] = tokens
    if not tokens:
        kwargs["na_filter"] = False
    return tokens


def _is_text(series: pd.Series) -> bool:
    return pd.api.types.is_string_dtype(series) or series.dtype == object


def _strip(value: Any) -> Any:
    return value.strip() if isinstance(value, str) else value


def _trim(
    frame: pd.DataFrame, tokens: list[str] | None, fixed: set[Any], infer: set[Any] | None
) -> pd.DataFrame:
    """Trim whitespace from text values, then treat values that became missing-value strings
    as missing.

    Columns in ``fixed`` are left alone. A changed column in ``infer`` (every column when it
    is None) whose values are now all numbers becomes numeric, as readr guesses the type
    after trimming.
    """
    frame.columns = pd.Index([_strip(name) for name in frame.columns])
    for name in frame.columns:
        column = frame[name]
        if name in fixed or not _is_text(column):
            continue
        trimmed = column.map(_strip, na_action="ignore")
        if tokens:
            trimmed = trimmed.mask(trimmed.isin(tokens))
        if trimmed.equals(column):
            continue
        if (infer is None or name in infer) and trimmed.notna().any():
            with contextlib.suppress(ValueError, TypeError):
                trimmed = pd.to_numeric(trimmed)
        frame[name] = trimmed
    return frame


def read_delimited(
    path: Path,
    sep: str,
    *,
    na: str | Sequence[str] | None = None,
    trim_ws: bool | None = None,
    kwargs: dict[str, Any],
) -> pd.DataFrame:
    """A CSV or TSV file, read with readr's rules (see the module docstring)."""
    tokens = _na_arguments(na, ["", "NA"], kwargs)
    trim = trim_ws is not False
    if trim:
        kwargs.setdefault("skipinitialspace", True)
    # Columns the caller typed with pandas' own arguments keep those types: converted
    # columns are left alone, and text columns are trimmed but not retyped
    dtype = kwargs.get("dtype")
    converted: set[Any] = set(kwargs.get("converters") or {})
    one_type = dtype is not None and not isinstance(dtype, dict)
    typed: set[Any] = set(dtype) if isinstance(dtype, dict) else set()

    frame: pd.DataFrame = pd.read_csv(path, sep=sep, **kwargs)
    if trim:
        guessed = None
        if one_type:
            guessed = set()
        elif typed:
            guessed = {name for name in frame.columns if name not in typed}
        frame = _trim(frame, tokens, converted, guessed)
    return frame
