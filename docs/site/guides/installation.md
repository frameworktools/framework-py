---
id: installation
title: Installation
description: Requirements, companion tools, optional extras, release verification, and updates
section: getting_started
position: 1
---

## Requirements

`framework-py` requires Python 3.11 or later on macOS, Linux, or Windows.

Three optional companion tools unlock additional features:

- [uv](https://docs.astral.sh/uv/) manages project dependencies. Package functions such as [package_add()](ref:package_add) call `uv`, and [bootstrap()](ref:bootstrap) runs `uv sync` to install a cloned project's environment.
- [Quarto](https://quarto.org) renders notebooks and presentations. [save_notebook()](ref:save_notebook) and [publish_notebook()](ref:publish_notebook) invoke the `quarto` CLI.
- Git tracks version history. New projects initialize a git repository when `git` is installed.

Any editor works. Framework writes a VS Code `.code-workspace` file by default, which Positron also opens.

## Package installation

The PyPI distribution is `framework-py`, and the Python module name is `framework`. Add it to a `uv` project:

```sh
uv add framework-py
```

Or install it into an active environment with `pip`:

```sh
pip install framework-py
```

Import the module as `fw`:

```python
import framework as fw
```

Projects created by Framework already declare `framework-py` in `pyproject.toml`. Run `uv sync` after creating or cloning a project.

## Optional extras

The core package reads CSV, Parquet, Stata, and SAS files, writes CSV and Parquet files, and connects to SQLite. Optional extras install database drivers, S3 storage, and additional file formats.

| Extra | Package | Purpose |
|-------|---------|---------|
| `postgres` | `psycopg` | PostgreSQL connections |
| `mysql` | `PyMySQL` | MySQL and MariaDB connections |
| `duckdb` | `duckdb` | DuckDB connections |
| `sqlserver` | `pyodbc` | SQL Server connections |
| `s3` | `boto3` | S3-compatible object storage and publishing |
| `excel` | `openpyxl` | `.xlsx` reads and writes |
| `spss` | `pyreadstat` | SPSS reads |
| `sas` | `pyreadstat` | Version 8 SAS transport reads |
| `rds` | `pyreadr` | R `.rds` data frame reads |

List extras in brackets when installing the package:

```sh
uv add "framework-py[postgres,s3]"
```

Functions that require an uninstalled extra raise an error naming the install command. [db_drivers_status()](ref:db_drivers_status) reports installed database drivers.

## Installation check

Print the installed version to confirm the import succeeds:

```sh
python -c "import framework as fw; print(fw.__version__)"
```

Prefix the command with `uv run` inside a `uv` project.

## Release verification

GitHub Actions builds releases with signed build provenance. Verify that a wheel or source archive came from the `framework-py` repository with the GitHub CLI:

```sh
gh attestation verify framework_py-<version>-py3-none-any.whl --repo frameworktools/framework-py
```

## Updates

Upgrade the locked version in a `uv` project and sync the environment:

```sh
uv lock --upgrade-package framework-py
uv sync
```

[packages_update()](ref:packages_update) runs both commands from Python:

```python
fw.packages_update("framework-py")
```

With `pip`, upgrade in place:

```sh
pip install --upgrade framework-py
```

After updating, run [ai_skills_update()](ref:ai_skills_update) to refresh a project's AI assistant skills from the installed release. See [AI assistants](guide:ai-assistants).
