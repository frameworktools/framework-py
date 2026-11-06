from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import framework as fw
from framework import _yaml
from framework.__main__ import main
from framework.hooks import hook_run, hook_script

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")


def git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, check=False
    )


@pytest.fixture
def repo(project: Path) -> Path:
    os.environ.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
    (project / ".gitignore").write_text(".env\n", encoding="utf-8")
    git(project, "init", "-q")
    return project


def hooks_setting(project: Path) -> dict[str, bool]:
    data = _yaml.read(project / "settings.yml")
    return dict(data["default"]["git"]["hooks"])


def test_shipped_hook_is_a_posix_script() -> None:
    script = hook_script()
    assert script.startswith("#!/bin/sh\n")
    assert "\r" not in script
    assert "-m framework hook pre-commit" in script
    assert "-c 'import framework'" in script


@pytest.mark.skipif(sys.platform == "win32", reason="runs the hook with /bin/sh")
def test_shipped_hook_finds_a_runtime_without_project_markers(repo: Path, tmp_path: Path) -> None:
    # Audit 2026-11-05 (shared with R's inst/hooks/pre-commit): a default project has
    # settings.yml but no .Rproj, renv.lock, pyproject.toml, uv.lock, or .venv. The hook
    # used to fail with "found neither R ... nor Python". Now it tries R, then Python.
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "python3.log"
    fake = bin_dir / "python3"
    fake.write_text(f'#!/bin/sh\necho "$*" >> "{log}"\nexit 0\n', encoding="utf-8")
    fake.chmod(0o755)
    hook = tmp_path / "pre-commit"
    hook.write_text(hook_script(), encoding="utf-8")
    env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}/usr/bin{os.pathsep}/bin"}
    result = subprocess.run(
        ["/bin/sh", str(hook)], cwd=repo, env=env, capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    assert "-m framework hook pre-commit" in log.read_text(encoding="utf-8")


def test_nothing_installed_until_a_hook_is_enabled(repo: Path) -> None:
    assert fw.git_hooks_install() is False
    assert not (repo / ".git/hooks/pre-commit").exists()


def test_enable_list_disable(repo: Path) -> None:
    assert fw.git_hooks_enable("data_security") is True
    hook = repo / ".git/hooks/pre-commit"
    assert hook.read_text(encoding="utf-8") == hook_script()
    if sys.platform != "win32":
        assert os.access(hook, os.X_OK)
    assert hooks_setting(repo) == {"data_security": True}

    listed = fw.git_hooks_list()
    assert listed.attrs["installed"] is True
    assert dict(zip(listed["hook"], listed["enabled"], strict=True)) == {
        "data_security": True,
        "check_sensitive_dirs": False,
        "check_notebook_outputs": False,
    }

    assert fw.git_hooks_disable("data_security") is False
    assert not hook.exists()


def test_split_git_settings_file(repo: Path) -> None:
    (repo / "settings").mkdir()
    (repo / "settings/git.yml").write_text("git:\n  enabled: true\n", encoding="utf-8")
    (repo / "settings.yml").write_text(
        "default:\n  project_name: Test\n  git: settings/git.yml\n", encoding="utf-8"
    )
    fw.git_hooks_enable("warn_unignored_sensitive")
    split = _yaml.read(repo / "settings/git.yml")
    assert split == {"git": {"enabled": True, "hooks": {"check_sensitive_dirs": True}}}
    assert fw.git_hooks_list()["enabled"].tolist() == [False, True, False]


def test_r_alias_counts_as_enabled(repo: Path) -> None:
    (repo / "settings.yml").write_text(
        "default:\n  git:\n    hooks:\n      warn_unignored_sensitive: true\n", encoding="utf-8"
    )
    assert fw.git_hooks_install() is True
    fw.git_hooks_disable("check_sensitive_dirs")
    assert hooks_setting(repo) == {"warn_unignored_sensitive": False, "check_sensitive_dirs": False}


def test_other_hooks_are_kept(repo: Path) -> None:
    hook = repo / ".git/hooks/pre-commit"
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text("#!/bin/sh\necho mine\n", encoding="utf-8")
    fw.git_hooks_enable("data_security")
    assert "mine" in hook.read_text(encoding="utf-8")
    assert fw.git_hooks_uninstall() is False
    assert fw.git_hooks_install(force=True) is True
    assert fw.git_hooks_uninstall() is True


def test_r_1x_hook_is_replaced(repo: Path) -> None:
    hook = repo / ".git/hooks/pre-commit"
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text("#!/usr/bin/env bash\n# Framework Git Hooks\nexit 0\n", encoding="utf-8")
    fw.git_hooks_enable("data_security")
    assert hook.read_text(encoding="utf-8") == hook_script()


def test_invalid_hook_name(repo: Path) -> None:
    with pytest.raises(ValueError, match="Invalid hook name"):
        fw.git_hooks_enable("lint")
    with pytest.raises(ValueError, match="ai_sync hook was removed"):
        fw.git_hooks_enable("ai_sync")


def test_old_ai_sync_setting_is_ignored(repo: Path) -> None:
    (repo / "settings.yml").write_text(
        "default:\n  git:\n    hooks: {ai_sync: true}\n", encoding="utf-8"
    )
    assert fw.git_hooks_install() is False
    assert fw.git_hooks_disable("ai_sync") is False
    assert hooks_setting(repo) == {"ai_sync": False}


def test_hook_run_blocks_tracked_private_data(repo: Path) -> None:
    fw.git_hooks_enable("data_security")
    assert hook_run("pre-commit") == 0
    (repo / "inputs/private").mkdir(parents=True)
    (repo / "inputs/private/ids.csv").write_text("id\n", encoding="utf-8")
    git(repo, "add", "-f", "inputs/private/ids.csv")
    assert hook_run("pre-commit") == 1
    assert main(["hook", "pre-commit"]) == 1


def test_hook_run_warns_about_sensitive_folders(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fw.git_hooks_enable("check_sensitive_dirs")
    (repo / "notes/confidential").mkdir(parents=True)
    (repo / "data/private").mkdir(parents=True)
    (repo / ".gitignore").write_text(".env\ndata/\n", encoding="utf-8")
    monkeypatch.setattr("framework.hooks._confirm", lambda question: None)
    assert hook_run("pre-commit") == 0
    err = capsys.readouterr().err
    assert "notes/confidential" in err
    assert "data/private" not in err
    monkeypatch.setattr("framework.hooks._confirm", lambda question: False)
    assert hook_run("pre-commit") == 1


def notebook(outputs: list[object], count: int | None) -> str:
    cell = {"cell_type": "code", "source": "df", "metadata": {}}
    return json.dumps({"cells": [{**cell, "outputs": outputs, "execution_count": count}]})


def test_hook_run_blocks_notebooks_with_outputs(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fw.git_hooks_enable("check_notebook_outputs")
    (repo / "clean.ipynb").write_text(notebook([], None), encoding="utf-8")
    git(repo, "add", "clean.ipynb")
    assert hook_run("pre-commit") == 0

    printed = [{"output_type": "stream", "name": "stdout", "text": "id 1\n"}]
    (repo / "analysis.ipynb").write_text(notebook(printed, 1), encoding="utf-8")
    (repo / "counted.ipynb").write_text(notebook([], 3), encoding="utf-8")
    git(repo, "add", "analysis.ipynb", "counted.ipynb")
    assert hook_run("pre-commit") == 1
    err = capsys.readouterr().err
    assert "  - analysis.ipynb\n  - counted.ipynb\n" in err
    assert "clean.ipynb" not in err
    assert "jupyter nbconvert --clear-output --inplace 'counted.ipynb'\n" in err

    # The staged version counts, so clearing the file on disk is not enough
    (repo / "analysis.ipynb").write_text(notebook([], None), encoding="utf-8")
    (repo / "counted.ipynb").write_text(notebook([], None), encoding="utf-8")
    assert hook_run("pre-commit") == 1
    git(repo, "add", "analysis.ipynb", "counted.ipynb")
    assert hook_run("pre-commit") == 0


def test_hook_run_outside_a_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    assert hook_run("pre-commit") == 0
    assert hook_run("post-merge") == 1


@pytest.mark.skipif(sys.platform == "win32", reason="runs the POSIX hook")
def test_git_commit_runs_the_hook(repo: Path, tmp_path_factory: pytest.TempPathFactory) -> None:
    """The real hook, through git, with this interpreter as the project's Python."""
    (repo / "pyproject.toml").write_text("[project]\nname = 't'\n", encoding="utf-8")
    bin_dir = tmp_path_factory.mktemp("bin")
    python = bin_dir / "python3"
    python.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n', encoding="utf-8")
    python.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "GIT_AUTHOR_NAME": "T",
        "GIT_AUTHOR_EMAIL": "t@example.com",
        "GIT_COMMITTER_NAME": "T",
        "GIT_COMMITTER_EMAIL": "t@example.com",
    }
    fw.git_hooks_enable("data_security")

    def commit(path: str) -> int:
        subprocess.run(["git", "-C", str(repo), "add", "-f", path], check=True, env=env)
        done = subprocess.run(
            ["git", "-C", str(repo), "commit", "-qm", "x"], env=env, capture_output=True
        )
        return done.returncode

    (repo / "a.py").write_text("x = 1\n", encoding="utf-8")
    assert commit("a.py") == 0
    (repo / "private").mkdir()
    (repo / "private/ids.csv").write_text("id\n", encoding="utf-8")
    assert commit("private/ids.csv") == 1


def test_project_create_installs_enabled_hooks(tmp_path: Path) -> None:
    os.environ.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
    layer = {"type": "project", "language": "python", "git": {"hooks": {"data_security": True}}}
    spec = fw.spec_resolve([layer], name="Hooked").spec
    root = fw.project_create(spec, tmp_path / "hooked")
    assert (root / ".git/hooks/pre-commit").read_text(encoding="utf-8") == hook_script()


def test_sensitive_folders_ignored_by_content_rules_are_covered(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Audit 2026-11-05: "scratch/*" and "private/" rules did not count, so every commit in
    # a new project warned about scratch/ and private/
    fw.git_hooks_enable("check_sensitive_dirs")
    for folder in ("scratch", "private", "outputs/cache", "notes/sensitive"):
        (repo / folder).mkdir(parents=True)
    (repo / ".gitignore").write_text(
        ".env\nscratch/*\n!scratch/.gitkeep\nprivate/\noutputs/cache/**\n", encoding="utf-8"
    )
    monkeypatch.setattr("framework.hooks._confirm", lambda question: False)
    assert hook_run("pre-commit") == 1
    err = capsys.readouterr().err
    assert "notes/sensitive" in err
    assert "  - notes/sensitive" in err
    for covered in ("scratch", "private", "outputs/cache"):
        assert f"  - {covered}\n" not in err
    (repo / ".gitignore").write_text(
        ".env\nscratch/*\nprivate/\noutputs/cache/**\nnotes/\n", encoding="utf-8"
    )
    assert hook_run("pre-commit") == 0
