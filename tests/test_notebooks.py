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
    assert notebook["cells"][0]["source"][0] == "# Explore\n"
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
    assert fw.make_script("x").read_text(encoding="utf-8") == "# mine X\n"
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
        "# from my template Z\n"
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


def test_titles_and_messages(notebooks_project: Path, caplog: pytest.LogCaptureFixture) -> None:
    from framework.notebooks import notebook_title

    assert notebook_title("bp-summary") == "Bp summary"
    assert notebook_title("setup-and-settings") == "Setup and settings"
    assert notebook_title("01_load_data") == "01 load data"
    assert notebook_title("My cool analysis") == "My cool analysis"
    assert notebook_title("BP-summary") == "BP-summary"
    assert notebook_title("analysis") == "Analysis"

    caplog.set_level("INFO", logger="framework")
    path = fw.make_notebook("bp-summary")
    assert 'title: "Bp summary"' in path.read_text(encoding="utf-8")
    assert caplog.messages[-1] == f"Created Quarto notebook: {path}"
    talk = fw.make_presentation("study-talk")
    assert 'title: "Study talk"' in talk.read_text(encoding="utf-8")
    assert caplog.messages[-1] == f"Created reveal.js presentation: {talk}"
    quick = fw.make_notebook("quick", stub="minimal")
    assert caplog.messages[-1] == f"Created Quarto notebook: {quick} (stub: minimal)"


def test_an_explicit_title_keeps_acronyms(notebooks_project: Path) -> None:
    # Ports 2026-11-07: a file name such as "bp-for-ldl-and-hba1c" has no case, so its
    # title was "Bp for ldl and hba1c". Capitals typed in the name are kept, and title=
    # sets the rest.
    from framework.notebooks import notebook_title

    assert notebook_title("bp-for-ldl-and-hba1c") == "Bp for ldl and hba1c"
    assert notebook_title("BP for LDL and HbA1c") == "BP for LDL and HbA1c"
    path = fw.make_notebook("bp-for-ldl-and-hba1c", title="BP for LDL and HbA1c")
    assert path.name == "bp-for-ldl-and-hba1c.qmd"
    assert 'title: "BP for LDL and HbA1c"' in path.read_text(encoding="utf-8")
    jupyter = fw.make_notebook("ldl.ipynb", title="LDL")
    assert '"# LDL\\n"' in jupyter.read_text(encoding="utf-8")
    talk = fw.make_presentation("ldl-talk", title="LDL talk")
    assert 'title: "LDL talk"' in talk.read_text(encoding="utf-8")
    script = fw.make_script("ldl", title="LDL checks")
    assert script.read_text(encoding="utf-8").startswith('"""LDL checks')
