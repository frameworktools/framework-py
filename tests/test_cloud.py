from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import time
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


def test_saved_server_comes_from_the_newest_login(project: Path) -> None:
    fw_file, r_file = fw.config_dir() / "cloud.yml", r_config_dir() / "cloud.yml"
    fw.config_dir().mkdir(parents=True, exist_ok=True)
    r_config_dir().mkdir(parents=True, exist_ok=True)
    _yaml.write(fw_file, {"server": "https://fw.example.org"})
    _yaml.write(r_file, {"server": "https://from-r.example.org"})
    now = time.time()

    # R logged in last
    os.utime(fw_file, (now - 3600, now - 3600))
    os.utime(r_file, (now, now))
    assert cloud.cloud_server().url == "https://from-r.example.org"

    # fw or Python logged in last
    os.utime(fw_file, (now + 60, now + 60))
    assert cloud.cloud_server().url == "https://fw.example.org"

    # A tie goes to fw's folder
    os.utime(r_file, (now + 60, now + 60))
    assert cloud.cloud_server().url == "https://fw.example.org"


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


def test_logout(project: Path, server: FakeCloud, keyring: dict[str, str]) -> None:
    server.route("DELETE", "/api/v1/token", lambda r: httpx.Response(204))
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


def test_status_and_settings_give_the_same_login_advice(
    project: Path, server: FakeCloud, caplog: pytest.LogCaptureFixture
) -> None:
    from framework.cloud import login_hint

    caplog.set_level("INFO", logger="framework")
    assert fw.cloud_status() is False
    with pytest.raises(fw.CloudError) as raised:
        fw.cloud_settings()
    hint = login_hint("https://framework.pub")
    assert hint.startswith("Run fw.cloud_login() to approve this computer in your browser")
    assert hint in caplog.text and hint in str(raised.value)
    other = login_hint("https://cloud.example.edu")
    assert 'fw.cloud_login(url="https://cloud.example.edu")' in other
    assert 'fw.cloud_login("fw_...", url="https://cloud.example.edu")' in other


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
    assert b'name="readable"' not in first
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
    server.route("GET", "/api/v1/projects/pull", lambda r: httpx.Response(200, json=envelope()))
    root = fw.new(key="fwp_project0123", location=tmp_path / "cloud-study", interactive=False)
    assert server.requests[0].headers["Authorization"] == "Bearer fwp_project0123"
    env = dotenv.read(root / ".env")
    assert env["FW_PROJECT_TOKEN"] == "fwp_project0123"
    assert env["FW_CLOUD_URL"] == "https://framework.pub"
    spec = json.loads((root / ".framework/spec.json").read_text(encoding="utf-8"))
    assert spec["source"]["blueprint"] == {"key": "r01", "version": 3}
    assert spec["type"] == "project"


def test_new_from_a_cloud_key_refuses_r_projects(tmp_path: Path, server: FakeCloud) -> None:
    server.route("GET", "/api/v1/projects/pull", lambda r: httpx.Response(200, json=envelope("r")))
    with pytest.raises(fw.SpecError, match="This is an R project"):
        fw.new(key="fwp_project0123", location=tmp_path / "x", interactive=False)


def choices_envelope() -> dict[str, Any]:
    return {
        "spec_version": 1,
        "name": "Lab",
        "layers": [
            {"kind": "base", "key": "framework/course", "layer": {"type": "course"}},
            {"kind": "facet", "key": "framework/quarto", "layer": {"tools": {"quarto": True}}},
            {
                "kind": "facet",
                "key": "framework/quarto_slides",
                "layer": {"tools": {"quarto_slides": True}},
            },
            {"kind": "project", "key": "lab", "layer": {"language": "python"}},
        ],
        "source": {"origin": "cloud"},
    }


def test_new_from_cloud_choices(tmp_path: Path, server: FakeCloud) -> None:
    os.environ["FW_CLOUD_TOKEN"] = "fw_user0123456"
    sent: dict[str, Any] = {}

    def resolve(request: httpx.Request) -> httpx.Response:
        sent["resolve"] = json.loads(request.content)
        return httpx.Response(200, json=choices_envelope())

    def create(request: httpx.Request) -> httpx.Response:
        sent["create"] = json.loads(request.content)
        return httpx.Response(201, json={"slug": "lab", "token": "fwp_lab0123456"})

    server.route("POST", "/api/v1/resolve", resolve)
    server.route("POST", "/api/v1/projects", create)
    root = fw.new(
        "Lab",
        tmp_path / "lab",
        structure="framework/course",
        tools=["quarto_slides"],
        skill_sets=["ada/teaching"],
        sensitive=False,
        interactive=False,
    )
    assert sent["resolve"] == {
        "name": "Lab",
        "language": "python",
        "structure": "framework/course",
        # The client adds what a tool requires
        "tools": {"quarto_slides": True, "quarto": True},
        "skill_sets": ["ada/teaching"],
        "settings": {"sensitive": False},
    }
    assert sent["create"] == sent["resolve"]
    config = _yaml.read(root / "settings.yml")["default"]
    assert config["project_type"] == "course"
    assert config["tools"]["quarto_slides"] is True
    assert dotenv.read(root / ".env")["FW_PROJECT_TOKEN"] == "fwp_lab0123456"


def test_new_from_cloud_choices_needs_a_login_and_reports_422(
    tmp_path: Path, server: FakeCloud
) -> None:
    with pytest.raises(fw.CloudError, match="No cloud token"):
        fw.new("Lab", tmp_path / "lab", blueprint="ada/lab", interactive=False)
    os.environ["FW_CLOUD_TOKEN"] = "fw_user0123456"
    server.route(
        "POST",
        "/api/v1/resolve",
        lambda r: httpx.Response(
            422,
            json={"message": "The given data was invalid.", "errors": {"blueprint": ["Unknown."]}},
        ),
    )
    with pytest.raises(fw.CloudError, match=r"blueprint: Unknown\."):
        fw.new("Lab", tmp_path / "lab", blueprint="ada/nope", interactive=False)
    assert not (tmp_path / "lab").exists()


def test_cloud_only_choices_need_a_blueprint(tmp_path: Path) -> None:
    with pytest.raises(fw.FrameworkError, match="sensitive need a Framework Cloud blueprint"):
        fw.new("X", tmp_path / "x", "project", sensitive=True, interactive=False)
    with pytest.raises(fw.FrameworkError, match="not both"):
        fw.new("X", tmp_path / "x", "project", blueprint="framework/project", interactive=False)


def test_new_resolves_your_defaults_and_caches_them(
    tmp_path: Path, server: FakeCloud, caplog: pytest.LogCaptureFixture
) -> None:
    os.environ["FW_CLOUD_TOKEN"] = "fw_user0123456"
    sent: list[dict[str, Any]] = []

    def resolve(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        headers = [
            ("Framework-Defaults-Revision", "rev-7"),
            ("Framework-Notice", "Your default blueprint doesn't support Python."),
        ]
        return httpx.Response(200, json=choices_envelope(), headers=headers)

    server.route("POST", "/api/v1/resolve", resolve)
    server.route(
        "POST", "/api/v1/projects", lambda r: httpx.Response(201, json={"token": "fwp_d0123456"})
    )
    caplog.set_level("INFO", logger="framework")
    root = fw.new("Lab", tmp_path / "lab", interactive=False)
    assert sent == [{"name": "Lab", "language": "python"}]
    assert _yaml.read(root / "settings.yml")["default"]["project_type"] == "course"
    assert "Your default blueprint doesn't support Python." in caplog.text

    path = fw.config_dir() / "cache/defaults/framework.pub/python.json"
    cached = json.loads(path.read_text(encoding="utf-8"))
    assert cached["defaults_revision"] == "rev-7"
    assert cached["envelope"] == choices_envelope()
    assert cached["fetched_at"].endswith("Z")


def offline(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("Connection refused", request=request)


@pytest.mark.parametrize("failure", ["network", "503"])
def test_new_offline_uses_the_cached_defaults(
    tmp_path: Path, server: FakeCloud, caplog: pytest.LogCaptureFixture, failure: str
) -> None:
    from framework._defaults_cache import write_cached_defaults

    os.environ["FW_CLOUD_TOKEN"] = "fw_user0123456"
    write_cached_defaults(cloud.cloud_server().url, "python", choices_envelope(), "rev-7")
    server.route(
        "POST",
        "/api/v1/resolve",
        offline if failure == "network" else (lambda r: httpx.Response(503)),
    )
    caplog.set_level("INFO", logger="framework")
    root = fw.new("Lab", tmp_path / "lab", interactive=False)
    assert _yaml.read(root / "settings.yml")["default"]["project_type"] == "course"
    assert "Using your Framework Cloud defaults from 20" in caplog.text


def test_new_offline_without_a_cache_never_falls_back_silently(
    tmp_path: Path, server: FakeCloud, monkeypatch: pytest.MonkeyPatch
) -> None:
    os.environ["FW_CLOUD_TOKEN"] = "fw_user0123456"
    server.route("POST", "/api/v1/resolve", offline)
    with pytest.raises(fw.FrameworkError, match=r"Pass offline=True"):
        fw.new("Lab", tmp_path / "lab", interactive=False)
    assert not (tmp_path / "lab").exists()

    monkeypatch.setattr("builtins.input", lambda prompt: "n")
    with pytest.raises(fw.FrameworkError, match="Cancelled"):
        fw.new("Lab", tmp_path / "lab", interactive=True)

    answers = iter(["y", "bare"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))
    root = fw.new("Lab", tmp_path / "lab", interactive=True)
    assert _yaml.read(root / "settings.yml")["default"]["project_type"] == "bare"

    # offline=True skips the cloud
    requests = len(server.requests)
    fw.new("Offline", tmp_path / "offline", interactive=False, offline=True)
    assert len(server.requests) == requests


def test_new_reports_auth_errors_instead_of_going_offline(
    tmp_path: Path, server: FakeCloud
) -> None:
    from framework._defaults_cache import write_cached_defaults

    os.environ["FW_CLOUD_TOKEN"] = "fw_user0123456"
    write_cached_defaults(cloud.cloud_server().url, "python", choices_envelope(), "rev-7")
    server.route(
        "POST", "/api/v1/resolve", lambda r: httpx.Response(401, json={"message": "Bad token"})
    )
    with pytest.raises(fw.CloudError, match="Bad token"):
        fw.new("Lab", tmp_path / "lab", interactive=False)


def test_defaults_cache_folders_replace_colons() -> None:
    from framework._defaults_cache import cache_path

    path = cache_path("http://localhost:8000", "python")
    assert path == fw.config_dir() / "cache/defaults/localhost_8000/python.json"


def test_older_servers_ask_before_using_the_built_ins(
    tmp_path: Path, server: FakeCloud, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without /api/v1/resolve, Python builds from the built-ins only after saying so."""
    os.environ["FW_CLOUD_TOKEN"] = "fw_user0123456"
    with pytest.raises(fw.FrameworkError, match="doesn't resolve projects yet"):
        fw.new("Plain", tmp_path / "plain", "project", interactive=False)

    def create(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        # An older server registers by name and type only; it doesn't know resolved choices
        if "project_type" not in body:
            return httpx.Response(404, json={"message": "Not found"})
        assert body == {"name": "Registered", "project_type": "project", "language": "python"}
        return httpx.Response(
            201, json={"project": {"slug": "registered"}, "pull_token": "fwp_new0123456"}
        )

    server.route("POST", "/api/v1/projects", create)
    monkeypatch.setattr("builtins.input", lambda prompt: "y")
    root = fw.new("Registered", tmp_path / "registered", "project", interactive=True)
    assert dotenv.read(root / ".env")["FW_PROJECT_TOKEN"] == "fwp_new0123456"

    # A failed registration is quiet
    server.route(
        "POST", "/api/v1/projects", lambda r: httpx.Response(422, json={"message": "Name taken"})
    )
    root = fw.new("Taken", tmp_path / "taken", "project", interactive=True)
    assert "FW_PROJECT_TOKEN" not in dotenv.read(root / ".env")

    # offline=True neither asks nor contacts the cloud
    requests = len(server.requests)
    fw.new("Away", tmp_path / "away", "project", interactive=False, offline=True)
    assert len(server.requests) == requests


def test_a_type_resolves_as_its_system_blueprint(tmp_path: Path, server: FakeCloud) -> None:
    os.environ["FW_CLOUD_TOKEN"] = "fw_user0123456"
    sent: dict[str, Any] = {}

    def resolve(request: httpx.Request) -> httpx.Response:
        sent["resolve"] = json.loads(request.content)
        return httpx.Response(200, json=choices_envelope())

    def create(request: httpx.Request) -> httpx.Response:
        sent["create"] = json.loads(request.content)
        return httpx.Response(201, json={"slug": "pieces", "token": "fwp_pieces0123"})

    server.route("POST", "/api/v1/resolve", resolve)
    server.route("POST", "/api/v1/projects", create)
    root = fw.new(
        "Pieces",
        tmp_path / "pieces",
        "project_sensitive",
        workflow="scripts",
        tools=["jupyter"],
        skill_sets=["framework"],
        interactive=False,
    )
    assert sent["resolve"] == {
        "name": "Pieces",
        "language": "python",
        "blueprint": "framework/project_sensitive",
        "workflow": "scripts",
        "tools": {"jupyter": True},
        "skill_sets": ["framework/framework"],
    }
    assert sent["create"] == sent["resolve"]
    assert dotenv.read(root / ".env")["FW_PROJECT_TOKEN"] == "fwp_pieces0123"
    # Explicit choices aren't your defaults, so nothing is cached
    assert not (fw.config_dir() / "cache").exists()


def test_explicit_choices_offline_ask_first(tmp_path: Path, server: FakeCloud) -> None:
    from framework._defaults_cache import write_cached_defaults

    os.environ["FW_CLOUD_TOKEN"] = "fw_user0123456"
    write_cached_defaults(cloud.cloud_server().url, "python", choices_envelope(), "rev-7")
    server.route("POST", "/api/v1/resolve", offline)
    with pytest.raises(fw.FrameworkError, match="these choices need Framework Cloud"):
        fw.new("Lab", tmp_path / "lab", "project", interactive=False)


@pytest.mark.parametrize(
    "name,mime",
    [
        ("plot.png", "image/png"),
        ("table.csv", "text/csv"),
        ("model.bin", "application/octet-stream"),
    ],
)
def test_cloud_file_artifact(cloud_project: Path, server: FakeCloud, name: str, mime: str) -> None:
    server.route(
        "POST",
        "/api/v1/documents",
        lambda r: httpx.Response(201, json={"url": "https://x/d/artifact", "version": 1}),
    )
    path = cloud_project / name
    path.write_bytes(b"original bytes")
    assert (
        fw.publish(
            path,
            to="cloud",
            title="Result",
            description="An estimate",
            password="test-password",
            kind="file",
        )
        == "https://x/d/artifact"
    )
    body = server.requests[-1].content
    assert f'filename="{name}"'.encode() in body
    assert f"Content-Type: {mime}".encode() in body
    assert b"original bytes" in body
    assert b'name="password"\r\n\r\ntest-password' in body


@pytest.mark.parametrize("attach_ok", [True, False])
def test_publish_collection_attachment(
    cloud_project: Path, server: FakeCloud, attach_ok: bool
) -> None:
    server.route(
        "GET",
        "/api/v1/collections",
        lambda r: httpx.Response(
            200,
            json={
                "collections": [
                    {"id": 7, "slug": "findings", "title": "Findings", "visibility": "unlisted"}
                ]
            },
        ),
    )
    server.route(
        "POST",
        "/api/v1/documents",
        lambda r: httpx.Response(201, json={"id": 14, "url": "https://x/d/artifact", "version": 2}),
    )
    attached = []

    def attach(request: httpx.Request) -> httpx.Response:
        attached.append(json.loads(request.content))
        return httpx.Response(201 if attach_ok else 422, json={"message": "Attachment denied"})

    server.route("POST", "/api/v1/collections/7/items", attach)
    path = cloud_project / "figure.png"
    path.write_bytes(b"png")
    kwargs = {"to": "cloud", "slug": False, "collection": "findings", "share_unlisted": True}
    if attach_ok:
        assert fw.publish(path, **kwargs) == "https://x/d/artifact"
    else:
        with pytest.warns(fw.FrameworkWarning, match="Artifact published at https://x/d/artifact"):
            assert fw.publish(path, **kwargs) == "https://x/d/artifact"
    assert attached == [{"document_id": 14, "share_unlisted": True}]
    assert sum(r.url.path == "/api/v1/documents" for r in server.requests) == 1


@pytest.mark.parametrize(
    "collection,rows,visibility,share",
    [
        ("missing", [], None, False),
        (
            "7",
            [
                {"id": 7, "slug": "a", "visibility": "private"},
                {"id": 8, "slug": "7", "visibility": "private"},
            ],
            None,
            False,
        ),
        ("findings", [{"id": 7, "slug": "findings", "visibility": "public"}], "unlisted", True),
        ("findings", [{"id": 7, "slug": "findings", "visibility": "unlisted"}], "unlisted", False),
    ],
)
def test_collection_preflight_before_upload(
    cloud_project: Path,
    server: FakeCloud,
    collection: str,
    rows: list[dict[str, Any]],
    visibility: str | None,
    share: bool,
) -> None:
    server.route(
        "GET", "/api/v1/collections", lambda r: httpx.Response(200, json={"collections": rows})
    )
    path = cloud_project / "figure.png"
    path.write_bytes(b"png")
    with pytest.raises(fw.CloudError):
        fw.publish(
            path, to="cloud", collection=collection, visibility=visibility, share_unlisted=share
        )
    assert all(r.method == "GET" for r in server.requests)


def test_cloud_dataframe_uses_structured_schema(cloud_project: Path, server: FakeCloud) -> None:
    import pandas as pd

    server.route(
        "POST",
        "/api/v1/documents",
        lambda r: httpx.Response(201, json={"id": 14, "url": "https://x/d/table", "version": 1}),
    )
    assert fw.publish(pd.DataFrame({"estimate": [1.2]}), to="cloud") == "https://x/d/table"
    body = server.requests[-1].content
    assert b'filename="table.json"' in body
    assert b'name="slug"\r\n\r\ntable' in body
    assert b'name="kind"\r\n\r\ntable' in body
    assert b'"schema": "framework.artifact/v1"' in body


# Browser login -------------------------------------------------------------------------


def _device_routes(server: FakeCloud, replies: list[int]) -> None:
    server.route(
        "POST",
        "/api/v1/cli/device",
        lambda r: httpx.Response(
            200,
            json={
                "device_code": "dev-123",
                "user_code": "ABCD-EFGH",
                "verification_url": "https://cloud.example.edu/device",
                "verification_url_complete": "https://cloud.example.edu/device?code=ABCD-EFGH",
                "interval": 2,
                "expires_in": 10,
            },
        ),
    )
    pending = list(replies)

    def poll(request: httpx.Request) -> httpx.Response:
        assert json.loads(request.content) == {"device_code": "dev-123"}
        assert "Authorization" not in request.headers
        status = pending.pop(0) if pending else 200
        if status == 200:
            return httpx.Response(200, json={"token": "fw_browser_0123456789"})
        return httpx.Response(status, json={"error": "pending"})

    server.route("POST", "/api/v1/cli/device/token", poll)


def test_browser_login_stores_the_approved_token(
    project: Path, server: FakeCloud, keyring: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    opened: list[str] = []
    waits: list[int] = []
    monkeypatch.setattr(cloud, "_sleep", waits.append)
    monkeypatch.setattr(cloud.webbrowser, "open", opened.append)
    _device_routes(server, [428, 429])
    stored = fw.cloud_login(url="https://cloud.example.edu")
    assert stored == "keyring"
    assert keyring == {"framework-cloud:cloud.example.edu": "fw_browser_0123456789"}
    assert opened == ["https://cloud.example.edu/device?code=ABCD-EFGH"]
    assert waits == [2, 2, 7]
    assert server.paths() == [
        "POST /api/v1/cli/device",
        "POST /api/v1/cli/device/token",
        "POST /api/v1/cli/device/token",
        "POST /api/v1/cli/device/token",
        "GET /api/v1/me",
    ]
    assert json.loads(server.requests[0].content)["client_name"].startswith("Python on ")


def test_browser_login_without_opening_a_browser(
    project: Path, server: FakeCloud, keyring: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cloud, "_sleep", lambda seconds: None)
    monkeypatch.setattr(cloud.webbrowser, "open", lambda url: pytest.fail("opened a browser"))
    _device_routes(server, [])
    assert fw.cloud_login(url="https://cloud.example.edu", browser=False) == "keyring"


@pytest.mark.parametrize(
    ("status", "message"), [(403, "denied in the browser"), (410, "code expired")]
)
def test_browser_login_reports_denial_and_expiry(
    project: Path,
    server: FakeCloud,
    keyring: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    status: int,
    message: str,
) -> None:
    monkeypatch.setattr(cloud, "_sleep", lambda seconds: None)
    monkeypatch.setattr(cloud.webbrowser, "open", lambda url: None)
    _device_routes(server, [status])
    with pytest.raises(fw.CloudError, match=message):
        fw.cloud_login(url="https://cloud.example.edu")
    assert keyring == {}


def test_browser_login_on_a_server_without_it(
    project: Path, server: FakeCloud, keyring: dict[str, str]
) -> None:
    with pytest.raises(fw.CloudError, match="does not offer browser login"):
        fw.cloud_login(url="https://cloud.example.edu")


# Audit 2026-11-05 regressions ------------------------------------------------------------


def _fields(body: bytes) -> dict[str, str]:
    """The plain multipart fields of a request body."""
    import re

    return {
        match[0]: match[1]
        for match in re.findall(rb'name="([^"]+)"\r\n\r\n([^\r]*)\r\n', body)
        if b"filename" not in match[0]
    }  # type: ignore[misc]


@pytest.mark.parametrize(
    ("slug", "visibility", "readable"),
    [
        ("x", "private", None),
        ("x", "unlisted", None),
        (False, "private", None),
        (True, "public", None),
        ("x", None, None),
        (True, None, b"1"),
        (False, None, b"0"),
    ],
)
def test_explicit_visibility_wins_over_the_slug(
    cloud_project: Path,
    server: FakeCloud,
    slug: str | bool,
    visibility: str | None,
    readable: bytes | None,
) -> None:
    # A string slug sent readable=1, which the server turned into public.
    # Now only slug=True or False sends the switch.
    server.route(
        "POST", "/api/v1/documents", lambda r: httpx.Response(201, json={"url": "https://x/d/1"})
    )
    report = cloud_project / "report.html"
    report.write_text("<p>hi</p>", encoding="utf-8")
    fw.publish(report, to="cloud", slug=slug, visibility=visibility)
    fields = _fields(server.requests[-1].content)
    assert fields.get(b"readable") == readable
    assert fields.get(b"visibility") == (visibility.encode() if visibility else None)


def test_hidden_visibility_with_a_slug_fails_a_public_collection_first(
    cloud_project: Path, server: FakeCloud
) -> None:
    rows = [{"id": 7, "slug": "findings", "title": "F", "visibility": "public"}]
    server.route(
        "GET", "/api/v1/collections", lambda r: httpx.Response(200, json={"collections": rows})
    )
    path = cloud_project / "figure.png"
    path.write_bytes(b"png")
    with pytest.raises(fw.CloudError, match="Public collections require public artifacts"):
        fw.publish(path, to="cloud", slug="fig", visibility="private", collection="findings")
    assert all(r.method == "GET" for r in server.requests)


def test_logout_removes_the_fw_token_file(project: Path, server: FakeCloud) -> None:
    # cloud_login() wrote FW_TOKEN_FILE, but cloud_logout() never removed it
    fw.cloud_login("fw_0123456789", consent=True)
    token_file = Path(os.environ["FW_TOKEN_FILE"])
    assert token_file.is_file() and fw.cloud_status() is True
    assert str(token_file) in fw.cloud_logout()
    assert not token_file.exists()
    assert fw.cloud_status() is False


def test_live_ledger_time_is_utc(cloud_project: Path, server: FakeCloud) -> None:
    # The live push sent local time with an offset, which the server stored as UTC
    from datetime import UTC, datetime, timedelta

    os.environ["FW_LEDGER"] = "live"
    fw.data_save(pd.DataFrame({"x": [1]}), "inputs/a.csv")
    recorded = server.ledger[0]["recorded_at"]
    assert recorded.endswith("Z")
    when = datetime.strptime(recorded, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    assert abs(datetime.now(UTC) - when) < timedelta(minutes=1)


def test_live_ledger_can_be_turned_on_in_the_project_env(
    cloud_project: Path, server: FakeCloud
) -> None:
    with (cloud_project / ".env").open("a", encoding="utf-8") as env:
        env.write("FW_LEDGER=live\n")
    fw.settings_read()
    assert "FW_LEDGER" not in os.environ
    fw.data_save(pd.DataFrame({"x": [1]}), "inputs/a.csv")
    assert [e["name"] for e in server.ledger] == ["inputs/a.csv"]


def test_data_verify_by_path_finds_the_catalog_record(cloud_project: Path) -> None:
    # Given a path, the framework.db lookup missed the record and passed a tampered file
    import warnings

    os.environ.pop("FW_PROJECT_TOKEN", None)
    (cloud_project / ".env").write_text("", encoding="utf-8")
    (cloud_project / "settings.yml").write_text(
        "default:\n  data:\n    a: inputs/a.csv\n", encoding="utf-8"
    )
    (cloud_project / "inputs/a.csv").write_text("x\n1\n", encoding="utf-8")
    fw.data_read("a")
    assert fw.data_verify("inputs/a.csv") is True
    (cloud_project / "inputs/a.csv").write_text("x\n2\n", encoding="utf-8")
    with pytest.warns(fw.FrameworkWarning, match="Integrity mismatch"):
        assert fw.data_verify("inputs/a.csv") is False
    (cloud_project / "inputs/b.csv").write_text("x\n1\n", encoding="utf-8")
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert fw.data_verify("inputs/b.csv") is True


def test_data_verify_reports_a_missing_record(
    cloud_project: Path, caplog: pytest.LogCaptureFixture
) -> None:
    (cloud_project / ".env").write_text("", encoding="utf-8")
    (cloud_project / "inputs/b.csv").write_text("x\n1\n", encoding="utf-8")
    caplog.set_level("INFO", logger="framework")
    assert fw.data_verify("inputs/b.csv") is True
    assert "no framework.db record for this file yet" in caplog.text


def test_sync_conflict_advice_works(project: Path, server: FakeCloud) -> None:
    # The conflict message advised a push that the server always rejects
    os.environ["FW_CLOUD_TOKEN"] = "fw_0123456789"
    mirror = fw.config_dir() / "cloud-settings.yml"
    fw.cloud_sync()
    server.settings = {"revision": 9, "document": {"author": {"name": "Remote"}}}
    _yaml.write(mirror, {"author": {"name": "Local"}})
    with pytest.raises(fw.CloudError) as raised:
        fw.cloud_sync()
    assert 'cloud_sync("push") to overwrite' not in str(raised.value)
    assert fw.cloud_sync("pull") == 9
    _yaml.write(mirror, {"author": {"name": "Local"}})
    assert fw.cloud_sync("push") == 10


def test_logged_in_bare_projects_stay_local(tmp_path: Path, server: FakeCloud) -> None:
    # The guide says bare projects are not registered; logged in, they were
    os.environ["FW_CLOUD_TOKEN"] = "fw_user0123456"
    envelope = {
        "spec_version": 1,
        "name": "Plain",
        "layers": [
            {"kind": "base", "key": "framework/bare", "layer": {"type": "bare"}},
            {"kind": "project", "key": "plain", "layer": {"language": "python"}},
        ],
        "source": {"origin": "cloud"},
    }
    server.route("POST", "/api/v1/resolve", lambda r: httpx.Response(200, json=envelope))
    root = fw.new("Plain", tmp_path / "plain", "bare", interactive=False)
    assert (root / "settings.yml").is_file()
    assert "POST /api/v1/projects" not in server.paths()


def test_new_shows_its_first_messages(
    tmp_path: Path, server: FakeCloud, capsys: pytest.CaptureFixture[str]
) -> None:
    # Messages logged before project_create() turned output on were lost
    import logging

    from framework._defaults_cache import write_cached_defaults

    logger = logging.getLogger("framework")
    saved = (list(logger.handlers), logger.level)
    for handler in [h for h in logger.handlers if getattr(h, "_framework", False)]:
        logger.removeHandler(handler)
    logger.setLevel(logging.NOTSET)
    try:
        os.environ["FW_CLOUD_TOKEN"] = "fw_user0123456"
        write_cached_defaults(cloud.cloud_server().url, "python", choices_envelope(), "rev-7")
        server.route("POST", "/api/v1/resolve", offline)
        fw.new("Lab", tmp_path / "lab", interactive=False)
        assert "Using your Framework Cloud defaults from 20" in capsys.readouterr().err
    finally:
        logger.handlers[:] = saved[0]
        logger.setLevel(saved[1])


# Interop fixes (2026-11-05) -------------------------------------------------------------------


def test_a_string_slug_does_not_fail_a_public_collection_pre_check(
    cloud_project: Path, server: FakeCloud
) -> None:
    # The pre-check took a string slug as public, so it never asked for share_unlisted
    rows = [{"id": 7, "slug": "findings", "title": "F", "visibility": "unlisted"}]
    server.route(
        "GET", "/api/v1/collections", lambda r: httpx.Response(200, json={"collections": rows})
    )
    path = cloud_project / "figure.png"
    path.write_bytes(b"png")
    server.route(
        "POST", "/api/v1/documents", lambda r: httpx.Response(201, json={"id": 1, "url": "u"})
    )
    server.route("POST", "/api/v1/collections/7/items", lambda r: httpx.Response(201, json={}))
    assert fw.publish(path, to="cloud", slug="fig", collection="findings") == "u"
    fields = _fields(server.requests[1].content)
    assert b"readable" not in fields and b"visibility" not in fields


@pytest.mark.parametrize(
    ("reply", "shown", "hidden"),
    [
        (
            {
                "visibility": "public",
                "readable_url": None,
                "notice": "Set a handle in Account > Account details to get a readable URL. "
                "The unlisted link works now.",
            },
            "Public. Set a handle in Account > Account details",
            "unlisted",
        ),
        (
            {
                "readable_url": None,
                "notice": "Set a handle in Account > Account details to get a readable URL. "
                "The unlisted link works now.",
            },
            "Public. Set a handle",
            "unlisted",
        ),
        ({"visibility": "public", "readable_url": "https://x/@ada/p/r"}, "", "Set a handle"),
        ({"visibility": "unlisted", "readable_url": None}, "", "Set a handle"),
        ({"notice": "Storage is almost full."}, "Storage is almost full.", "Set a handle"),
    ],
)
def test_the_handle_hint_is_only_for_public_artifacts_without_a_readable_url(
    cloud_project: Path,
    server: FakeCloud,
    caplog: pytest.LogCaptureFixture,
    reply: dict[str, Any],
    shown: str,
    hidden: str,
) -> None:
    server.route(
        "POST",
        "/api/v1/documents",
        lambda r: httpx.Response(201, json={"url": "https://x/d/1", **reply}),
    )
    report = cloud_project / "report.html"
    report.write_text("<p>hi</p>", encoding="utf-8")
    caplog.set_level("INFO", logger="framework")
    fw.publish(report, to="cloud", slug="r")
    assert shown in caplog.text
    assert hidden not in caplog.text


@pytest.mark.parametrize(
    ("status", "message"),
    [(204, None), (401, None), (500, "could not be revoked"), (None, "could not be revoked")],
)
def test_logout_revokes_the_token_first(
    project: Path,
    server: FakeCloud,
    keyring: dict[str, str],
    caplog: pytest.LogCaptureFixture,
    status: int | None,
    message: str | None,
) -> None:
    url = "https://cloud.example.edu"
    service = cloud.keyring_service(url)
    keyring[service] = "fw_stored0123"
    seen: list[tuple[str, bool]] = []

    def revoke(request: httpx.Request) -> httpx.Response:
        # The local copy is still there while the server revokes it
        seen.append((request.headers["Authorization"], service in keyring))
        if status is None:
            raise httpx.ConnectError("offline")
        return httpx.Response(status, json={} if status != 204 else None)

    server.route("DELETE", "/api/v1/token", revoke)
    caplog.set_level("INFO", logger="framework")
    assert fw.cloud_logout(url) == [f"keyring ({service})"]
    assert seen == [("Bearer fw_stored0123", True)]
    assert keyring == {}
    if message:
        assert message in caplog.text and f"{url}/tokens" in caplog.text
    else:
        assert "could not be revoked" not in caplog.text


def test_logout_never_revokes_the_environment_token(
    project: Path, server: FakeCloud, keyring: dict[str, str]
) -> None:
    os.environ["FW_CLOUD_TOKEN"] = "fw_env0123"
    assert fw.cloud_logout("https://cloud.example.edu") == []
    assert not any(r.method == "DELETE" for r in server.requests)


def test_logout_revokes_each_stored_token_once(
    project: Path, server: FakeCloud, keyring: dict[str, str]
) -> None:
    keyring["framework-cloud:framework.pub"] = "fw_same"
    keyring["framework.pub"] = "fw_same"
    fw.config_dir().mkdir(parents=True)
    (fw.config_dir() / "cloud-token").write_text("fw_other\n", encoding="utf-8")
    server.route("DELETE", "/api/v1/token", lambda r: httpx.Response(204))
    fw.cloud_logout()
    revoked = [r.headers["Authorization"] for r in server.requests if r.method == "DELETE"]
    assert revoked == ["Bearer fw_same", "Bearer fw_other"]


class FakeSecurity:
    """/usr/bin/security for one service: records each call and keeps one password."""

    def __init__(self) -> None:
        self.calls: list[tuple[list[str], str | None]] = []
        self.password: str | None = None

    def __call__(
        self, args: list[str], input: str | None = None, **kwargs: Any
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append((args, input))
        assert args[0] == "/usr/bin/security"
        command = args[1]
        if command == "-i":
            assert input is not None
            self.password = input.split("-w '")[1].split("'")[0]
            return subprocess.CompletedProcess(args, 0, "", "")
        if command == "find-generic-password":
            if self.password is None:
                return subprocess.CompletedProcess(args, 44, "", "not found")
            return subprocess.CompletedProcess(args, 0, self.password + "\n", "")
        if command == "delete-generic-password":
            code = 44 if self.password is None else 0
            self.password = None
            return subprocess.CompletedProcess(args, code, "", "")
        raise AssertionError(command)


def test_macos_keychain_goes_through_security(monkeypatch: pytest.MonkeyPatch) -> None:
    from framework import _keychain

    del os.environ["PYTHON_KEYRING_BACKEND"]
    fake = FakeSecurity()
    monkeypatch.setattr(_keychain.subprocess, "run", fake)
    monkeypatch.setattr(cloud.sys, "platform", "darwin")
    service = "framework-cloud:keychain-test.invalid"
    assert cloud._keyring_get(service) is None
    assert cloud._keyring_set(service, "fw_secret0123") is True
    # The token goes on stdin, never in argv
    write = next(call for call in fake.calls if call[0][1] == "-i")
    assert "fw_secret0123" not in " ".join(write[0])
    assert write[1] == (f"add-generic-password -U -s '{service}' -a 'token' -w 'fw_secret0123'\n")
    assert cloud._keyring_get(service) == "fw_secret0123"
    assert cloud._keyring_delete(service) is True
    assert cloud._keyring_delete(service) is False
    assert ["/usr/bin/security", "find-generic-password", "-s", service, "-a", "token", "-w"] in [
        call[0] for call in fake.calls
    ]


def test_macos_keychain_decodes_go_keyring_and_refuses_quotes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import base64

    from framework import _keychain

    assert _keychain.decode("go-keyring-base64:" + base64.b64encode(b"fw_x").decode() + "\n") == (
        "fw_x"
    )
    assert _keychain.decode("fw_plain\n") == "fw_plain"
    assert _keychain.decode("\n") is None
    del os.environ["PYTHON_KEYRING_BACKEND"]
    monkeypatch.setattr(_keychain.subprocess, "run", lambda *a, **k: pytest.fail("ran security"))
    assert _keychain.set("framework-cloud:keychain-test.invalid", "token", "a'b") is False
    assert _keychain.set("framework-cloud:keychain-test.invalid", "token", "a\\b") is False


def test_null_keyring_backend_skips_the_macos_keychain(monkeypatch: pytest.MonkeyPatch) -> None:
    from framework import _keychain

    monkeypatch.setattr(_keychain.subprocess, "run", lambda *a, **k: pytest.fail("ran security"))
    monkeypatch.setattr(cloud.sys, "platform", "darwin")
    assert cloud._keyring_get("framework-cloud:keychain-test.invalid") is None
    assert cloud._keyring_set("framework-cloud:keychain-test.invalid", "fw_x") is False
    assert cloud._keyring_delete("framework-cloud:keychain-test.invalid") is False


def test_new_from_a_cloud_key_on_a_named_server(tmp_path: Path, server: FakeCloud) -> None:
    server.route("GET", "/api/v1/projects/pull", lambda r: httpx.Response(200, json=envelope()))
    root = fw.new(
        key="fwp_project0123",
        url="https://cloud.example.edu/",
        location=tmp_path / "cloud-study",
        interactive=False,
    )
    assert str(server.requests[0].url) == "https://cloud.example.edu/api/v1/projects/pull"
    assert dotenv.read(root / ".env")["FW_CLOUD_URL"] == "https://cloud.example.edu"


def test_url_without_a_key_is_refused(tmp_path: Path) -> None:
    with pytest.raises(fw.FrameworkError, match="Pass key too"):
        fw.new_project("x", tmp_path / "x", "project", url="https://cloud.example.edu")


def test_a_blank_cloud_author_email_is_not_written(tmp_path: Path, server: FakeCloud) -> None:
    def pull(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={**envelope(), "author": {"name": "Ada", "email": ""}})

    server.route("GET", "/api/v1/projects/pull", pull)
    root = fw.new(key="fwp_project0123", location=tmp_path / "cloud-study", interactive=False)
    spec = json.loads((root / ".framework/spec.json").read_text(encoding="utf-8"))
    assert spec["author"]["name"] == "Ada"
    assert not spec["author"].get("email")
    # Settings keep email: "" as R writes it; front matter never gets an empty email
    for path in root.rglob("*"):
        if path.suffix in (".qmd", ".ipynb") and ".git" not in path.parts:
            assert "email:" not in path.read_text(encoding="utf-8"), path
    from framework.notebooks import author_front_matter

    lines = author_front_matter({"name": "Ada", "email": "", "url": "https://example.org"})
    assert not any("email" in line for line in lines)


def test_publish_list_points_cloud_projects_to_the_artifacts_page(cloud_project: Path) -> None:
    with pytest.raises(fw.FrameworkError) as error:
        fw.publish_list()
    assert "S3 storage buckets" in str(error.value)
    assert "https://cloud.example.edu/published" in str(error.value)
