# framework-py

Framework for Python: structured data science projects with a data catalog,
data integrity tracking, caching, saved outputs, and guardrails for AI coding
assistants.

framework-py is the Python implementation of
[Framework](https://framework.table1.org). It reads and writes the same
project files as the R package (`settings.yml`, the data catalog, and
`framework.db`), so either language can work with a project's data. It works
without Framework Cloud and without the `fw` command-line tool.

Status: in development. The plan is in [docs/plan.md](docs/plan.md).

## Install

```sh
uv add framework-py
# or
pip install framework-py
```

Optional extras add database drivers, S3 storage, and more file formats:
`postgres`, `mysql`, `duckdb`, `sqlserver`, `s3`, `excel`, `spss`, and `sas`
(version 8 SAS transport files).

```sh
uv add "framework-py[postgres,s3]"
```

Releases are published from GitHub Actions with signed build provenance. To
check that a downloaded wheel or sdist was built from this repository:

```sh
gh attestation verify framework_py-<version>-py3-none-any.whl --repo frameworktools/framework-py
```

## Use

```python
import framework as fw

fw.settings("directories.notebooks")
info = fw.data_info("inputs.raw.survey")
fw.data_list()
```

After cloning a project, prepare it with:

```sh
uv run python -m framework bootstrap
```

## Skill sync

Sync a local skill and its companion files with your Framework Cloud account:

```python
fw.skills_sync(".agents/skills/my-skill", dry_run=True)
fw.skills_sync(".agents/skills/my-skill")
```

Local edits create account versions. Edited Framework skills become private
forks with an exact source version. Concurrent edits stop for reconciliation.
[Skill sync guide](https://framework.pub/docs/skill-sync).

## Development

```sh
uv sync
uv run pytest
uv run ruff check && uv run ruff format --check
uv run mypy
uv run python scripts/check_parity.py
uv run python scripts/sync_assets.py --check
```

Shared files (JSON Schemas, the `framework.db` schema, conformance fixtures,
and the R export list) are copied from the R package with
`scripts/sync_assets.py`. Change them in the R repository, then copy them
again.

`parity.toml` maps every function the R package exports to its Python
equivalent, or gives the reason there is none.
