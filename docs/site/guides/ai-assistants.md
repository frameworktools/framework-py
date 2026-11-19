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

`pull_skills()` installs missing skills, updates unedited skills (using [cloud_login()](ref:cloud_login) for the latest versions of your own Cloud skills when available), keeps locally edited skills, lists skills the setup no longer names without deleting them, and refreshes the **Skills** table in `AGENTS.md`. Pass `overwrite=True` to replace locally edited skills after backing them up to `.framework/skills-backups/`, or `dry_run=True` to preview changes.

#### Skill lock

`.framework/skills.lock.json` records each skill's identity (a built-in Framework skill, a Cloud skill, inline setup text, or a folder not yet pushed), the version last synced, and every copy of it, with the hash a sync last wrote at that copy's path. A copy that still has that hash is unedited. Any other copy is edited, including one reverted to an older version, and pull keeps it. The lock also stores every hash the package or Cloud has shipped for the skill, so a copy installed before the lock existed is recognized as an older, unedited version. Lock files written by older releases (schema 1) are read and converted in memory. A pull, push, copy, or move that changes the lock saves it in the current format (schema 2); a command that changes nothing leaves the file as it is. A lock with a newer schema stops every skill command with a message to update framework-py.

#### Rules

- A skill in several assistant folders is judged as one. When its copies differ and one of them is edited, it is diverged and left alone, with the [skills_copy()](ref:skills_copy) call that makes them agree. `overwrite=True` never chooses between two copies.
- A tracked copy missing beside unedited copies is restored. A copy missing beside an edited one is diverged.
- A newly enabled assistant folder gets the skill when its other copies are unedited. With an edited copy, the skill is diverged; `overwrite=True` backs up the edited copy and fills both folders.
- A pull never moves a skill to an older version. After `push_skills()`, a setup that still names the older version leaves the project's copy in place.
- A folder another skill tracks is kept and never written: a different Cloud skill, the other variant of the same skill, or inline setup text under a built-in's name. The row gives the [skills_move()](ref:skills_move) call that switches the folder to the setup's skill.
- A copy the lock doesn't track is left alone and listed as untracked. When a tracked copy's folder is gone and a folder of the same name exists in an assistant folder (after renaming `.claude` to `.agents`, say), both rows give the `skills_move()` call that records the move.
- A built-in last delivered by Framework Cloud is never replaced by the package's copy, offline or in `upgrade()`.
- Offline, your own Cloud skills are listed as unavailable.
- When every copy of your own Cloud skill is deleted, a signed-in pull restores it from that skill.

`pull_all()` applies any change pull would make, including a change to the lock alone (a new version number with the same files), and lists failed, diverged, and missing skills in its `failed` result.

### Moving skills

[skills_move()](ref:skills_move) moves tracked copies and their lock records together. Pass a skill folder or an assistant folder name. A folder already renamed by hand is recorded where it is now:

```python
fw.skills_move("claude", "agents")
fw.skills_move(".claude/skills/team-style", ".agents/skills/team-style")
```

With `skill_id`, `variant`, or `builtin`, the copy is retargeted to another skill. Its unedited copies stay unedited, so the next pull updates them to that skill's version. Its edited copies stay edited, and `push_skills()` refuses them until `force=True`:

```python
fw.skills_move(".claude/skills/framework-data", skill_id="5d0c2a7e-...")
fw.skills_move(".claude/skills/team-style", variant="python")
```

A move stops before any change when the target folder exists, when another skill tracks it, or when the source isn't tracked.

### Pushing edited skills

[push_skills()](ref:push_skills) publishes locally modified project skills to your Framework Cloud account (requires [cloud_login()](ref:cloud_login)):

```python
fw.push_skills()
```

Edited built-in Framework skills save as private forks in your account, and the lock tracks the copy as that fork from then on. Concurrent Cloud edits stop with `Pull first` unless `force=True` is passed; non-interactive calls require `confirm=True`. To sync a standalone custom skill directory outside project setup, call [skills_sync()](ref:skills_sync).

### Copying skills between assistants

A project can keep the same skill in several assistant folders: `.claude/skills` for Claude Code and `.agents/skills` for Codex and Copilot. `pull_skills()` writes identical copies, but editing one copy makes them diverge. [skills_sync()](ref:skills_sync) then stops and names both copies. `push_skills()` reports the skill as failed and goes on with the others, and `pull_skills()` reports it as diverged and leaves it alone. The same happens when a tracked copy is missing beside an edited one. [skills_copy()](ref:skills_copy) copies the version to keep over the others:

```python
fw.skills_copy(from_="claude", skills=["team-style"], dry_run=True)
fw.skills_copy(from_="claude", skills=["team-style"])
fw.skills_copy(from_="claude")                    # every skill, to every other assistant folder
fw.skills_copy(from_dir=".claude/skills", to_dir="some/other/skills")
```

`from_` and `to` take assistant folder names: `"claude"` for `.claude/skills`, and `"agents"` (or `"codex"` or `"copilot"`) for `.agents/skills`. `from_dir` and `to_dir` take folders inside the project instead. Neither can be empty or absolute, use `..` or `:`, overlap the other, or be in, or contain, `.framework/`, where Framework keeps its own state. Without `to`, the copy goes to every other assistant skills folder in the project.

A skill is a folder holding a regular `SKILL.md` file. Other folders are ignored, and so are folders whose names start with a dot, which is where the installer stages copies. Symlinked skill folders, broken symlinks, and folders that can't be read stop the copy with an error. Each skill's `SKILL.md` and its syncable companion files are copied, with their executable modes. Excluded files, such as `.env` and keys, are not copied and stay in the target. A replaced copy is backed up to `.framework/skills-backups/` first, and other files only the target had are removed from the active copy and kept in that backup. Identical copies are left alone. Skills only a target has are left alone and not read: they are listed, never deleted. Everything is checked before anything is written, and a copy that fails partway is undone; if a copy can't be put back, the error names it and the backup that holds it. When the copied skill is tracked in `.framework/skills.lock.json`, each target path joins its entry with the source copy's record, so pulls and pushes treat the copies as one skill. A copy of an edited skill is edited too, and a following `push_skills()` sends it as usual. A copy onto a folder another skill tracks stops with an error. A dry run leaves the lock alone, and so does a copy of a skill the lock doesn't track. A folder joins a skill's entry only this way: never because its folder name or its files match.

[pull_all()](ref:pull_all) and [push_all()](ref:push_all) sync skills alongside packages and the data registry. See [Packages](guide:packages#sync-with-framework-cloud).

### Known limits

- Use one skill per folder name in a project.
- Copy skills between assistants with [skills_copy()](ref:skills_copy), not by hand. A copy made by hand isn't tracked, and pull leaves it alone.
- On macOS, folder names that differ only in letter case are the same folder. Pick one spelling.
- A command that refuses has changed nothing. Follow its message.

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
