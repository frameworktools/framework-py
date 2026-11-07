from __future__ import annotations

from pathlib import Path

import pytest

import framework as fw


def test_path_is_absolute_from_the_root_wherever_python_runs(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (project / "notebooks").mkdir()
    monkeypatch.chdir(project / "notebooks")
    assert fw.path() == project
    assert fw.path("outputs/tables", "x.csv") == project / "outputs" / "tables" / "x.csv"
    assert fw.path("outputs", Path("tables")).is_absolute()


def test_path_refuses_absolute_parts(project: Path) -> None:
    with pytest.raises(ValueError, match="relative to the project root"):
        fw.path("/etc/passwd")
    with pytest.raises(ValueError, match="relative to the project root"):
        fw.path("C:\\data")


def test_path_outside_a_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    with pytest.raises(fw.ProjectNotFoundError):
        fw.path("x")
