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


def test_load_replaces_existing_values(tmp_path: Path) -> None:
    os.environ["FW_DOTENV_TEST"] = "before"
    (tmp_path / ".env").write_text("FW_DOTENV_TEST=after\n", encoding="utf-8")
    dotenv.load(tmp_path / ".env")
    assert os.environ["FW_DOTENV_TEST"] == "after"


def test_value_and_missing_file(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    assert dotenv.read(env) == {}
    env.write_text("FW_CLOUD_URL=\nFW_PROJECT_TOKEN=abc\n", encoding="utf-8")
    assert dotenv.value(env, "FW_PROJECT_TOKEN") == "abc"
    assert dotenv.value(env, "FW_CLOUD_URL") is None
    assert dotenv.value(env, "MISSING") is None


def test_add_missing_keeps_existing_lines(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("FW_PROJECT_TOKEN=keep", encoding="utf-8")
    added = dotenv.add_missing(
        env, {"FW_PROJECT_TOKEN": "new", "FW_CLOUD_URL": "https://framework.pub"}
    )
    assert added == ["FW_CLOUD_URL"]
    assert env.read_text(encoding="utf-8") == (
        "FW_PROJECT_TOKEN=keep\nFW_CLOUD_URL=https://framework.pub\n"
    )
