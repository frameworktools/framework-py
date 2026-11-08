from __future__ import annotations

import logging
from pathlib import Path

import pytest

import framework as fw
from framework import _yaml
from framework._paths import output_directory
from framework.features import definition_layers, resolve_tools


def settings(root: Path) -> dict[str, object]:
    data = _yaml.read(root / "settings.yml")
    return dict(data["default"])


def test_tools_available_lists_python_tools() -> None:
    tools = fw.tools_available()
    assert set(tools["id"]) == {"jupyter", "quarto", "quarto_manuscript", "quarto_slides", "uv"}
    assert tools.set_index("id").loc["quarto_slides", "requires"] == ["quarto"]


def test_required_tools_are_added_with_a_message(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="framework")
    assert resolve_tools(["quarto_slides"]) == ["quarto", "quarto_slides"]
    assert "Also turning on quarto, which quarto_slides needs" in caplog.text


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"tools": ["latex"]}, "Unknown tool"),
        ({"tools": ["renv"]}, "isn't available for Python"),
        ({"tools": {"quarto": False, "quarto_slides": True}}, "needs quarto, which is turned off"),
        ({"tools": {"quarto": "yes"}}, "must be true or false"),
        ({"skill_sets": ["ggplot2"]}, "isn't available for Python"),
        ({"package_sets": ["tidyverse-analysis"]}, "isn't available for Python"),
        ({"skill_sets": ["nope"]}, "Unknown skill set"),
    ],
)
def test_bad_choices(kwargs: dict[str, object], message: str) -> None:
    with pytest.raises(fw.FrameworkError, match=message):
        definition_layers(**kwargs)  # type: ignore[arg-type]


def test_layers_follow_the_cloud_order() -> None:
    keys = [e["key"] for e in definition_layers(["jupyter"], skill_sets=["framework"])]
    assert keys == [
        "framework/tool-jupyter",
        "framework/tool-jupyter-python",
        "framework/skills-framework",
    ]


def test_new_with_jupyter_and_without_quarto(tmp_path: Path) -> None:
    root = fw.new(
        "Lab",
        tmp_path / "lab",
        "project",
        tools={"quarto": False, "jupyter": True},
        interactive=False,
    )
    assert settings(root)["tools"] == {"quarto": False, "uv": True, "jupyter": True}
    assert not list(root.rglob("_quarto.yml"))
    assert '"ipykernel"' in (root / "pyproject.toml").read_text(encoding="utf-8")
    with pytest.raises(fw.FrameworkError, match="doesn't use Quarto"):
        fw.quarto_regenerate(root)


def test_new_adds_slides_to_a_project(tmp_path: Path) -> None:
    root = fw.new(
        "Talk",
        tmp_path / "talk",
        "project",
        workflow="quarto",
        tools=["quarto_slides"],
        interactive=False,
    )
    assert settings(root)["tools"] == {"quarto": True, "uv": True, "quarto_slides": True}
    assert (root / "slides").is_dir()
    # Slides render as revealjs, set in the folder's own metadata
    assert "revealjs:" in (root / "slides/_metadata.yml").read_text(encoding="utf-8")


def test_a_free_type_label_starts_like_bare_with_core_files() -> None:
    layer = {"type": "lab_pipeline", "language": "python", "directories": {"data": "data"}}
    spec = fw.spec_resolve([layer], name="Lab").spec
    assert (spec["type"], spec["core_files"], spec["settings_layout"]) == (
        "lab_pipeline",
        True,
        "flat",
    )
    assert spec["tools"] == {"quarto": False, "uv": True}
    assert spec["directories"] == {"data": "data"}
    with pytest.raises(fw.SpecError, match="lowercase id"):
        fw.spec_resolve([{"type": "Lab Pipeline", "language": "python"}], name="Lab")


def test_spec_check_validates_the_new_keys() -> None:
    spec = fw.spec_resolve([{"language": "python"}], name="X").spec
    spec.update(core_files="yes", settings_layout="nested", tools={"Bad": True, "uv": 1})
    problems = fw.spec_check(spec)
    assert "core_files must be true or false" in problems
    assert "settings_layout must be 'split' or 'flat'" in problems
    assert "tools: invalid tool id 'Bad'" in problems
    assert "tools.uv must be true or false" in problems


def test_settings_layout_decides_split_files(tmp_path: Path) -> None:
    layer = {"type": "course", "language": "python", "settings_layout": "split"}
    spec = fw.spec_resolve([layer], name="Course").spec
    root = fw.project_create(spec, tmp_path / "course")
    assert (root / "settings/author.yml").is_file()


def test_output_folders_split_by_the_sensitive_setting(tmp_path: Path) -> None:
    sensitive = {"security": {"sensitive": True}, "directories": {}}
    assert output_directory(sensitive, "tables", False, tmp_path) == (
        tmp_path / "outputs/private/tables"
    )
    assert output_directory(sensitive, "tables", True, tmp_path) == (
        tmp_path / "outputs/public/tables"
    )
    keyed = {"directories": {"outputs_public_tables": "shared/tables"}}
    assert output_directory(keyed, "tables", True, tmp_path) == tmp_path / "shared/tables"
    plain = {"directories": {}}
    assert output_directory(plain, "tables", True, tmp_path) == tmp_path / "outputs/tables"


def test_cli_tools_and_sets(tmp_path: Path) -> None:
    from framework.__main__ import main

    argv = ["create", "--name", "Cli", "--path", str(tmp_path / "cli"), "--type", "project"]
    argv += ["--workflow", "quarto", "--tool", "jupyter", "--tool", "quarto=false"]
    argv += ["--skill-set", "framework"]
    assert main(argv) == 0
    assert settings(tmp_path / "cli")["tools"] == {"quarto": False, "uv": True, "jupyter": True}
    with pytest.raises(SystemExit):
        main(["create", "--from-layers", "x.json", "--path", "y", "--tool", "jupyter"])


def test_skill_sets_replace_the_default_skills() -> None:
    from framework.create import _replace_default_skills

    spec = fw.spec_resolve([{"type": "project_sensitive", "language": "python"}], name="S").spec
    assert "framework-workflow" in spec["skills"]
    chosen = [{"key": "framework/skills-x", "layer": {"skills": {"framework-data": {}}}}]
    _replace_default_skills(spec, "project_sensitive", chosen)
    assert sorted(spec["skills"]) == ["framework-data", "framework-sensitive-data"]


def test_off_switches_come_last() -> None:
    keys = [e["key"] for e in definition_layers({"quarto": False, "jupyter": True})]
    assert keys[-1] == "framework/tools-off"


def test_projects_without_quarto_make_jupyter_notebooks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = fw.new("Nb", tmp_path / "nb", "project", tools={"quarto": False}, interactive=False)
    assert _yaml.read(root / "settings/scaffold.yml")["scaffold"]["notebook_format"] == "jupyter"
    monkeypatch.chdir(root)
    assert fw.make_notebook("first").suffix == ".ipynb"


@pytest.mark.parametrize(
    ("scaffold", "message"),
    [
        ({"workflow": "notebooks"}, "scaffold.workflow must be quarto, jupyter, or scripts"),
        ({"notebook_format": "rmarkdown"}, "must be quarto, jupyter, or script"),
        (
            {"workflow": "scripts", "notebook_format": "jupyter"},
            "'jupyter' doesn't fit the scripts workflow, which uses 'script'",
        ),
    ],
)
def test_workflow_and_notebook_format_must_pair(scaffold: dict[str, str], message: str) -> None:
    spec = fw.spec_resolve([{"language": "python"}], name="X").spec
    spec["scaffold"].update(scaffold)
    assert any(message in problem for problem in fw.spec_check(spec))


def test_scripts_only_projects_make_scripts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    layer = {"language": "python", "tools": {"quarto": False}, "scaffold": {"workflow": "scripts"}}
    spec = fw.spec_resolve([layer], name="Scripts").spec
    root = fw.project_create(spec, tmp_path / "scripts")
    scaffold = _yaml.read(root / "settings/scaffold.yml")["scaffold"]
    assert (scaffold["workflow"], scaffold["notebook_format"]) == ("scripts", "script")
    assert not list(root.rglob("*.qmd")) and not list(root.rglob("*.ipynb"))
    monkeypatch.chdir(root)
    made = fw.make_notebook("clean")
    assert made.suffix == ".py"
    assert made.parent.name == "scripts"


def test_python_projects_default_to_the_jupyter_workflow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = fw.new("Nb", tmp_path / "nb", "project", interactive=False)
    assert settings(root)["tools"] == {"quarto": True, "uv": True, "jupyter": True}
    scaffold = _yaml.read(root / "settings/scaffold.yml")["scaffold"]
    assert (scaffold["workflow"], scaffold["notebook_format"]) == ("jupyter", "jupyter")
    assert '"ipykernel"' in (root / "pyproject.toml").read_text(encoding="utf-8")
    # Quarto stays for rendering and publishing
    assert (root / "_quarto.yml").is_file()
    monkeypatch.chdir(root)
    assert fw.make_notebook("first").suffix == ".ipynb"


def test_course_keeps_quarto_and_workflows_can_be_chosen(tmp_path: Path) -> None:
    course = fw.new("Course", tmp_path / "course", "course", interactive=False)
    assert settings(course)["scaffold"]["workflow"] == "quarto"
    scripts = fw.new("S", tmp_path / "s", "project", workflow="scripts", interactive=False)
    assert settings(scripts)["tools"] == {"quarto": False, "uv": True, "jupyter": False}
    assert not list(scripts.rglob("_quarto.yml"))
    with pytest.raises(fw.FrameworkError, match="Unknown workflow: notebooks"):
        fw.new("X", tmp_path / "x", "project", workflow="notebooks", interactive=False)


def test_python_projects_get_what_their_workflow_needs(tmp_path: Path) -> None:
    import tomllib

    def dependencies(root: Path) -> list[str]:
        data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
        return list(data["project"]["dependencies"])

    render = ["ipykernel", "nbformat", "nbclient"]
    jupyter = fw.new("J", tmp_path / "j", "project", interactive=False)
    assert dependencies(jupyter) == ["framework-py", *render, "jupyterlab"]
    for type in ("project", "course", "presentation"):
        root = fw.new(type, tmp_path / type, type, workflow="quarto", interactive=False)
        assert dependencies(root) == ["framework-py", *render], type
    scripts = fw.new("S", tmp_path / "s", "project", workflow="scripts", interactive=False)
    assert dependencies(scripts) == ["framework-py"]
    # A spec package of the same name keeps its version and is not added twice
    layer = {
        "type": "project",
        "language": "python",
        "tools": {"quarto": True},
        "packages": {
            "default_packages": {
                "nbclient": {"source": "pypi", "version": ">=0.10", "auto_attach": False}
            }
        },
    }
    spec = fw.spec_resolve([layer], name="Pinned").spec
    pinned = fw.project_create(spec, tmp_path / "pinned")
    assert dependencies(pinned) == ["framework-py", "nbclient>=0.10", "ipykernel", "nbformat"]
