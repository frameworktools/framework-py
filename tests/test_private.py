from __future__ import annotations

import sys
from pathlib import Path

import pytest

import framework as fw
from framework._private import confirm_private_upload, is_private_path


def test_folder_parts_match_ignoring_case(tmp_path: Path) -> None:
    assert is_private_path(tmp_path / "private/x.html")
    assert is_private_path(tmp_path / "proj/Private/out.html")
    assert is_private_path(tmp_path / "outputs_PRIVATE/out.html")
    assert not is_private_path(tmp_path / "public/x.html")
    # File names never count, nor folders that merely contain the word
    assert not is_private_path(tmp_path / "my_PRIVATE_notes.csv")
    assert not is_private_path(tmp_path / "notebooks/01-private-and-public-data.qmd")
    assert not is_private_path(tmp_path / "private-ish/x.html")
    folder = tmp_path / "a/private"
    folder.mkdir(parents=True)
    assert is_private_path(folder)


def test_folders_the_settings_mark_private(project: Path) -> None:
    (project / "settings.yml").write_text(
        "default:\n  directories:\n    outputs_private_tables: results/held\n"
        "    outputs_public_tables: results/shared\n",
        encoding="utf-8",
    )
    assert is_private_path(project / "results/held/t.csv")
    assert not is_private_path(project / "results/shared/t.csv")


# The same cases as R's test-publish.R
@pytest.mark.parametrize(
    "path",
    [
        pytest.param(
            "/private/var/folders/ab/report.html",
            marks=pytest.mark.skipif(sys.platform == "win32", reason="a macOS system path"),
        ),
        "/srv/project/outputs/public/report.html",
    ],
)
def test_silent_paths(path: str) -> None:
    # macOS resolves /tmp and /var to /private/..., which the user did not name
    assert not is_private_path(Path(path))


@pytest.mark.parametrize(
    "path",
    [
        "/private/var/folders/proj/Private/out.html",
        "~/proj/Private/out.html",
        "/home/study_private/report.html",
    ],
)
def test_flagged_paths(path: str) -> None:
    assert is_private_path(Path(path))


@pytest.mark.parametrize(
    "path", ["/home/PRIVATE-studies/report.html", "/srv/my_private_notes.html"]
)
def test_file_names_and_other_words_are_not_private(path: str) -> None:
    assert not is_private_path(Path(path))


def test_no_terminal_needs_include_private(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    secret = tmp_path / "private/ids.csv"
    with pytest.raises(fw.FrameworkError, match="include_private=True"):
        confirm_private_upload([secret], include_private=False)
    assert str(secret) in capsys.readouterr().err
    confirm_private_upload([secret], include_private=True)
    confirm_private_upload([tmp_path / "public.csv"], include_private=False)


@pytest.mark.parametrize(("answer", "uploads"), [("y", True), ("", False), ("no", False)])
def test_a_terminal_asks_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, answer: str, uploads: bool
) -> None:
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt: answer)
    secret = [tmp_path / "private/ids.csv"]
    if uploads:
        confirm_private_upload(secret, include_private=False)
    else:
        with pytest.raises(fw.FrameworkError, match="Cancelled"):
            confirm_private_upload(secret, include_private=False)


def test_the_question_matches_r(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    asked: list[str] = []
    monkeypatch.setattr("builtins.input", lambda prompt: asked.append(prompt) or "y")
    confirm_private_upload([tmp_path / "private/ids.csv"], include_private=False)
    confirm_private_upload([tmp_path / "x.csv"], include_private=False, reasons=["Sensitive."])
    assert asked == ["Upload them anyway? [y/N] ", "Upload anyway? [y/N] "]


# Sensitive projects and notebooks that read private data (ports 2026-11-07) -----------


def _site(project: Path, sensitive: bool) -> None:
    (project / "settings.yml").write_text(
        "default:\n"
        "  project_name: Test\n"
        + ("  security:\n    sensitive: true\n" if sensitive else "")
        + "  data:\n"
        "    inputs:\n"
        "      ids: inputs/private/raw/ids.csv\n"
        "      flagged: {path: inputs/raw/flagged.csv, private: true}\n"
        "      survey: inputs/raw/survey.csv\n"
        "  publish:\n"
        "    default: site\n"
        "    destinations:\n"
        "      site: {driver: folder, path: site, visibility: public}\n",
        encoding="utf-8",
    )


def test_sensitive_projects_confirm_objects_and_notebooks(
    project: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # publish_notebook() published a notebook that read private data from a sensitive
    # project with no question, as did publish() of a DataFrame
    import pandas as pd

    monkeypatch.setattr("framework.cloud.project_token", lambda: None)
    _site(project, sensitive=True)
    frame = pd.DataFrame({"n": [1]})
    with pytest.raises(fw.FrameworkError, match="include_private=True"):
        fw.publish(frame, slug="counts")
    assert "sensitive" in capsys.readouterr().err
    with pytest.raises(fw.FrameworkError, match="include_private=True"):
        fw.publish_data(frame, "tables/counts.csv")
    notebook = project / "notebooks/summary.qmd"
    notebook.parent.mkdir()
    notebook.write_text("---\ntitle: S\n---\n\n```{python}\nx = 1\n```\n", encoding="utf-8")
    with pytest.raises(fw.FrameworkError, match="include_private=True"):
        fw.publish_notebook(notebook)
    with pytest.raises(fw.FrameworkError, match="include_private=True"):
        fw.publish(notebook)
    assert not (project / "site").exists()
    assert fw.publish(frame, slug="counts", include_private=True)
    assert (project / "site/counts/artifact.json").is_file()


def test_a_terminal_confirms_an_object_in_a_sensitive_project(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import pandas as pd

    monkeypatch.setattr("framework.cloud.project_token", lambda: None)
    _site(project, sensitive=True)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt: "n")
    with pytest.raises(fw.FrameworkError, match="Cancelled"):
        fw.publish(pd.DataFrame({"n": [1]}), slug="counts")
    monkeypatch.setattr("builtins.input", lambda prompt: "y")
    assert fw.publish(pd.DataFrame({"n": [1]}), slug="counts")


def test_other_projects_publish_objects_without_asking(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import pandas as pd

    monkeypatch.setattr("framework.cloud.project_token", lambda: None)
    _site(project, sensitive=False)
    assert fw.publish(pd.DataFrame({"n": [1]}), slug="counts")


def test_notebooks_that_name_private_data(project: Path) -> None:
    from framework._private import notebook_private_reads

    _site(project, sensitive=False)
    qmd = project / "a.qmd"
    qmd.write_text(
        "---\ntitle: A\n---\n\nThis is private work.\n\n```{python}\n"
        'a = fw.data_read("inputs/private/raw/ids.csv")\n'
        "b = fw.data_read('inputs.ids')\n"
        'c = fw.data_read("inputs.flagged")\n'
        'd = fw.data_read("inputs.survey")\n'
        'e = fw.data_read("inputs.private.raw.other")\n'
        'print("private results are not shown")\n'
        'f = fw.data_read("data/outputs_private/x.csv")\n'
        "```\n",
        encoding="utf-8",
    )
    assert notebook_private_reads(qmd) == [
        "inputs/private/raw/ids.csv",
        "inputs.ids",
        "inputs.flagged",
        "data/outputs_private/x.csv",
    ]


def test_settings_keys_patterns_and_file_names_are_not_private_reads(project: Path) -> None:
    from framework._private import notebook_private_reads

    _site(project, sensitive=True)
    qmd = project / "01-private-and-public-data.qmd"
    qmd.write_text(
        "```{python}\n"
        'fw.settings("security.sensitive")\n'
        'fw.settings("inputs_private_raw")\n'
        'info[info["key"].str.contains("private|public")]\n'
        'print("sensitive:", 1)\n'
        'fw.publish_notebook("notebooks/01-private-and-public-data.qmd")\n'
        "```\n",
        encoding="utf-8",
    )
    assert notebook_private_reads(qmd) == []
    ipynb = project / "b.ipynb"
    ipynb.write_text(
        '{"cells": [{"cell_type": "markdown", "source": ["\\"inputs/private/x.csv\\""]},'
        ' {"cell_type": "code", "source": ["df = fw.data_read(\\"inputs.ids\\")"]}]}',
        encoding="utf-8",
    )
    assert notebook_private_reads(ipynb) == ["inputs.ids"]


def test_a_notebook_reading_private_data_needs_confirmation(
    project: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("framework.cloud.project_token", lambda: None)
    _site(project, sensitive=False)
    notebook = project / "notebooks/report.qmd"
    notebook.parent.mkdir()
    notebook.write_text('```{python}\nfw.data_read("inputs.ids")\n```\n', encoding="utf-8")
    with pytest.raises(fw.FrameworkError, match="include_private=True"):
        fw.publish_notebook(notebook)
    assert "report.qmd reads private data: inputs.ids" in capsys.readouterr().err


# Sensitive projects (R's .fw_settings_sensitive()) ------------------------------------


@pytest.mark.parametrize(
    ("settings", "sensitive"),
    [
        # Projects of this type made before Framework 2 have no security: block
        ({"project_type": "project_sensitive"}, True),
        ({"project_type": "project_sensitive", "security": {"sensitive": False}}, True),
        ({"project_type": "project", "security": {"sensitive": True}}, True),
        ({"project_type": "project", "security": {"sensitive": False}}, False),
        ({"project_type": "project"}, False),
        # Without the setting, the private layout counts
        ({"directories": {"inputs_private_raw": "inputs/private/raw"}}, True),
        (
            {
                "directories": {"inputs_private_raw": "inputs/private/raw"},
                "security": {"sensitive": False},
            },
            False,
        ),
        (None, False),
    ],
)
def test_settings_sensitive(settings: object, sensitive: bool) -> None:
    from framework._private import settings_sensitive

    assert settings_sensitive(settings) is sensitive


def test_an_old_project_sensitive_project_is_sensitive(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from framework._paths import output_directory
    from framework._private import project_is_sensitive, sensitive_mismatch
    from framework.settings import settings_read

    (project / "settings.yml").write_text(
        "default:\n  project_type: project_sensitive\n", encoding="utf-8"
    )
    config = settings_read(project / "settings.yml")
    assert project_is_sensitive()
    assert output_directory(config, "tables", public=False, root=project) == (
        project / "outputs/private/tables"
    )
    assert "run framework.upgrade()" in str(sensitive_mismatch(config))
    fw.status()
    out = capsys.readouterr().out
    assert "Sensitive data: yes" in out
    assert "no security: block" in out
