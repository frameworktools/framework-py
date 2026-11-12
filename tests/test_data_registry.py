import json
from pathlib import Path
from typing import Any

import pytest

import framework as fw
from framework import _yaml
from framework.cloud import CloudError
from framework.errors import FrameworkError
from framework.spec.check import catalog_nest, data_registry_problems
from framework.spec.merge import merge_layers

SERVER = "https://cloud.example"
PATH = "/api/v1/projects/data"

SETTINGS = """# Project settings
default:
  project_name: Test Project
  # Keep this comment
  directories:
    inputs_raw: inputs/raw
  data:
    inputs:
      raw:
        survey:
          path: inputs/raw/survey.csv
          type: csv
          locked: true
          note: local only
        visits: inputs/raw/visits.csv
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


class Cloud:
    """Framework Cloud's data registry endpoint, in memory."""

    def __init__(self) -> None:
        self.revision = "rev-1"
        self.entries = [
            entry("inputs.raw.survey", "inputs/raw/survey_v2.csv", type="csv", locked=True),
            entry("inputs.raw.visits", "inputs/raw/visits.csv"),
            entry(
                "inputs.final.analysis",
                "inputs/final/analysis.parquet",
                type="parquet",
                description="Analysis file",
            ),
        ]
        self.posts: list[dict[str, Any]] = []
        self.missing = False
        self.offline = False
        self.refused = False

    def api(self, path: str, token: str, url: str, method: str = "GET", body: Any = None) -> Any:
        from framework.cloud import CloudConflictError, CloudOfflineError, _MissingEndpointError

        assert token == "fw_project" and url == SERVER and path == PATH
        if self.offline:
            raise CloudOfflineError("Could not reach cloud.example")
        if self.missing:
            raise _MissingEndpointError("Not found")
        if self.refused:
            raise CloudError("Unauthenticated.")
        if method == "GET":
            return {"revision": self.revision, "entries": self.entries}
        self.posts.append(body)
        if body["base_revision"] != self.revision and not body["force"]:
            raise CloudConflictError(
                "Pull first", {"message": "Pull first", "revision": self.revision}
            )
        current = {e["name"]: e for e in self.entries}
        changes: dict[str, list[Any]] = {"added": [], "changed": [], "removed": [], "kept": []}
        for row in body["entries"]:
            before = current.get(row["name"])
            if before is None:
                changes["added"].append(row)
            elif before["path"] != row["path"]:
                changes["changed"].append({"name": row["name"], "from": before, "to": row})
        named = {r["name"] for r in body["entries"]}
        for name, row in current.items():
            if name not in named:
                changes["removed" if body["remove"] else "kept"].append(row)
        applied = not body["dry_run"] and any(changes[k] for k in ("added", "changed", "removed"))
        if applied:
            self.revision = "rev-2"
        return {"applied": applied, "revision": self.revision, "changes": changes}


@pytest.fixture(autouse=True)
def _messages() -> None:
    from framework._log import show_messages

    show_messages()


@pytest.fixture
def cloud(project: Path, monkeypatch: pytest.MonkeyPatch) -> Cloud:
    import framework.cloud as module

    (project / "settings.yml").write_text(SETTINGS, encoding="utf-8")
    (project / ".env").write_text(f"FW_PROJECT_TOKEN=fw_project\nFW_CLOUD_URL={SERVER}\n")
    monkeypatch.delenv("FW_PROJECT_TOKEN", raising=False)
    monkeypatch.delenv("FW_CLOUD_URL", raising=False)
    monkeypatch.setenv("FW_TOKEN_FILE", str(project.parent / "no-token"))
    server = Cloud()
    monkeypatch.setattr(module, "api", server.api)
    return server


def lock(project: Path) -> dict[str, Any]:
    return json.loads((project / ".framework/data.lock.json").read_text())


def data(project: Path) -> dict[str, Any]:
    return _yaml.read(project / "settings.yml")["default"]["data"]


def test_spec_data_is_checked_and_nested() -> None:
    assert data_registry_problems({"inputs.raw.survey": {"path": "inputs/raw/s.csv"}}) == []
    problems = data_registry_problems(
        {
            "inputs.raw": {"path": "inputs/raw.csv"},
            "inputs.raw.survey": {"path": "../outside.csv", "type": "word", "hash": "abc"},
        }
    )
    text = "\n".join(problems)
    assert "unknown field 'hash'" in text
    assert "must be relative" in text
    assert "type must be one of" in text
    assert "'inputs.raw' is an entry" in text
    nested = catalog_nest({"inputs.raw.survey": {"path": "a.csv"}, "outputs.x": {"path": "b"}})
    assert nested == {
        "inputs": {"raw": {"survey": {"path": "a.csv"}}},
        "outputs": {"x": {"path": "b"}},
    }
    assert catalog_nest(None) == {}


def test_data_entries_are_replaced_whole() -> None:
    merged = merge_layers(
        [
            {"data": {"a.b": {"path": "a.csv", "delimiter": "tab"}, "a.c": {"path": "c.csv"}}},
            {"data": {"a.b": {"path": "a2.csv"}, "a.c": None}},
        ]
    )
    assert merged.spec["data"] == {"a.b": {"path": "a2.csv"}}


def test_pull_lists_changes_then_adds_and_keeps_edits(
    cloud: Cloud, project: Path, caplog: Any
) -> None:
    caplog.set_level("INFO", logger="framework")
    fw.pull_data_registry(dry_run=True)
    text = caplog.text
    assert "Data registry changes from Framework Cloud (revision rev-1):" in text
    assert "Entries to add: inputs.final.analysis" in text
    assert "Entries that differ: inputs.raw.survey" in text
    assert "kept; pull_data_registry(overwrite=True) uses Cloud's" in text
    assert not (project / ".framework/data.lock.json").exists()

    before = (project / "settings.yml").read_text()
    with pytest.raises(FrameworkError, match=r"pull_data_registry\(\) asks before changing"):
        fw.pull_data_registry()
    assert (project / "settings.yml").read_text() == before

    result = fw.pull_data_registry(confirm=True)
    assert result["applied"]
    text = (project / "settings.yml").read_text()
    assert "# Project settings" in text and "# Keep this comment" in text
    catalog = data(project)
    assert catalog["inputs"]["final"]["analysis"]["path"] == "inputs/final/analysis.parquet"
    assert catalog["inputs"]["final"]["analysis"]["description"] == "Analysis file"
    assert catalog["inputs"]["raw"]["survey"]["path"] == "inputs/raw/survey.csv"
    assert lock(project)["revision"] == "rev-1"

    fw.pull_data_registry(overwrite=True, confirm=True)
    survey = data(project)["inputs"]["raw"]["survey"]
    assert survey["path"] == "inputs/raw/survey_v2.csv"
    assert survey["note"] == "local only"

    caplog.clear()
    fw.pull_data_registry(dry_run=True)
    assert "Data registry is up to date with Framework Cloud (revision rev-1)." in caplog.text


def test_pull_writes_a_split_data_file(cloud: Cloud, project: Path) -> None:
    (project / "settings").mkdir()
    (project / "settings.yml").write_text(
        "default:\n  project_name: Test Project\n  # The catalog lives in its own file\n"
        "  data: settings/data.yml\n"
    )
    (project / "settings/data.yml").write_text(
        "data:\n  inputs:\n    raw:\n      survey:\n        path: inputs/raw/survey.csv\n"
    )
    cloud.entries = cloud.entries[2:]
    fw.pull_data_registry(confirm=True)
    assert "# The catalog lives in its own file" in (project / "settings.yml").read_text()
    split = _yaml.read(project / "settings/data.yml")["data"]
    assert split["inputs"]["final"]["analysis"]["type"] == "parquet"
    assert split["inputs"]["raw"]["survey"]["path"] == "inputs/raw/survey.csv"


def test_push_previews_asks_and_records(cloud: Cloud, project: Path, caplog: Any) -> None:
    caplog.set_level("INFO", logger="framework")
    cloud.entries = []
    fw.push_data_registry(dry_run=True)
    assert "Entries to add: inputs.raw.survey, inputs.raw.visits" in caplog.text
    assert "no data files and no hashes" in caplog.text
    sent = cloud.posts[0]["entries"]
    assert sent[0] == {
        "name": "inputs.raw.survey",
        "path": "inputs/raw/survey.csv",
        "type": "csv",
        "locked": True,
    }
    assert sent[1] == {
        "name": "inputs.raw.visits",
        "path": "inputs/raw/visits.csv",
        "locked": False,
    }

    with pytest.raises(FrameworkError, match=r"push_data_registry\(\) asks before changing"):
        fw.push_data_registry()

    result = fw.push_data_registry(confirm=True)
    assert result["applied"]
    assert lock(project)["revision"] == "rev-2"
    assert "Data registry pushed: revision rev-2 (2 added)" in caplog.text


def test_push_refuses_a_stale_revision_unless_forced(
    cloud: Cloud, project: Path, caplog: Any
) -> None:
    caplog.set_level("INFO", logger="framework")
    (project / ".framework").mkdir(exist_ok=True)
    (project / ".framework/data.lock.json").write_text('{"schema": 1, "revision": "rev-old"}')
    cloud.entries = [entry("inputs.old", "old.csv")]
    with pytest.raises(CloudError, match=r"Pull first: pull_data_registry\(\)"):
        fw.push_data_registry(confirm=True)
    fw.push_data_registry(force=True, remove=True, confirm=True)
    assert "Entries to remove: inputs.old" in caplog.text
    assert "Cloud's newer data registry changes will be replaced" in caplog.text
    assert cloud.posts[-1]["remove"] is True


def test_offline_refused_older_server_and_no_token(
    cloud: Cloud, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cloud.offline = True
    with pytest.raises(CloudError, match=r"Framework Cloud not reached .*Nothing changed\."):
        fw.pull_data_registry()
    cloud.offline, cloud.refused = False, True
    with pytest.raises(CloudError, match="refused the project token"):
        fw.push_data_registry()
    cloud.refused, cloud.missing = False, True
    with pytest.raises(CloudError, match="no data registry endpoint yet"):
        fw.pull_data_registry()
    (project / ".env").write_text(f"FW_CLOUD_URL={SERVER}\n")
    with pytest.raises(CloudError, match="no project token"):
        fw.pull_data_registry()


def test_project_creation_writes_the_spec_data_entries(tmp_path: Path) -> None:
    data_layer = {"inputs.raw.survey": {"path": "inputs/raw/survey.csv", "locked": True}}
    for structure, file in (("project", "settings/data.yml"), ("presentation", "settings.yml")):
        spec = fw.spec_resolve(
            [{"language": "python", "type": structure, "data": data_layer}], "Data study"
        ).spec
        root = fw.project_create(spec, tmp_path / structure)
        content = _yaml.read(root / file)
        catalog = content["data"] if "data" in content else content["default"]["data"]
        assert catalog["inputs"]["raw"]["survey"] == {
            "path": "inputs/raw/survey.csv",
            "locked": True,
        }
