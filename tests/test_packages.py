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


def test_packages_status_without_a_lock_file_names_the_real_reason(
    project: Path,
    calls: list[list[str]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Audit 2026-11-05: with uv installed and no uv.lock, it said "uv is not installed"
    status = fw.packages_status()
    assert status.lock_current is False
    out = capsys.readouterr().out
    assert "uv is not installed" not in out
    assert "Lock file: missing (run packages_snapshot())" in out
    assert "Environment: unknown (no uv.lock to compare with)" in out


def test_package_set_add_with_uv(calls: list[list[str]]) -> None:
    result = fw.package_set_add("python-data-analysis", install=False)
    assert "pandas" in result["present"]
    assert "pandas" not in result["added"]
    assert result["added"]
    assert calls == [["uv", "add", *result["added"], "--no-sync"]]


def test_package_set_add_without_uv(
    project: Path, calls: list[list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(packages, "_uv", lambda: None)
    result = fw.package_set_add("fastapi-python")
    assert result == {"added": ["fastapi", "uvicorn", "pydantic", "httpx", "pytest"], "present": []}
    assert fw.packages_list()["package"].tolist() == [
        "framework-py",
        "pandas",
        "fastapi",
        "uvicorn",
        "pydantic",
        "httpx",
        "pytest",
    ]
    assert calls == [[sys.executable, "-m", "pip", "install", *result["added"]]]
    # A second time adds and installs nothing
    assert fw.package_set_add("fastapi-python")["added"] == []
    assert len(calls) == 1


def test_package_set_add_keeps_a_github_repo(
    project: Path, calls: list[list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    definition = {
        "id": "gh",
        "name": "GitHub set",
        "layer": {
            "packages": {
                "default_packages": {"mytools": {"source": "github", "repo": "someone/mytools"}}
            }
        },
    }
    monkeypatch.setattr(packages, "_package_set", lambda set_id: definition)
    fw.package_set_add("gh")
    assert calls == [["uv", "add", "mytools @ git+https://github.com/someone/mytools"]]


def test_package_set_add_rejects_unknown_and_r_sets(calls: list[list[str]]) -> None:
    with pytest.raises(
        fw.FrameworkError, match=r"Unknown package set: nope\. Available: .*fastapi"
    ):
        fw.package_set_add("nope")
    with pytest.raises(fw.FrameworkError, match=r"isn't available for Python projects\. Available"):
        fw.package_set_add("bayes-r")
    assert calls == []


def test_package_set_add_from_the_cloud(
    calls: list[list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    from framework import cloud

    sent: list[dict[str, object]] = []

    def resolve(body: dict[str, object], token: str, url: str) -> tuple[dict[str, object], None]:
        sent.append(body)
        layers = [
            {"kind": "base", "key": "framework/project", "layer": {}},
            {
                "kind": "facet",
                "key": "@me/lab",
                "layer": {"packages": {"default_packages": {"labtools": {"source": "pypi"}}}},
            },
        ]
        return {"layers": layers}, None

    monkeypatch.setattr(cloud, "_require_token", lambda url: "token")
    monkeypatch.setattr(cloud, "resolve_choices", resolve)
    assert fw.package_set_add("@me/lab", install=False)["added"] == ["labtools"]
    assert sent == [{"language": "python", "package_sets": ["@me/lab"]}]
    assert calls == [["uv", "add", "labtools", "--no-sync"]]
    with pytest.raises(fw.FrameworkError, match="isn't available for Python projects"):
        fw.package_set_add("@me/r-only")


def test_package_sets_list() -> None:
    sets = fw.package_sets_list()
    assert list(sets.columns) == ["id", "name", "description", "packages"]
    assert {"python-data-analysis", "fastapi-python"} <= set(sets["id"])
    assert "bayes-r" not in set(sets["id"])


AGENTS = """\
# Test

## Framework Environment <!-- @framework:regenerate -->

Kept.

## Installed Packages <!-- @framework:regenerate -->

**Dependencies** (in pyproject.toml): framework-py

Add packages with `fw.package_add("name")`.

## Project Notes

My notes.
"""


def test_package_add_refreshes_the_agents_md_package_list(
    project: Path, calls: list[list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    (project / "AGENTS.md").write_text(AGENTS, encoding="utf-8")
    monkeypatch.setattr(packages, "_uv", lambda: None)
    fw.package_add("statsmodels>=0.14")
    assert (project / "AGENTS.md").read_text(encoding="utf-8") == AGENTS.replace(
        "): framework-py\n", "): framework-py, pandas, statsmodels\n"
    )


def test_package_add_with_uv_refreshes_agents_md_from_pyproject(
    project: Path, calls: list[list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    (project / "CLAUDE.md").write_text(AGENTS, encoding="utf-8")

    def uv_add(args: list[str], root: Path) -> int:
        packages._add_to_pyproject(root / "pyproject.toml", args[2])
        return 0

    monkeypatch.setattr(packages, "_run", uv_add)
    fw.package_add("scipy")
    assert "): framework-py, pandas, scipy\n" in (project / "CLAUDE.md").read_text(encoding="utf-8")


def test_package_add_leaves_a_file_without_the_marked_section(
    project: Path, calls: list[list[str]]
) -> None:
    (project / "AGENTS.md").write_text("# Mine\n\n## Installed Packages\n\nmine\n", "utf-8")
    fw.package_add("scipy")
    assert (project / "AGENTS.md").read_text(encoding="utf-8").endswith("mine\n")
