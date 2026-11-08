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
        ".gitignore",
        "settings.yml",
    ]
    config = _yaml.read(existing / "settings.yml")["default"]
    assert (config["project_name"], config["project_type"]) == ("old-study", "project")
    assert config["language"] == "python"
    assert config["directories"]["inputs_raw"] == "data/raw"
    assert config["security"] == {"sensitive": False}
    err = capsys.readouterr().err
    # The mapping is shown with yes=True too, as R shows it
    assert "Proposed mapping (type: project):" in err
    assert "data/raw" in err
    assert "'inputs_private' looks private" in err
    assert "sensitive=True" in err
    spec = json.loads((existing / ".framework/spec.json").read_text(encoding="utf-8"))
    # The adopted spec, as R records it
    assert spec["type"] == "project"
    assert spec["core_files"] is False
    assert spec["settings_layout"] == "flat"
    assert spec["tools"] == {"quarto": False, "uv": False}
    assert spec["directories"] == {
        "functions": "R",
        "scripts": "analysis",
        "inputs_final": "data/processed",
        "inputs_raw": "data/raw",
        "outputs_figures": "figures",
    }
    assert spec["render_dirs"] == {}
    assert spec["git"]["use_git"] is True  # the folder has .git
    assert spec["ai"]["enabled"] is False
    assert spec["skills"] == {}
    assert spec["packages"]["default_packages"] == {}
    assert spec["source"]["adopted"] is True
    assert spec["source"]["origin"] == "python"

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
    sent: list[str] = []

    def create(request: httpx.Request) -> httpx.Response:
        sent.append(request.content.decode("utf-8"))
        body = {"slug": "old-study", "type": "project", "adopted": True}
        return httpx.Response(201, json={**body, "url": "https://x/p/1", "token": "fwp_secret0123"})

    server.route("POST", "/api/v1/projects", create)
    monkeypatch.setattr(cloud, "_transport", httpx.MockTransport(server))
    os.environ["FW_CLOUD_TOKEN"] = "fw_user0123456"
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    # Accept the folders, no to sensitive, yes to register
    answers = iter(["", "n", "y"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))
    caplog.set_level("INFO", logger="framework")
    fw.adopt(existing)
    # One request: the spec as written, no structure first
    assert server.paths() == ["POST /api/v1/projects"]
    body = json.loads(sent[0])
    assert body["spec"] == json.loads(
        (existing / ".framework/spec.json").read_text(encoding="utf-8")
    )
    assert (body["name"], body["slug"], body["adopted"]) == ("old-study", "old-study", True)
    assert "project_type" not in body and "structure" not in body
    assert "fwp_secret0123" in (existing / ".env").read_text(encoding="utf-8")
    assert ".env" in (existing / ".gitignore").read_text(encoding="utf-8").splitlines()
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


def test_scan_skips_generated_folders_and_links(tmp_path: Path) -> None:
    root = tmp_path / "study"
    for folder in (
        "settings",
        "notebooks/02-summary_files",
        "notebooks/fit_cache",
        "_site",
        "data",
    ):
        (root / folder).mkdir(parents=True)
    (root / "linked").symlink_to(root / "data", target_is_directory=True)
    assert scan_folders(root) == ["data", "notebooks"]


def test_plan_infers_the_type(tmp_path: Path) -> None:
    plan = adopt_plan(["slides", "Modules", "data", "data/raw"])
    assert plan.type == "course"
    assert plan.directories == {"data": "data", "slides": "slides", "notebooks": "Modules"}
    plan = adopt_plan(["inputs", "inputs/private", "inputs/private/raw", "scripts"])
    assert plan.type == "project_sensitive"
    assert adopt_plan(["scripts"]).type == "project"
    assert adopt_plan(["misc"]).type == "bare"
    assert adopt_plan(["slides", "Modules"], type="project").directories == {}


def test_adopt_takes_a_type_and_sensitive(existing: Path) -> None:
    with pytest.raises(fw.FrameworkError, match="leave sensitive out"):
        fw.adopt(existing, yes=True, type="project_sensitive", sensitive=False)
    with pytest.raises(fw.FrameworkError, match="type must be one of"):
        fw.adopt(existing, yes=True, type="shiny")
    fw.adopt(existing, yes=True, sensitive=True)
    config = _yaml.read(existing / "settings.yml")["default"]
    assert config["security"] == {"sensitive": True}
    assert config["project_type"] == "project"


def test_adopt_type_project_sensitive_is_sensitive(existing: Path) -> None:
    assert main(["adopt", "--path", str(existing), "--yes", "--type", "project_sensitive"]) == 0
    config = _yaml.read(existing / "settings.yml")["default"]
    assert (config["project_type"], config["security"]) == (
        "project_sensitive",
        {"sensitive": True},
    )


def test_the_sensitive_layout_is_inferred(tmp_path: Path) -> None:
    root = tmp_path / "cohort"
    for folder in ("inputs/private/raw", "inputs/public/raw", "outputs/private/tables", "R"):
        (root / folder).mkdir(parents=True)
    fw.adopt(root, yes=True)
    spec = json.loads((root / ".framework/spec.json").read_text(encoding="utf-8"))
    assert spec["type"] == "project_sensitive"
    assert spec["security"]["sensitive"] is True
    assert spec["git"]["use_git"] is False
    assert spec["directories"]["inputs_private_raw"] == "inputs/private/raw"


def test_a_folder_with_no_subfolders(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = tmp_path / "loose"
    root.mkdir()
    (root / "analysis.qmd").write_text("```{python}\n1\n```\n", encoding="utf-8")
    fw.adopt(root, yes=True)
    assert "No folders to map" in capsys.readouterr().err
    spec = json.loads((root / ".framework/spec.json").read_text(encoding="utf-8"))
    assert (spec["type"], spec["directories"], spec["extra_directories"]) == ("bare", {}, {})


@pytest.mark.parametrize(
    ("files", "language", "source"),
    [
        ({"study.Rproj": ""}, "r", "study.Rproj"),
        ({"pyproject.toml": "", "analysis.R": ""}, "python", "pyproject.toml"),
        ({"requirements.txt": ""}, "python", "requirements.txt"),
        ({"clean.R": ""}, "r", "clean.R"),
        ({"report.qmd": "```{python}\nx = 1\n```\n"}, "python", "report.qmd"),
        ({"report.qmd": "```{r}\nx <- 1\n```\n"}, "r", "report.qmd"),
        ({"run.do": "", "renv.lock": ""}, None, None),
        ({}, None, None),
    ],
)
def test_detect_language_as_fw_does(
    tmp_path: Path, files: dict[str, str], language: str | None, source: str | None
) -> None:
    from framework.adopt import detect_language

    for name, text in files.items():
        (tmp_path / name).write_text(text, encoding="utf-8")
    assert detect_language(tmp_path) == (language, source)


def test_an_r_project_is_not_adopted_as_python(
    existing: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (existing / "old-study.Rproj").write_text("Version: 1.0\n", encoding="utf-8")
    with pytest.raises(fw.FrameworkError, match=r"an R project \(from old-study\.Rproj\)"):
        fw.adopt(existing, yes=True)
    assert not (existing / "settings.yml").exists()
    assert main(["adopt", "--path", str(existing), "--yes"]) == 1
    assert "--force" in capsys.readouterr().err
    assert main(["adopt", "--path", str(existing), "--yes", "--force"]) == 0
    assert "Adopting it as Python" in capsys.readouterr().err
    assert _yaml.read(existing / "settings.yml")["default"]["language"] == "python"


def _git(root: Path, *args: str) -> None:
    import subprocess

    env = {
        **os.environ,
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "A",
        "GIT_AUTHOR_EMAIL": "a@b.c",
        "GIT_COMMITTER_NAME": "A",
        "GIT_COMMITTER_EMAIL": "a@b.c",
    }
    subprocess.run(["git", "-C", str(root), *args], check=True, env=env, capture_output=True)


@pytest.mark.skipif(not __import__("shutil").which("git"), reason="git is not installed")
def test_adopt_merges_its_gitignore_rules(tmp_path: Path) -> None:
    root = tmp_path / "study"
    for folder in ("scripts", "report_files", "secret stuff/Private notes"):
        (root / folder).mkdir(parents=True)
    (root / "report_files/plot.png").write_bytes(b"png")
    mine = "# mine\n.Renviron\n/.quarto/\nkeep-me.txt\n"
    (root / ".gitignore").write_text(mine, encoding="utf-8")
    (root / "notes").mkdir()
    (root / "notes/private-ish").mkdir()
    _git(root, "init", "-q")
    _git(root, "add", ".gitignore", "report_files/plot.png")
    _git(root, "commit", "-q", "-m", "init")
    fw.adopt(root, yes=True)
    text = (root / ".gitignore").read_text(encoding="utf-8")
    # The user's lines stay first, untouched; the rest follow in one block
    assert text.startswith(mine + "\n# Added by framework.adopt()\n")
    added = text[len(mine) :].splitlines()
    assert added.count(".Renviron") == 0
    assert "/.quarto/" not in added
    assert {".env", "framework.db", "**/*.quarto_ipynb", ".quarto_backups/"} <= set(added)
    # The folder that looks private, which no template rule covers
    assert "/secret stuff/Private notes/" in added
    # *_files/ would hide the tracked plot
    assert "*_files/" not in added
    spec = json.loads((root / ".framework/spec.json").read_text(encoding="utf-8"))
    assert spec["git"]["gitignore_content"] == text.rstrip("\n")
    # Running the merge again adds nothing
    from framework._gitignore import missing_rules

    assert missing_rules(root, spec["git"]["gitignore_content"]).add == []


def test_quarto_is_on_for_a_quarto_file_up_to_three_folders_deep(tmp_path: Path) -> None:
    def quarto_of(name: str, files: list[str]) -> tuple[bool, bool]:
        root = tmp_path / name
        for file in files:
            (root / file).parent.mkdir(parents=True, exist_ok=True)
            (root / file).write_text("x\n", encoding="utf-8")
        (root / "pyproject.toml").write_text("[project]\nname = 'x'\n", encoding="utf-8")
        fw.adopt(root, yes=True)
        settings = _yaml.read(root / "settings.yml")["default"]
        spec = json.loads((root / ".framework/spec.json").read_text(encoding="utf-8"))
        return settings["tools"]["quarto"], spec["tools"]["quarto"]

    assert quarto_of("a", ["notebooks/_quarto.yml", "notebooks/a.qmd"]) == (True, True)
    assert quarto_of("b", ["report-template.qmd"]) == (True, True)
    assert quarto_of("c", ["a/b/c/deep.qmd"]) == (True, True)
    assert quarto_of("d", ["a/b/c/d/too-deep.qmd", "a/run.py"]) == (False, False)
    assert quarto_of("e", [".venv/x.qmd", "analysis/run.py"]) == (False, False)
