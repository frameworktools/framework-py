import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import framework as fw
from framework.cloud import CloudError

SERVER = "https://cloud.example"

SETTINGS = """default:
  project_name: Test Project
  project_type: project
  data:
    inputs:
      raw:
        survey:
          path: inputs/raw/survey.csv
"""

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


def entry(name: str, path: str, **fields: Any) -> dict[str, Any]:
    return {
        "name": name,
        "path": path,
        "type": None,
        "delimiter": None,
        "locked": False,
        "private": None,
        "description": None,
        "stata_names": None,
        **fields,
    }


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
    """Framework Cloud's package list, layers pull, and data registry, in memory."""

    def __init__(self) -> None:
        self.language = "python"
        self.revision = "rev-1"
        self.packages = [row("pandas", ">=2.2"), row("polars"), row("numpy")]
        self.posts: list[dict[str, Any]] = []
        self.missing = False
        self.offline = False
        self.data_revision = "data-1"
        self.entries: list[dict[str, Any]] = [
            entry("inputs.raw.survey", "inputs/raw/survey.csv"),
            entry("inputs.raw.visits", "inputs/raw/visits.csv"),
        ]
        self.data_posts: list[dict[str, Any]] = []
        self.data_missing = False

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
        if path == "/api/v1/projects/data":
            return self.data(method, body)
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

    def data(self, method: str, body: Any) -> Any:
        from framework.cloud import CloudConflictError, _MissingEndpointError

        if self.data_missing:
            raise _MissingEndpointError("Not found")
        if method == "GET":
            return {"revision": self.data_revision, "entries": self.entries}
        self.data_posts.append(body)
        if body["base_revision"] != self.data_revision and not body["force"]:
            raise CloudConflictError(
                "Pull first", {"message": "Pull first", "revision": self.data_revision}
            )
        current = {e["name"]: e for e in self.entries}
        changes: dict[str, list[Any]] = {"added": [], "changed": [], "removed": [], "kept": []}
        for item in body["entries"]:
            before = current.get(item["name"])
            if before is None:
                changes["added"].append(item)
            elif before.get("path") != item.get("path"):
                changes["changed"].append({"name": item["name"], "from": before, "to": item})
        named = {e["name"] for e in body["entries"]}
        for name, item in current.items():
            if name not in named:
                changes["removed" if body["remove"] else "kept"].append(item)
        applied = not body["dry_run"] and any(changes[k] for k in ("added", "changed", "removed"))
        if applied:
            self.data_revision = "data-2"
        return {"applied": applied, "revision": self.data_revision, "changes": changes}

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

    (project / "settings.yml").write_text(SETTINGS, encoding="utf-8")
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


def data_lock(project: Path) -> dict[str, Any]:
    return json.loads((project / ".framework/data.lock.json").read_text())


def data_names(project: Path) -> list[str]:
    from framework.data_registry import _local

    return list(_local(project))


def test_pull_lists_every_part_then_applies_them(
    cloud: Cloud, project: Path, caplog: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    caplog.set_level("INFO", logger="framework")
    result = fw.pull_all(dry_run=True)
    assert result["message"].splitlines()[:5] == [
        "Changes from Framework Cloud:",
        "Packages (revision rev-1):",
        "  Packages to add: polars, numpy",
        "  Packages that differ: pandas (local >=2.0, Cloud >=2.2) — kept; "
        "pull_all(overwrite=True) uses Cloud's",
        "  Only in this project (kept): requests",
    ]
    assert "Data registry (revision data-1):" in result["message"]
    assert "  Entries to add: inputs.raw.visits" in result["message"]
    # framework-py and the unpinned tool packages are not part of the package list
    assert "ipykernel" not in caplog.text and "framework-py" not in result["message"]
    assert result["applied"] == [] and result["dry_run"] is True
    assert result["packages"]["added"] == ["polars", "numpy"]
    assert result["data_registry"]["added"] == ["inputs.raw.visits"]
    assert cloud.runs == []  # type: ignore[attr-defined]
    assert not (project / ".framework/setup.lock.json").exists()
    assert not (project / ".framework/data.lock.json").exists()

    # One question for everything; without a console it needs confirm
    monkeypatch.setattr("sys.stdin", SimpleNamespace(isatty=lambda: False))
    with pytest.raises(fw.FrameworkError, match=r"pull_all\(\) asks before changing anything"):
        fw.pull_all()
    assert fw.pull_all(confirm=False)["applied"] == []
    assert cloud.runs == []  # type: ignore[attr-defined]

    caplog.clear()
    result = fw.pull_all(confirm=True)
    assert result["applied"] == ["packages", "skills", "data_registry"]
    assert result["skills"]["counts"]["added"] > 0
    assert result["failed"] == {}
    assert "✓ Data registry: 1 entry added or updated" in caplog.text
    assert cloud.runs == [["uv", "add", "--raw", "polars", "numpy"]]  # type: ignore[attr-defined]
    assert "inputs.raw.visits" in data_names(project)
    assert lock(project)["revision"] == "rev-1"
    assert data_lock(project)["revision"] == "data-1"
    assert "fw_project" not in (project / ".framework/setup.lock.json").read_text()


def test_pull_overwrite_uses_clouds_versions(cloud: Cloud, project: Path) -> None:
    cloud.entries = [entry("inputs.raw.survey", "inputs/raw/survey_v2.csv")]
    result = fw.pull_all(dry_run=True)
    assert (
        "inputs.raw.survey (path inputs/raw/survey.csv -> inputs/raw/survey_v2.csv) — kept; "
        "pull_all(overwrite=True) uses Cloud's"
    ) in result["message"]
    fw.pull_all(confirm=True)
    from framework.data_registry import _local

    assert _local(project)["inputs.raw.survey"]["path"] == "inputs/raw/survey.csv"
    result = fw.pull_all(overwrite=True, confirm=True)
    assert cloud.runs[-1] == ["uv", "add", "--raw", "polars", "numpy", "pandas>=2.2"]  # type: ignore[attr-defined]
    assert result["packages"]["changed"] == ["pandas"]
    assert result["data_registry"]["changed"] == ["inputs.raw.survey"]
    assert _local(project)["inputs.raw.survey"]["path"] == "inputs/raw/survey_v2.csv"


def test_pull_up_to_date(cloud: Cloud, project: Path, caplog: Any) -> None:
    cloud.packages = [row("pandas", ">=2.0"), row("requests")]
    cloud.entries = [entry("inputs.raw.survey", "inputs/raw/survey.csv")]
    fw.pull_skills()
    caplog.set_level("INFO", logger="framework")
    result = fw.pull_all()
    assert result["message"] == "Project is up to date with Framework Cloud."
    assert lock(project)["revision"] == "rev-1"
    assert data_lock(project)["revision"] == "data-1"


def test_pull_applies_what_it_can_and_names_the_rest(cloud: Cloud, project: Path) -> None:
    cloud.data_missing = True
    result = fw.pull_all(dry_run=True)
    assert (
        "Data registry: not read. This Framework Cloud server has no data registry endpoint yet."
    ) in result["message"]
    assert list(result["failed"]) == ["data_registry"]
    with pytest.raises(CloudError) as caught:
        fw.pull_all(confirm=True)
    message = str(caught.value)
    assert message.startswith("pull_all() did not finish. Applied: Packages")
    assert message.endswith(
        "Not applied: Data registry (This Framework Cloud server has no data registry "
        "endpoint yet)."
    )
    assert cloud.runs == [["uv", "add", "--raw", "polars", "numpy"]]  # type: ignore[attr-defined]
    assert lock(project)["revision"] == "rev-1"
    assert not (project / ".framework/data.lock.json").exists()


def test_pull_offline_and_missing_endpoint_change_nothing(cloud: Cloud, project: Path) -> None:
    cloud.missing = True
    with pytest.raises(CloudError, match="no setup endpoint yet"):
        fw.pull_all(confirm=True)
    cloud.missing, cloud.offline = False, True
    with pytest.raises(CloudError, match="Framework Cloud not reached"):
        fw.pull_all(confirm=True)
    assert cloud.runs == []  # type: ignore[attr-defined]


def test_pull_stata_leaves_packages_alone(cloud: Cloud, project: Path) -> None:
    cloud.language = "stata"
    result = fw.pull_all(dry_run=True)
    assert "Packages: not managed for Stata projects" in result["message"]
    fw.pull_all(confirm=True)
    assert result["packages"]["added"] == []
    assert cloud.runs == []  # type: ignore[attr-defined]


def edited_skill(
    monkeypatch: pytest.MonkeyPatch, pushed: list[str], signed_in: bool = True
) -> None:
    """An edited skill to push, without reaching Cloud."""
    import framework.cloud as module
    import framework.skill_pull as skill_pull

    monkeypatch.setattr(module, "cloud_token", lambda url: "fw_user" if signed_in else None)
    monkeypatch.setattr(
        skill_pull, "_push_edited", lambda root: [({}, [".claude/skills/analysis"])]
    )
    monkeypatch.setattr(
        skill_pull,
        "_push_plan",
        lambda root, force: (
            [
                {
                    "name": "analysis",
                    "path": ".claude/skills/analysis",
                    "status": "push",
                    "builtin": False,
                    "forced": False,
                }
            ],
            [],
        ),
    )

    def apply(root: Path, plans: list[dict[str, Any]]) -> list[dict[str, Any]]:
        pushed.append("analysis")
        return [{**p, "status": "pushed"} for p in plans]

    monkeypatch.setattr(skill_pull, "_push_apply", apply)


def test_push_previews_every_part_asks_once_and_pushes(
    cloud: Cloud, project: Path, monkeypatch: pytest.MonkeyPatch, caplog: Any
) -> None:
    pushed: list[str] = []
    edited_skill(monkeypatch, pushed)
    lock_dir = project / ".framework"
    lock_dir.mkdir(exist_ok=True)
    (lock_dir / "setup.lock.json").write_text('{"schema": 1, "revision": "rev-1"}')
    result = fw.push_all(dry_run=True)
    assert result["message"].splitlines() == [
        "Changes for Framework Cloud:",
        "Packages:",
        "  Packages to add: requests",
        "  Packages to change: pandas (>=2.2 -> >=2.0)",
        "  On Cloud only (kept): polars, numpy",
        "  This changes Test Project's package list on Framework Cloud. "
        "New setups, zips, and Update on GitHub will use it.",
        "Skills:",
        "  Skills to push: analysis",
        "  This replaces the current version of analysis, used wherever it's attached.",
        "Data registry:",
        "  On Cloud only (kept): inputs.raw.visits",
    ]
    assert [p["dry_run"] for p in cloud.posts] == [True]
    assert [p["dry_run"] for p in cloud.data_posts] == [True]
    sent = cloud.posts[0]["packages"]
    assert {p["name"] for p in sent} == {"pandas", "requests"}
    assert all(p["auto_attach"] is False for p in sent)
    assert pushed == []

    monkeypatch.setattr("sys.stdin", SimpleNamespace(isatty=lambda: False))
    with pytest.raises(fw.FrameworkError, match=r"push_all\(\) asks before changing anything"):
        fw.push_all()
    assert fw.push_all(confirm=False)["applied"] == []
    assert pushed == []

    caplog.set_level("INFO", logger="framework")
    result = fw.push_all(remove=True, confirm=True)
    text = caplog.text
    assert "  Packages to remove: polars, numpy" in text
    assert "  Entries to remove: inputs.raw.visits" in text
    assert "✓ Packages pushed: revision rev-2 (1 added, 1 changed, 2 removed)" in text
    assert "✓ Skills pushed: analysis" in text
    assert "Data registry pushed: revision data-2 (1 removed)" in text
    assert result["applied"] == ["packages", "skills", "data_registry"]
    assert pushed == ["analysis"]
    assert cloud.posts[-1]["remove"] and not cloud.posts[-1]["dry_run"]
    assert cloud.data_posts[-1]["remove"] and not cloud.data_posts[-1]["dry_run"]
    assert lock(project)["revision"] == "rev-2"
    assert data_lock(project)["revision"] == "data-2"


def test_push_uses_clouds_revision_without_a_lock(cloud: Cloud, project: Path) -> None:
    fw.push_all(dry_run=True)
    assert cloud.posts[0]["base_revision"] == "rev-1"
    assert cloud.data_posts[0]["base_revision"] == "data-1"
    assert len(cloud.posts) == 1


def test_push_says_pull_first_pushes_the_rest_and_force_replaces(
    cloud: Cloud, project: Path, caplog: Any
) -> None:
    (project / ".framework").mkdir(exist_ok=True)
    (project / ".framework/setup.lock.json").write_text('{"schema": 1, "revision": "rev-0"}')
    (project / ".framework/data.lock.json").write_text('{"schema": 1, "revision": "data-1"}')
    cloud.entries = []
    pull_first = (
        "Packages: can't push. Framework Cloud's package list changed since this project "
        "last pulled it (revision rev-1). Pull first: pull_all(). "
        "push_all(force=True) replaces Cloud's changes."
    )
    result = fw.push_all(dry_run=True)
    assert pull_first in result["message"].splitlines()
    assert list(result["failed"]) == ["packages"]

    with pytest.raises(CloudError) as caught:
        fw.push_all(confirm=True)
    assert str(caught.value) == (
        "push_all() did not finish. Pushed: Data registry. Already in step: Skills. "
        "Not pushed: Packages (Framework Cloud's package list changed since this project "
        "last pulled it (revision rev-1). Pull first: pull_all(). "
        "push_all(force=True) replaces Cloud's changes)."
    )
    assert lock(project)["revision"] == "rev-0"
    assert data_lock(project)["revision"] == "data-2"

    caplog.set_level("INFO", logger="framework")
    result = fw.push_all(force=True, confirm=True)
    assert "Cloud's newer package changes will be replaced" in caplog.text
    assert all(p["force"] for p in cloud.posts[-2:])
    assert "packages" in result["applied"]
    assert lock(project)["revision"] == "rev-2"


def test_push_skills_need_a_login_but_the_rest_goes(
    cloud: Cloud, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pushed: list[str] = []
    edited_skill(monkeypatch, pushed, signed_in=False)
    result = fw.push_all(dry_run=True)
    assert any(
        line.startswith(
            "Skills: can't push. Not signed in, so edited skills (analysis) can't be pushed."
        )
        for line in result["message"].splitlines()
    )
    with pytest.raises(CloudError, match=r"Not pushed: Skills \(Not signed in"):
        fw.push_all(confirm=True)
    assert pushed == []
    assert lock(project)["revision"] == "rev-2"


def test_push_matching_project_sends_nothing(cloud: Cloud, project: Path) -> None:
    cloud.packages = [row("pandas", ">=2.0"), row("requests")]
    cloud.entries = [entry("inputs.raw.survey", "inputs/raw/survey.csv")]
    result = fw.push_all(confirm=True)
    assert result["message"] == "Framework Cloud already matches this project."
    assert [p["dry_run"] for p in cloud.posts] == [True]
    assert [p["dry_run"] for p in cloud.data_posts] == [True]


def test_tool_packages_count_when_the_setup_lists_them(cloud: Cloud, project: Path) -> None:
    # A Jupyter project's package list has ipykernel, as pyproject.toml does
    cloud.packages = [row("pandas", ">=2.0"), row("requests"), row("ipykernel")]
    cloud.entries = [entry("inputs.raw.survey", "inputs/raw/survey.csv")]
    fw.pull_skills()
    result = fw.pull_all()
    assert result["message"] == "Project is up to date with Framework Cloud."

    # So remove keeps it, and framework-py is still never sent
    fw.push_all(remove=True, confirm=True)
    sent = [p["name"] for p in cloud.posts[-1]["packages"]]
    assert sent == ["pandas", "requests", "ipykernel"]


def test_r_projects_are_refused(cloud: Cloud, project: Path) -> None:
    cloud.language = "r"
    with pytest.raises(
        CloudError,
        match=r"This is an R project on Framework Cloud\. Use the framework R package's pull_all",
    ):
        fw.pull_all(dry_run=True)
    with pytest.raises(CloudError, match=r"push_all\(\) or fw push\. Nothing changed\."):
        fw.push_all(remove=True, confirm=True)
    assert cloud.posts == [] and cloud.runs == []  # type: ignore[attr-defined]


def test_requirement_rows() -> None:
    from framework.sync_all import _row, _same

    assert _row("pandas >= 2.0, <3") == row("pandas", ">=2.0,<3")
    assert _row("tool @ git+https://github.com/owner/tool@v1") == row(
        "tool", source="github", repo="owner/tool"
    )
    assert _row("thing @ https://example.org/thing.whl") is None
    assert _same(row("x", "==1.2"), row("x", "1.2"))
    assert not _same(row("x", source="github", repo="a/x"), row("x", source="github", repo="b/x"))
