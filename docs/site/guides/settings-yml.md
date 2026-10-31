---
id: settings-yml
title: settings.yml
description: The project settings file, split settings files, environments, and expressions
section: getting_started
position: 4
---

## Overview

Every Framework project has a `settings.yml` file in its root folder. It records the project's name, type, language, folders, data catalog, connections, and options. Framework finds the project by looking for this file in the current folder and up to ten parent folders. An older project may use `config.yml` instead, which Framework also reads.

The settings use the same format as the R package, so either language reads a project's settings to the same values.

## Environments

The file's top level holds environment sections. Every file has a `default:` section, and the other sections hold overrides.

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

Framework reads environment sections when the top level contains `default`, `production`, `development`, `test`, `testing`, or `staging`. A file with environment sections must have a `default` section.

The active environment comes from the first of these environment variables that is set: `FW_CONFIG_ACTIVE`, `R_CONFIG_ACTIVE`, and `R_CONFIG_NAME`. Without any of them, the active environment is `default`.

```sh
FW_CONFIG_ACTIVE=production uv run python scripts/load.py
```

The active section is merged over `default`. Mappings merge key by key, a `null` value removes a key, and any other value, including a list, replaces the default value whole. An environment name that the file does not define falls back to `default` with a warning.

## Split Files

Standard and privacy-sensitive projects keep most sections in separate files under `settings/`. The main file names each split file in place of the section's content.

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

Each split file wraps its content in the section's own key.

```yaml
# settings/author.yml
author:
  name: Ada Lovelace
  email: ada@example.org
  affiliation: Analytical Engines
```

Course and presentation projects keep every section inline in `settings.yml`. Any project can use split files, because Framework treats any top-level value ending in `.yml` or `.yaml` as a file reference.

These rules govern split files.

- Paths are relative to the project root.
- A split file may name further split files. A circular reference is an error.
- A split file may have its own environment sections.
- When the main file and a split file both define a key, the main file's value is used and Framework issues a warning. The `options` section is the exception, and its values merge.

## Expressions

Settings can read environment variables, so credentials stay in the project's `.env` file. Framework loads `.env` before it reads split files.

`env("VAR")` is replaced with the variable's value, or with an empty string when it is not set. `env("VAR", "default")` supplies a fallback. Both forms need double quotes.

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

The R package also accepts `!expr`, which runs arbitrary R code. Python cannot run R code, so framework-py supports a single form, `!expr Sys.getenv("VAR")`, which works like `env("VAR")`. Any other `!expr` value raises a `SettingsError` when the settings are read. Settings shared between R and Python should use `env()`.

## Sections

A Python project's settings contain these sections.

| Section | Contents |
|---------|----------|
| `project_name`, `project_type`, `language` | The project's name, its type (`project`, `project_sensitive`, `course`, `presentation`, or `bare`), and `python`. |
| `directories` | Folder keys and their paths, relative to the project root. |
| `render_dirs` | Where Quarto writes rendered output for each source folder. See [Quarto](guide:quarto). |
| `extra_directories` | Folders added with [project_add_directory()](ref:project_add_directory). |
| `author` | `name`, `email`, and `affiliation`, used in notebook front matter. |
| `git` | `enabled` and the pre-commit `hooks`. See [Git](guide:git). |
| `ai` | `enabled`, `canonical_file` (`AGENTS.md`), and `assistants`. See [AI Assistants](guide:ai-assistants). |
| `scaffold` | `seed_on_scaffold`, `seed`, `source_all_functions`, and `notebook_format` (`quarto` or `jupyter`). |
| `quarto` | HTML and reveal.js rendering options. |
| `connections` | Databases and storage buckets. See [Connections](guide:connections). |
| `data` | The data catalog. See [Data Management](guide:data-management). |
| `security` | `sensitive: true` in privacy-sensitive projects. |

Python packages are listed in `pyproject.toml`. The R package's `packages` section has no effect on a Python project. See [Packages](guide:packages).

### Directories

The `directories` section maps keys to folders. These are the defaults of a standard project.

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

The `save_*()` functions find their folder through these keys, and `data_save()` uses the `inputs_*` keys. The [Project Types](guide:project-types) guide lists the defaults of each type.

### The Data Catalog

The catalog maps dot-notation names to files. Each part of a name is one level of nesting, and an entry is either a path or a mapping with a `path`.

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

This catalog defines `inputs.raw.survey` and `inputs.raw.sites`. [data_add()](ref:data_add) and [data_save()](ref:data_save) write every level as nested YAML. A key may also contain dots, such as `raw.survey:` under `inputs:`, and the name resolves the same way.

### Random Seeds

Seeding is off by default. With `seed_on_scaffold: true`, [scaffold()](ref:scaffold) seeds Python's `random` module and NumPy with `seed`.

```yaml
scaffold:
  seed_on_scaffold: true
  seed: '123'
```

When the settings do not set `seed_on_scaffold`, the `FW_SEED` environment variable turns seeding on. The project's `seed` takes precedence over the value of `FW_SEED`. New projects set `seed_on_scaffold: false`, and an explicit `false` turns seeding off even when `FW_SEED` is set.

### Options

The `options` section holds less common settings.

| Option | Effect |
|--------|--------|
| `source_all_functions: false` | `scaffold()` leaves the functions folder off the import path. |
| `functions_dir` | One folder, or a list of folders, to add to the import path instead of `directories.functions`. |
| `data.cache_default_expire` | The expiry for cached values that do not set one, such as `"7 days"`. |
| `cloud_ledger: live` | Sends each `data_save()` digest to the Framework Cloud ledger. See [Framework Cloud](guide:cloud). |

The top-level `dotenv_location` key points `scaffold()` and the settings reader at a different `.env` file or at a folder that holds one.

## Reading Settings

[settings()](ref:settings) returns one value by its dot-notation key. A missing key returns `default`, which is `None` unless you pass one.

```python
import framework as fw

fw.settings("author.name")
fw.settings("directories.notebooks")
fw.settings("missing.key", default="fallback")
```

A key with a single part is looked up in `directories` first, so `fw.settings("notebooks")` returns the notebooks folder. Called with no key, `settings()` returns the whole dictionary.

[settings_read()](ref:settings_read) returns the merged settings of one environment, with split files read and expressions replaced. The `data`, `connections`, `git`, `security`, `packages`, `directories`, and `options` sections are always present, as empty dictionaries when the file does not define them.

```python
config = fw.settings_read(environment="production")
config["connections"]
```

## Writing Settings

The settings files are plain YAML, and editing them by hand is the usual way to change them. Framework's own functions update the parts they manage: `data_add()` and `data_save()` write catalog entries, and the `git_hooks_*()` functions write the `hooks` section.

[settings_write()](ref:settings_write) replaces the whole settings file with a dictionary, wrapped in a `default:` section when the dictionary has none. Comments in a rewritten file are not kept.
