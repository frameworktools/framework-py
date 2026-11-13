import base64
import copy
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import framework as fw
from framework.skill_sync import _load, bundle_hash

SERVER = "https://cloud.example"


def bundle(text: str, name: str = "team-style") -> list[dict[str, Any]]:
    content = f"---\nname: {name}\ndescription: House style | for figures\n---\n{text}\n"
    return [
        {
            "path": "SKILL.md",
            "content": base64.b64encode(content.encode()).decode(),
            "executable": False,
        }
    ]


def cloud_entry(version: int, text: str) -> dict[str, Any]:
    files = bundle(text)
    return {
        "content": base64.b64decode(files[0]["content"]).decode(),
        "bundle": files,
        "sync": {
            "skill_id": "team-id",
            "version": version,
            "hash": bundle_hash(files),
            "variant": "python",
            "server": SERVER,
        },
    }


class Cloud:
    """Framework Cloud's layers pull and skill sync, in memory."""

    def __init__(self) -> None:
        self.skills: dict[str, Any] = {"team-style": cloud_entry(1, "Version one")}
        self.heads: dict[str, dict[str, Any]] = {}
        self.offline = False
        self.posts: list[dict[str, Any]] = []

    def envelope(self) -> dict[str, Any]:
        layer = {"type": "project", "language": "python", "skills": copy.deepcopy(self.skills)}
        return {
            "spec_version": 1,
            "name": "Demo",
            "layers": [{"kind": "project", "key": "demo", "layer": layer}],
        }

    def request(
        self, path: str, token: str, url: str, method: str = "GET", body: Any = None
    ) -> Any:
        from framework.cloud import CloudOfflineError

        if self.offline:
            raise CloudOfflineError("Could not reach cloud.example")
        assert path == "/api/v1/projects/pull" and token == "fw_project"
        return SimpleNamespace(json=lambda: self.envelope())

    def api(self, path: str, token: str, url: str, method: str = "GET", body: Any = None) -> Any:
        if method == "POST":
            self.posts.append(body)
            key = (
                body["skill_id"]
                if body["skill_id"] in self.heads and not body["skill_id"].startswith("framework:")
                else "own-id"
            )
            version = self.heads[key]["version"] + 1 if key in self.heads else 1
            self.heads[key] = {
                "skill_id": key,
                "version": version,
                "bundle": body["bundle"],
                "hash": bundle_hash(body["bundle"]),
            }
            return copy.deepcopy(self.heads[key])
        identity = path.split("/api/v1/skills/sync/")[1].split("?")[0].replace("%3A", ":")
        if identity.startswith("framework:"):
            # A built-in skill at the version the project installed
            target = Path.cwd() / ".claude/skills" / identity.split(":")[1]
            entry = next(
                e
                for e in _load(Path.cwd())["skills"].values()
                if target.relative_to(Path.cwd()).as_posix() in e["paths"]
            )
            return {
                "skill_id": identity,
                "version": 3,
                "hash": entry["origin"]["hash"],
                "bundle": self._original(identity),
                "system": True,
            }
        return copy.deepcopy(self.heads[identity])

    def _original(self, identity: str) -> list[dict[str, Any]]:
        from framework.ai import _shipped
        from framework.skill_sync import _source_files, validate

        return validate(_source_files(_shipped(identity.split(":")[1])))


@pytest.fixture
def cloud(project: Path, monkeypatch: pytest.MonkeyPatch) -> Cloud:
    import framework.cloud as module

    (project / "settings.yml").write_text(
        "default:\n  project_type: project\n  ai: {assistants: [claude]}\n", encoding="utf-8"
    )
    (project / ".env").write_text(f"FW_PROJECT_TOKEN=fw_project\nFW_CLOUD_URL={SERVER}\n")
    server = Cloud()
    monkeypatch.setattr(module, "_request", server.request)
    monkeypatch.setattr(module, "api", server.api)
    monkeypatch.setattr(module, "cloud_server", lambda *a, **k: SimpleNamespace(url=SERVER))
    return server


def statuses(result: dict[str, Any]) -> dict[str, str]:
    return {s["name"]: s["status"] for s in result["skills"]}


def skill_text(project: Path, name: str) -> str:
    return (project / ".claude/skills" / name / "SKILL.md").read_text(encoding="utf-8")


def test_pull_adds_updates_keeps_and_reports(cloud: Cloud, project: Path, capsys: Any) -> None:
    (project / "AGENTS.md").write_text(
        "# Demo\n\n## Skills\n\nold table\n\n## Project Notes\n\nmine\n"
    )
    first = fw.pull_skills(project)
    assert first["source"] == "cloud"
    assert statuses(first)["team-style"] == "added"
    assert first["counts"]["added"] == 8
    assert "Version one" in skill_text(project, "team-style")

    # Edited on Cloud: a new version, a removed skill. Edited here: framework-data.
    cloud.skills = {"team-style": cloud_entry(2, "Version two"), "framework-outputs": None}
    (project / ".claude/skills/framework-data/SKILL.md").write_text("my notes\n")
    second = fw.pull_skills(project)
    found = statuses(second)
    assert found["team-style"] == "updated"
    assert found["framework-data"] == "edited"
    assert found["framework-outputs"] == "removed"
    assert found["framework-workflow"] == "current"
    assert "Version two" in skill_text(project, "team-style")
    assert skill_text(project, "framework-data") == "my notes\n"
    # Never deleted
    assert (project / ".claude/skills/framework-outputs/SKILL.md").is_file()
    assert second["message"] == (
        "Skills: 1 updated, 5 up to date, 1 locally edited (kept), "
        "1 no longer in the project's setup"
    )
    err = capsys.readouterr().err
    assert (
        "  Kept (locally edited): framework-data — pull_skills(overwrite=True) replaces it" in err
    )
    assert "  No longer in the project's setup (kept): framework-outputs" in err

    agents = (project / "AGENTS.md").read_text()
    assert "old table" not in agents
    assert (
        "| `team-style` | House style \\| for figures | `.claude/skills/team-style/SKILL.md` |"
        in agents
    )
    assert agents.endswith("## Project Notes\n\nmine\n")
    entry = next(
        e for e in _load(project)["skills"].values() if ".claude/skills/team-style" in e["paths"]
    )
    assert entry["version"] == 2 and entry["skill_id"] == "team-id"


def test_pull_overwrite_replaces_edited_skills_after_a_backup(cloud: Cloud, project: Path) -> None:
    fw.pull_skills(project)
    (project / ".claude/skills/framework-data/SKILL.md").write_text("my notes\n")
    result = fw.pull_skills(project, overwrite=True)
    assert statuses(result)["framework-data"] == "replaced"
    assert skill_text(project, "framework-data") != "my notes\n"
    backups = list((project / ".framework/skills-backups").glob("*/SKILL.md"))
    assert any(b.read_text() == "my notes\n" for b in backups)


def test_pull_gets_the_latest_cloud_version_when_signed_in(
    cloud: Cloud, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FW_CLOUD_TOKEN", "fw_user")
    latest = cloud_entry(4, "Version four")
    cloud.heads["team-id"] = {
        "skill_id": "team-id",
        "version": 4,
        "bundle": latest["bundle"],
        "hash": latest["sync"]["hash"],
    }
    fw.pull_skills(project)
    assert "Version four" in skill_text(project, "team-style")
    # The setup's version counts as unedited too
    cloud.heads["team-id"] = {**cloud.heads["team-id"], "version": 5}
    assert statuses(fw.pull_skills(project))["team-style"] == "current"


def test_pull_without_cloud_uses_the_package_skills(
    cloud: Cloud, project: Path, capsys: Any
) -> None:
    cloud.offline = True
    (project / ".claude/skills/mine").mkdir(parents=True)
    (project / ".claude/skills/mine/SKILL.md").write_text("custom\n")
    result = fw.pull_skills(project)
    assert result["source"] == "package"
    assert "team-style" not in statuses(result)
    # Without the setup, nothing is reported as removed
    assert "mine" not in statuses(result)
    assert result["counts"]["added"] == 7
    assert (
        "Framework Cloud not reached (Could not reach cloud.example); "
        "using the package skills for this project type." in capsys.readouterr().err
    )


def test_pull_without_a_project_token_says_so(cloud: Cloud, project: Path) -> None:
    (project / ".env").write_text("")
    assert fw.pull_skills(project)["source"] == "package"


def test_pull_dry_run_writes_nothing(cloud: Cloud, project: Path) -> None:
    result = fw.pull_skills(project, dry_run=True)
    assert result["counts"]["added"] == 8
    assert result["message"].startswith("Skills (dry run, nothing written): 8 added")
    assert not (project / ".claude").exists()
    assert not (project / ".framework").exists()


def test_ai_skills_update_is_pull_skills(cloud: Cloud, project: Path) -> None:
    assert statuses(fw.ai_skills_update(project))["team-style"] == "added"


def pulled(cloud: Cloud, project: Path) -> None:
    fw.pull_skills(project)
    cloud.heads["team-id"] = {
        "skill_id": "team-id",
        "version": 1,
        "bundle": cloud.skills["team-style"]["bundle"],
        "hash": cloud.skills["team-style"]["sync"]["hash"],
    }


@pytest.fixture
def signed_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FW_CLOUD_TOKEN", "fw_user")


def test_push_sends_an_edited_account_skill(cloud: Cloud, project: Path, signed_in: None) -> None:
    pulled(cloud, project)
    (project / ".claude/skills/team-style/SKILL.md").write_text(
        "---\nname: team-style\ndescription: Edited\n---\nMine\n"
    )
    (project / ".claude/skills/stray").mkdir()
    (project / ".claude/skills/stray/SKILL.md").write_text("stray\n")
    result = fw.push_skills(project, confirm=True)
    assert statuses(result) == {"team-style": "pushed", "stray": "untracked"}
    assert result["message"] == "Skills pushed: 1 pushed, 1 not tracked"
    assert cloud.heads["team-id"]["version"] == 2
    assert cloud.posts[0]["expected_version"] == 1


def test_push_refuses_when_cloud_changed_unless_forced(
    cloud: Cloud, project: Path, signed_in: None
) -> None:
    pulled(cloud, project)
    (project / ".claude/skills/team-style/SKILL.md").write_text(
        "---\nname: team-style\ndescription: x\n---\nMine\n"
    )
    other = bundle("Someone else's")
    cloud.heads["team-id"] = {
        "skill_id": "team-id",
        "version": 2,
        "bundle": other,
        "hash": bundle_hash(other),
    }
    result = fw.push_skills(project, confirm=True)
    assert statuses(result)["team-style"] == "refused"
    assert result["message"] == "Skills pushed: 1 refused (pull first)"
    assert cloud.posts == []
    forced = fw.push_skills(project, force=True, confirm=True)
    assert statuses(forced)["team-style"] == "pushed"
    assert cloud.heads["team-id"]["version"] == 3


def test_push_saves_an_edited_builtin_as_your_own(
    cloud: Cloud, project: Path, signed_in: None, capsys: Any
) -> None:
    pulled(cloud, project)
    (project / ".claude/skills/framework-data/SKILL.md").write_text(
        "---\nname: framework-data\ndescription: x\n---\nMine\n"
    )
    result = fw.push_skills(project, confirm=True)
    assert statuses(result)["framework-data"] == "pushed"
    assert "own-id" in cloud.heads
    entry = next(
        e
        for e in _load(project)["skills"].values()
        if ".claude/skills/framework-data" in e["paths"]
    )
    assert entry["skill_id"] == "own-id"
    # The project's token names the project, so Cloud lists the copy in its setup
    assert cloud.posts[-1]["project_token"] == "fw_project"


def test_push_dry_run_and_confirmation(
    cloud: Cloud, project: Path, signed_in: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    pulled(cloud, project)
    (project / ".claude/skills/team-style/SKILL.md").write_text(
        "---\nname: team-style\ndescription: x\n---\nMine\n"
    )
    dry = fw.push_skills(project, dry_run=True)
    assert dry["message"] == "Skills (dry run, nothing sent): 1 to push"
    assert cloud.posts == []
    monkeypatch.setattr("sys.stdin", SimpleNamespace(isatty=lambda: False))
    with pytest.raises(fw.FrameworkError, match="confirm=True"):
        fw.push_skills(project)
    assert statuses(fw.push_skills(project, confirm=False))["team-style"] == "cancelled"
    assert cloud.posts == []


def test_push_needs_a_login(cloud: Cloud, project: Path) -> None:
    with pytest.raises(fw.CloudError, match="cloud_login"):
        fw.push_skills(project, confirm=True)


def test_lock_file_is_json(cloud: Cloud, project: Path) -> None:
    fw.pull_skills(project)
    json.loads((project / ".framework/skills.lock.json").read_text())


def test_pull_keeps_a_newer_pushed_version_when_signed_out(
    cloud: Cloud, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pulled(cloud, project)
    monkeypatch.setenv("FW_CLOUD_TOKEN", "fw_user")
    cloud.heads["team-id"] = {**copy.deepcopy(cloud.skills["team-style"]["sync"]), "version": 1}
    cloud.heads["team-id"]["bundle"] = cloud.skills["team-style"]["bundle"]
    mine = "---\nname: team-style\ndescription: Edited\n---\nMine\n"
    (project / ".claude/skills/team-style/SKILL.md").write_text(mine)
    fw.push_skills(project, confirm=True)
    assert cloud.heads["team-id"]["version"] == 2

    # The setup still names version 1, and without a login that is all a pull sees
    monkeypatch.delenv("FW_CLOUD_TOKEN")
    monkeypatch.setenv("FW_TOKEN_FILE", str(project.parent / "no-token"))
    result = fw.pull_skills(project)
    assert statuses(result)["team-style"] == "current"
    assert skill_text(project, "team-style") == mine
    entry = next(e for e in _load(project)["skills"].values() if e.get("skill_id") == "team-id")
    assert entry["version"] == 2


def test_push_saves_an_unpublished_builtin_as_a_new_skill(
    cloud: Cloud, project: Path, signed_in: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    from framework.cloud import CloudConflictError

    pulled(cloud, project)
    answer = cloud.api

    def api(path: str, *args: Any, **kwargs: Any) -> Any:
        # The package shipped a version of the built-in Cloud never published
        if "hash=" in path:
            raise CloudConflictError("The installed source version is unavailable.", {})
        return answer(path, *args, **kwargs)

    monkeypatch.setattr("framework.cloud.api", api)
    assert fw.push_skills(project, confirm=True)["message"] == "Skills pushed: nothing to push"
    (project / ".claude/skills/framework-data/SKILL.md").write_text(
        "---\nname: framework-data\ndescription: x\n---\nMine\n"
    )
    result = fw.push_skills(project, confirm=True)
    assert statuses(result)["framework-data"] == "pushed"
    assert cloud.posts[-1]["skill_id"] is None and cloud.posts[-1]["expected_version"] == 0
