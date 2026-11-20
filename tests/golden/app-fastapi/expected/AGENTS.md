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

This is a FastAPI app. Framework manages its packages, skills, and settings. The app doesn't call `fw.scaffold()`.

- The endpoints are in `app/main.py`. Tests in `tests/` use FastAPI's test client. Data files go in `data/`.
- Run it with `uv run uvicorn app.main:app --reload`. The API docs are at `/docs`.
- Run the tests with `uv run pytest`.
- Add packages with `fw.package_add("name")`. It records them in pyproject.toml.
- Ask before publishing anything public. GitHub Pages is public even from a private repository.

## Installed Packages <!-- @framework:regenerate -->

**Dependencies** (in pyproject.toml): framework-py, httpx, pytest, fastapi, uvicorn, pydantic

Add packages with `fw.package_add("name")`.

## Project Notes

*Add your project-specific notes, conventions, and documentation here.*
