"""The data catalog: names like ``inputs.raw.survey`` that resolve to data files.

The catalog is the ``data`` section of the settings. An entry is either a
path string or a mapping with ``path`` and optional ``type``, ``delimiter``,
``locked``, ``description``, and other fields. Nested mappings without a
``path`` group entries, and their keys form the dot-notation name.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

from framework.errors import DataNotFoundError
from framework.project import find_settings_file
from framework.settings import settings_read

# Extension -> (type, delimiter), in the order R checks them. Anything else
# is read as delimited text with the delimiter guessed at read time.
_TYPES: tuple[tuple[str, str, str | None], ...] = (
    (r"\.rds$", "rds", None),
    (r"\.tsv$", "csv", "tab"),
    (r"\.csv$", "csv", "comma"),
    (r"\.(xlsx|xls)$", "excel", None),
    (r"\.parquet$", "parquet", None),
    (r"\.dta$", "stata", None),
    (r"\.(sav|zsav)$", "spss", None),
    (r"\.por$", "spss_por", None),
    (r"\.(sas7bdat|sas7bcat)$", "sas", None),
    (r"\.xpt$", "sas_xpt", None),
)

_KNOWN_FIELDS = frozenset({"path", "type", "delimiter", "locked", "private", "description"})


@dataclass(frozen=True)
class DataInfo:
    """How to read one data file.

    Attributes:
        path: Absolute path to the file.
        type: File type: csv, parquet, rds, excel, stata, spss, spss_por, sas,
            or sas_xpt.
        delimiter: For delimited text: comma, tab, semicolon, or space. None
            means it is guessed from the extension.
        locked: Whether the file's hash is checked against framework.db on read.
        private: Whether the file is in a folder named ``private``.
        description: Shown when the data is read.
        name: The catalog name, when the file was found by name.
        extra: Any other fields of the catalog entry.
    """

    path: Path
    type: str
    delimiter: str | None = None
    locked: bool = False
    private: bool = False
    description: str | None = None
    name: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


def type_from_extension(path: str | Path) -> tuple[str, str | None]:
    """The file type and delimiter for a path, from its extension."""
    text = str(path)
    for pattern, file_type, delimiter in _TYPES:
        if re.search(pattern, text, re.IGNORECASE):
            return file_type, delimiter
    return "csv", None


def _is_absolute(path: str) -> bool:
    return Path(path).is_absolute() or bool(re.match(r"^[A-Za-z]:[/\\]", path))


def _build(path: str, base: Path, entry: dict[str, Any] | None, name: str | None) -> DataInfo:
    full = Path(path) if _is_absolute(path) else base / path
    full = full.resolve()
    file_type, delimiter = type_from_extension(full)
    fields: dict[str, Any] = {
        "type": file_type,
        "delimiter": delimiter,
        "locked": False,
        "private": full.parent.name == "private",
        "description": None,
    }
    extra: dict[str, Any] = {}
    for key, value in (entry or {}).items():
        if key == "path" or value is None:
            continue
        if key in _KNOWN_FIELDS:
            fields[key] = value
        else:
            extra[key] = value
    return DataInfo(
        path=full,
        type=str(fields["type"]),
        delimiter=fields["delimiter"],
        locked=bool(fields["locked"]),
        private=bool(fields["private"]),
        description=fields["description"] or None,
        name=name,
        extra=extra,
    )


def catalog_names(data: Any, prefix: str = "") -> list[str]:
    """Every dot-notation name in a catalog section, in file order."""
    names: list[str] = []
    if not isinstance(data, dict):
        return names
    for key, item in data.items():
        full = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(item, str) or (isinstance(item, dict) and "path" in item):
            names.append(full)
        elif isinstance(item, dict):
            names.extend(catalog_names(item, full))
    return names


def _lookup(name: str, data: Any) -> Any:
    current = data
    for part in name.split("."):
        if not isinstance(current, dict) or current.get(part) is None:
            return None
        current = current[part]
    return current


def _not_found(name: str, data: Any) -> DataNotFoundError:
    message = f"No data specification found for path: {name}"
    names = catalog_names(data)
    if names:
        shown = names[:20]
        if len(names) > 20:
            shown.append(f"... and {len(names) - 20} more")
        message += "\n\nAvailable data paths:\n  " + "\n  ".join(shown)
        message += (
            "\n\nTip: Use settings('data') to explore the full catalog, "
            "or narrow it down with settings('data.inputs')"
        )
    return DataNotFoundError(message)


def data_info(path: str) -> DataInfo:
    """How to read a data file, given a catalog name or a file path.

    ``path`` is a dot-notation catalog name (``"inputs.raw.survey"``), a path
    relative to the current folder (anything with a slash), or an absolute
    path. Catalog paths are relative to the project root.

    Raises:
        DataNotFoundError: The name is not in the catalog.
    """
    if _is_absolute(path):
        return _build(path, Path.cwd(), None, None)
    if "/" in path or "\\" in path:
        return _build(path, Path.cwd(), None, None)

    settings_file = find_settings_file()
    base = settings_file.parent if settings_file else Path.cwd()
    data = settings_read(settings_file).get("data")
    entry = _lookup(path, data)
    if entry is None:
        raise _not_found(path, data)
    if isinstance(entry, str):
        return _build(entry, base, None, path)
    if isinstance(entry, dict) and isinstance(entry.get("path"), str):
        return _build(entry["path"], base, entry, path)
    raise DataNotFoundError(f"Catalog entry '{path}' has no path")


def data_list() -> pd.DataFrame:
    """Every catalog entry, with columns name, path, type, locked, and description."""
    data = settings_read().get("data")
    rows = []
    for name in catalog_names(data):
        entry = _lookup(name, data)
        fields = {"path": entry} if isinstance(entry, str) else entry
        rows.append(
            {
                "name": name,
                "path": fields.get("path"),
                "type": fields.get("type"),
                "locked": bool(fields.get("locked", False)),
                "description": fields.get("description"),
            }
        )
    return pd.DataFrame(rows, columns=["name", "path", "type", "locked", "description"])
