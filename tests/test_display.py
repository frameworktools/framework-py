from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

import framework as fw


def test_show_table_is_a_pipe_table() -> None:
    # Ports 2026-11-07: 113 knitr::kable() calls had no Python counterpart
    frame = pd.DataFrame(
        {
            "arm": ["a", "b|c", None],
            "n": [1234567, 12, -4500],
            "rate": [0.5, float("nan"), float("inf")],
            "flag": [True, False, True],
        }
    )
    shown = fw.show_table(frame, caption="Counts  by arm", digits=2, big_mark=",", na="NA")
    assert shown._repr_markdown_() == (
        "| arm | n | rate | flag |\n"
        "|:---|---:|---:|:---|\n"
        "| a | 1,234,567 | 0.50 | True |\n"
        "| b\\|c | 12 | NA | False |\n"
        "| NA | -4,500 | Inf | True |\n"
        "\n"
        ": Counts by arm\n"
    )
    assert str(shown) == repr(shown) == shown.markdown


def test_show_table_defaults_and_other_inputs() -> None:
    frame = pd.DataFrame({"x": [1.0, 2.5, 1e-7]}, index=pd.Index(["p", "q", "r"], name="id"))
    assert fw.show_table(frame).markdown == "| x |\n|---:|\n| 1 |\n| 2.5 |\n| 1e-07 |\n"
    assert fw.show_table(frame, index=True).markdown.startswith("| id | x |\n|:---|---:|\n| p |")
    assert fw.show_table(pd.Series([1, 2], name="n")).markdown == "| n |\n|---:|\n| 1 |\n| 2 |\n"
    assert fw.show_table(np.array([[1, 2]])).markdown.count("|---:|") == 1
    with pytest.raises(fw.FrameworkError, match="DataFrame"):
        fw.show_table({"x": 1})
    with pytest.raises(fw.FrameworkError, match="digits"):
        fw.show_table(frame, digits=-1)
