# framework-py

Framework for Python provides structured data science projects with a data catalog, integrity tracking, caching, saved outputs, and AI coding assistant guardrails.

`framework-py` is the Python implementation of [Framework](https://framework.pub) ([documentation](https://py.framework.pub)). It reads and writes the same project files as the R package (`settings.yml`, the data catalog, and `framework.db`), so either language can read and verify a project's data. It runs standalone without Framework Cloud or the `fw` CLI.

Status: in development. See [docs/plan.md](docs/plan.md) for the implementation plan.

## Installation

```sh
uv add framework-py
# or
pip install framework-py
```

Optional extras install database drivers, S3 storage, and additional file formats: `postgres`, `mysql`, `duckdb`, `sqlserver`, `s3`, `excel`, `spss`, `sas` (version 8 SAS transport files), and `rds` (R `.rds` data frames).

```sh
uv add "framework-py[postgres,s3]"
```

GitHub Actions builds releases with signed build provenance. Verify that a wheel or sdist came from this repository:

```sh
gh attestation verify framework_py-<version>-py3-none-any.whl --repo frameworktools/framework-py
```

## Quick start

```python
import framework as fw

fw.settings("directories.notebooks")
info = fw.data_info("inputs.raw.survey")
fw.data_list()
```

Prepare a cloned project:

```sh
uv run python -m framework bootstrap
```

## Framework Cloud sync

Pull or push packages, skills, and the data registry for a linked Framework Cloud project. Each call lists changes by section and asks once before applying them.

```python
fw.pull_all(dry_run=True)
fw.pull_all()
fw.push_all()
```

`pull_skills()`, `push_skills()`, `pull_data_registry()`, and `push_data_registry()` sync individual sections.

## Skill sync

Sync a local skill and its companion files with your Framework Cloud account:

```python
fw.skills_sync(".agents/skills/my-skill", dry_run=True)
fw.skills_sync(".agents/skills/my-skill")
```

Local edits create account versions. Edited Framework skills become private forks pinned to an exact source version. Concurrent edits stop for reconciliation. See the [Skill sync guide](https://framework.pub/docs/skill-sync).

When a skill's copies in `.claude/skills` and `.agents/skills` differ, copy the version to keep over the others (what it replaces is backed up), then sync:

```python
fw.skills_copy(from_="claude", skills=["my-skill"])
```

## Development

```sh
uv sync
uv run pytest
uv run ruff check && uv run ruff format --check
uv run mypy
uv run python scripts/check_parity.py
uv run python scripts/sync_assets.py --check
```

`scripts/sync_assets.py` copies shared files (JSON Schemas, the `framework.db` schema, conformance fixtures, and the R export list) from the R package. Edit shared assets in the R repository, then run `scripts/sync_assets.py` here.

`parity.toml` maps every exported R function to its Python equivalent or records why none exists.
