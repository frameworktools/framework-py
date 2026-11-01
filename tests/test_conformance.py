"""The shared conformance cases from the R repository: merge and create."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from framework.spec.merge import SpecError, merge_layers
from scripts.make_create_fixtures import created_paths
from tests.conftest import SHARED

CASES = sorted((SHARED / "conformance" / "merge").glob("*.json"))
CREATE = sorted((SHARED / "conformance" / "create" / "python").glob("*.json"))


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


@pytest.mark.parametrize("case", CREATE, ids=[case.stem for case in CREATE])
def test_create_writes_the_pinned_paths(case: Path) -> None:
    data = json.loads(case.read_text(encoding="utf-8"))
    assert created_paths(data["spec"]) == data["expected"]["paths"]


def test_create_cases_are_pinned() -> None:
    assert len(CREATE) == 15


ADOPT = sorted((SHARED / "conformance" / "adopt").glob("*.json"))


@pytest.mark.parametrize("case", ADOPT, ids=[case.stem for case in ADOPT])
def test_adopt_maps_folders_like_r(case: Path) -> None:
    from framework.adopt import adopt_plan

    data = json.loads(case.read_text(encoding="utf-8"))
    plan = adopt_plan(data["folders"], data.get("language", "r"))
    expected = data["expected"]
    assert plan.directories == expected["directories"]
    assert plan.extra_directories == expected["extra_directories"]
    assert [{"code": "private_path", "path": p} for p in plan.private] == expected["warnings"]
