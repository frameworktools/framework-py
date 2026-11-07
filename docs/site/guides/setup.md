---
id: setup
title: Setup
description: Global settings, configure_global(), config_sync(), and personal templates
section: getting_started
position: 2
---

## Overview

Framework keeps your personal defaults in a global config folder. New projects start from these defaults, and each project can override them in its own settings. framework-py has no graphical setup screen. You set defaults with [configure_global()](ref:configure_global) or by editing the files in the config folder.

## The config folder

[config_dir()](ref:config_dir) returns the folder. It is chosen in this order.

1. `FW_CONFIG_HOME`, when that environment variable is set.
2. `$XDG_CONFIG_HOME/framework`, when `XDG_CONFIG_HOME` is set.
3. `~/.config/framework`, on every platform, including Windows.

Python and the `fw` command-line tool share this folder. The R package keeps a separate folder, as the [Copying settings from R](#copying-settings-from-r) section explains. The folder is created the first time Framework writes to it.

```python
import framework as fw

fw.config_dir()
```

The folder can hold these files.

```text
~/.config/framework/
├── settings.yml          # your defaults and the project registry
├── templates/            # your versions of the notebook, script, and presentation templates
├── cloud.yml             # the Framework Cloud server you logged in to
├── cloud-token           # a saved cloud token, only when no system keyring is available
├── cloud-settings.yml    # the local mirror of your cloud settings document
└── sync-state.yml        # the revision of the last cloud settings sync
```

The cloud files appear only after you use Framework Cloud. The [Framework Cloud](guide:cloud) guide describes them.

## Global settings

[configure_global()](ref:configure_global) merges new values into `settings.yml` and returns the result. Called without arguments, it returns the current settings and changes nothing.

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

Nested mappings merge by key. Each project type's `directories` are replaced whole, and so are `defaults.packages`, `defaults.connections`, and `defaults.env`. The function checks the values before it saves them. It refuses any secret-looking variable with a value in `defaults.env`, because secrets belong in each project's `.env` file.

[global_settings()](ref:global_settings) reads the file without changing it.

### Settings for new projects

[new_project()](ref:new_project) reads these keys from the global settings.

| Key | Effect on new projects |
|-----|------------------------|
| `author` | `name`, `email`, `affiliation`, `orcid`, `url`, and `affiliations` fill the project's author section and notebook front matter. |
| `defaults.scaffold` | `seed_on_scaffold`, `seed`, `source_all_functions`, and `ide`. An `ide` of `vscode`, `positron`, or `both` adds a `.code-workspace` file. |
| `defaults.ai` | `enabled` turns AI assistant files on or off. `assistants` lists assistants (`claude`, `codex`, or `copilot`). |
| `defaults.git` | `initialize` decides whether the project becomes a git repository. `hooks` turns on pre-commit checks. |
| `project_types.<type>.directories` | Folder names for one project type, such as `notebooks: analysis`. |

The assistants you list replace the default selection, which is Claude Code alone. Include `claude` in the list to keep it. Set `enabled: false` to create projects without AI assistant files. The [AI assistants](guide:ai-assistants) guide describes the files each assistant gets.

The example below sets a seed, turns on the sensitive-folder hook check, and renames the notebooks folder of standard projects.

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

The resulting `settings.yml` is plain YAML and can be edited by hand.

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

Values you do not set come from the package defaults that ship with framework-py. [load_settings_catalog()](ref:load_settings_catalog) returns them, together with the `.gitignore` text of each project type.

A `settings-catalog.yml` file in the config folder overrides the package defaults. [new_project()](ref:new_project) reads the same keys from it as from the global settings, and the global settings win where both set a value. `load_settings_catalog()` merges the file over the package defaults it returns. The R package's `new()` reads its own `settings-catalog.yml` in the same way.

```python
catalog = fw.load_settings_catalog()
catalog["defaults"]["scaffold"]
```

## The project registry

Framework records each project it creates in the `projects` list of the global `settings.yml`. Projects created inside the system's temporary folder are not recorded.

- [project_list()](ref:project_list) returns the registered projects as a DataFrame with the columns `id`, `path`, `created`, and `exists`. A project whose folder is gone has `exists` False, and a message names it so you can remove it with `project_unregister()`. Projects created in a temporary folder are not registered.
- [project_register()](ref:project_register) adds a folder and returns its id.
- [project_unregister()](ref:project_unregister) removes a project by id or path.

```python
fw.project_list()
fw.project_unregister(3)
```

## Copying settings from R

The R package keeps its global settings in `tools::R_user_dir("framework", "config")`, because CRAN does not allow a package to write elsewhere. Python never writes to that folder. People who use both languages copy R's settings into Python's folder with [config_sync()](ref:config_sync).

`config_sync()` copies `settings.yml`, `cloud.yml`, and the `cloud-token*` files. A file is copied when Python's folder does not have it or R's copy is newer. Modification times are kept, and token files are made readable only by you. With `dry_run=True`, the function reports what it would copy.

```python
result = fw.config_sync(dry_run=True)
print(result)
```

The `from_dir` argument names a different source folder. When `FW_CONFIG_HOME` is set, R and Python use the same folder and nothing needs to be copied.

## Personal templates

[make_notebook()](ref:make_notebook), [make_script()](ref:make_script), and [make_presentation()](ref:make_presentation) start from templates. You can keep your own version of three of them in the `templates/` subfolder of the config folder.

| Template | File | Used by |
|----------|------|---------|
| `notebook` | `notebook-default.qmd` | `make_notebook()` and `make_qmd()` |
| `script` | `script-default.py` | `make_script()` |
| `presentation` | `notebook-revealjs.qmd` | `make_presentation()` and `make_revealjs()` |

These functions manage the files.

- [read_framework_template()](ref:read_framework_template) returns your version when it exists, and Framework's otherwise.
- [write_framework_template()](ref:write_framework_template) saves your version.
- [reset_framework_template()](ref:reset_framework_template) deletes your version, so Framework's is used again.
- [framework_template_path()](ref:framework_template_path) returns the file your version is saved in.

```python
text = fw.read_framework_template("script")
fw.write_framework_template(
    "script",
    text.replace("import framework as fw", "import pandas as pd\n\nimport framework as fw"),
)
fw.reset_framework_template("script")
```

A template file in a project's `stubs/` folder takes precedence over your personal template. The [Notebooks and scripts](guide:notebooks) guide describes stubs and the placeholders templates can use.
