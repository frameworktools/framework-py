---
id: environment
title: Environment
description: .env files, secret validation, Framework environment variables, scratch captures, and output capture
section: features
position: 14
---

## Overview

Framework stores credentials and machine-specific configuration in a gitignored `.env` file while keeping shareable connection and project definitions in `settings.yml`.

## .env files

Every non-`bare` project includes a root `.env` file pre-populated with PostgreSQL and S3 placeholders:

```sh
POSTGRES_HOST=
POSTGRES_PORT=5432
POSTGRES_DB=
POSTGRES_SCHEMA=public
POSTGRES_USER=
POSTGRES_PASSWORD=
S3_BUCKET=
S3_ACCESS_KEY=
S3_SECRET_KEY=
S3_REGION=
S3_ENDPOINT=
S3_SESSION_TOKEN=
```

`framework-py` and the R package parse `.env` identically:

- Existing process environment variables take precedence over `.env`.
- Empty assignments (`S3_ACCESS_KEY=`) are skipped rather than overwriting variables with `""`.
- [settings_read()](ref:settings_read) applies `.env` values only while evaluating `env()` expressions without mutating `os.environ`.
- [scaffold()](ref:scaffold) loads `.env` into `os.environ` for the active session, clearing values loaded by any previously scaffolded project first.
- Blank lines and `#` comment lines are skipped; inline `#` characters remain part of the value; optional `export ` prefixes and matching outer single or double quotes are stripped without unescaping inner text.

Set `dotenv_location` in `settings.yml` to load `.env` from an alternate file or directory.

### Git exclusion and .env.example

Every project `.gitignore` excludes `.env`, [new_project()](ref:new_project) aborts if git would track `.env`, and [git_security_audit()](ref:git_security_audit) fails if `.env` is tracked. Commit a `.env.example` file with empty keys to document required variables; [bootstrap()](ref:bootstrap) copies `.env.example` to `.env` on clone and lists unset keys referenced by `env()` in `settings.yml`.

## Referencing variables in settings

Reference environment variables in `settings.yml` with `env("VAR")` or `env("VAR", "default")`:

```yaml
connections:
  databases:
    warehouse:
      driver: postgres
      host: env("POSTGRES_HOST")
      port: env("POSTGRES_PORT", "5432")
      database: env("POSTGRES_DB")
      user: env("POSTGRES_USER")
      password: env("POSTGRES_PASSWORD")
```

See [settings.yml](guide:settings-yml) for expression rules.

## Secret validation

Framework rejects literal values for variables whose names contain `PASSWORD`, `SECRET`, `TOKEN`, `ACCESS_KEY`, `API_KEY`, `PRIVATE_KEY`, or `CREDENTIAL`:

- [spec_check()](ref:spec_check) rejects non-empty secret values in project specs and blueprints.
- [configure_global()](ref:configure_global) rejects secret values in `defaults.env`:

```python
import framework as fw

fw.configure_global({"defaults": {"env": {"DB_PASSWORD": "hunter2"}}})
```

```text
SettingsError: defaults.env: secrets must not be stored in settings (DB_PASSWORD).
Leave the values empty and put them in each project's .env file.
```

If a credential is ever committed to git, remove the file, commit, and rotate the credential immediately because it remains in commit history.

## Framework environment variables

| Variable | Purpose |
|----------|---------|
| `FW_CONFIG_HOME` | Global configuration directory override. See [Setup](guide:setup). |
| `FW_CONFIG_ACTIVE` | Active `settings.yml` environment (`R_CONFIG_ACTIVE` and `R_CONFIG_NAME` checked next). |
| `FW_SEED` | Enables seeding in `scaffold()` when `seed_on_scaffold` is unset. |
| `FW_CACHE_DIR` | Cache directory override. See [Caching](guide:caching). |
| `FW_LEDGER` | `live`/`on`/`true` enables live Cloud ledger pushes; `off` disables them. |
| `FW_CLOUD_URL` | Framework Cloud server URL. |
| `FW_CLOUD_TOKEN` | Personal Cloud token override (checked before keyring). |
| `FW_TOKEN_FILE` | File path storing the personal Cloud token. |
| `FW_PROJECT_TOKEN` | Project Cloud token (typically stored in `.env`). |
| `S3_*`, `AWS_*` | S3-compatible bucket credentials and endpoints. See [Publishing](guide:publishing). |

## Scratch captures

`directories.scratch` (`scratch/`, gitignored across all project types) holds temporary inspection files. [scratch_capture()](ref:scratch_capture) writes an object to `scratch/` (TSV for DataFrames, plain text otherwise) and returns the original object unchanged for pipeline chaining:

```python
survey = fw.scratch_capture(fw.data_read("inputs.raw.survey"), "survey_peek", n=100)
fw.scratch_capture({"sites": ["north", "south"]}, "sites")
```

Options: `name` (filename; `.txt`, `.csv`, `.tsv`, or `.pkl` sets format; defaults to `capture_<timestamp>`), `to` (`"text"`, `"csv"`, `"tsv"`, or `"pickle"`), `n` (row limit for DataFrames), and `location` (alternate folder).

[scratch_clean()](ref:scratch_clean) deletes top-level files in `scratch/` while preserving subdirectories:

```python
fw.scratch_clean()
```

## Standard output capture

[capture_output()](ref:capture_output) captures `stdout` and `stderr` printed inside a `with` block without swallowing exceptions:

```python
with fw.capture_output() as captured:
    print(survey.describe())

captured.output
captured.lines
```
