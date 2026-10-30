"""Scratch files: quick captures of objects for a look outside Python."""

from __future__ import annotations

import pickle
from datetime import datetime
from pathlib import Path
from typing import Any, TypeVar, cast

import pandas as pd

from framework import _yaml
from framework._log import inform
from framework._paths import in_root
from framework.errors import FrameworkError
from framework.project import project_root
from framework.settings import settings_read

T = TypeVar("T")

_FORMATS = {"text": ".txt", "csv": ".csv", "tsv": ".tsv", "pickle": ".pkl"}
_FROM_EXTENSION = {".txt": "text", ".csv": "csv", ".tsv": "tsv", ".pkl": "pickle"}


def scratch_dir() -> Path:
    """The scratch folder: ``directories.scratch`` in the settings."""
    config = settings_read()
    directories = config.get("directories") or {}
    options = config.get("options") or {}
    data_options = options.get("data") if isinstance(options, dict) else None
    folder = (
        (directories.get("scratch") if isinstance(directories, dict) else None)
        or (data_options.get("scratch_dir") if isinstance(data_options, dict) else None)
        or "outputs/private/scratch"
    )
    return in_root(folder, project_root())


def _text(value: Any) -> str:
    if isinstance(value, list | tuple | set) and all(
        not isinstance(item, dict | list | tuple | set) for item in value
    ):
        return "".join(f"{item}\n" for item in value)
    if isinstance(value, dict | list | tuple):
        return f"# {type(value).__name__} with {len(value)} items\n" + _yaml.dump(value)
    return f"{value}\n"


def scratch_capture(
    x: T,
    name: str | None = None,
    to: str | None = None,
    location: str | Path | None = None,
    n: int | None = None,
) -> T:
    """Write an object to the scratch folder and return it unchanged.

    Args:
        x: The object. DataFrames are written as TSV by default, anything
            else as text.
        name: File name. An extension (.txt, .csv, .tsv, .pkl) sets the format.
            Default: ``capture_<timestamp>``.
        to: ``"text"``, ``"csv"``, ``"tsv"``, or ``"pickle"``.
        location: Folder to write to. Default: the scratch folder.
        n: For a DataFrame, write only the first ``n`` rows.
    """
    folder = Path(location) if location is not None else scratch_dir()
    folder.mkdir(parents=True, exist_ok=True)
    if name is None:
        name = f"capture_{datetime.now():%Y%m%d_%H%M%S}"
    suffix = Path(name).suffix
    if suffix in _FROM_EXTENSION:
        to = _FROM_EXTENSION[suffix]
        name = Path(name).stem
    is_frame = isinstance(x, pd.DataFrame)
    to = to or ("tsv" if is_frame else "text")
    if to not in _FORMATS:
        raise FrameworkError(f"'to' must be one of: {', '.join(_FORMATS)}")

    path = folder / f"{name}{_FORMATS[to]}"
    if to == "pickle":
        with path.open("wb") as handle:
            pickle.dump(x, handle)
    elif is_frame:
        if to == "text":
            raise FrameworkError("DataFrames can be saved as 'csv', 'tsv', or 'pickle'")
        frame = cast(pd.DataFrame, x)
        if n is not None:
            frame = frame.head(n)
        frame.to_csv(path, sep="," if to == "csv" else "\t", index=False, lineterminator="\n")
    elif to == "text":
        path.write_text(_text(x), encoding="utf-8")
    else:
        raise FrameworkError("Only DataFrames can be saved as 'csv' or 'tsv'")
    inform(f"Captured to {path}")
    return x


def scratch_clean() -> None:
    """Delete the files in the scratch folder. Subfolders are left alone, as in R."""
    folder = scratch_dir()
    if not folder.is_dir():
        inform(f"Scratch directory does not exist: {folder}")
        return
    for path in folder.iterdir():
        if path.is_file() or path.is_symlink():
            path.unlink()
    inform(f"Scratch directory cleaned: {folder}")
