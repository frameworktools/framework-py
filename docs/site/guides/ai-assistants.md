---
id: ai-assistants
title: AI assistants
description: AGENTS.md, assistant stubs, Framework skills, and the guards for privacy-sensitive projects
section: features
position: 13
---

## Overview

Framework prepares each project for AI coding assistants. It writes `AGENTS.md` as the canonical instruction file, installs skills that teach assistants the Framework functions, and adds a small pointer file for assistants that look for their own file name. In privacy-sensitive projects, it also tells assistants which folders they must not read and, for Claude Code, enforces that rule in the assistant's settings.

## Supported assistants

The `ai` section of the settings lists the assistants a project supports.

```yaml
ai:
  enabled: true
  canonical_file: AGENTS.md
  assistants:
  - claude
```

Each assistant gets a skills folder and, for some, a stub file.

| Assistant | Skills folder | Stub file |
|-----------|---------------|-----------|
| `claude` (Claude Code) | `.claude/skills/` | `CLAUDE.md` |
| `codex` (OpenAI Codex) | `.agents/skills/` | none, because Codex reads `AGENTS.md` |
| `copilot` (GitHub Copilot) | `.agents/skills/` | `.github/copilot-instructions.md` |

Claude Code is selected by default. A list under `defaults.ai.assistants` in your global settings replaces that selection, so Claude Code is selected only when the list names `claude`. `defaults.ai.enabled: false` creates projects without AI files. When `ai.enabled` is on and no assistant is selected, the skills go to `.agents/skills/` only. The [Setup](guide:setup) guide covers the global settings.

## AGENTS.md

`AGENTS.md` is the single source of project instructions. When Framework creates a project, it writes a guide generated from the project's settings, with these sections.

- **Skills.** A table of the installed skills, what each covers, and the path to its `SKILL.md` in the first assistant's skills folder.
- **Framework Environment.** What [scaffold()](ref:scaffold) does, and the rules that follow from it, such as adding packages with [package_add()](ref:package_add) instead of `pip install`.
- **Installed Packages.** The project's dependencies. [package_add()](ref:package_add) and [package_set_add()](ref:package_set_add) rewrite this section when they add a package.
- **Data Management.** The rule to read and save data with [data_read()](ref:data_read) and [data_save()](ref:data_save), and a table of the project's data folders.
- **Workflow, course, presentation, or privacy notes**, depending on the project. A privacy-sensitive project also gets a line right after the title that names the private folders and tells assistants not to read or print files in them.
- **Project Notes.** Space for your own instructions.

Add your own instructions under **Project Notes**: the study design, naming habits, data caveats, and anything else an assistant should know. You can edit any section. Apart from the package list, Framework does not rewrite `AGENTS.md` after creation. The R package writes the same sections, with R's functions.

The stub files point back to it. `CLAUDE.md` is written only when Claude Code is selected, and it contains one line, which tells Claude Code to read `AGENTS.md`.

```markdown
@AGENTS.md
```

`.github/copilot-instructions.md` asks Copilot to read `AGENTS.md` and not to duplicate its content. Keep instructions in `AGENTS.md`, so every assistant reads the same text.

## Skills

Skills are instruction files that an assistant loads when a task matches the skill's description. framework-py ships five.

| Skill | Teaches |
|-------|---------|
| `framework-workflow` | Starting notebooks and scripts with `fw.scaffold()`, the analysis workflow, and `make_notebook()` and `make_script()` |
| `framework-data` | Reading and writing data with `data_read()` and `data_save()`, and the integrity hashes |
| `framework-packages` | Adding packages with `package_add()` or `uv add` instead of a bare `pip install` |
| `framework-outputs` | `save_table()`, `save_figure()`, `save_model()`, `cache_remember()`, `db_query()`, and `publish()` |
| `framework-sensitive-data` | Private and public folders and the handling of identifiable data |

Every project except a bare one gets the first four. Privacy-sensitive projects also get `framework-sensitive-data`. Each skill is a folder with a `SKILL.md` file, copied into each selected assistant's skills folder.

```text
.claude/skills/
├── framework-data/SKILL.md
├── framework-outputs/SKILL.md
├── framework-packages/SKILL.md
└── framework-workflow/SKILL.md
```

Projects created from a framework.pub blueprint can carry additional skills. You can also add your own skill folders beside Framework's.

### Updating skills

A project's skills come from its setup on Framework Cloud: its blueprint, skill sets, and single skills. [pull_skills()](ref:pull_skills) brings the project's skills up to date with that setup.

```python
import framework as fw

fw.pull_skills()
```

It reads the project's current skill list with the project's token, then:

- adds skills the project is missing,
- updates unedited skills to their latest versions: Framework's from the installed framework-py, and your Cloud skills from Cloud,
- keeps skills you edited and lists them,
- lists skills that are no longer in the project's setup, without deleting them,
- refreshes the Skills table in `AGENTS.md`.

It prints a summary:

```text
Skills: 2 added, 3 updated, 5 up to date, 1 locally edited (kept)
  Kept (locally edited): framework-data — pull_skills(overwrite=True) replaces it
```

`overwrite=True` replaces edited skills too. The old copies go to `.framework/skills-backups/`. `dry_run=True` shows what would change and writes nothing. The latest versions of your Cloud skills need [cloud_login()](ref:cloud_login); without it, the versions in the project's setup are used.

Offline, or without a project token, it uses the Framework skills in the project's settings and says so. [ai_skills_update()](ref:ai_skills_update) does the same as `pull_skills()`.

### Sending edited skills to Cloud

[push_skills()](ref:push_skills) sends skills you edited in the project to Framework Cloud as new versions of your own skills. It needs [cloud_login()](ref:cloud_login).

```python
fw.push_skills()
```

Before pushing, it names what each new version affects and asks to continue. Without a console, pass `confirm=True`. A skill that changed on Cloud since the project pulled it is refused with "Pull first". `force=True` pushes it anyway; the Cloud changes stay in its history. A built-in Framework skill never changes: an edited one is saved as your own copy. `dry_run=True` lists what would be pushed.

Only skills Framework installed are pushed. [skills_sync()](ref:skills_sync) syncs one skill folder, including one you wrote.

[pull_setup()](ref:pull_setup) and [push_setup()](ref:push_setup) do the same for the whole setup: packages and skills together. See [Packages](guide:packages#setup-on-framework-cloud).

## Privacy-sensitive projects

Privacy-sensitive projects add two protections for restricted data.

`AGENTS.md` names the private folders and tells assistants not to read or print files in them.

```markdown
This project holds restricted data. Do not read or print files in `inputs/private/`, `outputs/private/`. Work from the data dictionary and simulated data instead.
```

When Claude Code is selected, Framework also writes `.claude/settings.json` with rules that Claude Code enforces.

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

The deny rules block Claude Code's own file tools. The sandbox's `denyRead` list also blocks commands it runs, such as `python`, from reading those paths. `failIfUnavailable` stops Claude Code from running commands when the sandbox cannot start, so the rules fail closed.

The private folders are computed from the project's `directories`, `extra_directories`, and `render_dirs`: each path with a segment named `private` or ending in `_private`, up to and including that segment, and each `directories` entry whose key marks it private (`inputs_private_raw: inputs/raw`), as configured. A folder inside another one on the list is left out. The rules match the ones the R package writes. Existing entries in `.claude/settings.json` are kept. [upgrade()](ref:upgrade) writes these rules in every sensitive project, whatever the AI settings say, since anyone can open the project in Claude Code, and says when git ignores `.claude/`, which would keep the rules on one computer.

The `framework-sensitive-data` skill gives assistants the same rules in prose, together with guidance for working from data dictionaries and simulated data.

## Differences from R

The R package once kept its context files in sync with a git hook. framework-py writes `AGENTS.md` once, when the project is created, and relies on skills for detailed instructions, so it has no context regeneration and no `ai_sync` hook.
