"""A project names the server that issued its token.

Every path that writes a new FW_PROJECT_TOKEN writes FW_CLOUD_URL too, and a project
with a token but no FW_CLOUD_URL never falls back to the saved login.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

import httpx
import pytest

import framework as fw
from framework import cloud, dotenv
from framework.__main__ import main
from tests.conftest import save_login
from tests.test_cloud import FakeCloud, choices_envelope, envelope

SELF_HOSTED = "https://cloud.example.edu"
MESSAGE = re.escape(cloud.PROJECT_SERVER_MISSING)


@pytest.fixture
def server(monkeypatch: pytest.MonkeyPatch) -> FakeCloud:
    """The fake cloud, with the last login on a self-hosted server."""
    fake = FakeCloud()
    monkeypatch.setattr(cloud, "_transport", httpx.MockTransport(fake))
    os.environ["FW_CLOUD_TOKEN"] = "fw_user0123456"
    save_login(SELF_HOSTED)
    return fake


@pytest.fixture
def adopted(tmp_path: Path) -> Path:
    root = tmp_path / "Old Study"
    for folder in ("data/raw", "scripts"):
        (root / folder).mkdir(parents=True)
    fw.adopt(root, yes=True)
    return root


def _env(root: Path) -> dict[str, str]:
    return dotenv.read(root / ".env")


def _created(request: httpx.Request) -> httpx.Response:
    body = json.loads(request.content)
    return httpx.Response(
        201,
        json={
            "slug": body.get("slug") or "lab",
            "type": "project",
            "url": f"{SELF_HOSTED}/projects/lab",
            "token": "fwp_new0123456",
        },
    )


# Resolving ------------------------------------------------------------------------------


def test_a_token_without_a_server_never_uses_the_saved_login(project: Path) -> None:
    save_login(SELF_HOSTED)
    (project / ".env").write_text("FW_PROJECT_TOKEN=fwp_project0123\n", encoding="utf-8")
    with pytest.raises(cloud.ProjectServerMissingError, match=MESSAGE):
        cloud.cloud_server(scope="project")
    assert not isinstance(cloud.ProjectServerMissingError(""), cloud.NoServerError)
    # Account calls keep the saved login
    assert cloud.cloud_server().url == SELF_HOSTED
    # The url argument and the environment variable still name it
    assert cloud.cloud_server("x.example.org", scope="project").url == "https://x.example.org"
    os.environ["FW_CLOUD_URL"] = "https://env.example.org"
    assert cloud.cloud_server(scope="project").url == "https://env.example.org"
    # An empty FW_CLOUD_URL names nothing
    del os.environ["FW_CLOUD_URL"]
    (project / ".env").write_text("FW_PROJECT_TOKEN=fwp_1\nFW_CLOUD_URL=\n", encoding="utf-8")
    with pytest.raises(cloud.ProjectServerMissingError):
        cloud.cloud_server(scope="project")


def test_a_project_server_wins_over_a_different_saved_login(project: Path) -> None:
    save_login("https://framework.pub")
    (project / ".env").write_text(
        f"FW_PROJECT_TOKEN=fwp_project0123\nFW_CLOUD_URL={SELF_HOSTED}\n", encoding="utf-8"
    )
    server = cloud.cloud_server(scope="project")
    assert (server.url, server.source) == (SELF_HOSTED, "the project's .env")


def test_project_calls_surface_the_missing_server(
    project: Path, server: FakeCloud, capsys: pytest.CaptureFixture[str]
) -> None:
    (project / ".env").write_text("FW_PROJECT_TOKEN=fwp_project0123\n", encoding="utf-8")
    report = project / "report.html"
    report.write_text("<p>hi</p>", encoding="utf-8")
    with pytest.raises(cloud.ProjectServerMissingError, match=MESSAGE):
        fw.publish(report)
    with pytest.raises(cloud.ProjectServerMissingError, match=MESSAGE):
        fw.ledger_list()
    with pytest.raises(cloud.ProjectServerMissingError, match=MESSAGE):
        fw.pull_skills(project, dry_run=True)
    with pytest.raises(cloud.ProjectServerMissingError, match=MESSAGE):
        fw.pull_data_registry(project, dry_run=True)
    assert server.requests == []
    # Status reports it and carries on with the account
    capsys.readouterr()
    fw.cloud_status()
    assert "FW_PROJECT_TOKEN but no FW_CLOUD_URL" in capsys.readouterr().err


# Writing --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("before", "after"),
    [
        (
            "",
            f"\n# Framework Cloud project key and server (created by framework.new)\n"
            f"FW_PROJECT_TOKEN=fwp_t\nFW_CLOUD_URL={SELF_HOSTED}\n",
        ),
        (
            "A=1\nFW_CLOUD_URL=\nB=2\n",
            "A=1\nFW_CLOUD_URL=https://cloud.example.edu\nB=2\n\n"
            "# Framework Cloud project key and server (created by framework.new)\n"
            "FW_PROJECT_TOKEN=fwp_t\n",
        ),
        (
            "FW_PROJECT_TOKEN=\nFW_CLOUD_URL=https://framework.pub\nB=2\n",
            f"FW_PROJECT_TOKEN=fwp_t\nFW_CLOUD_URL={SELF_HOSTED}\nB=2\n",
        ),
        (
            "# FW_CLOUD_URL=\nFW_PROJECT_TOKEN=\n",
            f"FW_CLOUD_URL={SELF_HOSTED}\nFW_PROJECT_TOKEN=fwp_t\n",
        ),
        ("FW_PROJECT_TOKEN=\nB=2\n", f"FW_PROJECT_TOKEN=fwp_t\nFW_CLOUD_URL={SELF_HOSTED}\nB=2\n"),
    ],
)
def test_write_project_token_writes_the_server(tmp_path: Path, before: str, after: str) -> None:
    if before:
        (tmp_path / ".env").write_text(before, encoding="utf-8")
    cloud.write_project_token(tmp_path, "fwp_t", "cloud.example.edu/")
    assert (tmp_path / ".env").read_text(encoding="utf-8").lstrip("\n") == after.lstrip("\n")


def test_a_kept_token_leaves_the_server_alone(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("FW_PROJECT_TOKEN=fwp_old\n", encoding="utf-8")
    assert cloud.write_project_token(tmp_path, "fwp_new", SELF_HOSTED) == []
    assert (tmp_path / ".env").read_text(encoding="utf-8") == "FW_PROJECT_TOKEN=fwp_old\n"


def test_new_from_a_key_writes_the_saved_login_server(tmp_path: Path, server: FakeCloud) -> None:
    server.route("GET", "/api/v1/projects/pull", lambda r: httpx.Response(200, json=envelope()))
    root = fw.new(key="fwp_project0123", location=tmp_path / "study", interactive=False)
    assert str(server.requests[0].url).startswith(SELF_HOSTED)
    assert _env(root)["FW_CLOUD_URL"] == SELF_HOSTED


def test_new_from_cloud_choices_writes_the_server(tmp_path: Path, server: FakeCloud) -> None:
    server.route("POST", "/api/v1/resolve", lambda r: httpx.Response(200, json=choices_envelope()))
    server.route("POST", "/api/v1/projects", _created)
    root = fw.new("Lab", tmp_path / "lab", "course", interactive=False)
    env = _env(root)
    assert (env["FW_PROJECT_TOKEN"], env["FW_CLOUD_URL"]) == ("fwp_new0123456", SELF_HOSTED)


@pytest.mark.parametrize("older", [False, True])
def test_registering_a_local_project_writes_the_server(
    tmp_path: Path, server: FakeCloud, older: bool
) -> None:
    def legacy(request: httpx.Request) -> httpx.Response:
        if "project_type" not in json.loads(request.content):
            return httpx.Response(404, json={"message": "Not found"})
        return httpx.Response(
            201, json={"project": {"slug": "lab"}, "pull_token": "fwp_old0123456"}
        )

    server.route("POST", "/api/v1/projects", legacy if older else _created)
    (tmp_path / ".env").write_text("FW_CLOUD_URL=https://framework.pub\n", encoding="utf-8")
    cloud.register_project(tmp_path, "Lab", "project", {"type": "project"})
    assert _env(tmp_path)["FW_CLOUD_URL"] == SELF_HOSTED


def test_cloud_register_replaces_a_different_server(adopted: Path, server: FakeCloud) -> None:
    server.route("POST", "/api/v1/projects", _created)
    (adopted / ".env").write_text("FW_CLOUD_URL=https://framework.pub\nA=1\n", encoding="utf-8")
    fw.cloud_register(adopted)
    assert (adopted / ".env").read_text(encoding="utf-8") == (
        f"FW_CLOUD_URL={SELF_HOSTED}\nA=1\n\n"
        "# Framework Cloud project key and server (created by framework.new)\n"
        "FW_PROJECT_TOKEN=fwp_new0123456\n"
    )


@pytest.mark.parametrize("minted_url", [None, "https://minted.example.edu/"])
def test_linking_to_your_project_writes_the_server(
    adopted: Path, server: FakeCloud, monkeypatch: pytest.MonkeyPatch, minted_url: str | None
) -> None:
    problem = "You already have a project with this slug."
    server.route(
        "POST",
        "/api/v1/projects",
        lambda r: httpx.Response(422, json={"message": problem, "errors": {"slug": [problem]}}),
    )
    minted: dict[str, Any] = {"token": "fwp_minted0123"}
    if minted_url:
        minted["url"] = minted_url
    server.route(
        "POST",
        "/api/v1/projects/old-study/pull-tokens",
        lambda r: httpx.Response(201, json=minted),
    )
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt: "y")
    fw.cloud_register(adopted)
    expected = "https://minted.example.edu" if minted_url else SELF_HOSTED
    assert _env(adopted)["FW_CLOUD_URL"] == expected


def test_adopt_cloud_writes_the_server(tmp_path: Path, server: FakeCloud) -> None:
    root = tmp_path / "cohort"
    (root / "scripts").mkdir(parents=True)
    server.route("POST", "/api/v1/projects", _created)
    assert main(["adopt", "--path", str(root), "--yes", "--cloud"]) == 0
    assert _env(root)["FW_CLOUD_URL"] == SELF_HOSTED


def test_a_live_ledger_push_skips_without_failing_the_save(
    project: Path, server: FakeCloud, capsys: pytest.CaptureFixture[str]
) -> None:
    import pandas as pd

    (project / ".env").write_text("FW_PROJECT_TOKEN=fwp_project0123\n", encoding="utf-8")
    (project / "inputs").mkdir()
    os.environ["FW_LEDGER"] = "live"
    fw.data_save(pd.DataFrame({"x": [1]}), "inputs/a.csv")
    assert (project / "inputs/a.csv").is_file()
    assert "Ledger push skipped: " + cloud.PROJECT_SERVER_MISSING in capsys.readouterr().err
    assert server.requests == []
