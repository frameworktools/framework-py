# Golden Project Sensitive

This file provides guidance to AI assistants working with this Framework project.
It is the canonical context file; detailed workflow instructions live in the
skills listed below. Edit the sections without regeneration markers freely -
they won't be overwritten.

This project holds restricted data. Do not read or print files in `inputs/private/`, `outputs/private/`. Work from the data dictionary and simulated data instead.

## Skills

Detailed instructions live in skill files, loaded on demand. Claude Code discovers
them automatically; other agents should read the relevant SKILL.md before working
on a matching task.

| Skill | Covers | Path |
|-------|--------|------|
| `framework-workflow` | scaffold() initialization, critical rules, creating notebooks/scripts | `.claude/skills/framework-workflow/SKILL.md` |
| `framework-data` | reading and saving data (data_read/data_save are mandatory) | `.claude/skills/framework-data/SKILL.md` |
| `framework-packages` | adding and managing Python packages (package_add, never pip install) | `.claude/skills/framework-packages/SKILL.md` |
| `framework-outputs` | results, caching, database queries, publishing | `.claude/skills/framework-outputs/SKILL.md` |
| `framework-sensitive-data` | PII/PHI handling and private/public directory rules | `.claude/skills/framework-sensitive-data/SKILL.md` |

## Framework Environment <!-- @framework:regenerate -->

This project uses Framework for reproducible data analysis. **Every notebook and script
MUST begin with `import framework as fw` and `fw.scaffold()`**, which initializes the environment.

### What scaffold() Does

1. **Finds the project root** and loads `.env` and the settings (it does not change the working directory)
2. **Creates framework.db** if needed and records the scaffold time
3. **Puts `functions/` on the import path**, so its modules import directly
4. **Runs `scaffold.py`** from the project root, if there is one

### CRITICAL RULES

**DO NOT** run `pip install` or `uv add` yourself. Add packages with `fw.package_add("name")`, which records them in pyproject.toml.

**DO NOT** copy helpers between notebooks. Put them in `functions/` and import them.

## Installed Packages <!-- @framework:regenerate -->

**Dependencies** (in pyproject.toml): framework-py

Add packages with `fw.package_add("name")`.

## Data Management <!-- @framework:regenerate -->

**All data I/O MUST use `fw.data_read()` and `fw.data_save()`** - see the
`framework-data` skill for the full rules. This project's data directories:

| Purpose | Directory |
|---------|-----------|
| Private raw data (PII/PHI) | `inputs/private/raw/` |
| Public raw data | `inputs/public/raw/` |
| Private intermediate | `inputs/private/intermediate/` |
| Public intermediate | `inputs/public/intermediate/` |
| Private analysis-ready | `inputs/private/final/` |
| Public analysis-ready | `inputs/public/final/` |
| Private tables | `outputs/private/tables/` |
| Public tables | `outputs/public/tables/` |
| Private figures | `outputs/private/figures/` |
| Public figures | `outputs/public/figures/` |

## Privacy Requirements

**This is a privacy-sensitive project.** Before ANY data operation, output, or
commit, follow the `framework-sensitive-data` skill. Never commit or publish
anything under a `private/` directory.

## Project Notes

*Add your project-specific notes, conventions, and documentation here.*
