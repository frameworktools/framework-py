"""AI assistant files: AGENTS.md, assistant stubs, skills, and agent guards."""

from __future__ import annotations

import json
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Any

from framework._agents_md import agents_md
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
    """Folders agents must not read: each configured path up to its ``private`` segment."""
    paths = list((spec.get("directories") or {}).values())
    paths += [entry.get("path") for entry in (spec.get("extra_directories") or {}).values()]
    restricted = set()
    for path in paths:
        if not isinstance(path, str):
            continue
        parts = path.split("/")
        if "private" in parts:
            restricted.add("/".join(parts[: parts.index("private") + 1]) + "/")
    return sorted(restricted)


def restricted_notice(spec: dict[str, Any]) -> str | None:
    """The line naming the folders agents must stay out of, for sensitive projects."""
    if not spec.get("security", {}).get("sensitive"):
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

    if spec.get("security", {}).get("sensitive"):
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


_SETTINGS_FILES = ("settings.yml", "config.yml")


def ai_skills_update(project_path: str | Path = ".") -> list[str]:
    """Refresh clean Framework skills. Preserve local edits and account forks.

    Only skills that ship with Framework are updated. Skills from a blueprint
    or facet, and skills you wrote, are left alone. The skills and assistants
    come from ``.framework/spec.json``, or from the settings for projects
    without one.

    Args:
        project_path: The project's root directory. Default: the current directory.

    Returns:
        The skill folders whose files changed. Skills that were already current are left
        out.

    Raises:
        ProjectNotFoundError: When the folder has no ``.framework/spec.json`` or settings file.
    """
    from framework._log import inform, show_messages

    show_messages()
    root = Path(project_path).expanduser()
    if not any((root / name).is_file() for name in (".framework/spec.json", *_SETTINGS_FILES)):
        from framework.errors import ProjectNotFoundError

        raise ProjectNotFoundError(
            f"{root} is not a Framework project: it has no .framework/spec.json or settings file."
        )
    spec = _installed_spec(root)
    folders, _ = assistant_targets(spec)
    updated = []
    for name, entry in (spec.get("skills") or {}).items():
        if not (isinstance(entry, dict) and entry.get("package") is True):
            continue
        if not _shipped(name).is_dir():
            continue
        for folder in folders:
            from framework.skill_sync import bundle_hash, install_package, scan

            target = root / folder / name
            # Only a skill whose files changed counts as updated
            before = bundle_hash(scan(target)) if target.is_dir() else None
            if install_package(_shipped(name), root, f"{folder}/{name}", name) and (
                (bundle_hash(scan(target)) if target.is_dir() else None) != before
            ):
                updated.append(f"{folder}/{name}")
    if updated:
        inform(f"✓ Updated {len(updated)} Framework skill(s)")
    else:
        inform("No Framework skills to update")
    return updated
