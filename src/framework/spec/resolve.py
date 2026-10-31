"""Resolving a project spec from layers, with Python's package defaults."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from importlib import resources
from typing import Any

from framework import _yaml
from framework.spec.check import spec_check
from framework.spec.merge import (
    MAP_PATHS,
    PACKAGE_DEFAULTS,
    Conflict,
    GitignoreBlock,
    Layer,
    SpecError,
    merge_layers,
    normalize_layers,
)

TYPES = ("project", "project_sensitive", "course", "presentation", "bare")
LANGUAGES = ("r", "python", "stata")
LANGUAGE = "python"

_ORDER = (
    "spec_version",
    "name",
    "type",
    "language",
    "author",
    "directories",
    "extra_directories",
    "render_dirs",
    "packages",
    "git",
    "ai",
    "scaffold",
    "quarto",
    "connections",
    "security",
    "env",
    "data_hooks",
    "files",
    "skills",
    "source",
)
_AFFILIATION_FIELDS = ("name", "department", "city", "country")


def language_message(language: str) -> str:
    return {
        "r": "This is an R project. Create it with the framework R package or `fw new`.",
        "stata": "This is a Stata project. Create it with `fw new` (the fw command-line tool).",
    }.get(language, f"The Python package creates Python projects only (got language '{language}').")


def _template(name: str) -> str:
    return resources.files("framework").joinpath(f"templates/{name}").read_text(encoding="utf-8")


def _defaults() -> dict[str, Any]:
    data: dict[str, Any] = _yaml.load(_template("defaults.yml"))
    return data


def package_defaults(type: str) -> dict[str, Any]:
    """Python's package defaults for a project type: the bottom layer of every spec."""
    defaults = _defaults()
    type_config = defaults["types"][type]
    skills: dict[str, Any] = {}
    if type != "bare":
        names = list(defaults["skills"])
        if type == "project_sensitive":
            names += defaults["sensitive_skills"]
        skills = {name: {"package": True} for name in names}
    quarto = dict(defaults["quarto"])
    if type_config.get("render_dir") is not None:
        quarto["render_dir"] = type_config["render_dir"]
    return {
        "author": dict(defaults["author"]),
        "directories": dict(type_config["directories"]),
        "extra_directories": {},
        "render_dirs": dict(type_config["render_dirs"]),
        "packages": defaults["packages"],
        "git": defaults["git"],
        "ai": defaults["ai"],
        "scaffold": defaults["scaffold"],
        "quarto": quarto,
        "connections": defaults["connections"],
        "security": {"sensitive": type == "project_sensitive"},
        "env": defaults["env"],
        "data_hooks": {},
        "files": {},
        "skills": skills,
    }


# Authors ----------------------------------------------------------------------


def _text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def author_normalize(author: Mapping[str, Any] | None) -> dict[str, Any]:
    """Known author fields with non-empty values, as R's ``.author_normalize()`` keeps them."""
    author = author or {}
    result: dict[str, Any] = {
        "name": _text(author.get("name")),
        "email": _text(author.get("email")),
        "affiliation": _text(author.get("affiliation")),
    }
    for key in ("orcid", "url"):
        if _text(author.get(key)):
            result[key] = author[key]
    affiliations = []
    for entry in author.get("affiliations") or []:
        if isinstance(entry, str):
            entry = {"name": entry}
        if not isinstance(entry, Mapping):
            continue
        kept = {k: entry[k] for k in _AFFILIATION_FIELDS if _text(entry.get(k))}
        if kept.get("name"):
            affiliations.append(kept)
    if affiliations:
        result["affiliations"] = affiliations
        if not result["affiliation"]:
            result["affiliation"] = affiliations[0]["name"]
    return result


def author_merge(
    base: Mapping[str, Any] | None, override: Mapping[str, Any] | None
) -> dict[str, Any]:
    """Non-empty override fields replace the base, as R's ``.author_merge()`` does."""
    merged = author_normalize(base)
    override = override or {}
    for key in ("name", "email", "affiliation", "orcid", "url"):
        if _text(override.get(key)):
            merged[key] = override[key]
    if override.get("affiliations") is not None:
        merged["affiliations"] = override["affiliations"]
        if not _text(override.get("affiliation")):
            merged["affiliation"] = ""
    return author_normalize(merged)


# Resolution -------------------------------------------------------------------


@dataclass
class ResolvedSpec:
    """A complete project spec and the keys more than one layer set.

    Attributes:
        spec: The resolved spec, ready for ``spec_check()`` and ``project_create()``.
        conflicts: Keys that more than one layer set to different values, each with its path
            and the labels of the layers that set it. Package defaults do not count.
    """

    spec: dict[str, Any]
    conflicts: list[Conflict] = field(default_factory=list)


def _gitignore(type: str, sensitive: bool, blocks: Sequence[GitignoreBlock]) -> str:
    template = (
        "sensitive"
        if sensitive
        else {"course": "course", "presentation": "presentation"}.get(
            type, "sensitive" if type == "project_sensitive" else "project"
        )
    )
    base = _template(f"gitignore/{template}").rstrip("\n")
    extra = [f"\n# --- from {block.label} ---\n{block.text}" for block in blocks]
    return "\n".join([base, *extra])


def _mark_maps(spec: dict[str, Any]) -> None:
    """Make sure every keyed section exists, as an empty object if unset."""
    for path in MAP_PATHS:
        node = spec
        for part in path[:-1]:
            node = node.setdefault(part, {})
        if not node.get(path[-1]):
            node[path[-1]] = {}


def _finalize(spec: dict[str, Any], blocks: Sequence[GitignoreBlock]) -> dict[str, Any]:
    spec["spec_version"] = 1
    security = spec.setdefault("security", {})
    security["sensitive"] = security.get("sensitive") is True
    skills = spec.setdefault("skills", {})
    if (
        security["sensitive"]
        and spec["type"] != "bare"
        and "framework-sensitive-data" not in skills
    ):
        skills["framework-sensitive-data"] = {"package": True}

    ai = spec.setdefault("ai", {})
    ai["assistants"] = {k: True for k, v in (ai.get("assistants") or {}).items() if v is True}
    ai.setdefault("agents_md", "stub")

    spec.setdefault("git", {})["gitignore_content"] = _gitignore(
        spec["type"], security["sensitive"], blocks
    )
    _mark_maps(spec)
    ordered = {key: spec[key] for key in _ORDER if key in spec}
    ordered.update({key: value for key, value in spec.items() if key not in ordered})
    return ordered


def spec_resolve(
    layers: Sequence[Any] = (),
    name: str = "",
    author: Mapping[str, Any] | None = None,
    check: bool = True,
) -> ResolvedSpec:
    """Merge layers over Python's package defaults into one complete project spec.

    The project type comes from the last layer that sets ``type`` (default
    ``"project"``), and the language from the last layer that sets
    ``language`` (default ``"r"``, as in R). This package builds Python
    projects only, so the language must resolve to ``"python"``.

    Args:
        layers: Layers, bottom to top. Each is a layer in spec shape, or an
            envelope ``{kind, key, version, layer}`` as in a layers file.
        name: The project name. It overrides any name in the layers.
        author: Non-empty fields override the layers' author.
        check: Raise ``SpecError`` when the result is invalid.
    """
    if not name:
        raise SpecError("A project name is required")
    entries = normalize_layers(layers)
    type, language = "project", "r"
    for entry in entries:
        type = entry.layer.get("type") or type
        language = entry.layer.get("language") or language
    if type not in TYPES:
        raise SpecError(f"Unknown project type '{type}'. Use one of: {', '.join(TYPES)}")
    if language != LANGUAGE:
        raise SpecError(language_message(language))

    merged = merge_layers([Layer(PACKAGE_DEFAULTS, package_defaults(type)), *entries])
    spec = merged.spec
    spec["type"], spec["language"], spec["name"] = type, language, name
    if author is not None:
        spec["author"] = author_merge(spec.get("author"), author)
    spec = _finalize(spec, merged.gitignore_blocks)

    if check:
        problems = spec_check(spec)
        if problems:
            raise SpecError(
                "The resolved project spec is invalid:\n" + "\n".join(f"  - {p}" for p in problems)
            )
    return ResolvedSpec(spec, merged.conflicts)
