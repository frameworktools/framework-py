"""Reading delimited text and Excel files with R's rules.

R's ``data_read()`` reads delimited text with readr and Excel files with readxl. pandas
treats more strings as missing ("None", "N/A", "null", and others) and keeps the spaces
around values, so the same file gave different data in the two languages. These readers
apply readr's and readxl's defaults:

- Missing values: only ``""`` and ``"NA"`` in text files, and only empty cells in Excel
  files. ``na=`` replaces the list, as it does in R.
- Whitespace: leading and trailing whitespace is trimmed from every text value (and the
  header), as readr's and readxl's ``trim_ws = TRUE`` does. A value that becomes a
  missing-value string after trimming is missing, and a text column whose values are all
  numbers after trimming becomes numeric.

They also take readr's and readxl's argument names (``sheet``, ``col_names``,
``col_types``, ``skip``, ``n_max``), so a call copied from R reads the same data.
"""

from __future__ import annotations

import contextlib
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pandas as pd

from framework.errors import FrameworkError

# R's arguments data_read() translates, by the file types they apply to
R_ARGUMENTS = ("na", "trim_ws", "col_names", "col_types", "skip", "n_max", "sheet")

_READR_TYPES = {
    "c": "str",
    "i": "Int64",
    "d": "float64",
    "n": "float64",
    "l": "boolean",
    "f": "category",
    "D": "date",
    "T": "datetime",
    "t": "str",
    "?": None,
    "_": "skip",
    "-": "skip",
}
_READXL_TYPES = ("skip", "guess", "logical", "numeric", "date", "text", "list")
_LOGICAL_TRUE = ("TRUE", "true", "True", "T")
_LOGICAL_FALSE = ("FALSE", "false", "False", "F")


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


def _default_names(count: int, excel: bool) -> list[str]:
    """R's names for a file read with ``col_names = FALSE``: readr's ``X1, X2, ...`` and
    readxl's ``...1, ...2, ...``."""
    return [f"...{i}" if excel else f"X{i}" for i in range(1, count + 1)]


def _column_names(
    col_names: bool | Sequence[str] | None, kwargs: dict[str, Any]
) -> bool | list[str] | None:
    """Set pandas' header arguments from ``col_names``. Returns False when R's default names
    are to be given after reading."""
    if col_names is None or col_names is True:
        return None
    if "header" in kwargs or "names" in kwargs:
        raise FrameworkError(
            "Pass column names as col_names= (R's name) or as header=/names= (pandas'), not both."
        )
    kwargs["header"] = None
    if col_names is False:
        return False
    if isinstance(col_names, str):
        raise FrameworkError("col_names must be True, False, or a list of names")
    names = [str(name) for name in col_names]
    kwargs["names"] = names
    return names


def _count_rows(skip: int | None, n_max: int | None, kwargs: dict[str, Any]) -> None:
    if skip is not None:
        if "skiprows" in kwargs:
            raise FrameworkError("Pass skip= (R's name) or skiprows= (pandas'), not both.")
        if isinstance(skip, bool) or not isinstance(skip, int) or skip < 0:
            raise FrameworkError("skip must be a whole number of rows, 0 or more")
        kwargs["skiprows"] = skip
    if n_max is not None:
        if "nrows" in kwargs:
            raise FrameworkError("Pass n_max= (R's name) or nrows= (pandas'), not both.")
        if isinstance(n_max, bool) or not isinstance(n_max, int) or n_max < 0:
            raise FrameworkError("n_max must be a whole number of rows, 0 or more")
        kwargs["nrows"] = n_max


def _readr_types(
    col_types: str | Sequence[str] | Mapping[str, str], names: list[str]
) -> dict[str, str | None]:
    """Column name -> type code, from readr's compact string ("cid_") or a mapping of
    names to one-letter codes."""
    if isinstance(col_types, str):
        codes = [code for code in col_types if code not in " "]
        if len(codes) != len(names):
            raise FrameworkError(
                f"col_types has {len(codes)} types, but the file has {len(names)} columns"
            )
        pairs = list(zip(names, codes, strict=True))
    elif isinstance(col_types, Mapping):
        unknown = [str(name) for name in col_types if name not in names]
        if unknown:
            raise FrameworkError(f"col_types names columns the file lacks: {', '.join(unknown)}")
        pairs = [(str(name), str(code)) for name, code in col_types.items()]
    else:
        raise FrameworkError(
            'col_types must be readr\'s compact string, such as "cid", or a mapping of column '
            'names to one-letter codes, such as {"id": "i"}'
        )
    types: dict[str, str | None] = {}
    for name, code in pairs:
        if code not in _READR_TYPES:
            raise FrameworkError(
                f"Unknown column type '{code}' for {name}. Use readr's codes: "
                "c, i, d, n, l, f, D, T, t, ?, or _"
            )
        types[name] = _READR_TYPES[code]
    return types


def _logical(series: pd.Series) -> pd.Series:
    def convert(value: Any) -> Any:
        if pd.isna(value):
            return pd.NA
        if isinstance(value, bool):
            return value
        if isinstance(value, int | float):
            return bool(value)
        text = str(value).strip()
        if text in _LOGICAL_TRUE:
            return True
        if text in _LOGICAL_FALSE:
            return False
        return pd.NA

    return series.map(convert).astype("boolean")


def _text_value(value: Any) -> Any:
    """A cell as readxl's text type gives it: whole numbers without ``.0``."""
    if pd.isna(value):
        return value
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _apply_type(frame: pd.DataFrame, name: str, kind: str | None) -> None:
    if kind is None:
        return
    column = frame[name]
    if kind == "str":
        frame[name] = column.map(_text_value, na_action="ignore").astype("str")
    elif kind == "Int64":
        numbers = pd.to_numeric(column, errors="coerce")
        frame[name] = numbers.where(numbers.isna() | (numbers % 1 == 0)).astype("Int64")
    elif kind == "float64":
        frame[name] = pd.to_numeric(column, errors="coerce").astype("float64")
    elif kind == "boolean":
        frame[name] = _logical(column)
    elif kind == "category":
        frame[name] = column.astype("category")
    elif kind == "date":
        frame[name] = pd.to_datetime(column, errors="coerce").dt.normalize()
    elif kind == "datetime":
        frame[name] = pd.to_datetime(column, errors="coerce")


def _header(path: Path, sep: str, kwargs: dict[str, Any]) -> list[str]:
    """The file's column names, read without the data."""
    keep = {
        key: kwargs[key]
        for key in ("skiprows", "header", "names", "encoding", "comment", "quotechar")
        if key in kwargs
    }
    return [str(name) for name in pd.read_csv(path, sep=sep, nrows=0, **keep).columns]


def read_delimited(
    path: Path,
    sep: str,
    *,
    na: str | Sequence[str] | None = None,
    trim_ws: bool | None = None,
    col_names: bool | Sequence[str] | None = None,
    col_types: str | Sequence[str] | Mapping[str, str] | None = None,
    skip: int | None = None,
    n_max: int | None = None,
    kwargs: dict[str, Any],
) -> pd.DataFrame:
    """A CSV or TSV file, read with readr's rules (see the module docstring)."""
    tokens = _na_arguments(na, ["", "NA"], kwargs)
    names = _column_names(col_names, kwargs)
    _count_rows(skip, n_max, kwargs)
    trim = trim_ws is not False
    if trim:
        kwargs.setdefault("skipinitialspace", True)
    # Columns the caller typed with pandas' own arguments keep those types: converted
    # columns are left alone, and text columns are trimmed but not retyped
    dtype = kwargs.get("dtype")
    converted: set[Any] = set(kwargs.get("converters") or {})
    one_type = dtype is not None and not isinstance(dtype, dict)
    typed: set[Any] = set(dtype) if isinstance(dtype, dict) else set()

    types: dict[str, str | None] = {}
    if col_types is not None:
        if one_type:
            raise FrameworkError("Pass col_types= (R's name) or one dtype= (pandas'), not both.")
        header = names if isinstance(names, list) else None
        if header is None:
            found = _header(path, sep, kwargs)
            if names is False:
                header = _default_names(len(found), excel=False)
            else:
                # The file's header row, named as it will be after trimming
                header = [_strip(name) for name in found] if trim else found
                kwargs["header"] = 0
            kwargs["names"] = header
        types = _readr_types(col_types, header)
        skipped = [name for name, kind in types.items() if kind == "skip"]
        if skipped:
            kwargs["usecols"] = [name for name in header if name not in skipped]
        # Typed columns are read as text and converted after trimming
        kwargs["dtype"] = {
            **(dtype or {}),
            **{name: "str" for name, kind in types.items() if kind not in (None, "skip")},
        }

    frame: pd.DataFrame = pd.read_csv(path, sep=sep, **kwargs)
    if names is False and col_types is None:
        frame.columns = pd.Index(_default_names(len(frame.columns), excel=False))
    if trim:
        typed |= {name for name, kind in types.items() if kind not in (None, "skip")}
        guessed = None
        if one_type:
            guessed = set()
        elif typed:
            guessed = {name for name in frame.columns if name not in typed}
        frame = _trim(frame, tokens, converted, guessed)
    for name, kind in types.items():
        if kind not in (None, "skip") and name in frame.columns:
            _apply_type(frame, name, kind)
    return frame


def _readxl_types(col_types: str | Sequence[str] | Mapping[str, str], count: int) -> list[str]:
    if isinstance(col_types, Mapping):
        raise FrameworkError(
            "For Excel files, col_types is one readxl type or a list with one per column"
        )
    values = [col_types] if isinstance(col_types, str) else [str(kind) for kind in col_types]
    unknown = [kind for kind in values if kind not in _READXL_TYPES]
    if unknown:
        raise FrameworkError(
            f"Unknown column type: {', '.join(unknown)}. Use readxl's types: "
            + ", ".join(_READXL_TYPES)
        )
    if len(values) == 1:
        return values * count
    if len(values) != count:
        raise FrameworkError(f"col_types has {len(values)} types, but the sheet has {count}")
    return values


_READXL_TO_KIND = {
    "logical": "boolean",
    "numeric": "float64",
    "date": "datetime",
    "text": "str",
    "guess": None,
    "list": None,
}


def read_excel(
    path: Path,
    *,
    na: str | Sequence[str] | None = None,
    trim_ws: bool | None = None,
    col_names: bool | Sequence[str] | None = None,
    col_types: str | Sequence[str] | Mapping[str, str] | None = None,
    skip: int | None = None,
    n_max: int | None = None,
    sheet: str | int | None = None,
    kwargs: dict[str, Any],
) -> pd.DataFrame:
    """An Excel sheet, read with readxl's rules (see the module docstring)."""
    tokens = _na_arguments(na, [""], kwargs)
    names = _column_names(col_names, kwargs)
    _count_rows(skip, n_max, kwargs)
    if sheet is not None:
        if "sheet_name" in kwargs:
            raise FrameworkError("Pass sheet= (R's name) or sheet_name= (pandas'), not both.")
        if isinstance(sheet, bool) or not isinstance(sheet, int | str):
            raise FrameworkError("sheet must be a sheet name or a position (1 is the first)")
        if isinstance(sheet, int) and sheet < 1:
            raise FrameworkError("sheet positions start at 1, as in R")
        # readxl counts sheets from 1, pandas from 0
        kwargs["sheet_name"] = sheet - 1 if isinstance(sheet, int) else sheet
    if col_types is not None:
        # Cells are read as Excel stored them, then converted
        kwargs["dtype"] = object

    frame = pd.read_excel(path, **kwargs)
    if not isinstance(frame, pd.DataFrame):
        raise FrameworkError("data_read() reads one sheet. Name it with sheet=.")
    if names is False:
        frame.columns = pd.Index(_default_names(len(frame.columns), excel=True))
    if trim_ws is not False:
        frame = _trim(frame, tokens, set(), infer=set())
    if col_types is not None:
        kinds = _readxl_types(col_types, len(frame.columns))
        for name, kind in zip(list(frame.columns), kinds, strict=True):
            if kind == "skip":
                frame = frame.drop(columns=name)
            elif _READXL_TO_KIND[kind] is None:
                frame[name] = frame[name].infer_objects()
            else:
                _apply_type(frame, name, _READXL_TO_KIND[kind])
    return frame
