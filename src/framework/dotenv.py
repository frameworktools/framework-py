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
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
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


# What load() set, and what each variable held before, so loading another project's
# file first takes those values back
_loaded: dict[str, tuple[str, str | None]] = {}


def _restore(applied: Mapping[str, tuple[str, str | None]]) -> None:
    """Undo values this module set, unless something changed them since."""
    for key, (value, previous) in applied.items():
        if os.environ.get(key) != value:
            continue
        if previous is None:
            del os.environ[key]
        else:
            os.environ[key] = previous


def _apply(values: Mapping[str, str]) -> dict[str, tuple[str, str | None]]:
    applied = {key: (value, os.environ.get(key)) for key, value in _applicable(values).items()}
    os.environ.update({key: value for key, (value, _) in applied.items()})
    return applied


def _applicable(values: Mapping[str, str]) -> dict[str, str]:
    """The values a project's ``.env`` may set. The R package follows the same rules.

    - A variable already set to a non-empty value wins, so a value exported in the
      shell is never replaced.
    - An empty value (``S3_ACCESS_KEY=``, as in the starter file) is skipped, so it never
      blanks a variable.
    """
    return {key: value for key, value in values.items() if value and not os.environ.get(key)}


@contextmanager
def scoped(values: Mapping[str, str]) -> Iterator[dict[str, str]]:
    """Set ``.env`` values in ``os.environ`` only inside the block.

    ``settings_read()`` uses this to resolve ``env()`` expressions, so one project's
    token or server never carries over into the next project. Yields what was set.
    """
    applied = _apply(values)
    try:
        yield {key: value for key, (value, _) in applied.items()}
    finally:
        _restore(applied)


def load(path: str | Path) -> dict[str, str]:
    """Read a ``.env`` file and set its values in ``os.environ`` for the session.

    ``scaffold()`` calls this. The rules of ``_applicable()`` apply: shell values win and
    empty values are skipped. Values an earlier ``load()`` set are removed first, so the
    previous project's values do not carry over. Returns the values that were set.
    """
    values = read(path)
    _restore(_loaded)
    _loaded.clear()
    _loaded.update(_apply(values))
    return {key: value for key, (value, _) in _loaded.items()}


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
