# Golden Removals And Assistants

This file provides guidance to AI assistants working with this Framework project.
It is the canonical context file; detailed workflow instructions live in the
skills listed below. Edit the sections without regeneration markers freely -
they won't be overwritten.

## Skills

Detailed instructions live in skill files, loaded on demand. Claude Code discovers
them automatically; other agents should read the relevant SKILL.md before working
on a matching task.

| Skill | Covers | Path |
|-------|--------|------|
| `framework-workflow` | Framework package initialization and notebook or script creation | `.agents/skills/framework-workflow/SKILL.md` |
| `framework-data` | reading and saving data with data_read() and data_save() | `.agents/skills/framework-data/SKILL.md` |
| `framework-packages` | adding and managing Python packages with package_add() | `.agents/skills/framework-packages/SKILL.md` |
| `framework-outputs` | saving results with the Framework package | `.agents/skills/framework-outputs/SKILL.md` |
| `framework-cache` | caching computations with the Framework package | `.agents/skills/framework-cache/SKILL.md` |
| `framework-databases` | querying configured Framework databases | `.agents/skills/framework-databases/SKILL.md` |
| `framework-publishing` | publishing artifacts with the Framework package | `.agents/skills/framework-publishing/SKILL.md` |

## Framework Environment <!-- @framework:regenerate -->

This project uses the Framework Python package. Follow its conventions in code that
calls `fw.scaffold()`. Leave existing code as it is unless the user asks you to
change it.

### scaffold()

Start each notebook and script with `import framework as fw` and `fw.scaffold()`. It:

1. Finds the project root and loads `.env` and the settings. The working directory stays as it is.
2. Creates framework.db if needed and records the scaffold time
3. Puts `functions/` on the import path, so its modules import directly
4. Runs `scaffold.py` from the project root, if there is one

### Conventions

- Prefer `fw.package_add("name")` to `pip install`. It records the package in pyproject.toml.
- Put helpers shared between notebooks in `functions/` and import them.
- Ask before running a database statement that changes data.
- Ask before publishing anything public. GitHub Pages is public even from a private repository.

## Installed Packages <!-- @framework:regenerate -->

**Dependencies** (in pyproject.toml): framework-py, ipykernel, nbformat, nbclient

Add packages with `fw.package_add("name")`.

## Data Management <!-- @framework:regenerate -->

Prefer `fw.data_read()` and `fw.data_save()` for project data. They record a hash
of each file. This project's data directories:

| Purpose | Directory |
|---------|-----------|
| Raw data (immutable) | `inputs/raw/` |
| Intermediate/cleaned data | `inputs/intermediate/` |
| Analysis-ready data | `inputs/final/` |
| Output tables | `outputs/tables/` |
| Output figures | `outputs/figures/` |
| Saved models | `outputs/models/` |
| Reports | `outputs/reports/` |

## Workflow Guidelines

Follow the standard Framework workflow (see the `framework-workflow` skill):
import with `fw.data_read()`, clean and save with `fw.data_save()`, analyze from
final datasets, export with `fw.save_table()`, `fw.save_figure()`, or `fw.save_model()`.

## Project Notes

*Add your project-specific notes, conventions, and documentation here.*
