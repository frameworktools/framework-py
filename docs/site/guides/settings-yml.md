---
id: settings-yml
title: settings.yml
description: Project settings, split files, environments, expressions, and read/write helpers
section: getting_started
position: 4
---

## Overview

Every Framework project has a `settings.yml` file in its root directory. It defines the project name, type, language, directory layout, data catalog, connections, and runtime options. Framework locates the project root by searching the current directory and up to ten parent directories for `settings.yml` (or legacy `config.yml`).

`framework-py` and the R package share the same YAML schema.

## Environments

Top-level environment blocks let one project define environment-specific overrides over `default`:

```yaml
default:
  project_name: Vaccine Study
  project_type: project
  language: python
  connections:
    databases:
      warehouse:
        driver: postgres
        database: analytics_dev

production:
  connections:
    databases:
      warehouse:
        database: analytics
```

Framework enables environment mode when the top level contains `default`, `production`, `development`, `test`, `testing`, or `staging`. A file in environment mode must include `default`.

Framework selects the active environment from the first set variable among `FW_CONFIG_ACTIVE`, `R_CONFIG_ACTIVE`, and `R_CONFIG_NAME`, defaulting to `default`:

```sh
FW_CONFIG_ACTIVE=production uv run python scripts/load.py
```

Framework merges the active block over `default`. Mappings merge recursively by key, `null` deletes a key, and scalars or lists replace the default value as a whole. Requesting an undefined environment falls back to `default` with a warning.

In environment mode, Framework ignores top-level keys that are not environment blocks and warns once per session:

```text
SettingsWarning: Settings file '.../settings.yml' has keys outside its environment sections, which are ignored: ehr_source, person. Move them under default: to use them.
```

A custom top-level mapping is recognized as an environment without warning when it matches the active environment name, contains `inherits:`, or shares a key with `default`.

## Split files

Standard and privacy-sensitive projects split major sections into files under `settings/` and reference them from `settings.yml`:

```yaml
default:
  project_name: Vaccine Study
  project_type: project
  language: python
  directories:
    notebooks: notebooks
    inputs_raw: inputs/raw
  author: settings/author.yml
  git: settings/git.yml
  ai: settings/ai.yml
  scaffold: settings/scaffold.yml
  quarto: settings/quarto.yml
  connections: settings/connections.yml
  data: settings/data.yml
  security:
    sensitive: false
```

Each split file wraps its contents in the section key:

```yaml
# settings/author.yml
author:
  name: Ada Lovelace
  email: ada@example.org
  affiliation: Analytical Engines
```

Course and presentation projects inline all sections in `settings.yml` by default, though any project can reference `.yml` or `.yaml` split files.

Split-file resolution rules:

- Paths resolve relative to the project root.
- Split files can reference additional split files; circular references raise an error.
- Split files can define their own environment blocks.
- When both `settings.yml` and a split file define the same key, `settings.yml` wins and emits a warning, except `options`, which merges keys across both files.

## Expressions

Settings files interpolate environment variables so secrets remain in `.env`. Framework loads `.env` before resolving split files; existing process environment variables take precedence, and empty `.env` assignments are skipped.

Use double-quoted `env("VAR")` to read a variable (or `""` when unset), and `env("VAR", "default")` to supply a fallback:

```yaml
connections:
  databases:
    warehouse:
      driver: postgres
      host: env("POSTGRES_HOST")
      port: env("POSTGRES_PORT", "5432")
      user: env("POSTGRES_USER")
      password: env("POSTGRES_PASSWORD")
```

For compatibility with R projects, `framework-py` also evaluates `!expr Sys.getenv("VAR")` identically to `env("VAR")`. Any other `!expr` tag raises `SettingsError` because Python does not evaluate arbitrary R expressions. Prefer `env()` in shared configurations.

## Configuration sections

| Section | Contents |
|---------|----------|
| `project_name`, `project_type`, `language` | Display name, project type (`project`, `project_sensitive`, `course`, `presentation`, or `bare`), and `python`. |
| `directories` | Directory keys mapped to paths relative to the project root. |
| `render_dirs` | Quarto output directories per source folder. See [Quarto](guide:quarto). |
| `extra_directories` | Custom folders added via [project_add_directory()](ref:project_add_directory). |
| `author` | `name`, `email`, and `affiliation` for notebook metadata. |
| `git` | `enabled` and pre-commit `hooks`. See [Git](guide:git). |
| `ai` | `enabled`, `canonical_file` (`AGENTS.md`), and `assistants`. See [AI assistants](guide:ai-assistants). |
| `scaffold` | `seed_on_scaffold`, `seed`, `set_theme_on_scaffold`, `matplotlib_style`, `source_all_functions`, `print_description_on_read`, `workflow`, and `notebook_format` (`quarto`, `jupyter`, or `script`). |
| `quarto` | HTML and RevealJS rendering options. |
| `connections` | Database and S3 storage definitions. See [Connections](guide:connections). |
| `data` | Data catalog entries. See [Data management](guide:data-management). |
| `security` | `sensitive: true` for privacy-sensitive projects. |

Python dependencies live in `pyproject.toml`; the R package's `packages` section does not apply to Python projects. See [Packages](guide:packages).

### Directories

`directories` maps logical keys to project-relative paths. Default paths for a standard project:

```yaml
directories:
  notebooks: notebooks
  scripts: scripts
  functions: functions
  inputs_raw: inputs/raw
  inputs_intermediate: inputs/intermediate
  inputs_final: inputs/final
  docs: docs
  outputs_notebooks: outputs/notebooks
  outputs_tables: outputs/tables
  outputs_figures: outputs/figures
  outputs_models: outputs/models
  outputs_reports: outputs/reports
  cache: outputs/cache
  scratch: scratch
  logs: logs
```

`save_*()` functions resolve target folders through `outputs_*` keys, and `data_save()` resolves `inputs_*` keys. See [Project types](guide:project-types) for each type's defaults.

### Data catalog

`data` maps dot-notation names to file paths or metadata mappings:

```yaml
data:
  inputs:
    raw:
      survey:
        path: inputs/raw/survey.csv
        type: csv
        locked: true
      sites: inputs/raw/sites.csv
```

This configuration defines `inputs.raw.survey` and `inputs.raw.sites`. [data_add()](ref:data_add) and [data_save()](ref:data_save) write nested YAML keys, and dotted keys such as `raw.survey:` under `inputs:` resolve identically.

### Random seeds

Seeding is disabled by default. With `seed_on_scaffold: true`, [scaffold()](ref:scaffold) seeds Python's `random` module and NumPy using `seed`:

```yaml
scaffold:
  seed_on_scaffold: true
  seed: '123'
```

When `seed_on_scaffold` is omitted, setting `FW_SEED` in the environment enables seeding; `scaffold.seed` still overrides `FW_SEED`'s value when present. An explicit `seed_on_scaffold: false` disables seeding even when `FW_SEED` is set.

### Data descriptions

With `print_description_on_read: true`, [data_read()](ref:data_read) prints a catalog entry's `description` on each read as `Note: <name>: <description>`. Printing is off by default. [data_list()](ref:data_list) always shows descriptions.

```yaml
scaffold:
  print_description_on_read: true
```

### Options

`options` holds additional runtime flags:

| Option | Effect |
|--------|--------|
| `source_all_functions: false` | Keeps `directories.functions` off `sys.path` during `scaffold()`. |
| `functions_dir` | Single path or list of paths added to `sys.path` instead of `directories.functions`. |
| `data.cache_default_expire` | Default expiration window for cached values, such as `"7 days"`. |
| `cloud_ledger: live` | Pushes each `data_save()` digest to the Framework Cloud ledger. See [Framework Cloud](guide:cloud). |

Top-level `dotenv_location` points `scaffold()` and settings loading at an alternate `.env` file or directory.

## Settings lookup

[settings()](ref:settings) reads one value by dot-notation key and returns `default` (`None` unless specified) when missing:

```python
import framework as fw

fw.settings("author.name")
fw.settings("directories.notebooks")
fw.settings("missing.key", default="fallback")
```

Single-segment keys check `directories` first, so `fw.settings("notebooks")` returns `directories.notebooks`. Calling `settings()` with no key returns the entire merged dictionary.

[settings_read()](ref:settings_read) loads one environment with split files merged and `env()` expressions resolved. It always includes `data`, `connections`, `git`, `security`, `packages`, `directories`, and `options` keys, defaulting unset sections to `{}`:

```python
config = fw.settings_read(environment="production")
config["connections"]
```

## Settings updates

Edit YAML settings files directly or let Framework helpers update their managed sections (`data_add()` and `data_save()` for `data`, `git_hooks_*()` for `git.hooks`, and `project_add_directory()` for `extra_directories`). In-place edits preserve comments, formatting, and list styles, normalizing YAML booleans to `true` and `false`.

[settings_write()](ref:settings_write) with `section=` updates a single section in place while preserving comments. Without `section=`, it overwrites `settings.yml` with the given dictionary (wrapping it under `default:` when needed) and removes existing comments.
