"""cloud_register(): one request with the spec, as fw project link --create sends it."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import httpx
import pytest

import framework as fw
from framework import cloud
from framework.__main__ import main
from tests.test_cloud import FakeCloud


@pytest.fixture
def adopted(tmp_path: Path) -> Path:
    root = tmp_path / "Old Study"
    for folder in ("data/raw", "scripts", "assets"):
        (root / folder).mkdir(parents=True)
    fw.adopt(root, yes=True)
    return root


@pytest.fixture
def server(monkeypatch: pytest.MonkeyPatch) -> FakeCloud:
    fake = FakeCloud()
    monkeypatch.setattr(cloud, "_transport", httpx.MockTransport(fake))
    os.environ["FW_CLOUD_TOKEN"] = "fw_user0123456"
    return fake


def _created(sent: list[dict[str, Any]], **extra: Any) -> Any:
    def create(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        body = {
            "slug": sent[-1].get("slug"),
            "name": sent[-1].get("name"),
            "language": "python",
            "type": "project",
            "adopted": True,
            "url": "https://framework.pub/projects/old-study",
            "token": "fwp_project0123",
            "github": None,
            **extra,
        }
        return httpx.Response(201, json=body)

    return create


def test_registers_from_the_spec_and_links(adopted: Path, server: FakeCloud) -> None:
    sent: list[dict[str, Any]] = []
    server.route("POST", "/api/v1/projects", _created(sent))
    result = fw.cloud_register(adopted, description="Cohort study")
    assert result == {
        "slug": "old-study",
        "url": "https://framework.pub/projects/old-study",
        "type": "project",
        "adopted": True,
        "created": True,
        "linked": True,
        "spec_changed": True,
    }
    assert server.paths() == ["POST /api/v1/projects"]
    spec = json.loads((adopted / ".framework/spec.json").read_text(encoding="utf-8"))
    assert sent[0] == {
        "spec": spec,
        "name": "Old Study",
        "slug": "old-study",
        "description": "Cohort study",
        "adopted": True,
    }
    # Empty maps stay objects, as on disk
    assert sent[0]["spec"]["render_dirs"] == {}
    env = (adopted / ".env").read_text(encoding="utf-8")
    assert "FW_PROJECT_TOKEN=fwp_project0123" in env
    assert "FW_CLOUD_URL=https://framework.pub" in env

    # Linked: nothing changes, nothing is sent
    again = fw.cloud_register(adopted)
    assert (again["created"], again["linked"]) == (False, True)
    assert len(server.requests) == 1


def test_an_older_server_gets_the_spec_separately(adopted: Path, server: FakeCloud) -> None:
    sent: list[dict[str, Any]] = []
    pushed: list[dict[str, Any]] = []
    server.route("POST", "/api/v1/projects", _created(sent, type=None))

    def push(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer fwp_project0123"
        pushed.append(json.loads(request.content))
        return httpx.Response(200, json={"changed": True})

    server.route("PUT", "/api/v1/project/spec", push)
    with pytest.warns(fw.FrameworkWarning, match="older than the spec API"):
        fw.cloud_register(adopted)
    assert pushed[0]["spec"]["name"] == "Old Study"


@pytest.mark.parametrize(
    ("problem", "match"),
    [
        ("You already have a project with this slug.", "fw project link --from old-study"),
        ("A deleted project in your Trash uses this slug.", "in your Trash"),
        ("The slug may only contain letters.", "refused the slug old-study"),
    ],
)
def test_a_taken_slug(
    adopted: Path, server: FakeCloud, monkeypatch: pytest.MonkeyPatch, problem: str, match: str
) -> None:
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    server.route(
        "POST",
        "/api/v1/projects",
        lambda r: httpx.Response(422, json={"message": problem, "errors": {"slug": [problem]}}),
    )
    with pytest.raises(fw.CloudError, match=match):
        fw.cloud_register(adopted)
    assert not (adopted / ".env").exists()


def test_a_taken_slug_of_your_own_links_to_it(
    adopted: Path, server: FakeCloud, monkeypatch: pytest.MonkeyPatch
) -> None:
    problem = "You already have a project with this slug."
    server.route(
        "POST",
        "/api/v1/projects",
        lambda r: httpx.Response(422, json={"message": problem, "errors": {"slug": [problem]}}),
    )
    server.route(
        "POST",
        "/api/v1/projects/old-study/pull-tokens",
        lambda r: httpx.Response(201, json={"token": "fwp_minted0123"}),
    )
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt: "y")
    result = fw.cloud_register(adopted)
    assert (result["created"], result["linked"]) == (False, True)
    assert "FW_PROJECT_TOKEN=fwp_minted0123" in (adopted / ".env").read_text(encoding="utf-8")


def test_checks_before_sending(
    adopted: Path, server: FakeCloud, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(fw.FrameworkError, match="cannot be a slug"):
        fw.cloud_register(adopted, slug="Old Study")
    body = fw.cloud_register(adopted, dry_run=True)
    assert (body["slug"], body["adopted"]) == ("old-study", True)
    assert "<.framework/spec.json>" in capsys.readouterr().err
    # A spec Cloud would refuse stops here, with the fix
    spec_path = adopted / ".framework/spec.json"
    good = spec_path.read_text(encoding="utf-8")
    spec = json.loads(good)
    spec["scaffold"]["positron"] = True
    spec["source"]["upgraded_from"] = "framework 1.x"
    spec_path.write_text(json.dumps(spec), encoding="utf-8")
    with pytest.raises(fw.FrameworkError, match=r"doesn't match spec\.v1.*positron.*upgrade"):
        fw.cloud_register(adopted, dry_run=True)
    spec_path.write_text(good, encoding="utf-8")
    (adopted / ".framework/spec.json").unlink()
    with pytest.raises(fw.FrameworkError, match=r"no \.framework/spec\.json"):
        fw.cloud_register(adopted)
    with pytest.raises(fw.FrameworkError, match="Not a Framework project"):
        fw.cloud_register(tmp_path)
    private = tmp_path / "private" / "study"
    (private / "scripts").mkdir(parents=True)
    fw.adopt(private, yes=True)
    with pytest.raises(fw.FrameworkError, match="inside a private folder"):
        fw.cloud_register(private)
    assert fw.cloud_register(private, include_private=True, dry_run=True)["slug"] == "study"
    assert server.requests == []


def test_adopt_cloud_registers(tmp_path: Path, server: FakeCloud) -> None:
    root = tmp_path / "cohort"
    (root / "scripts").mkdir(parents=True)
    sent: list[dict[str, Any]] = []
    server.route("POST", "/api/v1/projects", _created(sent))
    assert main(["adopt", "--path", str(root), "--yes", "--cloud", "--slug", "my-cohort"]) == 0
    assert sent[0]["slug"] == "my-cohort"
    assert sent[0]["spec"]["source"]["adopted"] is True
    assert "FW_PROJECT_TOKEN=fwp_project0123" in (root / ".env").read_text(encoding="utf-8")
