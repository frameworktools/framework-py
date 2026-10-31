# framework-py plan

Status: decided 2026-10-30, ready for phase 1. Nothing is committed, and no
PyPI name is registered.

## Goal

framework-py is a native Python implementation of Framework with feature parity
with the R package on branch `v2`. It creates projects itself, reads and writes
the same project files as R, and works without the cloud and without the Go
CLI. It never calls R or the CLI.

The reference is `~/Projects/framework` on `v2` at version 1.9.0.9000.

## Distribution and import names

PyPI was checked on 2026-10-30. A name that returns 404 from both the JSON API
and the simple index is unregistered. PyPI can still reject a generic name at
first upload, so availability is final only when the first release is
published.

| Name | Status |
|------|--------|
| `frameworktools` | free |
| `framework-py` | free |
| `framework-tools` | free |
| `framework` | free, but generic and likely to be refused or squatted |
| `framework-pub` | free |
| `fw` | taken (an unrelated experiment organizer) |
| `pyframework` | taken |

Erik chose the distribution name `framework-py` on 2026-10-30. The import
package is `framework`.

- `import framework as fw` mirrors `library(framework)` in R, so the skills,
  cheatsheet, and AGENTS.md stub can name the same functions in both
  languages.
- No current PyPI distribution installs a top-level `framework` module.

The name is not registered yet. It is claimed by the first release.

## Tooling

- **Python:** 3.11 and later (`requires-python = ">=3.11"`). CI tests 3.11,
  3.12, 3.13, and 3.14. 3.15 is added when uv ships it. 3.10 reaches end of
  life this month and is not supported.
- **Environments and locking:** uv, with `uv.lock` committed.
- **Build backend:** hatchling.
- **Lint and format:** ruff (`ruff check` and `ruff format`).
- **Type checker:** mypy in strict mode for `src/`. The package ships
  `py.typed`.
- **Tests:** pytest. No plugins beyond pytest-cov.
- **CI:** GitHub Actions on Ubuntu, macOS, and Windows. Jobs: lint, type
  check, tests across the Python matrix, golden trees, the shared-assets check,
  and the parity check. Releases publish to PyPI with trusted publishing, so no
  API token is stored in the repository.

### Dependencies

Required: PyYAML, jsonschema, httpx, keyring, pandas, and pyarrow, plus
SecretStorage on Linux (already a keyring dependency there). The `.env`
reader is about 40 lines and is written in the package, so python-dotenv is not
needed. `framework.db` uses the standard library `sqlite3` module.

Optional extras, each one well-known package:

| Extra | Package | Used for |
|-------|---------|----------|
| `postgres` | psycopg | PostgreSQL connections |
| `mysql` | PyMySQL | MySQL and MariaDB connections |
| `duckdb` | duckdb | DuckDB connections |
| `sqlserver` | pyodbc | SQL Server connections |
| `s3` | boto3 | `publish()` to S3-compatible storage, `storage_test()` |
| `excel` | openpyxl | reading and writing `.xlsx` |
| `spss` | pyreadstat | reading SPSS files |

Stata (`.dta`) and SAS (`.sas7bdat`, `.xpt`) use readers built into pandas.
A missing extra raises an error that names the `pip install
framework-py[extra]` command.

## Package layout

```
pyproject.toml
uv.lock
parity.toml                 # the parity manifest (see below)
src/framework/
  __init__.py               # the public API, with an explicit __all__
  py.typed
  __main__.py               # python -m framework create --from-layers FILE --path DIR
  errors.py                 # FrameworkError and its subclasses
  project.py                # project root discovery, project_info(), status()
  config_home.py            # config_dir(), global settings, project registry
  settings.py               # settings(), settings_read(), settings_write(), split files
  dotenv.py                 # .env reading and writing
  db.py                     # framework.db: open, initialize from init.sql, records
  integrity.py              # SHA-256 file hashes
  catalog.py                # the data catalog and dot-notation names
  data.py                   # data_read(), data_save(), data_add(), data_info(), data_list()
  cache.py
  outputs.py                # save_table(), save_figure(), save_model(), result_list()
  scratch.py
  connections/              # db_connect(), db_query(), db_execute(), one module per driver
  storage.py                # S3-compatible storage
  publish.py
  spec/                     # resolve.py, check.py, create.py, layers.py
  quarto.py                 # _quarto.yml generation
  notebooks.py              # make_notebook(), make_script(), stubs
  templates.py              # user templates in the config folder
  ai.py                     # skills, AGENTS.md and assistant stubs, agent guards
  git_hooks.py
  audit.py                  # git_security_audit()
  packages.py
  cloud/                    # server.py, tokens.py, client.py, ledger.py, sync.py
  _assets/                  # shared files copied from the R repository (see below)
tests/
  golden/                   # shared golden trees, copied from the R repository
  test_*.py
```

### Shared assets

The JSON Schemas, `init.sql`, gitignore templates, skills, AGENTS.md stub
text, Quarto templates, and the package-default settings must be identical in
R and Python. The R repository holds the canonical copies, as
`framework_2_plan.md` already decides for the schemas. framework-py copies them
into `src/framework/_assets/` at a pinned R release tag, with a manifest that
records the tag and each file's SHA-256. A CI job fails when a copied file no
longer matches the file at that tag. framework-cloud and the Go CLI use the
same pattern for the schemas.

## API style

- The public API is module-level functions in `framework`. Function names match
  R where the R name reads naturally in Python, so documentation and skills
  share one vocabulary.
- R arguments that take unevaluated code become Python idioms. `db_with()` and
  `db_transaction()` become context managers. `cache_remember()` takes a
  callable, and `cache_remember` also works as a decorator.
- Data comes back as pandas DataFrames. Arguments are typed, and keyword-only
  where R relies on names.
- Errors are `FrameworkError` subclasses with messages that say how to fix the
  problem. Reading an `.rds` file raises `UnsupportedFormatError`, which says
  the file is R-only and suggests saving it as Parquet or CSV from R.
- Progress messages go through the `framework` logger. `scaffold()` and
  project creation attach a stderr handler so interactive use shows them, and
  library use stays quiet unless configured.
- Lists of four named variants in R (`new_project()`, `new_course()`, and so
  on) become one function with a `type` argument.

## Shared formats

These follow the brief and were agreed with framework-d4 on 2026-10-30.

- **Settings.** `settings.yml`, split files under `settings/`, and environment
  sections (`default:` plus named sections). Both languages read the active
  section from `FW_CONFIG_ACTIVE`, then `R_CONFIG_ACTIVE`, then `default`.
  `env("VAR", "default")` works in both languages. R's `!expr` runs
  arbitrary R code, so Python supports only `!expr Sys.getenv(...)` and raises
  a clear error for anything else.
- **framework.db.** Created from the shared `init.sql`. Hashes are lowercase
  hex SHA-256 of the file bytes, as `openssl::sha256()` produces. Booleans
  are stored as 0 and 1. Timestamps follow what RSQLite writes: seconds since
  the Unix epoch as REAL in the `data`, `cache`, and `meta` tables (checked
  2026-10-30). R's `results` table holds local `%Y-%m-%d %H:%M:%S` strings,
  so readers accept both forms.
- **Data catalog.** Dot-notation names resolve the same way in both languages.
- **Saved data.** CSV and Parquet in both languages. R is adding Parquet to
  `data_read()` and `data_save()`. A dot-notation name without an extension
  is saved as `.rds` in R and as Parquet in Python. The catalog records each
  file's type, so either language reads the other's entries.
- **Cache.** Both languages share the `cache` table. R stores values as
  `.rds`. Python stores DataFrames as Parquet and other values as pickle, and
  records the file in `file_path` (R leaves it empty). An entry the current
  language cannot read is a cache miss with a warning, and the entry is left
  in place. `cache_forget()` and `cache_flush()` in Python remove entries from
  either language.
- **Catalog paths.** Python writes catalog and `framework.db` paths relative
  to the project root, so a project still works after it is moved or cloned.
- **.env.** `FW_PROJECT_TOKEN` and `FW_CLOUD_URL`, with the same quoting rules
  as R's dotenv package.
- **Project registry and global settings.** `projects.yml`, `settings.yml`,
  `cloud.yml`, and the `cloud-token` fallback files in the global config
  folder.

### Global config folder

Decided 2026-10-30 (option A). R must stay CRAN-safe, and few people use R
and Python together, so each tool keeps its own folder and a sync function
copies settings across when someone needs it.

- **Python and `fw`:** `FW_CONFIG_HOME` when set, otherwise
  `$XDG_CONFIG_HOME/framework`, else `~/.config/framework`
  (`%USERPROFILE%\.config\framework` on Windows).
- **R:** `FW_CONFIG_HOME` when set, otherwise
  `tools::R_user_dir("framework", "config")`. R never writes outside that
  folder.
- **Sync:** `config_sync(from_dir=None, dry_run=False)` copies files from R's
  folder into Python's when they are missing from Python's or newer in R's:
  `settings.yml`, `cloud.yml`, and the `cloud-token*` files. Token files are
  written with mode 0600, modification times are preserved, and dotfiles are
  skipped. Python never writes to R's folder. R's `config_sync()` does the
  same in the other direction.
- **Login fallback:** Python reads R's folder for the saved cloud server and
  token files when its own folder has none. R's folder is
  `R_USER_CONFIG_DIR`, else `XDG_CONFIG_HOME`, else `%APPDATA%\R\config` on
  Windows, `~/Library/Preferences/org.R-project.R` on macOS, and `~/.config`
  on Linux, with `/R/framework` appended in every case.

The rule is recorded in the R repository's `framework_2_plan.md` under
"Global config folders".

## Cloud

Python matches `R/cloud.R` on `v2`.

- User-scoped calls resolve the server from the argument, then `FW_CLOUD_URL`,
  then the `cloud_login()` default in `cloud.yml`, then
  `https://framework.pub`.
- Project-scoped calls resolve it from the argument, then the project's
  `.env` `FW_CLOUD_URL`, then the environment variable, then
  `https://framework.pub`.
- Plain `http` is accepted only for `localhost`, `127.0.0.1`, and `[::1]` (R
  also accepts `[::1]`).
- Tokens resolve from `FW_CLOUD_TOKEN`, then `FW_TOKEN_FILE`, then the keyring
  entry with service `framework-cloud:<host[:port]>` and username `token`, then
  the token file in the config folder. `FW_TOKEN_FILE` is authoritative when
  set. The token file is `cloud-token` for framework.pub and
  `cloud-token-<host>` for other servers, where `<host>` is `host[:port]`
  lowercased with characters outside `[a-z0-9.-]` replaced by `_`. The file
  is written with mode 0600, and its first line is the token. The legacy
  `framework.pub` keyring service and `~/.secrets/framework/token` are read
  for framework.pub only, as R does.
- `cloud_login()` shows the same consent notice and needs `consent=True` when
  not interactive.
- Nothing contacts the cloud unless the user connects. Data and code are never
  sent.

The keyring entries work across tools on macOS (Keychain service and account)
through the keyring package. On Linux, R's keyring package finds only items
whose `xdg:schema` attribute is `com.rstudio.keyring.password`. Python's
keyring package writes items without it, with an extra `application`
attribute, and its replace adds a second item beside R's. framework-cli's
Linux CI showed that R and `fw` then kept reading the older R-written value.
Python therefore writes through SecretStorage directly, as `fw` does: it
deletes every item matching `service` and `username: token`, then creates one
item with those attributes and R's `xdg:schema`. Reading prefers that item,
and deleting removes every match. On Windows, R's keyring package stores the token under the
Credential Manager target `:framework-cloud:<host[:port]>:token`, and Python's
keyring package would choose a different target. Python therefore reads and
writes that exact target with `CredReadW` and `CredWriteW` through ctypes, as
`fw` does, with no extra dependency. R stores the credential blob as UTF-8,
while Python's keyring package uses UTF-16LE. Python therefore writes UTF-8.
When reading, it decodes UTF-16LE if every second byte is zero (tokens are
ASCII), and UTF-8 otherwise, as `fw` does. A Windows CI job stores a token the
way R does and reads it from Python.

On macOS, Python's keyring package stores the token as plain text:
`security find-generic-password -s framework-cloud:<host> -a token -w` prints
exactly the stored token (checked 2026-10-30).

## Project creation and golden trees

`project_create(spec, location)` and `project_create_from_layers(layers_file,
path, interactive=False)` are native Python. Headless mode follows R: no
prompts, no git, no registry entry, no writes under `.github/workflows/`, and
the same allowed-files check for an existing checkout.
`python -m framework create --from-layers` runs it, so the framework.pub
GitHub Action can run with either language.

### Language

`spec.v1` and `layer.v1` now have a `language` field: `r`, `python`, or
`stata`. It is required in a resolved spec. In a layer it is optional, and a
layers file with no language means `r`, so existing layers files keep their
output. Each engine builds only its own language. R refuses any other value,
and Python refuses anything except `python`.

A Python project gets `scaffold.py`, Quarto stubs with Python chunks, and a
`pyproject.toml` that depends on `framework-py`. Python never writes or
maintains R templates. Projects do not mix languages: each project has one
`language`, and R and Python projects share data through the shared formats.

### Conformance and golden trees

- **The merge is shared.** The R repository has conformance fixtures in
  `inst/conformance/merge/`, documented in `inst/conformance/README.md`. Each
  case maps layers to `{spec, gitignore_blocks, conflicts}`, without package
  defaults. Python copies them at a pinned release tag with a SHA-256 check
  and compares parsed JSON: key order is ignored, `{}` equals `[]`, and
  conflicts are compared as a set. Python's package defaults are its own
  layer 0 and are not part of these fixtures.
- **The tree is Python's own.** Python golden trees live in framework-py: a
  `layers.json` per case and the expected tree. Starting cases: one per type
  (`project`, `project_sensitive`, `course`, `presentation`, and `bare`), a
  sensitive facet on an ordinary project, `null` removals, `files` templates
  with `{{variable}}` substitution, skills as content and as `{package: true}`,
  and each assistant combination. Dates and `source.framework_version` are
  fixed during tests.
- **One cross-language check (low priority).** A CI job builds a Python project and reads
  its `settings.yml` and `settings/*.yml` with R's `settings_read()`. The
  values must equal what Python reads. The comparison is on parsed values, so
  Python's YAML formatting does not need to match R's.

### Safety

- Sensitive projects get the Claude Code deny rules and the fail-closed
  sandbox in `.claude/settings.json`, computed from the same restricted paths
  as R. The golden trees cover this.
- Data dictionaries are written for review and never committed or pushed by
  Framework.
- `spec_check()` rejects secret-looking `env` values, as R does, and settings
  never hold secrets.
- R, Python, and `fw` install the same pre-commit hook: a POSIX `sh` script
  that finds a runtime and runs the checks in that language's package. The
  canonical copy is `inst/hooks/pre-commit` in the R package, and Python
  syncs it into `src/framework/_assets/hooks/`. It uses R when `renv.lock`, `.Rprofile`,
  or a `.Rproj` file is present, and Python when `pyproject.toml`, `uv.lock`,
  or `.venv` is present. When no runtime is found, the commit fails with a
  message, because the hook guards against committing restricted data.
  Python's entry point is `python -m framework hook pre-commit`.

## Parity manifest

`parity.toml` lists every exported R function with its Python equivalent or
the reason it has none. A CI job checks that:

- every export in R's `NAMESPACE` at the pinned tag has an entry, and
- every entry marked `ported`, `renamed`, or `folded` names something that
  exists in `framework.__all__`.

```toml
[functions.data_read]
python = "data_read"
status = "ported"     # ported | renamed | folded | r-only | deprecated | omitted
note = ""
```

### Mapping

R exports 122 functions. Status meanings:

- **ported:** same name and behavior.
- **renamed:** same behavior under a Python name.
- **folded:** covered by another Python function or idiom.
- **r-only:** has no meaning outside R.
- **deprecated:** deprecated in R 2.0, so Python never adds it.
- **omitted:** could be ported but is proposed to be left out. Erik decides.

#### Project creation and structure

| R | Python | Status | Note |
|---|--------|--------|------|
| `new()` | `new(name=None, location=None, type=None, *, key=None)` | ported | Like R, `type=None` prompts when interactive and means `bare` otherwise. `key` fetches cloud layers, as planned for R 2.0 |
| `new_project()` | `new(type="project")` | folded | |
| `new_project_sensitive()` | `new(type="project_sensitive")` | folded | |
| `new_course()` | `new(type="course")` | folded | |
| `new_presentation()` | `new(type="presentation")` | folded | |
| `project_create()` | `project_create(spec, location)` | ported | Takes a resolved spec only; the R argument list maps to spec fields |
| `project_create_from_layers()` | `project_create_from_layers()` | ported | Also `python -m framework create --from-layers` |
| `spec_resolve()` | `spec_resolve()` | ported | Returns a `ResolvedSpec` with `.spec` and `.conflicts` |
| `spec_check()` | `spec_check()` | ported | |
| `project_add_directory()` | `project_add_directory()` | ported | |
| `project_info()` | `project_info()` | ported | |
| `status()` | `status()` | ported | |
| `scaffold()` | `scaffold()` | ported | Finds the root, loads settings and `.env`, seeds `random` and NumPy, adds the functions folder to `sys.path`. Package attaching and ggplot themes are R-only parts |
| `standardize_wd()` | `project_root()` | folded | Python resolves paths from the root and does not change the working directory |
| `bootstrap()` | `bootstrap()` | ported | Same contract as R. Packages come from `uv.lock` instead of `renv.lock` |
| `bootstrap_project_init()` | none | r-only | Writes the legacy `init.R` |
| `quarto_generate_all()` | `quarto_generate_all()` | ported | |
| `quarto_regenerate()` | `quarto_regenerate()` | ported | |

#### Settings and global configuration

| R | Python | Status | Note |
|---|--------|--------|------|
| `settings()` | `settings()` | ported | |
| `settings_read()` | `settings_read()` | ported | |
| `settings_write()` | `settings_write()` | ported | |
| `configure_global()` | `configure_global()` | ported | |
| `load_settings_catalog()` | `load_settings_catalog()` | ported | |
| `fw_config_dir()` | `config_dir()` | renamed | The module name already says framework |
| `config_sync()` | `config_sync()` | ported | Pulls from R's folder into Python's |
| `read_frameworkrc()` | `global_settings()` | renamed | The R name refers to the retired `.frameworkrc` |
| `write_frameworkrc()` | `configure_global()` | folded | |
| `project_list()` | `project_list()` | ported | Reads the shared `projects.yml` |
| `add_project_to_config()` | `project_register()` | renamed | |
| `remove_project_from_config()` | `project_unregister()` | renamed | |
| `read_framework_template()` | `read_framework_template()` | ported | |
| `write_framework_template()` | `write_framework_template()` | ported | |
| `reset_framework_template()` | `reset_framework_template()` | ported | |
| `now()` | `now()` | ported | Same string format as R |

#### Data, integrity, and cache

| R | Python | Status | Note |
|---|--------|--------|------|
| `data_read()` | `data_read()` | ported | `.rds` raises `UnsupportedFormatError` |
| `data_save()` | `data_save()` | ported | CSV and Parquet |
| `data_add()` | `data_add()` | ported | |
| `data_info()` | `data_info()` | ported | |
| `data_list()` | `data_list()` | ported | |
| `data_verify()` | `data_verify()` | ported | |
| `cache()` | `cache()` | ported | |
| `cache_get()` | `cache_get()` | ported | Takes `default`, returned on a miss. R's unused `expire_after` is dropped |
| `cache_remember()` | `cache_remember()` | ported | Takes a callable, and works as a decorator |
| `cache_forget()` | `cache_forget()` | ported | |
| `cache_flush()` | `cache_flush()` | ported | |
| `cache_list()` | `cache_list()` | ported | |
| `scratch_capture()` | `scratch_capture()` | ported | Python cannot see the caller's expression, so the default name is `capture_<timestamp>` |
| `scratch_clean()` | `scratch_clean()` | ported | |

#### Outputs

| R | Python | Status | Note |
|---|--------|--------|------|
| `save_table()` | `save_table()` | ported | CSV, Parquet, and xlsx (extra) |
| `save_figure()` | `save_figure()` | ported | Any object with `savefig()` (matplotlib, seaborn) or `save()` (plotnine). No plotting dependency |
| `save_model()` | `save_model()` | ported | Pickle (`<name>.pkl`). The R `.rds` models are R-only |
| `save_notebook()` | `save_notebook()` | ported | Renders with the `quarto` command when the file needs rendering |
| `save_report()` | `save_report()` | ported | |
| `result_list()` | `result_list()` | ported | |

#### Databases

| R | Python | Status | Note |
|---|--------|--------|------|
| `db_connect()` | `db_connect()` | ported | Returns a DB-API connection that is also a context manager |
| `db_query()` | `db_query()` | ported | Returns a DataFrame. Parameters are passed separately, never formatted into SQL |
| `db_execute()` | `db_execute()` | ported | |
| `db_with()` | `with db_connect(name) as conn:` | folded | |
| `db_transaction()` | `db_transaction()` | ported | A context manager |
| `db_list()` | `db_list()` | ported | |
| `connections_list()` | `connections_list()` | ported | |
| `db_drivers_status()` | `db_drivers_status()` | ported | Reports which optional extras are installed |
| `db_drivers_install()` | none | r-only | Installs R packages. Python drivers come from extras |
| `.list_tables()` | private helper | omitted | R exports it only so S3 methods dispatch |
| `.list_columns()` | private helper | omitted | Same reason |
| `.has_column()` | private helper | omitted | Same reason |

#### Storage and publishing

| R | Python | Status | Note |
|---|--------|--------|------|
| `publish()` | `publish()` | ported | S3 needs the `s3` extra. Framework Cloud uses httpx |
| `publish_notebook()` | `publish_notebook()` | ported | |
| `publish_data()` | `publish_data()` | ported | |
| `publish_dir()` | `publish_dir()` | ported | |
| `publish_list()` | `publish_list()` | ported | |
| `storage_test()` | `storage_test()` | ported | |

#### Cloud

| R | Python | Status | Note |
|---|--------|--------|------|
| `cloud_login()` | `cloud_login()` | ported | |
| `cloud_logout()` | `cloud_logout()` | ported | |
| `cloud_status()` | `cloud_status()` | ported | |
| `cloud_settings()` | `cloud_settings()` | ported | |
| `cloud_sync()` | `cloud_sync()` | ported | |
| `ledger_push()` | `ledger_push()` | ported | |
| `ledger_list()` | `ledger_list()` | ported | |
| `setup()` | `new(key=...)` | folded | The token path becomes `new(key)` as in the R 2.0 plan. The GUI part is R-only |

#### Notebooks and stubs

| R | Python | Status | Note |
|---|--------|--------|------|
| `make_notebook()` | `make_notebook()` | ported | Quarto (`.qmd`) with Python chunks by default, `.ipynb` on request |
| `make_qmd()` | `make_qmd()` | ported | |
| `make_revealjs()` | `make_revealjs()` | ported | |
| `make_presentation()` | `make_presentation()` | ported | |
| `make_script()` | `make_script()` | ported | Writes a `.py` script |
| `make_rmd()` | none | r-only | R Markdown |
| `stubs_list()` | `stubs_list()` | ported | Python stubs |
| `stubs_path()` | `stubs_path()` | ported | |
| `stubs_publish()` | `stubs_publish()` | ported | |

#### AI context

| R | Python | Status | Note |
|---|--------|--------|------|
| `ai_skills_update()` | `ai_skills_update()` | ported | Reads `.framework/spec.json` as the 2.0 plan describes |
| `ai_generate_context()` | none | deprecated | Replaced by the AGENTS.md stub in 2.0 |
| `ai_regenerate_context()` | none | deprecated | Deprecated in R 2.0 |
| `ai_sync_context()` | none | deprecated | Deprecated in R 2.0 |

#### Git and security

| R | Python | Status | Note |
|---|--------|--------|------|
| `git_hooks_install()` | `git_hooks_install()` | ported | Hooks call Python. Shared hooks are an open item |
| `git_hooks_uninstall()` | `git_hooks_uninstall()` | ported | |
| `git_hooks_enable()` | `git_hooks_enable()` | ported | |
| `git_hooks_disable()` | `git_hooks_disable()` | ported | |
| `git_hooks_list()` | `git_hooks_list()` | ported | |
| `git_security_audit()` | `git_security_audit()` | ported | |
| `git_status()` | none | omitted | R wraps the git command for RStudio console users. Python users run git directly |
| `git_add()` | none | omitted | Same reason |
| `git_commit()` | none | omitted | Same reason |
| `git_diff()` | none | omitted | Same reason |
| `git_log()` | none | omitted | Same reason |
| `git_pull()` | none | omitted | Same reason |
| `git_push()` | none | omitted | Same reason |

#### Packages and environments

| R | Python | Status | Note |
|---|--------|--------|------|
| `package_add()` | `package_add()` | ported | Adds to `pyproject.toml` and installs with uv when present, otherwise pip |
| `packages_list()` | `packages_list()` | ported | Reads `pyproject.toml` |
| `packages_install()` | `packages_install()` | ported | `uv sync`, or pip |
| `packages_status()` | `packages_status()` | ported | |
| `packages_update()` | `packages_update()` | ported | |
| `packages_snapshot()` | `packages_snapshot()` | ported | `uv lock` |
| `packages_restore()` | `packages_restore()` | ported | `uv sync --frozen` |
| `renv_enable()` | none | r-only | renv. The Python equivalent is the uv lock file |
| `renv_disable()` | none | r-only | Same reason |
| `renv_enabled()` | none | r-only | Same reason |

#### R session and tooling

| R | Python | Status | Note |
|---|--------|--------|------|
| `capture_output()` | `capture_output()` | ported | A context manager |
| `env_summary()` | none | r-only | Summarizes the R global environment, which has no Python counterpart |
| `env_clear()` | none | r-only | Same reason |
| `view()` | none | omitted | R opens an HTML data viewer. Jupyter, VS Code, and Positron already show DataFrames |
| `gui()` | none | r-only | Removed in R 2.0 |
| `docs_export()` | none | r-only | Exports Rd help pages for the R docs site |

The table has one row for each of the 122 exports. 86 are ported, 4 renamed,
and 8 folded. 10 are R-only and 3 deprecated. 11 are omitted, including the
three dot-prefixed helpers.

#### Python-only additions

| Python | Purpose |
|--------|---------|
| `python -m framework create --from-layers FILE --path DIR [--interactive]` | Command-line entry point for `fw new --from` and the GitHub Action |
| `python -m framework create --name NAME --path DIR [--type T] [--interactive]` | Command-line entry point for `fw new`: runs `new()`, so the user's defaults apply |
| `python -m framework hook pre-commit` | Runs the pre-commit checks for the shared hook |
| `python -m framework bootstrap [--path DIR] [--interactive] [--no-packages]` | Command-line entry point for `fw bootstrap` |

## Decisions

Decided by Erik on 2026-10-30:

1. The distribution name is `framework-py`, imported as `framework`.
2. The spec has a single `language` field (`r`, `python`, or `stata`), one
   per project.
3. Python dependencies live in `pyproject.toml` and are managed with uv.
   `settings/packages.yml` stays the R package list. The goal is for Python
   projects to follow Python conventions.
4. The seven `git_*` wrappers and `view()` are left out.
5. The global config folder: each tool keeps its own folder, and
   `config_sync()` copies settings across (see "Global config folder").
6. The shared pre-commit hook, agreed with framework-d4.
7. Projects do not mix languages. There is no mixed R and Python project.

## Phases

1. **Foundation** (done 2026-10-30). pyproject, CI, shared-assets copy and
   check, parity check, `config_dir()`, settings, `.env`, `framework.db`,
   hashing, and the data catalog.
2. **Data** (done 2026-10-30). `data_*`, cache, outputs, scratch,
   `scaffold()`, `status()`, and cross-language tests in both directions:
   committed fixtures for files and rows R wrote, and a local test (skipped
   without R) in which R reads what Python wrote.
3. **Specs and creation** (done 2026-10-30, except `new(key)`, which needs
   the cloud). `spec_resolve()`, `spec_check()`, `project_create()`,
   `project_create_from_layers()`, `new()`, the `create` command, AI files and
   guards, Quarto generation, and nine golden trees in `tests/golden`. The
   shared merge passes R's conformance cases, and R's `settings_read()` reads
   every Python project type to the same values (local test, skipped
   without R).
4. **Connections, storage, and publishing** (done 2026-10-30, except
   publishing to Framework Cloud, which comes with phase 5). SQLite and DuckDB
   are tested directly; PostgreSQL, MySQL, and SQL Server are tested against
   fake drivers. S3 is tested against a fake client. `publish*()` refuses
   anything inside a `private` folder of the project.
5. **Cloud** (done 2026-10-30). Server and token resolution, login and
   logout, status, settings sync, the ledger (`ledger_push()`,
   `ledger_list()`, `data_verify()`, and live mode on `data_save()`),
   publishing to Framework Cloud, `new(key=...)` through
   `GET /api/v2/projects/pull`, and registering new projects when logged in.
   Tested against a fake server; the tests never touch the network or the
   real keyring. Windows keyring entries use R's target and UTF-8 blobs, but
   that code has not run on Windows yet.
6. **Git hooks, security audit, notebooks, and packages** (done
   2026-10-30). The shared pre-commit hook and `python -m framework hook
   pre-commit`, `git_security_audit()`, `bootstrap()` and `python -m
   framework bootstrap` on the contract agreed with R and fw, `make_*()` and
   `stubs_*()`, `package_add()` and `packages_*()` through uv,
   `configure_global()`, the settings catalog, user templates, and
   `ai_skills_update()`. All R exports are now handled
   (`check_parity.py --strict` passes).
7. **First release** under the approved name.
