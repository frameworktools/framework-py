"""Saving results: tables, figures, models, reports, and rendered notebooks.

Each save writes the file to the project's output folder for its kind and
records it in the ``results`` table of framework.db (named by file name, with
the file's hash), the same way R does. Sensitive projects keep public and
private outputs in separate folders.
"""

from __future__ import annotations

import pickle
import shutil
import subprocess
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pandas as pd

from framework import db
from framework._log import inform
from framework._paths import output_directory
from framework.errors import FrameworkError, UnsupportedFormatError
from framework.integrity import file_hash
from framework.settings import settings_read

_TABLE_FORMATS = {"csv": ".csv", "parquet": ".parquet", "xlsx": ".xlsx"}
_FIGURE_FORMATS = ("png", "pdf", "svg", "jpg", "jpeg", "tiff")
_NOTEBOOK_FORMATS = {"html": ".html", "pdf": ".pdf", "docx": ".docx"}
_RESULT_TYPES = ("table", "figure", "model", "report", "notebook")


def _folder(kind: str, public: bool) -> Path:
    try:
        config = settings_read()
    except FrameworkError:
        config = {}
    folder = output_directory(config, kind, public)
    if not folder.is_dir():
        folder.mkdir(parents=True, exist_ok=True)
        inform(f"Creating {kind} directory: {folder}")
    return folder


def _check_overwrite(path: Path, overwrite: bool) -> None:
    if path.exists() and not overwrite:
        raise FrameworkError(f"File already exists: {path}. Use overwrite=True to replace.")


def _record(path: Path, type: str, public: bool) -> None:
    """Record a saved output in the results table, as R's ``.save_result()`` does."""
    name = path.name
    digest = file_hash(path) if path.is_file() else None
    now = db.timestamp()
    with db.connect() as connection:
        if connection.execute("SELECT id FROM results WHERE name = ?", (name,)).fetchone():
            connection.execute(
                "UPDATE results SET type = ?, public = ?, comment = ?, hash = ?, updated_at = ? "
                "WHERE name = ?",
                (type, int(public), None, digest, now, name),
            )
        else:
            connection.execute(
                "INSERT INTO results (name, type, public, blind, comment, hash, created_at, "
                "updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (name, type, int(public), 0, None, digest, now, now),
            )


def save_table(
    data: pd.DataFrame,
    name: str,
    format: str = "csv",
    public: bool = False,
    overwrite: bool = True,
    **kwargs: Any,
) -> Path:
    """Save a table to the tables output folder.

    Args:
        data: The table.
        name: File name without extension.
        format: ``"csv"``, ``"parquet"``, or ``"xlsx"`` (needs the ``excel`` extra).
        public: In a sensitive project, save to the public tables folder.
        overwrite: Replace an existing file.
        **kwargs: Passed to the pandas writer.

    Returns:
        The saved file.
    """
    if format == "rds":
        raise UnsupportedFormatError(".rds tables are R-only. Use csv, parquet, or xlsx.")
    if format not in _TABLE_FORMATS:
        raise FrameworkError(f"format must be one of {', '.join(_TABLE_FORMATS)}")
    path = _folder("tables", public) / f"{name}{_TABLE_FORMATS[format]}"
    _check_overwrite(path, overwrite)
    if format == "csv":
        data.to_csv(path, index=False, **{"lineterminator": "\n", **kwargs})
    elif format == "parquet":
        data.to_parquet(path, index=False, **kwargs)
    else:
        try:
            import openpyxl  # noqa: F401
        except ImportError as error:
            raise FrameworkError(
                "xlsx tables need openpyxl. Install with: pip install framework-py[excel]"
            ) from error
        data.to_excel(path, index=False, **kwargs)
    inform(f"Saved table to {path}")
    _record(path, "table", public)
    return path


def _current_figure() -> Any:
    try:
        import matplotlib.pyplot as plt
    except ImportError as error:
        raise FrameworkError(
            "save_figure(None, ...) saves the current matplotlib figure, "
            "but matplotlib is not installed."
        ) from error
    return plt.gcf()


def save_figure(
    plot: Any,
    name: str,
    format: str = "png",
    width: float = 8,
    height: float = 6,
    dpi: int = 300,
    public: bool = False,
    overwrite: bool = True,
    **kwargs: Any,
) -> Path:
    """Save a figure to the figures output folder.

    Args:
        plot: A matplotlib or seaborn figure (anything with ``savefig()``), a
            plotnine plot (anything with ``save()``), or None for the current
            matplotlib figure.
        name: File name without extension.
        format: png, pdf, svg, jpg, jpeg, or tiff.
        width: Width in inches.
        height: Height in inches.
        dpi: Resolution for raster formats.
        public: In a sensitive project, save to the public figures folder.
        overwrite: Replace an existing file.
        **kwargs: Passed to ``savefig()`` or ``save()``.

    Returns:
        The saved file.
    """
    if format not in _FIGURE_FORMATS:
        raise FrameworkError(f"format must be one of {', '.join(_FIGURE_FORMATS)}")
    if width < 0.1 or height < 0.1 or dpi < 1:
        raise FrameworkError("width and height must be at least 0.1 and dpi at least 1")
    figure = _current_figure() if plot is None else plot
    path = _folder("figures", public) / f"{name}.{format}"
    _check_overwrite(path, overwrite)

    if hasattr(figure, "savefig"):
        target = getattr(figure, "figure", figure)
        if hasattr(target, "set_size_inches"):
            target.set_size_inches(width, height)
        figure.savefig(path, dpi=dpi, format=format, **kwargs)
    elif hasattr(figure, "save"):
        figure.save(path, width=width, height=height, dpi=dpi, units="in", verbose=False, **kwargs)
    else:
        raise FrameworkError(
            "plot must have a savefig() method (matplotlib, seaborn) or a save() method "
            "(plotnine), or be None to save the current matplotlib figure"
        )
    inform(f"Saved figure to {path}")
    _record(path, "figure", public)
    return path


def save_model(model: Any, name: str, public: bool = False, overwrite: bool = True) -> Path:
    """Save a fitted model to the models output folder with pickle (``<name>.pkl``).

    The file is recorded in the results table of framework.db.

    Args:
        model: The model, or any object pickle can save.
        name: The file name without extension.
        public: In a sensitive project, save to the public models folder.
        overwrite: Replace an existing file.

    Returns:
        The saved file.

    Raises:
        FrameworkError: When the file exists and ``overwrite`` is False.
    """
    path = _folder("models", public) / f"{name}.pkl"
    _check_overwrite(path, overwrite)
    with path.open("wb") as handle:
        pickle.dump(model, handle)
    inform(f"Saved model to {path}")
    _record(path, "model", public)
    return path


def save_report(
    file: str | Path,
    name: str | None = None,
    public: bool = False,
    overwrite: bool = True,
    move: bool = False,
) -> Path:
    """Copy (or move) a finished report into the reports output folder.

    Args:
        file: The report file.
        name: New file name without extension. Default: keep the name.
        public: In a sensitive project, save to the public reports folder.
        overwrite: Replace an existing file.
        move: Move the file instead of copying it.
    """
    source = Path(file)
    if not source.is_file():
        raise FrameworkError(f"File not found: {source}")
    folder = _folder("reports", public)
    destination = folder / (source.name if name is None else f"{name}{source.suffix}")
    _check_overwrite(destination, overwrite)
    if move:
        shutil.move(source, destination)
        inform(f"Moved report to {destination}")
    else:
        shutil.copyfile(source, destination)
        inform(f"Saved report to {destination}")
    _record(destination, "report", public)
    return destination


def save_notebook(
    file: str | Path,
    name: str | None = None,
    format: str = "html",
    public: bool = False,
    overwrite: bool = True,
    embed_resources: bool = True,
    quarto_args: Sequence[str] = (),
) -> Path:
    """Render a Quarto notebook or Jupyter notebook and save the result.

    Rendering uses the ``quarto`` command, which must be installed.

    Args:
        file: A ``.qmd`` or ``.ipynb`` file.
        name: Output file name without extension. Default: the notebook's name.
        format: ``"html"``, ``"pdf"``, or ``"docx"``.
        public: In a sensitive project, save to the public notebooks folder.
        overwrite: Replace an existing file.
        embed_resources: For HTML, embed images and styles in one file.
        quarto_args: Extra arguments for ``quarto render``.
    """
    source = Path(file)
    if not source.is_file() or source.suffix.lower() not in (".qmd", ".ipynb"):
        raise FrameworkError(f"save_notebook() needs an existing .qmd or .ipynb file: {source}")
    if format not in _NOTEBOOK_FORMATS:
        raise FrameworkError(f"format must be one of {', '.join(_NOTEBOOK_FORMATS)}")
    quarto = shutil.which("quarto")
    if quarto is None:
        raise FrameworkError(
            "Quarto not found. Install it from https://quarto.org/docs/get-started/"
        )

    folder = _folder("notebooks", public)
    destination = folder / f"{name or source.stem}{_NOTEBOOK_FORMATS[format]}"
    _check_overwrite(destination, overwrite)

    with tempfile.TemporaryDirectory(prefix="save_notebook_") as temp:
        command = [quarto, "render", str(source.resolve()), "--output-dir", temp, "--to", format]
        if embed_resources and format == "html":
            command.append("--embed-resources")
        command.extend(quarto_args)
        inform(f"Rendering {source.name}...")
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            raise FrameworkError(f"Quarto render failed\n{result.stdout}{result.stderr}")
        # Inside a Quarto project, the output keeps the file's folder under
        # --output-dir (notebooks/x.qmd -> <temp>/notebooks/x.html)
        extension = _NOTEBOOK_FORMATS[format]
        outputs = sorted(Path(temp).rglob(f"{source.stem}{extension}")) or sorted(
            Path(temp).rglob(f"*{extension}")
        )
        if not outputs:
            raise FrameworkError(f"No {format} output found after rendering")
        shutil.copyfile(outputs[0], destination)
    inform(f"Saved notebook to {destination}")
    _record(destination, "notebook", public)
    return destination


def result_list(type: str | None = None, public: bool | None = None) -> pd.DataFrame:
    """Saved results, newest first.

    Columns: name, type, public, comment, hash, created_at, and updated_at.
    Timestamps are local ``%Y-%m-%d %H:%M:%S`` strings, as in R.

    Args:
        type: Only this kind: table, figure, model, report, or notebook.
        public: Only public (True) or private (False) results.
    """
    if type is not None and type not in _RESULT_TYPES:
        raise FrameworkError(f"type must be one of {', '.join(_RESULT_TYPES)}")
    columns = ["name", "type", "public", "comment", "hash", "created_at", "updated_at"]
    query = (
        "SELECT name, type, public, comment, hash, created_at, updated_at "
        "FROM results WHERE deleted_at IS NULL"
    )
    params: list[Any] = []
    if type is not None:
        query += " AND type = ?"
        params.append(type)
    if public is not None:
        query += " AND public = ?"
        params.append(int(public))
    with db.connect() as connection:
        rows = connection.execute(query, params).fetchall()

    records = []
    for row in rows:
        created = db.parse_timestamp(row["created_at"])
        updated = db.parse_timestamp(row["updated_at"])
        records.append(
            {
                "name": row["name"],
                "type": row["type"],
                "public": bool(row["public"]),
                "comment": row["comment"],
                "hash": row["hash"],
                "created_at": created.strftime("%Y-%m-%d %H:%M:%S") if created else None,
                "updated_at": updated.strftime("%Y-%m-%d %H:%M:%S") if updated else None,
                "_sort": updated.timestamp() if updated else float("-inf"),
            }
        )
    records.sort(key=lambda record: record["_sort"], reverse=True)
    for record in records:
        del record["_sort"]
    return pd.DataFrame(records, columns=columns)
