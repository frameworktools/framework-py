from __future__ import annotations

from pathlib import Path

import pytest

import framework as fw


def test_project_path_is_absolute_from_the_root_wherever_python_runs(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (project / "notebooks").mkdir()
    monkeypatch.chdir(project / "notebooks")
    assert fw.project_path() == project
    assert fw.project_path("outputs/tables", "x.csv") == project / "outputs" / "tables" / "x.csv"
    assert fw.project_path("outputs", Path("tables")).is_absolute()


def test_path_is_an_alias_of_project_path(project: Path) -> None:
    assert fw.path("outputs", "x.csv") == fw.project_path("outputs", "x.csv")
    with pytest.raises(ValueError, match="relative to the project root"):
        fw.path("/etc/passwd")


@pytest.mark.parametrize("part", ["/etc/passwd", "C:\\data", "C:data", "\\data", "~", "~/x"])
def test_project_path_refuses_absolute_parts(project: Path, part: str) -> None:
    message = f"project_path() parts are relative to the project root, but '{part}' is absolute"
    with pytest.raises(ValueError) as error:
        fw.project_path(part)
    assert str(error.value) == message


def test_project_path_outside_a_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    with pytest.raises(fw.ProjectNotFoundError):
        fw.project_path("x")
