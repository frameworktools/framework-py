---
id: packages
title: Packages
description: Python package management with pyproject.toml and uv
section: features
position: 3
---

## Overview

A Python project lists its packages in `pyproject.toml`, and uv locks the exact versions in `uv.lock`. Framework uses these standard Python files. The `package_add()` and `packages_*()` functions call uv, so you can manage packages from a notebook or from the terminal with the same result.

New projects start with one dependency.

```toml
[project]
name = "vaccine-study"
version = "0.1.0"
description = "Vaccine Study"
requires-python = ">=3.11"
dependencies = [
    "framework-py",
]

[tool.uv]
package = false
```

`package = false` tells uv that the project is an analysis and not a library to build.

Commit `pyproject.toml` and `uv.lock`. A collaborator who clones the project installs the same versions with `uv sync` or [bootstrap()](ref:bootstrap).

## Adding packages

[package_add()](ref:package_add) adds a requirement to `pyproject.toml`, updates `uv.lock`, and installs the package. It runs `uv add` in the project root.

```python
import framework as fw

fw.package_add("statsmodels")
fw.package_add("seaborn>=0.13")
```

The `install=False` argument updates `pyproject.toml` and `uv.lock` without installing. The `dev=True` argument adds the package to the `dev` dependency group, for tools such as pytest that the analysis itself does not need.

```python
fw.package_add("pytest", dev=True)
```

`package_add()` and [package_set_add()](ref:package_set_add) also rewrite the **Installed Packages** section of the project's `AGENTS.md` from `pyproject.toml`, so AI assistants see the new package. Only that section changes, and only when it still has its `<!-- @framework:regenerate -->` marker; delete the marker to keep your own text there.

The terminal command adds the package too, but leaves `AGENTS.md` as it is.

```sh
uv add statsmodels
```

A requirement can pin a version or point at a git repository, using the standard Python requirement syntax.

```python
fw.package_add("pandas==2.2.3")
fw.package_add("mytools @ git+https://github.com/example/mytools@v1.2")
```

Quarto runs the Python chunks of `.qmd` notebooks through Jupyter, so projects that render notebooks need it in their environment.

```python
fw.package_add("jupyter")
```

## Package sets

A package set is a group of packages for one kind of work. [package_set_add()](ref:package_set_add) adds a set to an existing project. It adds the packages the project doesn't list yet and installs them. Packages already listed stay as they are. [package_sets_list()](ref:package_sets_list) shows the sets for Python.

```python
fw.package_sets_list()
fw.package_set_add("python-ml")
fw.package_set_add("fastapi-python", install=False)
```

Signed in to Framework Cloud, you can also add one of your account's sets by its handle, such as `fw.package_set_add("@yourname/lab-tools")`. New projects take sets with `new(package_sets=[...])`.

## Setup on Framework Cloud

A project's setup on Framework Cloud is its packages and skills. Folders and structure are not part of it. [pull_setup()](ref:pull_setup) brings Cloud's setup into the project, and [push_setup()](ref:push_setup) sends the project's packages back. Both use the project token in `.env`.

```python
fw.pull_setup(dry_run=True)  # list the changes only
fw.pull_setup()  # list them, ask, then apply
fw.push_setup()
```

`pull_setup()` lists the changes first, then asks "Apply these changes? [y/n]":

```text
Setup changes from Framework Cloud (revision 3f2a…):
  Packages to add: polars, numpy
  Packages that differ: pandas (local >=2.0, Cloud >=2.2) — kept; pull_setup(overwrite=True) uses Cloud's
  Only in this project (kept): requests
  Skills: 1 to add, 2 to update, 1 locally edited (kept)
```

New packages are added to `pyproject.toml` and installed, then [pull_skills()](ref:pull_skills) runs. Local packages are never removed. A package whose local version or source differs keeps it unless `overwrite=True`, which also replaces locally edited skills after backing them up.

`push_setup()` sends the package list as a dry run first and lists what would change on Cloud:

```text
Setup changes for Framework Cloud:
  Packages to add: requests
  Packages to change: pandas (>=2.2 -> >=2.0)
  On Cloud only (kept): polars, numpy
This changes Test Project's setup on Framework Cloud. New setups, zips, and Update on GitHub will use it.
```

It then asks "Push these changes? [y/n]". Packages only on Cloud stay unless `remove=True`. framework-py and the unpinned packages Framework adds for Quarto and Jupyter are not sent. Edited skills then go through [push_skills()](ref:push_skills), which needs `fw.cloud_login()`.

The revision last pulled or pushed is kept in `.framework/setup.lock.json`. When Cloud's setup changed since then, `push_setup()` stops with "Pull first". `force=True` replaces Cloud's newer changes. Without a console, pass `confirm=True`, or `dry_run=True` to only list the changes. Stata projects have no package list, so only their skills are pulled and pushed.

## Listing and checking

[packages_list()](ref:packages_list) returns the dependencies in `pyproject.toml` with the version installed in the running environment.

```python
fw.packages_list()
```

[packages_status()](ref:packages_status) checks whether `uv.lock` matches `pyproject.toml` and whether the environment matches `uv.lock`. It prints a short report and returns a `PackagesStatus` whose `ok` attribute is `True` when everything matches.

```python
status = fw.packages_status()
status.ok
status.missing
```

## Installing, locking, and updating

These functions wrap uv commands and run them in the project root.

| Function | uv command | Effect |
|----------|------------|--------|
| [packages_install()](ref:packages_install) | `uv sync` | Installs the dependencies |
| [packages_snapshot()](ref:packages_snapshot) | `uv lock` | Records the resolved versions in `uv.lock` |
| [packages_restore()](ref:packages_restore) | `uv sync --frozen` | Installs exactly the versions in `uv.lock` |
| [packages_update()](ref:packages_update) | `uv lock --upgrade` and `uv sync` | Upgrades all locked versions, or only the named packages |

```python
fw.packages_update(["pandas", "statsmodels"])
```

## Without uv

uv is the supported tool. When it is not installed, [package_add()](ref:package_add) edits the `dependencies` list in `pyproject.toml` as text and installs the package with pip into the running environment. [packages_install()](ref:packages_install) installs the listed dependencies with pip. Locking, restoring, updating, and dev dependencies need uv and raise an error that says how to install it.

## Packages in notebooks

Run notebooks with the project's environment, so that the packages in `uv.lock` are the ones imported. In VS Code and Positron, select the project's `.venv` interpreter. The workspace file Framework creates points at `.venv/bin/python`. From the terminal, prefix commands with `uv run`.

```sh
uv run quarto render notebooks/exploration.qmd
```

## Differences from R

The R package lists packages in a `packages` section of the settings and uses renv for locking. Python projects use `pyproject.toml` and `uv.lock` instead, and `scaffold()` does not import or attach packages. Each notebook imports what it uses.
