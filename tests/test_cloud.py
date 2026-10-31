from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pandas as pd
import pytest

import framework as fw
from framework import _wincred, _yaml, cloud, dotenv
from framework.config_home import r_config_dir

Handler = Callable[[httpx.Request], httpx.Response]


class FakeCloud:
    """A Framework Cloud API that records requests."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.routes: dict[tuple[str, str], Handler] = {}
        self.settings: dict[str, Any] = {"revision": 3, "document": {"author": {"name": "Ada"}}}
        self.ledger: list[dict[str, Any]] = []
        self.route(
            "GET", "/api/v1/me", lambda r: httpx.Response(200, json={"email": "ada@example.org"})
        )
        self.route("GET", "/api/v1/settings", lambda r: httpx.Response(200, json=self.settings))
        self.route("PUT", "/api/v1/settings", self._put_settings)
        self.route("GET", "/api/v1/ledger", self._get_ledger)
        self.route("POST", "/api/v1/ledger", self._post_ledger)

    def route(self, method: str, path: str, handler: Handler) -> None:
        self.routes[(method, path)] = handler

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        handler = self.routes.get((request.method, request.url.path))
        if handler is None:
            return httpx.Response(404, json={"message": f"No route {request.url.path}"})
        return handler(request)

    def _put_settings(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body["base_revision"] != self.settings["revision"]:
            return httpx.Response(409, json={"message": "Revision conflict"})
        self.settings = {"revision": self.settings["revision"] + 1, "document": body["document"]}
        return httpx.Response(200, json={"revision": self.settings["revision"]})

    def _get_ledger(self, request: httpx.Request) -> httpx.Response:
        name = request.url.params.get("name")
        entries = [e for e in reversed(self.ledger) if name is None or e["name"] == name]
        return httpx.Response(200, json={"entries": entries})

    def _post_ledger(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        entry = {**body, "sequence": len(self.ledger) + 1}
        self.ledger.append(entry)
        return httpx.Response(201, json=entry)

    def paths(self) -> list[str]:
        return [f"{r.method} {r.url.path}" for r in self.requests]


@pytest.fixture
def server(monkeypatch: pytest.MonkeyPatch) -> FakeCloud:
    fake = FakeCloud()
    monkeypatch.setattr(cloud, "_transport", httpx.MockTransport(fake))
    return fake


@pytest.fixture
def keyring(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    store: dict[str, str] = {}
    monkeypatch.setattr(cloud, "_keyring_get", store.get)
    monkeypatch.setattr(
        cloud, "_keyring_set", lambda service, token: store.update({service: token}) or True
    )
    monkeypatch.setattr(
        cloud, "_keyring_delete", lambda service: store.pop(service, None) is not None
    )
    del os.environ["FW_TOKEN_FILE"]
    return store


# Servers and tokens -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("framework.example.edu", "https://framework.example.edu"),
        ("https://framework.pub/", "https://framework.pub"),
        ("http://localhost:8080", "http://localhost:8080"),
        ("http://127.0.0.1", "http://127.0.0.1"),
        ("http://[::1]:9000", "http://[::1]:9000"),
    ],
)
def test_normalize_url(given: str, expected: str) -> None:
    assert cloud.normalize_url(given) == expected


@pytest.mark.parametrize(
    ("given", "message"),
    [
        ("http://framework.example.edu", "Refusing plain http"),
        ("ftp://x.org", "must start with https"),
    ],
)
def test_normalize_url_refuses(given: str, message: str) -> None:
    with pytest.raises(fw.CloudError, match=message):
        cloud.normalize_url(given)


def test_hosts_and_token_names() -> None:
    assert (
        cloud.host("https://user@Framework.Example.edu:8443/path") == "framework.example.edu:8443"
    )
    assert cloud.hostname("https://framework.example.edu:8443") == "framework.example.edu"
    assert cloud.keyring_service("http://localhost:8080") == "framework-cloud:localhost:8080"
    assert cloud.token_file_name("https://framework.pub") == "cloud-token"
    assert cloud.token_file_name("http://localhost:8080") == "cloud-token-localhost_8080"


def test_server_resolution(project: Path) -> None:
    assert cloud.cloud_server() == cloud.Server("https://framework.pub", "the built-in default")
    fw.config_dir().mkdir(parents=True)
    _yaml.write(fw.config_dir() / "cloud.yml", {"server": "https://saved.example.org"})
    assert cloud.cloud_server().source == "your cloud_login() default"
    assert cloud.cloud_server(scope="project").url == "https://framework.pub"

    os.environ["FW_CLOUD_URL"] = "https://env.example.org"
    assert cloud.cloud_server().url == "https://env.example.org"
    (project / ".env").write_text("FW_CLOUD_URL=https://project.example.org\n", encoding="utf-8")
    assert cloud.cloud_server().url == "https://env.example.org"
    assert cloud.cloud_server(scope="project").url == "https://project.example.org"
    assert cloud.cloud_server("arg.example.org", scope="project").url == "https://arg.example.org"


def test_saved_server_falls_back_to_r(project: Path) -> None:
    r_config_dir().mkdir(parents=True)
    _yaml.write(r_config_dir() / "cloud.yml", {"server": "https://from-r.example.org"})
    assert cloud.cloud_server().url == "https://from-r.example.org"


def test_token_resolution(project: Path, keyring: dict[str, str]) -> None:
    url = "https://framework.pub"
    assert cloud.cloud_token(url) is None
    r_config_dir().mkdir(parents=True)
    (r_config_dir() / "cloud-token").write_text("fw_from_r_file\n", encoding="utf-8")
    assert cloud.cloud_token(url) == "fw_from_r_file"
    fw.config_dir().mkdir(parents=True)
    (fw.config_dir() / "cloud-token").write_text("fw_python_file\n", encoding="utf-8")
    assert cloud.cloud_token(url) == "fw_python_file"
    keyring["framework.pub"] = "fw_legacy_keyring"
    assert cloud.cloud_token(url) == "fw_legacy_keyring"
    keyring["framework-cloud:framework.pub"] = "fw_keyring"
    assert cloud.cloud_token(url) == "fw_keyring"
    os.environ["FW_TOKEN_FILE"] = str(project / "explicit")
    assert cloud.cloud_token(url) is None  # authoritative, even when empty
    os.environ["FW_CLOUD_TOKEN"] = "fw_env"
    assert cloud.cloud_token(url) == "fw_env"


def test_windows_blob_decoding() -> None:
    assert _wincred.target("framework-cloud:localhost:8080", "token") == (
        ":framework-cloud:localhost:8080:token"
    )
    assert _wincred.decode(b"fw_abc") == "fw_abc"
    assert _wincred.decode("fw_abc".encode("utf-16-le")) == "fw_abc"


# Login, logout, and status -------------------------------------------------------------


def test_login_needs_consent(project: Path, server: FakeCloud, keyring: dict[str, str]) -> None:
    with pytest.raises(fw.CloudError, match="needs confirmation"):
        fw.cloud_login("fw_0123456789")
    assert keyring == {}


def test_login_stores_token_and_default(
    project: Path, server: FakeCloud, keyring: dict[str, str]
) -> None:
    stored = fw.cloud_login("fw_0123456789", url="https://cloud.example.edu", consent=True)
    assert stored == "keyring"
    assert keyring == {"framework-cloud:cloud.example.edu": "fw_0123456789"}
    assert _yaml.read(fw.config_dir() / "cloud.yml") == {"server": "https://cloud.example.edu"}
    assert server.paths() == ["GET /api/v1/me"]
    assert server.requests[0].headers["Authorization"] == "Bearer fw_0123456789"
    assert server.requests[0].headers["User-Agent"].startswith("framework-py/")


def test_login_without_a_keyring_writes_a_private_file(
    project: Path, server: FakeCloud, keyring: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cloud, "_keyring_set", lambda service, token: False)
    stored = fw.cloud_login(
        "fw_0123456789", url="http://localhost:8080", verify=False, consent=True
    )
    path = fw.config_dir() / "cloud-token-localhost_8080"
    assert stored == str(path)
    assert path.read_text(encoding="utf-8") == "fw_0123456789\n"
    if sys.platform != "win32":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert server.requests == []


def test_login_with_token_file(project: Path, server: FakeCloud) -> None:
    target = project / "secrets" / "token"
    os.environ["FW_TOKEN_FILE"] = str(target)
    assert fw.cloud_login("fw_0123456789", consent=True) == str(target)
    assert cloud.cloud_token("https://framework.pub") == "fw_0123456789"


def test_bad_token_is_reported(project: Path, server: FakeCloud, keyring: dict[str, str]) -> None:
    server.route(
        "GET", "/api/v1/me", lambda r: httpx.Response(401, json={"message": "Unauthenticated."})
    )
    with pytest.raises(fw.CloudError, match="Unauthenticated"):
        fw.cloud_login("fw_0123456789", consent=True)
    assert keyring == {}


def test_logout(project: Path, keyring: dict[str, str]) -> None:
    keyring["framework-cloud:framework.pub"] = "a"
    keyring["framework.pub"] = "b"
    fw.config_dir().mkdir(parents=True)
    (fw.config_dir() / "cloud-token").write_text("c\n", encoding="utf-8")
    removed = fw.cloud_logout()
    assert len(removed) == 3 and keyring == {}
    assert fw.cloud_logout() == []


def test_status(project: Path, server: FakeCloud, keyring: dict[str, str]) -> None:
    assert fw.cloud_status() is False
    keyring["framework-cloud:framework.pub"] = "fw_0123456789"
    assert fw.cloud_status() is True
    server.route("GET", "/api/v1/me", lambda r: httpx.Response(500))
    assert fw.cloud_status() is False


# Settings sync ----------------------------------------------------------------------------


def test_sync_pull_push_and_conflicts(project: Path, server: FakeCloud) -> None:
    os.environ["FW_CLOUD_TOKEN"] = "fw_0123456789"
    mirror = fw.config_dir() / "cloud-settings.yml"
    assert fw.cloud_sync() == 3
    assert _yaml.read(mirror) == {"author": {"name": "Ada"}}
    assert fw.cloud_sync() == 3  # already in sync

    _yaml.write(mirror, {"author": {"name": "Ada Lovelace"}})
    assert fw.cloud_sync() == 4
    assert server.settings["document"] == {"author": {"name": "Ada Lovelace"}}
    put = json.loads(server.requests[-1].content)
    assert put["client"]["app"] == "framework-py"

    server.settings = {"revision": 9, "document": {"author": {"name": "Remote"}}}
    _yaml.write(mirror, {"author": {"name": "Local"}})
    with pytest.raises(fw.CloudError, match="Both your local mirror and the cloud changed"):
        fw.cloud_sync()
    assert fw.cloud_sync("pull") == 9
    assert len(list(fw.config_dir().glob("cloud-settings.yml.backup-*"))) == 1
    assert fw.cloud_settings() == server.settings


def test_sync_needs_a_token(project: Path, server: FakeCloud) -> None:
    with pytest.raises(fw.CloudError, match=r"No cloud token found for framework\.pub"):
        fw.cloud_sync()


# The ledger -----------------------------------------------------------------------------------


@pytest.fixture
def cloud_project(project: Path, server: FakeCloud) -> Path:
    (project / ".env").write_text(
        "FW_PROJECT_TOKEN=fwp_project0123\nFW_CLOUD_URL=https://cloud.example.edu\n",
        encoding="utf-8",
    )
    (project / "inputs").mkdir()
    return project


def test_ledger_push_and_list(cloud_project: Path, server: FakeCloud) -> None:
    frame = pd.DataFrame({"x": [1]})
    fw.data_save(frame, "inputs/a.csv")
    fw.data_save(frame.assign(x=2), "inputs/b.csv")
    assert server.ledger == []  # live mode is off by default

    assert fw.ledger_push() == {"pushed": 2, "up_to_date": 0}
    assert fw.ledger_push() == {"pushed": 0, "up_to_date": 2}
    assert {r.url.host for r in server.requests} == {"cloud.example.edu"}
    assert server.ledger[0]["client"]["source"] == "ledger_push"
    assert server.ledger[0]["recorded_at"].endswith("Z")
    listed = fw.ledger_list("inputs/a.csv")
    assert listed["name"].tolist() == ["inputs/a.csv"]


def test_live_ledger_on_save(cloud_project: Path, server: FakeCloud) -> None:
    os.environ["FW_LEDGER"] = "live"
    fw.data_save(pd.DataFrame({"x": [1]}), "inputs/a.csv")
    assert [e["name"] for e in server.ledger] == ["inputs/a.csv"]
    assert server.ledger[0]["size_bytes"] > 0


def test_live_ledger_failure_never_fails_the_save(cloud_project: Path, server: FakeCloud) -> None:
    os.environ["FW_LEDGER"] = "on"
    server.route("POST", "/api/v1/ledger", lambda r: httpx.Response(503))
    fw.data_save(pd.DataFrame({"x": [1]}), "inputs/a.csv")
    assert (cloud_project / "inputs/a.csv").is_file()


def test_data_verify(cloud_project: Path, server: FakeCloud) -> None:
    (cloud_project / "settings.yml").write_text(
        "default:\n  data:\n    a: inputs/a.csv\n", encoding="utf-8"
    )
    (cloud_project / "inputs/a.csv").write_text("x\n1\n", encoding="utf-8")
    fw.data_read("a")
    assert fw.data_verify("a") is True
    fw.ledger_push()
    assert fw.data_verify("a") is True
    (cloud_project / "inputs/a.csv").write_text("x\n2\n", encoding="utf-8")
    with pytest.warns(fw.FrameworkWarning, match="Integrity mismatch for 'a'"):
        assert fw.data_verify("a") is False


def test_ledger_needs_a_project_token(project: Path) -> None:
    with pytest.raises(fw.CloudError, match="No project token found"):
        fw.ledger_push()


# Publishing ------------------------------------------------------------------------------------


def test_cloud_publish_html_and_bundle(cloud_project: Path, server: FakeCloud) -> None:
    received: list[dict[str, Any]] = []

    def documents(request: httpx.Request) -> httpx.Response:
        body = request.content
        received.append({"type": request.headers["Content-Type"], "body": body})
        return httpx.Response(201, json={"url": "https://cloud.example.edu/d/report", "version": 2})

    server.route("POST", "/api/v1/documents", documents)
    report = cloud_project / "outputs" / "report.html"
    report.parent.mkdir()
    report.write_text("<p>hi</p>", encoding="utf-8")
    assert fw.publish(report, slug="main-results") == "https://cloud.example.edu/d/report"
    first = received[0]["body"]
    assert b'name="slug"\r\n\r\nmain-results' in first
    assert b'name="readable"\r\n\r\n1' in first
    assert b'filename="report.html"' in first

    (cloud_project / "outputs" / "report_files").mkdir()
    (cloud_project / "outputs" / "report_files" / "style.css").write_text("p{}", encoding="utf-8")
    fw.publish(report, slug=False)
    second = received[1]["body"]
    assert b'name="entrypoint"\r\n\r\nreport.html' in second
    assert b'name="readable"\r\n\r\n0' in second
    assert b'filename="bundle.zip"' in second
    assert server.requests[-1].headers["Authorization"] == "Bearer fwp_project0123"


def test_cloud_publish_renders_a_jupyter_notebook(
    cloud_project: Path, server: FakeCloud, monkeypatch: pytest.MonkeyPatch
) -> None:
    def render(command: list[str], **kwargs: Any) -> Any:
        out = Path(command[command.index("--output-dir") + 1])
        out.mkdir(parents=True)
        (out / "analysis.html").write_text("<p>rendered</p>", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(cloud.shutil, "which", lambda name: "/usr/bin/quarto")
    monkeypatch.setattr(cloud.subprocess, "run", render)
    server.route(
        "POST",
        "/api/v1/documents",
        lambda r: httpx.Response(201, json={"url": "https://cloud.example.edu/d/k", "version": 1}),
    )
    notebook = cloud_project / "analysis.ipynb"
    notebook.write_text("{}", encoding="utf-8")
    assert cloud.cloud_publish(notebook) == "https://cloud.example.edu/d/k"
    assert b"<p>rendered</p>" in server.requests[-1].content


def test_cloud_publish_errors(cloud_project: Path, server: FakeCloud) -> None:
    server.route(
        "POST", "/api/v1/documents", lambda r: httpx.Response(413, json={"message": "Too big"})
    )
    report = cloud_project / "report.html"
    report.write_text("<p>hi</p>", encoding="utf-8")
    with pytest.raises(fw.CloudError, match="Too big"):
        fw.publish(report)


def test_cloud_publish_asks_about_private_paths(cloud_project: Path, server: FakeCloud) -> None:
    server.route(
        "POST", "/api/v1/documents", lambda r: httpx.Response(200, json={"url": "https://x/d/1"})
    )
    report = cloud_project / "private" / "report.html"
    report.parent.mkdir()
    report.write_text("<p>hi</p>", encoding="utf-8")
    with pytest.raises(fw.FrameworkError, match="include_private=True"):
        fw.publish(report)
    assert fw.publish(report, include_private=True) == "https://x/d/1"


# Projects ------------------------------------------------------------------------------------


def envelope(language: str = "python") -> dict[str, Any]:
    return {
        "spec_version": 1,
        "name": "Cloud Study",
        "layers": [
            {"kind": "base", "key": "project", "layer": {"type": "project"}},
            {"kind": "project", "key": "cloud-study", "layer": {"language": language}},
        ],
        "source": {"origin": "cloud", "blueprint": {"key": "r01", "version": 3}},
    }


def test_new_from_a_cloud_key(tmp_path: Path, server: FakeCloud) -> None:
    server.route("GET", "/api/v2/projects/pull", lambda r: httpx.Response(200, json=envelope()))
    root = fw.new(key="fwp_project0123", location=tmp_path / "cloud-study", interactive=False)
    assert server.requests[0].headers["Authorization"] == "Bearer fwp_project0123"
    env = dotenv.read(root / ".env")
    assert env["FW_PROJECT_TOKEN"] == "fwp_project0123"
    assert env["FW_CLOUD_URL"] == "https://framework.pub"
    spec = json.loads((root / ".framework/spec.json").read_text(encoding="utf-8"))
    assert spec["source"]["blueprint"] == {"key": "r01", "version": 3}
    assert spec["type"] == "project"


def test_new_from_a_cloud_key_refuses_r_projects(tmp_path: Path, server: FakeCloud) -> None:
    server.route("GET", "/api/v2/projects/pull", lambda r: httpx.Response(200, json=envelope("r")))
    with pytest.raises(fw.SpecError, match="This is an R project"):
        fw.new(key="fwp_project0123", location=tmp_path / "x", interactive=False)


def test_new_registers_when_logged_in(tmp_path: Path, server: FakeCloud) -> None:
    os.environ["FW_CLOUD_TOKEN"] = "fw_user0123456"

    def create(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body == {"name": "Registered", "project_type": "project", "language": "python"}
        return httpx.Response(
            201, json={"project": {"slug": "registered"}, "pull_token": "fwp_new0123456"}
        )

    server.route("POST", "/api/v1/projects", create)
    root = fw.new("Registered", tmp_path / "registered", "project", interactive=False)
    assert dotenv.read(root / ".env")["FW_PROJECT_TOKEN"] == "fwp_new0123456"


def test_new_registration_failure_is_quiet(tmp_path: Path, server: FakeCloud) -> None:
    os.environ["FW_CLOUD_TOKEN"] = "fw_user0123456"
    server.route(
        "POST", "/api/v1/projects", lambda r: httpx.Response(422, json={"message": "Name taken"})
    )
    root = fw.new("Taken", tmp_path / "taken", "project", interactive=False)
    assert "FW_PROJECT_TOKEN" not in dotenv.read(root / ".env")
