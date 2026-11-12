"""AI assistant files: AGENTS.md, assistant stubs, skills, and agent guards."""

from __future__ import annotations

import json
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Any

from framework._agents_md import agents_md
from framework._private import spec_sensitive
from framework.spec.check import ASSISTANTS

AGENTS_DEFAULT = ".agents/skills"


def _write(root: Path, relative: str, content: str) -> None:
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def assistant_targets(spec: dict[str, Any]) -> tuple[list[str], list[str]]:
    """Skills folders and stub files for the selected assistants.

    With no assistants selected, skills go to ``.agents/skills`` only, the
    folder more tools read.
    """
    selected = [name for name in spec.get("ai", {}).get("assistants", {}) if name in ASSISTANTS]
    if not selected:
        return [AGENTS_DEFAULT], []
    folders = list(dict.fromkeys(ASSISTANTS[name][0] for name in selected))
    stubs = [stub for name in selected if (stub := ASSISTANTS[name][1]) is not None]
    return folders, stubs


def restricted_paths(spec: dict[str, Any]) -> list[str]:
    """Folders agents must not read in a sensitive project, each ending in ``/``.

    Every configured folder (``directories``, ``extra_directories``, ``render_dirs``)
    with a segment named ``private`` or ending in ``_private``, collapsed to that
    segment, and every ``directories`` entry whose key marks it private
    (``inputs_private_raw: inputs/raw``), as configured. ``.``, unsafe paths (outside
    the project), and folders inside another restricted folder are left out. ``.env``
    (secrets) is added by the callers. R: ``.spec_restricted_paths()``.
    """
    from framework._private import is_private_folder_name
    from framework.spec.check import is_safe_path

    def clean(values: Any) -> list[str]:
        out = []
        for value in values:
            if isinstance(value, str) and value:
                path = value.removeprefix("./").rstrip("/")
                if path and is_safe_path(path):
                    out.append(path)
        return out

    def mapping(value: Any) -> dict[str, Any]:
        return value if isinstance(value, dict) else {}

    directories = mapping(spec.get("directories"))
    extra = spec.get("extra_directories")
    entries = (
        extra.values() if isinstance(extra, dict) else extra if isinstance(extra, list) else []
    )
    paths = clean(
        [
            *directories.values(),
            *(e.get("path") for e in entries if isinstance(e, dict)),
            *mapping(spec.get("render_dirs")).values(),
        ]
    )
    restricted: list[str] = []
    for path in paths:
        parts = path.split("/")
        hits = [i for i, part in enumerate(parts) if is_private_folder_name(part)]
        if hits:
            restricted.append("/".join(parts[: hits[0] + 1]))
    restricted += clean(
        value
        for key, value in directories.items()
        if "private" in str(key).lower() and "public" not in str(key).lower()
    )
    restricted = [p for p in dict.fromkeys(restricted) if p != "."]
    kept = [p for p in restricted if not any(p.startswith(f"{o}/") for o in restricted if o != p)]
    return [f"{p}/" for p in sorted(kept)]


def restricted_notice(spec: dict[str, Any]) -> str | None:
    """The line naming the folders agents must stay out of, for sensitive projects."""
    if not spec_sensitive(spec):
        return None
    folders = ", ".join(f"`{path}`" for path in restricted_paths(spec))
    return (
        f"This project holds restricted data. Do not read or print files in {folders}. "
        "Work from the data dictionary and simulated data instead."
    )


def pointer_stub(name: str) -> str:
    return (
        f"# {name}\n\n"
        "Project instructions live in AGENTS.md (the canonical AI context file) and in\n"
        "the project's skills. Read AGENTS.md before working here; do not duplicate its\n"
        "content in this file.\n"
    )


def _shipped(name: str) -> Traversable:
    return resources.files("framework").joinpath(f"templates/skills/{name}")


def _copy_tree(source: Traversable, root: Path, target: str) -> None:
    for entry in source.iterdir():
        if entry.is_dir():
            _copy_tree(entry, root, f"{target}/{entry.name}")
        else:
            _write(root, f"{target}/{entry.name}", entry.read_text(encoding="utf-8"))


def write_ai_files(root: Path, spec: dict[str, Any]) -> list[str]:
    """Write AGENTS.md, assistant stubs, skills, and (for sensitive projects) agent guards."""
    written = []
    folders, stubs = assistant_targets(spec)
    files = spec.get("files") or {}

    if spec.get("ai", {}).get("agents_md") == "stub" and "AGENTS.md" not in files:
        # The full guide, as R writes it; its skills table points at the first skills folder
        skills_dir = folders[0] if folders else ".agents/skills"
        _write(root, "AGENTS.md", agents_md(spec, skills_dir, restricted_notice(spec)))
        written.append("AGENTS.md")
    for stub in stubs:
        _write(root, stub, "@AGENTS.md\n" if stub == "CLAUDE.md" else pointer_stub(spec["name"]))
        written.append(stub)

    for name, entry in (spec.get("skills") or {}).items():
        for folder in folders:
            target = f"{folder}/{name}"
            if entry.get("package") is True:
                from framework.skill_sync import install_package

                install_package(_shipped(name), root, target, name)
            elif entry.get("sync") and entry.get("bundle"):
                from framework.skill_sync import install_cloud

                install_cloud(root, target, entry)
            else:
                _write(root, f"{target}/SKILL.md", entry["content"])
                for relative, content in (entry.get("files") or {}).items():
                    _write(root, f"{target}/{relative}", content)
            written.append(target)

    if spec_sensitive(spec):
        guard = write_agent_guards(root, spec)
        if guard:
            written.append(guard)
    return written


def write_agent_guards(root: Path, spec: dict[str, Any]) -> str | None:
    """Keep Claude Code out of restricted folders in a sensitive project.

    Read and Edit deny rules block Claude Code's file tools, and the sandbox's
    ``denyRead`` also blocks subprocesses such as ``python``. The sandbox fails
    closed when it is unavailable. The rules match the R package's.
    """
    if "claude" not in (spec.get("ai") or {}).get("assistants", {}):
        return None
    restricted = [path.rstrip("/") for path in restricted_paths(spec)]
    deny = [rule for path in restricted for rule in (f"Read(/{path}/**)", f"Edit(/{path}/**)")]
    deny.append("Read(/.env)")
    sandbox_deny = [f"./{path}" for path in restricted] + ["./.env"]

    relative = ".claude/settings.json"
    path = root / relative
    settings: dict[str, Any] = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    permissions = settings.setdefault("permissions", {})
    permissions["deny"] = list(dict.fromkeys([*permissions.get("deny", []), *deny]))
    sandbox = settings.setdefault("sandbox", {})
    sandbox["enabled"] = True
    sandbox["failIfUnavailable"] = True
    filesystem = sandbox.setdefault("filesystem", {})
    filesystem["denyRead"] = list(dict.fromkeys([*filesystem.get("denyRead", []), *sandbox_deny]))
    _write(root, relative, json.dumps(settings, indent=2) + "\n")
    return relative


def _installed_spec(root: Path) -> dict[str, Any]:
    """The spec a project was created from, or one rebuilt from its settings."""
    path = root / ".framework" / "spec.json"
    if path.is_file():
        try:
            spec = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            spec = None
        if isinstance(spec, dict):
            return spec
    from framework.settings import settings_read
    from framework.spec.resolve import TYPES, package_defaults

    config = settings_read(root / "settings.yml") if (root / "settings.yml").is_file() else {}
    ai = config.get("ai")
    assistants = (ai.get("assistants") if isinstance(ai, dict) else None) or []
    type = config.get("project_type")
    return {
        "ai": {"assistants": {name: True for name in assistants}},
        "skills": package_defaults(type if type in TYPES else "project")["skills"],
    }


def ai_skills_update(project_path: str | Path = ".") -> dict[str, Any]:
    """Bring the project's skills up to date. The same as ``pull_skills(project_path)``.

    Kept for scripts written before ``pull_skills()``. See ``pull_skills()`` for what is
    added, updated, kept, and listed.

    Args:
        project_path: The project's root directory. Default: the current directory.

    Returns:
        What ``pull_skills()`` returns.

    Raises:
        ProjectNotFoundError: When the folder has no ``.framework/spec.json`` or settings file.
    """
    from framework.skill_pull import pull_skills

    return pull_skills(project_path)
