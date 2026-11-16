from __future__ import annotations

import os
from pathlib import Path

import pytest

from framework import dotenv
from framework.errors import SettingsError


def test_parse_follows_r_dotenv() -> None:
    text = (
        "# comment\n"
        "\n"
        "PLAIN=value\n"
        'DOUBLE="quoted value"\n'
        "SINGLE='single'\n"
        "export EXPORTED=yes\n"
        "INLINE=value # not a comment\n"
        'ESCAPE="a\\nb"\n'
        "EMPTY=\n"
        "  INDENTED=1\n"
        "PLAIN=second\n"
    )
    assert dotenv.parse(text) == {
        "PLAIN": "second",
        "DOUBLE": "quoted value",
        "SINGLE": "single",
        "EXPORTED": "yes",
        "INLINE": "value # not a comment",
        "ESCAPE": "a\\nb",
        "EMPTY": "",
        "INDENTED": "1",
    }


def test_unparseable_line() -> None:
    with pytest.raises(SettingsError, match=r"Cannot parse \.env line: not a pair"):
        dotenv.parse("not a pair\n")


def test_load_keeps_shell_values_and_skips_empty_ones(tmp_path: Path) -> None:
    # Audit 2026-11-05: a .env value replaced a shell value, and an empty one blanked it
    os.environ["FW_DOTENV_TEST"] = "shell"
    os.environ["FW_DOTENV_KEY"] = "exported"
    os.environ["FW_DOTENV_BLANK"] = ""
    (tmp_path / ".env").write_text(
        "FW_DOTENV_TEST=file\nFW_DOTENV_KEY=\nFW_DOTENV_BLANK=filled\nFW_DOTENV_NEW=new\n",
        encoding="utf-8",
    )
    assert dotenv.load(tmp_path / ".env") == {"FW_DOTENV_BLANK": "filled", "FW_DOTENV_NEW": "new"}
    assert os.environ["FW_DOTENV_TEST"] == "shell"
    assert os.environ["FW_DOTENV_KEY"] == "exported"
    assert os.environ["FW_DOTENV_BLANK"] == "filled"
    assert os.environ["FW_DOTENV_NEW"] == "new"


def test_loading_another_project_takes_back_the_last_ones_values(tmp_path: Path) -> None:
    os.environ["FW_DOTENV_BLANK"] = ""
    first, second = tmp_path / "a.env", tmp_path / "b.env"
    first.write_text("FW_DOTENV_A=a\nFW_DOTENV_BLANK=a\nFW_DOTENV_BOTH=a\n", encoding="utf-8")
    second.write_text("FW_DOTENV_BOTH=b\n", encoding="utf-8")
    dotenv.load(first)
    dotenv.load(second)
    assert "FW_DOTENV_A" not in os.environ
    assert os.environ["FW_DOTENV_BLANK"] == ""
    assert os.environ["FW_DOTENV_BOTH"] == "b"


def test_scoped_values_leave_the_environment_as_it_was() -> None:
    os.environ["FW_DOTENV_TEST"] = "shell"
    with dotenv.scoped({"FW_DOTENV_TEST": "file", "FW_DOTENV_NEW": "new", "FW_EMPTY": ""}) as set_:
        assert set_ == {"FW_DOTENV_NEW": "new"}
        assert os.environ["FW_DOTENV_NEW"] == "new"
        assert os.environ["FW_DOTENV_TEST"] == "shell"
        assert "FW_EMPTY" not in os.environ
    assert "FW_DOTENV_NEW" not in os.environ
    assert os.environ["FW_DOTENV_TEST"] == "shell"


def test_value_and_missing_file(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    assert dotenv.read(env) == {}
    env.write_text("FW_CLOUD_URL=\nFW_PROJECT_TOKEN=abc\n", encoding="utf-8")
    assert dotenv.value(env, "FW_PROJECT_TOKEN") == "abc"
    assert dotenv.value(env, "FW_CLOUD_URL") is None
    assert dotenv.value(env, "MISSING") is None


def test_set_values_replaces_lines_in_place(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("A=1\nFW_PROJECT_TOKEN=\nB=2\nFW_CLOUD_URL=https://old.example.org\n")
    written = dotenv.set_values(
        env, {"FW_PROJECT_TOKEN": "new", "FW_CLOUD_URL": "https://cloud.example.edu"}
    )
    assert written == ["FW_PROJECT_TOKEN", "FW_CLOUD_URL"]
    assert env.read_text(encoding="utf-8") == (
        "A=1\nFW_PROJECT_TOKEN=new\nB=2\nFW_CLOUD_URL=https://cloud.example.edu\n"
    )
    assert dotenv.set_values(env, {"FW_CLOUD_URL": "https://cloud.example.edu"}) == []


def test_set_values_fills_placeholders_or_goes_next_to_the_last_key(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("# FW_CLOUD_URL=\nFW_PROJECT_TOKEN=\nB=2\n", encoding="utf-8")
    dotenv.set_values(env, {"FW_PROJECT_TOKEN": "t", "FW_CLOUD_URL": "https://x.org"})
    assert (
        env.read_text(encoding="utf-8") == "FW_CLOUD_URL=https://x.org\nFW_PROJECT_TOKEN=t\nB=2\n"
    )
    env.write_text("FW_PROJECT_TOKEN=\nB=2", encoding="utf-8")
    dotenv.set_values(env, {"FW_PROJECT_TOKEN": "t", "FW_CLOUD_URL": "https://x.org"})
    assert (
        env.read_text(encoding="utf-8") == "FW_PROJECT_TOKEN=t\nFW_CLOUD_URL=https://x.org\nB=2\n"
    )


def test_set_values_appends_new_keys_under_a_header(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("A=1", encoding="utf-8")
    dotenv.set_values(env, {"K": "v", "L": "w"}, header="Added")
    assert env.read_text(encoding="utf-8") == "A=1\n\n# Added\nK=v\nL=w\n"
    missing = tmp_path / "new.env"
    dotenv.set_values(missing, {"K": "v"})
    assert missing.read_text(encoding="utf-8") == "K=v\n"
