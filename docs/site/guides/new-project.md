---
id: new-project
title: New projects
description: Project creation, CLI commands, Framework Cloud blueprints, adoption, upgrades, and bootstrap()
section: getting_started
position: 5
---

## Python API

[new_project()](ref:new_project) creates a Python project from your global defaults:

```python
import framework as fw

fw.new_project("Vaccine Study", "~/code/vaccine-study", type="project")
```

Pass `type` as `project`, `project_sensitive`, `course`, `presentation`, or `bare`. See [Project types](guide:project-types) for directory layouts.

```python
fw.new_project("Patient Outcomes", "~/code/patient-outcomes", type="project_sensitive")
fw.new_project("Stats 101", "~/teaching/stats-101", type="course")
fw.new_project("Q4 Review", "~/talks/q4-review", type="presentation")
```

In an interactive terminal, `new_project()` prompts for omitted arguments and defaults `type` to `project`. When `location` is omitted, it proposes `./<slug>` in the current directory, or `<projects_folder>/<slug>` when a Projects folder is configured in Framework Cloud **Settings > Project defaults** (or cached locally via `cloud_sync()`):

```text
Create the project in /home/ada/research/vaccine-study? [y/n]
```

Framework creates the parent Projects folder if missing. Press Enter or `y` to accept, or `n` to enter another directory (blank uses the current directory). In non-interactive scripts and notebooks, omitting `name` or `location` raises an error, and omitting `type` defaults to `project`. Pass `interactive=True` or `interactive=False` to override terminal detection.

`new_project()` expands `~`, resolves relative paths against the current working directory, requires a non-existent target path, and returns the created `Path`.

### Workflows

Pass `workflow` to choose the default authoring format:

| Workflow | Enabled tools | `make_notebook()` output |
|----------|---------------|--------------------------|
| `jupyter` | `jupyter`, `quarto` | Jupyter notebooks (`.ipynb`), with Quarto kept for rendering and publishing |
| `quarto` | `quarto` | Quarto documents (`.qmd`) |
| `scripts` | none (`quarto` and `jupyter` disabled) | Python scripts (`.py`) |

`project` and `project_sensitive` default to `jupyter`. `course` and `presentation` use `quarto`, and `bare` uses no workflow. `scripts` omits starter notebooks unless supplied by a blueprint. Framework records both `workflow` and `notebook_format` under `scaffold` in `settings.yml`.

```python
fw.new_project("Pipeline", "~/code/pipeline", type="project", workflow="scripts")
```

### Tools and sets

Tools add optional files, directories, and dependencies. [tools_available()](ref:tools_available) lists Python tools:

- `uv`: writes `pyproject.toml` and manages `uv.lock` (enabled for all project types).
- `quarto`: writes `_quarto.yml`, render settings, and `ipykernel`, `nbformat`, and `nbclient` dependencies (enabled for all types except `bare`).
- `jupyter`: adds `ipykernel` and `jupyterlab` so `uv run jupyter lab` works immediately (enabled by the `jupyter` workflow).
- `quarto_slides`: adds a RevealJS `slides/` directory.
- `quarto_manuscript`: adds a Quarto `manuscript/` directory.

Pass `tools` as a list to enable extras or as a dictionary to disable defaults:

```python
fw.new_project(
    "Lab Notes", "~/code/lab-notes", type="project", tools={"quarto": False, "jupyter": True}
)
fw.new_project("Q4 Review", "~/talks/q4-review", type="project", tools=["quarto_slides"])
```

Enabling `quarto_slides` or `quarto_manuscript` automatically enables `quarto`. Disabling a required dependency raises an error. Framework records active tools under `tools` in `settings.yml`; older projects without `tools` default to Quarto enabled.

Pass `skill_sets` and `package_sets` to bundle AI skills and packages (for example, `framework` installs Framework's four core skills). Tool and set definitions match the R package and Framework Cloud.

## Command-line creation

Run the module CLI to create projects from a shell:

```sh
python -m framework create --name "Vaccine Study" --path ~/code/vaccine-study --type project
```

`--type` defaults to `project`. Pass `--interactive` to prompt for missing options, `--tool ID` or `--tool ID=false` to toggle tools, and repeatable `--skill-set` or `--package-set` flags to attach sets.

Before installing `framework-py` into a project, run it ephemerally with `uv`:

```sh
uv run --with framework-py python -m framework create --name "Vaccine Study" --path ~/code/vaccine-study --type project
```

### fw CLI

The cross-language `fw` CLI delegates Python project creation to `python -m framework create`:

```sh
fw new vaccine-study --lang python --type project
```

`--dir` names the project folder. Without it, `fw new` offers `./<name>` in an interactive terminal and stops elsewhere.

### Layers files

Framework Cloud exports resolved project definitions as layers files. Build a project from one with `--from-layers` or [project_create_from_layers()](ref:project_create_from_layers):

```sh
python -m framework create --from-layers layers.json --path .
```

`--from-layers` runs headless unless `--interactive` is set: it skips prompts, git initialization, `.github/workflows/` changes, and global project registration, allowing the Framework Cloud GitHub Action to scaffold into an initialized checkout.

## Framework Cloud projects

Create a local checkout from a Framework Cloud project token:

```python
fw.new_project(key="fw_...")
fw.new(key="fw_...", url="https://cloud.example.edu")
```

`url` targets a self-hosted instance without signing in first; otherwise Framework checks `FW_CLOUD_URL`, your saved login server, and `https://framework.pub`. `new_project()` downloads the project spec, scaffolds the directory, and writes `FW_PROJECT_TOKEN` and `FW_CLOUD_URL` to `.env` for publishing and ledger verification. Scripts must pass `location` explicitly. See [Framework Cloud](guide:cloud).

### Blueprints and structures

Sign in with [cloud_login()](ref:cloud_login) to create projects from Cloud blueprints, structures, and sets:

```python
fw.new_project("Lab Notes", "~/code/lab-notes", blueprint="ada/lab")
fw.new_project(
    "Stats 101",
    "~/teaching/stats-101",
    structure="framework/course",
    tools=["quarto_slides"],
    skill_sets=["ada/teaching"],
    sensitive=False,
)
```

`structure` overrides a blueprint's folder layout. Bare tool and set IDs such as `quarto` resolve under `framework/`. `new_project()` resolves layers through Framework Cloud, scaffolds the project locally, registers it on the server, and stores `FW_PROJECT_TOKEN` in `.env`. Only configuration metadata leaves your machine; data and code remain local.

When signed in with no explicit blueprint choices, `new_project()` caches your resolved Cloud defaults under `cache/defaults/` in the configuration directory. If the server is unreachable, default calls build from that cache and print its date; other calls, and default calls with no cache, prompt in interactive sessions or require `offline=True` in scripts:

```python
fw.new_project("Field Notes", "~/code/field-notes", offline=True)
```

Rejected tokens always raise an error rather than falling back offline. Signed-out sessions use built-in defaults directly.

## Generated project files

Depending on project type and tools, `new_project()` writes:

- Project directories for the chosen type
- `settings.yml` (plus `settings/*.yml` split files for `project` and `project_sensitive`)
- `pyproject.toml` configured for `uv` (`package = false`) with `framework-py` and tool dependencies
- `scaffold.py`, executed by [scaffold()](ref:scaffold) after loading settings
- `.env` with empty PostgreSQL and S3 placeholders, ignored by `.gitignore`
- `framework.db` SQLite tracking database
- `_quarto.yml` and per-folder `_metadata.yml` files when Quarto is enabled
- `AGENTS.md`, assistant skills, and `CLAUDE.md` when AI support is enabled
- `<slug>.code-workspace` for VS Code and Positron
- `presentation.qmd` for `presentation` projects
- `.framework/spec.json` recording the resolved v1 project specification

`bare` projects include only `settings.yml`, `pyproject.toml`, `.gitignore`, and `.framework/spec.json`.

Post-creation steps:

1. Initializes git (when installed and enabled), creates the initial commit when `user.name` and `user.email` are set, and installs `.git/hooks/pre-commit` when any hook check is enabled.
2. Registers the project in global `settings.yml` (unless inside the system temp directory).
3. Registers non-`bare` projects on Framework Cloud when signed in and writes `FW_PROJECT_TOKEN` to `.env`.

Install project packages before starting work:

```sh
cd ~/code/vaccine-study
uv sync
```

## Existing project adoption

[adopt()](ref:adopt) converts an existing directory into a Framework project in place without moving files. It scans folder names up to three levels deep (never file contents) and proposes mappings to Framework `directories`, `extra_directories`, or `render_dirs`, skipping hidden directories, `.venv`, `renv`, caches, `settings/`, and Quarto `*_files/` or `*_cache/` folders.

```python
fw.adopt("~/code/old-study")  # interactive mapping review
fw.adopt("~/code/old-study", yes=True)  # accept proposed mapping
fw.adopt("~/code/cohort", yes=True, sensitive=True)  # mark sensitive
fw.adopt("~/teaching/methods", yes=True, type="course")  # explicit type
fw.adopt("~/code/old-study", yes=True, cloud=True)  # register on Framework Cloud
```

Unless `type` is passed, `adopt()` infers `course`, `project_sensitive`, `project`, or `bare` from existing directory names. After confirmation (or `yes=True`), it writes `settings.yml` and `.framework/spec.json` and appends missing `.gitignore` rules under `# Added by framework.adopt()`, omitting rules that match tracked files or template negations that would override existing `.gitignore` entries.

A path containing `private` triggers a warning, and the review asks whether the project holds sensitive data. In a sensitive adopt (`sensitive=True`), `adopt()` flags candidate data and output directories and asks about each; marked folders get `inputs_private_*` or `outputs_private_*` keys and are added to `.gitignore` (or pass `private=[...]`). `adopt()` rejects R or Stata repositories unless `force=True` is passed, and directs existing Framework projects to `upgrade()`.

Shell equivalent:

```sh
python -m framework adopt --path ~/code/old-study --yes [--type TYPE] [--sensitive] [--cloud [--slug SLUG]]
```

[structure_import()](ref:structure_import) uploads only a directory's folder paths to your Framework Cloud account as a reusable private structure:

```python
fw.structure_import("~/code/old-study", name="Lab layout")
```

## Project upgrades

[upgrade()](ref:upgrade) migrates older `framework-py` or 1.x `config.yml` projects to the current layout. By default (`dry_run=True`), it prints the migration plan without modifying files:

```python
fw.upgrade("~/code/old-study")  # preview plan
fw.upgrade("~/code/old-study", dry_run=False)  # apply changes
```

```sh
python -m framework upgrade            # preview plan
python -m framework upgrade --apply    # apply changes
```

`upgrade()` adds missing `tools` and `security` sections, updates legacy connection schemas, appends missing `.gitignore` entries, sets `AGENTS.md` as the canonical AI file with `CLAUDE.md` pointing to it, refreshes unmodified Framework skills, fills missing Quarto keys, and writes a valid v1 `.framework/spec.json`. Pass `move_settings=True` (`--move-settings`) to move top-level keys outside `default:` into `settings/<key>.yml` split files. Plaintext passwords in settings are reported with `.env` variable suggestions rather than rewritten automatically. Modified files are backed up to `.framework/backups/<timestamp>/` unless `backup=False` (`--no-backup`) is set.

Pass `type="project_sensitive"` to convert an existing project to the sensitive layout in one step, adding `private/` and `public/` directories, `.gitignore` rules, Claude Code deny rules, and the `framework-sensitive-data` skill while keeping existing directory keys intact:

```python
fw.upgrade("~/code/old-study", type="project_sensitive", dry_run=False)
```

## Framework Cloud registration

[cloud_register()](ref:cloud_register) registers an unlinked local project on Framework Cloud from `.framework/spec.json` and writes `FW_PROJECT_TOKEN` and `FW_CLOUD_URL` to `.env` (matching `fw project link --create`):

```python
fw.cloud_register("~/code/old-study", dry_run=True)
fw.cloud_register("~/code/old-study", slug="old-study")
```

Run `upgrade()` first if `.framework/spec.json` does not exist yet.

## Cloned project setup

[bootstrap()](ref:bootstrap) restores untracked local state after `git clone`. Every step is idempotent, and none contacts Framework Cloud:

1. Ensures `.gitignore` ignores `.env`.
2. Copies `.env.example` to `.env` when missing and lists unset variables referenced via `env()`.
3. Creates missing directories from `settings.yml`.
4. Creates or migrates `framework.db`.
5. Runs `uv sync` (with `--frozen` when `uv.lock` is present).
6. Installs `.git/hooks/pre-commit` when hook checks are enabled.

Run from a shell after cloning:

```sh
git clone https://github.com/example/vaccine-study.git
cd vaccine-study
uv run python -m framework bootstrap
```

Pass `--path` to target another directory, `--interactive` to prompt for empty `.env` values (masking secret variables), or `--no-packages` to skip `uv sync`. From Python, `bootstrap()` returns a `BootstrapResult` with `done` and `todo` lists:

```python
result = fw.bootstrap("~/code/vaccine-study", packages=False)
result.todo
```

## Layer and spec resolution

Lower-level helpers expose the spec pipeline behind `new_project()`:

- [spec_resolve()](ref:spec_resolve) merges layers into a complete spec and reports overridden keys.
- [spec_check()](ref:spec_check) validates a spec before creation.
- [project_create()](ref:project_create) scaffolds a directory from a resolved spec.

```python
resolved = fw.spec_resolve(
    [{"kind": "project", "layer": {"type": "course", "language": "python"}}],
    name="Stats 101",
)
fw.project_create(resolved.spec, "~/teaching/stats-101")
```

`new_project()` is the primary creation entry point; [new()](ref:new) is an alias.
