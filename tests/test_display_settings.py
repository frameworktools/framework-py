"""Embed display settings: the display option on Framework Cloud publishing."""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import Any

import httpx
import pandas as pd
import pytest

import framework as fw
from framework import snippets
from framework.cloud import DISPLAY_KEYS, display_settings
from framework.errors import FrameworkError
from tests.test_cloud import FakeCloud, cloud_project, server  # noqa: F401


@pytest.fixture
def documents(server: FakeCloud) -> FakeCloud:  # noqa: F811
    server.route(
        "POST",
        "/api/v1/documents",
        lambda r: httpx.Response(201, json={"id": 7, "url": "https://x/d/a", "version": 1}),
    )
    return server


def field(cloud: FakeCloud, name: str) -> str | None:
    """A form field of the last multipart publish request, or None when it wasn't sent."""
    body = cloud.requests[-1].content.decode("utf-8", "replace")
    marker = f'name="{name}"\r\n\r\n'
    if marker not in body:
        return None
    return body.split(marker, 1)[1].split("\r\n--", 1)[0]


# Validation ----------------------------------------------------------------------------


def test_settings_become_compact_json() -> None:
    assert display_settings(None) is None
    assert display_settings({"analysis": False, "title": True}) == '{"analysis":false,"title":true}'
    assert {"legend", "credit", "notes", "ignored"} <= set(DISPLAY_KEYS)
    assert len(DISPLAY_KEYS) == 16


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ({"legnd": False}, "Unknown display setting: legnd. Settings: title, description"),
        ({"code": 0}, "must be True or False: code"),
        ({"code": "off"}, "must be True or False: code"),
        (["code"], "must be a dict"),
    ],
)
def test_invalid_settings_are_refused(value: Any, message: str) -> None:
    with pytest.raises(FrameworkError, match=message):
        display_settings(value)


def test_invalid_settings_are_refused_before_publishing(
    cloud_project: Path,  # noqa: F811
    documents: FakeCloud,
) -> None:
    with pytest.raises(FrameworkError, match="Unknown display setting"):
        fw.publish(pd.DataFrame({"x": [1]}), to="cloud", display={"summaries": False})
    with pytest.raises(FrameworkError, match="Unknown display setting"):
        fw.publish_dag("dag { a -> b }", to="cloud", display={"legnd": False})
    assert all(r.method == "GET" for r in documents.requests)


# Framework Cloud -----------------------------------------------------------------------


def test_publish_sends_display_only_when_given(
    cloud_project: Path,  # noqa: F811
    documents: FakeCloud,
) -> None:
    frame = pd.DataFrame({"x": [1]})
    fw.publish(frame, to="cloud", slug="t")
    assert field(documents, "display") is None
    fw.publish(frame, to="cloud", slug="t", display={"column_summaries": False})
    assert json.loads(field(documents, "display") or "") == {"column_summaries": False}


def test_publish_dag_sends_display(
    cloud_project: Path,  # noqa: F811
    documents: FakeCloud,
) -> None:
    fw.publish_dag("dag { a -> b }", slug="d", to="cloud", display={"code": False, "legend": False})
    assert field(documents, "kind") == "dag"
    assert json.loads(field(documents, "display") or "") == {"code": False, "legend": False}


def test_publish_data_sends_display(
    cloud_project: Path,  # noqa: F811
    documents: FakeCloud,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = {"publish": {"default": "cloud"}}
    monkeypatch.setattr(
        importlib.import_module("framework.settings"), "settings_read", lambda *a: config
    )
    monkeypatch.setattr("framework.storage.settings_read", lambda *a: config)
    fw.publish_data(pd.DataFrame({"x": [1]}), "tables/x.csv", display={"title": False})
    assert json.loads(field(documents, "display") or "") == {"title": False}


# Other destinations ---------------------------------------------------------------------


def test_folder_destination_ignores_display_with_a_message(
    project: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    config = {
        "publish": {"destinations": {"lab": {"driver": "folder", "path": str(project / "site")}}}
    }
    monkeypatch.setattr(
        importlib.import_module("framework.settings"), "settings_read", lambda *a: config
    )
    monkeypatch.setattr("framework.storage.settings_read", lambda *a: config)
    caplog.set_level("INFO", logger="framework")
    fw.publish(pd.DataFrame({"x": [1]}), to="lab", slug="x", display={"title": False})
    assert (project / "site" / "x" / "artifact.json").is_file()
    assert 'display applies on Framework Cloud only; the "lab" destination ignores it.' in (
        caplog.text
    )


# Snippets ------------------------------------------------------------------------------


def test_snippet_passes_display_to_publish(
    cloud_project: Path,  # noqa: F811
    documents: FakeCloud,
) -> None:
    with fw.snippet("display-check", to="cloud", display={"links": False}):
        print("hi")
    assert field(documents, "kind") == "code_snippet"
    assert json.loads(field(documents, "display") or "") == {"links": False}


def test_snippet_refuses_invalid_display_before_running() -> None:
    ran = []
    with (
        pytest.raises(FrameworkError, match="Unknown display setting"),
        fw.snippet("x", display={"outputs": False}),
    ):
        ran.append(True)
    assert ran == []


def test_magic_display_option() -> None:
    arguments = snippets._magic_arguments("fit --display output=off --display links=on")
    assert arguments.display == {"output": False, "links": True}
    assert snippets._magic_arguments("fit").display is None
    with pytest.raises(FrameworkError, match="SETTING=on or SETTING=off"):
        snippets._magic_arguments("fit --display output=false")
    with pytest.raises(FrameworkError, match="Unknown display setting: outputs"):
        snippets._magic_arguments("fit --display outputs=off")
