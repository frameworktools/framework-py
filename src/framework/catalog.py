"""The data catalog: names like ``inputs.raw.survey`` that resolve to data files.

The catalog is the ``data`` section of the settings. An entry is either a
path string or a mapping with ``path`` and optional ``type``, ``delimiter``,
``locked``, ``description``, and other fields. Nested mappings without a
``path`` group entries, and their keys form the dot-notation name. A key may
itself contain dots, so ``inputs.raw.survey`` is found as nested YAML, as
``raw.survey:`` under ``inputs:``, or as ``inputs.raw.survey:`` directly under
``data:``.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import pandas as pd

from framework._paths import has_private_segment
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
        type: File type: csv, tsv, parquet, rds, excel, stata, spss, spss_por, sas,
            or sas_xpt. A ``.tsv`` file found by its extension is csv with a tab
            delimiter; ``type: tsv`` in the catalog is kept as tsv.
        delimiter: For delimited text: comma, tab, semicolon, or space. None
            means it is guessed from the extension.
        locked: Whether the file's hash is checked against framework.db on read.
        private: Whether the file is in a private folder: one whose name
            contains private, confidential, or sensitive, at any depth below
            the project root.
        description: Shown when the data is read.
        name: The catalog name, when the file was found by name.
        extra: Any other fields of the catalog entry.
        columns: With ``data_info(..., columns=True)``: one row per column, with its
            ``name``, its ``type`` in R's words (logical, integer, double, character,
            factor, date, datetime, time, duration, or other), its pandas ``dtype``
            (``int64``, ``object``, ...), and its count of ``missing`` values. Never any
            values. None otherwise.
        rows: With ``columns=True``, the number of rows. None otherwise.
    """

    path: Path
    type: str
    delimiter: str | None = None
    locked: bool = False
    private: bool = False
    description: str | None = None
    name: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)
    columns: pd.DataFrame | None = field(default=None, compare=False)
    rows: int | None = None


def type_from_extension(path: str | Path) -> tuple[str, str | None]:
    """The file type and delimiter for a path, from its extension."""
    text = str(path)
    for pattern, file_type, delimiter in _TYPES:
        if re.search(pattern, text, re.IGNORECASE):
            return file_type, delimiter
    return "csv", None


def _is_absolute(path: str) -> bool:
    return Path(path).is_absolute() or bool(re.match(r"^[A-Za-z]:[/\\]", path))


def _in_private_folder(path: Path, root: Path | None) -> bool:
    """Whether any folder above ``path``, below the project root, is private.

    R checks only the immediate folder, so it misses ``inputs/private/raw``.
    """
    folder = path.parent
    if root is not None and folder.is_relative_to(root.resolve()):
        folder = folder.relative_to(root.resolve())
    return has_private_segment(folder)


def _build(
    path: str,
    base: Path,
    entry: dict[str, Any] | None,
    name: str | None,
    root: Path | None = None,
) -> DataInfo:
    expanded = Path(path).expanduser()
    full = expanded if _is_absolute(str(expanded)) else base / expanded
    full = full.resolve()
    file_type, delimiter = type_from_extension(full)
    fields: dict[str, Any] = {
        "type": file_type,
        "delimiter": delimiter,
        "locked": False,
        "private": _in_private_folder(full, root),
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


def _pathless_entry(item: Any) -> bool:
    """Whether a mapping is an entry's fields without a path, rather than a group.

    Every key must be an entry field, and no value may look like a file path, so a
    group with an entry named ``type: data/type.csv`` still reads as a group.
    """
    if not isinstance(item, dict) or not item or not set(item) <= _KNOWN_FIELDS:
        return False
    return not any(
        isinstance(value, (str, dict))
        and (isinstance(value, dict) or "/" in value or "\\" in value or Path(value).suffix)
        for key, value in item.items()
        if key != "description"
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
        elif _pathless_entry(item):
            # broken: {type: csv} is an entry missing its path, not a group
            names.append(full)
        elif isinstance(item, dict):
            names.extend(catalog_names(item, full))
    return names


def catalog_locate(name: str, data: Any) -> tuple[dict[str, Any], str] | None:
    """The mapping that holds a catalog name's value, and its key there.

    Keys may contain dots, so each run of name parts is tried as one key:
    nested keys first, then ``raw.survey:``-style mixed keys, then a flat
    ``inputs.raw.survey:`` key.
    """
    parts = name.split(".")

    def search(node: Any, start: int) -> tuple[dict[str, Any], str] | None:
        if not isinstance(node, dict):
            return None
        for end in range(start + 1, len(parts) + 1):
            key = ".".join(parts[start:end])
            if node.get(key) is None:
                continue
            if end == len(parts):
                return node, key
            found = search(node[key], end)
            if found is not None:
                return found
        return None

    return search(data, 0)


def _lookup(name: str, data: Any) -> Any:
    found = catalog_locate(name, data)
    return None if found is None else found[0][found[1]]


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


def data_info(path: str, columns: bool = False) -> DataInfo:
    """How to read a data file, given a catalog name or a file path.

    ``path`` is a dot-notation catalog name (``"inputs.raw.survey"``), a relative
    path (anything with a slash), or an absolute path. A relative path is looked for
    from the project root, then from the current folder. Catalog paths are relative to
    the project root.

    With ``columns=True``, the file is read and summarized without showing any values:
    each column's name, type, pandas dtype, and missing count, and the number of rows.
    An agent under the sensitive-data rules can learn a restricted file's layout this way
    without seeing its records::

        info = fw.data_info("inputs.private.raw.patients", columns=True)
        info.columns   # name, type, dtype, missing
        info.rows

    Args:
        path: A dot-notation catalog name, a relative path containing a slash, or an absolute
            path.
        columns: Read the file and summarize its columns. The read is not recorded in
            framework.db and does not check the file's hash.

    Returns:
        The file's path, type, and catalog fields, with ``columns`` and ``rows`` when asked.

    Raises:
        DataNotFoundError: The name is not in the catalog, its entry has no path, or (with
            ``columns=True``) the file does not exist.
    """
    info = _data_info(path)
    if not columns:
        return info
    from framework.data import read_file

    frame = read_file(info)
    return replace(info, columns=column_summary(frame), rows=len(frame))


def _type_name(column: pd.Series) -> str:
    """A column's type in R's words, so the summary reads the same in both languages."""
    kind = column.dtype
    if pd.api.types.is_bool_dtype(kind):
        return "logical"
    if pd.api.types.is_integer_dtype(kind):
        return "integer"
    if pd.api.types.is_float_dtype(kind):
        return "double"
    if isinstance(kind, pd.CategoricalDtype):
        return "factor"
    if pd.api.types.is_datetime64_any_dtype(kind):
        return "datetime"
    if pd.api.types.is_timedelta64_dtype(kind):
        return "duration"
    kinds = {type(value) for value in column.dropna()}
    if not kinds or kinds <= {str}:
        return "character"
    if kinds <= {bool}:
        return "logical"
    if kinds <= {dt.date}:
        return "date"
    if kinds <= {dt.time}:
        return "time"
    if kinds <= {dt.datetime, pd.Timestamp}:
        return "datetime"
    if kinds <= {int}:
        return "integer"
    if kinds <= {int, float}:
        return "double"
    return "other"


def column_summary(frame: pd.DataFrame) -> pd.DataFrame:
    """Each column's name, type (in R's words, as R's summary has it), pandas dtype, and
    missing count. Never any values."""
    return pd.DataFrame(
        {
            "name": [str(name) for name in frame.columns],
            "type": [_type_name(frame.iloc[:, i]) for i in range(frame.shape[1])],
            "dtype": [str(frame.iloc[:, i].dtype) for i in range(frame.shape[1])],
            "missing": [int(frame.iloc[:, i].isna().sum()) for i in range(frame.shape[1])],
        },
        columns=["name", "type", "dtype", "missing"],
    )


def _data_info(path: str) -> DataInfo:
    settings_file = find_settings_file()
    root = settings_file.parent if settings_file else None
    if _is_absolute(path) or "/" in path or "\\" in path:
        # A relative path starts at the project root, as data_save() writes it. The current
        # folder is used only when the file is there and not under the root, so a missing
        # file is reported under the root.
        relative = Path(path).expanduser()
        base = root
        if base is None or ((Path.cwd() / relative).exists() and not (base / relative).exists()):
            base = Path.cwd()
        return _build(path, base, None, None, root)

    base = root or Path.cwd()
    data = settings_read(settings_file).get("data")
    entry = _lookup(path, data)
    if entry is None:
        raise _not_found(path, data)
    if isinstance(entry, str):
        return _build(entry, base, None, path, root)
    if isinstance(entry, dict) and isinstance(entry.get("path"), str):
        return _build(entry["path"], base, entry, path, root)
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
