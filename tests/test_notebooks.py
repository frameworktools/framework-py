from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

import framework as fw
from framework.notebooks import author_front_matter


@pytest.fixture
def notebooks_project(project: Path) -> Path:
    (project / "settings.yml").write_text(
        "default:\n"
        "  project_name: Test\n"
        "  language: python\n"
        "  directories: {notebooks: notebooks, scripts: scripts}\n"
        '  author: {name: "Ada \\"Countess\\" Lovelace", email: ada@example.com}\n',
        encoding="utf-8",
    )
    return project


def test_make_notebook_quarto(notebooks_project: Path) -> None:
    path = fw.make_notebook("Data Cleaning!")
    assert path == notebooks_project / "notebooks/data-cleaning.qmd"
    text = path.read_text(encoding="utf-8")
    assert 'title: "Data Cleaning!"' in text
    assert 'author: "Ada \\"Countess\\" Lovelace"' in text
    assert f'date: "{date.today():%Y-%m-%d}"' in text
    assert "{{" not in text
    with pytest.raises(fw.FrameworkError, match="already exists"):
        fw.make_notebook("data cleaning")
    fw.make_qmd("data cleaning", overwrite=True)


def test_explicit_dir_is_relative_to_the_project_root(
    notebooks_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (notebooks_project / "notebooks").mkdir()
    monkeypatch.chdir(notebooks_project / "notebooks")
    path = fw.make_qmd("talk", dir="slides")
    assert path == notebooks_project / "slides/talk.qmd"
    assert fw.make_script("job", dir="scripts") == notebooks_project / "scripts/job.py"


def test_make_notebook_jupyter_is_valid_json(notebooks_project: Path) -> None:
    path = fw.make_notebook("explore.ipynb", subdir="eda")
    assert path == notebooks_project / "notebooks/eda/explore.ipynb"
    notebook = json.loads(path.read_text(encoding="utf-8"))
    assert notebook["cells"][0]["source"][0] == "# explore\n"
    assert 'Ada "Countess" Lovelace' in notebook["cells"][0]["source"][2]


def test_notebook_format_setting(notebooks_project: Path) -> None:
    text = (notebooks_project / "settings.yml").read_text(encoding="utf-8")
    (notebooks_project / "settings.yml").write_text(
        text + "  scaffold: {notebook_format: jupyter}\n", encoding="utf-8"
    )
    assert fw.make_notebook("a").suffix == ".ipynb"
    assert fw.make_notebook("b", type="quarto").suffix == ".qmd"


def test_make_script_and_presentation(notebooks_project: Path) -> None:
    script = fw.make_script("Load Data")
    assert script == notebooks_project / "scripts/load-data.py"
    assert '"""Load Data' in script.read_text(encoding="utf-8")
    slides = fw.make_presentation("Talk", dir=notebooks_project / "slides")
    assert "revealjs:" in slides.read_text(encoding="utf-8")
    assert fw.make_revealjs("Talk 2").parent == notebooks_project / "notebooks"


def test_author_block_with_details(notebooks_project: Path) -> None:
    (notebooks_project / "settings.yml").write_text(
        "default:\n"
        "  author:\n"
        "    name: Ada\n"
        "    orcid: 0000-0001\n"
        "    affiliations: [{name: Analytical Society, city: London}]\n",
        encoding="utf-8",
    )
    text = fw.make_qmd("x", dir=notebooks_project).read_text(encoding="utf-8")
    assert (
        'author:\n  - name: "Ada"\n    orcid: "0000-0001"\n    affiliations:\n'
        '      - name: "Analytical Society"\n        city: "London"\n'
    ) in text


def test_author_front_matter_basic() -> None:
    assert author_front_matter({}) == ['author: "Your Name"']
    assert author_front_matter({"name": "A", "affiliation": "U"}) == ['author: "A"']


def test_global_author_fills_in(project: Path) -> None:
    fw.configure_global({"author": {"name": "Global Person"}})
    text = fw.make_qmd("x", dir=project).read_text(encoding="utf-8")
    assert 'author: "Global Person"' in text


def test_stubs(notebooks_project: Path) -> None:
    listed = fw.stubs_list()
    assert set(zip(listed["name"], listed["type"], strict=True)) >= {
        ("default", "quarto"),
        ("default", "jupyter"),
        ("default", "script"),
        ("minimal", "quarto"),
        ("revealjs", "quarto"),
    }
    assert set(listed["source"]) == {"framework"}
    assert fw.stubs_path() == fw.stubs_path("framework")

    published = fw.stubs_publish("scripts")
    assert [p.name for p in published] == ["script-default.py"]
    assert fw.stubs_path() == notebooks_project / "stubs"
    (notebooks_project / "stubs/script-default.py").write_text("# mine {{ title }}\n", "utf-8")
    assert fw.make_script("x").read_text(encoding="utf-8") == "# mine x\n"
    assert fw.stubs_publish("scripts") == []
    row = fw.stubs_list("script").iloc[0]
    assert (row["name"], row["source"]) == ("default", "user")

    with pytest.raises(fw.FrameworkError, match="Stub template not found: fancy"):
        fw.make_qmd("y", stub="fancy")


def test_user_templates(project: Path) -> None:
    assert "fw.scaffold()" in fw.read_framework_template("script")
    path = fw.write_framework_template("script", "# from my template {{ title }}")
    assert path.parent == fw.config_dir() / "templates"
    assert fw.make_script("z", dir=project).read_text(encoding="utf-8") == (
        "# from my template z\n"
    )
    fw.reset_framework_template("script")
    assert "fw.scaffold()" in fw.make_script("w", dir=project).read_text(encoding="utf-8")
    with pytest.raises(fw.FrameworkError, match="add rules with a facet"):
        fw.read_framework_template("gitignore-project")


def test_stubs_list_shows_your_templates(project: Path) -> None:
    # Audit 2026-11-05: make_qmd() used a personal template, but stubs_list() said
    # "framework" for it
    fw.write_framework_template("notebook", "---\ntitle: mine\n---\n")
    listed = fw.stubs_list("quarto").set_index("name")
    assert listed.loc["default", "source"] == "personal"
    assert listed.loc["minimal", "source"] == "framework"
