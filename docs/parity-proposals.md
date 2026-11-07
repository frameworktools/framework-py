# Parity proposals for the R package

framework-py changes that the R package should mirror after CRAN accepts 2.0.0. Each entry
gives the R signature, the behavior both languages share, and what R does today. Names,
arguments, and semantics are meant to be identical; where a language must differ, the
entry says so.

The items come from porting real R projects to Python (FINDINGS.md in framework-ports,
item numbers in brackets).

## data_read(): readr's missing values and whitespace in CSV and TSV files [B4]

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

## data_read(): R's reader argument names, and readxl's rules for Excel [B5]

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

## data_read() and data_info(): a missing file is named from the project root [B7]

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

## data_save(): CSV in readr's form [B8]

R needs no change: it writes CSV with `readr::write_delim()`. Python now writes the same
bytes for the same data (checked against readr 2.2.0): doubles in readr's shortest form
(`1261940`, `0.5`, `1e15`, `15e14`, `1.5e-4`, `Inf`), `TRUE`/`FALSE`, `NA` for every
missing value (and for NaN), date-times in UTC as `2020-01-02T03:04:05Z`, dates as
`2020-01-02`, quotes only where needed, and `\n` line endings.

One deliberate difference: readr drops fractional seconds from date-times; Python keeps
them (`03:04:05.5Z`), which readr reads back. R could keep them too.

The writer is `framework._csv.write_csv(frame, path, sep=",")` in framework-py, for
`save_table()` and any other CSV that should match R.

## data_save(): saving over locked data [B6]

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

## data_read(): `.rds` data frames in Python [D5]

Python only; R needs no change. `data_read()` reads an `.rds` file through the optional
`rds` extra (`framework-py[rds]`, pyreadr) when it holds a data frame, tibble, vector, or
matrix, with the same catalog integrity checks as other formats (hash recorded on first
read, `IntegrityError` for a changed locked file). Without the extra, the error names
`pip install "framework-py[rds]"`. An `.rds` holding anything else (a model, a list)
raises `UnsupportedFormatError`. Python still does not write `.rds`.

## data_info(path, columns = FALSE): a column summary that never shows values [D11]

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

## path(...): a path from the project root, like here::here() [D7]

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

## settings_read(): warn about keys outside the environment sections [B13]

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

## package_add() and package_set_add(): refresh AGENTS.md's package list [B12]

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

## project_sensitive: hooks on by default, installed with the repository [B14]

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

## cache_remember(depends_on = NULL), cache(depends_on = NULL) [D4]

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
