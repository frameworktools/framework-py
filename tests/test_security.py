from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

import framework as fw
from framework import db
from framework.security import _matches_gitignore

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")


def git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


@pytest.fixture
def repo(project: Path) -> Path:
    os.environ.update(
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_CONFIG_NOSYSTEM="1",
        GIT_AUTHOR_NAME="T",
        GIT_AUTHOR_EMAIL="t@example.com",
        GIT_COMMITTER_NAME="T",
        GIT_COMMITTER_EMAIL="t@example.com",
    )
    (project / "settings.yml").write_text(
        "default:\n"
        "  project_name: Test\n"
        "  language: python\n"
        "  directories:\n"
        "    inputs_private_raw: inputs/private/raw\n"
        "    outputs_public_tables: outputs/public/tables\n"
        "    scripts: scripts\n",
        encoding="utf-8",
    )
    (project / ".gitignore").write_text(".env\n/inputs/\n", encoding="utf-8")
    (project / "inputs/private/raw").mkdir(parents=True)
    (project / "inputs/private/raw/people.csv").write_text("id\n1\n", encoding="utf-8")
    (project / "outputs/public/tables").mkdir(parents=True)
    (project / "outputs/public/tables/summary.csv").write_text("n\n1\n", encoding="utf-8")
    git(project, "init", "-q")
    git(project, "add", ".")
    git(project, "commit", "-qm", "init")
    return project


def test_a_clean_project_passes(repo: Path) -> None:
    result = fw.git_security_audit(verbose=False)
    assert result.passed
    assert result.status == "pass"
    assert list(result.summary["check"]) == [
        "gitignore_coverage",
        "private_data_exposure",
        "git_history",
        "orphaned_files",
        "secrets_exposure",
    ]


def test_unignored_private_folder_fails(repo: Path) -> None:
    (repo / ".gitignore").write_text(".env\n", encoding="utf-8")
    result = fw.git_security_audit(verbose=False)
    coverage = result.findings["gitignore_coverage"]
    assert set(coverage["file"]) == {"inputs/private/raw", "inputs/private/raw/people.csv"}
    assert not result.passed


def test_folders_the_gitignore_template_does_not_ignore_are_not_private(repo: Path) -> None:
    # A standard project commits inputs/raw and outputs/tables unless you
    # ignore them; its .gitignore ignores private folders, cache, scratch,
    # and logs. Only those count as private.
    (repo / "settings.yml").write_text(
        "default:\n"
        "  directories:\n"
        "    inputs_raw: inputs/raw\n"
        "    outputs_tables: outputs/tables\n"
        "    outputs_private: outputs/private\n"
        "    inputs_private_raw: inputs/private/raw\n"
        "    cache: outputs/private/cache\n"
        "    scratch: scratch\n",
        encoding="utf-8",
    )
    for folder in ("inputs/raw", "outputs/tables", "outputs/private/cache", "scratch"):
        (repo / folder).mkdir(parents=True)
        (repo / folder / "a.csv").write_text("a\n", encoding="utf-8")
    (repo / ".gitignore").write_text(".env\n/inputs/private/\n", encoding="utf-8")
    coverage = fw.git_security_audit(verbose=False).findings["gitignore_coverage"]
    assert {"outputs_private", "cache", "scratch"} <= set(coverage["directory"])
    assert not {"inputs_raw", "outputs_tables", "inputs_private_raw"} & set(coverage["directory"])


def test_a_new_standard_project_passes_the_audit(tmp_path: Path) -> None:
    root = fw.new("Standard", tmp_path / "standard", type="project", interactive=False)
    (root / "inputs/raw/survey.csv").write_text("id\n1\n", encoding="utf-8")
    (root / "outputs/private/cache").mkdir(parents=True)
    (root / "outputs/private/cache/c.csv").write_text("id\n1\n", encoding="utf-8")
    result = fw.git_security_audit(root / "settings.yml", verbose=False)
    assert result.findings["gitignore_coverage"].empty
    assert result.passed


def test_tracked_private_data_and_history(repo: Path) -> None:
    git(repo, "add", "-f", "inputs/private/raw/people.csv")
    git(repo, "commit", "-qm", "oops")
    result = fw.git_security_audit(verbose=False)
    assert list(result.findings["private_data_exposure"]["file"]) == [
        "inputs/private/raw/people.csv"
    ]
    history = result.findings["git_history"]
    assert list(history["file"]) == ["inputs/private/raw/people.csv"]
    assert list(history["action"]) == ["added"]
    assert not result.passed


def test_staged_data_in_an_unconfigured_private_folder(repo: Path) -> None:
    (repo / "inputs/sensitive").mkdir()
    (repo / "inputs/sensitive/ids.csv").write_text("id\n", encoding="utf-8")
    git(repo, "add", "-f", "inputs/sensitive/ids.csv")
    exposed = fw.git_security_audit(verbose=False).findings["private_data_exposure"]
    assert "inputs/sensitive/ids.csv" in set(exposed["file"])


def test_tracked_env_file_fails(repo: Path) -> None:
    (repo / ".env").write_text("DB_PASSWORD=x\n", encoding="utf-8")
    (repo / ".env.example").write_text("DB_PASSWORD=\n", encoding="utf-8")
    git(repo, "add", "-f", ".env", ".env.example")
    result = fw.git_security_audit(check_git_history=False, verbose=False)
    assert list(result.findings["secrets_exposure"]["file"]) == [".env"]
    assert result.summary.set_index("check").loc["git_history", "status"] == "skipped"


def test_orphaned_files_warn(repo: Path) -> None:
    (repo / "loose.parquet").write_bytes(b"")
    (repo / ".framework").mkdir()
    (repo / ".framework/spec.json").write_text("{}", encoding="utf-8")
    result = fw.git_security_audit(verbose=False)
    assert list(result.findings["orphaned_files"]["path"]) == ["loose.parquet"]
    assert result.status == "warning"
    assert result.passed


def test_orphaned_files_skip_what_git_ignores(repo: Path) -> None:
    (repo / ".gitignore").write_text(".env\n/inputs/\nlocal.sqlite\npublished/\n", encoding="utf-8")
    (repo / "local.sqlite").write_bytes(b"")
    (repo / "published/table").mkdir(parents=True)
    (repo / "published/table/data.csv").write_text("x\n1\n", encoding="utf-8")
    (repo / "loose.csv").write_text("x\n1\n", encoding="utf-8")
    # Quarto's render folders beside a notebook, at any depth
    (repo / "notebooks/report_files/libs").mkdir(parents=True)
    (repo / "notebooks/report_files/libs/data.json").write_text("{}", encoding="utf-8")
    (repo / "notebooks/report_cache").mkdir()
    (repo / "notebooks/report_cache/chunk.csv").write_text("x\n", encoding="utf-8")
    result = fw.git_security_audit(verbose=False)
    assert list(result.findings["orphaned_files"]["path"]) == ["loose.csv"]


def test_auto_fix_adds_folders(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (repo / ".gitignore").write_text(".env\n", encoding="utf-8")
    fw.git_security_audit(auto_fix=True)
    text = (repo / ".gitignore").read_text(encoding="utf-8")
    assert (
        "# Framework Security Audit - Auto-generated\ninputs/private/raw/\ninputs/private/raw/**\n"
        in text
    )
    assert "AUDIT FAILED" in capsys.readouterr().out
    assert fw.git_security_audit(verbose=False).findings["gitignore_coverage"].empty


def test_result_recorded_in_framework_db(repo: Path) -> None:
    db.ensure_db(repo)
    fw.git_security_audit(verbose=False)
    assert db.get_meta("last_audit_status", repo) == "pass"
    assert db.get_meta("last_audit_issues", repo) == "0"


def test_without_git_uses_rs_gitignore_rules(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("framework.security.shutil.which", lambda name: None)
    (project / "settings.yml").write_text(
        "default:\n  directories:\n    inputs_private_raw: inputs/private/raw\n", encoding="utf-8"
    )
    (project / "inputs/private/raw").mkdir(parents=True)
    (project / "inputs/private/raw/a.csv").write_text("a\n", encoding="utf-8")
    (project / ".gitignore").write_text("inputs/private/raw/\n", encoding="utf-8")
    result = fw.git_security_audit(verbose=False)
    assert result.findings["gitignore_coverage"].empty
    (project / ".gitignore").write_text(".env\n", encoding="utf-8")
    coverage = fw.git_security_audit(verbose=False).findings["gitignore_coverage"]
    assert set(coverage["file"]) == {"inputs/private/raw", "inputs/private/raw/a.csv"}
    assert result.summary.set_index("check").loc["secrets_exposure", "status"] == "skipped"


@pytest.mark.parametrize(
    ("path", "lines", "expected"),
    [
        ("inputs/raw", ["/inputs/"], True),
        ("inputs/raw/a.csv", ["**/raw/**"], True),
        ("inputs/raw/a.csv", ["inputs/**", "!inputs/raw/a.csv"], False),
        ("data.csv", ["*.csv"], True),
        ("data.csv", ["# *.csv", ""], False),
    ],
)
def test_r_gitignore_matcher(path: str, lines: list[str], expected: bool) -> None:
    assert _matches_gitignore(path, lines) is expected


def test_invalid_arguments(repo: Path) -> None:
    with pytest.raises(ValueError, match="history_depth"):
        fw.git_security_audit(history_depth="deep", verbose=False)
    with pytest.raises(ValueError, match="history_depth"):
        fw.git_security_audit(history_depth=0, verbose=False)


def test_auto_fix_writes_files_as_files(repo: Path) -> None:
    # Audit 2026-11-05: a data file was written as "<file>/" and "<file>/**", folder
    # patterns that never match it
    (repo / "inputs/private/raw").mkdir(parents=True, exist_ok=True)
    (repo / "inputs/private/raw/a.parquet").write_bytes(b"PAR1")
    (repo / "inputs/private/raw/keep.parquet").write_bytes(b"PAR1")
    (repo / ".gitignore").write_text(
        ".env\ninputs/private/raw/*\n!inputs/private/raw/keep.parquet\n", encoding="utf-8"
    )
    coverage = fw.git_security_audit(verbose=False).findings["gitignore_coverage"]
    assert list(coverage["file"]) == ["inputs/private/raw/keep.parquet"]
    fw.git_security_audit(auto_fix=True, verbose=False)
    lines = (repo / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "/inputs/private/raw/keep.parquet" in lines
    assert not any(line.startswith("inputs/private/raw/keep.parquet/") for line in lines)
    assert fw.git_security_audit(verbose=False).findings["gitignore_coverage"].empty


def test_auto_fix_adds_only_the_folder_when_it_covers_the_files(repo: Path) -> None:
    (repo / "inputs/private/raw").mkdir(parents=True, exist_ok=True)
    (repo / "inputs/private/raw/a.parquet").write_bytes(b"PAR1")
    (repo / ".gitignore").write_text(".env\n", encoding="utf-8")
    fw.git_security_audit(auto_fix=True, verbose=False)
    lines = (repo / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert lines[-2:] == ["inputs/private/raw/", "inputs/private/raw/**"]
