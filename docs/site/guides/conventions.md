---
id: conventions
title: Conventions
description: Project folders, dot-notation names, and naming rules
section: getting_started
position: 3
---

## Overview

Framework uses consistent defaults across projects. Every project of a given type uses the same directory layout, settings file, and session entry point, while allowing overrides in project `settings.yml` or global defaults.

## Session entry point

Start every notebook and script with two lines:

```python
import framework as fw

fw.scaffold()
```

[scaffold()](ref:scaffold) locates the project root, loads `settings.yml` and `.env`, sets the random seed when configured, and adds `functions/` to `sys.path`. See [scaffold()](guide:scaffold) for the full startup sequence.

Framework resolves project paths from the project root without changing the working directory, so a notebook in `notebooks/` and a script in `scripts/` address the same dataset by the same catalog name.

Use [project_path()](ref:project_path) (or its alias [path()](ref:path)) for explicit file paths instead of relative `Path(...)` calls, which resolve from a notebook's own folder:

```python
if not fw.project_path("outputs/tables/incidence.csv").exists():
    raise RuntimeError("Run notebook 3 first")
```

## Reusable modules

Place shared Python modules in `functions/`. Because `scaffold()` adds `functions/` to `sys.path`, notebooks and scripts import those modules directly:

```text
functions/
├── cleaning.py
└── plotting.py
```

```python
import framework as fw

fw.scaffold()

from cleaning import standardize_names
```

Python runs top-level module code once per session on import. Keep files in `functions/` limited to definitions without side effects.

## Inputs and outputs

Framework separates input datasets from generated artifacts.

### Inputs

Input datasets live in `inputs/` by pipeline stage:

```text
inputs/
├── raw/           # unmodified source files
├── intermediate/  # cleaned or transformed pipeline outputs
└── final/         # analysis-ready datasets
```

### Outputs

Generated artifacts live in `outputs/` by type. Project creation initializes `outputs/notebooks/`, and Framework creates the remaining subdirectories on first save:

```text
outputs/
├── tables/     # save_table()
├── figures/    # save_figure()
├── models/     # save_model()
├── reports/    # save_report()
├── notebooks/  # rendered notebooks from Quarto and save_notebook()
└── cache/      # cached values
```

See [Saving work](guide:saving-work) for the `save_*()` functions.

### Work directories

- `notebooks/` stores Quarto (`.qmd`) and Jupyter (`.ipynb`) notebooks.
- `scripts/` stores standalone Python scripts.
- `functions/` stores reusable Python modules.
- `scratch/` stores temporary explorations and captures.
- `docs/` stores project documentation.
- `logs/` stores execution logs.

The standard `.gitignore` excludes `scratch/`, `logs/`, and every `cache/` directory.

## Dot notation

Framework addresses datasets through dot-separated catalog keys such as `inputs.raw.survey`. Each entry records the file path, format, and integrity settings so analysis code references a stable name:

```python
survey = fw.data_read("inputs.raw.survey")
```

Catalog entries live under `data` in `settings.yml` (or `settings/data.yml`). Each dot usually steps down one YAML mapping level, though dotted YAML keys such as `raw.survey:` under `inputs:` resolve identically:

```yaml
data:
  inputs:
    raw:
      survey:
        path: inputs/raw/survey.csv
        type: csv
        locked: true
```

[data_add()](ref:data_add) and [data_save()](ref:data_save) manage catalog entries automatically. See [Data management](guide:data-management).

[settings()](ref:settings) uses the same dot notation: `fw.settings("directories.notebooks")` returns the configured `notebooks` path.

## Naming rules

- **Catalog names** mirror directory structure: a file in `inputs/raw/` uses `inputs.raw.<name>`. In `data_save()`, the longest matching prefix selects a directory key and the remainder becomes the filename stem.
- **Directory keys** join path segments with underscores, such as `inputs_raw` for `inputs/raw` and `outputs_tables` for `outputs/tables`.
- **Notebooks and scripts** slugify titles to kebab-case: `make_notebook("Data Cleaning")` creates `data-cleaning.qmd` (or `data-cleaning.ipynb`).
- **Saved outputs** combine the given stem with the format extension, such as `summary.csv`, and record paths relative to the project root in `framework.db`.
- **Projects** store a display name in `settings.yml` and derive kebab-case names for `pyproject.toml` and `.code-workspace`.

## Privacy-sensitive projects

Privacy-sensitive projects split `inputs/` and `outputs/` into `private/` and `public/` trees:

```text
inputs/
├── private/
│   ├── raw/
│   ├── intermediate/
│   └── final/
└── public/
    ├── raw/
    ├── intermediate/
    └── final/
```

Git ignores every `private/` path, publishing functions require confirmation before uploading from `private/`, and Claude Code settings deny read access to `private/` directories. See [Project types](guide:project-types).

## Data integrity

Framework stores a SHA-256 hash for each cataloged file in `framework.db` and verifies it on subsequent reads. Modified unlocked files emit a `DataChangedWarning`; modified locked files raise an `IntegrityError`:

```text
IntegrityError: Hash mismatch for locked data 'inputs.raw.survey'
Expected hash: 71da0ff755e5
Current hash:  ddaa40dcf34b
```

R and Python compute identical SHA-256 digests, so files saved in one language verify in the other.

## Project language

Each project records a single `language` in `settings.yml`. `framework-py` creates and operates on Python projects, while the R package manages R projects. Cross-language workflows share data through CSV and Parquet files, the YAML data catalog, and `framework.db`.

## Logging

Framework emits status messages through Python's `framework` logger. Interactive calls such as [scaffold()](ref:scaffold) and [new_project()](ref:new_project) print messages to `stderr`; library calls stay silent unless configured via the standard `logging` module.
