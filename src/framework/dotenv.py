"""Reading and writing ``.env`` files with the same rules as R's dotenv package.

The rules, from dotenv 1.0:

- Lines that start with ``#`` and blank lines are skipped. A ``#`` later in a
  line is part of the value.
- Each other line is ``[export ]KEY=VALUE``. The key is everything before the
  first ``=``, kept exactly as written.
- A value wrapped in matching single or double quotes loses the quotes.
  Nothing inside the quotes is unescaped.
- A line that does not match is an error.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from pathlib import Path

from framework.errors import SettingsError

_LINE = re.compile(r"^\s*(?:export\s+)?(?P<key>[^=]+)=(?P<q>['\"]?)(?P<value>.*)(?P=q)\s*$")


def parse(text: str) -> dict[str, str]:
    """Parse ``.env`` text into a dict, in file order. Later keys win."""
    values: dict[str, str] = {}
    for line in text.splitlines():
        if line.startswith("#") or not line.strip():
            continue
        match = _LINE.match(line)
        if match is None:
            raise SettingsError(f"Cannot parse .env line: {line[:40]}")
        values[match["key"]] = match["value"]
    return values


def read(path: str | Path) -> dict[str, str]:
    """Read a ``.env`` file. A missing file reads as empty."""
    file = Path(path)
    if not file.is_file():
        return {}
    return parse(file.read_text(encoding="utf-8"))


def load(path: str | Path) -> dict[str, str]:
    """Read a ``.env`` file and set its values in ``os.environ``.

    Values replace variables that are already set, as R's ``load_dot_env()``
    does, so a project behaves the same from R and Python.
    """
    values = read(path)
    os.environ.update(values)
    return values


def value(path: str | Path, key: str) -> str | None:
    """One value from a ``.env`` file, or None when it is missing or empty."""
    found = read(path).get(key, "")
    return found or None


def add_missing(path: str | Path, values: Mapping[str, str]) -> list[str]:
    """Append ``KEY=value`` lines for keys the file does not already define.

    Returns the keys that were added. Existing lines are never changed.
    """
    file = Path(path)
    text = file.read_text(encoding="utf-8") if file.exists() else ""
    present = parse(text)
    added = [key for key in values if key not in present]
    if not added:
        return []
    if text and not text.endswith("\n"):
        text += "\n"
    text += "".join(f"{key}={values[key]}\n" for key in added)
    file.write_text(text, encoding="utf-8")
    return added
