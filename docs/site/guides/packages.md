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

The terminal command is equivalent.

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
