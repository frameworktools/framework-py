import base64
import copy
import json
import os
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import framework as fw
from framework.skill_pull import row_text
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
                if target.relative_to(Path.cwd()).as_posix() in e["copies"]
            )
            return {
                "skill_id": identity,
                "version": 3,
                "hash": entry["base"]["hash"],
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


def view(entry: dict[str, Any]) -> dict[str, Any]:
    """A v2 lock entry in the terms these tests check: its copies' paths, and the Cloud
    skill and version it was last synced with (``skill_id`` None for a package copy)."""
    identity, base = entry["identity"], entry.get("base") or {}
    on_cloud = identity["kind"] == "cloud" or base.get("channel") == "cloud"
    return {
        **entry,
        "paths": sorted(entry["copies"]),
        "skill_id": identity.get("skill_id") if on_cloud else None,
        "version": base.get("version"),
        "hash": base.get("hash"),
        "variant": identity["variant"],
        "server": identity.get("server"),
    }


def views(project: Path) -> list[dict[str, Any]]:
    return [view(e) for e in _load(project)["skills"].values()]


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
        "  edited: framework-data. .claude/skills/framework-data has local edits, which were "
        "kept. pull_skills(overwrite=True) replaces them with the Cloud version." in err
    )
    assert (
        "  removed: framework-outputs. No longer in the project's setup, so pull left it alone."
        in err
    )

    agents = (project / "AGENTS.md").read_text()
    assert "old table" not in agents
    assert (
        "| `team-style` | House style \\| for figures | `.claude/skills/team-style/SKILL.md` |"
        in agents
    )
    assert agents.endswith("## Project Notes\n\nmine\n")
    entry = next(e for e in views(project) if ".claude/skills/team-style" in e["paths"])
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
    entry = next(e for e in views(project) if ".claude/skills/framework-data" in e["paths"])
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
    entry = next(e for e in views(project) if e.get("skill_id") == "team-id")
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


def test_pull_checks_every_skill_before_writing_any(cloud: Cloud, project: Path) -> None:
    bad = cloud_entry(1, "Tampered")
    bad["sync"] = {**bad["sync"], "skill_id": "bad-id", "hash": "0" * 64}
    cloud.skills = {"team-style": cloud_entry(1, "Version one"), "zz-bad": bad}
    with pytest.raises(ValueError, match="Cloud skill verification failed: zz-bad"):
        fw.pull_skills(project)
    assert not (project / ".claude/skills").exists()
    assert not (project / ".framework/skills.lock.json").exists()


def two_assistants(project: Path) -> None:
    (project / "settings.yml").write_text(
        "default:\n  project_type: project\n  ai: {assistants: [claude, codex]}\n",
        encoding="utf-8",
    )


def test_pull_updates_every_folder_of_a_two_folder_skill(cloud: Cloud, project: Path) -> None:
    two_assistants(project)
    fw.pull_skills(project)
    cloud.skills = {"team-style": cloud_entry(2, "Version two")}
    result = fw.pull_skills(project)
    assert statuses(result)["team-style"] == "updated"
    for folder in (".claude/skills", ".agents/skills"):
        assert "Version two" in (project / folder / "team-style/SKILL.md").read_text()
    # Nothing was edited, so the next pull finds both current
    assert statuses(fw.pull_skills(project))["team-style"] == "current"


def test_pull_keeps_a_copy_edited_between_planning_and_installing(
    cloud: Cloud, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import framework.skill_pull as skill_pull

    two_assistants(project)
    fw.pull_skills(project)
    cloud.skills = {"team-style": cloud_entry(2, "Version two")}
    edited = project / ".agents/skills/team-style/SKILL.md"
    real = skill_pull.planner.plan

    def plan(*args: Any) -> Any:
        result = real(*args)
        edited.write_text(edited.read_text() + "Edited mid-pull\n")
        return result

    monkeypatch.setattr(skill_pull.planner, "plan", plan)
    result = fw.pull_skills(project)
    found = next(s for s in result["skills"] if s["name"] == "team-style")
    assert found["status"] == "edited" and found["reason_code"] == "changed_during_pull"
    assert "Version two" in skill_text(project, "team-style")
    assert "Version one" in edited.read_text() and "Edited mid-pull" in edited.read_text()


@pytest.mark.parametrize("dry_run", [True, False])
def test_pull_refuses_a_skills_folder_that_is_a_file(
    cloud: Cloud, project: Path, dry_run: bool
) -> None:
    two_assistants(project)
    (project / ".agents").mkdir()
    (project / ".agents/skills").write_text("not a folder\n")
    with pytest.raises(fw.FrameworkError, match=r"\.agents/skills: it is not a folder"):
        fw.pull_skills(project, dry_run=dry_run)
    assert not (project / ".claude/skills").exists()
    assert (project / ".agents/skills").read_text() == "not a folder\n"


# Rule P: several tracked copies; rule Q: a new assistant folder


def team_entry(project: Path) -> dict[str, Any]:
    return next(e for e in views(project) if ".claude/skills/team-style" in e["paths"])


def test_pull_restores_a_missing_copy_beside_an_unedited_one(
    cloud: Cloud, project: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    two_assistants(project)
    # Signed in: both folders get Cloud's v2 while the setup still names v1
    monkeypatch.setenv("FW_CLOUD_TOKEN", "fw_user")
    latest = cloud_entry(2, "Version two")
    cloud.heads["team-id"] = {
        "skill_id": "team-id",
        "version": 2,
        "bundle": latest["bundle"],
        "hash": latest["sync"]["hash"],
    }
    fw.pull_skills(project)
    assert team_entry(project)["version"] == 2
    monkeypatch.delenv("FW_CLOUD_TOKEN")
    lock = (project / ".framework/skills.lock.json").read_text()
    shutil.rmtree(project / ".agents/skills/team-style")

    result = fw.pull_skills(project)
    # v2: nothing edited would be lost, so the absent copy is restored. The setup's v1 is
    # older than the project's v2, so it is copied from the project's copy, never v1.
    found = next(s for s in result["skills"] if s["name"] == "team-style")
    assert found["status"] == "restored"
    assert result["counts"]["diverged"] == 0
    capsys.readouterr()
    restored = (project / ".agents/skills/team-style/SKILL.md").read_text()
    assert "Version two" in restored and "Version two" in skill_text(project, "team-style")
    # Its record was already v2's: the lock doesn't change
    assert (project / ".framework/skills.lock.json").read_text() == lock


def test_pull_leaves_differing_tracked_copies_alone(cloud: Cloud, project: Path) -> None:
    two_assistants(project)
    fw.pull_skills(project)
    cloud.skills = {"team-style": cloud_entry(2, "Version two")}
    agents = project / ".agents/skills/team-style/SKILL.md"
    agents.write_text(agents.read_text() + "Codex edit\n")
    for overwrite in (False, True):
        result = fw.pull_skills(project, overwrite=overwrite)
        assert statuses(result)["team-style"] == "diverged"
        assert "Version one" in skill_text(project, "team-style")
        assert "Codex edit" in agents.read_text()
        assert team_entry(project)["version"] == 1


def test_push_fails_an_edited_skill_with_a_missing_tracked_copy(
    cloud: Cloud, project: Path, signed_in: None
) -> None:
    two_assistants(project)
    pulled(cloud, project)
    shutil.rmtree(project / ".agents/skills/team-style")
    # v2: nothing is edited, so there is nothing to push (pull restores the copy)
    result = fw.push_skills(project, confirm=True)
    assert "team-style" not in statuses(result)
    assert result["message"] == "Skills pushed: nothing to push"
    # Edited beside the missing copy: failed, with the copy hint
    claude = project / ".claude/skills/team-style/SKILL.md"
    claude.write_text(claude.read_text() + "Edit\n")
    result = fw.push_skills(project, confirm=True)
    found = next(s for s in result["skills"] if s["name"] == "team-style")
    assert found["status"] == "failed"
    assert found["reason"].endswith("is missing.")
    assert found["hint"] == 'skills_copy(from_="claude", to="agents", skills=["team-style"])'
    assert cloud.posts == []


def test_push_fails_only_the_skill_with_an_unreadable_copy(
    cloud: Cloud, project: Path, signed_in: None
) -> None:
    pulled(cloud, project)
    (project / ".claude/skills/team-style/draft notes.md").write_text("draft\n")
    (project / ".claude/skills/framework-data/SKILL.md").write_text(
        "---\nname: framework-data\ndescription: x\n---\nMine\n"
    )
    result = fw.push_skills(project, confirm=True)
    found = {s["name"]: s for s in result["skills"]}
    assert found["team-style"]["status"] == "failed"
    assert "Invalid skill path" in found["team-style"]["reason"]
    assert found["framework-data"]["status"] == "pushed"


def test_pull_keeps_a_personal_skill_when_a_new_assistant_folder_gets_the_builtin(
    cloud: Cloud, project: Path, signed_in: None
) -> None:
    pulled(cloud, project)
    mine = "---\nname: framework-data\ndescription: x\n---\nMine\n"
    (project / ".claude/skills/framework-data/SKILL.md").write_text(mine)
    assert statuses(fw.push_skills(project, confirm=True))["framework-data"] == "pushed"

    def owner() -> str:
        return next(e for e in views(project) if ".claude/skills/framework-data" in e["paths"])[
            "skill_id"
        ]

    assert owner() == "own-id"
    (project / ".agents/skills").mkdir(parents=True)
    two_assistants(project)  # A newly enabled assistant
    for overwrite in (False, True):
        result = fw.pull_skills(project, overwrite=overwrite)
        rows = {r["paths"][0]: r for r in result["skills"] if r["name"] == "framework-data"}
        # v2: the pushed copy is your own skill now, a different identity from the
        # built-in the setup names: kept, with the call that retargets it. The new folder
        # has no copy of either, so it gets the setup's built-in.
        kept = rows[".claude/skills/framework-data"]
        assert kept["status"] == "kept" and kept["reason_code"] == "other_identity"
        # The retarget hint only while the built-in isn't tracked yet: once the new
        # folder holds it, skills_move() would refuse an identity the lock tracks
        assert kept["hint"] == (
            None
            if overwrite
            else 'skills_move(".claude/skills/framework-data", builtin="framework-data", '
            'variant="python")'
        )
        assert rows[".agents/skills/framework-data"]["status"] in ("added", "current")
        assert skill_text(project, "framework-data") == mine
        assert owner() == "own-id"
    # Unedited built-ins still go to the new folder
    assert (project / ".agents/skills/framework-workflow/SKILL.md").is_file()


def test_pull_keeps_a_locally_edited_skill_out_of_a_new_assistant_folder(
    cloud: Cloud, project: Path
) -> None:
    fw.pull_skills(project)
    (project / ".claude/skills/framework-data/SKILL.md").write_text("my notes\n")
    (project / ".agents/skills").mkdir(parents=True)
    two_assistants(project)  # A newly enabled assistant
    result = fw.pull_skills(project)
    assert statuses(result)["framework-data"] == "diverged"
    assert not (project / ".agents/skills/framework-data").exists()


def test_installers_never_replace_a_cloud_identity_or_go_back_a_version(
    cloud: Cloud, project: Path
) -> None:
    from framework.ai import _shipped
    from tests.conftest import install_skill

    fw.pull_skills(project)
    team = project / ".claude/skills/team-style/SKILL.md"
    team.write_text(team.read_text() + "Version five\n")
    own(project, ".claude/skills/team-style", "team-id", 5)
    data = project / ".claude/skills/framework-data/SKILL.md"
    data.write_text(data.read_text() + "Mine\n")
    own(project, ".claude/skills/framework-data", "own-id", 1)
    # The setup names v1: older than the project's v5, so never written, even with overwrite
    result = fw.pull_skills(project, overwrite=True)
    assert statuses(result)["team-style"] == "current"
    assert "Version five" in team.read_text()
    assert lock_entry(project, ".claude/skills/team-style")["version"] == 5
    # The package's copy never replaces a copy tracked as another skill
    assert not install_skill(
        _shipped("framework-data"), project, ".claude/skills/framework-data", force=True
    )
    assert "Mine" in data.read_text()
    assert lock_entry(project, ".claude/skills/framework-data")["skill_id"] == "own-id"


def test_overwrite_replaces_an_edited_builtin_in_every_folder_including_a_new_one(
    cloud: Cloud, project: Path
) -> None:
    fw.pull_skills(project)
    (project / ".claude/skills/framework-data/SKILL.md").write_text("my notes\n")
    (project / ".agents/skills").mkdir(parents=True)
    two_assistants(project)  # A newly enabled assistant
    result = fw.pull_skills(project, overwrite=True)
    assert statuses(result)["framework-data"] == "replaced"
    assert [s for s in result["skills"] if s["status"] == "diverged"] == []
    claude = (project / ".claude/skills/framework-data/SKILL.md").read_text()
    assert claude != "my notes\n"
    assert (project / ".agents/skills/framework-data/SKILL.md").read_text() == claude
    entry = next(e for e in views(project) if ".claude/skills/framework-data" in e["paths"])
    assert ".agents/skills/framework-data" in entry["paths"]
    assert entry["skill_id"] is None
    # An unedited Cloud skill named by the setup at the same version fills the new folder too
    assert (project / ".agents/skills/team-style/SKILL.md").read_text() == skill_text(
        project, "team-style"
    )
    assert statuses(fw.pull_skills(project))["framework-data"] == "current"


# Round 6


def lock_entry(project: Path, path: str) -> dict[str, Any]:
    return next(e for e in views(project) if path in e["paths"])


def own(project: Path, path: str, skill_id: str, version: int) -> None:
    """Tie a tracked copy's entry to a Cloud skill at a version, as a push or a Cloud pull
    would: its base and every copy's record become the copy's current hash."""
    from framework._skill_plan import after_push
    from framework.skill_sync import _save, scan

    state = _load(project)
    key = next(k for k, e in state["skills"].items() if path in e["copies"])
    digest = bundle_hash(scan(project / path))
    response = {"skill_id": skill_id, "version": version, "hash": digest, "server": SERVER}
    _save(project, after_push(state, key, response))


def test_inline_setup_content_never_replaces_a_personal_skill(
    cloud: Cloud, project: Path, capsys: Any
) -> None:
    fw.pull_skills(project)
    data = project / ".claude/skills/framework-data/SKILL.md"
    data.write_text("---\nname: framework-data\ndescription: x\n---\nMine\n")
    own(project, ".claude/skills/framework-data", "own-id", 3)
    # Tied to another skill than the setup's: kept, never reported as current
    assert statuses(fw.pull_skills(project, overwrite=True))["framework-data"] == "kept"
    data.write_text(data.read_text() + "Edited since the push\n")
    cloud.skills = {
        "framework-data": {"content": "---\nname: framework-data\ndescription: y\n---\nTeam\n"}
    }
    capsys.readouterr()
    for overwrite in (False, True):
        result = fw.pull_skills(project, overwrite=overwrite)
        found = next(s for s in result["skills"] if s["name"] == "framework-data")
        assert found["status"] == "kept" and found["hint_key"] == "retarget"
        assert found["reason"] == (
            ".claude/skills/framework-data belongs to a different skill (own-id), so pull left "
            "it alone."
        )
        assert "Mine" in data.read_text()
        assert lock_entry(project, ".claude/skills/framework-data")["skill_id"] == "own-id"
    err = capsys.readouterr().err
    assert "kept: framework-data. .claude/skills/framework-data belongs to a different skill" in err
    assert "overwrite=True) replaces" not in err


def test_every_copy_deleted_push_says_missing_and_pull_restores(
    cloud: Cloud, project: Path, signed_in: None, capsys: Any
) -> None:
    pulled(cloud, project)
    shutil.rmtree(project / ".claude/skills/team-style")
    pushed = fw.push_skills(project, confirm=True)
    assert statuses(pushed)["team-style"] == "missing"
    assert pushed["message"] == "Skills pushed: 1 missing (every copy gone)"
    assert "missing: team-style. Every tracked copy is gone." in capsys.readouterr().err
    result = fw.pull_skills(project)
    assert statuses(result)["team-style"] == "restored"
    assert result["counts"]["restored"] == 1 and result["counts"]["added"] == 0
    assert "Version one" in skill_text(project, "team-style")


def test_an_untracked_edited_copy_keeps_the_shipped_text_out_of_a_new_folder(
    cloud: Cloud, project: Path
) -> None:
    cloud.offline = True
    mine = project / ".claude/skills/framework-data/SKILL.md"
    mine.parent.mkdir(parents=True)
    mine.write_text("my own notes\n")
    (project / ".agents/skills").mkdir(parents=True)
    two_assistants(project)  # A newly enabled assistant
    result = fw.pull_skills(project)
    found = next(s for s in result["skills"] if s["name"] == "framework-data")
    assert found["status"] == "diverged"
    assert 'skills_copy(from_="claude", to="agents", skills=["framework-data"])' in found["hint"]
    assert not (project / ".agents/skills/framework-data").exists()
    assert mine.read_text() == "my own notes\n"
    # With overwrite, the setup's copy replaces it everywhere
    replaced = fw.pull_skills(project, overwrite=True)
    assert statuses(replaced)["framework-data"] == "replaced"
    assert (project / ".agents/skills/framework-data/SKILL.md").read_text() == mine.read_text()


@pytest.mark.parametrize("overwrite", [False, True])
def test_a_package_fallback_never_replaces_cloud_content(
    cloud: Cloud, project: Path, overwrite: bool
) -> None:
    fw.pull_skills(project)
    data = project / ".claude/skills/framework-data/SKILL.md"
    data.write_text("---\nname: framework-data\ndescription: x\n---\nCloud v7\n")
    own(project, ".claude/skills/framework-data", "framework:framework-data", 7)
    before = lock_entry(project, ".claude/skills/framework-data")
    cloud.offline = True
    for dry_run in (True, False):
        result = fw.pull_skills(project, overwrite=overwrite, dry_run=dry_run)
        found = next(s for s in result["skills"] if s["name"] == "framework-data")
        assert found["status"] == "unavailable"
        assert "wasn't reached" in found["reason"]
    assert "Cloud v7" in data.read_text()
    assert lock_entry(project, ".claude/skills/framework-data") == before


def test_signed_out_pull_of_a_newer_identical_version_agrees_with_its_dry_run(
    cloud: Cloud, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FW_CLOUD_TOKEN", "fw_user")
    same = cloud_entry(1, "Version one")
    cloud.heads["team-id"] = {
        "skill_id": "team-id",
        "version": 2,
        "bundle": same["bundle"],
        "hash": same["sync"]["hash"],
    }
    fw.pull_skills(project)
    assert lock_entry(project, ".claude/skills/team-style")["version"] == 2
    monkeypatch.delenv("FW_CLOUD_TOKEN")
    dry = fw.pull_skills(project, dry_run=True)
    real = fw.pull_skills(project)
    assert statuses(dry)["team-style"] == statuses(real)["team-style"] == "current"
    assert lock_entry(project, ".claude/skills/team-style")["version"] == 2


def test_a_new_path_joins_the_skills_one_lock_entry(cloud: Cloud, project: Path) -> None:
    fw.pull_skills(project)
    (project / ".agents/skills").mkdir(parents=True)
    two_assistants(project)  # A newly enabled assistant
    fw.pull_skills(project)
    for name in ("team-style", "framework-data"):
        entries = [e for e in views(project) if any(Path(p).name == name for p in e["paths"])]
        assert len(entries) == 1
        assert sorted(entries[0]["paths"]) == [f".agents/skills/{name}", f".claude/skills/{name}"]


def test_package_installers_decline_any_cloud_identity(cloud: Cloud, project: Path) -> None:
    from framework.ai import _shipped
    from tests.conftest import install_skill

    fw.pull_skills(project)
    path = ".claude/skills/framework-data"
    (project / path / "SKILL.md").write_text("---\nname: framework-data\ndescription: x\n---\nv7\n")
    own(project, path, "framework:framework-data", 7)
    lock = (project / ".framework/skills.lock.json").read_text()
    # Last updated from Cloud: the package's copy is kept out, unedited or edited
    assert not install_skill(_shipped("framework-data"), project, path, force=True)
    (project / path / "SKILL.md").write_text("edited\n")
    assert not install_skill(_shipped("framework-data"), project, path, force=True)
    assert (project / path / "SKILL.md").read_text() == "edited\n"
    assert lock_entry(project, path)["skill_id"] == "framework:framework-data"
    assert (project / ".framework/skills.lock.json").read_text() == lock


@pytest.mark.parametrize("newer", [False, True])
def test_overwrite_resets_your_cloud_skill_to_its_own_cloud_version(
    cloud: Cloud, project: Path, newer: bool, capsys: Any
) -> None:
    fw.pull_skills(project)
    mine = project / ".claude/skills/team-style/SKILL.md"
    mine.write_text(mine.read_text() + "My edit\n")
    if newer:
        cloud.skills = {"team-style": cloud_entry(2, "Version two")}
    capsys.readouterr()
    # Without overwrite: kept, and the message says what overwrite does
    kept = fw.pull_skills(project)
    assert statuses(kept)["team-style"] == "edited"
    assert "My edit" in mine.read_text()
    hint = (
        "edited: team-style. .claude/skills/team-style has local edits, which were kept. "
        "pull_skills(overwrite=True) replaces them with the Cloud version."
    )
    assert hint in capsys.readouterr().err
    # Run the hint: the same skill's Cloud version (same or newer), after a backup
    replaced = fw.pull_skills(project, overwrite=True)
    assert statuses(replaced)["team-style"] == "replaced"
    text = mine.read_text()
    assert "My edit" not in text
    assert ("Version two" if newer else "Version one") in text
    backups = list((project / ".framework/skills-backups").glob("*/SKILL.md"))
    assert any("My edit" in b.read_text() for b in backups)
    assert lock_entry(project, ".claude/skills/team-style")["skill_id"] == "team-id"


# Round 7


def entries_for(project: Path, name: str) -> list[dict[str, Any]]:
    return [e for e in views(project) if any(Path(p).name == name for p in e["paths"])]


def test_identical_bytes_never_switch_a_copys_identity(cloud: Cloud, project: Path) -> None:
    from framework.ai import _shipped
    from framework.skill_sync import _source_files, validate

    fw.pull_skills(project)
    path = ".claude/skills/framework-data"
    assert lock_entry(project, path)["skill_id"] is None
    lock = (project / ".framework/skills.lock.json").read_text()
    before = (project / path / "SKILL.md").stat().st_mtime_ns
    files = validate(_source_files(_shipped("framework-data")))
    cloud.skills = {
        "framework-data": {
            "content": base64.b64decode(files[0]["content"]).decode(),
            "bundle": files,
            "sync": {
                "skill_id": "own-id",
                "version": 1,
                "hash": bundle_hash(files),
                "variant": "python",
                "server": SERVER,
            },
        }
    }
    # v2: only push and skills_move() change an identity. The copy is kept, with the call
    # that retargets it, even with the same bytes.
    result = fw.pull_skills(project)
    found = next(s for s in result["skills"] if s["name"] == "framework-data")
    assert found["status"] == "kept" and found["reason_code"] == "other_identity"
    assert found["hint"] == (
        'skills_move(".claude/skills/framework-data", skill_id="own-id", variant="python")'
    )
    assert (project / ".framework/skills.lock.json").read_text() == lock
    assert (project / path / "SKILL.md").stat().st_mtime_ns == before
    assert not (project / ".framework/skills-backups").exists()


def test_a_newer_version_with_the_same_bytes_moves_the_lock_forward(
    cloud: Cloud, project: Path
) -> None:
    fw.pull_skills(project)
    cloud.skills = {"team-style": cloud_entry(2, "Version one")}  # v2, same files as v1
    assert statuses(fw.pull_skills(project))["team-style"] == "current"
    assert lock_entry(project, ".claude/skills/team-style")["version"] == 2
    # Never back: the setup going back to v1 leaves the lock at v2
    cloud.skills = {"team-style": cloud_entry(1, "Version one")}
    assert statuses(fw.pull_skills(project))["team-style"] == "current"
    assert lock_entry(project, ".claude/skills/team-style")["version"] == 2


def test_skills_copy_joins_the_copy_so_sync_finds_one_skill(
    cloud: Cloud, project: Path, signed_in: None
) -> None:
    pulled(cloud, project)
    (project / ".agents/skills").mkdir(parents=True)
    fw.skills_copy(from_="claude", to="agents", skills=["team-style"], project_path=project)
    result = fw.skills_sync(".agents/skills/team-style", project)
    assert result["action"] == "unchanged" and result["skill_id"] == "team-id"
    assert cloud.posts == []
    [entry] = entries_for(project, "team-style")
    assert sorted(entry["paths"]) == [".agents/skills/team-style", ".claude/skills/team-style"]
    # A copy that differs is diverged, not a new skill
    agents = project / ".agents/skills/team-style/SKILL.md"
    agents.write_text(agents.read_text() + "Codex edit\n")
    with pytest.raises(ValueError, match=r"differ\. Pick one and copy it over the other"):
        fw.skills_sync(".agents/skills/team-style", project)
    assert cloud.posts == []


def test_restore_puts_back_every_tracked_path_it_can_and_hints_the_rest(
    cloud: Cloud, project: Path, capsys: Any
) -> None:
    from framework.skill_sync import _save

    (project / ".agents/skills").mkdir(parents=True)
    two_assistants(project)  # A newly enabled assistant
    fw.pull_skills(project)
    state = _load(project)
    key = next(k for k, e in state["skills"].items() if ".claude/skills/team-style" in e["copies"])
    copies = state["skills"][key]["copies"]
    copies["team/skills/team-style"] = dict(copies[".claude/skills/team-style"])
    _save(project, state)
    (project / "team/skills").mkdir(parents=True)
    # Both assistant copies gone, and .agents/skills (Codex disabled again) with them
    (project / "settings.yml").write_text(
        "default:\n  project_type: project\n  ai: {assistants: [claude]}\n", encoding="utf-8"
    )
    shutil.rmtree(project / ".claude/skills/team-style")
    shutil.rmtree(project / ".agents")
    capsys.readouterr()
    result = fw.pull_skills(project)
    found = next(s for s in result["skills"] if s["name"] == "team-style")
    restored = [s["path"] for s in result["steps"] if s["status"] == "restored"]
    assert restored == [".claude/skills/team-style", "team/skills/team-style"]
    assert (project / "team/skills/team-style/SKILL.md").read_text() == skill_text(
        project, "team-style"
    )
    # v2: the row's status is its worst copy's: the copy whose folder is gone is missing
    assert found["status"] == "missing" and found["reason_code"] == "folder_gone"
    assert found["reason"] == ".agents/skills/team-style is tracked, and its folder is gone."
    assert "missing: team-style. .agents/skills/team-style is tracked" in capsys.readouterr().err


def test_a_split_lock_from_an_older_release_is_merged(
    cloud: Cloud, project: Path, signed_in: None
) -> None:
    from framework.skill_sync import _save

    two_assistants(project)
    pulled(cloud, project)
    # As older releases wrote it (v1): one Cloud skill, two entries
    entry = team_entry(project)
    old = {
        "variant": "python",
        "server": SERVER,
        "skill_id": "team-id",
        "version": entry["version"],
        "hash": entry["hash"],
    }
    state = {
        "schema": 1,
        "skills": {
            f"account:{SERVER}:team-id:python": {**old, "paths": [".claude/skills/team-style"]},
            "split": {**old, "paths": [".agents/skills/team-style"]},
        },
    }
    path = project / ".framework/skills.lock.json"
    path.write_text(json.dumps(state))
    [merged] = entries_for(project, "team-style")
    assert sorted(merged["paths"]) == [".agents/skills/team-style", ".claude/skills/team-style"]
    # A one-sided edit is diverged: never pushed
    claude = project / ".claude/skills/team-style/SKILL.md"
    claude.write_text(claude.read_text() + "One side\n")
    pushed = fw.push_skills(project, confirm=True)
    assert statuses(pushed)["team-style"] == "failed"
    assert cloud.posts == []
    assert statuses(fw.pull_skills(project))["team-style"] == "diverged"
    _save(project, _load(project))
    assert len(entries_for(project, "team-style")) == 1


def test_overwrite_updates_an_edited_cloud_skill_in_both_folders(
    cloud: Cloud, project: Path
) -> None:
    two_assistants(project)
    fw.pull_skills(project)
    for folder in (".claude/skills", ".agents/skills"):
        copy = project / folder / "team-style/SKILL.md"
        copy.write_text(copy.read_text() + "Same edit\n")
    cloud.skills = {"team-style": cloud_entry(2, "Version two")}
    result = fw.pull_skills(project, overwrite=True)
    assert statuses(result)["team-style"] == "replaced"
    for folder in (".claude/skills", ".agents/skills"):
        text = (project / folder / "team-style/SKILL.md").read_text()
        assert "Version two" in text and "Same edit" not in text
    [entry] = entries_for(project, "team-style")
    assert entry["version"] == 2
    assert len(list((project / ".framework/skills-backups").iterdir())) == 2


# Round 8: explicit joins


def run_hint(project: Path, hint: str) -> None:
    """Run the skills_copy() call a hint prints, as a user would paste it."""
    import re

    call = re.search(r"skills_copy\(([^()]*)\)", hint)
    assert call, hint
    eval(f"fw.skills_copy({call.group(1)}, project_path=project)", {"fw": fw, "project": project})


def test_the_printed_hint_joins_the_copy_for_pull_and_push(
    cloud: Cloud, project: Path, signed_in: None
) -> None:
    pulled(cloud, project)
    data = project / ".claude/skills/framework-data/SKILL.md"
    data.write_text("---\nname: framework-data\ndescription: x\n---\nMine\n")
    (project / ".agents/skills").mkdir(parents=True)
    two_assistants(project)  # A newly enabled assistant
    found = next(s for s in fw.pull_skills(project)["skills"] if s["name"] == "framework-data")
    assert found["status"] == "diverged"
    run_hint(project, found["hint"])
    [entry] = entries_for(project, "framework-data")
    assert sorted(entry["paths"]) == [
        ".agents/skills/framework-data",
        ".claude/skills/framework-data",
    ]
    # Now one skill: pull keeps both edited copies, and a one-sided edit isn't pushed
    assert statuses(fw.pull_skills(project))["framework-data"] == "edited"
    data.write_text(data.read_text() + "Claude only\n")
    pushed = fw.push_skills(project, confirm=True)
    assert statuses(pushed)["framework-data"] == "failed"
    assert "untracked" not in statuses(pushed).values()
    assert cloud.posts == []


def test_a_copy_in_a_custom_folder_moves_to_the_new_version(cloud: Cloud, project: Path) -> None:
    fw.pull_skills(project)
    fw.skills_copy(
        from_dir=".claude/skills",
        to_dir="team/skills",
        skills=["team-style"],
        project_path=project,
    )
    [entry] = entries_for(project, "team-style")
    assert "team/skills/team-style" in entry["paths"]
    cloud.skills = {"team-style": cloud_entry(2, "Version two")}
    result = fw.pull_skills(project)
    assert statuses(result)["team-style"] == "updated"
    assert (
        "team/skills/team-style"
        in next(s for s in result["skills"] if s["name"] == "team-style")["paths"]
    )
    for path in (".claude/skills", "team/skills"):
        assert "Version two" in (project / path / "team-style/SKILL.md").read_text()
    assert lock_entry(project, "team/skills/team-style")["version"] == 2


def test_a_standalone_skill_with_the_same_name_keeps_its_own_identity(
    cloud: Cloud, project: Path, signed_in: None
) -> None:
    pulled(cloud, project)
    lab = project / "lab/skills/team-style"
    lab.mkdir(parents=True)
    (lab / "SKILL.md").write_text("---\nname: team-style\ndescription: lab\n---\nLab's own\n")
    result = fw.skills_sync("lab/skills/team-style", project)
    assert result["action"] == "push" and result["skill_id"] == "own-id"
    assert lock_entry(project, ".claude/skills/team-style")["paths"] == [
        ".claude/skills/team-style"
    ]
    assert lock_entry(project, "lab/skills/team-style")["skill_id"] == "own-id"
    # Pull leaves the standalone skill alone, and gives no hint that would overwrite it
    cloud.skills = {"team-style": cloud_entry(2, "Version two")}
    pulled_now = fw.pull_skills(project)
    assert statuses(pulled_now)["team-style"] == "updated"
    assert "Lab's own" in (lab / "SKILL.md").read_text()
    assert all("lab/skills" not in (s.get("hint") or "") for s in pulled_now["skills"])


def test_a_renamed_copy_gets_an_explanation_not_a_command(cloud: Cloud, project: Path) -> None:
    from framework.skill_sync import _diverged, _save

    (project / ".agents/skills").mkdir(parents=True)
    two_assistants(project)  # A newly enabled assistant
    fw.pull_skills(project)
    # An adoption under another folder name: one entry, two names, the other one absent
    state = _load(project)
    key = next(k for k, e in state["skills"].items() if ".claude/skills/team-style" in e["copies"])
    copies = state["skills"][key]["copies"]
    copies[".agents/skills/house-style"] = copies.pop(".agents/skills/team-style")
    _save(project, state)
    shutil.rmtree(project / ".agents/skills/team-style")
    # Edited beside the absent copy: diverged (unedited, it would just be restored)
    claude = project / ".claude/skills/team-style/SKILL.md"
    claude.write_text(claude.read_text() + "Edit\n")
    found = next(s for s in fw.pull_skills(project)["skills"] if s["name"] == "team-style")
    assert found["status"] == "diverged"
    assert found["hint"] is None
    assert "different names" in row_text(found)
    differ = _diverged([".claude/skills/x", ".agents/skills/y"], ["a", "b"])
    assert "skills_copy(from" not in differ and "different names" in differ


def test_a_dry_run_copy_records_no_join(cloud: Cloud, project: Path) -> None:
    fw.pull_skills(project)
    lock = (project / ".framework/skills.lock.json").read_text()
    fw.skills_copy(
        from_dir=".claude/skills", to_dir="team/skills", dry_run=True, project_path=project
    )
    assert (project / ".framework/skills.lock.json").read_text() == lock


# R round-8 checks


def test_two_independent_skills_with_one_name_are_not_diverged(
    cloud: Cloud, project: Path, signed_in: None
) -> None:
    pulled(cloud, project)
    theirs = project / ".agents/skills/team-style"
    theirs.mkdir(parents=True)
    (theirs / "SKILL.md").write_text("---\nname: team-style\ndescription: x\n---\nCodex's own\n")
    assert fw.skills_sync(".agents/skills/team-style", project)["skill_id"] == "own-id"
    two_assistants(project)
    result = fw.pull_skills(project)
    found = next(s for s in result["skills"] if s["name"] == "team-style")
    assert found["status"] != "diverged"
    assert "skills_copy(from" not in (found.get("hint") or "")
    assert "Codex's own" in (theirs / "SKILL.md").read_text()
    assert len(entries_for(project, "team-style")) == 2
    # Each entry stands alone: an edit to Claude's pushes Claude's skill only
    claude = project / ".claude/skills/team-style/SKILL.md"
    claude.write_text(claude.read_text() + "Claude edit\n")
    before = len(cloud.posts)
    pushed = fw.push_skills(project, confirm=True)
    assert statuses(pushed)["team-style"] == "pushed"
    assert [p["skill_id"] for p in cloud.posts[before:]] == ["team-id"]


def test_variants_of_one_cloud_skill_never_merge() -> None:
    from framework._skill_plan import migrate

    base = {"skill_id": "team-id", "server": SERVER, "hash": "h", "version": 1}
    state = {
        "schema": 1,
        "skills": {
            "py": {**base, "variant": "python", "paths": [".claude/skills/x"]},
            "r": {**base, "variant": "r", "paths": [".agents/skills/x"]},
            "other-server": {
                **base,
                "server": "https://other.example",
                "variant": "python",
                "paths": ["lab/skills/x"],
            },
        },
    }
    assert sorted(migrate(state)["skills"]) == [
        f"{SERVER}/team-id@python",
        f"{SERVER}/team-id@r",
        "https://other.example/team-id@python",
    ]


def test_switching_assistants_still_moves_every_tracked_copy(cloud: Cloud, project: Path) -> None:
    fw.pull_skills(project)
    fw.skills_copy(
        from_dir=".claude/skills", to_dir="team/skills", skills=["team-style"], project_path=project
    )
    # Codex only from now on; the Claude and custom copies are still tracked
    (project / "settings.yml").write_text(
        "default:\n  project_type: project\n  ai: {assistants: [codex]}\n", encoding="utf-8"
    )
    cloud.skills = {"team-style": cloud_entry(3, "Version three")}
    result = fw.pull_skills(project)
    found = next(s for s in result["skills"] if s["name"] == "team-style")
    assert found["status"] in ("updated", "added")
    for folder in (".claude/skills", "team/skills", ".agents/skills"):
        assert "Version three" in (project / folder / "team-style/SKILL.md").read_text()
    [entry] = entries_for(project, "team-style")
    assert entry["version"] == 3


def test_a_case_alias_source_still_joins_the_entry(cloud: Cloud, project: Path) -> None:
    fw.pull_skills(project)
    if not (project / ".CLAUDE/skills").exists():
        pytest.skip("case-sensitive file system")
    fw.skills_copy(
        from_dir=".Claude/skills", to_dir="team/skills", skills=["team-style"], project_path=project
    )
    [entry] = entries_for(project, "team-style")
    assert sorted(entry["paths"]) == [".claude/skills/team-style", "team/skills/team-style"]
    # A target alias of a tracked path keeps the lock's spelling too
    (project / ".agents/skills").mkdir(parents=True)
    fw.skills_copy(from_="claude", to="agents", skills=["team-style"], project_path=project)
    fw.skills_copy(
        from_dir=".CLAUDE/skills",
        to_dir=".AGENTS/skills",
        skills=["team-style"],
        project_path=project,
    )
    [entry] = entries_for(project, "team-style")
    assert sorted(entry["paths"]) == [
        ".agents/skills/team-style",
        ".claude/skills/team-style",
        "team/skills/team-style",
    ]


# Round 10: per entry, end to end


def independent_row(row: dict[str, Any]) -> bool:
    """Whether a row is an independent entry's: your own skill (own-id) or a folder
    skills_sync() tracks, never the setup's skill."""
    entry = str(row.get("entry") or "")
    return "own-id" in entry or entry.startswith("local:")


def local_entry(path: str, record: str = "x") -> dict[str, Any]:
    """A v2 entry for a folder skills_sync() started tracking, not yet on Cloud."""
    return {
        "identity": {"kind": "local", "variant": "python"},
        "base": None,
        "known": [],
        "copies": {path: {"hash": record}},
    }


def setup_row(result: dict[str, Any], name: str) -> dict[str, Any]:
    """The setup's skill's row (not an independent entry's, nor an untracked folder's)."""
    return next(
        s
        for s in result["skills"]
        if s["name"] == name and not independent_row(s) and s["status"] != "untracked"
    )


def test_a_hand_made_untracked_copy_is_left_alone(cloud: Cloud, project: Path) -> None:
    fw.pull_skills(project)
    # By hand, not skills_copy(): the lock doesn't track it, even with the same bytes
    shutil.copytree(project / ".claude/skills/team-style", project / ".agents/skills/team-style")
    two_assistants(project)
    cloud.skills = {"team-style": cloud_entry(2, "Version two")}
    result = fw.pull_skills(project)
    assert setup_row(result, "team-style")["status"] == "updated"
    assert "Version two" in skill_text(project, "team-style")
    assert "Version one" in (project / ".agents/skills/team-style/SKILL.md").read_text()
    [entry] = entries_for(project, "team-style")
    assert entry["paths"] == [".claude/skills/team-style"]


def independent(project: Path, folder: str = ".agents/skills") -> Path:
    """An independent skill named team-style, your own, tracked on its own."""
    theirs = project / folder / "team-style"
    theirs.mkdir(parents=True)
    (theirs / "SKILL.md").write_text("---\nname: team-style\ndescription: x\n---\nTheirs\n")
    fw.skills_sync(f"{folder}/team-style", project)
    return theirs


def test_a_diverged_independent_pair_never_blocks_the_setups_skill(
    cloud: Cloud, project: Path, signed_in: None
) -> None:
    pulled(cloud, project)
    lab = independent(project, "lab/skills")
    fw.skills_copy(
        from_dir="lab/skills", to_dir="lab2/skills", skills=["team-style"], project_path=project
    )
    (lab / "SKILL.md").write_text(lab.joinpath("SKILL.md").read_text() + "Lab edit\n")
    cloud.skills = {"team-style": cloud_entry(2, "Version two")}
    cloud.heads["team-id"] = {
        **cloud.heads["team-id"],
        "version": 2,
        "bundle": cloud.skills["team-style"]["bundle"],
        "hash": cloud.skills["team-style"]["sync"]["hash"],
    }
    result = fw.pull_skills(project)
    assert setup_row(result, "team-style")["status"] == "updated"
    assert "Version two" in skill_text(project, "team-style")
    lab_row = next(s for s in result["skills"] if independent_row(s))
    assert lab_row["status"] == "diverged"
    assert sorted(lab_row["paths"]) == ["lab/skills/team-style", "lab2/skills/team-style"]
    assert "Lab edit" in (lab / "SKILL.md").read_text()


@pytest.mark.parametrize("case", ["delete-one", "diverge-one", "update-the-other"])
def test_two_independent_skills_with_one_name(
    cloud: Cloud, project: Path, signed_in: None, case: str
) -> None:
    pulled(cloud, project)
    theirs = independent(project)
    two_assistants(project)
    if case == "delete-one":
        shutil.rmtree(theirs)
    elif case == "diverge-one":
        fw.skills_copy(
            from_dir=".agents/skills",
            to_dir="lab/skills",
            skills=["team-style"],
            project_path=project,
        )
        (theirs / "SKILL.md").write_text("changed\n")
    cloud.skills = {"team-style": cloud_entry(2, "Version two")}
    cloud.heads["team-id"] = {
        **cloud.heads["team-id"],
        "version": 2,
        "bundle": cloud.skills["team-style"]["bundle"],
        "hash": cloud.skills["team-style"]["sync"]["hash"],
    }
    result = fw.pull_skills(project)
    row = setup_row(result, "team-style")
    assert row["status"] == "updated"
    assert "skills_copy(from" not in (row.get("hint") or "")
    assert "Version two" in skill_text(project, "team-style")
    if case == "delete-one":
        # v2: signed in, restored from its own Cloud skill, never the setup's
        assert "Theirs" in (theirs / "SKILL.md").read_text()
    else:
        assert "Version two" not in (theirs / "SKILL.md").read_text()
    if case == "diverge-one":
        assert any(independent_row(s) and s["status"] == "diverged" for s in result["skills"])
    assert lock_entry(project, ".claude/skills/team-style")["version"] == 2


def test_an_unreadable_entry_fails_alone(cloud: Cloud, project: Path) -> None:
    from framework.skill_sync import _save

    fw.pull_skills(project)
    (project / "lab/skills/broken").mkdir(parents=True)
    (project / "lab/skills/broken/notes.md").write_text("no SKILL.md here\n")
    state = _load(project)
    state["skills"]["local:lab/skills/broken"] = local_entry("lab/skills/broken")
    _save(project, state)
    cloud.skills = {"team-style": cloud_entry(2, "Version two")}
    result = fw.pull_skills(project)
    broken = next(s for s in result["skills"] if s.get("entry") == "local:lab/skills/broken")
    assert broken["status"] == "failed" and "SKILL.md" in broken["reason"]
    assert setup_row(result, "team-style")["status"] == "updated"
    # The setup's own skill failing to read fails just that skill
    (project / ".claude/skills/framework-data/SKILL.md").unlink()
    result = fw.pull_skills(project)
    assert setup_row(result, "framework-data")["status"] == "failed"
    assert setup_row(result, "framework-workflow")["status"] == "current"


def test_a_case_alias_target_is_recorded_as_spelled_on_disk(
    cloud: Cloud, project: Path, signed_in: None
) -> None:
    pulled(cloud, project)
    (project / ".agents/skills").mkdir(parents=True)
    if not (project / ".AGENTS/skills").exists():
        pytest.skip("case-sensitive file system")
    result = fw.skills_copy(
        from_dir=".CLAUDE/skills",
        to_dir=".AGENTS/skills",
        skills=["team-style"],
        project_path=project,
    )
    assert result["from"] == ".claude/skills" and result["to"] == [".agents/skills"]
    assert result["skills"][0]["to"] == ".agents/skills/team-style"
    [entry] = entries_for(project, "team-style")
    assert sorted(entry["paths"]) == [".agents/skills/team-style", ".claude/skills/team-style"]
    two_assistants(project)
    fw.pull_skills(project)
    [entry] = entries_for(project, "team-style")
    assert len(entry["paths"]) == 2
    status = fw.skills_sync(".AGENTS/skills/team-style", project, direction="status")
    assert status["action"] == "unchanged"


def test_builtin_origins_merge_only_within_a_variant() -> None:
    from framework._skill_plan import migrate

    origin = {"skill_id": "framework:framework-data", "hash": "h"}
    state = {
        "schema": 1,
        "skills": {
            "py": {
                "origin": origin,
                "variant": "python",
                "paths": [".claude/skills/framework-data"],
            },
            "r": {"origin": origin, "variant": "r", "paths": [".agents/skills/framework-data"]},
            "py2": {"origin": origin, "variant": "python", "paths": ["lab/framework-data"]},
        },
    }
    merged = migrate(state)["skills"]
    assert sorted(merged) == ["framework:framework-data@python", "framework:framework-data@r"]
    assert sorted(merged["framework:framework-data@python"]["copies"]) == [
        ".claude/skills/framework-data",
        "lab/framework-data",
    ]


# Round 11


def case_insensitive(project: Path) -> bool:
    probe = project / "Case-Probe"
    probe.mkdir(exist_ok=True)
    found = (project / "case-probe").exists()
    probe.rmdir()
    return found


def test_an_alias_spelled_lock_path_is_the_same_skill_for_sync(
    cloud: Cloud, project: Path, signed_in: None
) -> None:
    if not case_insensitive(project):
        pytest.skip("case-sensitive file system")
    pulled(cloud, project)
    path = project / ".framework/skills.lock.json"
    path.write_text(
        path.read_text().replace(".claude/skills/team-style", ".CLAUDE/skills/team-style")
    )
    result = fw.skills_sync(".claude/skills/team-style", project)
    assert result["action"] == "unchanged" and result["skill_id"] == "team-id"
    assert cloud.posts == []
    [entry] = entries_for(project, "team-style")
    assert entry["paths"] == [".claude/skills/team-style"]


def test_a_tracked_folder_gone_after_a_switch_is_missing_and_the_copy_untracked(
    cloud: Cloud, project: Path, capsys: Any
) -> None:
    fw.pull_skills(project)
    # .claude renamed to .agents, and the project now uses Codex only
    (project / ".claude").rename(project / ".agents")
    (project / "settings.yml").write_text(
        "default:\n  project_type: project\n  ai: {assistants: [codex]}\n", encoding="utf-8"
    )
    capsys.readouterr()
    result = fw.pull_skills(project)
    rows = [s for s in result["skills"] if s["name"] == "team-style"]
    assert {r["status"] for r in rows} == {"missing", "untracked"}
    # v2: both rows give the skills_move() call that records the move
    move = 'skills_move(".claude/skills/team-style", ".agents/skills/team-style")'
    missing = next(r for r in rows if r["status"] == "missing")
    assert missing["paths"] == [".claude/skills/team-style"]
    assert missing["reason"] == ".claude/skills/team-style is tracked, and its folder is gone."
    assert missing["hint"] == move
    kept = next(r for r in rows if r["status"] == "untracked")
    assert kept["paths"] == [".agents/skills/team-style"]
    assert kept["reason"] == ".agents/skills/team-style isn't tracked, so pull left it alone."
    assert kept["hint"] == move
    assert "Version one" in (project / ".agents/skills/team-style/SKILL.md").read_text()
    err = capsys.readouterr().err
    assert "missing: team-style. .claude/skills/team-style is tracked" in err
    assert "untracked: team-style. .agents/skills/team-style isn't tracked" in err
    assert f"If it was moved by hand, record the move: {move}" in err
    # The hint does what it says: the lock follows the folder, and pull finds it current
    fw.skills_move(".claude/skills/team-style", ".agents/skills/team-style", project_path=project)
    [entry] = entries_for(project, "team-style")
    assert entry["paths"] == [".agents/skills/team-style"]
    assert setup_row(fw.pull_skills(project), "team-style")["status"] == "current"


def test_another_cloud_skill_under_the_same_name_is_kept_not_current(
    cloud: Cloud, project: Path, capsys: Any
) -> None:
    fw.pull_skills(project)
    other = cloud_entry(1, "Another account's skill")
    other["sync"] = {**other["sync"], "skill_id": "acct-2"}
    cloud.skills = {"team-style": other}
    capsys.readouterr()
    for overwrite in (False, True):
        result = fw.pull_skills(project, overwrite=overwrite)
        row = setup_row(result, "team-style")
        assert row["status"] == "kept"
        assert row["hint"] == (
            'skills_move(".claude/skills/team-style", skill_id="acct-2", variant="python")'
        )
        assert row["reason"] == (
            ".claude/skills/team-style belongs to a different skill (team-id), so pull left "
            "it alone."
        )
        assert "Version one" in skill_text(project, "team-style")
        assert lock_entry(project, ".claude/skills/team-style")["skill_id"] == "team-id"
    assert "kept: team-style. .claude/skills/team-style belongs to a different skill" in (
        capsys.readouterr().err
    )


def test_a_deleted_personal_fork_is_restored_from_its_own_cloud_skill(
    cloud: Cloud, project: Path, monkeypatch: pytest.MonkeyPatch, signed_in: None
) -> None:
    pulled(cloud, project)
    data = project / ".claude/skills/framework-data/SKILL.md"
    mine = "---\nname: framework-data\ndescription: x\n---\nMine\n"
    data.write_text(mine)
    assert statuses(fw.push_skills(project, confirm=True))["framework-data"] == "pushed"
    shutil.rmtree(data.parent)
    # Signed out: missing, and the hint names what restores it. The folder tracks your
    # skill, so your skill's entry plans it, and the setup's built-in never claims it.
    monkeypatch.delenv("FW_CLOUD_TOKEN")
    result = fw.pull_skills(project)
    [row] = [r for r in result["skills"] if r["name"] == "framework-data"]
    assert row["status"] == "missing"
    assert row["reason"] == "Every tracked copy is gone." and row["skill_id"] == "own-id"
    assert row_text(row) == (
        "Every tracked copy is gone. Its last pushed version is Cloud skill own-id. "
        "After cloud_login(), pull can get it from Cloud."
    )
    assert not data.exists()
    # Signed in: restored from own-id, never from the package
    monkeypatch.setenv("FW_CLOUD_TOKEN", "fw_user")
    rows = fw.pull_skills(project)["skills"]
    [row] = [r for r in rows if r["name"] == "framework-data"]
    assert row["status"] == "restored" and independent_row(row)
    assert data.read_text() == mine
    assert lock_entry(project, ".claude/skills/framework-data")["skill_id"] == "own-id"


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
def test_skills_sync_refuses_a_symlinked_path_as_typed(
    cloud: Cloud, project: Path, signed_in: None
) -> None:
    pulled(cloud, project)
    (project / ".claude/skills/linked").symlink_to(
        project / ".claude/skills/team-style", target_is_directory=True
    )
    typed_paths = [".claude/skills/linked"]
    if case_insensitive(project):
        typed_paths.append(".CLAUDE/skills/linked")
    for typed in typed_paths:
        with pytest.raises(ValueError, match="symlinks"):
            fw.skills_sync(typed, project)
    assert cloud.posts == []


def test_a_renamed_skill_folder_is_updated_never_also_removed(cloud: Cloud, project: Path) -> None:
    if not case_insensitive(project):
        pytest.skip("case-sensitive file system")
    fw.pull_skills(project)
    folder = project / ".claude/skills/team-style"
    folder.rename(project / ".claude/skills/tmp")
    (project / ".claude/skills/tmp").rename(project / ".claude/skills/Team-Style")
    cloud.skills = {"team-style": cloud_entry(2, "Version two")}
    result = fw.pull_skills(project)
    rows = [s for s in result["skills"] if s["name"].casefold() == "team-style"]
    assert [r["status"] for r in rows] == ["updated"]
    assert "Version two" in (project / ".claude/skills/Team-Style/SKILL.md").read_text()


# Round 12


def run_hints(project: Path, hint: str) -> None:
    """Run every skills_copy() call a hint prints, as a user would paste them."""
    import re

    calls = re.findall(r"skills_copy\(([^()]*)\)", hint)
    assert calls, hint
    for call in calls:
        eval(f"fw.skills_copy({call}, project_path=project)", {"fw": fw, "project": project})


def test_a_deleted_skill_outside_the_setup_is_missing_then_restored_by_identity(
    cloud: Cloud, project: Path, signed_in: None, capsys: Any
) -> None:
    pulled(cloud, project)
    lab = independent(project, "lab/skills")
    shutil.rmtree(lab)
    capsys.readouterr()
    pushed = fw.push_skills(project, confirm=True)
    missing = [s for s in pushed["skills"] if s["status"] == "missing"]
    assert [s["paths"] for s in missing] == [["lab/skills/team-style"]]
    err = capsys.readouterr().err
    assert (
        "missing: team-style. Every tracked copy is gone. Its last pushed version is Cloud "
        "skill own-id." in err
    )
    # v2: signed in, pull fetches your skill by its own identity and restores it
    result = fw.pull_skills(project)
    row = next(s for s in result["skills"] if independent_row(s))
    assert row["paths"] == ["lab/skills/team-style"]
    assert row["status"] == "restored"
    assert "Theirs" in (lab / "SKILL.md").read_text()


@pytest.mark.skipif(
    sys.platform == "win32" or os.geteuid() == 0, reason="needs POSIX permissions, not root"
)
def test_an_unreadable_custom_folder_fails_only_its_entry(cloud: Cloud, project: Path) -> None:
    from framework.skill_sync import _save

    fw.pull_skills(project)
    (project / "lab/skills/team-x").mkdir(parents=True)
    (project / "lab/skills/team-x/SKILL.md").write_text("x\n")
    state = _load(project)
    state["skills"]["local:lab/skills/team-x"] = local_entry("lab/skills/team-x")
    _save(project, state)
    (project / "lab").chmod(0)
    try:
        cloud.skills = {"team-style": cloud_entry(2, "Version two")}
        result = fw.pull_skills(project)
    finally:
        (project / "lab").chmod(0o755)
    assert (
        next(s for s in result["skills"] if s.get("entry") == "local:lab/skills/team-x")["status"]
        == "failed"
    )
    assert setup_row(result, "team-style")["status"] == "updated"


def test_a_case_only_rename_with_another_setup_skill_gives_a_row(
    cloud: Cloud, project: Path
) -> None:
    if not case_insensitive(project):
        pytest.skip("case-sensitive file system")
    fw.pull_skills(project)
    (project / ".claude/skills/team-style").rename(project / ".claude/skills/tmp")
    (project / ".claude/skills/tmp").rename(project / ".claude/skills/Team-Style")
    other = cloud_entry(1, "Another account's skill")
    other["sync"] = {**other["sync"], "skill_id": "acct-2"}
    cloud.skills = {"team-style": other}
    row = setup_row(fw.pull_skills(project), "team-style")
    assert row["status"] == "kept"
    assert "Version one" in (project / ".claude/skills/Team-Style/SKILL.md").read_text()


def test_the_kept_hint_for_another_skill_states_the_situation(cloud: Cloud, project: Path) -> None:
    fw.pull_skills(project)
    other = cloud_entry(1, "Another account's skill")
    other["sync"] = {**other["sync"], "skill_id": "acct-2"}
    cloud.skills = {"team-style": other}
    row = setup_row(fw.pull_skills(project), "team-style")
    assert row["hint_key"] == "retarget" and row["hint_args"]["identity"]["skill_id"] == "acct-2"
    assert "belongs to a different skill (team-id), so pull left it alone." in row["reason"]


def test_running_a_three_copy_hint_once_reconciles_every_copy(cloud: Cloud, project: Path) -> None:
    two_assistants(project)
    fw.pull_skills(project)
    fw.skills_copy(
        from_dir=".claude/skills", to_dir="lab/skills", skills=["team-style"], project_path=project
    )
    (project / "lab/skills/team-style/SKILL.md").write_text("lab edit\n")
    row = setup_row(fw.pull_skills(project), "team-style")
    assert row["status"] == "diverged"
    run_hints(project, row["hint"])
    # v2: each copy takes the edited source's record, so all three are one edit, kept
    assert statuses(fw.pull_skills(project))["team-style"] == "edited"
    texts = {
        (project / f / "team-style/SKILL.md").read_text()
        for f in (".claude/skills", ".agents/skills", "lab/skills")
    }
    assert len(texts) == 1


def test_a_case_alias_source_is_never_its_own_default_target(cloud: Cloud, project: Path) -> None:
    if not case_insensitive(project):
        pytest.skip("case-sensitive file system")
    fw.pull_skills(project)
    (project / ".agents/skills").mkdir(parents=True)
    result = fw.skills_copy(from_dir=".Claude/skills", skills=["team-style"], project_path=project)
    assert result["from"] == ".claude/skills" and result["to"] == [".agents/skills"]


def test_a_file_at_a_disabled_assistants_path_doesnt_block_pull(
    cloud: Cloud, project: Path
) -> None:
    (project / "settings.yml").write_text(
        "default:\n  project_type: project\n  ai: {assistants: [codex]}\n", encoding="utf-8"
    )
    (project / ".claude").mkdir()
    (project / ".claude/skills").write_text("not a folder\n")
    result = fw.pull_skills(project)
    assert statuses(result)["team-style"] == "added"
    assert (project / ".agents/skills/team-style/SKILL.md").is_file()


# Cross-language parity fixes


@pytest.mark.parametrize("field", ["variant", "server"])
@pytest.mark.parametrize("overwrite", [False, True])
def test_another_variant_or_server_is_another_skill(
    cloud: Cloud, project: Path, field: str, overwrite: bool
) -> None:
    from framework.skill_sync import _save

    fw.pull_skills(project)
    from framework._skill_plan import key_of

    state = _load(project)
    key = next(k for k, e in state["skills"].items() if ".claude/skills/team-style" in e["copies"])
    mine = {"variant": "r", "server": "https://other.example"}[field]
    entry = state["skills"].pop(key)
    entry["identity"][field] = mine
    state["skills"][key_of(entry["identity"])] = entry
    _save(project, state)
    other = cloud_entry(2, "Version two")
    if field == "variant":
        other["sync"] = {**other["sync"], "variant": "stata"}
    cloud.skills = {"team-style": other}
    row = setup_row(fw.pull_skills(project, overwrite=overwrite), "team-style")
    assert row["status"] == "kept" and row["reason"]
    assert "Version one" in skill_text(project, "team-style")
    assert lock_entry(project, ".claude/skills/team-style")[field] == mine


def test_skills_copy_never_replaces_another_skills_copy(
    cloud: Cloud, project: Path, signed_in: None
) -> None:
    pulled(cloud, project)
    theirs = independent(project)  # .agents/skills/team-style, your own-id skill
    lock = (project / ".framework/skills.lock.json").read_text()
    for dry_run in (True, False):
        with pytest.raises(fw.FrameworkError, match="belongs to another skill"):
            fw.skills_copy(
                from_="claude",
                to="agents",
                skills=["team-style"],
                dry_run=dry_run,
                project_path=project,
            )
    assert "Theirs" in (theirs / "SKILL.md").read_text()
    assert (project / ".framework/skills.lock.json").read_text() == lock


def test_only_enabled_assistants_are_probed(cloud: Cloud, project: Path) -> None:
    from framework.ai import assistant_targets

    folders, _ = assistant_targets({"ai": {"assistants": {"claude": True, "codex": False}}})
    assert folders == [".claude/skills"]
    # Codex is off: its folder, even with an edited copy in it, isn't touched or listed
    mine = project / ".agents/skills/team-style/SKILL.md"
    mine.parent.mkdir(parents=True)
    mine.write_text("hand-made\n")
    result = fw.pull_skills(project)
    assert all(".agents" not in p for s in result["skills"] for p in s["paths"])
    assert mine.read_text() == "hand-made\n"
    assert statuses(result)["team-style"] == "added"


def test_direct_sync_never_reuses_a_key_or_overwrites_an_entry(
    cloud: Cloud, project: Path, signed_in: None
) -> None:
    from framework.skill_sync import _save

    pulled(cloud, project)
    # An unrelated entry whose key is the path about to be synced
    state = _load(project)
    state["skills"]["local:lab/skills/notes"] = local_entry(".claude/skills/team-style-old")
    _save(project, state)
    lab = project / "lab/skills/notes"
    lab.mkdir(parents=True)
    (lab / "SKILL.md").write_text("---\nname: notes\ndescription: x\n---\nNotes\n")
    fw.skills_sync("lab/skills/notes", project)
    skills = _load(project)["skills"]
    assert list(skills["local:lab/skills/notes"]["copies"]) == [".claude/skills/team-style-old"]
    # Pushed as a new skill: tracked under that skill's key
    assert list(skills[f"{SERVER}/own-id@python"]["copies"]) == ["lab/skills/notes"]
    # An explicit skill_id that the lock tracks: the path joins that entry
    shutil.copytree(project / ".claude/skills/team-style", project / "lab/skills/team-style")
    result = fw.skills_sync("lab/skills/team-style", project, skill_id="team-id")
    assert result["skill_id"] == "team-id"
    [entry] = entries_for(project, "team-style")
    assert sorted(entry["paths"]) == [".claude/skills/team-style", "lab/skills/team-style"]


def test_an_edited_cloud_skill_is_never_added_to_a_newly_enabled_folder(
    cloud: Cloud, project: Path
) -> None:
    fw.pull_skills(project)
    mine = project / ".claude/skills/team-style/SKILL.md"
    mine.write_text(mine.read_text() + "My edit\n")
    two_assistants(project)
    row = setup_row(fw.pull_skills(project), "team-style")
    assert row["status"] == "diverged"
    assert 'skills_copy(from_="claude", to="agents", skills=["team-style"])' in row["hint"]
    assert not (project / ".agents/skills/team-style").exists()
    assert lock_entry(project, ".claude/skills/team-style")["paths"] == [
        ".claude/skills/team-style"
    ]


def test_known_hashes_from_the_setup_mark_an_older_copy_unedited(
    cloud: Cloud, project: Path
) -> None:
    # A copy of version one with no lock record (restored from git, say)
    old = cloud_entry(1, "Version one")
    folder = project / ".claude/skills/team-style"
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(old["content"])
    newer = cloud_entry(2, "Version two")
    cloud.skills = {"team-style": newer}
    assert statuses(fw.pull_skills(project, dry_run=True))["team-style"] == "edited"
    # Cloud lists every version's hash: the copy is an older, unedited version
    newer["sync"]["known_hashes"] = [old["sync"]["hash"], newer["sync"]["hash"]]
    assert statuses(fw.pull_skills(project))["team-style"] == "updated"
    assert "Version two" in skill_text(project, "team-style")
    assert old["sync"]["hash"] in lock_entry(project, ".claude/skills/team-style")["known"]


def test_a_second_pull_leaves_the_lock_bytes_as_they_are(cloud: Cloud, project: Path) -> None:
    two_assistants(project)
    fw.pull_skills(project)
    lock = (project / ".framework/skills.lock.json").read_bytes()
    fw.pull_skills(project)
    assert (project / ".framework/skills.lock.json").read_bytes() == lock


def test_two_setup_names_for_one_folder_stop_with_paths_alias(
    cloud: Cloud, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import framework.skill_pull as skill_pull
    from framework._skill_plan import SkillPlanError

    # As on a case-insensitive volume (macOS, Windows), whatever this one is
    monkeypatch.setattr(skill_pull, "_case_insensitive", lambda root: True)
    cloud.skills = {"Team-Style": cloud_entry(1, "Upper"), "team-style": cloud_entry(1, "x")}
    for dry_run in (True, False):
        with pytest.raises(SkillPlanError, match="one folder on this volume") as caught:
            fw.pull_skills(project, dry_run=dry_run)
        assert caught.value.code == "paths_alias"
    assert not (project / ".claude/skills").exists()
    assert not (project / ".framework/skills.lock.json").exists()
