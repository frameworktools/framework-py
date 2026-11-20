"""App projects (Shiny for Python, FastAPI): no fw.scaffold() leftovers, and an AGENTS.md
that describes the app."""

from __future__ import annotations

import base64
import json
import random
import re
from pathlib import Path
from typing import Any

import pytest

import framework as fw
from framework import db
from framework._agents_md import agents_md
from framework.create import project_create_from_layers
from framework.settings import settings_read

GOLDEN = Path(__file__).parent / "golden"
APPS = {"shiny_python": "app-shiny-python", "fastapi": "app-fastapi"}


@pytest.fixture(params=sorted(APPS))
def app(request: pytest.FixtureRequest, tmp_path: Path) -> Path:
    return project_create_from_layers(GOLDEN / APPS[request.param] / "layers.json", tmp_path / "a")


def sections(text: str) -> list[str]:
    return [re.sub(r" <!--.*", "", line[3:]) for line in text.splitlines() if line[:3] == "## "]


def test_an_app_has_no_scaffold_leftovers(app: Path) -> None:
    assert not (app / "scaffold.py").exists()
    assert not (app / "framework.db").exists()
    config = settings_read(app / "settings.yml")
    assert "quarto" not in config
    assert "connections:" not in (app / "settings.yml").read_text(encoding="utf-8")
    workspace = json.loads((app / "demo-app.code-workspace").read_text(encoding="utf-8"))
    assert "files.associations" not in workspace["settings"]
    assert config["scaffold"] == {"workflow": "scripts", "notebook_format": "script"}


def test_settings_readers_tolerate_the_missing_keys(
    app: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """fw.scaffold() still works in an app, and creates framework.db when it runs."""
    monkeypatch.chdir(app)
    random.seed(7)
    expected = random.random()
    random.seed(7)
    assert fw.scaffold() == app.resolve()
    assert random.random() == expected  # No seed: scaffold() left it alone
    assert (app / "framework.db").is_file()


def test_database_readers_create_it_lazily(app: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(app)
    assert fw.cache("answer", 42) == 42
    assert fw.cache_get("answer") == 42
    assert (app / "framework.db").is_file()
    with db.connect(app) as connection:
        assert connection.execute("SELECT count(*) FROM cache").fetchone()[0] == 1


def test_analysis_projects_keep_their_scaffold_files(tmp_path: Path) -> None:
    root = project_create_from_layers(GOLDEN / "project/layers.json", tmp_path / "p")
    assert (root / "scaffold.py").is_file()
    assert (root / "framework.db").is_file()
    assert "seed" in settings_read(root / "settings.yml")["scaffold"]


def test_an_app_guide_describes_the_app(app: Path) -> None:
    text = (app / "AGENTS.md").read_text(encoding="utf-8")
    assert sections(text) == [
        "Skills",
        "Framework Environment",
        "Installed Packages",
        "Project Notes",
    ]
    assert "## Framework Environment <!-- @framework:regenerate -->" in text
    assert "The app doesn't call `fw.scaffold()`." in text
    assert "### scaffold()" not in text
    assert "scaffold.py" not in text
    if (app / "app.py").exists():
        assert "This is a Shiny for Python app." in text
        assert "Static files go in `www/`, data files in `data/`, and tests in `tests/`." in text
        assert "`uv run shiny run --reload app.py`" in text
    else:
        assert "This is a FastAPI app." in text
        assert "The endpoints are in `app/main.py`." in text
        assert "`uv run uvicorn app.main:app --reload`. The API docs are at `/docs`." in text


def spec(**extra: Any) -> dict[str, Any]:
    return {"name": "Study", "type": "project", "language": "python", **extra}


def test_folder_names_come_from_the_spec() -> None:
    directories = {"www": "static", "data": "inputs", "tests": "checks", "app": "api"}
    shiny = agents_md(spec(type="shiny_python", directories=directories), ".agents/skills", None)
    assert "Static files go in `static/`, data files in `inputs/`, and tests in `checks/`." in shiny
    fastapi = agents_md(spec(type="fastapi", directories=directories), ".agents/skills", None)
    assert "The endpoints are in `api/main.py`. Tests in `checks/`" in fastapi
    assert "`uv run uvicorn api.main:app --reload`" in fastapi


def test_workflow_guidelines_need_the_workflow_skill() -> None:
    without = agents_md(spec(skills={"framework-data": {}}), ".agents/skills", None)
    assert "Workflow Guidelines" not in sections(without)
    assert "Data Management" in sections(without)
    with_skill = agents_md(spec(skills={"framework-workflow": {}}), ".agents/skills", None)
    assert "Workflow Guidelines" in sections(with_skill)
    assert "`framework-workflow` skill" in with_skill
    app = agents_md(spec(type="fastapi", skills={"framework-workflow": {}}), ".x", None)
    assert "Workflow Guidelines" not in sections(app)


SKILL = "---\nname: team-style\ndescription: Use when\n  writing | styling.\n---\n\n# Style\n"


@pytest.mark.parametrize(
    "entry",
    [
        {"content": SKILL},
        {"bundle": [{"path": "SKILL.md", "content": base64.b64encode(SKILL.encode()).decode()}]},
    ],
    ids=["content", "bundle"],
)
def test_skill_descriptions_come_from_the_spec(entry: dict[str, Any]) -> None:
    text = agents_md(spec(skills={"team-style": entry}), ".agents/skills", None)
    assert "| `team-style` | Use when writing \\| styling. |" in text


def test_skill_descriptions_come_from_the_written_skill(tmp_path: Path) -> None:
    (tmp_path / ".agents/skills/team-style").mkdir(parents=True)
    (tmp_path / ".agents/skills/team-style/SKILL.md").write_text(SKILL, encoding="utf-8")
    text = agents_md(
        spec(skills={"team-style": {"package": True}}), ".agents/skills", None, tmp_path
    )
    assert "| `team-style` | Use when writing \\| styling. |" in text


def test_a_created_project_describes_every_skill(app: Path) -> None:
    rows = [
        line
        for line in (app / "AGENTS.md").read_text(encoding="utf-8").splitlines()
        if line.startswith("| `")
    ]
    assert rows
    assert all(row.split(" | ")[1].strip() for row in rows)
    assert any(row.startswith("| `git-workflow` | Use when reviewing changes") for row in rows)
