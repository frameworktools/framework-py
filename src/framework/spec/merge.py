"""The layer merge shared by every Framework engine.

This is the part of ``spec_resolve()`` that R, Python, and ``fw`` must agree
on exactly. The shared test cases are the conformance fixtures in the R
repository (``inst/conformance/merge``). Package defaults are not part of the
merge: each engine passes its own as the first layer.

Rules:

- Objects merge by key, and a later layer wins.
- A key set to null removes it.
- Entries of ``files``, ``skills``, ``extra_directories``,
  ``packages.default_packages``, ``connections.databases``, and
  ``connections.storage_buckets`` are replaced whole.
- An empty object or array changes nothing, but makes sure the section exists.
- ``git.gitignore_content`` is collected per layer instead of merged.
- A key set to different values by more than one layer (package defaults
  excepted) is a conflict.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from framework.errors import FrameworkError

PACKAGE_DEFAULTS = "package defaults"

# Sections whose entries are replaced whole.
ATOMIC_MAPS = frozenset(
    {
        "files",
        "skills",
        "extra_directories",
        "packages/default_packages",
        "connections/databases",
        "connections/storage_buckets",
        "data",
    }
)

# Sections that must be objects keyed by name.
MAP_PATHS: tuple[tuple[str, ...], ...] = (
    ("tools",),
    ("directories",),
    ("extra_directories",),
    ("render_dirs",),
    ("env",),
    ("data_hooks",),
    ("files",),
    ("skills",),
    ("packages", "default_packages"),
    ("git", "hooks"),
    ("ai", "assistants"),
    ("connections", "databases"),
    ("connections", "storage_buckets"),
)

_ENVELOPE_KEYS = frozenset({"kind", "key", "version", "layer"})
_REMOVED = "<removed>"


class SpecError(FrameworkError):
    """A layer or spec is malformed."""


@dataclass(frozen=True)
class Layer:
    label: str
    layer: dict[str, Any]


@dataclass(frozen=True)
class GitignoreBlock:
    label: str
    text: str


@dataclass(frozen=True)
class Conflict:
    path: str
    layers: str


@dataclass
class MergeResult:
    spec: dict[str, Any] = field(default_factory=dict)
    gitignore_blocks: list[GitignoreBlock] = field(default_factory=list)
    setters: dict[str, list[tuple[str, Any]]] = field(default_factory=dict)

    @property
    def conflicts(self) -> list[Conflict]:
        return conflicts(self.setters)


def normalize_layers(layers: Sequence[Any]) -> list[Layer]:
    """Label each layer. An envelope ``{kind, key, version, layer}`` is unwrapped."""
    normalized = []
    for index, entry in enumerate(layers, start=1):
        if isinstance(entry, Layer):
            normalized.append(entry)
            continue
        is_envelope = (
            isinstance(entry, Mapping) and "layer" in entry and set(entry) <= _ENVELOPE_KEYS
        )
        if is_envelope:
            parts = [str(entry.get("kind") or f"layer {index}")]
            if entry.get("key") is not None:
                parts.append(str(entry["key"]))
            if entry.get("version") is not None:
                parts.append(f"v{entry['version']}")
            normalized.append(Layer(" ".join(parts), dict(entry.get("layer") or {})))
        else:
            normalized.append(Layer(f"layer {index}", dict(entry or {})))
    return normalized


def _get(data: Mapping[str, Any], path: tuple[str, ...]) -> Any:
    value: Any = data
    for part in path:
        if not isinstance(value, Mapping):
            return None
        value = value.get(part)
    return value


def _merge(
    base: dict[str, Any],
    layer: Mapping[str, Any],
    path: tuple[str, ...],
    label: str,
    setters: dict[str, list[tuple[str, Any]]],
) -> dict[str, Any]:
    for key, value in layer.items():
        key_path = (*path, key)
        path_str = "/".join(key_path)

        if value is None:
            base.pop(key, None)
            setters.setdefault(path_str, []).append((label, _REMOVED))
            continue

        atomic = "/".join(path) in ATOMIC_MAPS
        current = base.get(key)
        if (
            not atomic
            and isinstance(value, Mapping)
            and (current is None or isinstance(current, dict))
        ):
            base[key] = _merge(dict(current or {}), value, key_path, label, setters)
        elif isinstance(value, list) and not value:
            base.setdefault(key, {})
        else:
            base[key] = value
            setters.setdefault(path_str, []).append((label, value))
    return base


def merge_layers(layers: Sequence[Any]) -> MergeResult:
    """Merge layers, bottom to top, with the shared rules."""
    result = MergeResult()
    for entry in normalize_layers(layers):
        layer = dict(entry.layer)
        for path in MAP_PATHS:
            value = _get(layer, path)
            if isinstance(value, list) and value:
                raise SpecError(
                    f"Layer '{entry.label}': {'.'.join(path)} must be an object keyed by name, "
                    "not a list"
                )
        git = layer.get("git")
        if isinstance(git, Mapping):
            text = git.get("gitignore_content")
            if isinstance(text, list):
                text = "\n".join(str(line) for line in text)
            if text:
                result.gitignore_blocks.append(GitignoreBlock(entry.label, str(text)))
            layer["git"] = {k: v for k, v in git.items() if k != "gitignore_content"}
        result.spec = _merge(result.spec, layer, (), entry.label, result.setters)
    return result


def conflicts(setters: Mapping[str, list[tuple[str, Any]]]) -> list[Conflict]:
    """Keys set to different values by more than one layer, package defaults excepted."""
    found = []
    for path, entries in setters.items():
        relevant = [(label, value) for label, value in entries if label != PACKAGE_DEFAULTS]
        labels = list(dict.fromkeys(label for label, _ in relevant))
        values: list[Any] = []
        for _, value in relevant:
            if value not in values:
                values.append(value)
        if len(labels) > 1 and len(values) > 1:
            found.append(Conflict(path, ", ".join(labels)))
    return found
