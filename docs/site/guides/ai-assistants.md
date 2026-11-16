---
id: ai-assistants
title: AI assistants
description: AGENTS.md instructions, assistant pointer files, Framework skills, Cloud skill sync, and Claude Code sandbox guards
section: features
position: 13
---

## Overview

Framework configures projects for AI coding assistants by writing `AGENTS.md` as the single instruction file, installing task-specific skills, adding assistant pointer files where needed, and enforcing filesystem read guards in privacy-sensitive projects.

## Assistant configuration

Configure supported assistants under `ai` in `settings.yml` (or `settings/ai.yml`):

```yaml
ai:
  enabled: true
  canonical_file: AGENTS.md
  assistants:
    - claude
```

| Assistant | Skills directory | Pointer file |
|-----------|------------------|--------------|
| `claude` (Claude Code) | `.claude/skills/` | `CLAUDE.md` (`@AGENTS.md`) |
| `codex` (OpenAI Codex) | `.agents/skills/` | None (reads `AGENTS.md` natively) |
| `copilot` (GitHub Copilot) | `.agents/skills/` | `.github/copilot-instructions.md` |

`claude` is enabled by default. Setting `defaults.ai.assistants` in global settings replaces that list, and `defaults.ai.enabled: false` omits AI files entirely. When `ai.enabled: true` with an empty `assistants` list, skills install to `.agents/skills/`. See [Setup](guide:setup).

## AGENTS.md

`new_project()` generates `AGENTS.md` from project settings with six sections:

- **Skills:** table of installed skills, descriptions, and `SKILL.md` paths.
- **Framework Environment:** `fw.scaffold()` startup behavior and package rules.
- **Installed Packages:** `pyproject.toml` dependencies, refreshed automatically by [package_add()](ref:package_add) and [package_set_add()](ref:package_set_add) while the `<!-- @framework:regenerate -->` marker remains.
- **Data Management:** `data_read()` and `data_save()` conventions and configured data directories.
- **Project-type notes:** workflow, course, presentation, or sensitive-data rules (sensitive projects place a restricted-folder notice directly beneath the title).
- **Project Notes:** open section for study design, domain conventions, and caveats.

Keep all project instructions in `AGENTS.md` so every assistant reads one source. `CLAUDE.md` contains only `@AGENTS.md`, and `.github/copilot-instructions.md` directs Copilot to `AGENTS.md`.

## Built-in skills

`framework-py` bundles eight skills:

| Skill | Scope |
|-------|-------|
| `framework-workflow` | `fw.scaffold()`, project conventions, `make_notebook()`, and `make_script()` |
| `framework-data` | `data_read()`, `data_save()`, catalog keys, and SHA-256 integrity checks |
| `framework-packages` | Managing dependencies via `package_add()` and `uv add` rather than `pip install` |
| `framework-outputs` | `save_table()`, `save_figure()`, `save_model()`, `save_notebook()`, and `result_list()` |
| `framework-cache` | `cache_remember()`, `cache()`, `cache_get()`, and `cache_forget()` |
| `framework-databases` | `db_query()`, `db_execute()`, and `db_connect()` |
| `framework-publishing` | Publishing destinations and private-file checks |
| `framework-sensitive-data` | Private/public directory boundaries and working from schemas or synthetic data |

Every non-`bare` project installs the first seven skills; privacy-sensitive projects also install `framework-sensitive-data`:

```text
.claude/skills/
├── framework-cache/SKILL.md
├── framework-data/SKILL.md
├── framework-databases/SKILL.md
├── framework-outputs/SKILL.md
├── framework-packages/SKILL.md
├── framework-publishing/SKILL.md
└── framework-workflow/SKILL.md
```

### Skill updates

[pull_skills()](ref:pull_skills) (and its alias [ai_skills_update()](ref:ai_skills_update)) reconciles installed skills with the project's Framework Cloud setup (or local `framework-py` release when offline):

```python
import framework as fw

fw.pull_skills()
```

`pull_skills()` installs missing skills, updates unmodified skills (using [cloud_login()](ref:cloud_login) for latest personal Cloud versions when available), preserves locally edited skills, leaves removed setup skills on disk with a note, and refreshes the **Skills** table in `AGENTS.md`. Pass `overwrite=True` to replace locally edited skills after backing them up to `.framework/skills-backups/`, or `dry_run=True` to preview changes.

### Pushing edited skills

[push_skills()](ref:push_skills) publishes locally modified project skills to your Framework Cloud account (requires [cloud_login()](ref:cloud_login)):

```python
fw.push_skills()
```

Edited built-in Framework skills save as private forks in your account rather than modifying built-in defaults. Concurrent Cloud edits stop with `Pull first` unless `force=True` is passed; non-interactive calls require `confirm=True`. To sync a standalone custom skill directory outside project setup, call [skills_sync()](ref:skills_sync).

[pull_all()](ref:pull_all) and [push_all()](ref:push_all) sync skills alongside packages and the data registry. See [Packages](guide:packages#sync-with-framework-cloud).

## Privacy-sensitive guardrails

Privacy-sensitive projects layer prose instructions with enforced tool permissions:

1. `AGENTS.md` and `framework-sensitive-data` instruct assistants never to read or print files under `private/` directories and to inspect schemas via `data_info(..., columns=True)` or synthetic data instead.
2. When Claude Code is enabled (or whenever [upgrade()](ref:upgrade) runs on a sensitive project), Framework writes `.claude/settings.json` denying both built-in file tools and sandboxed shell commands (`python`, `cat`, etc.) access to private directories and `.env`, with `failIfUnavailable: true` so execution fails closed if sandboxing is unavailable:

```json
{
  "permissions": {
    "deny": [
      "Read(/inputs/private/**)",
      "Edit(/inputs/private/**)",
      "Read(/outputs/private/**)",
      "Edit(/outputs/private/**)",
      "Read(/.env)"
    ]
  },
  "sandbox": {
    "enabled": true,
    "failIfUnavailable": true,
    "filesystem": {
      "denyRead": [
        "./inputs/private",
        "./outputs/private",
        "./.env"
      ]
    }
  }
}
```
