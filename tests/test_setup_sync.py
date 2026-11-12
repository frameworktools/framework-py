import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import framework as fw
from framework.cloud import CloudError

SERVER = "https://cloud.example"

PYPROJECT = """[project]
name = "demo"
version = "0.1.0"
dependencies = [
    "framework-py",
    "pandas>=2.0",
    "requests",
    "ipykernel",
]
"""


def row(name: str, version: str | None = None, **extra: Any) -> dict[str, Any]:
    return {
        "name": name,
        "source": "pypi",
        "version": version,
        "auto_attach": False,
        "repo": None,
        **extra,
    }


class Cloud:
    """Framework Cloud's setup endpoints and layers pull, in memory."""

    def __init__(self) -> None:
        self.language = "python"
        self.revision = "rev-1"
        self.packages = [row("pandas", ">=2.2"), row("polars"), row("numpy")]
        self.posts: list[dict[str, Any]] = []
        self.missing = False
        self.offline = False

    def envelope(self) -> dict[str, Any]:
        layer = {"type": "project", "language": "python", "skills": {}}
        return {
            "spec_version": 1,
            "name": "Demo",
            "layers": [{"kind": "project", "key": "demo", "layer": layer}],
        }

    def setup(self) -> dict[str, Any]:
        return {
            "revision": self.revision,
            "language": self.language,
            "packages": self.packages,
            "skills": [],
        }

    def api(self, path: str, token: str, url: str, method: str = "GET", body: Any = None) -> Any:
        from framework.cloud import CloudConflictError, CloudOfflineError, _MissingEndpointError

        assert token == "fw_project" and url == SERVER
        if self.offline:
            raise CloudOfflineError("Could not reach cloud.example")
        if self.missing:
            raise _MissingEndpointError("Not found")
        if path == "/api/v1/projects/pull":
            return self.envelope()
        if path == "/api/v1/projects/setup":
            return self.setup()
        assert path == "/api/v1/projects/setup/packages" and method == "POST"
        self.posts.append(body)
        if body["base_revision"] != self.revision and not body["force"]:
            raise CloudConflictError(
                "Pull first", {"message": "Pull first", "revision": self.revision}
            )
        current = {p["name"]: p for p in self.packages}
        changes: dict[str, list[Any]] = {"added": [], "changed": [], "removed": [], "kept": []}
        for package in body["packages"]:
            before = current.get(package["name"])
            if before is None:
                changes["added"].append(package)
            elif before["version"] != package["version"]:
                changes["changed"].append({"name": package["name"], "from": before, "to": package})
        named = {p["name"] for p in body["packages"]}
        for name, package in current.items():
            if name not in named:
                changes["removed" if body["remove"] else "kept"].append(package)
        applied = not body["dry_run"] and any(changes[k] for k in ("added", "changed", "removed"))
        if applied:
            self.revision = "rev-2"
        return {"applied": applied, "revision": self.revision, "changes": changes}

    def request(
        self, path: str, token: str, url: str, method: str = "GET", body: Any = None
    ) -> Any:
        reply = self.api(path, token, url, method, body)
        return SimpleNamespace(json=lambda: reply)


@pytest.fixture(autouse=True)
def _messages() -> None:
    # Before caplog sets a level, so its teardown restores INFO, not NOTSET, and
    # later tests that read stderr still see Framework's messages
    from framework._log import show_messages

    show_messages()


@pytest.fixture
def cloud(project: Path, monkeypatch: pytest.MonkeyPatch) -> Cloud:
    import framework.cloud as module
    import framework.packages as packages

    (project / "settings.yml").write_text(
        "default:\n  project_name: Test Project\n  project_type: project\n", encoding="utf-8"
    )
    (project / "pyproject.toml").write_text(PYPROJECT, encoding="utf-8")
    (project / ".env").write_text(f"FW_PROJECT_TOKEN=fw_project\nFW_CLOUD_URL={SERVER}\n")
    monkeypatch.delenv("FW_PROJECT_TOKEN", raising=False)
    monkeypatch.delenv("FW_CLOUD_URL", raising=False)
    monkeypatch.setenv("FW_TOKEN_FILE", str(project.parent / "no-token"))
    server = Cloud()
    monkeypatch.setattr(module, "_request", server.request)
    monkeypatch.setattr(module, "api", server.api)
    server.runs = []  # type: ignore[attr-defined]
    monkeypatch.setattr(packages, "_uv", lambda: "uv")
    monkeypatch.setattr(packages, "_run", lambda args, root: server.runs.append(args) or 0)  # type: ignore[attr-defined]
    return server


def lock(project: Path) -> dict[str, Any]:
    return json.loads((project / ".framework/setup.lock.json").read_text())


def test_pull_lists_changes_and_adds_packages(cloud: Cloud, project: Path, caplog: Any) -> None:
    caplog.set_level("INFO", logger="framework")
    result = fw.pull_setup(confirm=True)
    text = caplog.text
    assert "Setup changes from Framework Cloud (revision rev-1):" in text
    assert "Packages to add: polars, numpy" in text
    assert "Packages that differ: pandas (local >=2.0, Cloud >=2.2) — kept" in text
    assert "Only in this project (kept): requests" in text
    # framework-py and the unpinned tool packages are not part of the setup
    assert "ipykernel" not in text and "framework-py" not in text
    assert cloud.runs == [["uv", "add", "polars", "numpy"]]  # type: ignore[attr-defined]
    assert result["applied"] is True
    assert result["packages"]["differ_kept"] == ["pandas"]
    assert result["skills"] is not None
    assert lock(project)["revision"] == "rev-1"
    assert "fw_project" not in (project / ".framework/setup.lock.json").read_text()


def test_pull_overwrite_uses_clouds_version(cloud: Cloud, project: Path) -> None:
    result = fw.pull_setup(overwrite=True, confirm=True)
    assert cloud.runs == [["uv", "add", "polars", "numpy", "pandas>=2.2"]]  # type: ignore[attr-defined]
    assert result["packages"]["changed"] == ["pandas"]


def test_pull_dry_run_writes_nothing(cloud: Cloud, project: Path) -> None:
    result = fw.pull_setup(dry_run=True)
    assert cloud.runs == []  # type: ignore[attr-defined]
    assert not (project / ".framework/setup.lock.json").exists()
    assert result["applied"] is False and result["dry_run"] is True


def test_pull_without_console_needs_confirm(
    cloud: Cloud, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("sys.stdin", SimpleNamespace(isatty=lambda: False))
    with pytest.raises(fw.FrameworkError, match="confirm=True"):
        fw.pull_setup()
    assert cloud.runs == []  # type: ignore[attr-defined]
    assert fw.pull_setup(confirm=False)["applied"] is False


def test_pull_up_to_date(cloud: Cloud, project: Path, caplog: Any) -> None:
    caplog.set_level("INFO", logger="framework")
    cloud.packages = [row("pandas", ">=2.0"), row("requests")]
    fw.pull_skills()
    fw.pull_setup()
    assert "Setup is up to date with Framework Cloud (revision rev-1)." in caplog.text
    assert lock(project)["revision"] == "rev-1"


def test_pull_offline_and_missing_endpoint_change_nothing(cloud: Cloud, project: Path) -> None:
    cloud.missing = True
    with pytest.raises(CloudError, match="no setup endpoint yet"):
        fw.pull_setup(confirm=True)
    cloud.missing, cloud.offline = False, True
    with pytest.raises(CloudError, match="Framework Cloud not reached"):
        fw.pull_setup(confirm=True)
    assert cloud.runs == []  # type: ignore[attr-defined]


def test_pull_stata_does_skills_only(cloud: Cloud, project: Path, caplog: Any) -> None:
    caplog.set_level("INFO", logger="framework")
    cloud.language = "stata"
    result = fw.pull_setup(confirm=True)
    assert "not managed for Stata projects" in caplog.text
    assert result["packages"]["added"] == []
    assert cloud.runs == []  # type: ignore[attr-defined]


def test_push_previews_then_sends(cloud: Cloud, project: Path, caplog: Any) -> None:
    caplog.set_level("INFO", logger="framework")
    (project / ".framework").mkdir(exist_ok=True)
    (project / ".framework/setup.lock.json").write_text('{"schema": 1, "revision": "rev-1"}')
    result = fw.push_setup(confirm=True)
    text = caplog.text
    assert "Setup changes for Framework Cloud:" in text
    assert "Packages to add: requests" in text
    assert "Packages to change: pandas (>=2.2 -> >=2.0)" in text
    assert "On Cloud only (kept): polars, numpy" in text
    assert (
        "This changes Test Project's setup on Framework Cloud. "
        "New setups, zips, and Update on GitHub will use it."
    ) in text
    assert "Setup pushed: revision rev-2 (1 added, 1 changed)" in text
    assert [p["dry_run"] for p in cloud.posts] == [True, False]
    sent = cloud.posts[0]["packages"]
    assert {p["name"] for p in sent} == {"pandas", "requests"}
    assert all(p["auto_attach"] is False for p in sent)
    assert result["applied"] is True and lock(project)["revision"] == "rev-2"
    # Signed out, the skills step says so instead of failing the push
    assert "Skills not pushed" in text


def test_push_uses_clouds_revision_without_a_lock(cloud: Cloud, project: Path) -> None:
    fw.push_setup(dry_run=True)
    assert cloud.posts[0]["base_revision"] == "rev-1"
    assert len(cloud.posts) == 1


def test_push_refuses_a_stale_setup_unless_forced(cloud: Cloud, project: Path, caplog: Any) -> None:
    caplog.set_level("INFO", logger="framework")
    (project / ".framework").mkdir(exist_ok=True)
    (project / ".framework/setup.lock.json").write_text('{"schema": 1, "revision": "rev-0"}')
    with pytest.raises(CloudError, match=r"Pull first: pull_setup\(\)"):
        fw.push_setup(confirm=True)
    assert len(cloud.posts) == 1
    fw.push_setup(force=True, confirm=True)
    assert all(p["force"] for p in cloud.posts[1:])
    assert "Cloud's newer setup changes will be replaced" in caplog.text


def test_push_remove_and_cancel(
    cloud: Cloud, project: Path, caplog: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    caplog.set_level("INFO", logger="framework")
    result = fw.push_setup(remove=True, confirm=False)
    assert "Packages to remove: polars, numpy" in caplog.text
    assert [p["remove"] for p in cloud.posts] == [True]
    assert result["applied"] is False
    monkeypatch.setattr("sys.stdin", SimpleNamespace(isatty=lambda: False))
    with pytest.raises(fw.FrameworkError, match="confirm=True"):
        fw.push_setup()


def test_push_runs_push_skills(
    cloud: Cloud, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import framework.skill_pull as skill_pull

    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(skill_pull, "push_skills", lambda root, **kw: calls.append(kw) or {"ok": 1})
    result = fw.push_setup(force=True, dry_run=True)
    assert calls == [{"force": True, "confirm": None, "dry_run": True}]
    assert result["skills"] == {"ok": 1}
    assert all(p["dry_run"] for p in cloud.posts)


def test_push_matching_setup_sends_nothing(cloud: Cloud, project: Path, caplog: Any) -> None:
    caplog.set_level("INFO", logger="framework")
    cloud.packages = [row("pandas", ">=2.0"), row("requests")]
    fw.push_setup(confirm=True)
    assert "Setup on Framework Cloud already matches this project." in caplog.text
    assert [p["dry_run"] for p in cloud.posts] == [True]


def test_requirement_rows() -> None:
    from framework.setup_sync import _row, _same

    assert _row("pandas >= 2.0, <3") == row("pandas", ">=2.0,<3")
    assert _row("tool @ git+https://github.com/owner/tool@v1") == row(
        "tool", source="github", repo="owner/tool"
    )
    assert _row("thing @ https://example.org/thing.whl") is None
    assert _same(row("x", "==1.2"), row("x", "1.2"))
    assert not _same(row("x", source="github", repo="a/x"), row("x", source="github", repo="b/x"))
