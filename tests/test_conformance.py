"""The shared merge conformance cases from the R repository."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from framework.spec.merge import SpecError, merge_layers
from tests.conftest import SHARED

CASES = sorted((SHARED / "conformance" / "merge").glob("*.json"))


def normalize(value: Any) -> Any:
    """Compare as the conformance README says: {} equals []."""
    if isinstance(value, dict):
        return {key: normalize(item) for key, item in value.items()} or []
    if isinstance(value, list):
        return [normalize(item) for item in value]
    return value


@pytest.mark.parametrize("case", CASES, ids=[case.stem for case in CASES])
def test_merge_matches_r(case: Path) -> None:
    data = json.loads(case.read_text(encoding="utf-8"))
    result = merge_layers(data["layers"])
    expected = data["expected"]
    assert normalize(result.spec) == normalize(expected["spec"])
    assert [
        {"label": block.label, "text": block.text} for block in result.gitignore_blocks
    ] == expected["gitignore_blocks"]
    assert {(c.path, c.layers) for c in result.conflicts} == {
        (c["path"], c["layers"]) for c in expected["conflicts"]
    }


def test_lists_are_rejected_where_objects_are_needed() -> None:
    with pytest.raises(SpecError, match="extra_directories must be an object"):
        merge_layers([{"extra_directories": [{"path": "x"}]}])
