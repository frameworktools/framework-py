from __future__ import annotations

import pickle
import shutil
import sqlite3
from contextlib import closing
from pathlib import Path

import matplotlib
import pandas as pd
import pytest

import framework as fw
from framework.integrity import file_hash

matplotlib.use("Agg")
import matplotlib.pyplot as plt

FRAME = pd.DataFrame({"x": [1, 2]})


@pytest.fixture
def sensitive(project: Path) -> Path:
    (project / "settings.yml").write_text(
        "default:\n  project_type: project_sensitive\n"
        "  directories: {outputs_private_tables: outputs/private/tables}\n",
        encoding="utf-8",
    )
    return project


def test_save_table_formats(project: Path) -> None:
    assert fw.save_table(FRAME, "t") == project / "outputs/tables/t.csv"
    assert fw.save_table(FRAME, "t", format="parquet").suffix == ".parquet"
    assert fw.save_table(FRAME, "t", format="xlsx").suffix == ".xlsx"
    with pytest.raises(fw.UnsupportedFormatError):
        fw.save_table(FRAME, "t", format="rds")
    with pytest.raises(fw.FrameworkError, match="already exists"):
        fw.save_table(FRAME, "t", overwrite=False)


def test_sensitive_projects_split_public_and_private(sensitive: Path) -> None:
    assert fw.save_table(FRAME, "p") == sensitive / "outputs/private/tables/p.csv"
    assert fw.save_table(FRAME, "q", public=True) == sensitive / "outputs/public/tables/q.csv"
    listed = fw.result_list(public=True)
    assert listed["name"].tolist() == ["outputs/public/tables/q.csv"]


def test_same_named_outputs_keep_separate_records(sensitive: Path) -> None:
    # Audit 2026-11-05: records were keyed by file name, so the public save took over
    # the private file's record and hash
    private = fw.save_table(FRAME, "summary")
    public = fw.save_table(FRAME.head(1), "summary", public=True)
    listed = fw.result_list().set_index("name")
    assert set(listed.index) == {
        "outputs/private/tables/summary.csv",
        "outputs/public/tables/summary.csv",
    }
    assert listed.loc["outputs/private/tables/summary.csv", "public"] == False  # noqa: E712
    assert listed.loc["outputs/private/tables/summary.csv", "hash"] == file_hash(private)
    assert listed.loc["outputs/public/tables/summary.csv", "hash"] == file_hash(public)


def test_a_record_named_by_file_name_moves_to_the_path(project: Path) -> None:
    path = fw.save_table(FRAME, "old")
    with closing(sqlite3.connect(project / "framework.db")) as connection, connection:
        connection.execute("UPDATE results SET name = 'old.csv'")
    fw.save_table(FRAME, "old")
    assert fw.result_list()["name"].tolist() == ["outputs/tables/old.csv"]
    assert path.is_file()


def test_save_figure(project: Path) -> None:
    figure, axis = plt.subplots()
    axis.plot([1, 2], [3, 4])
    path = fw.save_figure(figure, "line", width=4, height=3, dpi=50)
    assert path == project / "outputs/figures/line.png"
    assert path.read_bytes()[:4] == b"\x89PNG"
    assert tuple(figure.get_size_inches()) == (4, 3)
    assert fw.save_figure(None, "current", format="svg").suffix == ".svg"
    plt.close("all")


def test_save_figure_with_a_save_method(project: Path) -> None:
    class Plot:
        def save(self, path: Path, **kwargs: object) -> None:
            Path(path).write_text(str(sorted(kwargs)), encoding="utf-8")

    path = fw.save_figure(Plot(), "plotnine", format="pdf")
    assert "units" in path.read_text(encoding="utf-8")


def test_save_figure_rejects_other_objects(project: Path) -> None:
    with pytest.raises(fw.FrameworkError, match="savefig"):
        fw.save_figure(object(), "nothing")
    with pytest.raises(fw.FrameworkError, match="format"):
        fw.save_figure(None, "x", format="bmp")


def test_save_model(project: Path) -> None:
    path = fw.save_model({"coef": [1, 2]}, "fit")
    assert path == project / "outputs/models/fit.pkl"
    with path.open("rb") as handle:
        assert pickle.load(handle) == {"coef": [1, 2]}


def test_a_failed_pickle_keeps_the_existing_model(project: Path) -> None:
    # Audit 2026-11-05: the file was opened for writing first, so a failed dump left
    # 0 bytes and framework.db kept the old hash
    path = fw.save_model({"coef": [1, 2]}, "fit")
    before = path.read_bytes()
    recorded = fw.result_list()["hash"].tolist()
    with pytest.raises((pickle.PicklingError, AttributeError, TypeError)):
        fw.save_model(lambda x: x, "fit")
    assert path.read_bytes() == before
    assert fw.result_list()["hash"].tolist() == recorded == [file_hash(path)]
    assert sorted(p.name for p in path.parent.iterdir()) == ["fit.pkl"]


def test_save_report_copy_and_move(project: Path) -> None:
    report = project / "draft.pdf"
    report.write_bytes(b"%PDF")
    copied = fw.save_report(report, name="final")
    assert copied == project / "outputs/reports/final.pdf" and report.exists()
    moved = fw.save_report(report, move=True)
    assert moved.name == "draft.pdf" and not report.exists()
    with pytest.raises(fw.FrameworkError, match="File not found"):
        fw.save_report(report)


def test_result_list(project: Path) -> None:
    fw.save_table(FRAME, "first")
    fw.save_model(1, "second")
    listed = fw.result_list()
    assert listed["name"].tolist() == ["outputs/models/second.pkl", "outputs/tables/first.csv"]
    assert fw.result_list(type="table")["name"].tolist() == ["outputs/tables/first.csv"]
    assert listed["public"].tolist() == [False, False]
    with pytest.raises(fw.FrameworkError, match="type must be"):
        fw.result_list(type="other")


def test_save_notebook_needs_quarto(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    notebook = project / "analysis.qmd"
    notebook.write_text("# Analysis\n", encoding="utf-8")
    monkeypatch.setattr(shutil, "which", lambda _: None)
    with pytest.raises(fw.FrameworkError, match="Quarto not found"):
        fw.save_notebook(notebook)
    with pytest.raises(fw.FrameworkError, match=r"\.qmd or \.ipynb"):
        fw.save_notebook(project / "settings.yml")


@pytest.mark.skipif(shutil.which("quarto") is None, reason="quarto is not installed")
def test_save_notebook_renders(project: Path) -> None:
    notebook = project / "analysis.qmd"
    notebook.write_text("---\ntitle: Analysis\n---\n\nHello.\n", encoding="utf-8")
    path = fw.save_notebook(notebook)
    assert path == project / "outputs/notebooks/analysis.html"
    assert "Hello." in path.read_text(encoding="utf-8")


@pytest.mark.skipif(shutil.which("quarto") is None, reason="quarto is not installed")
def test_save_notebook_in_a_project_with_a_root_quarto_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = fw.new(
        "Rendered", tmp_path / "rendered", "project", workflow="quarto", interactive=False
    )
    assert not (root / "notebooks/_quarto.yml").exists()
    notebook = root / "notebooks/summary.qmd"
    notebook.write_text("---\ntitle: Summary\n---\n\nHello.\n", encoding="utf-8")
    monkeypatch.chdir(root)
    path = fw.save_notebook(notebook)
    assert path == root / "outputs/notebooks/summary.html"
    assert "Hello." in path.read_text(encoding="utf-8")


def test_save_figure_takes_an_axes(project: Path) -> None:
    # Audit 2026-11-05: Axes, which axes-level seaborn plots return, were rejected
    _, axis = plt.subplots()
    axis.plot([1, 2], [3, 4])
    path = fw.save_figure(axis, "from-axes", dpi=20)
    assert path.read_bytes()[:4] == b"\x89PNG"
    plt.close("all")


def test_save_notebook_without_embedding_overrides_the_project(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Audit 2026-11-05: embed_resources=False had no effect, because the project's
    # _quarto.yml turns embedding on
    import subprocess

    from framework import outputs

    commands: list[list[str]] = []

    def render(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        out = Path(command[command.index("--output-dir") + 1]) / "notebooks"
        (out / "report_files").mkdir(parents=True)
        (out / "report.html").write_text("<p>r</p>", encoding="utf-8")
        (out / "report_files" / "style.css").write_text("p{}", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(outputs.shutil, "which", lambda name: "/usr/bin/quarto")
    monkeypatch.setattr(outputs.subprocess, "run", render)
    notebook = project / "notebooks" / "report.qmd"
    notebook.parent.mkdir()
    notebook.write_text("---\ntitle: R\n---\n", encoding="utf-8")
    saved = fw.save_notebook(notebook, name="final", embed_resources=False)
    assert commands[-1][-2:] == ["-M", "embed-resources:false"]
    assert (saved.parent / "report_files" / "style.css").is_file()
    fw.save_notebook(notebook)
    assert "--embed-resources" in commands[-1]


def test_save_notebook_leaves_an_executed_ipynb_unchanged(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Hand testing 2026-11-07: quarto_args=["--execute"] wrote the cell outputs back
    # into the source notebook
    import subprocess

    from framework import outputs

    notebook = project / "analysis.ipynb"
    original = '{"cells": [], "metadata": {}, "nbformat": 4, "nbformat_minor": 5}\n'
    notebook.write_text(original, encoding="utf-8")

    def render(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        notebook.write_text('{"cells": [{"outputs": ["secret"]}]}', encoding="utf-8")
        out = Path(command[command.index("--output-dir") + 1])
        (out / "analysis.html").write_text("<p>secret</p>", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(outputs.shutil, "which", lambda name: "/usr/bin/quarto")
    monkeypatch.setattr(outputs.subprocess, "run", render)
    saved = fw.save_notebook(notebook, quarto_args=["--execute"])
    assert "secret" in saved.read_text(encoding="utf-8")
    assert notebook.read_text(encoding="utf-8") == original
