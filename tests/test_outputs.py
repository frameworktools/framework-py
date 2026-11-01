from __future__ import annotations

import pickle
import shutil
from pathlib import Path

import matplotlib
import pandas as pd
import pytest

import framework as fw

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
    assert listed["name"].tolist() == ["q.csv"]


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
    assert listed["name"].tolist() == ["second.pkl", "first.csv"]
    assert fw.result_list(type="table")["name"].tolist() == ["first.csv"]
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
