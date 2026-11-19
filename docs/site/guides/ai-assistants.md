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

`pull_skills()` installs missing skills, updates unmodified skills (using [cloud_login()](ref:cloud_login) for latest personal Cloud versions when available), preserves locally edited skills, leaves removed setup skills on disk with a note, and refreshes the **Skills** table in `AGENTS.md`. Pass `overwrite=True` to replace locally edited skills after backing them up to `.framework/skills-backups/`, or `dry_run=True` to preview changes. A skill whose tracked copies differ, or with one of them missing, is reported as diverged and left alone, with the [skills_copy()](ref:skills_copy) call that fixes it. So is a skill `pull_skills()` would add to a new assistant folder while its existing copy is locally edited or your own Cloud skill. A pull works per lock entry: a copy the lock doesn't track (one you made by hand, say) is left alone and listed as untracked, with the commands that track it; a skill whose files can't be read is reported as failed; a skill whose tracked folder is gone (after switching assistants, say) is reported as missing; and a problem in one entry never holds up another, even one with the same folder name. A copy that belongs to a different skill than the one the setup names is reported as kept, never as current or edited. When every copy of your own Cloud skill is deleted, a signed-in pull restores it from that skill. `pull_all()` lists any failed or diverged skill in its `failed` result. A pull never changes a skill's Cloud identity or moves it to an older version: a copy tied to a Cloud skill the setup doesn't name at that version is kept, even with `overwrite=True`. With `overwrite=True`, your edits to one of your own Cloud skills are discarded: the copy is backed up and reset to that same skill's Cloud version, same or newer. overwrite never replaces it with a different skill (another Cloud skill, the package's, or inline content); and when Framework Cloud can't be reached such a skill is reported as unavailable and left alone. A tracked skill whose copies were all deleted is put back and reported as restored; `push_skills()` reports it as missing.

### Pushing edited skills

[push_skills()](ref:push_skills) publishes locally modified project skills to your Framework Cloud account (requires [cloud_login()](ref:cloud_login)):

```python
fw.push_skills()
```

Edited built-in Framework skills save as private forks in your account rather than modifying built-in defaults. Concurrent Cloud edits stop with `Pull first` unless `force=True` is passed; non-interactive calls require `confirm=True`. To sync a standalone custom skill directory outside project setup, call [skills_sync()](ref:skills_sync).

### Copying skills between assistants

A project can keep the same skill in several assistant folders: `.claude/skills` for Claude Code and `.agents/skills` for Codex and Copilot. `pull_skills()` writes identical copies, but editing one copy makes them diverge. [skills_sync()](ref:skills_sync) then stops with `Assistant skill copies diverged`, naming both copies; `push_skills()` reports the skill as failed and goes on with the others, and `pull_skills()` leaves it alone. The same happens when a tracked copy is missing. [skills_copy()](ref:skills_copy) copies the version to keep over the others:

```python
fw.skills_copy(from_="claude", skills=["team-style"], dry_run=True)
fw.skills_copy(from_="claude", skills=["team-style"])
fw.skills_copy(from_="claude")                    # every skill, to every other assistant folder
fw.skills_copy(from_dir=".claude/skills", to_dir="some/other/skills")
```

`from_` and `to` take assistant folder names: `"claude"` for `.claude/skills`, and `"agents"` (or `"codex"` or `"copilot"`) for `.agents/skills`. `from_dir` and `to_dir` take folders inside the project instead. Neither can be empty or absolute, use `..` or `:`, overlap the other, or be in, or contain, `.framework/`, where Framework keeps its own state. Without `to`, the copy goes to every other assistant skills folder in the project.

A skill is a folder holding a regular `SKILL.md` file. Other folders are ignored, and so are folders whose names start with a dot, which is where the installer stages copies. Symlinked skill folders, broken symlinks, and folders that can't be read stop the copy with an error. Each skill's `SKILL.md` and its syncable companion files are copied, with their executable modes. Excluded files, such as `.env` and keys, are not copied and stay in the target. A replaced copy is backed up to `.framework/skills-backups/` first, and other files only the target had are removed from the active copy and kept in that backup. Identical copies are left alone. Skills only a target has are left alone and not read: they are listed, never deleted. Everything is checked before anything is written, and a copy that fails partway is undone; if a copy can't be put back, the error names it and the backup that holds it. When the copied skill is tracked in `.framework/skills.lock.json`, each target path joins its entry, so pulls and pushes treat the copies as one skill and move them to the same version. Nothing else in the lock changes, so a following `push_skills()` sends the edited skill as usual. A dry run leaves the lock alone, and so does a copy of a skill the lock doesn't track. A folder joins a skill's entry only this way: never because its folder name or its files match.

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
