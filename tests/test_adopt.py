from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import httpx
import pytest

import framework as fw
from framework import _yaml
from framework.__main__ import main
from framework.adopt import adopt_plan, scan_folders
from tests.test_cloud import FakeCloud


@pytest.fixture
def existing(tmp_path: Path) -> Path:
    root = tmp_path / "old-study"
    for folder in (
        "data/raw/2024",
        "data/processed",
        "analysis",
        "R",
        "figures",
        "assets/logos",
        "inputs_private",
        ".git/objects",
        ".venv/lib",
        "renv/library",
        "deep/a/b/c",
    ):
        (root / folder).mkdir(parents=True)
    (root / "analysis/clean.py").write_text("print('hi')\n", encoding="utf-8")
    return root


def test_scan_skips_hidden_environments_and_deep_folders(existing: Path) -> None:
    assert scan_folders(existing) == [
        "R",
        "analysis",
        "assets",
        "assets/logos",
        "data",
        "data/processed",
        "data/raw",
        "data/raw/2024",
        "deep",
        "deep/a",
        "deep/a/b",
        "figures",
        "inputs_private",
    ]


def test_plan_maps_folders_with_the_shared_rules(existing: Path) -> None:
    plan = adopt_plan(scan_folders(existing))
    assert plan.directories == {
        "functions": "R",
        "scripts": "analysis",
        "inputs_final": "data/processed",
        "inputs_raw": "data/raw",
        "outputs_figures": "figures",
    }
    assert plan.extra_directories == {
        "assets": {"label": "assets", "path": "assets"},
        "deep": {"label": "deep", "path": "deep"},
        "inputs_private": {"label": "inputs_private", "path": "inputs_private"},
    }
    assert plan.private == ["inputs_private"]


def test_adopt_writes_settings_and_nothing_else(
    existing: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    before = sorted(p.relative_to(existing).as_posix() for p in existing.rglob("*"))
    fw.adopt(existing, yes=True)
    after = sorted(p.relative_to(existing).as_posix() for p in existing.rglob("*"))
    assert sorted(set(after) - set(before)) == [
        ".framework",
        ".framework/spec.json",
        "settings.yml",
    ]
    config = _yaml.read(existing / "settings.yml")["default"]
    assert (config["project_name"], config["project_type"]) == ("old-study", "bare")
    assert config["directories"]["inputs_raw"] == "data/raw"
    assert config["security"] == {"sensitive": False}
    assert "inputs_private" in capsys.readouterr().err
    spec = json.loads((existing / ".framework/spec.json").read_text(encoding="utf-8"))
    assert spec["core_files"] is False
    assert spec["settings_layout"] == "flat"

    with pytest.raises(fw.FrameworkError, match="already a Framework project"):
        fw.adopt(existing, yes=True)


def test_review_changes_roles_and_turns_on_sensitive(
    existing: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    # 1 is R: make it extra. -7 leaves figures out. Then accept, yes to sensitive.
    answers = iter(["1", "extra", "-7", "", "y"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))
    fw.adopt(existing)
    config = _yaml.read(existing / "settings.yml")["default"]
    assert "functions" not in config["directories"]
    assert "outputs_figures" not in config["directories"]
    assert {"key": "r", "label": "R", "path": "R"} in config["extra_directories"]
    assert config["security"] == {"sensitive": True}


def test_adopt_needs_a_terminal_or_yes(existing: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    with pytest.raises(fw.FrameworkError, match="Pass yes=True"):
        fw.adopt(existing)
    assert main(["adopt", "--path", str(existing), "--yes"]) == 0
    assert (existing / "settings.yml").is_file()


def test_structure_import(existing: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from framework import cloud

    server = FakeCloud()
    monkeypatch.setattr(cloud, "_transport", httpx.MockTransport(server))
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    with pytest.raises(fw.FrameworkError, match="Pass yes=True"):
        fw.structure_import(existing)
    with pytest.raises(fw.CloudError, match="No cloud token"):
        fw.structure_import(existing, yes=True)
    os.environ["FW_CLOUD_TOKEN"] = "fw_user0123456"
    with pytest.raises(fw.CloudError, match="doesn't support structures yet"):
        fw.structure_import(existing, yes=True)
    sent: dict[str, Any] = {}

    def create(request: httpx.Request) -> httpx.Response:
        sent.update(json.loads(request.content))
        body = {"key": "ada/old-study", "name": "Old study", "url": "https://x/s/1"}
        return httpx.Response(201, json=body)

    server.route("POST", "/api/v1/structures", create)
    assert fw.structure_import(existing, name="Old study", yes=True)["key"] == "ada/old-study"
    assert sent["name"] == "Old study"
    assert sent["directories"]["inputs_raw"] == "data/raw"
    assert sent["extra_directories"]["assets"] == {"label": "assets", "path": "assets"}

    # The review: leave assets (3) out, then accept; or cancel
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    answers = iter(["-3", ""])
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))
    fw.structure_import(existing)
    assert "assets" not in sent["extra_directories"]
    monkeypatch.setattr("builtins.input", lambda prompt: "q")
    with pytest.raises(fw.FrameworkError, match="Nothing was sent"):
        fw.structure_import(existing)


def test_registering_an_adopted_project_keeps_the_token_out_of_git(
    existing: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    import shutil
    import subprocess

    from framework import cloud

    if shutil.which("git"):
        subprocess.run(["git", "init", "-q", str(existing)], check=True)
    server = FakeCloud()
    server.route(
        "POST",
        "/api/v1/projects",
        lambda r: httpx.Response(201, json={"slug": "old", "token": "fwp_secret0123"}),
    )
    monkeypatch.setattr(cloud, "_transport", httpx.MockTransport(server))
    os.environ["FW_CLOUD_TOKEN"] = "fw_user0123456"
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    # Accept the folders, no to sensitive, yes to register, no to the structure
    answers = iter(["", "n", "y", "n"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))
    caplog.set_level("INFO", logger="framework")
    fw.adopt(existing)
    assert "fwp_secret0123" in (existing / ".env").read_text(encoding="utf-8")
    assert (existing / ".gitignore").read_text(encoding="utf-8").strip().endswith(".env")
    assert "so the project token stays out of git" in caplog.text
    assert "fwp_secret0123" not in caplog.text
    if shutil.which("git"):
        ignored = subprocess.run(["git", "-C", str(existing), "check-ignore", "-q", ".env"])
        assert ignored.returncode == 0


@pytest.mark.skipif(not __import__("shutil").which("git"), reason="git is not installed")
def test_a_tracked_env_refuses_the_token(tmp_path: Path) -> None:
    import subprocess

    from framework.create import keep_env_out_of_git

    env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True, env=env)
    (tmp_path / ".env").write_text("A=1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", ".env"], check=True, env=env)
    with pytest.raises(fw.FrameworkError, match=r"git rm --cached \.env"):
        keep_env_out_of_git(tmp_path)


def test_a_config_yml_counts_only_with_framework_keys(existing: Path) -> None:
    from framework.project import is_framework_config, settings_files

    config = existing / "config.yml"
    # A tool's own config.yml (sidr-pipeline's repository settings)
    config.write_text("repository:\n  name: pipeline\noutput: build\n", encoding="utf-8")
    assert not is_framework_config(config)
    assert settings_files(existing) == []
    for text in (
        "default:\n  project_type: project\n",
        "project_name: Old\n",
        "default:\n  options:\n    data_dir: data\n",
        "default:\n  packages:\n    - name: dplyr\n      auto_attach: true\n",
    ):
        config.write_text(text, encoding="utf-8")
        assert is_framework_config(config), text
    config.write_text("repository: pipeline\n", encoding="utf-8")
    fw.adopt(existing, yes=True)
    assert (existing / "settings.yml").is_file()


def test_settings_yaml_is_an_existing_project(existing: Path) -> None:
    (existing / "settings.yaml").write_text("default:\n  project_name: x\n", encoding="utf-8")
    with pytest.raises(fw.FrameworkError, match=r"settings\.yaml"):
        fw.adopt(existing, yes=True)
    assert not (existing / "settings.yml").exists()
