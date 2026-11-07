"""YAML reading and writing with R-compatible behavior.

R's yaml package and PyYAML both follow YAML 1.1, so ``yes``/``no``,
``on``/``off``, octal numbers, and ``~`` read the same way. Two additions
match R:

- ``y``, ``Y``, ``n``, and ``N`` are booleans, as they are in R.
- The ``!expr`` tag is kept as the string ``"!expr <code>"`` for later
  evaluation.

``update()`` edits a settings file in place with ruamel.yaml, so the comments,
the layout of sequences, and quoting in the rest of the file are kept.
"""

from __future__ import annotations

import io
import re
import warnings
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml
from ruamel.yaml import YAML
from ruamel.yaml.error import MantissaNoDotYAML1_1Warning
from ruamel.yaml.representer import RoundTripRepresenter

_BOOL_TAG = "tag:yaml.org,2002:bool"
_SHORT_BOOLS = {"y": True, "n": False}


class _Loader(yaml.SafeLoader):
    pass


def _expr(loader: yaml.SafeLoader, node: yaml.Node) -> str:
    value = loader.construct_scalar(node)  # type: ignore[arg-type]
    return f"!expr {value}"


def _bool(loader: yaml.SafeLoader, node: yaml.Node) -> bool:
    value = str(loader.construct_scalar(node)).lower()  # type: ignore[arg-type]
    if value in _SHORT_BOOLS:
        return _SHORT_BOOLS[value]
    return bool(loader.bool_values[value])


_Loader.add_constructor("!expr", _expr)
_Loader.add_constructor(_BOOL_TAG, _bool)
_Loader.add_implicit_resolver(_BOOL_TAG, re.compile(r"^(?:y|Y|n|N)$"), list("yYnN"))


def load(text: str) -> Any:
    return yaml.load(text, Loader=_Loader)


def read(path: Path) -> Any:
    return load(path.read_text(encoding="utf-8"))


class _Dumper(yaml.SafeDumper):
    pass


def _none(dumper: yaml.SafeDumper, _: None) -> yaml.Node:
    # R writes NULL as "~".
    return dumper.represent_scalar("tag:yaml.org,2002:null", "~")


# Plain scalars R's yaml package reads as something other than a string:
# "." and ".na" become NA, and y/n are booleans. R quotes them when writing.
_R_SPECIAL = re.compile(r"^(?:\.|\.na|y|Y|n|N)$")


def _str(dumper: yaml.SafeDumper, value: str) -> yaml.Node:
    if _R_SPECIAL.match(value):
        return dumper.represent_scalar("tag:yaml.org,2002:str", value, style="'")
    return dumper.represent_str(value)


_Dumper.add_representer(type(None), _none)
_Dumper.add_representer(str, _str)


def dump(data: Any) -> str:
    return yaml.dump(
        data,
        Dumper=_Dumper,
        default_flow_style=False,
        sort_keys=False,
        allow_unicode=True,
        width=1_000_000,
    )


def write(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dump(data), encoding="utf-8")


# Editing in place ------------------------------------------------------------


class _RoundTripRepresenter(RoundTripRepresenter):
    pass


def _round_trip_str(representer: RoundTripRepresenter, value: str) -> Any:
    # YAML 1.1 output already quotes strings such as "no" and "y". R also reads "."
    # and ".na" as NA, so they are quoted too, as _dump() does.
    if _R_SPECIAL.match(value):
        return representer.represent_scalar("tag:yaml.org,2002:str", value, style="'")
    return representer.represent_str(value)


def _round_trip_none(representer: RoundTripRepresenter, _: None) -> Any:
    return representer.represent_scalar("tag:yaml.org,2002:null", "~")


_RoundTripRepresenter.add_representer(str, _round_trip_str)
_RoundTripRepresenter.add_representer(type(None), _round_trip_none)
_DIRECTIVE = "%YAML 1.1\n---\n"


def _round_trip() -> YAML:
    handler = YAML()
    handler.Representer = _RoundTripRepresenter
    # YAML 1.1, as R and PyYAML read it: yes/no and y/n are booleans, 012 is octal,
    # and a new string that would read as something else is quoted
    handler.version = (1, 1)
    handler.preserve_quotes = True
    handler.width = 1_000_000
    handler.indent(mapping=2, sequence=2, offset=0)
    return handler


def round_trip_load(text: str) -> Any:
    """YAML text as ruamel.yaml's round-trip objects, which keep comments, layout, and tags
    such as ``!expr``. None for empty text."""
    handler = _round_trip()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", MantissaNoDotYAML1_1Warning)
        return handler.load(text) if text.strip() else None


def round_trip_dump(data: Any) -> str:
    """Round-trip objects (or plain values) as YAML text, as ``update()`` writes them."""
    out = io.StringIO()
    _round_trip().dump(data, out)
    return out.getvalue().removeprefix(_DIRECTIVE)


def update_text(text: str, change: Callable[[Any], Any]) -> str:
    """``update()`` on text: the YAML ``text`` after ``change``, keeping its comments and
    layout."""
    data = round_trip_load(text)
    result = change(data)
    return round_trip_dump(data if result is None else result)


def update(path: Path, change: Callable[[Any], Any]) -> None:
    """Edit a YAML file in place, keeping its comments and layout.

    ``change`` receives the file's content (mappings and sequences that behave like dicts
    and lists, or None for an empty or missing file), changes it in place or returns a new
    value, and the result is written back. Values read from the file keep their form,
    except that YAML 1.1 booleans such as ``yes`` are written ``true`` and ``false``. New
    sequences are block sequences, and new booleans are ``true`` and ``false``.
    """
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    handler = _round_trip()
    with warnings.catch_warnings():
        # "1e3" is a string to PyYAML and a float to ruamel; either way it is written
        # back as it was
        warnings.simplefilter("ignore", MantissaNoDotYAML1_1Warning)
        data = handler.load(text) if text.strip() else None
    result = change(data)
    data = data if result is None else result
    out = io.StringIO()
    handler.dump(data, out)
    # The version directive is only for ruamel: R and PyYAML read YAML 1.1 anyway
    written = out.getvalue().removeprefix(_DIRECTIVE)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(written, encoding="utf-8")
