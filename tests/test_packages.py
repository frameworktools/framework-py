from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

import framework as fw
from framework import packages

PYPROJECT = """\
[project]
name = "test"
version = "0.1.0"
# Packages this analysis uses.
dependencies = [
    "framework-py",
    "pandas>=2",
]

[tool.uv]
package = false
"""


@pytest.fixture
def calls(project: Path, monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    (project / "pyproject.toml").write_text(PYPROJECT, encoding="utf-8")
    recorded: list[list[str]] = []

    def run(args: list[str], root: Path) -> int:
        recorded.append(args)
        return 0

    monkeypatch.setattr(packages, "_run", run)
    monkeypatch.setattr(packages, "_uv", lambda: "uv")
    return recorded


def test_package_add_with_uv(calls: list[list[str]]) -> None:
    fw.package_add("statsmodels>=0.14")
    fw.package_add("pytest", dev=True, install=False)
    assert calls == [
        ["uv", "add", "statsmodels>=0.14"],
        ["uv", "add", "pytest", "--dev", "--no-sync"],
    ]


def test_package_add_without_uv(
    project: Path, calls: list[list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(packages, "_uv", lambda: None)
    fw.package_add("statsmodels>=0.14")
    text = (project / "pyproject.toml").read_text(encoding="utf-8")
    assert '    "pandas>=2",\n    "statsmodels>=0.14",\n]' in text
    assert "# Packages this analysis uses." in text
    assert calls == [[sys.executable, "-m", "pip", "install", "statsmodels>=0.14"]]
    fw.package_add("Pandas", install=False)
    assert (project / "pyproject.toml").read_text(encoding="utf-8") == text
    with pytest.raises(fw.FrameworkError, match="need uv"):
        fw.package_add("pytest", dev=True)


def test_add_to_an_empty_list(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (project / "pyproject.toml").write_text(
        '[project]\nname = "t"\ndependencies = []\n', encoding="utf-8"
    )
    monkeypatch.setattr(packages, "_uv", lambda: None)
    fw.package_add("numpy", install=False)
    assert fw.packages_list()["requirement"].tolist() == ["numpy"]


def test_packages_list(calls: list[list[str]]) -> None:
    listed = fw.packages_list()
    assert listed["package"].tolist() == ["framework-py", "pandas"]
    assert listed.set_index("package").loc["pandas", "installed"] is not None


def test_uv_commands(project: Path, calls: list[list[str]]) -> None:
    fw.packages_install()
    fw.packages_snapshot()
    fw.packages_update()
    fw.packages_update(["pandas", "numpy"])
    with pytest.raises(fw.FrameworkError, match=r"No uv\.lock"):
        fw.packages_restore()
    (project / "uv.lock").write_text("", encoding="utf-8")
    fw.packages_restore()
    assert calls == [
        ["uv", "sync"],
        ["uv", "lock"],
        ["uv", "lock", "--upgrade"],
        ["uv", "sync"],
        ["uv", "lock", "--upgrade-package", "pandas", "--upgrade-package", "numpy"],
        ["uv", "sync"],
        ["uv", "sync", "--frozen"],
    ]


def test_failures_and_missing_uv(
    project: Path, calls: list[list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(packages, "_run", lambda args, root: 1)
    with pytest.raises(fw.FrameworkError, match="uv sync failed"):
        fw.packages_install()
    monkeypatch.setattr(packages, "_uv", lambda: None)
    with pytest.raises(fw.FrameworkError, match="needs uv"):
        fw.packages_snapshot()


def test_packages_status_without_uv(
    project: Path,
    calls: list[list[str]],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(packages, "_uv", lambda: None)
    status = fw.packages_status()
    assert status.lock_current is None
    assert "pandas" not in status.missing
    assert "uv is not installed" in capsys.readouterr().out


@pytest.mark.skipif(shutil.which("uv") is None, reason="uv is not installed")
def test_real_uv_lock_and_status(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """uv itself, on a project with no dependencies so nothing is downloaded."""
    version = f"{sys.version_info.major}.{sys.version_info.minor}"
    (project / "pyproject.toml").write_text(
        f'[project]\nname = "t"\nversion = "0"\nrequires-python = ">={version}"\n'
        "dependencies = []\n\n[tool.uv]\npackage = false\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("UV_OFFLINE", "1")
    monkeypatch.setenv("UV_PYTHON", sys.executable)
    assert fw.packages_status().lock_current is False
    fw.packages_snapshot()
    assert (project / "uv.lock").is_file()
    assert fw.packages_status().lock_current is True


def test_no_pyproject(project: Path) -> None:
    with pytest.raises(fw.FrameworkError, match=r"No pyproject\.toml"):
        fw.packages_list()
