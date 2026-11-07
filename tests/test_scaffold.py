from __future__ import annotations

import json
import os
import random
import shutil
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

import framework as fw
from framework import db


def test_scaffold_from_a_subfolder(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (project / "functions").mkdir()
    (project / "functions" / "fw_test_helpers.py").write_text("VALUE = 7\n", encoding="utf-8")
    (project / "scaffold.py").write_text(
        "import os\nos.environ['FW_SCAFFOLD_RAN'] = 'yes'\n", encoding="utf-8"
    )
    (project / "notebooks").mkdir()
    monkeypatch.chdir(project / "notebooks")
    monkeypatch.setattr(sys, "path", list(sys.path))

    assert fw.scaffold() == project.resolve()
    assert Path.cwd() == (project / "notebooks").resolve()
    assert os.environ["FW_SCAFFOLD_RAN"] == "yes"
    assert str((project / "functions").resolve()) in sys.path
    assert (project / "framework.db").is_file()

    history = json.loads(db.get_meta("scaffold_history") or "{}")
    assert history["first"] == history["last"]
    assert history["first"].endswith("Z")


def test_scaffold_keeps_the_first_time(project: Path) -> None:
    db.set_meta("scaffold_history", '{"first":"2020-01-01T00:00:00Z","last":"x"}')
    fw.scaffold()
    history = json.loads(db.get_meta("scaffold_history") or "{}")
    assert history["first"] == "2020-01-01T00:00:00Z"


def test_seed_from_scaffold_settings(project: Path) -> None:
    (project / "settings.yml").write_text(
        "default:\n  scaffold: {seed_on_scaffold: yes, seed: '42'}\n", encoding="utf-8"
    )
    fw.scaffold()
    first = random.random()
    random.seed(42)
    assert first == random.random()


@pytest.mark.parametrize(
    ("scaffold", "env", "expected"),
    [
        ("", "7", 7),  # no project setting: FW_SEED turns seeding on
        ("{seed_on_scaffold: no}", "7", None),  # the project says no
        ("{seed_on_scaffold: yes, seed: '5'}", "7", 5),  # the project's seed wins
        ("{seed_on_scaffold: yes}", "", 123),  # fallback
        ("{seed: '5'}", "", None),  # a seed alone does not turn seeding on
    ],
)
def test_seed_rules(project: Path, scaffold: str, env: str, expected: int | None) -> None:
    from framework.scaffold import _set_seed
    from framework.settings import settings_read

    if scaffold:
        (project / "settings.yml").write_text(
            f"default:\n  scaffold: {scaffold}\n", encoding="utf-8"
        )
    os.environ["FW_SEED"] = env
    assert _set_seed(settings_read()) == expected


def test_missing_dotenv_location(project: Path) -> None:
    (project / "settings.yml").write_text(
        "default:\n  dotenv_location: secrets\n", encoding="utf-8"
    )
    with pytest.raises(fw.SettingsError, match="Dotenv file not found"):
        fw.scaffold()


def test_scaffold_outside_a_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    with pytest.raises(fw.ProjectNotFoundError):
        fw.scaffold()


def test_status(r_project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    fw.status()
    output = capsys.readouterr().out
    assert "Type: project" in output
    assert "AGENTS.md, CLAUDE.md" in output
    assert "ok inputs_raw -> inputs/raw" in output


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
def test_status_of_a_project_inside_another_repository(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    os.environ.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
    outer = tmp_path / "outer"
    project = outer / "inner"
    project.mkdir(parents=True)

    def git(*args: str) -> None:
        subprocess.run(["git", "-C", str(outer), *args], check=True, capture_output=True)

    git("init", "-q")
    (outer / "other.txt").write_text("x\n", encoding="utf-8")
    git("add", "other.txt")
    git("-c", "user.name=T", "-c", "user.email=t@example.com", "commit", "-q", "-m", "one")
    (outer / "unrelated.txt").write_text("x\n", encoding="utf-8")
    (project / "settings.yml").write_text("default:\n  project_name: Inner\n", encoding="utf-8")
    monkeypatch.chdir(project)
    fw.status()
    output = capsys.readouterr().out
    assert f"Inside the repository at {outer.resolve()}" in output
    assert "Commits: 0" in output
    # settings.yml is the project's one change; unrelated.txt is outside it
    assert "Uncommitted changes: 1" in output


def test_project_info(r_project: Path) -> None:
    info = fw.project_info()
    assert info.attrs == {"name": "Fixture Study", "type": "project"}
    row = info[info["key"] == "inputs_raw"].iloc[0]
    assert (row["path"], bool(row["exists"])) == ("inputs/raw", True)


def test_scratch(project: Path) -> None:
    frame = pd.DataFrame({"a": [1, 2, 3]})
    assert fw.scratch_capture(frame, "rows", n=2) is frame
    folder = project / "outputs/private/scratch"
    assert (folder / "rows.tsv").read_text(encoding="utf-8") == "a\n1\n2\n"
    fw.scratch_capture([1, 2], "values")
    assert (folder / "values.txt").read_text(encoding="utf-8") == "1\n2\n"
    fw.scratch_capture({"k": 1}, "mapping.txt")
    assert "k: 1" in (folder / "mapping.txt").read_text(encoding="utf-8")
    fw.scratch_capture(frame, "frame.csv")
    assert (folder / "frame.csv").is_file()
    fw.scratch_capture(object(), "obj.pkl")
    assert (folder / "obj.pkl").is_file()
    with pytest.raises(fw.FrameworkError):
        fw.scratch_capture(frame, "bad", to="text")

    (folder / "keep").mkdir()
    fw.scratch_clean()
    assert [p.name for p in folder.iterdir()] == ["keep"]


def test_capture_output() -> None:
    with fw.capture_output() as captured:
        print("hello")
        print("problem", file=sys.stderr)
    assert captured.lines == ["hello", "problem"]

    with pytest.raises(ValueError), fw.capture_output() as failed:
        print("before")
        raise ValueError("boom")
    assert failed.output == "before\n"


def test_source_all_functions_from_scaffold_settings(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # New projects write source_all_functions under scaffold:, in a split file.
    (project / "settings.yml").write_text(
        "default:\n  scaffold: settings/scaffold.yml\n", encoding="utf-8"
    )
    (project / "settings").mkdir()
    (project / "settings/scaffold.yml").write_text(
        "scaffold:\n  source_all_functions: false\n", encoding="utf-8"
    )
    (project / "functions").mkdir()
    monkeypatch.setattr(sys, "path", list(sys.path))
    fw.scaffold()
    assert str((project / "functions").resolve()) not in sys.path


def test_options_source_all_functions_still_applies(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (project / "settings.yml").write_text(
        "default:\n  options:\n    source_all_functions: false\n", encoding="utf-8"
    )
    (project / "functions").mkdir()
    monkeypatch.setattr(sys, "path", list(sys.path))
    fw.scaffold()
    assert str((project / "functions").resolve()) not in sys.path


def test_user_paths_expand_the_home_folder(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(project.parent))
    monkeypatch.setenv("USERPROFILE", str(project.parent))
    monkeypatch.chdir(project.parent)
    monkeypatch.setattr(sys, "path", list(sys.path))
    settings_file = f"~/{project.name}/settings.yml"
    assert fw.scaffold(settings_file) == project.resolve()
    assert fw.settings_read(settings_file)["project_name"] == "Test"
    assert not fw.git_security_audit(settings_file, verbose=False).summary.empty
    assert list(fw.git_hooks_list(settings_file)["enabled"]) == [False, False, False]
    fw.ai_skills_update(f"~/{project.name}")
    fw.quarto_regenerate(f"~/{project.name}", backup=False)
    assert not (project.parent / "~").exists()
    assert (project / "_quarto.yml").is_file()


def test_scaffold_warns_about_modules_that_hide_others(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    functions = project / "functions"
    (functions / "pandas").mkdir(parents=True)
    (functions / "pandas" / "__init__.py").write_text("", encoding="utf-8")
    (functions / "data").mkdir()
    for name in ("statistics.py", "sys.py", "fw_helpers.py"):
        (functions / name).write_text("", encoding="utf-8")
    monkeypatch.setattr(sys, "path", list(sys.path))

    with pytest.warns(fw.FrameworkWarning) as caught:
        fw.scaffold()
    messages = [str(w.message) for w in caught]
    assert messages == [
        "functions/pandas hides the installed pandas module. "
        "Rename it so imports of pandas get the installed one.",
        "functions/statistics.py hides Python's statistics module. "
        "Rename it so imports of statistics get the standard one.",
    ]
    assert caught[0].filename == __file__


def test_capture_output_includes_framework_messages(project: Path) -> None:
    # Audit 2026-11-05: the message handler kept the original stderr, so Framework's own
    # progress messages escaped the capture
    from framework._log import show_messages

    show_messages()
    with fw.capture_output() as captured:
        fw.save_model({"a": 1}, "captured")
    assert any("Saved model to" in line for line in captured.lines)


def test_scaffold_names_a_missing_settings_file(tmp_path: Path) -> None:
    # Audit 2026-11-05: an explicit missing file was reported as a lookup from the cwd
    with pytest.raises(fw.ProjectNotFoundError, match=r"Settings file not found: .*nope\.yml"):
        fw.scaffold(tmp_path / "nope.yml")
