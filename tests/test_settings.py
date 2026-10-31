from __future__ import annotations

import os
from pathlib import Path

import pytest

import framework as fw
from framework import _yaml
from framework.settings import SettingsWarning, active_environment, merge_env, modify_list


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_active_environment_order() -> None:
    assert active_environment() == "default"
    os.environ["R_CONFIG_NAME"] = "named"
    assert active_environment() == "named"
    os.environ["R_CONFIG_ACTIVE"] = "r"
    assert active_environment() == "r"
    os.environ["FW_CONFIG_ACTIVE"] = "fw"
    assert active_environment() == "fw"


def test_flat_file_has_no_environments(project: Path) -> None:
    write(project / "settings.yml", "project_name: Flat\n")
    config = fw.settings_read()
    assert config["project_name"] == "Flat"
    for section in ("data", "connections", "git", "security", "packages", "directories", "options"):
        assert config[section] == {}


def test_settings_found_from_a_subfolder(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (project / "notebooks").mkdir()
    monkeypatch.chdir(project / "notebooks")
    assert fw.settings("project_name") == "Test"
    assert fw.project_root() == project.resolve()


def test_config_yml_is_the_fallback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    write(tmp_path / "config.yml", "default:\n  project_name: Old\n")
    monkeypatch.chdir(tmp_path)
    assert fw.settings("project_name") == "Old"


def test_no_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    with pytest.raises(fw.SettingsError, match="No settings file found"):
        fw.settings_read()
    with pytest.raises(fw.ProjectNotFoundError):
        fw.project_root()


def test_settings_lookup_rules(project: Path) -> None:
    write(
        project / "settings.yml",
        "default:\n"
        "  directories: {notebooks: nb}\n"
        "  options: {scripts_dir: legacy_scripts}\n"
        "  connections: {db: {host: localhost}}\n",
    )
    assert fw.settings("notebooks") == "nb"
    assert fw.settings("scripts") == "legacy_scripts"
    assert fw.settings("connections.db.host") == "localhost"
    assert fw.settings("connections.db.port", default=5432) == 5432
    assert fw.settings()["directories"] == {"notebooks": "nb"}


def test_split_files_nest_and_resolve_from_the_root(project: Path) -> None:
    write(project / "settings.yml", "default:\n  connections: settings/connections.yml\n")
    write(project / "settings/connections.yml", "connections: settings/inner.yml\n")
    write(project / "settings/inner.yml", "connections: {db: {driver: sqlite}}\n")
    assert fw.settings("connections.db.driver") == "sqlite"


def test_split_file_environments(project: Path) -> None:
    write(project / "settings.yml", "default:\n  git: settings/git.yml\nproduction: {}\n")
    write(
        project / "settings/git.yml",
        "default:\n  git: {use_git: true}\nproduction:\n  git: {use_git: false}\n",
    )
    assert fw.settings("git.use_git") is True
    os.environ["FW_CONFIG_ACTIVE"] = "production"
    assert fw.settings("git.use_git") is False


def test_missing_split_file(project: Path) -> None:
    write(project / "settings.yml", "default:\n  data: settings/data.yml\n")
    with pytest.raises(fw.SettingsError, match=r"settings/data.yml not found"):
        fw.settings_read()


def test_circular_split_files(project: Path) -> None:
    write(project / "settings.yml", "default:\n  a: settings/a.yml\n")
    write(project / "settings/a.yml", "a: settings/b.yml\n")
    write(project / "settings/b.yml", "b: settings/a.yml\n")
    with pytest.raises(fw.SettingsError, match="Circular reference"):
        fw.settings_read()


def test_split_file_conflicts_warn_and_main_file_wins(project: Path) -> None:
    write(project / "settings.yml", "default:\n  owner: main\n  extra: settings/extra.yml\n")
    write(project / "settings/extra.yml", "extra: {x: 1}\nowner: split\n")
    with pytest.warns(SettingsWarning, match="defined in both main config"):
        assert fw.settings("owner") == "main"


def test_split_file_options_merge(project: Path) -> None:
    write(
        project / "settings.yml",
        "default:\n  options: {a: main}\n  extra: settings/extra.yml\n",
    )
    write(project / "settings/extra.yml", "extra: {}\noptions: {a: split, b: split}\n")
    assert fw.settings("options") == {"a": "main", "b": "split"}


def test_env_and_expr(project: Path) -> None:
    os.environ["FW_TEST_VALUE"] = "set"
    write(
        project / "settings.yml",
        "default:\n"
        '  a: env("FW_TEST_VALUE")\n'
        '  b: env("FW_TEST_MISSING", "fallback")\n'
        '  c: !expr Sys.getenv("FW_TEST_VALUE")\n'
        "  d: !expr Sys.getenv('FW_TEST_MISSING', unset = 'u')\n"
        '  e: env("FW_TEST_MISSING")\n'
        '  nested: [{f: env("FW_TEST_VALUE")}]\n',
    )
    config = fw.settings_read()
    assert [config[key] for key in "abcde"] == ["set", "fallback", "set", "u", ""]
    assert config["nested"] == [{"f": "set"}]


def test_other_r_expressions_are_refused(project: Path) -> None:
    write(project / "settings.yml", "default:\n  seed: !expr 1 + 1\n")
    with pytest.raises(fw.SettingsError, match=r"Cannot evaluate '!expr 1 \+ 1' in Python"):
        fw.settings_read()


def test_dotenv_location(project: Path) -> None:
    write(project / "settings.yml", 'default:\n  dotenv_location: secrets\n  v: env("FW_LOC")\n')
    write(project / "secrets/.env", "FW_LOC=found\n")
    assert fw.settings("v") == "found"


def test_yaml_booleans_follow_r() -> None:
    parsed = _yaml.load("a: y\nb: N\nc: yes\nd: off\ne: 'y'\nf: yeah\n")
    assert parsed == {"a": True, "b": False, "c": True, "d": False, "e": "y", "f": "yeah"}


def test_modify_list_follows_r() -> None:
    base = {"keep": 1, "map": {"a": 1, "b": 2}, "vector": ["x", "y"], "records": [{"a": 1}]}
    update = {"map": {"b": None, "c": 3}, "vector": ["z"], "records": [{"b": 2}], "new": 1}
    assert modify_list(base, update) == {
        "keep": 1,
        "map": {"a": 1, "c": 3},
        "vector": ["z"],
        "records": [{"a": 1}],
        "new": 1,
    }


def test_write_whole_file_wraps_in_default(project: Path) -> None:
    fw.settings_write({"project_name": "Rewritten"})
    assert _yaml.read(project / "settings.yml") == {"default": {"project_name": "Rewritten"}}


def test_write_section(project: Path) -> None:
    fw.settings_write({"notebooks": "nb"}, section="directories")
    assert fw.settings("notebooks") == "nb"
    assert fw.settings("project_name") == "Test"


def test_write_section_in_a_flat_file(project: Path) -> None:
    write(project / "settings.yml", "project_name: Flat\ndirectories:\n  notebooks: notebooks\n")
    fw.settings_write({"notebooks": "nb"}, section="directories")
    assert _yaml.read(project / "settings.yml") == {
        "project_name": "Flat",
        "directories": {"notebooks": "nb"},
    }


def test_write_section_into_split_file(project: Path) -> None:
    write(project / "settings.yml", "default:\n  git: settings/git.yml\n")
    write(project / "settings/git.yml", "git:\n  use_git: true\n  hooks: {}\n")
    fw.settings_write({"git": {"use_git": False}}, section="git")
    assert _yaml.read(project / "settings/git.yml") == {"git": {"use_git": False, "hooks": {}}}


def test_write_section_needs_a_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    with pytest.raises(fw.SettingsError, match="does not exist"):
        fw.settings_write({"a": 1}, section="directories")


def test_written_null_reads_back_as_null(project: Path) -> None:
    fw.settings_write({"extra_directories": None, "name": "x"})
    assert "extra_directories: ~" in (project / "settings.yml").read_text(encoding="utf-8")
    assert fw.settings("extra_directories", default="missing") is None


def test_merge_env_replaces_every_sequence() -> None:
    base = {"keep": 1, "map": {"a": 1, "b": 2}, "vector": ["x", "y"], "records": [{"a": 1}]}
    update = {"map": {"b": None, "c": 3}, "vector": ["z"], "records": [{"b": 2}], "new": 1}
    assert merge_env(base, update) == {
        "keep": 1,
        "map": {"a": 1, "c": 3},
        "vector": ["z"],
        "records": [{"b": 2}],
        "new": 1,
    }
    assert merge_env({"a": 1}, ["x"]) == ["x"]


def test_values_r_would_misread_are_quoted() -> None:
    text = _yaml.dump({"a": ".", "b": "y", "c": ".na", "d": "N", "e": "plain", "f": "yes"})
    assert text == "a: '.'\nb: 'y'\nc: '.na'\nd: 'N'\ne: plain\nf: 'yes'\n"
    assert _yaml.load(text) == {"a": ".", "b": "y", "c": ".na", "d": "N", "e": "plain", "f": "yes"}


@pytest.mark.parametrize(
    "values", [{"use_git": False}, {"git": {"use_git": False}}], ids=["bare", "wrapped"]
)
def test_write_section_into_split_file_with_or_without_the_wrapper(
    project: Path, values: dict[str, object]
) -> None:
    write(project / "settings.yml", "default:\n  git: settings/git.yml\n")
    write(project / "settings/git.yml", "git:\n  use_git: true\n  hooks: {}\n")
    fw.settings_write(values, section="git")
    assert _yaml.read(project / "settings/git.yml") == {"git": {"use_git": False, "hooks": {}}}
    assert fw.settings("git.use_git") is False


def test_write_section_into_an_empty_split_file(project: Path) -> None:
    write(project / "settings.yml", "default:\n  git: settings/git.yml\n")
    write(project / "settings/git.yml", "")
    fw.settings_write({"use_git": False}, section="git")
    assert _yaml.read(project / "settings/git.yml") == {"git": {"use_git": False}}


def test_write_section_wraps_an_older_unwrapped_split_file(project: Path) -> None:
    write(project / "settings.yml", "default:\n  git: settings/git.yml\n")
    write(project / "settings/git.yml", "use_git: true\nhooks: {}\n")
    fw.settings_write({"use_git": False}, section="git")
    assert _yaml.read(project / "settings/git.yml") == {"git": {"use_git": False, "hooks": {}}}
