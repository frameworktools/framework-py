"""Checking a spec against ``spec.v1.json`` before it is written.

Framework Cloud validates every spec it receives against the same schema and refuses
one with keys the schema doesn't allow (HTTP 422). ``create``, ``adopt()``, and
``upgrade()`` write specs; each goes through ``ready()``, which drops keys the schema
doesn't allow and stops on any other problem, so no spec framework-py writes can be
refused. R: ``R/spec_schema.R``.
"""

from __future__ import annotations

import json
from functools import cache
from importlib import resources
from typing import Any

from framework._log import inform
from framework.spec.merge import SpecError


@cache
def schema() -> dict[str, Any]:
    """The parsed ``spec.v1.json``."""
    text = resources.files("framework").joinpath("_assets/schema/spec.v1.json").read_text("utf-8")
    parsed: dict[str, Any] = json.loads(text)
    return parsed


def _as_json(spec: Any) -> Any:
    # As JSON sees it: written the way .framework/spec.json is, and read back
    return json.loads(json.dumps(spec, default=str))


def _resolve(node: Any) -> Any:
    root = schema()
    while isinstance(node, dict) and "$ref" in node:
        target: Any = root
        for part in str(node["$ref"]).removeprefix("#/").split("/"):
            target = target[part]
        node = target
    return node


def problems(spec: Any) -> list[str]:
    """The spec's problems against spec.v1, as ``"path: what is wrong"``; empty when valid."""
    import jsonschema

    validator = jsonschema.Draft7Validator(schema())
    out = []
    for error in sorted(validator.iter_errors(_as_json(spec)), key=lambda e: list(e.path)):
        where = ".".join(str(p) for p in error.absolute_path) or "(spec)"
        out.append(f"{where}: {error.message}")
    return out


def prune(spec: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """The spec without keys spec.v1 doesn't allow, where the schema says exactly which
    keys an object may hold, and the dot paths dropped. Inside ``anyOf`` and ``oneOf``
    nothing is removed: which form applies is for the check to say."""
    dropped: list[str] = []

    def walk(value: Any, node: Any, path: str) -> Any:
        node = _resolve(node)
        if not isinstance(node, dict):
            return value
        if isinstance(value, dict):
            props = node.get("properties") or {}
            additional = node.get("additionalProperties")
            out = {}
            for key, item in value.items():
                where = f"{path}.{key}" if path else str(key)
                if key in props:
                    out[key] = walk(item, props[key], where)
                elif additional is False:
                    dropped.append(where)
                elif isinstance(additional, dict):
                    out[key] = walk(item, additional, where)
                else:
                    out[key] = item
            return out
        if isinstance(value, list) and isinstance(node.get("items"), dict):
            return [walk(item, node["items"], f"{path}[{i}]") for i, item in enumerate(value)]
        return value

    return walk(_as_json(spec), schema(), ""), dropped


def ready(spec: dict[str, Any], quiet: bool = False) -> dict[str, Any]:
    """The spec ready to write: keys spec.v1 doesn't allow are dropped (with a message
    naming them), and any other problem raises ``SpecError`` with all of them listed."""
    pruned, dropped = prune(spec)
    if dropped and not quiet:
        inform(
            "  Left out of .framework/spec.json (not part of spec.v1, so Framework Cloud "
            f"would refuse them): {', '.join(dropped)}"
        )
    found = problems(pruned)
    if found:
        raise SpecError(".framework/spec.json would not match spec.v1: " + "; ".join(found))
    return pruned
