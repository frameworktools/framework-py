"""upgrade(): an older project brought to the current layout, as R's upgrade() does."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import framework as fw
from framework import _yaml
from framework.__main__ import main
from framework._spec_schema import problems as schema_problems

CLAUDE = """# Old Study

Project notes the user wrote.

## Framework Environment <!-- @framework:regenerate -->

Old text about the environment.

## Data Management <!-- @framework:regenerate -->

Old data table.

## Project Notes

Keep this paragraph.
"""


@pytest.fixture
def old(tmp_path: Path) -> Path:
    """A Python project as an older framework-py left it: no spec.json, no tools: or
    security: block, an old connections layout, and CLAUDE.md holding the full guide."""
    root = tmp_path / "old-study"
    for folder in ("inputs/private/raw", "notebooks", "settings", ".claude/skills/mine"):
        (root / folder).mkdir(parents=True)
    (root / "settings.yml").write_text(
        "# Study settings\n"
        "default:\n"
        "  project_name: Old Study\n"
        "  project_type: project_sensitive  # chosen at the start\n"
        "  language: python\n"
        "  directories:\n"
        "    inputs_private_raw: inputs/private/raw\n"
        "    notebooks: notebooks\n"
        "  ai:\n"
        "    enabled: true\n"
        "    canonical_file: CLAUDE.md\n"
        "    assistants: [claude]\n"
        "  connections: settings/connections.yml\n"
        "ehr_source: optum\n",
        encoding="utf-8",
    )
    (root / "settings/connections.yml").write_text(
        "options:\n"
        "  default_connection: omop\n"
        "connections:\n"
        "  omop:\n"
        "    driver: postgres\n"
        "    host: !expr Sys.getenv('OMOP_HOST')\n"
        "    password: hunter2\n"
        "  bucket:\n"
        "    driver: s3\n"
        "    bucket: raw-files\n",
        encoding="utf-8",
    )
    (root / ".gitignore").write_text("# mine\n.env\nnotes.txt\n", encoding="utf-8")
    (root / "CLAUDE.md").write_text(CLAUDE, encoding="utf-8")
    (root / ".claude/skills/mine/SKILL.md").write_text("mine\n", encoding="utf-8")
    (root / "_quarto.yml").write_text(
        "project:\n  title: Old\nformat:\n  html:\n    theme: cosmo\n", encoding="utf-8"
    )
    return root


def test_plans_without_changing_anything(old: Path, capsys: pytest.CaptureFixture[str]) -> None:
    before = {p: p.read_bytes() for p in old.rglob("*") if p.is_file()}
    result = fw.upgrade(old)
    assert not result.applied
    assert {p: p.read_bytes() for p in old.rglob("*") if p.is_file()} == before
    steps = list(zip(result.actions["step"], result.actions["path"], strict=True))
    assert steps == [
        ("settings", "settings.yml"),
        ("connections", "settings/connections.yml"),
        ("gitignore", ".gitignore"),
        ("ai", "AGENTS.md"),
        ("ai", "CLAUDE.md"),
        ("ai", "settings.yml"),
        ("ai", "AGENTS.md"),
        ("ai", ".claude/settings.json"),
        ("skills", ".claude/skills/framework-workflow"),
        ("skills", ".claude/skills/framework-data"),
        ("skills", ".claude/skills/framework-packages"),
        ("skills", ".claude/skills/framework-outputs"),
        ("skills", ".claude/skills/framework-sensitive-data"),
        ("quarto", "_quarto.yml"),
        ("spec", ".framework/spec.json"),
    ]
    changes: dict[str, str] = {}
    for path, change in zip(result.actions["path"], result.actions["change"], strict=True):
        changes.setdefault(path, change)
    assert changes["settings.yml"].startswith("add tools (quarto: true, uv: false); add security")
    assert "project_type is project_sensitive" in changes["settings.yml"]
    assert changes["settings/connections.yml"] == (
        "rewrite in the current layout: 1 database(s), default_database: omop"
    )
    err = capsys.readouterr().err
    assert "keys outside default: (ehr_source)" in err
    assert "settings/connections.yml has a password or token written in" in err
    assert "hunter2" not in err
    assert "Dry run: nothing was changed" in err


def test_applies_the_plan_once(old: Path) -> None:
    result = fw.upgrade(old, dry_run=False)
    assert result.applied
    settings = (old / "settings.yml").read_text(encoding="utf-8")
    # Comments and the user's keys stay
    assert settings.startswith("# Study settings\ndefault:\n")
    assert "project_type: project_sensitive  # chosen at the start" in settings
    config = _yaml.load(settings)["default"]
    assert config["security"] == {"sensitive": True}
    assert config["tools"] == {"quarto": True, "uv": False}
    assert config["ai"]["canonical_file"] == "AGENTS.md"

    connections = (old / "settings/connections.yml").read_text(encoding="utf-8")
    assert "host: !expr Sys.getenv('OMOP_HOST')" in connections
    parsed = _yaml.load(connections)["connections"]
    assert parsed["default_database"] == "omop"
    assert set(parsed["databases"]) == {"omop"}
    assert set(parsed["storage_buckets"]) == {"bucket"}
    assert parsed["databases"]["omop"]["password"] == "hunter2"

    gitignore = (old / ".gitignore").read_text(encoding="utf-8")
    assert gitignore.startswith("# mine\n.env\nnotes.txt\n\n# Added by framework.upgrade()\n")
    assert gitignore.count("\n.env\n") == 1

    agents = (old / "AGENTS.md").read_text(encoding="utf-8")
    assert (old / "CLAUDE.md").read_text(encoding="utf-8") == "@AGENTS.md\n"
    assert "Project notes the user wrote." in agents
    assert "Keep this paragraph." in agents
    assert "Old text about the environment." not in agents
    assert "`fw.scaffold()`" in agents
    assert "inputs/private/raw" in agents

    guards = json.loads((old / ".claude/settings.json").read_text(encoding="utf-8"))
    assert "Read(/inputs/private/**)" in guards["permissions"]["deny"]
    assert (old / ".claude/skills/framework-sensitive-data/SKILL.md").is_file()
    assert (old / ".claude/skills/mine/SKILL.md").read_text(encoding="utf-8") == "mine\n"

    quarto = (old / "_quarto.yml").read_text(encoding="utf-8")
    assert "theme: cosmo" in quarto and "type: default" in quarto

    spec = json.loads((old / ".framework/spec.json").read_text(encoding="utf-8"))
    assert spec["type"] == "project_sensitive"
    assert spec["security"]["sensitive"] is True
    assert spec["connections"]["databases"]["omop"]["password"] == 'env("OMOP_PASSWORD")'
    assert spec["source"] == {"origin": "python", "framework_version": fw.__version__}
    assert schema_problems(spec) == []
    assert spec["git"]["gitignore_content"] == gitignore.rstrip("\n")
    assert "framework-sensitive-data" in spec["skills"]
    assert fw.spec_check(spec) == []

    # The files as they were, where git doesn't see them
    assert result.backup_location is not None
    assert (result.backup_location / "settings.yml").is_file()
    assert (result.backup_location / "CLAUDE.md").read_text(encoding="utf-8") == CLAUDE
    assert "backups/" in (old / ".framework/.gitignore").read_text(encoding="utf-8")

    again = fw.upgrade(old)
    assert again.actions.empty


def test_a_1x_config_yml_becomes_settings_yml(tmp_path: Path) -> None:
    root = tmp_path / "v1"
    root.mkdir()
    (root / "pyproject.toml").write_text("[project]\nname = 'v1'\n", encoding="utf-8")
    (root / "config.yml").write_text(
        "default:\n  project_name: V1\n  options:\n    data_dir: data\n", encoding="utf-8"
    )
    result = fw.upgrade(root, dry_run=False)
    assert list(result.actions["path"][:2]) == ["settings.yml", "config.yml"]
    assert not (root / "config.yml").exists()
    config = _yaml.read(root / "settings.yml")["default"]
    # The files say Python, so the settings say it too
    assert config["language"] == "python"
    spec = json.loads((root / ".framework/spec.json").read_text(encoding="utf-8"))
    assert "upgraded_from" not in spec["source"]
    assert fw.upgrade(root).actions.empty


def test_refuses_other_languages_and_folders(tmp_path: Path) -> None:
    with pytest.raises(fw.FrameworkError, match=r"use framework\.adopt\(\)"):
        fw.upgrade(tmp_path)
    (tmp_path / "settings.yml").write_text(
        "default:\n  project_name: R\n  language: r\n", encoding="utf-8"
    )
    with pytest.raises(fw.FrameworkError, match=r"framework::upgrade\(\)"):
        fw.upgrade(tmp_path)
    (tmp_path / "settings.yml").write_text("default:\n  project_name: R\n", encoding="utf-8")
    (tmp_path / "study.Rproj").write_text("", encoding="utf-8")
    with pytest.raises(fw.FrameworkError, match=r"reads as an R project \(the files say r"):
        fw.upgrade(tmp_path)
    assert not (tmp_path / ".framework").exists()


def test_the_command_line(old: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(old)
    assert main(["upgrade"]) == 0
    assert not (old / ".framework").exists()
    assert main(["upgrade", "--apply", "--no-backup"]) == 0
    assert (old / ".framework/spec.json").is_file()
    assert not (old / ".framework/backups").exists()


def test_adopt_points_an_existing_project_to_upgrade(old: Path) -> None:
    with pytest.raises(fw.FrameworkError, match=r"framework\.upgrade\(\)"):
        fw.adopt(old, yes=True)


def test_specs_hold_only_what_spec_v1_allows(old: Path) -> None:
    (old / "settings/scaffold.yml").write_text(
        "scaffold:\n  positron: yes\n  seed_on_scaffold: true\n  seed: 123\n  extra: 1\n",
        encoding="utf-8",
    )
    text = (old / "settings.yml").read_text(encoding="utf-8")
    (old / "settings.yml").write_text(
        text.replace("  ai:\n", "  scaffold: settings/scaffold.yml\n  ai:\n"), encoding="utf-8"
    )
    fw.upgrade(old, dry_run=False)
    spec_path = old / ".framework/spec.json"
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    # positron: yes names the IDE; numbers become the schema's strings; the rest stays out
    assert spec["scaffold"]["ide"] == "positron"
    assert spec["scaffold"]["seed"] == "123"
    assert "positron" not in spec["scaffold"] and "extra" not in spec["scaffold"]
    assert schema_problems(spec) == []

    # A spec an earlier upgrade() wrote is repaired
    spec["scaffold"]["positron"] = True
    spec["source"]["upgraded_from"] = "framework before 2.0 (no spec.json)"
    spec_path.write_text(json.dumps(spec, indent=2) + "\n", encoding="utf-8")
    result = fw.upgrade(old, dry_run=False)
    change = result.actions.set_index("path").loc[".framework/spec.json", "change"]
    assert change.startswith("drop scaffold.positron, source.upgraded_from (not part of spec.v1")
    assert schema_problems(json.loads(spec_path.read_text(encoding="utf-8"))) == []
    assert fw.upgrade(old).actions.empty


def test_prune_and_ready() -> None:
    from framework._spec_schema import prune, ready
    from framework.spec.merge import SpecError

    spec = json.loads(
        (Path(__file__).parent / "shared/conformance/create/python/project-scripts.json").read_text(
            encoding="utf-8"
        )
    )["spec"]
    spec["author"]["nickname"] = "E"
    spec["security"]["reviewed"] = ["a.csv"]
    pruned, dropped = prune(spec)
    assert dropped == ["author.nickname", "security.reviewed"]
    assert schema_problems(pruned) == []
    pruned["type"] = "Not A Label"
    with pytest.raises(SpecError, match="type: 'Not A Label' does not match"):
        ready(pruned)


@pytest.fixture
def plain(tmp_path: Path) -> Path:
    """A Python project that isn't sensitive, with AI on and a Claude skills folder."""
    root = tmp_path / "plain"
    for folder in ("inputs/raw", "outputs/tables", "notebooks", ".claude/skills/mine"):
        (root / folder).mkdir(parents=True)
    (root / "settings.yml").write_text(
        "default:\n"
        "  project_name: Plain\n"
        "  project_type: project\n"
        "  language: python\n"
        "  directories:\n"
        "    notebooks: notebooks\n"
        "    inputs_raw: inputs/raw\n"
        "    outputs_tables: outputs/tables\n"
        "  render_dirs:\n"
        "    notebooks: outputs/notebooks\n"
        "  ai:\n"
        "    enabled: true\n"
        "    canonical_file: AGENTS.md\n"
        "    assistants: [claude]\n",
        encoding="utf-8",
    )
    (root / ".claude/skills/mine/SKILL.md").write_text("mine\n", encoding="utf-8")
    return root


def test_type_project_sensitive_makes_a_project_sensitive_in_one_pass(plain: Path) -> None:
    plan = fw.upgrade(plain, type="project_sensitive")
    change = " ".join(plan.actions.loc[plan.actions["path"] == "settings.yml", "change"])
    assert "set project_type: project_sensitive (was project)" in change
    assert "add the private and public folders" in change
    notes = " ".join(plan.notes)
    assert "save_table() to outputs/private/tables (was outputs/tables)" in notes
    assert "inputs_raw (inputs/raw)" in notes
    assert "render_dirs.notebooks (outputs/notebooks)" in notes

    fw.upgrade(plain, type="project_sensitive", dry_run=False)
    settings = _yaml.read(plain / "settings.yml")["default"]
    assert settings["project_type"] == "project_sensitive"
    assert settings["security"]["sensitive"] is True
    assert settings["directories"]["inputs_raw"] == "inputs/raw"
    assert settings["directories"]["outputs_private_tables"] == "outputs/private/tables"
    assert settings["directories"]["inputs_private_raw"] == "inputs/private/raw"
    assert "/inputs/private/" in (plain / ".gitignore").read_text(encoding="utf-8").splitlines()
    guards = json.loads((plain / ".claude/settings.json").read_text(encoding="utf-8"))
    assert "Read(/inputs/private/**)" in guards["permissions"]["deny"]
    assert (plain / ".claude/skills/framework-sensitive-data/SKILL.md").is_file()
    spec = json.loads((plain / ".framework/spec.json").read_text(encoding="utf-8"))
    assert spec["type"] == "project_sensitive" and spec["security"]["sensitive"] is True
    assert fw.upgrade(plain).actions.empty
    assert fw.upgrade(plain, type="project_sensitive").actions.empty


def test_a_project_made_sensitive_by_hand_hears_where_outputs_go(plain: Path) -> None:
    fw.upgrade(plain, dry_run=False)
    spec = json.loads((plain / ".framework/spec.json").read_text(encoding="utf-8"))
    assert spec["security"]["sensitive"] is False
    text = (plain / "settings.yml").read_text(encoding="utf-8")
    (plain / "settings.yml").write_text(
        text.replace("sensitive: false", "sensitive: true"), encoding="utf-8"
    )
    plan = fw.upgrade(plain)
    assert any(
        "save_figure() to outputs/private/figures (was outputs/figures)" in n for n in plan.notes
    )
    with pytest.raises(fw.FrameworkError, match="type must be one of"):
        fw.upgrade(plain, type="lab")
    # A sensitive project made another type stays sensitive
    plan = fw.upgrade(plain, type="course")
    assert any("stays sensitive" in n for n in plan.notes)


def test_refreshes_the_spec_author_and_gitignore_copy_and_agents_skills(
    old: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import hashlib

    from framework import skill_sync

    fw.upgrade(old, dry_run=False)
    spec_path = old / ".framework/spec.json"
    gitignore = (old / ".gitignore").read_text(encoding="utf-8")
    assert json.loads(spec_path.read_text(encoding="utf-8"))["git"]["gitignore_content"] == (
        gitignore.rstrip("\n")
    )

    # A copy in .agents/skills from an older release, with no lock entry: the lock's
    # entry for .claude/skills/... says nothing about it
    released = "---\nname: framework-data\n---\nAn older release.\n"
    digest = hashlib.sha256(released.encode()).hexdigest()
    monkeypatch.setattr(skill_sync, "_shipped_history", lambda: {"framework-data": [digest]})
    agents = old / ".agents/skills/framework-data"
    agents.mkdir(parents=True)
    (agents / "SKILL.md").write_text(released, encoding="utf-8")

    # The author is filled in and a .gitignore rule added by hand
    (old / "settings/author.yml").write_text(
        "author:\n  name: Ada Lovelace\n  email: ada@example.org\n  affiliation: JHU\n"
        "  github: ada\n",
        encoding="utf-8",
    )
    text = (old / "settings.yml").read_text(encoding="utf-8")
    (old / "settings.yml").write_text(
        text.replace("  ai:\n", "  author: settings/author.yml\n  ai:\n", 1), encoding="utf-8"
    )
    with (old / ".gitignore").open("a", encoding="utf-8") as handle:
        handle.write("/exports/\n")
    plan = fw.upgrade(old)
    change = plan.actions.set_index("path").loc[".framework/spec.json", "change"]
    assert "author" in change and "git.gitignore_content" in change
    assert ".agents/skills/framework-data" in set(plan.actions["path"])

    fw.upgrade(old, dry_run=False)
    assert "older release" not in (agents / "SKILL.md").read_text(encoding="utf-8")
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    assert spec["author"]["name"] == "Ada Lovelace"
    assert "github" not in spec["author"]
    assert "/exports/" in spec["git"]["gitignore_content"]
    assert schema_problems(spec) == []
    assert fw.upgrade(old).actions.empty
