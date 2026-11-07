"""Saving results: tables, figures, models, reports, and rendered notebooks.

Each save writes the file to the project's output folder for its kind and
records it in the ``results`` table of framework.db (named by its path from the
project root, with the file's hash), as in the R package.
Sensitive projects keep public and private outputs in separate folders.
"""

from __future__ import annotations

import os
import pickle
import shutil
import subprocess
import tempfile
import warnings
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pandas as pd

from framework import db
from framework._log import inform
from framework._paths import output_directory, relative_to_root
from framework.errors import FrameworkError, FrameworkWarning, UnsupportedFormatError
from framework.integrity import file_hash
from framework.settings import settings_read

_TABLE_FORMATS = {"csv": ".csv", "parquet": ".parquet", "xlsx": ".xlsx"}
_FIGURE_FORMATS = ("png", "pdf", "svg", "jpg", "jpeg", "tiff")
_NOTEBOOK_FORMATS = {"html": ".html", "pdf": ".pdf", "docx": ".docx"}
_RESULT_TYPES = ("table", "figure", "model", "report", "notebook")


def _folder_path(kind: str, public: bool) -> Path:
    """The output folder for a kind, without creating it."""
    try:
        config = settings_read()
    except FrameworkError:
        config = {}
    return output_directory(config, kind, public)


def _folder(kind: str, public: bool) -> Path:
    folder = _folder_path(kind, public)
    if not folder.is_dir():
        folder.mkdir(parents=True, exist_ok=True)
        inform(f"Creating {kind} directory: {folder}")
    return folder


def _check_overwrite(path: Path, overwrite: bool) -> None:
    if path.exists() and not overwrite:
        raise FrameworkError(f"File already exists: {path}. Use overwrite=True to replace.")


def _result_name(path: Path) -> str:
    """The results key: the path from the project root, so same-named files stay apart."""
    from framework.project import find_settings_file

    found = find_settings_file()
    return relative_to_root(path, found.parent) if found else path.resolve().as_posix()


def _record(path: Path, type: str, public: bool) -> None:
    """Record a saved output in the results table, keyed by its project-relative path.

    Older saves, from either language, keyed by file name. A row with only this file's
    name is renamed to the path the first time the file is saved again.
    """
    name = _result_name(path)
    digest = file_hash(path) if path.is_file() else None
    now = db.timestamp()
    with db.connect() as connection:
        if not connection.execute("SELECT id FROM results WHERE name = ?", (name,)).fetchone():
            connection.execute("UPDATE results SET name = ? WHERE name = ?", (name, path.name))
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
                'xlsx tables need openpyxl. Install with: pip install "framework-py[excel]"'
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
        plot: A matplotlib or seaborn figure (anything with ``savefig()``), a matplotlib
            Axes (its figure is saved), a plotnine plot (anything with ``save()``), or None
            for the current matplotlib figure.
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
    if not hasattr(figure, "savefig") and hasattr(figure, "get_figure"):
        # matplotlib Axes, which axes-level seaborn plots return, as publish() accepts
        figure = figure.get_figure() or figure
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


def _data_rows(model: Any) -> int:
    """How many data rows a model object carries, as far as can be told: a statsmodels
    result's estimation data, or a DataFrame in the object or among its attributes."""
    if isinstance(model, pd.DataFrame | pd.Series):
        return len(model)
    inner = getattr(model, "model", None)
    data = getattr(inner, "data", None)
    frame = getattr(data, "frame", None)
    if isinstance(frame, pd.DataFrame) and len(frame):
        return len(frame)
    endog = getattr(inner, "endog", None)
    if endog is not None and getattr(endog, "ndim", 0) >= 1 and len(endog):
        return len(endog)
    attributes = getattr(model, "__dict__", None)
    if isinstance(attributes, dict):
        for value in attributes.values():
            if isinstance(value, pd.DataFrame) and len(value):
                return len(value)
    return 0


def save_model(model: Any, name: str, public: bool = False, overwrite: bool = True) -> Path:
    """Save a fitted model to the models output folder with pickle (``<name>.pkl``).

    The file is recorded in the results table of framework.db. ``load_model()`` reads it
    back.

    Many fitted models keep the data they were fitted on: a statsmodels result holds its
    estimation data, for example. With ``public=True``, a model that holds data rows is
    saved with a ``FrameworkWarning``, since the rows go to the public folder with it. For
    statsmodels, ``result.remove_data()`` before saving drops them, except from a formula
    result (``smf.ols("y ~ x", data)``), which keeps its data frame to rebuild the formula
    when it is loaded.

    Args:
        model: The model, or any object pickle can save.
        name: The file name without extension.
        public: In a sensitive project, save to the public models folder.
        overwrite: Replace an existing file.

    Returns:
        The saved file.

    Raises:
        FrameworkError: When the file exists and ``overwrite`` is False.

    A model that cannot be pickled raises pickle's error and leaves any existing file and
    its record unchanged.
    """
    if public:
        rows = _data_rows(model)
        if rows:
            if getattr(getattr(model, "model", None), "formula", None):
                hint = (
                    " A statsmodels formula result keeps its data even after remove_data(), "
                    "to rebuild the formula on load. Save the estimates instead, for example "
                    "with save_table(), or fit without a formula and call remove_data()."
                )
            elif callable(getattr(model, "remove_data", None)):
                hint = " Call remove_data() on the result first to drop them."
            else:
                hint = " Remove them from the object first."
            warnings.warn(
                f"save_model(public=True): '{name}' holds {rows} data rows, which would be "
                f"saved to the public models folder with it.{hint}",
                FrameworkWarning,
                stacklevel=2,
            )
    path = _folder("models", public) / f"{name}.pkl"
    _check_overwrite(path, overwrite)
    # Pickle to a temporary file first, so a failed dump leaves any existing model intact
    descriptor, temp = tempfile.mkstemp(prefix=f".{name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            pickle.dump(model, handle)
        Path(temp).replace(path)
    except BaseException:
        Path(temp).unlink(missing_ok=True)
        raise
    inform(f"Saved model to {path}")
    _record(path, "model", public)
    return path


def _unpickle(path: Path) -> Any:
    """Read a pickle from inside a function.

    statsmodels rebuilds a formula model's design on load by evaluating the formula in a
    caller's frame. At the top level of a script there is no such frame and the load fails,
    so it always happens here. numpy and pandas are in this frame for formulas that call
    them, such as ``np.log(x)``.
    """
    import numpy as np  # noqa: F401
    import pandas as pd  # noqa: F401

    with path.open("rb") as handle:
        return pickle.load(handle)


def load_model(name: str, public: bool | None = None) -> Any:
    """Load a model ``save_model()`` saved.

    Loading is pickle's, so only load models from projects you trust: a pickle can run code.
    A statsmodels formula result loads anywhere, including the top level of a script.

    Args:
        name: The name it was saved under, without extension.
        public: Which models folder to read in a sensitive project: the public one (True)
            or the private one (False). Default: the private folder, then the public one.

    Returns:
        The model.

    Raises:
        FrameworkError: No model of that name, or only an R model (``.rds``), which Python
            cannot read.

    A file that no longer matches the hash ``save_model()`` recorded loads with a
    ``FrameworkWarning``.
    """
    sides = [public] if public is not None else [False, True]
    folders = list(dict.fromkeys(_folder_path("models", side) for side in sides))
    for folder in folders:
        path = folder / f"{name}.pkl"
        if path.is_file():
            break
    else:
        r_models = [
            folder / f"{name}.rds" for folder in folders if (folder / f"{name}.rds").is_file()
        ]
        if r_models:
            raise FrameworkError(
                f"{r_models[0]} is a model R saved (.rds), which Python cannot read."
            )
        looked = ", ".join(str(folder / f"{name}.pkl") for folder in folders)
        raise FrameworkError(f"No saved model named '{name}'. Looked for: {looked}")
    _check_recorded(path)
    model = _unpickle(path)
    inform(f"Loaded model from {path}")
    return model


def _check_recorded(path: Path) -> None:
    with db.connect() as connection:
        row = connection.execute(
            "SELECT hash FROM results WHERE name = ?", (_result_name(path),)
        ).fetchone()
    if row is not None and row["hash"] and row["hash"] != file_hash(path):
        warnings.warn(
            f"{path.name} changed since save_model() recorded it.", FrameworkWarning, stacklevel=3
        )


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


def _render_keeping_source(command: list[str], source: Path) -> subprocess.CompletedProcess[str]:
    """Run ``quarto render`` and put a ``.ipynb`` source back as it was.

    Quarto writes cell outputs back into a Jupyter notebook it executes (with
    ``--execute``), and saved notebooks must keep the source unchanged, so its bytes and
    times are restored after the render, even when the render fails or is interrupted.
    """
    if source.suffix.lower() != ".ipynb":
        return subprocess.run(command, capture_output=True, text=True, check=False)
    original = source.read_bytes()
    times = source.stat()
    try:
        return subprocess.run(command, capture_output=True, text=True, check=False)
    finally:
        try:
            changed = source.read_bytes() != original
        except OSError:
            changed = True
        if changed:
            source.write_bytes(original)
            os.utime(source, ns=(times.st_atime_ns, times.st_mtime_ns))


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
        embed_resources: For HTML, embed images and styles in one file. False overrides the
            project's ``_quarto.yml`` and saves the page's ``_files/`` folder beside it.
        quarto_args: Extra arguments for ``quarto render``. A ``.ipynb`` rendered with
            ``--execute`` is left unchanged: its outputs go into the saved file only.
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
        if format == "html":
            # -M wins over the project's _quarto.yml, which turns embedding on
            command += ["--embed-resources"] if embed_resources else ["-M", "embed-resources:false"]
        command.extend(quarto_args)
        inform(f"Rendering {source.name}...")
        result = _render_keeping_source(command, source)
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
        assets = outputs[0].with_name(f"{outputs[0].stem}_files")
        if assets.is_dir():
            # Without embedding, the page links to its supporting folder by the source's name
            target = destination.parent / assets.name
            shutil.rmtree(target, ignore_errors=True)
            shutil.copytree(assets, target)
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
