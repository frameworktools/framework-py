"""fw.flatten_versions(): keeping only an artifact's newest version on Framework Cloud."""

from __future__ import annotations

import io
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest

import framework as fw
from framework.cloud import CloudError
from framework.errors import FrameworkError
from tests.test_cloud import FakeCloud, cloud_project, server  # noqa: F401

MANAGE = "https://cloud.example.edu/artifacts/12"


@pytest.fixture
def messages(caplog: pytest.LogCaptureFixture) -> pytest.LogCaptureFixture:
    caplog.set_level("INFO", logger="framework")
    return caplog


def answer(cloud: FakeCloud, path: str, status: int = 200, **reply: Any) -> None:
    body = reply or {"artifact": "fig-1", "version": 1, "removed": 3, "artifact_url": MANAGE}
    cloud.route("POST", path, lambda r: httpx.Response(status, json=body))


def console(monkeypatch: pytest.MonkeyPatch, reply: str) -> list[str]:
    asked: list[str] = []
    monkeypatch.setattr(sys, "stdin", io.StringIO(reply))
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)

    def ask(question: str) -> str:
        asked.append(question)
        return reply

    monkeypatch.setattr("builtins.input", ask)
    return asked


def posts(cloud: FakeCloud) -> list[httpx.Request]:
    return [r for r in cloud.requests if r.method == "POST"]


def test_a_project_token_flattens_its_artifact(
    cloud_project: Path,  # noqa: F811
    server: FakeCloud,  # noqa: F811
    messages: pytest.LogCaptureFixture,
) -> None:
    answer(server, "/api/v1/artifacts/fig-1/flatten")
    url = fw.flatten_versions("fig-1", confirm=True)
    assert url == MANAGE
    request = posts(server)[-1]
    assert request.url.host == "cloud.example.edu"
    assert request.headers["Authorization"] == "Bearer fwp_project0123"
    assert "project" not in request.url.params
    assert messages.messages == [
        "Flattened fig-1 to version 1; removed 3 older versions.",
        f"Manage: {MANAGE}",
    ]


def test_an_account_token_names_the_project(
    project: Path,
    server: FakeCloud,  # noqa: F811
    messages: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FW_CLOUD_TOKEN", "fw_account")
    answer(
        server,
        "/api/v1/artifacts/fig 1/flatten",
        artifact="fig 1",
        version=1,
        removed=1,
        artifact_url=MANAGE,
    )
    fw.flatten_versions("fig 1", project="study", confirm=True)
    request = posts(server)[-1]
    assert request.url.raw_path.startswith(b"/api/v1/artifacts/fig%201/flatten")
    assert request.url.params["project"] == "study"
    assert request.url.host == "framework.pub"
    assert request.headers["Authorization"] == "Bearer fw_account"
    assert messages.messages[0] == "Flattened fig 1 to version 1; removed 1 older version."


def test_a_console_asks_and_no_is_the_default(
    cloud_project: Path,  # noqa: F811
    server: FakeCloud,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    messages: pytest.LogCaptureFixture,
) -> None:
    answer(server, "/api/v1/artifacts/fig-1/flatten")
    asked = console(monkeypatch, "")
    assert fw.flatten_versions("fig-1") is None
    assert asked and asked[0].endswith("[y/N] ")
    assert "deleted for good" in asked[0]
    assert messages.messages == ["Nothing changed."]
    assert posts(server) == []
    console(monkeypatch, "y")
    assert fw.flatten_versions("fig-1") == MANAGE
    assert len(posts(server)) == 1


def test_scripts_need_confirm(
    cloud_project: Path,  # noqa: F811
    server: FakeCloud,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    with pytest.raises(FrameworkError, match="confirm=True"):
        fw.flatten_versions("fig-1")
    assert fw.flatten_versions("fig-1", confirm=False) is None
    assert posts(server) == []


@pytest.mark.parametrize(
    ("status", "reply", "message"),
    [
        (404, {"message": "Not found."}, 'No artifact "fig-1" on cloud.example.edu'),
        (409, {"message": "Several projects have fig-1."}, "Pass project="),
        (422, {"message": "External publications have no versions to flatten."}, "External"),
    ],
)
def test_refusals_are_errors(
    cloud_project: Path,  # noqa: F811
    server: FakeCloud,  # noqa: F811
    status: int,
    reply: dict[str, Any],
    message: str,
) -> None:
    answer(server, "/api/v1/artifacts/fig-1/flatten", status, **reply)
    with pytest.raises(CloudError, match=message):
        fw.flatten_versions("fig-1", confirm=True)


def test_an_empty_slug_is_refused(cloud_project: Path) -> None:  # noqa: F811
    with pytest.raises(FrameworkError, match="slug"):
        fw.flatten_versions(" ", confirm=True)
