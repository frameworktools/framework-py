# Parity proposals for the R package

framework-py changes that the R package should mirror after CRAN accepts 2.0.0. Each entry
gives the R signature or settings, the behavior both languages share, what Python does,
and what R does today or needs to change. Names, arguments, and semantics are meant to be
identical; where a language must differ, the entry says so.

The items come from porting real R projects to Python (FINDINGS.md in framework-ports,
item numbers in brackets). They are in two parts: data and settings, then outputs,
publishing, and security.

The security audit's summary gains three checks, in this order after the five R has:
`published_outputs` [B3], `connection_secrets` [D9], and `identifier_columns` [D8].

## Data and settings

### data_read(): readr's missing values and whitespace in CSV and TSV files [B4]

```r
data_read(path, delim = NULL, keep_attributes = FALSE, ...)
```

Shared behavior for `csv` and `tsv` files:

- Only `""` and `"NA"` are missing. `na =` replaces the list.
- Leading and trailing whitespace is trimmed from values and column names
  (`trim_ws = TRUE`). A value that is a missing-value string after trimming is missing,
  and a column whose values are all numbers after trimming is numeric.
- `trim_ws = FALSE` keeps the whitespace.

Python: `data_read(path, delim=None, keep_attributes=False, *, na=None, trim_ws=None,
**kwargs)`. `na` takes a string or a list. pandas' own `na_values` and `keep_default_na`
are used as given when passed, and passing both `na` and `na_values` is an error. Columns
typed with pandas' `dtype=` are trimmed but not retyped; `converters=` columns are left
alone. `na` and `trim_ws` on a format other than CSV, TSV, or Excel are an error.

R today: `readr::read_delim()` already reads only `""` and `"NA"` as missing, but its
`trim_ws` default is FALSE (readr 2.2.0; `read_csv()` defaults to TRUE, `read_delim()`
does not). So R keeps `"  hi "` and reads `"    1"` as text.

R change: pass `trim_ws = TRUE` to `read_delim()` for `csv` and `tsv` unless the caller
gives `trim_ws` in `...`. This changes what R returns for padded files; mention it in
NEWS.

### data_read(): R's reader argument names, and readxl's rules for Excel [B5]

R needs no change: its `data_read()` passes `...` to `readr::read_delim()` and
`readxl::read_excel()`, so these names already work there. Python takes the same names as
keyword-only arguments and translates them to pandas, so a call copied from R reads the
same data:

| R name | Applies to | Python behavior |
|--------|------------|-----------------|
| `sheet` | Excel | Name, or position counting from 1 (pandas counts from 0) |
| `col_names` | CSV, TSV, Excel | `TRUE`/`FALSE`/names. `FALSE` names columns `X1, X2, ...` (readr) or `...1, ...2, ...` (readxl) |
| `col_types` | CSV, TSV | readr's compact string (`"cid_"`) or a named mapping of one-letter codes |
| `col_types` | Excel | readxl's types (`"guess"`, `"text"`, `"numeric"`, `"logical"`, `"date"`, `"list"`, `"skip"`), one or one per column |
| `skip` | CSV, TSV, Excel | Rows skipped before the names |
| `n_max` | CSV, TSV, Excel | Most data rows read |
| `na` | CSV, TSV, Excel | Missing-value strings. Excel default: `""` only (readxl) |
| `trim_ws` | CSV, TSV, Excel | Trim whitespace. Default TRUE (readxl's default) |

Shared Excel rules (readxl's defaults): only empty cells are missing, so the text `"NA"`
is text; text cells are trimmed. Python reads readr's `cols()` objects as a mapping of
names to codes; R's `cols()` specification objects have no Python form beyond that.

Known differences that remain: readxl gives a column that mixes numbers and text as text
(`"4"`), while pandas keeps the mixed values; readxl reads an Excel time-only cell as a
date-time on 1899-12-31, pandas as a `datetime.time`; readr's `n` parses numbers with
grouping marks (`"1,000"`), Python's `n` does not.

### data_read() and data_info(): a missing file is named from the project root [B7]

Shared behavior: a relative path is looked for from the project root, then from the
working directory. `data_info()` returns the path under the root unless the file exists
only under the working directory, so a missing file resolves under the root. The error
names the path from the root and the absolute path looked for:

```text
File not found: outputs/tables/incidence.csv
Looked for: /home/me/project/outputs/tables/incidence.csv
```

For a catalog name: `File not found for 'inputs.raw.survey': inputs/raw/survey.csv`, then
the same `Looked for:` line.

R today: the message is `File not found: <spec$path>`, with whatever path `data_info()`
built, which may be absolute or relative to the working directory.

### data_save(): CSV in readr's form [B8]

R needs no change: it writes CSV with `readr::write_delim()`. Python now writes the same
bytes for the same data (checked against readr 2.2.0): doubles in readr's shortest form
(`1261940`, `0.5`, `1e15`, `15e14`, `1.5e-4`, `Inf`), `TRUE`/`FALSE`, `NA` for every
missing value (and for NaN), date-times in UTC as `2020-01-02T03:04:05Z`, dates as
`2020-01-02`, quotes only where needed, and `\n` line endings.

One deliberate difference: readr drops fractional seconds from date-times; Python keeps
them (`03:04:05.5Z`), which readr reads back. R could keep them too.

The writer is `framework._csv.write_csv(frame, path, sep=",")` in framework-py.
`save_table()` uses it too, so its CSVs match R's `save_table()` byte for byte (pandas
options passed to `save_table()` apply on top of the same formatting).

### data_save(): saving over locked data [B6]

```r
data_save(data, path, type = NULL, delimiter = "comma", locked = TRUE, force = FALSE)
```

The default is already the same in both languages: `locked = TRUE`. (The porting report
said R defaults to unlocked; it does not. R's legacy `ai_context.R` template still
documents `data_save(data, path, locked = FALSE)`, which is where that impression comes
from; that text should be fixed.)

Shared behavior, new in both: `data_save()` over a name or path whose framework.db record
is locked still writes the file and records the new hash, as R does today, but when the
new file's hash differs from the recorded one it warns:

```text
data_save() replaced locked data 'inputs.intermediate.cohort' with different contents.
Previous hash: 71da0ff755e5
Current hash:  ddaa40dcf34b
Pass locked=False for data a pipeline rebuilds.
```

Saving identical contents is silent, so rerunning a deterministic pipeline does not warn.
Python raises it as `DataChangedWarning`; R should use `warning()`.

R today: the second save overwrites silently.

### data_read(): `.rds` data frames in Python [D5]

Python only; R needs no change. `data_read()` reads an `.rds` file through the optional
`rds` extra (`framework-py[rds]`, pyreadr) when it holds a data frame, tibble, vector, or
matrix, with the same catalog integrity checks as other formats (hash recorded on first
read, `IntegrityError` for a changed locked file). Without the extra, the error names
`pip install "framework-py[rds]"`. An `.rds` holding anything else (a model, a list)
raises `UnsupportedFormatError`. Python still does not write `.rds`.

### data_info(path, columns = FALSE): a column summary that never shows values [D11]

```r
data_info(path, columns = FALSE)
```

With `columns = TRUE`, the file is read and summarized:

- `columns`: a data frame with one row per column and the columns `name`, `type`, and
  `missing` (the count of missing values).
- `rows`: the number of rows.

`type` is one of `logical`, `integer`, `double`, `character`, `factor`, `date`,
`datetime`, `time`, `duration`, or `other`. These are R's words; R maps its classes as:
logical, integer, double (numeric), character, factor (also ordered), `Date` -> date,
`POSIXct`/`POSIXlt` -> datetime, `hms` -> time, `difftime` -> duration, anything else ->
other. Python maps pandas dtypes to the same words, and inspects object columns' Python
types.

Never shown: values, factor levels, ranges, or examples. The read does not check the
hash and records nothing in framework.db; a missing file is an error. The R return value
is the existing spec list with `columns` and `rows` added; Python's is the `DataInfo`
with `columns` and `rows` fields.

The `framework-sensitive-data` skill (shared asset, R side) should tell agents to use
`data_info(name, columns = TRUE)` instead of reading restricted files to learn their
layout.

### path(...): a path from the project root, like here::here() [D7]

```r
path(...)
```

Shared behavior:

- Joins its arguments under the project root and returns an absolute path. Arguments may
  hold slashes (`path("outputs/tables", "x.csv")`). With no arguments, the root.
- The same call gives the same path from any folder in the project, so existence checks
  copied from R work in Python, which never changes the working directory.
- The file need not exist.
- An absolute argument is an error (`path() parts are relative to the project root`).
- Outside a project, the "no Framework project found" error.

Python: `fw.path(*parts) -> pathlib.Path`. R: returns a character string, like
`here::here()`.

R naming note: `path()` masks `fs::path()` when both packages are attached. The name was
chosen to match Python's `fw.path()`; R users who attach fs will see the masking message.

### settings_read(): warn about keys outside the environment sections [B13]

Shared behavior: when a settings file (or split file) has environment sections, every
top-level key that is not an environment is reported once per session (per file and set
of keys):

```text
Settings file '<path>' has keys outside its environment sections, which are ignored:
ehr_source, person. Move them under default: to use them.
```

A top-level key is an environment, and not reported, when it is one of `default`,
`production`, `development`, `test`, `testing`, `staging`, or the active environment, or
when its value is a mapping that has `inherits` or shares at least one key with
`default`. Keys are listed in file order. Flat files (no environment sections) are never
reported. Python: `SettingsWarning`; R: `warning()`, with a session-level memo
(for example in `.framework_env`) so it shows once.

R today: such keys are silently invisible to `settings()` and `config()`.

### package_add() and package_set_add(): refresh AGENTS.md's package list [B12]

Shared behavior: after a package is added, the canonical AI context file
(`ai.canonical_file` in the settings, else `AGENTS.md`, else `CLAUDE.md`) has its
`## Installed Packages <!-- @framework:regenerate -->` section rewritten from the
project's package list; R from its package settings, as `.generate_packages_section()`
does, Python from `pyproject.toml`'s `[project] dependencies`. Only that section changes,
from its heading up to the next `## ` heading or the end of the file. A file without the
marked heading is left alone, so removing the marker opts out. A file that cannot be read
or written is skipped quietly; the package is still added. A message names the file when
it changed (`Updated the package list in AGENTS.md`).

R today: `ai_regenerate_context(sections = "packages")` does this by hand, and is
deprecated in 2.0; `package_add()` does not call it.

### project_sensitive: hooks on by default, installed with the repository [B14]

Shared behavior: a `project_sensitive` project's package defaults set

```yaml
git:
  hooks:
    data_security: true
    check_sensitive_dirs: true
```

(a layer above can still turn either off). When creation initializes the project's own
repository, the order is: `git init`, check `.env` is ignored, stage, make the initial
commit, then install the pre-commit hook when any check is enabled. Installing after the
initial commit means the first commit, which holds only generated files, does not need a
runtime with Framework yet. A project inside another repository gets no hook, as today.

R today: R 2.0 sets `data_security: false` for every type, including `project_sensitive`
(`inst/templates/settings.project.yml`, `inst/settings/global-settings-default.yml`, and
the `create/` conformance specs). The porting report's "R turns two on" came from an R
project made by an older version, so this is a new default for R too. R's conformance
fixtures `create/*/project_sensitive-*.json` carry `data_security: false` in their input
spec and need regenerating once R changes.

Depends on: the `check_sensitive_dirs` false positive on Framework's own
`.claude/skills/framework-sensitive-data` folder (B3, in progress separately). Until that
lands, every commit in a new sensitive project warns about that folder, and asks at a
terminal.

### cache_remember(depends_on = NULL), cache(depends_on = NULL) [D4]

```r
cache_remember(name, expr, file = NULL, expire_after = NULL, refresh = FALSE,
               expire = NULL, depends_on = NULL)
cache(name, value, file = NULL, expire_after = NULL, depends_on = NULL)
```

`depends_on` is one item or a list of items. Each item becomes a dependency with a
`kind`, a `ref`, and a `hash` (lowercase hex SHA-256):

| Item | kind | ref | hash |
|------|------|-----|------|
| A string naming an existing folder (from the project root, then the working directory) | `folder` | the string as given | see below |
| A string naming an existing file (same lookup) | `file` | the string as given | the file's SHA-256, as data integrity computes it |
| A string without `/` that is a catalog name | `data` | the name | SHA-256 of the catalog file |
| Anything that is not a string | `value` | `value <n>`, its 1-based position in the list | a hash of the value (language-local) |

Paths are checked before catalog names. A string that resolves to none of these is an
error: `depends_on: '<s>' is not a catalog name, file, or folder. ...`. A catalog name
whose file is missing is an error. To hash a string value, wrap it (`list(rule = ">= 0")`
in R, `{"rule": ">= 0"}` in Python). In R, a character vector of length > 1 is several
references.

Folder hash: SHA-256 of the concatenation, sorted bytewise, of one line per file below
the folder, recursively: `<path relative to the folder, / separators>\t<file SHA-256>\n`.
Files and folders whose names start with `.` and folders named `__pycache__` are
skipped.

Entry key: SHA-256 of the concatenation, sorted bytewise, of
`<kind>\t<ref>\t<hash>\n` for every dependency. Order in `depends_on` does not matter.

Storage: framework.db's `meta` table, key `cache_depends:<name>`, value JSON
`{"key": "<entry key>", "dependencies": [{"kind": ..., "ref": ..., "hash": ...}, ...]}`.
No schema change. `cache()` and a recomputing `cache_remember()` write it;
`cache()` without `depends_on` deletes it; `cache_forget()` deletes it, and
`cache_flush()` deletes every `cache_depends:%` row.

Lookup:

1. `depends_on` given: hash the dependencies before evaluating `expr`. When the entry
   has no record or its key differs, it is a miss: message
   `Cache '<name>' is out of date: <refs that differ> changed`, then compute and store
   with the new dependencies.
2. `depends_on` not given, and the entry has a record (also `cache_get()`): hash its
   `data`, `file`, and `folder` dependencies again (values keep their stored hash). If
   any differs, or no longer resolves, warn
   `Cache '<name>' is older than its dependencies: <refs> changed since it was stored, so
   it is treated as a miss. Pass depends_on= to cache_remember() to recompute it without
   this warning.` and treat it as a miss. `cache_remember()` then stores the recomputed
   value with the re-hashed dependencies, so tracking continues.

Expiry, `refresh`, and the file hash check work as before and come first.

### load_model(name, public = NULL), and save_model(public = TRUE) warns about data rows [D6]

```r
load_model(name, public = NULL)
```

Reads a model `save_model()` saved: `<name>.rds` in R (`readRDS()`), `<name>.pkl` in
Python. `public = NULL` looks in the private models folder, then the public one (they are
the same folder in projects without the split); `TRUE`/`FALSE` picks one. Errors: no such
model (`No saved model named '<name>'. Looked for: <paths>`), or only the other
language's file (Python: `... is a model R saved (.rds), which Python cannot read.`; R
should say the same of a `.pkl`). When the file's hash differs from the `results` record
`save_model()` wrote, it loads with a warning (`<file> changed since save_model() recorded
it.`). Python's version loads inside a function, so a statsmodels formula result loads at
the top level of a script.

`save_model(model, name, public = TRUE, ...)`: when the object holds data rows, warn
before saving:

```text
save_model(public=True): '<name>' holds <n> data rows, which would be saved to the public
models folder with it. <hint>
```

Detection in R: the largest `nrow()` among data frame components (`lm$model`,
`glm$data`, `lme4`'s `frame`, any list element that is a data frame), or `length(y)` when
the model keeps `y`; `x = TRUE` and `qr` also hold the design matrix. Hint: refit with
`model = FALSE` (and without `y`/`x`), or save the estimates with `save_table()`. Python
checks a statsmodels result's estimation data, the object itself when it is a DataFrame,
and DataFrame attributes; a statsmodels formula result keeps its data frame even after
`remove_data()`, and the hint says so. No warning for `public = FALSE`.

When the session has also read private data in a sensitive project (B2, under Outputs and
security), the two causes share this one warning: it ends with
`This session also read private data (<names>).`, and B2's separate warning is not given.

### Database helpers: db_execute_file(), db_copy_from(), and read_only per call [D10]

```r
db_connect(name, read_only = NULL)
db_query(query, connection_name, ..., read_only = NULL)
db_execute_file(conn, path, transaction = TRUE)
db_copy_from(conn, table, csv, delim = "comma", na = c("", "NA"))
```

#### read_only

`NULL` uses the connection's `read_only` setting. `TRUE`/`FALSE` override it for this
call on sqlite and duckdb connections (R: `RSQLite::SQLITE_RO` flags and
`duckdb(dbdir, read_only = )`). For server drivers (postgres, mysql/mariadb, sqlserver)
`read_only = TRUE` is an error (`read_only=True applies to sqlite and duckdb files, and
'<name>' is <driver>. Connect as a database user without write access instead.`) and
`FALSE` changes nothing. A connection opened with `read_only = FALSE` is writable for
`db_execute_file()`, `db_copy_from()`, and `db_transaction()`. `db_execute()` keeps its
signature: it opens by name, so a build step that writes to a `read_only: true` database
uses `db_connect(name, read_only = FALSE)` and the helpers that take a connection.

#### db_execute_file(conn, path, transaction = TRUE)

- `conn`: an open connection, or a connection name (opened and closed around the call).
- `path`: a relative path is looked for from the project root, then the working
  directory. Read as UTF-8; a byte-order mark is dropped.
- Splitting: statements end at `;` outside single quotes (`''` escapes), double quotes,
  backticks, `--` and `/* */` comments, and PostgreSQL dollar quotes (`$$` / `$tag$`);
  MySQL also has `#` comments and backslash escapes. SQLite files are split with
  SQLite's `sqlite3_complete()` rule (Python's `sqlite3.complete_statement()`): `;`-ended
  pieces accumulate until they form a complete statement, so `CREATE TRIGGER ... BEGIN
  ... END;` stays one statement. R needs the same rule. SQL Server splits into batches
  at lines holding only `GO` (case-insensitive, optional trailing `--` comment); each
  batch runs whole. Statements holding only comments are dropped. MySQL `DELIMITER` is
  not supported.
- `transaction = TRUE`: all statements in one `db_transaction()`. On failure, roll back
  and stop: `Statement <n> of <total> in <file name> (line <line of its first word>)
  failed: <driver message> The file was rolled back.` `transaction = FALSE`: commit after
  each statement; the same message without the last sentence.
- Refuses a read-only connection (`Connection '<name>' is read-only. db_execute_file()
  needs a writable connection: db_connect('<name>', read_only=False).`).
- Returns the number of statements (batches for SQL Server) run.

#### db_copy_from(conn, table, csv, delim = "comma", na = c("", "NA"))

- `conn` and `csv` resolve as for `db_execute_file()`.
- `table`: an existing table, optionally `schema.table`; each part is quoted for the
  driver (`"x"`, `` `x` ``, `[x]`), so names keep their case.
- The CSV's header names the target columns; other columns get their defaults. Values
  are sent as text (or NULL for values in `na`) and the database casts them. No
  whitespace trimming.
- Loading: postgres `COPY <table> (<cols>) FROM STDIN` (R: `RPostgres::dbWriteTable`
  with `copy = TRUE` into the existing table, or `postgresqlCopyInDataframe`); duckdb
  inserts each parsed chunk with one `INSERT ... SELECT` (R: `duckdb_register()` or
  `dbAppendTable()`); sqlserver `executemany` with `fast_executemany` (R:
  `dbAppendTable()` with odbc's batching); sqlite and mysql `executemany` (R:
  `dbAppendTable()`). Chunks of 50,000 rows.
- One transaction for the whole file; on failure nothing is loaded:
  `Failed to load <file name> into <table>; nothing was loaded: <driver message>`.
- Refuses a read-only connection, as above. Returns the number of rows loaded.

Not done: `db_cli_args(name)` (the a2cps report's third suggestion, credentials for
`psql`) is out of scope here.

## Outputs, publishing, and security

### save_figure(), save_table(), save_model(): sizes, the caller's figure, and subfolders [B9]

Rule:

1. A `name` with `/` names subfolders of the output folder. They are created, as the
   output folder itself is. `save_table(df, "meeting-2026-10-16/cohort")` writes
   `outputs/tables/meeting-2026-10-16/cohort.csv`; the results row is keyed by that path.
2. `width` and `height` (defaults 8 and 6 inches, unchanged) set the size of the saved
   file for every kind of plot the language accepts, including multi-panel compositions.
3. Saving never changes the object passed in. A figure that has to be resized to save it
   is put back at its own size afterwards.

Python: `save_figure()` restores a matplotlib figure's size after `savefig()`. A plotnine
composition (`plotnine.composition.Compose`, detected by class name so plotnine is never
imported for other plots) is sized with `plot & theme(figure_size=(width, height))`, which
copies it, then drawn and saved, because `Compose.save()` ignores `width` and `height`.

R: `ggsave()` already sizes patchwork compositions and doesn't touch the plot, so only rule
1 needs work: create `dirname(file_path)` with `dir.create(recursive = TRUE)` in
`save_figure()`, `save_table()`, and `save_model()` before writing.

### save_figure(format = c(...)): several formats, and eps and emf [D14]

Rule:

1. `format` accepts one format or a vector/list of them. Each format writes
   `<name>.<format>` and gets its own results row. The return value is the path for a
   single format (a string in Python, a length-1 character vector in R) and the paths in
   the given order for several. Duplicates are dropped, keeping the first.
2. Allowed formats: `png`, `pdf`, `svg`, `eps`, `jpg`, `jpeg`, `tiff`, `emf`.
3. Every format, and every existing file when `overwrite` is false, is checked before any
   file is written, so a failed call writes nothing.
4. `emf` is written when the language has a device for it, and otherwise is an error that
   says to save svg or pdf and convert.

Python: `format: str | list[str] | tuple[str, ...]`, typed with overloads (`Path` for a
string, `list[Path]` for a list). eps comes from matplotlib's PostScript backend. Python
raises `UnsupportedFormatError` for emf unless a matplotlib backend is registered for it,
which none is today.

R: `checkmate::assert_subset(format, c(..., "eps", "emf"))`, then loop. eps through
`ggsave(device = grDevices::cairo_ps)` or `"eps"`, and for recorded plots
`grDevices::postscript(onefile = FALSE, horizontal = FALSE, paper = "special")`. emf
through `devEMF::emf` when installed (a Suggests), else the same error as Python.

### publish_data(): the message shows the normalized slug [B10]

Rule: the "Publishing <dest> as <slug>" message shows the final slug, after the same
normalization every slug gets (`artifact_slug()`: lowercase, each run of characters other
than `a-z` and `0-9` becomes `-`, leading and trailing `-` removed).
`publish_data(df, "tables/bp_by_visit.csv")` says `as tables-bp-by-visit`, not
`tables-bp_by_visit`.

Python: `_data_slug()` returns the normalized slug, so the message, the folder, and the
manifest agree.

R: normalize in R's `publish_data()` slug helper before the message (`.artifact_slug()` or
its equivalent), and pass the normalized slug on.

### publish_dir(): the slug keeps the folder part of dest [B11]

Rule: for folder, GitHub, and Cloud destinations, `publish_dir()`'s slug is `dest` (default:
the folder's name) with each `/` turned into `-`, then normalized like every slug. Unlike
`publish_data()`, no extension is removed, since `dest` names a folder: `"reports/v1.2"`
becomes `reports-v1-2`. `publish_dir(dir, dest = "figures/meeting")` publishes as
`figures-meeting`, not `meeting`. It reports `Publishing <dest> as <slug>`, as
`publish_data()` does. Plain bucket uploads are unchanged: files go under `dest`.

R: `publish_dir()` in `R/publish.R` uses `basename(sub("/+$", "", dest))` (and
`basename(dest)` for the bundle), so R drops the folder too. Replace both with the same
`/`-to-`-` join and normalization, and add the message.

### make_notebook(title = NULL): titles and acronyms [B19]

The default title rule stays as it is in both languages (`.notebook_title()` in R,
`notebook_title()` in Python):

1. A name with whitespace or any capital letter is already a title: it is kept exactly as
   typed, trimmed. `"BP for LDL and HbA1c"` stays `"BP for LDL and HbA1c"`, and
   `"BP-summary"` stays `"BP-summary"`.
2. Otherwise each run of `-` and `_` becomes one space and the first character is
   uppercased. Nothing is ever lowercased.

So every token typed in capitals already survives, which is the rule FINDINGS asked for.
The ports' problem was the input: a file-style name such as `"bp-for-ldl-and-hba1c"` has
no case information, so no rule can tell `ldl` from `and`. A list of known acronyms was
considered and rejected: it can never be complete for a field, it would need to be kept
identical in two languages, and a wrong guess ("Cat" -> "CAT") is worse than none.
Rule 1 also isn't changed to split hyphenated names with capitals (`"BP-for-LDL"` ->
`"BP for LDL"`), because that breaks names such as `"COVID-19-trends"`.

New API, the only way to fix acronyms in a lowercase name:

- `make_notebook(..., title = NULL)` and the wrappers `make_qmd()`, `make_rmd()` (R),
  `make_revealjs()`, `make_presentation()`, and `make_script()` take `title`. When given,
  it fills `{{ title }}` in the stub exactly as typed (the Jupyter stub's heading too); the
  file name still comes from `name`.

Python: keyword-only `title: str | None = None` on `make_notebook()`, `make_qmd()`,
`make_revealjs()`, `make_presentation()`, and `make_script()`.

R: add `title = NULL` after `subdir` (`make_script()`: after `overwrite`), and use
`title %||% .notebook_title(original_name)` where `make_notebook()` fills the stub,
including the `.ipynb` heading cell.

### publish(), publish_notebook(), publish_data(): confirming notebooks and objects that can show private data [B1]

Today the publish guard checks only file paths: a path containing `private` (any case)
is listed, a terminal session is asked `[y/N]`, and a session with no terminal fails unless
`include_private = TRUE`. Rendered notebooks and in-memory objects have no telling path, so
they published silently, even from `security: sensitive: true` projects.

Rule: the same confirmation (one warning per reason, one question, the same error without
a terminal, skipped with `include_private = TRUE`) also applies when:

1. **Sensitive project, notebook.** The settings have `security: sensitive: true` and the
   source is a `.qmd`, `.Rmd`, or `.ipynb` file, published with `publish_notebook()` or
   `publish()` (every destination: bucket, folder, GitHub, S3, Cloud).
2. **Sensitive project, object.** The settings have `security: sensitive: true` and the
   source is an object: `publish()` of a data frame, model, figure, or anything else that
   isn't a path, and `publish_data()` of a data frame. Files given by path keep the path
   rule only.
3. **Any project, notebook naming private data.** The notebook's code has a quoted string
   (single or double quotes, on one line, up to 500 characters) that either
   - has a part, split on `/`, `\`, or `.`, with no whitespace, whose name contains
     `private`, `confidential`, or `sensitive` (case-insensitive):
     `"inputs/private/raw/ids.csv"`, `"inputs.private.raw.ids"`; or
   - is a data catalog name (`a.b`, letters, digits, `_`, `-`) whose entry is private
     (in a private folder, or `private: true`), as `data_info()` reports it.

   For `.ipynb`, only code cells are read. For `.qmd`/`.Rmd`, the whole file is read
   (chunk options can name data; prose rarely quotes a path). Strings are reported in order
   of appearance, each once.

Messages (Python wording; R should match in substance):

```text
Warning: This project holds sensitive data (security: sensitive: true), and the rendered notebook 05-review.qmd can show it. Check that it holds only results that may be shared.
Warning: 05-review.qmd reads private data: inputs.private.release, outputs/private/x.csv.
Error: Not uploading this without confirmation. Review it, then pass include_private=True to upload it.
```

For an object, the first reason names its class: `this DataFrame`. When private paths are
also flagged, the error keeps saying "files with 'private' in their path".

Python: `_private.confirm_private_upload(paths, include_private, reasons)` takes the extra
reasons; `sensitive_reasons()`, `notebook_private_reads()`, and `notebook_reasons()` build
them. `publish()` checks notebooks and objects, `publish_notebook()`'s bucket and Cloud
path checks notebooks, and `publish_data()` checks data frames.

R: R's `publish()`, `publish_notebook()`, and `publish_data()` need the same three checks
in `.fw_confirm_private()` (`R/publish.R`), which should take the extra reasons, with
`security$sensitive` read from the settings.

### save_table(), save_figure(), save_model(public = TRUE): warn after the session read private data [B2]

Nothing tracks provenance, so knowing that a particular table came from private data
isn't cheap. The rule uses the session instead:

1. `data_read()` marks the session as having read private data when the file it read is
   private: its catalog entry is private (`data_info()`'s `private`: a private folder or
   `private: true`), or, for a file path, a folder between the project root and the file
   is named like private, confidential, or sensitive (the same segment rule as the audit),
   or, for a file outside the project, `private` appears anywhere in its full path. It
   remembers the name as the caller gave it (catalog name or path), once each, in order.
2. In a project with `security: sensitive: true`, `save_table()`, `save_figure()`, and
   `save_model()` with `public = TRUE` warn after such a read, once per saved file, after
   saving and recording it:

   ```text
   Saved summary.csv as public, and this session read private data (inputs.private.raw.visits, inputs/private/raw/ids.csv, inputs.flagged, and 2 more). Check that it holds only aggregated or de-identified results before sharing it.
   ```

   At most three names are shown. Private saves, and saves in other projects, never warn.
   When `save_model()` already warns that a public model holds data rows [D6], that
   warning carries the names instead (`This session also read private data (...)`), so
   one save never gives two warnings.
3. The mark lasts for the session (the Python process; the R session). There is no public
   function to clear it.

Not covered: `save_report()` and `save_notebook()` (a notebook renders in its own process,
which the session can't see; B1 checks notebooks at publish time), and data reached other
than through `data_read()` (cache, database queries).

Python: `_private.note_private_read()`, `warn_public_save()`; the warning is a
`FrameworkWarning`.

R: keep the names in `.framework_env$private_reads` from `data_read()`, and warn with
`cli::cli_warn()` from `save_table()`, `save_figure()`, and `save_model()`.

### git_security_audit() and the hook: duplicates, Framework's skills, publishing folders, outside folders [B3]

Rules:

1. **Each file once.** `gitignore_coverage` and `private_data_exposure` visit private
   folders deepest first (most path segments; settings order among equals) and report each
   file, and each unignored folder, once, under the deepest folder's key. R's fallback keys
   (`outputs_tables` -> `outputs/private/tables`) alias folders, which listed one staged file
   three times.
2. **Framework's own skills.** The `check_sensitive_dirs` hook skips any folder named
   `framework-*` whose parent folder is named `skills`
   (`.claude/skills/framework-sensitive-data`, `.agents/skills/...`), doesn't descend into
   it, and skips `.framework/`. New sensitive projects turn `check_sensitive_dirs` on
   [B14], so without this every commit there warned about Framework's own skill.
3. **New check `published_outputs`** (sixth in the summary, after `secrets_exposure`).
   Candidates are `publish.destinations` entries with `driver: folder` whose `path` (relative
   to the project root unless absolute, `~` expanded) is inside the project and isn't the
   root itself. In a project with `security: sensitive: true` every such destination is
   checked; in other projects, one with `visibility: public` or `private: false` is skipped,
   since it's meant to be committed (a GitHub Pages `docs/`). A destination is a finding
   when its folder holds files that git doesn't ignore. Finding columns: `destination`,
   `path`, `files` (count not ignored), `tracked` (count of those git tracks). Status:
   `warning` when there are findings, else `pass`. Recommendation: "Add folder publishing
   destinations to .gitignore (for example /published/), or set visibility: public on a
   destination meant to be committed".
4. **Generated `.gitignore`.** Project creation appends, after the type's template and the
   `.env` rule, a `# Folder publishing destinations` comment and `/<path>/` for each
   destination rule 3 would check, unless the line is already there.
5. **Folders outside the project.** A `directories` entry whose path resolves outside the
   project root is not checked, since git can't commit it from here. The result's metadata
   gets `skipped_directories` (key -> path as configured), and the printed report adds:

   ```text
   --- Skipped: Folders Outside the Project ---
     original_private_tables: /data/other-project/outputs/private/tables
        Git can't commit them from this project, so they weren't checked.
   ```

   A path inside the project given as absolute is checked as the relative path.
6. **Watched folders.** Besides `inputs_*`, `outputs_*`, `cache`, `scratch`, and `logs`,
   the audit watches any `directories` key or path named like private, confidential, or
   sensitive (`original_private_tables`, `review/confidential`).

Python: `security.py` (`_deepest_first()`, `folder_destinations()`, `_check_published()`,
`_outside_directories()`), `hooks._framework_skill()`, `create._ignore_publish_folders()`.

R: `R/security_audit.R` (`.get_data_directories()`, `.identify_private_dirs()`, the
coverage and exposure checks, the report), the hook's sensitive-folder walk, and project
creation's `.gitignore` writer. `CHECKS` order matters for the summary table, so append
`published_outputs` after `secrets_exposure` (and D9's and D8's checks after it).

### git_security_audit(): the connection_secrets check [D9]

Rule: a new audit check, seventh in the summary, after `published_outputs`.

- Files: the settings file the audit read, and every `.yml`/`.yaml` file under the
  project's `settings/` folder (split files). Each is parsed as YAML without evaluating
  anything (`!expr` stays text).
- Scope: everything under a key named `connections`, at any depth (so `default:`, other
  environment blocks, and a split file whose top key is `connections` all count).
- A credential key is one whose name matches, case-insensitively,
  `password|passwd|pwd|secret|token|api_?key|access_key`.
- Its value is **written out** unless it is missing, empty, a mapping or list, a boolean,
  `env("VAR")` with no default, or `!expr Sys.getenv("VAR")` with no default.
  `env("VAR", "x")` and `Sys.getenv("VAR", "x")` count as written out, since the default is
  in the file. Numbers count.
- Finding columns: `file` (from the project root), `key` (dot path such as
  `connections.databases.omop.password`; list items as `[i]`), `tracked` (git tracks the
  file). Values are never stored or printed.
- Status: `fail` when any finding's file is tracked by git, else `warning` when there are
  findings (including without git), else `pass`.
- Recommendations: "Move connection passwords and tokens to .env and refer to them with
  env("VARIABLE") in the settings" and "Rotate any secret that was committed: it stays in
  git history".
- Report section "Secrets Written in Connection Settings", one line per finding:
  `settings/connections.yml: connections.databases.omop.password (tracked in git)`.

Python: `security._check_connection_secrets()`. The pre-commit hook's `data_security`
check runs it with the rest of the audit.

R: add `.check_connection_secrets()` to `R/security_audit.R` with the same rule, reading
the files with `yaml::read_yaml(eval.expr = FALSE)`.

### data_save() and git_security_audit(): identifier-like columns [D8]

Column names only, never values; files are read for their header only.

**Name rule.** Split a column name into lowercase tokens at every non-alphanumeric
character and at camelCase boundaries (`patientDOB` -> `patient`, `dob`;
`MedicalRecordNumber` -> `medical`, `record`, `number`). `joined` is the tokens run
together. The first matching kind wins, in this order:

| Kind | Matches |
|------|---------|
| `dob` | token `dob`; or token `birth` with `date`, `dt`, or `day`; or `joined` contains `dateofbirth`, `birthdate`, or `birthday` |
| `zip` | token `zip`, `zipcode`, `zip5`, `zip9`, `postal`, `postcode`, or `postalcode`; or `joined` contains `zipcode` or `postalcode` (`zip3` doesn't match) |
| `ssn` | token `ssn`; or `joined` contains `socialsecurity` |
| `mrn` | token `mrn`; or `joined` contains `medicalrecordnumber`, `medicalrecordno`, or `medrec` |
| `name` | token `name` with `first`, `last`, `given`, `family`, `full`, `middle`, `maiden`, `patient`, or `person`; or `joined` is `firstname`, `lastname`, `surname`, `fullname`, `fname`, or `lname` |

`name` columns count only when the same table has a `dob` column. Results keep column order.
Test vectors (from `tests/test_security.py::test_identifier_names`): `patientDOB`,
`date_of_birth`, `BirthDate`, `birth_dt` are dob; `birth_year`, `zip3`, `site_name`,
`zipper`, `dobutamine` match nothing; `ZIP`, `zip5`, `postal_code` are zip; `SSN` is ssn;
`mrn` and `MedicalRecordNumber` are mrn; `last_name` is name (with a dob present), and
`first_name, last_name, site` alone match nothing.

**`data_save()`.** After writing, when the file's folder (relative to the project root) has
no segment named like private, confidential, or sensitive, and the data frame's columns
match, warn (the save still happens). At most five columns are listed. In a project that
isn't `security: sensitive: true`:

```text
inputs/intermediate/patients.parquet has columns that look like direct identifiers (dob, zip_code), and it isn't in a private folder. If this is real data about people, save it in a private folder, which git ignores, and consider a privacy-sensitive project (type project_sensitive).
```

In a sensitive project the last sentence is "If this is real data about people, save it in
a private folder instead."

**Audit check `identifier_columns`** (eighth in the summary, after `connection_secrets`).
For each file git tracks (including staged files) with extension `.csv`, `.tsv`, `.txt`,
`.parquet`, `.dta`, or `.sav`, outside folders named like private, read the header (CSV:
the first line, delimiter tab if `.tsv` or a tab is present, else `;` if it has more `;`
than `,`, else `,`; Parquet: the schema; Stata/SPSS: metadata only, skipped when the reader
isn't installed). Finding columns: `file`, `columns` (matching columns, `, `-joined),
`kinds` (distinct kinds in order). Status `warning` when there are findings, never `fail`:
a header can't tell real data from synthetic. Report section "Tracked Data With
Identifier-Like Columns", one line `<file> (<columns>)`.

**Pre-commit hook.** When the `data_security` audit passes, the hook prints the findings of
`published_outputs`, `connection_secrets`, and `identifier_columns`, headed
"⚠ Security audit warnings (the commit goes ahead):", and exits 0.

Python: `framework/_identifiers.py` (`identifier_columns()`, `header_columns()`,
`warn_identifiers()`), `security._check_identifiers()`, `security.hook_warnings()`.

R: the same rule in a helper shared by `data_save()` and `R/security_audit.R`; headers via
`readLines(n = 1)`, `arrow::read_schema()` (Suggests), and `haven::read_dta(n_max = 0)`.

### show_table(): tables for Quarto and Jupyter, and plotly under embed-resources [D12]

New function, Python first (listed in `parity.toml` as `python_only` until R exports it):

```text
show_table(data, caption = NULL, digits = NULL, big_mark = "", na = "", index = FALSE)
```

Rule: it returns a Markdown pipe table that the notebook displays as Markdown.

- `data`: a data frame (Python: pandas or polars DataFrame or Series, or a numpy array).
  Every row is shown.
- Header: the column names. Alignment row: `---:` for numeric columns (not logical/bool),
  `:---` for all others.
- Cells, in order of checks:
  1. missing (`NA`, `None`, `NaN`, `NaT`) -> `na`;
  2. logical -> as the language prints it (`TRUE`/`FALSE` in R, `True`/`False` in Python);
  3. integer -> digits with `big_mark` between groups of three;
  4. double: infinite -> `Inf`/`-Inf`; with `digits`, fixed `digits` decimals
     (`1.50`); without, a whole number without decimals (`1`) and anything else as the
     language prints it (Python `str()`, R `format(x, digits = 15)`); `big_mark` groups the
     whole part; a value printed in exponent form is left as is;
  5. anything else -> its string form.
  Then `|` becomes `\|` and runs of whitespace (including newlines) become one space.
- `caption`: after the table, a blank line and `: <caption>` (Pandoc's table caption),
  whitespace collapsed. In Quarto, `#| tbl-cap` with `#| label: tbl-...` does the same and
  adds a cross-reference.
- `index = TRUE` (Python only; R has no index, and R's `row.names` is the equivalent):
  the index becomes the first column.
- Errors: data that isn't table-like; `digits` negative or not a whole number.

Python: `framework/display.py`; returns `TableDisplay`, with `_repr_markdown_()` for
Jupyter and Quarto, and `str()`/`repr()` giving the Markdown. Verified with Quarto 1.10:
the table renders with Bootstrap table classes, its caption, and a working `@tbl-` reference.

R: implement with `knitr::kable(format = "pipe")` and `knitr::asis_output()` (knitr is
already used for Quarto), applying the cell rules above through `format.args` or by
pre-formatting columns, so both languages render the same Markdown for the same data.

plotly: documented only (`docs/site/guides/quarto.md`, "plotly with embed-resources"): with
`embed-resources: true`, write each figure's HTML with plotly.js only once and no MathJax.
R's htmlwidgets have the same issue with `embed-resources`; the R docs could note
`plotly::partial_bundle()` or `selfcontained = FALSE` there.

### Folder destinations with layout: site-root [D13]

New destination option for `driver: folder`:

```yaml
publish:
  destinations:
    pages:
      driver: folder
      path: docs
      url: https://me.github.io/my-talk   # optional
      visibility: public
      layout: site-root                   # default: versioned
```

Rules:

1. `layout` is `versioned` (the default, today's behavior) or `site-root`. Another value is
   an error ("A folder destination's layout must be versioned or site-root."), and `layout`
   on a non-folder destination is an error ("layout applies to folder destinations only.").
   Both are checked before anything is rendered or written.
2. A `site-root` publish builds the same bundle as a versioned one, then:
   1. reads `<path>/.framework-site.json`, and deletes each file in its `files` list
      (paths relative to the folder; anything resolving outside it, or not a file, is
      skipped), then removes folders this left empty, up to the destination folder;
   2. copies every bundle file to the folder, keeping its relative path and replacing a file
      of the same name, except `artifact.json`, and except the bundle's `original` file when
      it is an `.html`/`.htm` file at the bundle's top, isn't `index.html`, and is
      byte-identical to `index.html` (so a deck is written once);
   3. writes `.framework-site.json`:
      `{"schema": "framework.site/v1", "version": <previous + 1, from 1>, "slug", "title",
      "kind", "description", "created_at", "files": [<written, sorted>]}`.

   No `<slug>/`, `v/<n>/`, `index.json`, or site `index.html` is written. Files Framework
   didn't write (`CNAME`, `.nojekyll`) stay.
3. The receipt: `url` is `<url>/` when the destination has one, else none; `location` is
   `<path>/index.html`; `version` is the record's version. The message is the usual
   "Published version N: <location>".
4. `publish_list()` for a site-root destination returns at most one row: key `<slug>/`,
   size (sum of the recorded files that exist), `last_modified` (`created_at`), and `url`
   (`<url>/`, or the `index.html` path).
5. Cloud tracking is unchanged (the manifest and receipt are the same shape).

Interaction with B3: a site-root folder for GitHub Pages should have `visibility: public`,
or new projects ignore it and the audit warns about it (in sensitive projects it's checked
either way).

Python: `publish_bundle.LAYOUTS`, `folder_deliver_root()`, `storage._site_root_list()`.

R: `R/publish_drivers.R`'s folder driver and destination validation, and the folder branch
of `publish_list()`.
