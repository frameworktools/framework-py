"""The embed link Framework Cloud answers a publish with."""

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


def answer(cloud: FakeCloud, **fields: Any) -> None:
    reply = {"id": 7, "url": URL, "version": 3, **fields}
    cloud.route("POST", "/api/v1/documents", lambda r: httpx.Response(201, json=reply))


@pytest.fixture
def messages(caplog: pytest.LogCaptureFixture) -> pytest.LogCaptureFixture:
    caplog.set_level("INFO", logger="framework")
    return caplog


def test_the_embed_link_is_printed_and_returned(
    cloud_project: Path,  # noqa: F811
    server: FakeCloud,  # noqa: F811
    messages: pytest.LogCaptureFixture,
) -> None:
    answer(server, embed_link="https://framework.pub/go/abcd")
    published = fw.publish(pd.DataFrame({"x": [1]}), to="cloud", slug="t")
    assert published == URL
    assert isinstance(published, Published) and isinstance(published, str)
    assert published.url == URL
    assert published.version == 3
    assert published.embed_link == "https://framework.pub/go/abcd"
    lines = messages.messages
    assert lines[lines.index(f"Published version 3: {URL}") + 1] == (
        "Embed: https://framework.pub/go/abcd"
    )


@pytest.mark.parametrize("reply", [{"embed_link": None}, {}], ids=["null", "missing"])
def test_no_embed_link_prints_nothing_more(
    cloud_project: Path,  # noqa: F811
    server: FakeCloud,  # noqa: F811
    messages: pytest.LogCaptureFixture,
    reply: dict[str, Any],
) -> None:
    answer(server, **reply)
    published = fw.publish_dag("dag { a -> b }", slug="d", to="cloud")
    assert published == URL
    assert isinstance(published, Published)
    assert published.embed_link is None
    assert "Embed:" not in messages.text
    assert f"Published version 3: {URL}" in messages.text


def test_publish_dag_and_snippets_print_the_embed_link(
    cloud_project: Path,  # noqa: F811
    server: FakeCloud,  # noqa: F811
    messages: pytest.LogCaptureFixture,
) -> None:
    answer(server, embed_link=URL)
    assert fw.publish_dag("dag { a -> b }", slug="d", to="cloud").embed_link == URL
    with fw.snippet("embed-check", to="cloud"):
        print("hi")
    assert messages.text.count(f"Embed: {URL}") == 2


def test_the_magic_shows_the_embed_link(
    cloud_project: Path,  # noqa: F811
    server: FakeCloud,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("IPython")
    from IPython.core.interactiveshell import InteractiveShell

    answer(server, embed_link="https://framework.pub/go/abcd")
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
    assert any("Embed: https://framework.pub/go/abcd" in text for text in shown)
