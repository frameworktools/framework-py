"""``show_table()``: a table for a Quarto page or a Jupyter notebook.

The table is a Markdown pipe table, which Quarto turns into a real table in every
output format (HTML, PDF, Word), with its caption and cross-references, and which
Jupyter, VS Code, and Positron render as a table. It is the Python counterpart of
``knitr::kable()``.
"""

from __future__ import annotations

import contextlib
import math
from typing import Any

import pandas as pd
from pandas.api import types as dtypes

from framework.errors import FrameworkError


class TableDisplay:
    """A table ``show_table()`` made. Notebooks show it as a table; ``str()`` gives its
    Markdown."""

    def __init__(self, markdown: str) -> None:
        self.markdown = markdown

    def _repr_markdown_(self) -> str:
        return self.markdown

    def __str__(self) -> str:
        return self.markdown

    def __repr__(self) -> str:
        return self.markdown


def _group(digits: str, big_mark: str) -> str:
    if not big_mark or len(digits) <= 3:
        return digits
    head = len(digits) % 3 or 3
    return big_mark.join([digits[:head], *(digits[i : i + 3] for i in range(head, len(digits), 3))])


def _number(value: float | int, decimals: int | None, big_mark: str) -> str:
    if isinstance(value, float) and math.isinf(value):
        return "Inf" if value > 0 else "-Inf"
    if decimals is not None:
        text = f"{value:.{decimals}f}"
    elif isinstance(value, float) and value.is_integer():
        text = str(int(value))
    else:
        text = str(value)
    sign = "-" if text.startswith("-") else ""
    whole, _, rest = text.lstrip("-").partition(".")
    if not whole.isdigit():  # 1e-07 and other exponent forms are left as they are
        return text
    return f"{sign}{_group(whole, big_mark)}" + (f".{rest}" if rest else "")


def _cell(value: Any, decimals: int | None, big_mark: str, na: str) -> str:
    if value is None or value is pd.NA or value is pd.NaT:
        return na
    if isinstance(value, float) and math.isnan(value):
        return na
    if hasattr(value, "item") and not isinstance(value, (str, bytes)):
        with contextlib.suppress(ValueError, AttributeError):
            value = value.item()
    if isinstance(value, bool):
        text = str(value)
    elif isinstance(value, int):
        text = _number(value, None, big_mark)
    elif isinstance(value, float):
        if math.isnan(value):
            return na
        text = _number(value, decimals, big_mark)
    else:
        text = str(value)
    return " ".join(text.replace("|", "\\|").split())


def show_table(
    data: Any,
    caption: str | None = None,
    digits: int | None = None,
    big_mark: str = "",
    na: str = "",
    index: bool = False,
) -> TableDisplay:
    """Show a table in a Quarto page or Jupyter notebook, as ``knitr::kable()`` does in R.

    The result is a Markdown pipe table. Quarto makes it a real table in HTML, PDF, and
    Word, styled like the page, so it stays small in a page with ``embed-resources``.
    Jupyter, VS Code, and Positron show it as a table, and ``print()`` shows its Markdown.
    Numeric columns are right-aligned and others left-aligned. Every row is shown, so
    summarize first.

    Args:
        data: A pandas or polars DataFrame or Series, or a numpy array.
        caption: A caption below the table. In Quarto, ``#| tbl-cap`` on the cell does the
            same and adds a cross-reference label with ``#| label: tbl-...``; use one or
            the other.
        digits: Decimal places for floating-point numbers, kept even when they are zero
            (``2`` shows ``1.50``). Default: whole numbers without decimals and other
            numbers as Python prints them.
        big_mark: The thousands separator for numbers, such as ``","``. Default: none.
        na: What a missing value shows. Default: an empty cell.
        index: Show the DataFrame's index as the first column.

    Returns:
        A ``TableDisplay``, which a notebook shows as the table when it is the last line
        of a cell. Call ``IPython.display.display()`` on it to show it from elsewhere.

    Raises:
        FrameworkError: When ``data`` is not table-like, or ``digits`` is negative.
    """
    from framework._artifacts import as_frame

    frame = as_frame(data)
    if frame is None:
        raise FrameworkError("show_table() needs a DataFrame, a Series, or a numpy array.")
    if digits is not None and (isinstance(digits, bool) or digits < 0):
        raise FrameworkError("digits must be a whole number of at least 0.")
    if index:
        frame = frame.reset_index()
    header = [" ".join(str(name).replace("|", "\\|").split()) for name in frame.columns]
    numeric = [
        dtypes.is_numeric_dtype(frame[column]) and not dtypes.is_bool_dtype(frame[column])
        for column in frame.columns
    ]
    rows = [
        [_cell(value, digits, big_mark, na) for value in row]
        for row in frame.itertuples(index=False, name=None)
    ]
    lines = [
        "| " + " | ".join(header) + " |",
        "|" + "|".join("---:" if right else ":---" for right in numeric) + "|",
        *("| " + " | ".join(row) + " |" for row in rows),
    ]
    if caption:
        lines += ["", f": {' '.join(caption.split())}"]
    return TableDisplay("\n".join(lines) + "\n")
