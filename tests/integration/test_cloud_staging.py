"""Framework Cloud against the staging server. Opt-in and local only.

The staging server is reachable only from the house network and the tailnet,
and CI never reaches it. The tests run when all three variables are set and
skip otherwise, also in CI:

    FW_CLOUD_STAGING_URL          https://framework-cloud-staging.pequod.sh
    FW_CLOUD_STAGING_TOKEN        personal token of ci-primary
    FW_CLOUD_STAGING_TOKEN_OTHER  personal token of ci-other

The tokens are in the System vault (ansible/vault/apps/framework-cloud-staging.yml).
The server is reset every night at 04:30 New York time and may be shared with
other runs, so every project gets a unique name and no test depends on the
account being empty.
"""

from __future__ import annotations

import hashlib
import os
import uuid
from pathlib import Path
from typing import Any

import pytest

import framework as fw
from framework import cloud

pytestmark = pytest.mark.integration

URL = os.environ.get("FW_CLOUD_STAGING_URL", "").rstrip("/")
TOKEN = os.environ.get("FW_CLOUD_STAGING_TOKEN", "")
TOKEN_OTHER = os.environ.get("FW_CLOUD_STAGING_TOKEN_OTHER", "")


@pytest.fixture
def staging(monkeypatch: pytest.MonkeyPatch) -> str:
    """The staging server as this test's default, with ci-primary logged in."""
    if not (URL and TOKEN and TOKEN_OTHER):
        pytest.skip("opt-in: set FW_CLOUD_STAGING_URL and both staging tokens")
    monkeypatch.setattr(cloud, "_transport", None)
    monkeypatch.setenv("FW_CLOUD_URL", URL)
    monkeypatch.setenv("FW_CLOUD_TOKEN", TOKEN)
    return URL


@pytest.fixture
def registered(staging: str, project: Path) -> dict[str, Any]:
    """A new project on the server, with its key in the project's .env."""
    name = f"ci-py-{uuid.uuid4().hex[:10]}"
    result = cloud.register_project(project, name, "project")
    assert result is not None, "registration failed (see the messages above)"
    return dict(result)


def test_status_and_a_bad_token(staging: str, monkeypatch: pytest.MonkeyPatch) -> None:
    assert fw.cloud_status() is True
    monkeypatch.setenv("FW_CLOUD_TOKEN", "fw_not_a_real_token_0000")
    assert fw.cloud_status() is False
    with pytest.raises(cloud.CloudError):
        cloud.api("/api/v1/me", "fw_not_a_real_token_0000", staging)


def test_settings_document_is_readable(staging: str) -> None:
    assert isinstance(fw.cloud_settings(), dict)


def test_registration_writes_the_key_to_env_only(registered: dict[str, Any], project: Path) -> None:
    env = (project / ".env").read_text(encoding="utf-8")
    assert f"FW_PROJECT_TOKEN={registered['pull_token']}" in env
    assert f"FW_CLOUD_URL={URL}" in env
    assert registered["pull_token"] not in (project / "settings.yml").read_text(encoding="utf-8")


def test_pull_returns_the_project_definition(registered: dict[str, Any]) -> None:
    envelope, server = cloud.fetch_layers(registered["pull_token"], URL)
    assert server == URL
    assert isinstance(envelope, dict)


def test_ledger_round_trip(registered: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FW_LEDGER", "live")
    digest = hashlib.sha256(uuid.uuid4().bytes).hexdigest()
    assert cloud.ledger_push_quietly("inputs.raw.ci", digest) is not None
    ledger = fw.ledger_list("inputs.raw.ci")
    assert ledger["hash"].iloc[0] == digest
    assert set(ledger["name"]) == {"inputs.raw.ci"}


def test_publish_a_document(registered: dict[str, Any], project: Path) -> None:
    page = project / "outputs/public/report.html"
    page.parent.mkdir(parents=True)
    page.write_text("<!DOCTYPE html><title>CI</title><p>staging</p>", encoding="utf-8")
    url = cloud.cloud_publish(page, title="CI report")
    assert url.startswith(URL)


def test_another_account_cannot_use_this_project(registered: dict[str, Any]) -> None:
    """ci-other's personal token is not this project's key and cannot read its ledger."""
    with pytest.raises(cloud.CloudError):
        cloud.api("/api/v1/ledger", TOKEN_OTHER, URL)
    slug = registered["project"]["slug"]
    try:
        listing = cloud.api("/api/v1/projects", TOKEN_OTHER, URL)
    except cloud.CloudError:
        return
    assert slug not in str(listing)
