"""The AGENTS.md guide a new project gets, generated from its spec.

The sections follow R's AGENTS.md, with Python's functions. Headings marked
``<!-- @framework:regenerate -->`` describe the settings. The others are the
user's to edit.
"""

from __future__ import annotations

import base64
import binascii
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from framework.spec.resolve import is_app_type

REGENERATE = "<!-- @framework:regenerate -->"
PACKAGES_HEADING = "Installed Packages"

SKILL_DESCRIPTIONS = {
    "framework-workflow": "Framework package initialization and notebook or script creation",
    "framework-data": "reading and saving data with data_read() and data_save()",
    "framework-packages": "adding and managing Python packages with package_add()",
    "framework-outputs": "saving results with the Framework package",
    "framework-cache": "caching computations with the Framework package",
    "framework-databases": "querying configured Framework databases",
    "framework-publishing": "publishing artifacts with the Framework package",
    "framework-sensitive-data": "private and public folders in a privacy-sensitive project",
}

_PURPOSES = {
    "inputs_raw": "Raw data (immutable)",
    "inputs_intermediate": "Intermediate/cleaned data",
    "inputs_final": "Analysis-ready data",
    "outputs_tables": "Output tables",
    "outputs_figures": "Output figures",
    "outputs_models": "Saved models",
    "outputs_reports": "Reports",
    "inputs_private_raw": "Private raw data",
    "inputs_public_raw": "Public raw data",
    "inputs_private_intermediate": "Private intermediate",
    "inputs_public_intermediate": "Public intermediate",
    "inputs_private_final": "Private analysis-ready",
    "inputs_public_final": "Public analysis-ready",
    "outputs_private_tables": "Private tables",
    "outputs_public_tables": "Public tables",
    "outputs_private_figures": "Private figures",
    "outputs_public_figures": "Public figures",
    "data": "Data files",
    "outputs": "Output files",
}


def _header(name: str) -> str:
    return (
        f"# {name}\n\n"
        "This file provides guidance to AI assistants working with this Framework project.\n"
        "It is the canonical context file; detailed workflow instructions live in the\n"
        "skills listed below. Edit the sections without regeneration markers freely -\n"
        "they won't be overwritten.\n"
    )


def skill_description(folder: Path) -> str:
    """A skill's own description, from its SKILL.md front matter, on one line with pipes
    escaped, for skills Framework has no line for. R: ``.skill_description()``."""
    try:
        text = (folder / "SKILL.md").read_text(encoding="utf-8")
    except OSError:
        return ""
    return _front_description(text)


def _entry_skill_md(entry: object) -> str | None:
    """A spec skill entry's SKILL.md text: its ``content``, else the file in its Cloud
    ``bundle`` (base64). None for a package skill, which ships its own."""
    if not isinstance(entry, Mapping):
        return None
    content = entry.get("content")
    if isinstance(content, str) and content:
        return content
    for item in entry.get("bundle") or []:
        if isinstance(item, Mapping) and item.get("path") == "SKILL.md":
            try:
                return base64.b64decode(str(item.get("content") or "")).decode("utf-8")
            except (binascii.Error, ValueError):
                return None
    return None


def _front_description(text: str) -> str:
    from framework import _yaml

    found = re.match(r"---\r?\n(.*?)\r?\n---", text, re.DOTALL)
    try:
        meta = _yaml.load(found.group(1)) if found else None
    except Exception:
        meta = None
    value = meta.get("description") if isinstance(meta, dict) else None
    if not isinstance(value, str):
        return ""
    return " ".join(value.split()).replace("|", "\\|")


def _skills(
    skills: list[str],
    skills_dir: str,
    root: Path | None = None,
    entries: Mapping[str, Any] | None = None,
) -> str:
    """The Skills section. A skill Framework has no line for is described by its SKILL.md
    front matter: from the spec's entry when there is one, else the copy in the project."""

    def covers(skill: str) -> str:
        known = SKILL_DESCRIPTIONS.get(skill)
        if known is not None:
            return known
        text = _entry_skill_md((entries or {}).get(skill))
        if text is not None:
            described = _front_description(text)
            if described:
                return described
        return skill_description(root / skills_dir / skill) if root is not None else ""

    rows = [
        f"| `{skill}` | {covers(skill)} | `{skills_dir}/{skill}/SKILL.md` |" for skill in skills
    ]
    return "\n".join(
        [
            "## Skills",
            "",
            "Detailed instructions live in skill files, loaded on demand. Claude Code discovers",
            "them automatically; other agents should read the relevant SKILL.md before working",
            "on a matching task.",
            "",
            "| Skill | Covers | Path |",
            "|-------|--------|------|",
            *rows,
        ]
    )


def _folder(spec: Mapping[str, Any], key: str) -> str:
    """A folder's name from the spec's directories, else its usual name."""
    value = (spec.get("directories") or {}).get(key)
    return str(value).strip("/") if isinstance(value, str) and value else key


def _app_environment(spec: dict[str, Any]) -> str | None:
    """The Framework Environment section of an app, which doesn't call ``fw.scaffold()``.
    None for other project types."""
    www, data, tests = (_folder(spec, key) for key in ("www", "data", "tests"))
    publish = (
        "- Ask before publishing anything public. GitHub Pages is public even from a "
        "private repository."
    )
    package = '- Add packages with `fw.package_add("name")`. It records them in pyproject.toml.'
    if spec.get("type") == "shiny_python":
        kind = "a Shiny for Python app"
        lines = [
            f"- The interface and server are in `app.py`. Static files go in `{www}/`, data "
            f"files in `{data}/`, and tests in `{tests}/`.",
            "- Run it with `uv run shiny run --reload app.py`.",
            package,
            publish,
        ]
    elif spec.get("type") == "fastapi":
        app = _folder(spec, "app")
        module = app.replace("/", ".")
        kind = "a FastAPI app"
        lines = [
            f"- The endpoints are in `{app}/main.py`. Tests in `{tests}/` use FastAPI's test "
            f"client. Data files go in `{data}/`.",
            f"- Run it with `uv run uvicorn {module}.main:app --reload`. The API docs are at "
            "`/docs`.",
            "- Run the tests with `uv run pytest`.",
            package,
            publish,
        ]
    else:
        return None
    return "\n".join(
        [
            f"## Framework Environment {REGENERATE}",
            "",
            f"This is {kind}. Framework manages its packages, skills, and settings. The app "
            "doesn't call `fw.scaffold()`.",
            "",
            *lines,
        ]
    )


def _environment(spec: dict[str, Any]) -> str:
    app = _app_environment(spec)
    if app is not None:
        return app
    scaffold = spec.get("scaffold") or {}
    functions = (spec.get("directories") or {}).get("functions") or "functions"
    steps = [
        "Finds the project root and loads `.env` and the settings. The working directory "
        "stays as it is.",
        "Creates framework.db if needed and records the scaffold time",
        f"Puts `{functions}/` on the import path, so its modules import directly",
        "Runs `scaffold.py` from the project root, if there is one",
    ]
    conventions = [
        '- Prefer `fw.package_add("name")` to `pip install`. It records the package in '
        "pyproject.toml.",
        f"- Put helpers shared between notebooks in `{functions}/` and import them.",
    ]
    seed = scaffold.get("seed")
    if scaffold.get("seed_on_scaffold"):
        steps.append(f"Sets the random seed to {seed}")
        conventions.append(
            "- Prefer the project's seed. When an operation needs its own seed, say why in a "
            "comment."
        )
    conventions += [
        "- Ask before running a database statement that changes data.",
        "- Ask before publishing anything public. GitHub Pages is public even from a private "
        "repository.",
    ]
    return "\n".join(
        [
            f"## Framework Environment {REGENERATE}",
            "",
            "This project uses the Framework Python package. Follow its conventions in code that",
            "calls `fw.scaffold()`. Leave existing code as it is unless the user asks you to",
            "change it.",
            "",
            "### scaffold()",
            "",
            "Start each notebook and script with `import framework as fw` and `fw.scaffold()`. It:",
            "",
            *(f"{i}. {step}" for i, step in enumerate(steps, 1)),
            "",
            "### Conventions",
            "",
            *conventions,
        ]
    )


def _packages(spec: dict[str, Any]) -> str:
    from framework.create import pyproject_dependencies

    return packages_section([name for name, _ in pyproject_dependencies(spec)])


def packages_section(names: list[str]) -> str:
    """The Installed Packages section, listing the project's dependencies by name."""
    lines = [f"## {PACKAGES_HEADING} {REGENERATE}", ""]
    lines += [f"**Dependencies** (in pyproject.toml): {', '.join(names)}", ""]
    lines.append('Add packages with `fw.package_add("name")`.')
    return "\n".join(lines)


def context_file(root: Path) -> Path | None:
    """The project's AI context file: ``ai.canonical_file`` in the settings, else AGENTS.md,
    else CLAUDE.md for older projects, as R's ``ai_regenerate_context()`` picks it."""
    from framework.settings import settings_read

    try:
        ai = settings_read(root / "settings.yml").get("ai")
        name = ai.get("canonical_file") if isinstance(ai, dict) else None
    except Exception:
        name = None
    for candidate in (name, "AGENTS.md", "CLAUDE.md"):
        if isinstance(candidate, str) and candidate and (root / candidate).is_file():
            return root / candidate
    return None


def refresh_packages(root: Path, names: list[str]) -> Path | None:
    """Rewrite the context file's Installed Packages section with ``names``.

    Only a file with the marked heading changes; the rest of it is kept. Returns the file
    when it changed.
    """
    path = context_file(root)
    if path is None:
        return None
    text = path.read_text(encoding="utf-8")
    heading = re.escape(f"## {PACKAGES_HEADING} {REGENERATE}")
    found = re.search(heading + r".*?(?=\n+## |\n*\Z)", text, re.DOTALL)
    if found is None:
        return None
    updated = text[: found.start()] + packages_section(names) + text[found.end() :]
    if updated == text:
        return None
    path.write_text(updated, encoding="utf-8")
    return path


def refresh_skills(root: Path, skills: list[str], skills_dir: str) -> Path | None:
    """Rewrite the context file's Skills section to list ``skills`` in ``skills_dir``.

    Only a file with a ``## Skills`` heading changes; the rest of it is kept. Returns the
    file when it changed.
    """
    path = context_file(root)
    if path is None:
        return None
    text = path.read_text(encoding="utf-8")
    found = re.search(r"^## Skills[^\n]*\n.*?(?=\n+## |\n*\Z)", text, re.DOTALL | re.MULTILINE)
    if found is None:
        return None
    updated = text[: found.start()] + _skills(skills, skills_dir, root) + text[found.end() :]
    if updated == text:
        return None
    path.write_text(updated, encoding="utf-8")
    return path


def _data(spec: dict[str, Any]) -> str:
    directories = spec.get("directories") or {}
    rows = [
        f"| {purpose} | `{directories[key]}/` |"
        for key, purpose in (
            (key, _PURPOSES[key]) for key in directories if key in _PURPOSES and directories[key]
        )
    ]
    table = (
        ["| Purpose | Directory |", "|---------|-----------|", *rows]
        if rows
        else ["This project has no data folders configured yet."]
    )
    return "\n".join(
        [
            f"## Data Management {REGENERATE}",
            "",
            "Prefer `fw.data_read()` and `fw.data_save()` for project data. They record a hash",
            "of each file. This project's data directories:",
            "",
            *table,
        ]
    )


def _type_section(spec: dict[str, Any]) -> str | None:
    # Sensitive data is a setting, whatever structure the project uses
    from framework._private import spec_sensitive

    if spec_sensitive(spec):
        return (
            "## Private Folders\n\n"
            "This project was created as privacy sensitive. Private files go in the\n"
            "`private/` folders, which Git ignores. The `framework-sensitive-data` skill\n"
            "describes the folders and the checks Framework runs."
        )
    if spec.get("type") == "course":
        return (
            "## Course Structure\n\n"
            "This is a teaching/course project. Lecture slides, assignments, modules, course\n"
            "documents, data, and readings each have their own folder (see settings.yml).\n\n"
            "### Creating Course Materials\n\n"
            "```python\n"
            'fw.make_revealjs("lecture-01-intro", dir="slides")\n'
            'fw.make_notebook("hw-01-basics", dir="assignments")\n'
            "```"
        )
    if spec.get("type") == "presentation":
        return (
            "## Presentation Workflow\n\n"
            "This is a presentation project with minimal structure. Edit `presentation.qmd`\n"
            "for your slides and render it with `quarto render presentation.qmd`.\n\n"
            "```python\n"
            'fw.make_revealjs("backup-slides")\n'
            "```"
        )
    # The guidelines point at the framework-workflow skill, so they need it
    if is_app_type(spec.get("type")) or "framework-workflow" not in (spec.get("skills") or {}):
        return None
    return (
        "## Workflow Guidelines\n\n"
        "Follow the standard Framework workflow (see the `framework-workflow` skill):\n"
        "import with `fw.data_read()`, clean and save with `fw.data_save()`, analyze from\n"
        "final datasets, export with `fw.save_table()`, `fw.save_figure()`, or `fw.save_model()`."
    )


def agents_md(
    spec: dict[str, Any],
    skills_dir: str,
    restricted_notice: str | None,
    root: Path | None = None,
) -> str:
    """The full AGENTS.md for a new project.

    ``restricted_notice`` names the folders agents must stay out of. It goes
    right after the header in sensitive projects. ``root`` is the project, for the
    descriptions of skills already written there. An app (Shiny for Python or FastAPI)
    has no Data Management or Workflow Guidelines section.
    """
    skills = spec.get("skills") or {}
    app = is_app_type(spec.get("type"))
    sections: list[str | None] = [_header(spec["name"])]
    if restricted_notice:
        sections.append(restricted_notice)
    sections += [
        _skills(list(skills), skills_dir, root, skills),
        _environment(spec),
        _packages(spec),
        None if app else _data(spec),
        _type_section(spec),
        "## Project Notes\n\n*Add your project-specific notes, conventions, and documentation "
        "here.*",
    ]
    return "\n\n".join(section.rstrip("\n") for section in sections if section) + "\n"
