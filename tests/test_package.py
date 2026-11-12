"""Package-level checks: shared assets, the parity manifest, and small helpers."""

from __future__ import annotations

import json
import re
from importlib import resources

import jsonschema

import framework as fw
from scripts import check_parity
from tests.conftest import SHARED


def test_now_matches_r_format() -> None:
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d[+-]\d{4}", fw.now())


def test_shipped_schemas_are_valid_json_schemas() -> None:
    folder = resources.files("framework").joinpath("_assets/schema")
    names = sorted(entry.name for entry in folder.iterdir())
    assert names == ["docs-reference.v1.json", "layer.v1.json", "layers.v1.json", "spec.v1.json"]
    for name in names:
        schema = json.loads(folder.joinpath(name).read_text(encoding="utf-8"))
        jsonschema.validators.validator_for(schema).check_schema(schema)


def test_spec_schema_knows_python() -> None:
    schema = json.loads(
        resources.files("framework").joinpath("_assets/schema/spec.v1.json").read_text("utf-8")
    )
    assert "python" in schema["properties"]["language"]["enum"]


def test_conformance_fixtures_are_present() -> None:
    cases = sorted((SHARED / "conformance" / "merge").glob("*.json"))
    assert cases
    for case in cases:
        data = json.loads(case.read_text(encoding="utf-8"))
        assert {"layers", "expected"} <= set(data)


def test_parity_manifest_is_consistent() -> None:
    assert check_parity.check(strict=False) == 0


def test_every_r_export_is_mapped() -> None:
    assert len(check_parity.r_exports()) == 140
