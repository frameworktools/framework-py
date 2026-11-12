"""Where new_project() creates a project when no location is given, as in R."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest

import framework as fw
from framework import cloud, create
from tests.test_cloud import FakeCloud

HERE_PROMPT = "Create the project here ({path})? [y/n] "
WHERE_PROMPT = "Where should the project go? Leave blank to use the current directory ({here}): "


@pytest.fixture
def server(monkeypatch: pytest.MonkeyPatch) -> FakeCloud:
    fake = FakeCloud()
    monkeypatch.setattr(cloud, "_transport", httpx.MockTransport(fake))
    return fake


@pytest.fixture
def here(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    folder = tmp_path / "work"
    folder.mkdir()
    monkeypatch.chdir(folder)
    return folder


def answers(monkeypatch: pytest.MonkeyPatch, *replies: str) -> list[str]:
    """Answer each prompt in turn, and record the prompts."""
    prompts: list[str] = []
    queue: Iterator[str] = iter(replies)

    def reply(prompt: str) -> str:
        prompts.append(prompt)
        return next(queue)

    monkeypatch.setattr(create, "_readline", reply)
    return prompts


def new(**kwargs: Any) -> Path:
    return fw.new_project("Field Study", type="bare", interactive=True, offline=True, **kwargs)


@pytest.mark.parametrize("reply", ["", "y", "Yes", " Y "])
def test_yes_or_enter_creates_it_here(
    here: Path, monkeypatch: pytest.MonkeyPatch, reply: str
) -> None:
    prompts = answers(monkeypatch, reply)
    root = new()
    assert root == here / "field-study"
    assert root.is_dir()
    assert prompts == [HERE_PROMPT.format(path=here / "field-study")]


def test_no_then_blank_uses_the_current_directory(
    here: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prompts = answers(monkeypatch, "n", "")
    assert new() == here / "field-study"
    assert prompts[1] == WHERE_PROMPT.format(here=here)


def test_no_then_a_path_is_the_parent_folder(
    here: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    answers(monkeypatch, "no", str(tmp_path / "code"))
    assert new() == tmp_path / "code" / "field-study"


def test_a_relative_path_is_taken_from_here(here: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    answers(monkeypatch, "n", "sub/")
    assert new() == here / "sub" / "field-study"


def test_a_tilde_path_is_expanded(
    here: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    answers(monkeypatch, "n", "~/code")
    assert new() == tmp_path / "home" / "code" / "field-study"


def test_a_path_ending_in_the_slug_is_used_as_is(
    here: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    answers(monkeypatch, "n", str(tmp_path / "code" / "field-study"))
    assert new() == tmp_path / "code" / "field-study"


def test_another_answer_asks_again(
    here: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level("INFO", logger="framework")
    prompts = answers(monkeypatch, "maybe", "y")
    assert new() == here / "field-study"
    assert len(prompts) == 2
    assert prompts[0] == prompts[1]
    assert "Answer y or n." in caplog.messages


def test_non_interactive_needs_a_location(here: Path) -> None:
    with pytest.raises(fw.FrameworkError, match=r"No project location given\. Pass `location=`"):
        fw.new_project("Field Study", type="bare", interactive=False, offline=True)
    assert not (here / "field-study").exists()


def test_an_explicit_relative_location_is_made_absolute(
    here: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level("INFO", logger="framework")
    root = fw.new_project(
        "Field Study", "studies/one", type="bare", interactive=False, offline=True
    )
    assert root == here / "studies" / "one"
    assert f"Creating the project at {here / 'studies' / 'one'}" in caplog.messages
    assert f"Project ready at {root}" in caplog.messages


def envelope() -> dict[str, Any]:
    return {
        "spec_version": 1,
        "name": "Cloud Study",
        "layers": [
            {"kind": "base", "key": "project", "layer": {"type": "project"}},
            {"kind": "project", "key": "cloud-study-2", "layer": {"language": "python"}},
        ],
        "source": {"origin": "cloud"},
    }


def test_a_cloud_key_asks_with_the_project_slug(
    here: Path, server: FakeCloud, monkeypatch: pytest.MonkeyPatch
) -> None:
    server.route("GET", "/api/v1/projects/pull", lambda r: httpx.Response(200, json=envelope()))
    prompts = answers(monkeypatch, "")
    root = fw.new(key="fwp_project0123", interactive=True)
    assert root == here / "cloud-study-2"
    assert prompts == [HERE_PROMPT.format(path=here / "cloud-study-2")]


def test_a_cloud_key_without_a_location_is_an_error_when_not_interactive(
    here: Path, server: FakeCloud
) -> None:
    server.route("GET", "/api/v1/projects/pull", lambda r: httpx.Response(200, json=envelope()))
    with pytest.raises(fw.FrameworkError, match="No project location given"):
        fw.new(key="fwp_project0123", interactive=False)
    assert list(here.iterdir()) == []
