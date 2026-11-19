"""The shared skill sync scenarios (``tests/shared/conformance/skills``), copied from R.

Each case runs twice: through the pure planner (``error``, ``steps``, ``rows``, and
``lock``), and through the command itself on a real project folder with Cloud mocked
(every field, including ``disk`` and ``backups``). The format is in the folder's
README; the rules in R's ``docs/architecture/skills-lock-v2.md``.
"""

from __future__ import annotations

import base64
import copy
import json
import posixpath
from pathlib import Path
from typing import Any

import pytest

from framework import _skill_plan as planner
from framework.skill_sync import bundle_hash

CASES = Path(__file__).parent / "shared" / "conformance" / "skills"
SCENARIOS = sorted(CASES.glob("*.json"))
FOLDERS = {"claude": ".claude/skills", "agents": ".agents/skills", "codex": ".agents/skills"}
SERVER = "https://cloud.test"

# Local overrides of shared cases, by id: each changes the case as loaded, with the reason.
# None so far.
OVERRIDES: dict[str, Any] = {}


def skill_md(label: str) -> str:
    return f"---\nname: fixture\ndescription: Skill scenario fixture.\n---\n\n{label}\n"


def label_bundle(label: str) -> list[dict[str, Any]]:
    return [
        {
            "path": "SKILL.md",
            "content": base64.b64encode(skill_md(label).encode()).decode(),
            "executable": False,
        }
    ]


_HASHES: dict[str, str] = {}


def label_hash(label: str) -> str:
    if label not in _HASHES:
        _HASHES[label] = bundle_hash(label_bundle(label))
    return _HASHES[label]


def label_of(value: str) -> str:
    for label, found in _HASHES.items():
        if found == value:
            return label
    return value


def resolve(value: Any) -> Any:
    """Every ``@<label>`` replaced by that label's bundle hash."""
    if isinstance(value, str) and value.startswith("@"):
        return label_hash(value[1:])
    if isinstance(value, list):
        return [resolve(v) for v in value]
    if isinstance(value, dict):
        return {k: resolve(v) for k, v in value.items()}
    return value


def load(path: Path) -> dict[str, Any]:
    case = json.loads(path.read_text(encoding="utf-8"))
    if case["id"] in OVERRIDES:
        case = OVERRIDES[case["id"]](case)
    # Labels used only as content still need hashes, for label_of
    for value in [*case["disk"].values(), *_contents(case)]:
        if value and not value.startswith("!"):
            label_hash(value)
    return case


def _contents(case: dict[str, Any]) -> list[str]:
    offer = case.get("offer") or {}
    sources = [*(offer.get("skills") or []), *(offer.get("by_identity") or {}).values()]
    return [s["content"] for s in sources if s.get("content")]


def source(raw: dict[str, Any]) -> dict[str, Any]:
    out = resolve(copy.deepcopy(raw))
    out["bundle"] = label_bundle(raw["content"]) if raw.get("content") else None
    out["known"] = sorted(set(out.get("known") or []))
    return out


def offer_of(case: dict[str, Any]) -> dict[str, Any] | None:
    raw = case.get("offer")
    if raw is None:
        return None
    return {
        "source": raw.get("source", "cloud"),
        "skills": [source(s) for s in raw.get("skills") or []],
        "by_identity": {k: source(s) for k, s in (raw.get("by_identity") or {}).items()},
        "remote": resolve(raw.get("remote") or {}),
    }


def disk_of(case: dict[str, Any]) -> planner.Disk:
    disk = planner.Disk()
    folders = set(case.get("folders") or [])
    for path, label in case["disk"].items():
        disk.hashes[path] = planner.UNREADABLE if label.startswith("!") else label_hash(label)
        parent = posixpath.dirname(path)
        disk.children.setdefault(parent, []).append(posixpath.basename(path))
        folders.add(parent)
    for folder in list(folders):
        while folder:
            disk.folders.add(folder)
            folder = posixpath.dirname(folder)
    return disk


def folder(name: str | None) -> str | None:
    return FOLDERS.get(name, name) if name is not None else None


def options_of(case: dict[str, Any], offer: dict[str, Any] | None) -> dict[str, Any]:
    o = case.get("options") or {}
    command = {"pull_all": "pull", "push_all": "push"}.get(case["command"], case["command"])
    opt: dict[str, Any] = {
        "command": command,
        "dirs": case["project"]["dirs"],
        "overwrite": bool(o.get("overwrite")),
        "force": bool(o.get("force")),
        "dry_run": bool(o.get("dry_run")),
        "offline": bool(o.get("offline")),
        "scope": "builtin" if command == "upgrade" else "all",
    }
    if command == "copy":
        source_dir = folder(o["from"])
        names = sorted(
            posixpath.basename(p) for p in case["disk"] if posixpath.dirname(p) == source_dir
        )
        opt.update(from_dir=source_dir, to_dirs=[folder(o["to"])], names=names, names_is_all=True)
    if command == "move":
        identity = {k: o.get(k) for k in ("skill_id", "variant", "builtin") if o.get(k)}
        opt.update(
            {"from": folder(o["from"]), "to": folder(o.get("to")), "identity": identity or None}
        )
        if identity:
            opt["identity"]["server"] = SERVER
    return opt


def comparable_steps(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for step in steps:
        row = {
            k: step.get(k)
            for k in ("path", "entry", "name", "action", "status", "reason_code", "hint")
        }
        if step["action"] == "write":
            row["content"] = label_of(step["hash"])
        if step["action"] in ("copy", "move") or (step["action"] == "record" and "from" in step):
            row["from"] = step["from"]
        if step.get("backup"):
            row["backup"] = True
        out.append(_sorted_hint(row))
    return sorted(out, key=lambda r: r["path"])


def _sorted_hint(row: dict[str, Any]) -> dict[str, Any]:
    found = row.get("hint")
    if isinstance(found, dict) and isinstance(found.get("to"), list):
        row["hint"] = {**found, "to": sorted(found["to"])}
    return row


def comparable_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = [
        _sorted_hint(
            {
                "name": r["name"],
                "status": r["status"],
                "paths": sorted(r["paths"]),
                "reason_code": r["reason_code"],
                "hint": r["hint"],
            }
        )
        for r in rows
    ]
    return sorted(out, key=lambda r: (r["name"], r["paths"]))


def expected_steps(case: dict[str, Any]) -> list[dict[str, Any]]:
    return sorted(
        (_sorted_hint(dict(s)) for s in case["expected"]["steps"]), key=lambda r: r["path"]
    )


def expected_rows(case: dict[str, Any]) -> list[dict[str, Any]]:
    return comparable_rows(case["expected"]["rows"])


def normal_lock(lock: dict[str, Any]) -> dict[str, Any]:
    return planner.dump(planner.migrate(lock))


def ids(paths: list[Path]) -> list[str]:
    return [p.stem for p in paths]


# Planner -----------------------------------------------------------------------------------


@pytest.mark.parametrize("path", SCENARIOS, ids=ids(SCENARIOS))
def test_planner(path: Path) -> None:
    case = load(path)
    expected = case["expected"]
    offer = offer_of(case)
    opt = options_of(case, offer)
    raw_lock = resolve(case["lock"])
    try:
        lock = planner.migrate(raw_lock) if raw_lock is not None else planner.empty_lock()
        plan = planner.plan(disk_of(case), lock, offer, opt)
    except (planner.SkillPlanError, planner.LockSchemaError) as error:
        assert expected["error"] == error.code
        return
    assert expected["error"] is None
    steps = plan.steps
    lock_after = plan.lock_after
    if opt["command"] == "push":
        confirm = (case.get("options") or {}).get("confirm", True)
        if not confirm:
            planner.cancel(steps)
        elif not opt["dry_run"]:
            responses = resolve((case.get("offer") or {}).get("push_response") or {})
            for key in {s["entry"] for s in steps if s["action"] == "push"}:
                lock_after = planner.after_push(lock_after, key, responses[key])
    assert comparable_steps(steps) == expected_steps(case)
    assert comparable_rows(planner.rows(steps, opt["command"])) == expected_rows(case)
    if opt["dry_run"]:
        return  # The command saves nothing; the plan is the same as a real run's
    if expected["lock"] == "unchanged":
        assert planner.dump(lock_after) == planner.dump(lock)
    else:
        assert planner.dump(lock_after) == normal_lock(resolve(expected["lock"]))


# Commands ----------------------------------------------------------------------------------


def make_project(root: Path, case: dict[str, Any]) -> None:
    dirs = case["project"]["dirs"]
    assistants = {"claude": ".claude/skills" in dirs, "codex": ".agents/skills" in dirs}
    spec = {"name": "scenario", "ai": {"assistants": assistants}, "skills": {}}
    (root / ".framework").mkdir(parents=True)
    (root / ".framework/spec.json").write_text(json.dumps(spec), encoding="utf-8")
    for folder_ in case.get("folders") or []:
        (root / folder_).mkdir(parents=True, exist_ok=True)
    for relative, label in case["disk"].items():
        target = root / relative
        target.mkdir(parents=True, exist_ok=True)
        if label.startswith("!"):
            (target / "README.txt").write_text("Not a skill.\n", encoding="utf-8")
        else:
            (target / "SKILL.md").write_bytes(skill_md(label).encode())
    if case["lock"] is not None:
        text = json.dumps(resolve(case["lock"]), indent=2) + "\n"
        (root / ".framework/skills.lock.json").write_text(text, encoding="utf-8")


def skill_folders(root: Path) -> dict[str, str]:
    """Every skill folder in the project, as content labels."""
    from framework.skill_sync import scan

    found = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if not path.is_dir() or relative.startswith(".framework") or path.name.startswith("."):
            continue
        if (path / "SKILL.md").is_file():
            found[relative] = label_of(bundle_hash(scan(path)))
        elif (path / "README.txt").is_file():
            found[relative] = "!unreadable"
    return found


def shown_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for row in rows:
        key = row.get("hint_key")
        found = {"key": key, **(row.get("hint_args") or {})} if key else None
        out.append({**row, "hint": found})
    return comparable_rows(out)


def _failed(rows: list[dict[str, Any]]) -> list[str]:
    return sorted(r["name"] for r in rows if r["status"] in ("failed", "diverged", "missing"))


def run_command(
    case: dict[str, Any], root: Path, monkeypatch: pytest.MonkeyPatch
) -> dict[str, Any]:
    import framework as fw
    import framework.cloud as cloud
    import framework.skill_pull as skill_pull
    import framework.skill_sync as skill_sync
    from framework import sync_all

    o = case.get("options") or {}
    offer = offer_of(case)
    command = case["command"]
    if command in ("pull", "pull_all"):
        monkeypatch.setattr(skill_pull, "_pull_offer", lambda root, spec: (offer, []))
        monkeypatch.setattr(skill_pull, "_fetch_by_identity", lambda *a: None)
    if command in ("push", "push_all"):
        assert offer is not None
        responses = resolve(case["offer"].get("push_response") or {})

        def heads(root: Path, lock: Any, keys: Any, server: str, token: Any) -> Any:
            return {k: {**v, "skill_id": None} for k, v in offer["remote"].items()}, {}

        def post(path: str, token: str, url: str, method: str = "GET", body: Any = None) -> Any:
            assert method == "POST" and path == "/api/v1/skills/sync"
            assert len(responses) == 1, "one push per case"
            return next(iter(responses.values()))

        monkeypatch.setattr(skill_pull, "_push_login", lambda: (SERVER, "token"))
        monkeypatch.setattr(skill_pull, "_push_remote", heads)
        monkeypatch.setattr(cloud, "api", post)
        monkeypatch.setattr(cloud, "cloud_token", lambda *a, **k: "token")

    if command == "pull":
        return fw.pull_skills(
            root, overwrite=bool(o.get("overwrite")), dry_run=bool(o.get("dry_run"))
        )
    if command == "pull_all":
        part = sync_all._skills_pull_plan(root, bool(o.get("overwrite")))
        result = part["plan"]
        if part["pending"] and not o.get("dry_run"):
            result = part["apply"]()
        return {**result, "failed": _failed(result["skills"])}
    if command == "upgrade":
        assert offer is not None
        by_name = {s["name"]: s for s in offer["skills"]}
        monkeypatch.setattr(skill_sync, "package_source", lambda name: by_name[name])
        planned = skill_pull.upgrade_skills(root, case["project"]["dirs"], list(by_name))
        steps = planned["steps"]
        if not o.get("dry_run"):
            planned["apply"]()
        return {"steps": steps, "skills": planned["rows"]}
    if command == "push":
        return fw.push_skills(
            root,
            force=bool(o.get("force")),
            confirm=o.get("confirm", True),
            dry_run=bool(o.get("dry_run")),
        )
    if command == "push_all":
        part = sync_all._skills_push_plan(root, bool(o.get("force")))
        steps = part.get("steps") or []
        rows = part["plans"]
        if part["pending"] and not o.get("dry_run"):
            rows = part["apply"]()
        return {"skills": rows, "steps": steps, "failed": _failed(rows)}
    if command == "copy":
        names = {k: v for k, v in (("from", o["from"]), ("to", o["to"])) if v in FOLDERS}
        return fw.skills_copy(
            from_=names.get("from"),
            to=names.get("to"),
            from_dir=None if "from" in names else o["from"],
            to_dir=None if "to" in names else o["to"],
            project_path=root,
        )
    if command == "move":
        return fw.skills_move(
            o["from"],
            o.get("to"),
            skill_id=o.get("skill_id"),
            variant=o.get("variant"),
            builtin=o.get("builtin"),
            project_path=root,
        )
    raise AssertionError(command)


@pytest.mark.parametrize("path", SCENARIOS, ids=ids(SCENARIOS))
def test_command(path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import framework.skill_sync as skill_sync

    case = load(path)
    expected = case["expected"]
    root = tmp_path / "project"
    make_project(root, case)
    monkeypatch.setenv("FW_CLOUD_URL", SERVER)
    monkeypatch.chdir(root)
    lock_file = root / ".framework/skills.lock.json"
    lock_before = lock_file.read_bytes() if lock_file.exists() else None

    backups: list[str] = []
    install = skill_sync._install

    def spy(root_: Path, paths: list[str], files: Any, expected_: Any) -> Any:
        done = install(root_, paths, files, expected_)
        backups.extend(p for p, b in done.items() if b)
        return done

    monkeypatch.setattr(skill_sync, "_install", spy)
    try:
        result = run_command(case, root, monkeypatch)
    except (planner.SkillPlanError, planner.LockSchemaError) as error:
        assert expected["error"] == error.code
        result = None
    else:
        assert expected["error"] is None
    if result is not None:
        if "steps" in result and (result["steps"] or expected["steps"]):
            assert comparable_steps(result["steps"]) == expected_steps(case)
        assert shown_rows(result["skills"]) == expected_rows(case)
        if "failed" in expected:
            assert result["failed"] == sorted(expected["failed"])
    assert skill_folders(root) == expected["disk"]
    assert sorted(backups) == sorted(expected["backups"])
    after = lock_file.read_bytes() if lock_file.exists() else None
    if expected["lock"] == "unchanged":
        assert after == lock_before
    else:
        assert after is not None
        assert json.loads(after) == normal_lock(resolve(expected["lock"]))
