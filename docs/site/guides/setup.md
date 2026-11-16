---
id: setup
title: Setup
description: Global settings, configure_global(), config_sync(), and personal templates
section: getting_started
position: 2
---

## Overview

Framework stores personal defaults in a global configuration directory. New projects start from those defaults, and each project can override any setting in `settings.yml`. Set defaults with [configure_global()](ref:configure_global) or edit the YAML files directly.

## Configuration directory

[config_dir()](ref:config_dir) resolves the configuration directory in priority order:

1. `FW_CONFIG_HOME`, when set.
2. `$XDG_CONFIG_HOME/framework`, when `XDG_CONFIG_HOME` is set.
3. `~/.config/framework` on macOS, Linux, and Windows.

`framework-py` and the `fw` CLI share this directory. The R package uses a separate directory by default; see [R settings sync](#r-settings-sync). Framework creates the directory on first write.

```python
import framework as fw

fw.config_dir()
```

Directory layout:

```text
~/.config/framework/
├── settings.yml          # global defaults and local project registry
├── templates/            # personal notebook, script, and presentation templates
├── cloud.yml             # active Framework Cloud server URL
├── cloud-token           # fallback cloud token when no system keyring is available
├── cloud-settings.yml    # local mirror of your Framework Cloud settings document
└── sync-state.yml        # revision metadata from the last cloud settings sync
```

The cloud files appear after signing in to Framework Cloud. See [Framework Cloud](guide:cloud).

## Global settings

[configure_global()](ref:configure_global) merges values into `settings.yml` and returns the updated configuration. Calling it with no arguments returns the current settings without writing to disk.

```python
fw.configure_global(
    {
        "author": {
            "name": "Ada Lovelace",
            "email": "ada@example.org",
            "affiliation": "Analytical Engines",
        },
    }
)
```

Nested mappings merge by key. Project type `directories`, `defaults.packages`, `defaults.connections`, and `defaults.env` replace their prior values as a whole. `configure_global()` validates settings before saving and rejects secret-looking variables with values in `defaults.env`; store credentials in each project's `.env` file instead.

[global_settings()](ref:global_settings) reads `settings.yml` without modifying it.

### New-project defaults

[new_project()](ref:new_project) reads these global keys:

| Key | Effect on new projects |
|-----|------------------------|
| `author` | Populates `name`, `email`, `affiliation`, `orcid`, `url`, and `affiliations` in project settings and notebook front matter. |
| `defaults.scaffold` | Sets `seed_on_scaffold`, `seed`, `source_all_functions`, and `ide`. Setting `ide` to `vscode`, `positron`, or `both` writes a `.code-workspace` file. |
| `defaults.ai` | `enabled` controls AI assistant files. `assistants` selects `claude`, `codex`, or `copilot`. |
| `defaults.git` | `initialize` controls git repository initialization. `hooks` enables pre-commit checks. |
| `project_types.<type>.directories` | Overrides directory paths for a project type, such as `notebooks: analysis`. |

The `assistants` list replaces the default selection (`claude`). Include `claude` to retain Claude Code alongside other assistants, or set `enabled: false` to omit AI assistant files. See [AI assistants](guide:ai-assistants).

Set a default random seed, enable the sensitive-directory hook check, and rename the notebooks folder for standard projects:

```python
fw.configure_global(
    {
        "defaults": {
            "scaffold": {"seed_on_scaffold": True, "seed": 42},
            "git": {"hooks": {"check_sensitive_dirs": True}},
        },
        "project_types": {
            "project": {"directories": {"notebooks": "analysis"}},
        },
    }
)
```

The resulting `settings.yml` is plain YAML:

```yaml
author:
  name: Ada Lovelace
  email: ada@example.org
  affiliation: Analytical Engines
defaults:
  scaffold:
    seed_on_scaffold: true
    seed: 42
  git:
    hooks:
      check_sensitive_dirs: true
project_types:
  project:
    directories:
      notebooks: analysis
```

### Package defaults

Unset keys fall back to the built-in catalog shipped with `framework-py`. [load_settings_catalog()](ref:load_settings_catalog) returns those defaults along with each project type's `.gitignore` template.

Placing a `settings-catalog.yml` file in the configuration directory overrides the built-in catalog. [new_project()](ref:new_project) reads `settings-catalog.yml` before `settings.yml`, and `settings.yml` takes precedence when both set a key. `load_settings_catalog()` merges `settings-catalog.yml` over the built-in defaults.

```python
catalog = fw.load_settings_catalog()
catalog["defaults"]["scaffold"]
```

## Project registry

Framework records each created project under `projects` in global `settings.yml`, except projects created inside the system temporary directory.

- [project_list()](ref:project_list) returns registered projects as a DataFrame with `id`, `path`, `created`, and `exists` columns. Missing directories show `exists: False` and emit a message naming `project_unregister()`.
- [project_register()](ref:project_register) registers a directory and returns its numeric ID.
- [project_unregister()](ref:project_unregister) removes an entry by ID or path.

```python
fw.project_list()
fw.project_unregister(3)
```

## R settings sync

The R package stores global configuration in `tools::R_user_dir("framework", "config")` to satisfy CRAN policy. `framework-py` never writes to that folder. Copy R settings into Python's configuration directory with [config_sync()](ref:config_sync).

`config_sync()` copies `settings.yml`, `cloud.yml`, and `cloud-token*` files when missing in Python's directory or newer in R's directory. It preserves modification times and restricts token files to owner-only permissions (`0600`). Pass `dry_run=True` to preview changes without writing files:

```python
result = fw.config_sync(dry_run=True)
print(result)
```

Pass `from_dir` to read from another source directory. Setting `FW_CONFIG_HOME` points R, Python, and `fw` at the same directory without copying.

## Personal templates

[make_notebook()](ref:make_notebook), [make_script()](ref:make_script), and [make_presentation()](ref:make_presentation) render files from templates. Store custom defaults in the `templates/` subdirectory of your configuration directory:

| Template | File | Functions |
|----------|------|-----------|
| `notebook` | `notebook-default.qmd` | `make_notebook()`, `make_qmd()` |
| `script` | `script-default.py` | `make_script()` |
| `presentation` | `notebook-revealjs.qmd` | `make_presentation()`, `make_revealjs()` |

Template helper functions:

- [read_framework_template()](ref:read_framework_template) returns your custom template when present or the built-in template otherwise.
- [write_framework_template()](ref:write_framework_template) writes a custom template.
- [reset_framework_template()](ref:reset_framework_template) deletes your custom template so the built-in version takes effect again.
- [framework_template_path()](ref:framework_template_path) returns the file path for a custom template.

```python
text = fw.read_framework_template("script")
fw.write_framework_template(
    "script",
    text.replace("import framework as fw", "import pandas as pd\n\nimport framework as fw"),
)
fw.reset_framework_template("script")
```

Project-level templates in `stubs/` override personal templates. See [Notebooks and scripts](guide:notebooks) for stub files and template placeholders.
