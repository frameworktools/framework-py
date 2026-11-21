"""The share and manage links Framework Cloud answers a publish with."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pandas as pd
import pytest

import framework as fw
from framework.cloud import Published
from tests.test_cloud import FakeCloud, cloud_project, server  # noqa: F401

URL = "https://framework.pub/d/abc123"
SHORT = "https://framework.pub/go/az7h"
MANAGE = "https://framework.pub/artifacts/123"


def answer(cloud: FakeCloud, **fields: Any) -> None:
    reply = {"id": 123, "url": URL, "version": 3, **fields}
    cloud.route("POST", "/api/v1/documents", lambda r: httpx.Response(201, json=reply))


@pytest.fixture
def messages(caplog: pytest.LogCaptureFixture) -> pytest.LogCaptureFixture:
    caplog.set_level("INFO", logger="framework")
    return caplog


def publish_table() -> Any:
    return fw.publish(pd.DataFrame({"x": [1]}), to="cloud", slug="t")


@pytest.mark.parametrize(
    ("reply", "printed", "embed_link"),
    [
        (
            {"embed_link": SHORT, "artifact_url": MANAGE, "visibility": "public"},
            ["Published version 3.", f"Share:  {SHORT}", f"Manage: {MANAGE}"],
            SHORT,
        ),
        (
            {"embed_link": URL, "artifact_url": MANAGE, "visibility": "unlisted"},
            ["Published version 3.", f"Share:  {URL}", f"Manage: {MANAGE}"],
            URL,
        ),
        (
            {"embed_link": None, "artifact_url": MANAGE, "visibility": "private"},
            ["Published version 3.", f"Manage: {MANAGE}"],
            None,
        ),
    ],
    ids=["public", "unlisted", "private"],
)
def test_published_lines(
    cloud_project: Path,  # noqa: F811
    server: FakeCloud,  # noqa: F811
    messages: pytest.LogCaptureFixture,
    reply: dict[str, Any],
    printed: list[str],
    embed_link: str | None,
) -> None:
    answer(server, readable_url="https://framework.pub/@ada/t", **reply)
    published = publish_table()
    assert messages.messages == printed
    assert published == URL
    assert isinstance(published, Published) and isinstance(published, str)
    assert published.url == URL
    assert published.version == 3
    assert published.embed_link == embed_link
    assert published.artifact_url == MANAGE


def test_an_unchanged_snippet(
    cloud_project: Path,  # noqa: F811
    server: FakeCloud,  # noqa: F811
    messages: pytest.LogCaptureFixture,
) -> None:
    answer(server, unchanged=True, embed_link=URL, artifact_url=MANAGE)
    with fw.snippet("links-check", to="cloud"):
        print("hi")
    assert messages.messages[-3:] == [
        "Unchanged, still version 3.",
        f"Share:  {URL}",
        f"Manage: {MANAGE}",
    ]


@pytest.mark.parametrize("reply", [{}, {"embed_link": None}], ids=["missing", "null"])
def test_an_older_server_still_prints_the_url(
    cloud_project: Path,  # noqa: F811
    server: FakeCloud,  # noqa: F811
    messages: pytest.LogCaptureFixture,
    reply: dict[str, Any],
) -> None:
    answer(server, **reply)
    published = fw.publish_dag("dag { a -> b }", slug="d", to="cloud")
    assert messages.messages == [f"Published version 3: {URL}"]
    assert published == URL
    assert published.embed_link is None
    assert published.artifact_url is None


def test_the_magic_shows_the_links(
    cloud_project: Path,  # noqa: F811
    server: FakeCloud,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("IPython")
    from IPython.core.interactiveshell import InteractiveShell

    answer(server, embed_link=SHORT, artifact_url=MANAGE)
    shell = InteractiveShell.instance()
    try:
        shell.extension_manager.load_extension("framework")
        shown: list[str] = []
        monkeypatch.setattr(
            shell.display_pub,
            "publish",
            lambda data, *a, **k: shown.append(str(data.get("text/plain", ""))),
        )
        shell.run_cell("%%fw_snippet fit --to cloud\nprint('hi')")
    finally:
        InteractiveShell.clear_instance()
    assert f"Published version 3.\nShare:  {SHORT}\nManage: {MANAGE}" in shown
