---
id: data-management
title: Data management
description: The data catalog, reading and saving data, integrity hashes, and locked files
section: features
position: 4
---

## Overview

Framework manages data through a catalog. Each data file gets a dot-notation name such as `inputs.raw.survey`, and the catalog records the file's path, type, and integrity settings. Code reads data by name, so the path appears in one place. Framework picks the reader from the file type and checks the file against the hash it recorded earlier.

```python
import framework as fw

fw.scaffold()
survey = fw.data_read("inputs.raw.survey")
```

Data comes back as a pandas DataFrame.

## The catalog

The catalog is the `data` section of the settings, stored in `settings/data.yml` in standard and privacy-sensitive projects. Each part of a name is one level of nesting.

```yaml
data:
  inputs:
    raw:
      survey:
        path: inputs/raw/survey.csv
        type: csv
        delimiter: comma
        locked: true
        description: Baseline survey, wave 1
      sites: inputs/raw/sites.csv
    intermediate:
      survey_clean:
        path: inputs/intermediate/survey_clean.parquet
        type: parquet
        locked: true
```

An entry is either a path or a mapping with these fields.

| Field | Meaning |
|-------|---------|
| `path` | The file, relative to the project root. Required. |
| `type` | The file type. Without it, the type comes from the extension. |
| `delimiter` | For delimited text: `comma`, `tab`, `semicolon`, or `space`. |
| `locked` | When `true`, a changed file raises an error on read. An entry without it is unlocked, so `data_add()` and `data_save()` write `locked: true` for a locked file and leave the field out otherwise, as R does. |
| `description` | Printed when the data is read. |

Other fields are kept and returned by [data_info()](ref:data_info) in its `extra` attribute.

A key may contain dots. `inputs.raw.survey` is found as nested YAML, as a `raw.survey:` key under `inputs:`, or as an `inputs.raw.survey:` key directly under `data:`. Nested YAML is the form Framework writes.

You can write entries by hand, but [data_add()](ref:data_add) and [data_save()](ref:data_save) write them for you.

## Adding existing files

[data_add()](ref:data_add) registers a file that is already in the project. It writes the catalog entry, records the file's hash in `framework.db`, and returns the entry.

```python
fw.data_add("inputs/raw/survey.csv")
```

Without a `name`, a file in a configured inputs folder (any `directories` key that starts with `inputs`) is named after the deepest one, so this call registers `inputs.raw.survey`, and `inputs/private/raw/participants.csv` in a privacy-sensitive project registers `inputs.private.raw.participants`. Elsewhere the name is the file name, and Framework suggests choosing a more descriptive one.

```python
fw.data_add("inputs/raw/sites_2024.csv", name="inputs.raw.sites")
```

Entries added this way are locked by default. Pass `locked=False` for files that are expected to change. The `type` and `delimiter` arguments override what the extension suggests. With `update_config=False`, only `framework.db` is updated.

## Reading data

[data_read()](ref:data_read) takes a catalog name or a file path.

```python
survey = fw.data_read("inputs.raw.survey")
lookup = fw.data_read("inputs/reference/codes.csv")
```

A catalog name is resolved from the project root. A relative path, which is anything containing a slash, is looked for from the project root, then from the current folder. When the file is in neither, the error names its path from the project root, along with the full path looked for. Only files read by catalog name are checked against `framework.db`.

The `delim` argument overrides the delimiter. Other keyword arguments go to the pandas or pyreadstat reader.

```python
fw.data_read("inputs.raw.survey", usecols=["id", "score"])
```

For Stata and SPSS files, `keep_attributes=True` returns value labels as categories. By default the stored codes are returned, as in R.

### Missing values and whitespace

CSV, TSV, and Excel files follow R's rules (readr's and readxl's), so one file gives the same rows in both languages:

- In CSV and TSV files, only empty fields and `NA` are missing. In Excel files, only empty cells are missing, and a cell holding the text `NA` is text. pandas on its own also reads `None`, `N/A`, `null`, `NaN`, and others as missing, which would silently drop rows R keeps.
- Leading and trailing whitespace is trimmed from values and column names. A padded `"   NA"` in a text file is missing, and a padded number is a number.

`na=` replaces the list of missing-value strings, and `trim_ws=False` keeps the whitespace.

```python
fw.data_read("inputs.raw.prams", na=["", "NA", "."])
fw.data_read("inputs.raw.fixed_width_export", trim_ws=False)
```

pandas' own `na_values` and `keep_default_na` still work, and are used as given.

### R's reader arguments

For CSV, TSV, and Excel files, `data_read()` takes the argument names R passes to readr and readxl, so a call copied from R reads the same data:

| Argument | Meaning |
|----------|---------|
| `sheet` | Excel only: the sheet's name, or its position counting from 1 |
| `col_names` | `True` reads names from the first row. `False` reads the first row as data and names the columns as R does (`X1, X2, ...` for text, `...1, ...2, ...` for Excel). A list gives the names |
| `col_types` | Text files: readr's compact string such as `"cid_"`, or a mapping such as `{"zip": "c"}`. Excel: readxl's types, one for all columns or one per column |
| `skip` | Rows to skip before reading anything, including the names |
| `n_max` | The most data rows to read |
| `na`, `trim_ws` | Missing values and whitespace, as above |

```python
fw.data_read("inputs/raw/labs.xlsx", sheet="results", skip=1, col_names=False)
fw.data_read("inputs/raw/zips.csv", col_types={"zip": "c"})
```

readr's codes are `c` text, `i` integer, `d` or `n` double, `l` logical, `f` factor (a pandas category), `D` date, `T` date-time, `t` time (kept as text), `?` guess, and `_` or `-` to skip the column. readxl's types are `"guess"`, `"text"`, `"numeric"`, `"logical"`, `"date"`, `"list"` (guessed), and `"skip"`. Passing an R name and its pandas equivalent together, such as `sheet` and `sheet_name`, is an error.

## Saving data

[data_save()](ref:data_save) writes a DataFrame, records its hash, and returns the DataFrame, so a call can end a pipeline.

```python
clean = survey.dropna()
fw.data_save(clean, "inputs.intermediate.survey_clean")
```

When the target is a catalog name, its leading parts select a folder from `directories` and the remaining parts become the file name. The longest run of leading parts that names a directory key wins. `inputs.intermediate.survey_clean` uses the `inputs_intermediate` folder and writes `inputs/intermediate/survey_clean.parquet`, and `inputs.private.intermediate.patients_clean` uses `inputs_private_intermediate` in a privacy-sensitive project. A name is also added to the catalog, so `data_read()` finds it under the same name. A one-word key works too: in a course, `data.week1_visits` uses the `data` folder.

Names are saved as Parquet by default. Pass `type="csv"` for CSV, with `delimiter` to choose the separator. A name may also end in the extension: `inputs.final.analysis.csv` is the name `inputs.final.analysis` saved as CSV.

```python
fw.data_save(clean, "inputs.final.analysis", type="csv")
```

CSV files are written as R's readr writes them: whole numbers without `.0`, `TRUE` and `FALSE` for logicals, `NA` for missing values, date-times in UTC as `2020-01-02T03:04:05Z`, and `\n` line endings. The same data saved from R and from Python gives the same file, and the same hash.

When the target is a path with a `/`, ending in `.csv` or `.parquet`, the file is written there and its hash is recorded under its path from the project root, however the path was given. A relative path starts at the project root, wherever Python runs, so a notebook in `notebooks/` writes to the same place as a script in the root. [data_read()](ref:data_read) and [data_add()](ref:data_add) look for a relative path from the project root first, then from the current folder. A path does not create a catalog entry. Register it afterward with [data_add()](ref:data_add) to read it by name.

```python
fw.data_save(clean, "exports/patients_clean.parquet")
fw.data_add("exports/patients_clean.parquet", name="exports.patients_clean")
```

Saved files are locked by default, as in R, so a later read raises `IntegrityError` if anything else changes them. Saving again replaces the file, as R does. When the new contents differ from locked data, `data_save()` issues a `DataChangedWarning` with both hashes; saving the same contents again is silent. Pass `locked=False` for intermediate data a pipeline rebuilds. The target folder must exist unless you pass `force=True`.

## Supported formats

framework-py reads these formats. It writes CSV and Parquet, which both languages read.

| Type | Extensions | Reader | Extra |
|------|------------|--------|-------|
| `csv` | `.csv`, `.tsv` | `pandas.read_csv()`, with readr's rules | |
| `tsv` | `.tsv`, `.txt`, `.dat` | `pandas.read_csv()`, with readr's rules | |
| `parquet` | `.parquet` | `pandas.read_parquet()` | |
| `excel` | `.xlsx`, `.xls` | `pandas.read_excel()`, with readxl's rules | `excel` for `.xlsx` |
| `stata` | `.dta` | `pandas.read_stata()` | |
| `spss` | `.sav`, `.zsav` | `pyreadstat.read_sav()` | `spss` |
| `spss_por` | `.por` | `pyreadstat.read_por()` | `spss` |
| `sas` | `.sas7bdat` | `pandas.read_sas()` | |
| `sas_xpt` | `.xpt` | `pandas.read_sas()`, or pyreadstat for version 8 files | `sas` for version 8 |
| `rds` | `.rds` | `pyreadr.read_r()`, data frames only | `rds` |

A catalog entry with an extension Framework does not recognize is read as comma-separated text, with a warning, unless the entry or the call names a delimiter.

`.rds` files that hold a data frame (or tibble, vector, or matrix) are read with pyreadr, from the `rds` extra (`uv add "framework-py[rds]"`). They are integrity-checked like any other catalog file, so an `.rds` intermediate R wrote reads in Python under the same name and hash. Without the extra, reading one raises an error with the install command. An `.rds` file that holds something else, such as a model or a list, raises `UnsupportedFormatError`. Python does not write `.rds` files; save CSV or Parquet, which both languages read. The catalog records each file's type, so either language reads the entries the other wrote.

## Data integrity

Framework hashes each catalog file with SHA-256 and stores the hash in `framework.db`.

1. The first read of a name records the file's hash. [data_add()](ref:data_add) and [data_save()](ref:data_save) record it when they register the file.
2. Each later read compares the file with the recorded hash.
3. When an unlocked file has changed, Framework issues a `DataChangedWarning`, records the new hash, and reads the file.
4. When a locked file has changed, Framework raises `IntegrityError` and does not read it.

Locking protects source data that should never change, such as the raw files a study received.

```text
IntegrityError: Hash mismatch for locked data 'inputs.raw.survey'
Expected hash: 71da0ff755e5
Current hash:  ddaa40dcf34b
```

When a locked file changes on purpose, such as a corrected delivery, register it again. `data_add()` records the new hash.

```python
fw.data_add("inputs/raw/survey.csv", name="inputs.raw.survey")
```

[data_verify()](ref:data_verify) checks a file against `framework.db` and, for projects connected to Framework Cloud, against the integrity ledger. It returns `True` when every record matches.

## Inspecting the catalog

[data_info()](ref:data_info) returns how Framework will read one file: its absolute path, type, delimiter, locked flag, and description.

```python
info = fw.data_info("inputs.raw.survey")
info.path
info.locked
```

With `columns=True`, `data_info()` also reads the file and describes its columns without showing any values: `info.columns` is a DataFrame with each column's `name`, `type`, and count of `missing` values, and `info.rows` is the number of rows. Types use R's words, so the summary reads the same in both languages: `logical`, `integer`, `double`, `character`, `factor`, `date`, `datetime`, `time`, `duration`, or `other`. The read does not check or record the file's hash.

```python
info = fw.data_info("inputs.private.raw.patients", columns=True)
info.columns
#      name       type  missing
# 0     mrn  character        0
# 1     age    integer        2
# 2  smoker    logical        0
info.rows
```

This is how an AI assistant working under the sensitive-data rules can learn a restricted file's layout without printing its records.

[data_list()](ref:data_list) returns every catalog entry as a DataFrame with the columns `name`, `path`, `type`, `locked`, and `description`.

```python
fw.data_list()
```

An unknown name raises `DataNotFoundError`, and the message lists up to twenty names the catalog does define.

## Organizing data

Keep data in the stage folders, and name catalog entries after the folder.

```text
inputs/
├── raw/           # inputs.raw.*: files as received, locked
├── intermediate/  # inputs.intermediate.*: cleaned and joined data
└── final/         # inputs.final.*: analysis-ready datasets
```

Raw data stays as it arrived. Each cleaning step reads from one stage and saves to the next, so the pipeline can be rerun from the raw files.

## Real data in the wrong place

Real data about people sometimes ends up in a standard project, where git commits `inputs/` and `outputs/`. Framework looks at column names, never values, for columns that look like direct identifiers:

| Kind | Column names such as |
|------|----------------------|
| Date of birth | `dob`, `patientDOB`, `date_of_birth`, `birth_date`, `BirthDate`, `birth_dt` |
| ZIP or postal code | `zip`, `ZIP`, `zip_code`, `zip5`, `postal_code`, `postcode` (not `zip3`) |
| Social Security number | `ssn`, `social_security_number` |
| Medical record number | `mrn`, `medical_record_number` |
| Name | `first_name`, `lastName`, `surname`, `patient_name`, but only alongside a date of birth |

[data_save()](ref:data_save) warns when it writes such columns outside a private folder (a folder named like `private`, `confidential`, or `sensitive`). The file is still saved:

```text
FrameworkWarning: inputs/intermediate/patients.parquet has columns that look like direct identifiers (dob, zip_code), and it isn't in a private folder. If this is real data about people, save it in a private folder, which git ignores, and consider a privacy-sensitive project (type project_sensitive).
```

The [security audit](guide:git) has a matching `identifier_columns` check, which reads only the header of each CSV, TSV, Parquet, Stata, or SPSS file git tracks outside private folders. It warns without failing, and the `data_security` pre-commit check prints the warning and lets the commit go ahead. Synthetic data with these columns gets the same warning: it's a reminder to check, not proof of real data.
