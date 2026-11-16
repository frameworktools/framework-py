---
id: packages
title: Packages
description: Python dependency management with pyproject.toml, uv, package sets, and Framework Cloud sync
section: features
position: 3
---

## Overview

Python projects declare dependencies in `pyproject.toml` and lock resolved versions in `uv.lock`. Framework's `package_add()` and `packages_*()` helpers wrap `uv` so package changes from Python or the terminal stay identical.

Default `pyproject.toml` in a new project:

```toml
[project]
name = "vaccine-study"
version = "0.1.0"
description = "Vaccine Study"
requires-python = ">=3.11"
dependencies = [
    "framework-py",
    "ipykernel",
    "nbformat",
    "nbclient",
    "jupyterlab",
]

[tool.uv]
package = false
```

`package = false` configures `uv` for an analysis repository rather than an installable library build.

Commit both `pyproject.toml` and `uv.lock`. Collaborators reproduce the exact environment with `uv sync` or [bootstrap()](ref:bootstrap).

## Package installation

[package_add()](ref:package_add) runs `uv add` in the project root to update `pyproject.toml`, lock versions in `uv.lock`, and install into `.venv`:

```python
import framework as fw

fw.package_add("statsmodels")
fw.package_add("seaborn>=0.13")
```

Pass `install=False` to update `pyproject.toml` and `uv.lock` without installing, or `dev=True` to add development tools such as `pytest` to the `dev` dependency group:

```python
fw.package_add("pytest", dev=True)
```

`package_add()` and [package_set_add()](ref:package_set_add) also refresh the `<!-- @framework:regenerate -->` **Installed Packages** block in `AGENTS.md` from `pyproject.toml`. Remove that HTML comment marker if you maintain custom text in that section. Running `uv add` directly in a shell updates `pyproject.toml` and `uv.lock` without modifying `AGENTS.md`.

Standard PEP 508 version pins and git URLs work directly:

```python
fw.package_add("pandas==2.2.3")
fw.package_add("mytools @ git+https://github.com/example/mytools@v1.2")
```

Projects that render `.qmd` notebooks with Python chunks require `jupyter` (or `ipykernel`, `nbformat`, and `nbclient`, added automatically when the `quarto` tool is enabled):

```python
fw.package_add("jupyter")
```

## Package sets

Package sets bundle dependencies for common workflows. [package_sets_list()](ref:package_sets_list) lists available Python sets, and [package_set_add()](ref:package_set_add) adds any packages not already declared in `pyproject.toml`:

```python
fw.package_sets_list()
fw.package_set_add("python-ml")
fw.package_set_add("fastapi-python", install=False)
```

When signed in to Framework Cloud, pass account set handles such as `fw.package_set_add("@yourname/lab-tools")`. Pass `package_sets=[...]` to `new_project()` to include sets at creation time.

## Sync with Framework Cloud

[pull_all()](ref:pull_all) pulls packages, skills, and data registry entries from a linked Framework Cloud project in one pass; [push_all()](ref:push_all) pushes local changes back. Directory layout is not synced. Package and data registry sync authenticate with `FW_PROJECT_TOKEN` in `.env`, whereas pushing edited skills also requires [cloud_login()](ref:cloud_login).

```python
fw.pull_all(dry_run=True)  # preview changes only
fw.pull_all()              # list changes, confirm once, and apply
fw.push_all()
```

`pull_all()` groups incoming changes by section and asks `Apply these changes? [y/n]`:

```text
Changes from Framework Cloud:
Packages (revision 3f2a…):
  Packages to add: polars, numpy
  Packages that differ: pandas (local >=2.0, Cloud >=2.2) - kept; pull_all(overwrite=True) uses Cloud's
  Only in this project (kept): requests
Skills:
  1 to add, 2 to update, 1 locally edited (kept)
Data registry (revision 8c1d…): up to date
```

`pull_all()` adds and installs new packages, updates skills via [pull_skills()](ref:pull_skills), and merges data catalog entries via [pull_data_registry()](ref:pull_data_registry). Local-only packages and catalog entries are preserved. Conflicting local edits remain untouched unless `overwrite=True` is passed (which backs up locally edited skills before replacing them).

`push_all()` previews remote changes first and prompts `Push these changes? [y/n]`. Cloud-only packages and catalog entries remain on the server unless `remove=True` is passed. `framework-py` and unpinned Quarto/Jupyter support packages are excluded from the pushed package list.

Framework tracks base revisions in `.framework/setup.lock.json`, `.framework/data.lock.json`, and `.framework/skills.lock.json`. If a section advanced on Framework Cloud since your last sync, `push_all()` stops that section with `Pull first: pull_all()` unless `force=True` is passed. Non-interactive runs require `confirm=True` (or `dry_run=True`). In a terminal, `fw pull` and `fw push` run the same workflow.

## Package inspection

[packages_list()](ref:packages_list) returns declared `pyproject.toml` dependencies alongside currently installed versions:

```python
fw.packages_list()
```

[packages_status()](ref:packages_status) checks whether `uv.lock` matches `pyproject.toml` and whether `.venv` matches `uv.lock`, returning a `PackagesStatus` object (`status.ok`, `status.missing`):

```python
status = fw.packages_status()
status.ok
status.missing
```

## Lockfile and environment sync

| Function | `uv` command | Effect |
|----------|--------------|--------|
| [packages_install()](ref:packages_install) | `uv sync` | Installs project dependencies |
| [packages_snapshot()](ref:packages_snapshot) | `uv lock` | Writes resolved versions to `uv.lock` |
| [packages_restore()](ref:packages_restore) | `uv sync --frozen` | Installs exact versions from `uv.lock` |
| [packages_update()](ref:packages_update) | `uv lock --upgrade` + `uv sync` | Upgrades all or named packages |

```python
fw.packages_update(["pandas", "statsmodels"])
```

## Fallback without uv

`uv` is the standard environment manager. When `uv` is absent, [package_add()](ref:package_add) appends requirements to `pyproject.toml` and installs via `pip`, and [packages_install()](ref:packages_install) installs listed dependencies via `pip`. Lockfile operations (`packages_snapshot()`, `packages_restore()`, `packages_update()`, and `dev=True`) require `uv` and raise an error with installation instructions when `uv` is missing.

## Notebook environments

Select the project's `.venv` kernel in VS Code or Positron (preconfigured in the generated `.code-workspace` file as `.venv/bin/python`), or prefix CLI renders with `uv run`:

```sh
uv run quarto render notebooks/exploration.qmd
```

## Comparison with R

The R package lists dependencies under `packages` in `settings.yml`, locks versions with `renv`, and supports `auto_attach` during `scaffold()`. Python projects use `pyproject.toml` and `uv.lock`, and each notebook or script imports its own modules explicitly.
