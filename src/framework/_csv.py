"""Writing CSV files in the form R's readr writes them.

R's ``data_save()`` writes CSV with ``readr::write_delim()``. pandas writes whole-number
doubles as ``1.0``, booleans as ``True``, and missing values as empty fields, so the same
data gave different files, and different hashes, in the two languages. ``write_csv()``
writes what readr writes:

- Doubles in readr's shortest form: ``1`` and ``0.5``, ``1e15`` and ``1.5e-4`` past the
  limits where readr switches to an exponent, ``Inf``, and ``NA`` for NaN.
- Logicals as ``TRUE`` and ``FALSE``.
- Missing values as ``NA``.
- Date-times in UTC as ``2020-01-02T03:04:05Z``, and dates as ``2020-01-02``. readr drops
  fractional seconds; this writer keeps them (``03:04:05.5Z``), which readr reads back.
- Fields quoted only when needed, and ``\\n`` line endings on every platform.

Other writers that should match R, such as ``save_table()``, can call ``write_csv()``.
"""

from __future__ import annotations

import datetime as dt
import math
from decimal import Decimal
from pathlib import Path
from typing import Any

import pandas as pd

NA = "NA"


def format_double(value: float) -> str:
    """One double as readr writes it."""
    if math.isnan(value):
        return NA
    if math.isinf(value):
        return "Inf" if value > 0 else "-Inf"
    if value == 0:
        return "-0" if math.copysign(1.0, value) < 0 else "0"
    # repr() gives the shortest digits that read back as the same double, as readr's
    # writer does. The value is digits * 10**exponent.
    sign, digit_tuple, exponent = Decimal(repr(float(value))).as_tuple()
    assert isinstance(exponent, int)
    digits = "".join(str(d) for d in digit_tuple)
    stripped = digits.rstrip("0")
    exponent += len(digits) - len(stripped)
    digits = stripped
    point = len(digits) + exponent  # where the decimal point falls
    prefix = "-" if sign else ""
    if exponent >= 0:
        if exponent == 0 or point <= 15:
            return prefix + digits + "0" * exponent
        return f"{prefix}{digits}e{exponent}"
    if point > 0:
        return f"{prefix}{digits[:point]}.{digits[point:]}"
    if point > -3:
        return f"{prefix}0.{'0' * -point}{digits}"
    mantissa = digits[0] + (f".{digits[1:]}" if len(digits) > 1 else "")
    return f"{prefix}{mantissa}e{point - 1}"


def _format_datetime(value: pd.Timestamp | dt.datetime) -> str:
    stamp = pd.Timestamp(value)
    if stamp.tzinfo is not None:
        stamp = stamp.tz_convert("UTC")
    text = stamp.strftime("%Y-%m-%dT%H:%M:%S")
    fraction = stamp.microsecond * 1000 + stamp.nanosecond
    if fraction:
        text += f".{fraction:09d}".rstrip("0")
    return text + "Z"


def _format_value(value: Any) -> str:
    if value is None or value is pd.NA or value is pd.NaT:
        return NA
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, float):
        return format_double(value)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, pd.Timestamp | dt.datetime):
        return _format_datetime(value)
    if isinstance(value, dt.date):
        return value.isoformat()
    try:
        if pd.isna(value):
            return NA
    except (TypeError, ValueError):
        pass
    if hasattr(value, "item"):
        # numpy scalars: format the Python value
        return _format_value(value.item())
    return str(value)


def _format_column(column: pd.Series) -> pd.Series:
    kind = column.dtype
    if pd.api.types.is_bool_dtype(kind):
        values = column.map(lambda v: NA if pd.isna(v) else ("TRUE" if v else "FALSE"))
    elif pd.api.types.is_integer_dtype(kind):
        values = column.map(lambda v: NA if pd.isna(v) else str(int(v)))
    elif pd.api.types.is_float_dtype(kind):
        values = column.map(lambda v: NA if pd.isna(v) else format_double(float(v)))
    elif pd.api.types.is_datetime64_any_dtype(kind):
        values = column.map(lambda v: NA if pd.isna(v) else _format_datetime(v))
    elif isinstance(kind, pd.CategoricalDtype):
        values = column.astype(object).map(_format_value)
    else:
        values = column.map(_format_value)
    return values.astype(object)


def format_like_r(frame: pd.DataFrame) -> pd.DataFrame:
    """``frame`` with every value as the text readr would write for it."""
    if frame.columns.empty:
        return frame.copy()
    formatted = pd.concat(
        [_format_column(frame.iloc[:, position]) for position in range(frame.shape[1])], axis=1
    )
    formatted.columns = frame.columns
    return formatted


def write_csv(frame: pd.DataFrame, path: str | Path, sep: str = ",") -> None:
    """Write ``frame`` without its index, as R's ``readr::write_delim()`` would."""
    format_like_r(frame).to_csv(
        path, sep=sep, index=False, lineterminator="\n", na_rep=NA, encoding="utf-8"
    )
