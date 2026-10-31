"""Reading and saving data with integrity tracking.

``data_read()`` checks a catalog file's SHA-256 against the hash recorded in
framework.db: a locked file that changed is an error, and an unlocked file
that changed is a warning. ``data_save()`` and ``data_add()`` record the hash.
Hashes match R's, so a file saved in one language verifies in the other.

Python reads CSV, TSV, Parquet, Excel, Stata, SPSS, and SAS files. It writes
CSV and Parquet, which R reads too. ``.rds`` files are R-only.
"""

from __future__ import annotations

import re
import warnings
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pandas as pd

from framework import _yaml, db
from framework._log import inform
from framework._paths import in_root, relative_to_root
from framework.catalog import DataInfo, catalog_locate, data_info
from framework.errors import (
    DataChangedWarning,
    DataNotFoundError,
    FrameworkError,
    FrameworkWarning,
    IntegrityError,
    SettingsError,
    UnsupportedFormatError,
)
from framework.integrity import file_hash
from framework.project import find_settings_file, project_root
from framework.settings import settings_read

DELIMITERS = {"comma": ",", "tab": "\t", "semicolon": ";", "space": " "}

# Extension -> type for files read directly by path, as R's data_read() does.
_DIRECT_TYPES = {
    "csv": "csv",
    "tsv": "tsv",
    "txt": "tsv",
    "dat": "tsv",
    "rds": "rds",
    "xlsx": "excel",
    "xls": "excel",
    "dta": "stata",
    "sav": "spss",
    "zsav": "spss",
    "por": "spss_por",
    "sas7bdat": "sas",
    "sas7bcat": "sas",
    "xpt": "sas_xpt",
    "parquet": "parquet",
}

# Extension -> type recorded by data_add(). R's data_add() records .por as
# spss and .xpt as sas, which its own data_read() then opens with the wrong
# reader. Python records the reader types, which R reads too.
_ADD_TYPES = {
    "csv": "csv",
    "tsv": "tsv",
    "txt": "tsv",
    "dat": "tsv",
    "rds": "rds",
    "xlsx": "excel",
    "xls": "excel",
    "dta": "stata",
    "sav": "spss",
    "zsav": "spss",
    "por": "spss_por",
    "sas7bdat": "sas",
    "xpt": "sas_xpt",
    "parquet": "parquet",
}

# Catalog entries R's data_add() wrote for these extensions carry the generic
# type, so the extension picks the reader.
_GENERIC_TYPES = {("spss", ".por"): "spss_por", ("sas", ".xpt"): "sas_xpt"}

_SAVE_TYPES = ("csv", "parquet")

_R_ONLY = (
    "{path} is an .rds file, which only R can read. "
    "Save it from R as Parquet or CSV to use it in Python, for example "
    "data_save(df, 'inputs.intermediate.name', type = 'parquet')."
)


def _separator(delimiter: str | None) -> str:
    if delimiter is None:
        return ","
    if delimiter in DELIMITERS:
        return DELIMITERS[delimiter]
    if delimiter in DELIMITERS.values():
        return delimiter
    raise FrameworkError(
        f"Unknown delimiter: {delimiter}. Must be one of: comma, tab, semicolon, space"
    )


def _require(module: str, extra: str, what: str) -> None:
    try:
        __import__(module)
    except ImportError as error:
        raise FrameworkError(
            f"{what} need the {module} package. Install it with: pip install framework-py[{extra}]"
        ) from error


def _read_spss(path: Path, keep_attributes: bool, kwargs: dict[str, Any], portable: bool) -> Any:
    _require("pyreadstat", "spss", "SPSS files")
    import pyreadstat

    reader = pyreadstat.read_por if portable else pyreadstat.read_sav
    frame, _ = reader(str(path), apply_value_formats=keep_attributes, **kwargs)
    return frame


def _reader(file_type: str) -> Callable[[Path, str | None, bool, dict[str, Any]], Any]:
    def csv(path: Path, delimiter: str | None, _: bool, kwargs: dict[str, Any]) -> Any:
        return pd.read_csv(path, sep=_separator(delimiter), **kwargs)

    def tsv(path: Path, _d: str | None, _k: bool, kwargs: dict[str, Any]) -> Any:
        return pd.read_csv(path, sep="\t", **kwargs)

    def parquet(path: Path, _d: str | None, _k: bool, kwargs: dict[str, Any]) -> Any:
        return pd.read_parquet(path, **kwargs)

    def excel(path: Path, _d: str | None, _k: bool, kwargs: dict[str, Any]) -> Any:
        if path.suffix.lower() == ".xlsx":
            _require("openpyxl", "excel", "Excel files")
        return pd.read_excel(path, **kwargs)

    def stata(path: Path, _d: str | None, keep: bool, kwargs: dict[str, Any]) -> Any:
        kwargs.setdefault("convert_categoricals", keep)
        return pd.read_stata(path, **kwargs)

    def spss(path: Path, _d: str | None, keep: bool, kwargs: dict[str, Any]) -> Any:
        return _read_spss(path, keep, kwargs, portable=False)

    def spss_por(path: Path, _d: str | None, keep: bool, kwargs: dict[str, Any]) -> Any:
        return _read_spss(path, keep, kwargs, portable=True)

    # Without an encoding, pandas returns SAS text columns as bytes.
    def sas(path: Path, _d: str | None, _k: bool, kwargs: dict[str, Any]) -> Any:
        kwargs.setdefault("encoding", "infer")
        return pd.read_sas(path, format="sas7bdat", **kwargs)

    def sas_xpt(path: Path, _d: str | None, _k: bool, kwargs: dict[str, Any]) -> Any:
        # pandas reads only version 5 transport files. pyreadstat (the sas
        # extra) also reads version 8, which R's haven writes by default.
        try:
            import pyreadstat
        except ImportError:
            if path.read_bytes()[:80].find(b"LIBV8") != -1:
                raise FrameworkError(
                    "This is a version 8 SAS transport file, which needs the pyreadstat "
                    "package. Install it with: pip install framework-py[sas]"
                ) from None
            kwargs.setdefault("encoding", "utf-8")
            return pd.read_sas(path, format="xport", **kwargs)
        frame, _ = pyreadstat.read_xport(str(path), **kwargs)
        return frame

    readers: dict[str, Callable[[Path, str | None, bool, dict[str, Any]], Any]] = {
        "csv": csv,
        "tsv": tsv,
        "parquet": parquet,
        "excel": excel,
        "xlsx": excel,
        "xls": excel,
        "stata": stata,
        "dta": stata,
        "spss": spss,
        "sav": spss,
        "zsav": spss,
        "spss_por": spss_por,
        "por": spss_por,
        "sas": sas,
        "sas7bdat": sas,
        "sas7bcat": sas,
        "sas_xpt": sas_xpt,
        "xpt": sas_xpt,
    }
    if file_type not in readers:
        raise UnsupportedFormatError(f"Unsupported file type: {file_type}")
    return readers[file_type]


def _guess_delimiter(path: Path) -> str | None:
    return {"csv": "comma", "tsv": "tab", "txt": "tab", "dat": "tab"}.get(
        path.suffix.lower().lstrip(".")
    )


def _check_integrity(name: str, info: DataInfo) -> None:
    """Compare the file with framework.db and record its current hash."""
    current = file_hash(info.path)
    record = db.get_data_record(name)
    if record is not None and record.hash and record.hash != current:
        detail = f"'{name}'\nExpected hash: {record.hash[:12]}\nCurrent hash:  {current[:12]}"
        if info.locked:
            raise IntegrityError(f"Hash mismatch for locked data {detail}")
        warnings.warn(f"File hash changed for {detail}", DataChangedWarning, stacklevel=3)
    root = project_root()
    db.set_data_record(
        name,
        path=relative_to_root(info.path, root),
        type=info.type,
        delimiter=info.delimiter,
        locked=info.locked,
        hash=current,
        root=root,
    )


def data_read(
    path: str, delim: str | None = None, keep_attributes: bool = False, **kwargs: Any
) -> pd.DataFrame:
    """Read data by catalog name or file path.

    Files found by catalog name are integrity-checked: Framework records the
    file's SHA-256 in framework.db the first time and compares it on every
    later read. A changed locked file raises ``IntegrityError``. A changed
    unlocked file issues a ``DataChangedWarning``.

    Args:
        path: A catalog name such as ``"inputs.raw.survey"``, or a file path.
        delim: Delimiter for delimited text: comma, tab, semicolon, or space.
            Default: the catalog's ``delimiter``, then the file extension.
        keep_attributes: Keep value labels from Stata and SPSS files as
            categories. By default the stored codes are returned, as in R.
        **kwargs: Passed to the pandas (or pyreadstat) reader.

    Raises:
        DataNotFoundError: The name is not in the catalog, or the file is missing.
        UnsupportedFormatError: The file is an ``.rds`` file or another
            format Python cannot read.
        IntegrityError: A locked file changed since its hash was recorded.
    """
    direct = Path(path).expanduser()
    if direct.is_file():
        extension = direct.suffix.lower().lstrip(".")
        if extension not in _DIRECT_TYPES:
            raise UnsupportedFormatError(f"Unsupported file extension: {extension}")
        info = DataInfo(path=direct.resolve(), type=_DIRECT_TYPES[extension])
        tracked = False
    else:
        info = data_info(path)
        tracked = True

    if not info.path.is_file():
        raise DataNotFoundError(f"File not found: {info.path}")
    if info.type == "rds":
        raise UnsupportedFormatError(_R_ONLY.format(path=info.path))

    if tracked:
        _check_integrity(path, info)

    delimiter = delim or info.delimiter or _guess_delimiter(info.path)
    if delimiter is None and info.type == "csv":
        warnings.warn(
            f"Could not determine delimiter from extension for {info.path}, defaulting to comma",
            FrameworkWarning,
            stacklevel=2,
        )
    file_type = _GENERIC_TYPES.get((info.type, info.path.suffix.lower()), info.type)
    try:
        data: pd.DataFrame = _reader(file_type)(info.path, delimiter, keep_attributes, dict(kwargs))
    except (FrameworkError, ImportError):
        raise
    except Exception as error:
        raise FrameworkError(f"Failed to load data: {error}") from error

    if info.description:
        inform(f"Note: {path}: {info.description}")
    return data


def _save_target(path: str, type: str | None) -> tuple[Path, str, bool]:
    """The file to write, its type, and whether ``path`` was a catalog name."""
    has_slash = "/" in path or "\\" in path
    extension = re.search(r"\.(csv|parquet|rds)$", path, re.IGNORECASE)

    if has_slash or extension:
        if type is None:
            if extension is None:
                raise FrameworkError(
                    "Cannot auto-detect file type from path. Please specify 'type'."
                )
            type = extension.group(1).lower()
        target = Path(path)
        if not path.lower().endswith(f".{type}"):
            target = Path(f"{path}.{type}")
            inform(f"Note: Added .{type} extension to path")
        return target, type, False

    parts = path.split(".")
    if len(parts) == 1:
        suffix = type or "parquet"
        raise FrameworkError(
            f"Path '{path}' has no directory. Either:\n"
            f"  - Use dot notation: 'inputs.raw.{path}' or 'inputs.intermediate.{path}'\n"
            f"  - Provide a full path: 'inputs/raw/{path}.{suffix}'"
        )
    if len(parts) < 3:
        raise FrameworkError(
            "Dot notation requires at least three parts (e.g., 'inputs.raw.filename')"
        )

    config = settings_read()
    directories = config.get("directories")
    if not isinstance(directories, dict):
        directories = {}
    # The longest run of leading parts that names a directory key selects the
    # folder, so inputs.private.raw.x uses inputs_private_raw. R joins only
    # the first two parts, so it cannot reach three-part keys.
    for end in range(len(parts) - 1, 1, -1):
        folder = directories.get("_".join(parts[:end]))
        if isinstance(folder, str) and folder:
            break
    else:
        available = ", ".join(k.replace("_", ".") for k in directories) or "none"
        raise FrameworkError(
            f"Unknown directory '{parts[0]}.{parts[1]}'. Use a configured directory.\n\n"
            f"Available directories:\n  {available}\n\n"
            "Examples:\n  data_save(df, 'inputs.raw.mydata')\n"
            "  data_save(df, 'outputs.private.results')"
        )
    type = type or "parquet"
    target = in_root(folder, project_root()) / f"{'_'.join(parts[end:])}.{type}"
    return target, type, True


def data_save(
    data: pd.DataFrame,
    path: str,
    type: str | None = None,
    delimiter: str = "comma",
    locked: bool = True,
    force: bool = False,
) -> pd.DataFrame:
    """Save a DataFrame and record its hash.

    Args:
        data: The data to save.
        path: A catalog name such as ``"inputs.intermediate.cleaned"`` or a
            file path ending in ``.csv`` or ``.parquet``. A name's leading
            parts select a folder from ``directories``: the longest run that
            names a key, such as ``inputs_intermediate`` or
            ``inputs_private_raw``. The rest becomes the file name. A name is
            also added to the data catalog, so ``data_read()`` finds it.
        type: ``"parquet"`` or ``"csv"``. Default: from the file extension,
            or Parquet for a catalog name.
        delimiter: Delimiter for CSV files: comma, tab, semicolon, or space.
        locked: Whether later reads must find the file unchanged.
        force: Create the folder if it does not exist.

    Returns:
        ``data``, so the call can end a pipeline.
    """
    if not isinstance(data, pd.DataFrame):
        raise FrameworkError("data_save() needs a pandas DataFrame")
    target, file_type, is_name = _save_target(path, type)
    if file_type == "rds":
        raise UnsupportedFormatError(
            ".rds files are R-only. Save as 'parquet' or 'csv' so both languages can read it."
        )
    if file_type not in _SAVE_TYPES:
        raise UnsupportedFormatError(f"data_save() writes csv or parquet, not {file_type}")

    folder = target.parent
    if not folder.is_dir():
        if not force:
            raise FrameworkError(
                f"Directory '{folder}' does not exist. Use force=True to create it."
            )
        folder.mkdir(parents=True, exist_ok=True)
        inform(f"Created directory: {folder}")

    if file_type == "csv":
        # "\n" on every platform, as readr writes, so hashes match R's.
        data.to_csv(target, sep=_separator(delimiter), index=False, lineterminator="\n")
    else:
        data.to_parquet(target, index=False)
    inform(f"Data saved to: {target}")

    root = project_root()
    digest = file_hash(target)
    db.set_data_record(
        path,
        path=relative_to_root(target, root),
        type=file_type,
        delimiter=delimiter if file_type == "csv" else None,
        locked=locked,
        hash=digest,
        root=root,
    )
    inform(f"Database updated: {path}")

    # Mirror the digest to the project's cloud ledger in live mode only;
    # framework.db stays the record.
    from framework.cloud import ledger_push_quietly

    ledger_push_quietly(path, digest, target)

    if is_name:
        try:
            data_add(target, name=path, type=file_type, delimiter=delimiter, locked=locked)
        except FrameworkError as error:
            warnings.warn(
                f"File saved but could not update data catalog for '{path}': {error}",
                FrameworkWarning,
                stacklevel=2,
            )
    return data


def _derive_name(file_path: Path, config: dict[str, Any], root: Path) -> str:
    directories = config.get("directories") or {}
    for key in ("inputs_raw", "inputs_intermediate", "inputs_final", "inputs_reference"):
        folder = directories.get(key) if isinstance(directories, dict) else None
        if not isinstance(folder, str):
            continue
        base = in_root(folder, root).resolve()
        if file_path.is_relative_to(base):
            return f"{key.replace('_', '.')}.{file_path.stem}"
    inform(
        f"Note: Using '{file_path.stem}' as data name. Consider specifying a more descriptive name."
    )
    return file_path.stem


def _catalog_set(name: str, entry: dict[str, Any]) -> None:
    """Write one catalog entry into settings.yml or the split file that holds ``data``."""
    settings_file = find_settings_file()
    if settings_file is None:
        raise SettingsError("Configuration file 'settings.yml' (or legacy config.yml) not found")
    raw = _yaml.read(settings_file) or {}
    environment = "default" if isinstance(raw.get("default"), dict) else None
    config = raw[environment] if environment else raw
    source = config.get("data")

    if isinstance(source, str):
        split_path = in_root(source, settings_file.parent)
        content = _yaml.read(split_path) if split_path.is_file() else None
        current = content.get("data", content) if isinstance(content, dict) else None
    else:
        split_path = None
        current = source
    if not isinstance(current, dict):
        current = {}

    # An entry already written with dotted keys is updated where it is.
    existing = catalog_locate(name, current)
    if existing is not None:
        existing[0][existing[1]] = entry
    else:
        node = current
        parts = name.split(".")
        for part in parts[:-1]:
            if not isinstance(node.get(part), dict):
                node[part] = {}
            node = node[part]
        node[parts[-1]] = entry

    if split_path is not None:
        _yaml.write(split_path, {"data": current})
    else:
        config["data"] = current
        _yaml.write(settings_file, raw)


def data_add(
    file_path: str | Path,
    name: str | None = None,
    type: str | None = None,
    delimiter: str = "comma",
    locked: bool = True,
    update_config: bool = True,
) -> dict[str, Any]:
    """Add an existing file to the data catalog and record its hash.

    Args:
        file_path: The file to add. It must exist.
        name: Catalog name. Default: from the folder (``inputs.raw.<file>``
            for a file in ``inputs_raw``), else the file name.
        type: File type. Default: from the extension.
        delimiter: Delimiter for CSV files.
        locked: Whether later reads must find the file unchanged.
        update_config: Write the entry to the settings, not just framework.db.

    Returns:
        The catalog entry. Its path is relative to the project root, so the
        project still works after it is moved or cloned.
    """
    path = Path(file_path).expanduser()
    if not path.is_file():
        raise DataNotFoundError(f"File not found: {file_path}")
    path = path.resolve()
    root = project_root()
    if type is None:
        extension = path.suffix.lower().lstrip(".")
        if extension not in _ADD_TYPES:
            raise UnsupportedFormatError(
                f"Cannot auto-detect type for extension: {extension}. Please specify 'type'."
            )
        type = _ADD_TYPES[extension]
    _separator(delimiter)
    if name is None:
        name = _derive_name(path, settings_read(), root)

    relative = relative_to_root(path, root)
    stored_delimiter = delimiter if type == "csv" else None
    db.set_data_record(
        name,
        path=relative,
        type=type,
        delimiter=stored_delimiter,
        locked=locked,
        hash=file_hash(path),
        root=root,
    )
    inform(f"Data registered in database: {name}")

    entry: dict[str, Any] = {"path": relative, "type": type}
    if stored_delimiter is not None:
        entry["delimiter"] = delimiter
    if locked:
        entry["locked"] = True
    if update_config:
        _catalog_set(name, entry)
        inform(f"Data spec added to config: {name}")
    inform(f'\nYou can now read this data with: data_read("{name}")')
    return entry
