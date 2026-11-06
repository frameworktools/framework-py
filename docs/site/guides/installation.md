---
id: installation
title: Installation
description: Requirements, companion tools, optional extras, release verification, and updates
section: getting_started
position: 1
---

## Requirements

framework-py needs Python 3.11 or later. It runs on macOS, Linux, and Windows.

Three companion tools are optional. Each one enables a group of features.

- [uv](https://docs.astral.sh/uv/) manages each project's packages. The package functions, such as [package_add()](ref:package_add), call uv, and [bootstrap()](ref:bootstrap) uses it to install a cloned project's packages.
- [Quarto](https://quarto.org) renders notebooks and presentations. [save_notebook()](ref:save_notebook) and [publish_notebook()](ref:publish_notebook) call the `quarto` command.
- Git versions the project. New projects are initialized as git repositories when git is installed.

Any editor works. Framework creates a VS Code workspace file by default, which Positron also opens.

## Package installation

The distribution is named `framework-py`, and the module it installs is named `framework`. Inside a uv project, add it as a dependency.

```sh
uv add framework-py
```

With pip, install it into the active environment.

```sh
pip install framework-py
```

Code imports the module under the short name `fw`.

```python
import framework as fw
```

Projects created by Framework already list `framework-py` in their `pyproject.toml`, so a project only needs `uv sync` after it is created or cloned.

## Optional extras

The core package reads CSV, Parquet, Stata, and SAS files, writes CSV and Parquet files, and connects to SQLite. Extras add database drivers, S3 storage, and further file formats. Each extra installs one well-known package.

| Extra | Package | Used for |
|-------|---------|----------|
| `postgres` | psycopg | PostgreSQL connections |
| `mysql` | PyMySQL | MySQL and MariaDB connections |
| `duckdb` | duckdb | DuckDB connections |
| `sqlserver` | pyodbc | SQL Server connections |
| `s3` | boto3 | Publishing to S3-compatible storage |
| `excel` | openpyxl | Reading and writing `.xlsx` files |
| `spss` | pyreadstat | Reading SPSS files |
| `sas` | pyreadstat | Reading version 8 SAS transport files |

Name the extras in brackets when you add the package.

```sh
uv add "framework-py[postgres,s3]"
```

A function that needs a missing extra raises an error that names the install command. [db_drivers_status()](ref:db_drivers_status) lists the database drivers that are installed.

## Installation check

Print the installed version to confirm that Python can import the package.

```sh
python -c "import framework as fw; print(fw.__version__)"
```

In a uv project, prefix the command with `uv run`.

## Release verification

Releases are built by GitHub Actions with signed build provenance. The GitHub CLI checks that a downloaded wheel or source archive was built from the framework-py repository.

```sh
gh attestation verify framework_py-<version>-py3-none-any.whl --repo frameworktools/framework-py
```

## Updates

In a uv project, upgrade the locked version and install it.

```sh
uv lock --upgrade-package framework-py
uv sync
```

[packages_update()](ref:packages_update) runs the same two commands from Python.

```python
fw.packages_update("framework-py")
```

With pip, upgrade the package in place.

```sh
pip install --upgrade framework-py
```

After an update, [ai_skills_update()](ref:ai_skills_update) replaces a project's AI assistant skills with the versions in the new release. The [AI assistants](guide:ai-assistants) guide describes the skills.
