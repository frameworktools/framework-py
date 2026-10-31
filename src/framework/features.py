"""Framework's tools and its bundled skill and package sets.

The definitions are canonical in the R package (``inst/features/`` and
``inst/sets/``) and copied here by ``scripts/sync_assets.py``. Each holds a
layer in the spec layer format, so an offline ``new()`` and framework.pub build
the same projects. A tool's ``layer`` applies first, then its layer for the
project's language.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from functools import cache
from importlib import resources
from typing import Any

import pandas as pd

from framework._log import inform
from framework.errors import FrameworkError

LANGUAGE = "python"


@cache
def _definitions(folder: str) -> dict[str, dict[str, Any]]:
    root = resources.files("framework").joinpath(f"_assets/{folder}")
    found: dict[str, dict[str, Any]] = {}
    for entry in sorted(root.iterdir(), key=lambda e: e.name):
        if entry.name.endswith(".json"):
            definition = json.loads(entry.read_text(encoding="utf-8"))
            found[definition["id"]] = definition
    return found


def _tools() -> dict[str, dict[str, Any]]:
    return _definitions("features")


def _sets(kind: str) -> dict[str, dict[str, Any]]:
    return _definitions(f"sets/{kind}")


def _for_python(definition: Mapping[str, Any]) -> bool:
    return LANGUAGE in (definition.get("language") or [])


def tools_available() -> pd.DataFrame:
    """Framework's tools for Python projects.

    Returns:
        One row per tool with ``id``, ``name``, ``description``, and ``requires`` (a list of
        tool ids).
    """
    rows = [
        {
            "id": d["id"],
            "name": d["name"],
            "description": d["description"],
            "requires": list(d.get("requires") or []),
        }
        for d in _tools().values()
        if _for_python(d)
    ]
    return pd.DataFrame(rows, columns=["id", "name", "description", "requires"])


def resolve_tools(tools: Iterable[str], off: Iterable[str] = ()) -> list[str]:
    """The tools to turn on: those asked for plus what they require, in order.

    A required tool is added with a message, as R and framework.pub's launch
    page do.

    Raises:
        FrameworkError: For an unknown tool, a tool that isn't for Python, a conflict, or a
            required tool that was turned off.
    """
    definitions = _tools()
    asked = list(dict.fromkeys(tools))
    unknown = [t for t in asked if t not in definitions]
    if unknown:
        available = ", ".join(t for t, d in definitions.items() if _for_python(d))
        raise FrameworkError(f"Unknown tool(s): {', '.join(unknown)}. Available: {available}")
    turned_off = set(off)
    wanted: list[str] = []

    def add(tool: str, needed_by: str | None) -> None:
        if tool in wanted:
            return
        if needed_by is not None:
            if tool in turned_off:
                raise FrameworkError(f"The {needed_by} tool needs {tool}, which is turned off.")
            if tool not in asked:
                inform(f"Also turning on {tool}, which {needed_by} needs")
        for required in definitions[tool].get("requires") or []:
            add(required, tool)
        wanted.append(tool)

    for tool in asked:
        add(tool, None)
    for tool in wanted:
        definition = definitions[tool]
        if not _for_python(definition):
            name = definition["name"]
            raise FrameworkError(f"The {name} tool isn't available for Python projects.")
        clash = [t for t in definition.get("conflicts") or [] if t in wanted]
        if clash:
            raise FrameworkError(f"The {tool} tool can't be used with {', '.join(clash)}.")
    return wanted


def _pick(ids: Iterable[str], kind: str, label: str) -> list[dict[str, Any]]:
    sets = _sets(kind)
    entries = []
    for handle in ids:
        # Framework's handles work offline too
        set_id = handle.removeprefix("framework/")
        definition = sets.get(set_id)
        if definition is None:
            available = ", ".join(i for i, d in sets.items() if _for_python(d))
            raise FrameworkError(f"Unknown {label}: {set_id}. Available: {available}")
        if not _for_python(definition):
            raise FrameworkError(f"The {definition['name']} {label} isn't available for Python.")
        prefix = "packages-" if kind == "packages" else "skills-"
        entries.append(
            {"kind": "facet", "key": f"framework/{prefix}{set_id}", "layer": definition["layer"]}
        )
    return entries


def definition_layers(
    tools: Mapping[str, bool] | Iterable[str] = (),
    package_sets: Iterable[str] = (),
    skill_sets: Iterable[str] = (),
) -> list[dict[str, Any]]:
    """Layer envelopes for tools and sets, in framework.pub's order.

    Tools come first, then package sets, then skill sets. ``tools`` is a list of tool ids or
    a map of tool id to true or false. Tools set to false are turned off by one last layer,
    ``framework/tools-off``, which overrides the project's defaults.
    """
    choices = dict(tools) if isinstance(tools, Mapping) else dict.fromkeys(tools, True)
    for tool, on in choices.items():
        if not isinstance(on, bool):
            raise FrameworkError(f"tools.{tool} must be true or false")
    off = [t for t, on in choices.items() if on is False]
    unknown = [t for t in off if t not in _tools()]
    if unknown:
        raise FrameworkError(f"Unknown tool(s): {', '.join(unknown)}")
    entries: list[dict[str, Any]] = []
    for tool in resolve_tools([t for t, on in choices.items() if on], off):
        definition = _tools()[tool]
        key = f"framework/tool-{tool}"
        entries.append({"kind": "facet", "key": key, "layer": definition["layer"]})
        extra = (definition.get("language_layers") or {}).get(LANGUAGE)
        if extra:
            entries.append(
                {"kind": "facet", "key": f"framework/tool-{tool}-{LANGUAGE}", "layer": extra}
            )
    entries += _pick(package_sets, "packages", "package set")
    entries += _pick(skill_sets, "skills", "skill set")
    if off:
        # After the tools and sets, as in R, so the project's defaults are overridden
        layer = {"tools": dict.fromkeys(off, False)}
        entries.append({"kind": "facet", "key": "framework/tools-off", "layer": layer})
    return entries
