---
id: scaffold
title: scaffold()
description: Python session setup for project work
section: features
position: 1
---

## Overview

[scaffold()](ref:scaffold) initializes a Python session inside a Framework project. Call it at the top of every notebook and script:

```python
import framework as fw

fw.scaffold()
```

It returns the project root as a `pathlib.Path`:

```python
root = fw.scaffold()
```

## Startup sequence

Each `scaffold()` call runs six steps in order:

1. **Project discovery.** Searches the current directory and up to ten parent directories for `settings.yml` (or legacy `config.yml`).
2. **Environment and settings loading.** Loads `.env` into `os.environ` (existing process variables win; empty values are skipped) and resolves `settings.yml`, split files, and `env()` expressions.
3. **Database setup.** Creates `framework.db` if missing, creates missing private directories, and records first and latest scaffold timestamps in the `meta` table.
4. **Random seeding and plot style.** Seeds Python's `random` module and NumPy when seeding is enabled, and applies `matplotlib_style` when `set_theme_on_scaffold` is on.
5. **Import path setup.** Prepends `directories.functions` to `sys.path`.
6. **Custom hook execution.** Executes `scaffold.py` in the project root when present.

`scaffold()` leaves the working directory unchanged. Framework resolves catalog paths, output directories, and cache paths from the project root, so code runs identically from `notebooks/`, `scripts/`, or the root directory.

## Project discovery

Because `scaffold()` searches parent directories, notebooks and scripts in subdirectories find the project automatically:

```text
vaccine-study/
├── settings.yml
├── notebooks/
│   └── exploration.qmd   # scaffold() resolves vaccine-study/
├── scripts/
│   └── load.py           # scaffold() resolves vaccine-study/
└── functions/
    └── cleaning.py
```

Outside a project, `scaffold()` raises `ProjectNotFoundError`. Pass an explicit `settings.yml` path to target a specific project:

```python
from pathlib import Path

fw.scaffold(Path.home() / "code" / "vaccine-study" / "settings.yml")
```

[project_root()](ref:project_root) locates the root directory without running setup steps, and [project_path()](ref:project_path) resolves paths relative to the root (`fw.project_path("outputs", "tables", "x.csv")`).

## Functions directory

`scaffold()` prepends `directories.functions` to `sys.path` so notebooks and scripts can import project modules directly:

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

Because `functions/` sits at the front of `sys.path`, a local module with the same name as a standard library or installed package shadows it. `scaffold()` emits a warning when shadowing occurs (for example, `functions/statistics.py` shadowing Python's `statistics` module).

`directories.functions` also accepts a list of directories:

```yaml
directories:
  functions:
    - functions
    - helpers
```

Set `source_all_functions: false` under `scaffold` (or `options` in older projects) to leave `sys.path` unchanged:

```yaml
scaffold:
  source_all_functions: false
```

## Random seeds

Seeding is off by default. When enabled, `scaffold()` seeds Python's `random` module and NumPy's global generator:

```yaml
scaffold:
  seed_on_scaffold: true
  seed: '123'
```

When `seed_on_scaffold` is omitted, setting `FW_SEED` in the environment enables seeding; `scaffold.seed` still overrides `FW_SEED`'s value, and the fallback seed is `123`.

## Plot style

With `set_theme_on_scaffold: true`, `scaffold()` applies `matplotlib_style` so every figure starts from the same style:

```yaml
scaffold:
  set_theme_on_scaffold: true
  matplotlib_style: seaborn-v0_8-whitegrid
```

Check `matplotlib.style.available` for installed style names. Missing `matplotlib` or an unknown style emits a warning without failing.

## scaffold.py

New projects include a root `scaffold.py` file that `scaffold()` executes after its built-in steps. Use it for shared session configuration such as pandas display options:

```python
# scaffold.py
import pandas as pd

pd.set_option("display.max_columns", 50)
```

`scaffold.py` runs in an isolated namespace: global library settings (such as pandas options or matplotlib styles) persist in the session, whereas local variables defined in `scaffold.py` do not leak into notebooks. Place reusable functions and constants in `functions/` and import them explicitly.

## Project inspection

Two read-only helpers inspect project state:

[status()](ref:status) prints the Framework version, project type and author, git branch and working tree state, AI instruction files, package lock status, and directory existence:

```python
fw.status()
```

[project_info()](ref:project_info) returns configured directories as a DataFrame with `key`, `path`, and `exists` columns:

```python
info = fw.project_info()
info.attrs["type"]
```

## Session messages

`scaffold()` enables progress messages on the `framework` logger (`stderr`) for the remainder of the Python session:

```text
Random seed set to 123.
Added functions to the import path
```
