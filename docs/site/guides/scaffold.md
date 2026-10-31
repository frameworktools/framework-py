---
id: scaffold
title: scaffold()
description: Preparing a Python session for work in a project
section: features
position: 1
---

## Overview

[scaffold()](ref:scaffold) is the entry point for work in a Framework project. Call it at the top of every notebook and script.

```python
import framework as fw

fw.scaffold()
```

It returns the project root as a `pathlib.Path`, which is useful when code needs an absolute path.

```python
root = fw.scaffold()
```

## What scaffold() Does

Each call runs these steps in order.

1. **Finds the project.** It looks for `settings.yml`, or the older `config.yml`, in the current folder and up to ten parent folders.
2. **Loads the settings and the environment.** It reads the project's `.env` file into `os.environ` and resolves the settings, including split files and `env()` expressions.
3. **Prepares the database.** It creates `framework.db` if it is missing and records the first and latest scaffold times in its `meta` table.
4. **Sets the random seed**, when the settings turn seeding on.
5. **Extends the import path.** It puts the project's functions folder at the front of `sys.path`.
6. **Runs `scaffold.py`**, when the project root has one.

The working directory is not changed. Framework resolves catalog paths, output folders, and the cache folder from the project root, so code behaves the same from `notebooks/`, from `scripts/`, and from the root.

## Project Discovery

Because `scaffold()` searches parent folders, it works from any subfolder of the project.

```text
vaccine-study/
├── settings.yml
├── notebooks/
│   └── exploration.qmd   # scaffold() works here
├── scripts/
│   └── load.py           # and here
└── functions/
    └── cleaning.py
```

Outside a project, it raises `ProjectNotFoundError`. To use a specific settings file, pass its path.

```python
from pathlib import Path

fw.scaffold(Path.home() / "projects" / "vaccine-study" / "settings.yml")
```

[project_root()](ref:project_root) returns the root without the other steps.

## The Functions Folder

`scaffold()` adds the folder named by `directories.functions` to `sys.path`. Modules in that folder can then be imported by name.

```python
# functions/cleaning.py
import pandas as pd


def standardize_names(frame: pd.DataFrame) -> pd.DataFrame:
    return frame.rename(columns=str.lower)
```

```python
import framework as fw

fw.scaffold()

from cleaning import standardize_names
```

The value can be a list, which adds several folders.

```yaml
directories:
  functions:
    - functions
    - helpers
```

To leave the functions folder off the import path, set `source_all_functions: false` in the `scaffold` section, where new projects write it. The `options` section is also read, for older projects.

```yaml
scaffold:
  source_all_functions: false
```

## Random Seeds

Seeding is off by default. When it is on, `scaffold()` seeds Python's `random` module and NumPy's global generator.

```yaml
scaffold:
  seed_on_scaffold: true
  seed: '123'
```

When the settings do not set `seed_on_scaffold`, the `FW_SEED` environment variable turns seeding on. The project's `seed` takes precedence over the value of `FW_SEED`, and the fallback seed is 123.

## scaffold.py

New projects have a `scaffold.py` file in the root. `scaffold()` runs it after the other steps. Use it for setup that every notebook and script shares, such as a plotting style or pandas display options.

```python
# scaffold.py
import matplotlib.pyplot as plt
import pandas as pd

plt.style.use("seaborn-v0_8-whitegrid")
pd.set_option("display.max_columns", 50)
```

The file runs in its own namespace. Changes to shared state, such as matplotlib styles and pandas options, carry over to the notebook. Variables it defines do not. Put shared functions and values in the functions folder and import them instead.

## Project Status

Two functions describe a project without changing it.

[status()](ref:status) prints an overview: the Framework version, the project type and author, the git branch and uncommitted changes, the AI instruction files, the package lock state, and whether each configured folder exists.

```python
fw.status()
```

[project_info()](ref:project_info) returns the configured folders as a DataFrame with the columns `key`, `path`, and `exists`. Folders that do not exist yet are created the first time something is saved there.

```python
info = fw.project_info()
info.attrs["type"]
```

## Messages

`scaffold()` turns on Framework's progress messages for the rest of the session. They go to standard error through the `framework` logger.

```text
Random seed set to 123.
Added functions to the import path
```
