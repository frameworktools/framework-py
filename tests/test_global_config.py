from __future__ import annotations

import shutil
from pathlib import Path

import pytest

import framework as fw


def test_configure_global_merges_and_replaces() -> None:
    assert fw.configure_global() == {}
    fw.configure_global(
        {
            "author": {"name": "Ada", "email": "ada@example.com"},
            "defaults": {"env": {"DB_HOST": "db", "DB_PORT": "5432"}},
            "project_types": {"project": {"directories": {"notebooks": "nb", "scripts": "s"}}},
        }
    )
    updated = fw.configure_global(
        {
            "author": {"name": "Ada Lovelace"},
            "defaults": {"env": {"DB_HOST": "other"}},
            "project_types": {"project": {"directories": {"notebooks": "work", "scripts": ""}}},
            "global": {"projects_root": str(Path.home() / "code")},
        }
    )
    assert updated["author"] == {"name": "Ada Lovelace", "email": "ada@example.com"}
    assert updated["defaults"]["env"] == {"DB_HOST": "other"}
    assert updated["project_types"]["project"]["directories"] == {"notebooks": "work"}
    assert updated["global"]["projects_root"] == "~/code"
    assert fw.global_settings() == updated


def test_configure_global_refuses_secrets_and_bad_values() -> None:
    with pytest.raises(fw.SettingsError, match="secrets must not be stored"):
        fw.configure_global({"defaults": {"env": {"DB_PASSWORD": "hunter2"}}})
    assert fw.global_settings() == {}
    fw.configure_global({"defaults": {"env": {"DB_PASSWORD": ""}}})
    with pytest.raises(fw.SettingsError, match=r"\(API_TOKEN, DB_PASSWORD\)"):
        fw.configure_global(
            {"defaults": {"env": {"raw": "export API_TOKEN='abc'\nDB_PASSWORD=x # y\n"}}}
        )
    with pytest.raises(fw.SettingsError, match=r"\(SECRET_KEY\)"):
        fw.configure_global({"defaults": {"env": {"variables": {"SECRET_KEY": "k"}}}})
    fw.configure_global({"defaults": {"env": "DB_PASSWORD= # fill in\nAPI_TOKEN=''\nHOST=db\n"}})
    with pytest.raises(fw.SettingsError, match="Unknown project types"):
        fw.configure_global({"project_types": {"thesis": {}}})
    with pytest.raises(fw.SettingsError, match=r"author\.name must be text"):
        fw.configure_global({"author": {"name": 3}})


def test_load_settings_catalog() -> None:
    catalog = fw.load_settings_catalog()
    # The notebook format follows the workflow or the tools, so the defaults leave it out
    assert "notebook_format" not in catalog["defaults"]["scaffold"]
    assert "/inputs/private/" in catalog["project_types"]["project_sensitive"]["gitignore"]
    (fw.config_dir()).mkdir(parents=True, exist_ok=True)
    (fw.config_dir() / "settings-catalog.yml").write_text(
        "defaults:\n  scaffold: {notebook_format: jupyter}\n", encoding="utf-8"
    )
    assert fw.load_settings_catalog()["defaults"]["scaffold"]["notebook_format"] == "jupyter"
    assert (
        "notebook_format"
        not in (fw.load_settings_catalog(include_user=False)["defaults"]["scaffold"])
    )


def test_ai_skills_update(tmp_path: Path) -> None:
    root = fw.new("Skills", tmp_path / "skills", "project_sensitive")
    skill = root / ".claude/skills/framework-data/SKILL.md"
    skill.write_text("stale\n", encoding="utf-8")
    (root / ".claude/skills/mine").mkdir()
    (root / ".claude/skills/mine/SKILL.md").write_text("custom\n", encoding="utf-8")
    shutil.rmtree(root / ".claude/skills/framework-sensitive-data")
    updated = fw.ai_skills_update(root)
    # Audit 2026-11-05: skills that were already current were reported as updated
    assert updated == [".claude/skills/framework-sensitive-data"]
    assert (root / ".claude/skills/framework-sensitive-data/SKILL.md").is_file()
    assert fw.ai_skills_update(root) == []
    assert skill.read_text(encoding="utf-8") == "stale\n"
    assert (root / ".claude/skills/mine/SKILL.md").read_text(encoding="utf-8") == "custom\n"


def test_ai_skills_update_without_spec(project: Path) -> None:
    (project / "settings.yml").write_text(
        "default:\n  project_type: project\n  ai: {assistants: [codex]}\n", encoding="utf-8"
    )
    updated = fw.ai_skills_update(project)
    assert ".agents/skills/framework-workflow" in updated
    assert (project / ".agents/skills/framework-workflow/SKILL.md").is_file()


def test_ai_skills_update_refuses_a_folder_that_is_not_a_project(tmp_path: Path) -> None:
    # Audit 2026-11-05: it wrote skill folders into any folder
    with pytest.raises(fw.ProjectNotFoundError, match="not a Framework project"):
        fw.ai_skills_update(tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_ai_skills_update_updates_skills_from_an_older_release(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import hashlib

    from framework import skill_sync

    old = "---\nname: framework-data\n---\nAn older release.\n"
    shipped = hashlib.sha256(old.encode()).hexdigest()
    monkeypatch.setattr(skill_sync, "_shipped_history", lambda: {"framework-data": [shipped]})
    (project / "settings.yml").write_text(
        "default:\n  project_type: project\n  ai: {assistants: [claude]}\n", encoding="utf-8"
    )
    skills = project / ".claude/skills"
    # Installed before skills.lock.json: no lock entry. Windows line endings count too.
    (skills / "framework-data").mkdir(parents=True)
    (skills / "framework-data/SKILL.md").write_bytes(old.replace("\n", "\r\n").encode())
    (skills / "framework-outputs").mkdir()
    (skills / "framework-outputs/SKILL.md").write_text("my own notes\n", encoding="utf-8")
    updated = fw.ai_skills_update(project)
    assert ".claude/skills/framework-data" in updated
    assert "older release" not in (skills / "framework-data/SKILL.md").read_text(encoding="utf-8")
    assert list((project / ".framework/skills-backups").iterdir())
    assert ".claude/skills/framework-outputs" not in updated
    text = (skills / "framework-outputs/SKILL.md").read_text(encoding="utf-8")
    assert text == "my own notes\n"


def test_the_skill_history_is_current() -> None:
    import subprocess
    import sys

    root = Path(__file__).resolve().parent.parent
    result = subprocess.run(
        [sys.executable, "scripts/skill_history.py", "--check"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout
