from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

import framework as fw
from framework.__main__ import main
from framework.create import ensure_secrets_ignored, fill_template
from scripts.make_golden_trees import GOLDEN, build

CASES = sorted(p for p in GOLDEN.iterdir() if (p / "layers.json").is_file())


def expected_tree(case: Path) -> dict[str, str]:
    root = case / "expected"
    return {
        path.relative_to(root).as_posix(): path.read_text(encoding="utf-8")
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


@pytest.mark.parametrize("case", CASES, ids=[case.name for case in CASES])
def test_golden_tree(case: Path) -> None:
    """The same layers file always produces the same project (scripts/make_golden_trees.py)."""
    actual = build(case)
    expected = expected_tree(case)
    assert sorted(actual) == sorted(expected)
    for path, text in expected.items():
        assert actual[path] == text, path


def python_layer(**layer: Any) -> list[dict[str, Any]]:
    return [{"language": "python", **layer}]


# spec_resolve and spec_check ------------------------------------------------------


def test_missing_language_means_r() -> None:
    with pytest.raises(fw.SpecError, match="This is an R project"):
        fw.spec_resolve([{"type": "project"}], "R study")
    with pytest.raises(fw.SpecError, match="Stata project"):
        fw.spec_resolve([{"language": "stata"}], "Stata study")


def test_conflicts_are_reported() -> None:
    resolved = fw.spec_resolve(
        [
            {
                "kind": "facet",
                "key": "a",
                "layer": {"language": "python", "scaffold": {"seed": "1"}},
            },
            {"kind": "facet", "key": "b", "layer": {"scaffold": {"seed": "2"}}},
        ],
        "Conflicts",
    )
    assert [(c.path, c.layers) for c in resolved.conflicts] == [
        ("scaffold/seed", "facet a, facet b")
    ]
    assert resolved.spec["scaffold"]["seed"] == "2"


def test_author_argument_overrides_non_empty_fields() -> None:
    spec = fw.spec_resolve(
        python_layer(author={"name": "Layer", "email": "layer@example.org"}),
        "Authors",
        author={"name": "Override", "email": ""},
    ).spec
    assert spec["author"]["name"] == "Override"
    assert spec["author"]["email"] == "layer@example.org"


@pytest.mark.parametrize(
    ("layer", "message"),
    [
        ({"directories": {"x": "../outside"}}, "must be relative and stay inside"),
        ({"files": {"/etc/passwd": {"content": "x"}}}, "must be relative"),
        ({"env": {"DB_PASSWORD": "hunter2"}}, "secrets must not be stored"),
        ({"env": {"S3_ACCESS_KEY": "AKIA123"}}, "secrets must not be stored"),
        ({"data_hooks": {"inputs_private_raw": "fetch.R::fetch"}}, "file.py::function"),
        (
            {"packages": {"default_packages": {"dplyr": {"source": "cran", "auto_attach": True}}}},
            "source must be pypi or github",
        ),
        ({"scaffold": {"notebook_format": "rmarkdown"}}, "quarto or jupyter"),
        ({"skills": {"unknown-skill": {"package": True}}}, "not shipped"),
        (
            {"skills": {"mine": {"content": "---\nname: other\n---\n"}}},
            "frontmatter name is 'other'",
        ),
        ({"ai": {"assistants": {"cursor": True}}}, "unknown assistant 'cursor'"),
    ],
)
def test_invalid_specs(layer: dict[str, Any], message: str) -> None:
    with pytest.raises(fw.SpecError, match=message):
        fw.spec_resolve(python_layer(**layer), "Invalid")


def test_spec_check_reports_missing_sections() -> None:
    problems = fw.spec_check({"spec_version": 2, "language": "python", "extra": 1})
    assert "missing section 'directories'" in problems
    assert "unknown section 'extra'" in problems
    assert "spec_version must be 1, got 2" in problems


def test_resolved_specs_match_the_shared_schema() -> None:
    from importlib import resources

    import jsonschema

    schema = json.loads(
        resources.files("framework").joinpath("_assets/schema/spec.v1.json").read_text("utf-8")
    )
    for case in CASES:
        envelope = json.loads((case / "layers.json").read_text(encoding="utf-8"))
        spec = fw.spec_resolve(envelope["layers"], envelope["name"]).spec
        spec["source"] = {"origin": "python"}
        jsonschema.validate(spec, schema)


# project_create -------------------------------------------------------------------


def test_existing_folder_is_refused(tmp_path: Path) -> None:
    spec = fw.spec_resolve(python_layer(), "Exists").spec
    with pytest.raises(fw.FrameworkError, match="already exists"):
        fw.project_create(spec, tmp_path)


def test_headless_needs_a_fresh_checkout(tmp_path: Path) -> None:
    layers = tmp_path / "layers.json"
    layers.write_text(
        json.dumps({"spec_version": 1, "name": "Checkout", "layers": [{"language": "python"}]}),
        encoding="utf-8",
    )
    checkout = tmp_path / "checkout"
    (checkout / ".git").mkdir(parents=True)
    (checkout / "README.md").write_text("# Checkout\n", encoding="utf-8")
    fw.project_create_from_layers(layers, checkout)
    assert (checkout / "settings.yml").is_file()
    assert (checkout / "README.md").read_text(encoding="utf-8") == "# Checkout\n"

    dirty = tmp_path / "dirty"
    dirty.mkdir()
    (dirty / "analysis.py").write_text("", encoding="utf-8")
    with pytest.raises(fw.FrameworkError, match=r"already contains analysis\.py"):
        fw.project_create_from_layers(layers, dirty)


def test_headless_refuses_workflow_files(tmp_path: Path) -> None:
    spec = fw.spec_resolve(
        python_layer(files={".github/workflows/ci.yml": {"content": "on: push\n"}}), "Workflows"
    ).spec
    with pytest.raises(fw.FrameworkError, match="cannot write workflow files"):
        fw.project_create(spec, tmp_path / "p", headless=True)


def test_layers_file_errors(tmp_path: Path) -> None:
    layers = tmp_path / "layers.json"
    layers.write_text(json.dumps({"spec_version": 2, "name": "x"}), encoding="utf-8")
    with pytest.raises(fw.FrameworkError, match="Unsupported spec_version"):
        fw.project_create_from_layers(layers, tmp_path / "p")
    layers.write_text(json.dumps({"spec_version": 1}), encoding="utf-8")
    with pytest.raises(fw.FrameworkError, match="has no project name"):
        fw.project_create_from_layers(layers, tmp_path / "p")
    layers.write_text("{not json", encoding="utf-8")
    with pytest.raises(fw.FrameworkError, match="Failed to read layers file"):
        fw.project_create_from_layers(layers, tmp_path / "p")


def test_created_project_works(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = fw.project_create(fw.spec_resolve(python_layer(), "Works").spec, tmp_path / "works")
    monkeypatch.chdir(root / "notebooks")
    assert fw.scaffold() == root.resolve()
    assert fw.settings("notebooks") == "notebooks"
    assert fw.settings("connections.databases.framework.driver") == "sqlite"
    assert fw.settings("scaffold.notebook_format") == "quarto"


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
def test_git_commit_never_includes_env(tmp_path: Path) -> None:
    os.environ.update(
        GIT_AUTHOR_NAME="Test",
        GIT_AUTHOR_EMAIL="test@example.org",
        GIT_COMMITTER_NAME="Test",
        GIT_COMMITTER_EMAIL="test@example.org",
        GIT_CONFIG_GLOBAL=str(tmp_path / "gitconfig"),
    )
    (tmp_path / "gitconfig").write_text(
        "[user]\n  name = Test\n  email = test@example.org\n", encoding="utf-8"
    )
    spec = fw.spec_resolve(python_layer(git={"gitignore_content": "!.env"}), "Git").spec
    root = fw.project_create(spec, tmp_path / "git-project")
    tracked = subprocess.run(
        ["git", "-C", str(root), "ls-files"], capture_output=True, text=True, check=True
    ).stdout.split()
    assert ".env" not in tracked
    assert "settings.yml" in tracked


def test_secrets_backstop(tmp_path: Path) -> None:
    (tmp_path / ".gitignore").write_text("*.log", encoding="utf-8")
    assert ensure_secrets_ignored(tmp_path) is True
    assert (tmp_path / ".gitignore").read_text(encoding="utf-8").endswith("\n.env\n")
    assert ensure_secrets_ignored(tmp_path) is False


def test_fill_template_leaves_other_braces() -> None:
    spec = {"name": "P", "author": {"name": "A"}}
    assert fill_template("{{project_name}} {{ author }} {{< include x >}} {{ other }}", spec) == (
        "P A {{< include x >}} {{ other }}"
    )


# new() and the registry -------------------------------------------------------------


def test_new_uses_user_defaults(tmp_path: Path) -> None:
    config = fw.config_dir()
    config.mkdir(parents=True)
    (config / "settings.yml").write_text(
        "author: {name: Grace Hopper, email: grace@example.org}\n"
        "defaults:\n"
        "  scaffold: {seed_on_scaffold: true, seed: 2024}\n"
        "  ai: {assistants: [codex]}\n"
        "project_types:\n"
        "  project: {directories: {notebooks: analysis}}\n",
        encoding="utf-8",
    )
    root = fw.new("Defaults", tmp_path / "defaults", "project", interactive=False)
    spec = json.loads((root / ".framework/spec.json").read_text(encoding="utf-8"))
    assert spec["author"]["name"] == "Grace Hopper"
    assert spec["scaffold"]["seed"] == "2024"
    # The list replaces the default selection, as in R, so Claude Code is off.
    assert spec["ai"]["assistants"] == {"codex": True}
    assert not (root / "CLAUDE.md").exists()
    assert spec["directories"]["notebooks"] == "analysis"
    assert spec["source"]["origin"] == "python"


def test_new_with_an_empty_assistant_list_keeps_claude(tmp_path: Path) -> None:
    config = fw.config_dir()
    config.mkdir(parents=True)
    (config / "settings.yml").write_text("defaults:\n  ai: {assistants: []}\n", encoding="utf-8")
    root = fw.new("Empty", tmp_path / "empty", "project", interactive=False)
    spec = json.loads((root / ".framework/spec.json").read_text(encoding="utf-8"))
    assert spec["ai"]["assistants"] == {"claude": True}


def test_new_applies_the_user_settings_catalog(tmp_path: Path) -> None:
    config = fw.config_dir()
    config.mkdir(parents=True)
    (config / "settings-catalog.yml").write_text(
        "defaults:\n"
        "  scaffold: {seed: 999, ide: positron}\n"
        "  ai: {assistants: [copilot]}\n"
        "project_types:\n"
        "  project: {directories: {notebooks: analysis}}\n",
        encoding="utf-8",
    )
    (config / "settings.yml").write_text("defaults:\n  scaffold: {seed: 2024}\n", encoding="utf-8")
    root = fw.new("Catalog", tmp_path / "catalog", "project", interactive=False)
    spec = json.loads((root / ".framework/spec.json").read_text(encoding="utf-8"))
    # The global settings win over the catalog, which wins over the package defaults.
    assert spec["scaffold"]["seed"] == "2024"
    assert spec["scaffold"]["ide"] == "positron"
    assert spec["ai"]["assistants"] == {"copilot": True}
    assert spec["directories"]["notebooks"] == "analysis"


def test_new_defaults_to_bare_and_needs_answers(tmp_path: Path) -> None:
    root = fw.new("Bare", tmp_path / "bare", interactive=False)
    assert json.loads((root / ".framework/spec.json").read_text("utf-8"))["type"] == "bare"
    with pytest.raises(fw.FrameworkError, match="name is required"):
        fw.new(interactive=False)
    with pytest.raises(fw.FrameworkError, match="location is required"):
        fw.new("x", interactive=False)
    with pytest.raises(fw.FrameworkError, match="Invalid project type"):
        fw.new("x", tmp_path / "x", "website", interactive=False)


def test_registry() -> None:
    first = fw.project_register("/projects/one")
    assert fw.project_register("/projects/one") == first
    second = fw.project_register("/projects/two")
    assert second == first + 1
    assert fw.project_list()["id"].tolist() == [first, second]
    assert fw.project_unregister(first) is True
    assert fw.project_unregister("/projects/two") is True
    assert fw.project_unregister(99) is False
    assert fw.project_list().empty


def test_project_add_directory(r_project: Path) -> None:
    folder = fw.project_add_directory(r_project, "interviews", "Interviews", "inputs/interviews")
    assert folder.is_dir()
    raw = (r_project / "settings.yml").read_text(encoding="utf-8")
    assert "key: interviews" in raw
    with pytest.raises(fw.FrameworkError, match="relative"):
        fw.project_add_directory(r_project, "x", "X", "../outside")


def test_quarto_regenerate_backs_up(r_project: Path) -> None:
    (r_project / "_quarto.yml").write_text("# edited\n", encoding="utf-8")
    result = fw.quarto_regenerate(r_project)
    assert "_quarto.yml" in result.backed_up
    assert result.backup_location is not None
    assert (result.backup_location / "_quarto.yml").read_text(encoding="utf-8") == "# edited\n"
    assert "type: default" in (r_project / "_quarto.yml").read_text(encoding="utf-8")


# The command line -------------------------------------------------------------------


def test_cli_from_layers_and_name(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    layers = tmp_path / "layers.json"
    layers.write_text(
        json.dumps({"spec_version": 1, "name": "Cli", "layers": [{"language": "python"}]}),
        encoding="utf-8",
    )
    assert main(["create", "--from-layers", str(layers), "--path", str(tmp_path / "a")]) == 0
    assert main(["create", "--name", "Cli", "--path", str(tmp_path / "b"), "--type", "course"]) == 0
    assert (tmp_path / "b/settings.yml").is_file()
    assert main(["create", "--name", "Cli", "--path", str(tmp_path / "b")]) == 1
    assert "already exists" in capsys.readouterr().err
    r_layers = tmp_path / "r.json"
    r_layers.write_text(
        json.dumps({"spec_version": 1, "name": "R", "layers": []}), encoding="utf-8"
    )
    assert main(["create", "--from-layers", str(r_layers), "--path", str(tmp_path / "c")]) == 1
    with pytest.raises(SystemExit) as usage:
        main(["create", "--from-layers", str(layers), "--type", "course", "--path", "x"])
    assert usage.value.code == 2


def test_secrets_backstop_respects_negation(tmp_path: Path) -> None:
    (tmp_path / ".gitignore").write_text(".env\n!.env\n", encoding="utf-8")
    assert ensure_secrets_ignored(tmp_path) is True
    (tmp_path / ".gitignore").write_text("!.env\n.env\n", encoding="utf-8")
    assert ensure_secrets_ignored(tmp_path) is False


@pytest.mark.parametrize(
    ("text", "added"),
    [
        ("**/.env\n", False),
        ("/.env*\n", False),
        ("*.env\n", False),
        ("**/.env\n!/.env\n", True),
        (".envrc\n", True),
    ],
)
def test_secrets_backstop_patterns(tmp_path: Path, text: str, added: bool) -> None:
    (tmp_path / ".gitignore").write_text(text, encoding="utf-8")
    assert ensure_secrets_ignored(tmp_path) is added
