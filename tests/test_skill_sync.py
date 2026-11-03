import base64
import copy
import json
from pathlib import Path

import pytest

from framework.skill_sync import _load, bundle_hash, install_package, scan, skills_sync


def write_skill(root, text="First"):
    folder = root / ".agents/skills/example"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "SKILL.md").write_text(
        "---\nname: example\ndescription: Example tasks\n---\n" + text + "\n"
    )
    return folder


class Cloud:
    def __init__(self):
        self.head = None
        self.calls = []
        self.receipts = {}
        self.drop = False

    def api(self, path, token, url, method="GET", body=None):
        self.calls.append((path, method, body))
        if method == "GET":
            return copy.deepcopy(self.head)
        if body["request_id"] in self.receipts:
            return copy.deepcopy(self.receipts[body["request_id"]])
        assert body["expected_version"] == (self.head["version"] if self.head else 0)
        self.head = {
            "skill_id": "private-id",
            "version": (self.head["version"] if self.head else 0) + 1,
            "bundle": body["bundle"],
            "hash": bundle_hash(body["bundle"]),
        }
        self.receipts[body["request_id"]] = copy.deepcopy(self.head)
        if self.drop:
            self.drop = False
            raise TimeoutError("Lost response")
        return copy.deepcopy(self.head)


@pytest.fixture
def cloud(monkeypatch):
    from types import SimpleNamespace

    import framework.cloud as module

    server = Cloud()
    monkeypatch.setattr(module, "api", server.api)
    monkeypatch.setattr(
        module, "cloud_server", lambda: SimpleNamespace(url="https://cloud.example")
    )
    monkeypatch.setattr(module, "_require_token", lambda _: "token")
    return server


def test_cross_language_hash_and_paths():
    fixture = json.loads((Path(__file__).parent / "fixtures/skill-bundle-v1.json").read_text())
    assert bundle_hash(fixture["bundle"]) == fixture["hash"]
    assert bundle_hash(list(reversed(fixture["bundle"]))) == fixture["hash"]
    for path in ("../escape", ".env", "foo/.env.local", "secret.pem", "SKILL.md/child"):
        with pytest.raises(ValueError):
            bundle_hash(fixture["bundle"] + [{"path": path, "content": "", "executable": False}])


def test_push_pull_conflict_status_and_timeout_retry(tmp_path, cloud):
    folder = write_skill(tmp_path)
    result = skills_sync(".agents/skills/example", tmp_path, dry_run=True)
    assert result["action"] == "push" and cloud.head is None
    assert not (tmp_path / ".framework/skills.lock.json").exists()
    cloud.drop = True
    with pytest.raises(TimeoutError):
        skills_sync(".agents/skills/example", tmp_path)
    first_key = cloud.calls[-1][2]["request_id"]
    skills_sync(".agents/skills/example", tmp_path)
    assert cloud.calls[-1][2]["request_id"] == first_key and cloud.head["version"] == 1
    source = folder / "SKILL.md"
    source.write_text(source.read_text() + "Local edit\n")
    assert skills_sync(".agents/skills/example", tmp_path)["action"] == "push"
    remote = copy.deepcopy(cloud.head["bundle"])
    remote[0]["content"] = base64.b64encode(
        base64.b64decode(remote[0]["content"]) + b"Web edit\n"
    ).decode()
    cloud.head.update(bundle=remote, hash=bundle_hash(remote), version=3)
    assert skills_sync(".agents/skills/example", tmp_path, direction="status")["action"] == "pull"
    assert "Web edit" not in source.read_text()
    assert skills_sync(".agents/skills/example", tmp_path)["action"] == "pull"
    assert "Web edit" in source.read_text()
    source.write_text(source.read_text() + "More local\n")
    remote[0]["content"] = base64.b64encode(
        base64.b64decode(remote[0]["content"]) + b"More remote\n"
    ).decode()
    cloud.head.update(bundle=remote, hash=bundle_hash(remote), version=4)
    before = source.read_bytes()
    assert skills_sync(".agents/skills/example", tmp_path)["action"] == "conflict"
    assert source.read_bytes() == before
    assert skills_sync(".agents/skills/example", tmp_path, resolve="local")["action"] == "push"
    assert source.read_bytes() == before
    assert cloud.head["version"] == 5


def test_safe_package_refresh_aliases_and_symlinks(tmp_path):
    source = tmp_path / "package"
    source.mkdir()
    (source / "SKILL.md").write_text("---\nname: example\ndescription: Examples\n---\nSource\n")
    assert install_package(source, tmp_path, ".agents/skills/example", "example")
    assert install_package(source, tmp_path, ".claude/skills/example", "example")
    installed = tmp_path / ".agents/skills/example/SKILL.md"
    installed.write_text(installed.read_text() + "Personal edit\n")
    (source / "SKILL.md").write_text("New package version\n")
    assert not install_package(source, tmp_path, ".agents/skills/example", "example")
    assert "Personal edit" in installed.read_text()
    assert len(next(iter(_load(tmp_path)["skills"].values()))["paths"]) == 2
    (tmp_path / ".agents/skills/example/link").symlink_to(source / "SKILL.md")
    with pytest.raises(ValueError, match="symlink"):
        scan(installed.parent)


def test_initial_pull_and_atomic_binary_deletion(tmp_path, cloud):
    fixture = json.loads((Path(__file__).parent / "fixtures/skill-bundle-v1.json").read_text())
    cloud.head = {"skill_id": "private-id", "version": 1, **fixture}
    assert skills_sync(".agents/skills/new", tmp_path, skill_id="private-id")["action"] == "pull"
    target = tmp_path / ".agents/skills/new"
    assert (target / "scripts/run.bin").read_bytes() == b"\x00\xff\x01\r\n"
    assert (target / "scripts/run.bin").stat().st_mode & 0o111
    (target / ".env").write_text("local secret")
    files = [f for f in fixture["bundle"] if f["path"] != "scripts/run.bin"]
    cloud.head.update(bundle=files, hash=bundle_hash(files), version=2)
    skills_sync(".agents/skills/new", tmp_path)
    assert not (target / "scripts/run.bin").exists()
    assert (target / ".env").read_text() == "local secret"


def test_canonical_package_bytes_match_shipped_identity(tmp_path):
    from importlib import resources

    folder = resources.files("framework").joinpath("templates/skills")
    for source in folder.iterdir():
        metadata = source.joinpath(".framework-skill.json")
        if not metadata.is_file():
            continue
        expected = json.loads(metadata.read_text(encoding="utf-8"))["hash"]
        relative = ".agents/skills/" + source.name
        assert install_package(source, tmp_path, relative, source.name)
        assert bundle_hash(scan(tmp_path / relative)) == expected
