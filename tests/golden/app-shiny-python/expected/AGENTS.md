# Demo App

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
| `git-workflow` | Use when reviewing changes, committing analysis work, resolving merge conflicts, or coordinating branches in a research repository. | `.agents/skills/git-workflow/SKILL.md` |
| `framework-packages` | adding and managing Python packages with package_add() | `.agents/skills/framework-packages/SKILL.md` |

## Framework Environment <!-- @framework:regenerate -->

This is a Shiny for Python app. Framework manages its packages, skills, and settings. The app doesn't call `fw.scaffold()`.

- The interface and server are in `app.py`. Static files go in `www/`, data files in `data/`, and tests in `tests/`.
- Run it with `uv run shiny run --reload app.py`.
- Add packages with `fw.package_add("name")`. It records them in pyproject.toml.
- Ask before publishing anything public. GitHub Pages is public even from a private repository.

## Installed Packages <!-- @framework:regenerate -->

**Dependencies** (in pyproject.toml): framework-py, numpy, shiny, pandas

Add packages with `fw.package_add("name")`.

## Project Notes

*Add your project-specific notes, conventions, and documentation here.*
