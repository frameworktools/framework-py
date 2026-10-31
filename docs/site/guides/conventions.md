---
id: conventions
title: Conventions
description: Project folders, dot-notation names, and the naming rules Framework follows
section: getting_started
position: 3
---

## Philosophy

Framework favors convention over configuration. Every project of a given type has the same folders, the same settings file, and the same entry point, so a person or an AI assistant who knows one project can find their way in another. Nearly every convention can be changed in the project's settings or in your global defaults.

This page describes the conventions that the rest of the documentation assumes.

## One Entry Point

Every notebook and script starts with two lines.

```python
import framework as fw

fw.scaffold()
```

[scaffold()](ref:scaffold) finds the project root, loads the settings and the `.env` file, sets the random seed when that is turned on, and puts the `functions/` folder on the import path. The [scaffold()](guide:scaffold) guide describes each step.

Framework resolves project paths from the project root. The working directory is not changed, so a notebook in `notebooks/` and a script in `scripts/` refer to the same data by the same name.

## Functions

Reusable code lives in the `functions/` folder as ordinary Python modules. `scaffold()` adds the folder to `sys.path`, so notebooks and scripts import from it directly.

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

Python imports run a module's top-level code once per session. Keep these modules to definitions, so that importing them has no side effects.

## Inputs and Outputs

Framework separates the data a project reads from the files it produces.

### Inputs

Data the project reads lives in `inputs/`, organized by stage.

```text
inputs/
├── raw/           # data from outside sources, never modified
├── intermediate/  # cleaned or transformed data, mid-pipeline
└── final/         # analysis-ready datasets
```

### Outputs

Files the project produces live in `outputs/`, organized by kind. Apart from `outputs/notebooks/`, which is created with the project, each folder is created the first time something is saved there.

```text
outputs/
├── tables/     # save_table()
├── figures/    # save_figure()
├── models/     # save_model()
├── reports/    # save_report()
├── notebooks/  # rendered notebooks, from Quarto and save_notebook()
└── cache/      # cached values
```

The [Saving Work](guide:saving-work) guide describes the `save_*()` functions.

### Work Folders

- `notebooks/` holds Quarto (`.qmd`) and Jupyter (`.ipynb`) notebooks.
- `scripts/` holds standalone Python scripts.
- `functions/` holds reusable modules.
- `scratch/` holds throwaway files and quick captures.
- `docs/` holds project documentation.
- `logs/` holds run logs.

The standard `.gitignore` excludes `scratch/`, `logs/`, and every `cache/` folder.

## Dot Notation

Framework names data files with dot notation. A name such as `inputs.raw.survey` stands for a catalog entry, which records the file's path, type, and integrity settings. Code refers to the name, and the catalog maps it to a file.

```python
survey = fw.data_read("inputs.raw.survey")
```

The catalog is the `data` section of the settings. Each part of a dot-notation name is usually one level of nesting, and a key may also contain dots, such as `raw.survey:` under `inputs:`.

```yaml
data:
  inputs:
    raw:
      survey:
        path: inputs/raw/survey.csv
        type: csv
        locked: true
```

[data_add()](ref:data_add) and [data_save()](ref:data_save) write these entries for you. The [Data Management](guide:data-management) guide covers the catalog.

Settings keys use the same notation. `fw.settings("directories.notebooks")` returns the `notebooks` entry of the `directories` section.

## Naming

Framework applies a few naming rules consistently.

- **Catalog names** follow the folder they describe, so a file in `inputs/raw/` is named `inputs.raw.<file>`. When `data_save()` receives a name, its leading parts select the folder, using the longest run that names a directory key, and the rest becomes the file name.
- **Directory keys** in the settings join the parts of a folder with underscores, such as `inputs_raw` for `inputs/raw` and `outputs_tables` for `outputs/tables`.
- **Notebook and script files** are named from the title you give. `make_notebook("Data Cleaning")` creates `data-cleaning.qmd`.
- **Saved outputs** are named by the name you pass plus the format's extension, such as `summary.csv`. The results table in `framework.db` records them by that file name.
- **Projects** have a display name in `settings.yml`. The `pyproject.toml` name and the workspace file use its kebab-case form.

## Privacy-Sensitive Projects

Projects that hold restricted data split inputs and outputs into private and public folders.

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

Everything under a `private/` folder is ignored by git, and the publishing functions ask before they upload it. When Claude Code is one of the project's assistants, its settings deny it access to these folders. The [Project Types](guide:project-types) guide compares the types.

## Data Integrity

Framework records a SHA-256 hash of each catalog file in `framework.db`. A later read compares the file with the recorded hash. A changed file raises a warning, and a changed locked file raises an error.

```text
IntegrityError: Hash mismatch for locked data 'inputs.raw.survey'
Expected hash: 71da0ff755e5
Current hash:  ddaa40dcf34b
```

The hashes match the ones the R package records, so a file saved in one language verifies in the other.

## One Language per Project

Each project has a single `language`, recorded in `settings.yml`. framework-py creates and works with Python projects. R projects are created by the R package, and the two kinds of project never mix. They share data through formats both languages read: CSV and Parquet files, the data catalog, and `framework.db`.

## Messages

Framework reports progress through the `framework` logger. [scaffold()](ref:scaffold), project creation, and other interactive functions print these messages to standard error. In library use the logger stays quiet unless you configure it with the `logging` module.
