"""The AGENTS.md guide a new project gets, generated from its spec.

The sections follow R's AGENTS.md, with Python's functions. Headings marked
``<!-- @framework:regenerate -->`` describe the settings. The others are the
user's to edit.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

REGENERATE = "<!-- @framework:regenerate -->"
PACKAGES_HEADING = "Installed Packages"

SKILL_DESCRIPTIONS = {
    "framework-workflow": "scaffold() initialization, critical rules, creating notebooks/scripts",
    "framework-data": "reading and saving data (data_read/data_save are mandatory)",
    "framework-packages": "adding and managing Python packages (package_add, never pip install)",
    "framework-outputs": "results, caching, database queries, publishing",
    "framework-sensitive-data": "PII/PHI handling and private/public directory rules",
}

_PURPOSES = {
    "inputs_raw": "Raw data (immutable)",
    "inputs_intermediate": "Intermediate/cleaned data",
    "inputs_final": "Analysis-ready data",
    "outputs_tables": "Output tables",
    "outputs_figures": "Output figures",
    "outputs_models": "Saved models",
    "outputs_reports": "Reports",
    "inputs_private_raw": "Private raw data (PII/PHI)",
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


def _skills(skills: list[str], skills_dir: str) -> str:
    rows = [
        f"| `{skill}` | {SKILL_DESCRIPTIONS.get(skill, '')} | `{skills_dir}/{skill}/SKILL.md` |"
        for skill in skills
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


def _environment(spec: dict[str, Any]) -> str:
    scaffold = spec.get("scaffold") or {}
    functions = (spec.get("directories") or {}).get("functions") or "functions"
    steps = [
        "1. **Finds the project root** and loads `.env` and the settings (it does not change "
        "the working directory)",
        "2. **Creates framework.db** if needed and records the scaffold time",
        f"3. **Puts `{functions}/` on the import path**, so its modules import directly",
        "4. **Runs `scaffold.py`** from the project root, if there is one",
    ]
    rules = [
        "**DO NOT** run `pip install` or `uv add` yourself. Add packages with "
        '`fw.package_add("name")`, which records them in pyproject.toml.',
        "",
        f"**DO NOT** copy helpers between notebooks. Put them in `{functions}/` and import them.",
    ]
    seed = scaffold.get("seed")
    if scaffold.get("seed_on_scaffold"):
        steps.append(f"5. **Sets the random seed to {seed}** for reproducibility")
        rules += [
            "",
            f"**DO NOT** reseed after scaffold(). The seed is already set to {seed}.",
            "If you need a different seed for a specific operation, document why.",
        ]
    return "\n".join(
        [
            f"## Framework Environment {REGENERATE}",
            "",
            "This project uses Framework for reproducible data analysis. **Every notebook and "
            "script",
            "MUST begin with `import framework as fw` and `fw.scaffold()`**, which initializes "
            "the environment.",
            "",
            "### What scaffold() Does",
            "",
            *steps,
            "",
            "### CRITICAL RULES",
            "",
            *rules,
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
            "**All data I/O MUST use `fw.data_read()` and `fw.data_save()`** - see the",
            "`framework-data` skill for the full rules. This project's data directories:",
            "",
            *table,
        ]
    )


def _type_section(spec: dict[str, Any]) -> str:
    # Sensitive data is a setting, whatever structure the project uses
    if (spec.get("security") or {}).get("sensitive"):
        return (
            "## Privacy Requirements\n\n"
            "**This is a privacy-sensitive project.** Before ANY data operation, output, or\n"
            "commit, follow the `framework-sensitive-data` skill. Never commit or publish\n"
            "anything under a `private/` directory."
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
    return (
        "## Workflow Guidelines\n\n"
        "Follow the standard Framework workflow (see the `framework-workflow` skill):\n"
        "import with `fw.data_read()`, clean and save with `fw.data_save()`, analyze from\n"
        "final datasets, export with `fw.save_table()`, `fw.save_figure()`, or `fw.save_model()`."
    )


def agents_md(spec: dict[str, Any], skills_dir: str, restricted_notice: str | None) -> str:
    """The full AGENTS.md for a new project.

    ``restricted_notice`` names the folders agents must stay out of. It goes
    right after the header in sensitive projects.
    """
    sections = [_header(spec["name"])]
    if restricted_notice:
        sections.append(restricted_notice)
    sections += [
        _skills(list(spec.get("skills") or {}), skills_dir),
        _environment(spec),
        _packages(spec),
        _data(spec),
        _type_section(spec),
        "## Project Notes\n\n*Add your project-specific notes, conventions, and documentation "
        "here.*",
    ]
    return "\n\n".join(section.rstrip("\n") for section in sections) + "\n"
