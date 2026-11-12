"""Where new_project() creates a project when no location is given, as in R."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import quote

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
    # expanduser reads HOME on POSIX and USERPROFILE on Windows
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
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


# The Projects folder ------------------------------------------------------------------

IN_PROMPT = "Create the project in {path}? [y/n] "


def test_a_projects_folder_is_offered_instead_of_here(
    here: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    projects = tmp_path / "not" / "yet" / "research"
    monkeypatch.setattr(create, "_projects_dir", lambda: str(projects))
    prompts = answers(monkeypatch, "")
    root = new()
    assert root == projects / "field-study"
    assert root.is_dir()
    assert prompts == [IN_PROMPT.format(path=projects / "field-study")]


def test_a_projects_folder_with_a_tilde_is_expanded(
    here: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # expanduser reads HOME on POSIX and USERPROFILE on Windows
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    monkeypatch.setattr(create, "_projects_dir", lambda: "~/research")
    prompts = answers(monkeypatch, "y")
    assert new() == tmp_path / "home" / "research" / "field-study"
    assert prompts == [IN_PROMPT.format(path=tmp_path / "home" / "research" / "field-study")]


def test_declining_the_projects_folder_asks_where(
    here: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(create, "_projects_dir", lambda: str(tmp_path / "research"))
    prompts = answers(monkeypatch, "n", "")
    assert new() == here / "field-study"
    assert prompts == [
        IN_PROMPT.format(path=tmp_path / "research" / "field-study"),
        WHERE_PROMPT.format(here=here),
    ]
    assert not (tmp_path / "research").exists()


def test_non_interactive_ignores_the_projects_folder(
    here: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail() -> str:
        raise AssertionError("not asked for")

    monkeypatch.setattr(create, "_projects_dir", fail)
    with pytest.raises(fw.FrameworkError, match="No project location given"):
        fw.new_project("Field Study", type="bare", interactive=False, offline=True)


def test_the_projects_folder_comes_from_the_cloud_settings(
    server: FakeCloud, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FW_CLOUD_TOKEN", "fw_user")
    server.settings = {"revision": 3, "document": {"defaults": {"projects_dir": "~/research"}}}
    assert create._projects_dir() == "~/research"
    server.settings = {"revision": 4, "document": {"defaults": {"projects_dir": None}}}
    assert create._projects_dir() is None
    server.settings = {"revision": 5, "document": {"defaults": {"projects_dir": "  "}}}
    assert create._projects_dir() is None


def test_unset_without_a_token_or_mirror() -> None:
    assert create._projects_dir() is None


def test_offline_or_signed_out_the_projects_folder_comes_from_the_mirror(
    server: FakeCloud, monkeypatch: pytest.MonkeyPatch
) -> None:
    mirror = cloud._sync_paths()[0]
    mirror.parent.mkdir(parents=True, exist_ok=True)
    mirror.write_text("defaults:\n  projects_dir: ~/cached\n")
    assert create._projects_dir() == "~/cached"

    monkeypatch.setenv("FW_CLOUD_TOKEN", "fw_user")
    server.route("GET", "/api/v1/settings", lambda r: httpx.Response(503))
    assert create._projects_dir() == "~/cached"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        ("", None),
        ("~/research", "~/research"),
        ("~/My%20Projects/%C3%A9tudes", "~/My Projects/études"),
    ],
)
def test_the_projects_dir_header_is_decoded(value: str | None, expected: str | None) -> None:
    assert cloud.header_projects_dir(value) == expected


def test_a_cloud_key_offers_the_owners_projects_folder_from_the_header(
    here: Path, tmp_path: Path, server: FakeCloud, monkeypatch: pytest.MonkeyPatch
) -> None:
    projects = tmp_path / "My Projects" / "études"
    encoded = "/".join(quote(part) for part in str(projects).split("/"))
    server.route(
        "GET",
        "/api/v1/projects/pull",
        lambda r: httpx.Response(200, json=envelope(), headers={"Framework-Projects-Dir": encoded}),
    )
    monkeypatch.setattr(create, "_projects_dir", lambda: "/from/settings")
    prompts = answers(monkeypatch, "")
    root = fw.new(key="fwp_project0123", interactive=True)
    assert root == projects / "cloud-study-2"
    assert (root / "settings.yml").is_file()
    assert prompts == [IN_PROMPT.format(path=projects / "cloud-study-2")]


def test_a_cloud_key_without_the_header_uses_the_synced_projects_folder(
    here: Path, tmp_path: Path, server: FakeCloud, monkeypatch: pytest.MonkeyPatch
) -> None:
    server.route("GET", "/api/v1/projects/pull", lambda r: httpx.Response(200, json=envelope()))
    monkeypatch.setattr(create, "_projects_dir", lambda: str(tmp_path / "research"))
    prompts = answers(monkeypatch, "")
    assert (
        fw.new(key="fwp_project0123", interactive=True) == tmp_path / "research" / "cloud-study-2"
    )
    assert prompts == [IN_PROMPT.format(path=tmp_path / "research" / "cloud-study-2")]
