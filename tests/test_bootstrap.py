from __future__ import annotations

import importlib
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

import framework as fw
from framework.__main__ import main

# The package's bootstrap() function hides the module of the same name.
bootstrap_module = importlib.import_module("framework.bootstrap")


@pytest.fixture
def clone(tmp_path: Path) -> Path:
    """A Python project as git would check it out: no .env, no empty folders, no database."""
    root = tmp_path / "clone"
    root.mkdir()
    (root / "settings.yml").write_text(
        "default:\n"
        "  project_name: Clone\n"
        "  language: python\n"
        "  directories:\n"
        "    inputs_raw: inputs/raw\n"
        "    outputs_tables: outputs/tables\n"
        "    shared: /mnt/shared/never-created\n"
        "  extra_directories:\n"
        "    - {key: refs, label: References, path: references}\n"
        "  connections: settings/connections.yml\n"
        "  api_token: !expr Sys.getenv('API_TOKEN')\n"
        '  # bucket: env("S3_BUCKET")\n',
        encoding="utf-8",
    )
    (root / "settings").mkdir()
    (root / "settings/connections.yml").write_text(
        "connections:\n  databases:\n    warehouse:\n      driver: postgres\n"
        '      host: env("DB_HOST")\n      password: env("DB_PASSWORD")\n',
        encoding="utf-8",
    )
    # S3_BUCKET is a placeholder the settings never read
    (root / ".env.example").write_text(
        "DB_HOST=localhost\nDB_PASSWORD=\nAPI_TOKEN=\nS3_BUCKET=\n", encoding="utf-8"
    )
    return root


def test_prepares_a_clone(clone: Path) -> None:
    result = fw.bootstrap(clone, packages=False)
    assert (clone / ".env").read_text(encoding="utf-8").startswith("DB_HOST=localhost")
    for folder in ("inputs/raw", "outputs/tables", "references"):
        assert (clone / folder).is_dir()
    assert not Path("/mnt/shared/never-created").exists()
    assert (clone / "framework.db").is_file()
    assert ".env" in (clone / ".gitignore").read_text(encoding="utf-8")
    assert result.todo == ["Fill in .env: DB_PASSWORD, API_TOKEN"]
    assert "Created framework.db" in result.done


def test_file_entries_in_directories_stay_files(clone: Path) -> None:
    # Presentation projects list presentation.qmd under directories.
    text = (clone / "settings.yml").read_text(encoding="utf-8")
    (clone / "settings.yml").write_text(
        text.replace(
            "  directories:\n", "  directories:\n    presentation_source: presentation.qmd\n"
        ),
        encoding="utf-8",
    )
    result = fw.bootstrap(clone, packages=False)
    assert not (clone / "presentation.qmd").exists()
    assert "presentation.qmd" not in " ".join(result.done)
    (clone / "presentation.qmd").write_text("---\n---\n", encoding="utf-8")
    fw.bootstrap(clone, packages=False)
    assert (clone / "presentation.qmd").is_file()


def test_is_idempotent(clone: Path) -> None:
    fw.bootstrap(clone, packages=False)
    (clone / ".env").write_text("DB_PASSWORD=kept\n", encoding="utf-8")
    again = fw.bootstrap(clone, packages=False)
    assert (clone / ".env").read_text(encoding="utf-8") == "DB_PASSWORD=kept\n"
    # Audit 2026-11-05: an unchanged framework.db was listed as a step taken
    assert again.done == []
    assert again.todo == []


def test_interactive_fills_empty_values(clone: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    asked: list[str] = []

    def secret(prompt: str) -> str:
        asked.append(prompt)
        return "s3cret value" if prompt.startswith("DB_PASSWORD") else ""

    monkeypatch.setattr(bootstrap_module.getpass, "getpass", secret)
    result = fw.bootstrap(clone, interactive=True, packages=False)
    assert asked == ["DB_PASSWORD (leave empty to skip): ", "API_TOKEN (leave empty to skip): "]
    assert 'DB_PASSWORD="s3cret value"' in (clone / ".env").read_text(encoding="utf-8")
    assert result.todo == ["Fill in .env: API_TOKEN"]


def test_restores_packages_with_uv(clone: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (clone / "pyproject.toml").write_text("[project]\nname = 'clone'\n", encoding="utf-8")
    (clone / "uv.lock").write_text("", encoding="utf-8")
    calls: list[list[str]] = []

    def run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(bootstrap_module.shutil, "which", lambda name: "/bin/uv")
    monkeypatch.setattr(bootstrap_module.subprocess, "run", run)
    fw.bootstrap(clone)
    assert calls == [["/bin/uv", "sync", "--frozen"]]

    monkeypatch.setattr(
        bootstrap_module.subprocess,
        "run",
        lambda args, **kwargs: subprocess.CompletedProcess(args, 2),
    )
    with pytest.raises(fw.FrameworkError, match="Package restore failed"):
        fw.bootstrap(clone)
    monkeypatch.setattr(bootstrap_module.shutil, "which", lambda name: None)
    with pytest.raises(fw.FrameworkError, match="uv is not installed"):
        fw.bootstrap(clone)


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
def test_installs_enabled_hooks(clone: Path) -> None:
    os.environ.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
    subprocess.run(["git", "-C", str(clone), "init", "-q"], check=True)
    text = (clone / "settings.yml").read_text(encoding="utf-8")
    (clone / "settings.yml").write_text(
        text + "  git:\n    hooks: {data_security: true}\n", encoding="utf-8"
    )
    result = fw.bootstrap(clone, packages=False)
    assert "Installed git hooks" in result.done
    assert (clone / ".git/hooks/pre-commit").is_file()


def test_refusals(tmp_path: Path, clone: Path) -> None:
    with pytest.raises(fw.FrameworkError, match=r"No settings\.yml"):
        fw.bootstrap(tmp_path / "missing")
    r_project = tmp_path / "r"
    r_project.mkdir()
    (r_project / "settings.yml").write_text("default:\n  project_name: R\n", encoding="utf-8")
    # Audit 2026-11-05: a project with no language was called "a r project"
    with pytest.raises(fw.FrameworkError, match=r"name no language.*framework::bootstrap"):
        fw.bootstrap(r_project)
    (r_project / "settings.yml").write_text("default:\n  language: r\n", encoding="utf-8")
    with pytest.raises(fw.FrameworkError, match=r"This is a r project.*framework::bootstrap"):
        fw.bootstrap(r_project)
    (clone / ".framework").mkdir()
    (clone / ".framework/spec.json").write_text(json.dumps({"type": "nope"}), encoding="utf-8")
    with pytest.raises(fw.FrameworkError, match=r"spec\.json is invalid"):
        fw.bootstrap(clone, packages=False)


def test_cli(clone: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["bootstrap", "--path", str(clone), "--no-packages"]) == 0
    assert capsys.readouterr().out.strip() == str(clone.resolve())
    (clone / "settings.yml").unlink()
    assert main(["bootstrap", "--path", str(clone)]) == 1
    assert capsys.readouterr().err.strip().splitlines()[-1].startswith("Error: No settings.yml")


def test_created_project_bootstraps(tmp_path: Path) -> None:
    """A project Python created bootstraps cleanly, spec.json included."""
    root = fw.new("Round Trip", tmp_path / "rt", "project")
    shutil.rmtree(root / "inputs")
    (root / "framework.db").unlink()
    result = fw.bootstrap(root, packages=False)
    assert (root / "inputs").is_dir()
    assert "Created framework.db" in result.done


def test_create_help_describes_interactive(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("COLUMNS", "500")
    with pytest.raises(SystemExit):
        main(["create", "--help"])
    text = " ".join(capsys.readouterr().out.split())
    assert "with --name, ask for the type when --type is missing" in text
    assert "with --from-layers, initialize git and register the project" in text


def test_cli_messages_name_flags(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # Audit 2026-11-05: --tool X=maybe meant on; the no-terminal error said "yes=True"
    with pytest.raises(SystemExit):
        main(["create", "--name", "T", "--path", str(tmp_path / "t"), "--tool", "jupyter=maybe"])
    assert "jupyter=true, or jupyter=false" in capsys.readouterr().err
    (tmp_path / "old").mkdir()
    assert main(["adopt", "--path", str(tmp_path / "old")]) == 1
    err = capsys.readouterr().err
    assert "Pass --yes." in err and "yes=True" not in err


def test_warns_when_a_sensitive_project_lacks_the_setting(clone: Path) -> None:
    settings = clone / "settings.yml"
    text = settings.read_text(encoding="utf-8")
    sensitive = "  language: python\n  project_type: project_sensitive\n"
    settings.write_text(text.replace("  language: python\n", sensitive, 1), encoding="utf-8")
    with pytest.warns(fw.FrameworkWarning, match="no security: block"):
        fw.bootstrap(clone, packages=False)
