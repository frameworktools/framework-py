---
id: environment
title: Environment
description: The .env file, keeping secrets out of settings, Framework's environment variables, and scratch helpers
section: features
position: 14
---

## Overview

Framework keeps credentials and machine-specific values in a `.env` file that git ignores, and keeps them out of the settings files that git tracks. This guide covers the `.env` file, the environment variables Framework reads, and the helpers for quick, throwaway output.

## The .env File

Every new project except a bare one has a `.env` file in its root. It starts with empty placeholders for a PostgreSQL connection and S3 storage.

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

Fill in the values the project uses and add any others it needs.

[scaffold()](ref:scaffold) and [settings_read()](ref:settings_read) load `.env` into `os.environ`. Values in the file replace variables that are already set, as the R package's dotenv loader does, so a project behaves the same in both languages. The `dotenv_location` key in the settings points at a different file, or at a folder that holds one.

The file follows the rules of R's dotenv package.

- Blank lines and lines that start with `#` are skipped. A `#` later in a line is part of the value.
- Every other line is `KEY=VALUE`, optionally preceded by `export`.
- A value wrapped in matching single or double quotes loses the quotes. Nothing inside the quotes is unescaped.
- A line that does not match this form is an error.

### Keeping .env out of Git

Every project type's `.gitignore` excludes `.env`. Project creation and [bootstrap()](ref:bootstrap) add a rule when the file is missing one, and creation stops with an error when git would still track `.env`. [git_security_audit()](ref:git_security_audit) fails when git tracks a `.env` file.

To tell collaborators which variables a project needs, commit a `.env.example` with empty values. `bootstrap()` copies it to `.env` in a fresh clone and lists the variables that are still empty.

## Using Environment Variables in Settings

Settings refer to environment variables with `env("VAR")`, or `env("VAR", "default")` for a fallback.

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

The settings file can be committed and shared, and each person's credentials stay in their own `.env`. The [settings.yml](guide:settings-yml) guide describes expressions.

## Secrets Never Go in Settings

Framework refuses to store secret-looking values in settings. A variable counts as secret when its name contains `PASSWORD`, `SECRET`, `TOKEN`, `ACCESS_KEY`, `API_KEY`, `PRIVATE_KEY`, or `CREDENTIAL`.

- [spec_check()](ref:spec_check) rejects a project spec whose `env` section gives such a variable a value, so blueprints carry only empty placeholders.
- [configure_global()](ref:configure_global) refuses such a value in `defaults.env` of your global settings.
- Framework Cloud project tokens are written to the project's `.env`, and personal tokens to the system keyring.

```python
import framework as fw

fw.configure_global({"defaults": {"env": {"DB_PASSWORD": "hunter2"}}})
```

```text
SettingsError: defaults.env: secrets must not be stored in settings (DB_PASSWORD).
Leave the values empty and put them in each project's .env file.
```

When a secret has been committed by mistake, remove the file from git, commit, and rotate the secret, because it remains in the repository's history.

## Environment Variables Framework Reads

| Variable | Effect |
|----------|--------|
| `FW_CONFIG_HOME` | The global config folder. See [Setup](guide:setup). |
| `FW_CONFIG_ACTIVE` | The settings environment, such as `production`. `R_CONFIG_ACTIVE` and `R_CONFIG_NAME` are read after it. |
| `FW_SEED` | Turns on seeding in `scaffold()` when the settings do not decide. |
| `FW_CACHE_DIR` | The cache folder. See [Caching](guide:caching). |
| `FW_LEDGER` | `live`, `on`, or `true` sends data hashes to the cloud ledger. `off` turns it off. |
| `FW_CLOUD_URL` | The Framework Cloud server. |
| `FW_CLOUD_TOKEN` | A personal cloud token, used before the keyring. |
| `FW_TOKEN_FILE` | A file that holds the personal cloud token. |
| `FW_PROJECT_TOKEN` | The project's cloud token, usually set in `.env`. |
| `S3_*` and `AWS_*` | Storage bucket credentials and locations. See [Publishing](guide:publishing). |

## Scratch Files

The scratch folder holds throwaway output that does not belong in the project's results. Every project type's `.gitignore` excludes `scratch/` folders. The folder is `directories.scratch`, which is `scratch/` in standard and privacy-sensitive projects.

[scratch_capture()](ref:scratch_capture) writes an object to the scratch folder and returns it unchanged, so it can sit in the middle of a pipeline. DataFrames are written as TSV and other objects as text.

```python
survey = fw.scratch_capture(fw.data_read("inputs.raw.survey"), "survey_peek", n=100)
fw.scratch_capture({"sites": ["north", "south"]}, "sites")
```

The arguments choose the output.

- `name` is the file name. An extension (`.txt`, `.csv`, `.tsv`, or `.pkl`) sets the format. Without a name, the file is named `capture_<timestamp>`.
- `to` is `"text"`, `"csv"`, `"tsv"`, or `"pickle"`.
- `n` writes only the first `n` rows of a DataFrame.
- `location` writes to another folder.

[scratch_clean()](ref:scratch_clean) deletes the files in the scratch folder and leaves its subfolders alone.

```python
fw.scratch_clean()
```

## Capturing Printed Output

[capture_output()](ref:capture_output) is a context manager that captures everything printed to standard output and standard error inside its block.

```python
with fw.capture_output() as captured:
    print(survey.describe())

captured.output
captured.lines
```

An exception inside the block is not caught. The output printed before it remains available in `captured.output`.
