"""YAML reading and writing with R-compatible behavior.

R's yaml package and PyYAML both follow YAML 1.1, so ``yes``/``no``,
``on``/``off``, octal numbers, and ``~`` read the same way. Two additions
match R:

- ``y``, ``Y``, ``n``, and ``N`` are booleans, as they are in R.
- The ``!expr`` tag is kept as the string ``"!expr <code>"`` for later
  evaluation.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

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
