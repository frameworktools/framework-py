---
id: data-management
title: Data management
description: Data catalog entries, R-compatible reading and writing, SHA-256 integrity hashes, and identifier checks
section: features
position: 4
---

## Overview

Framework manages datasets through a central catalog. Each dataset receives a dot-notation key such as `inputs.raw.survey` mapped to its path, file format, and integrity settings. Analysis code reads datasets by key rather than hardcoded file paths, and Framework verifies SHA-256 hashes automatically on every read:

```python
import framework as fw

fw.scaffold()
survey = fw.data_read("inputs.raw.survey")
```

[data_read()](ref:data_read) returns a `pandas.DataFrame`.

## Data catalog

Catalog entries live under `data` in `settings.yml` (or `settings/data.yml` in standard and privacy-sensitive projects):

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

Each entry is either a path string or a mapping with these fields:

| Field | Meaning |
|-------|---------|
| `path` | Project-relative file path (required). |
| `type` | File format; inferred from the file extension when omitted. |
| `delimiter` | Delimiter for text files: `comma`, `tab`, `semicolon`, or `space`. |
| `locked` | When `true`, file modifications raise `IntegrityError` on read. Omitted for unlocked entries. |
| `description` | Summary printed on read and shown by `data_list()`. |

Custom metadata fields are preserved and exposed via `info.extra` on [data_info()](ref:data_info). Framework writes nested YAML keys, though dotted keys (`raw.survey:` under `inputs:`) resolve identically.

## Existing file registration

[data_add()](ref:data_add) registers an existing project file in the catalog, records its SHA-256 hash in `framework.db`, and returns the catalog entry:

```python
fw.data_add("inputs/raw/survey.csv")
```

When `name` is omitted, files inside configured `inputs*` directories derive their catalog name from the deepest matching directory key (`inputs.raw.survey`, or `inputs.private.raw.participants` in a privacy-sensitive project). For files elsewhere, pass `name` explicitly:

```python
fw.data_add("inputs/raw/sites_2024.csv", name="inputs.raw.sites")
```

`data_add()` leaves entries unlocked unless you pass `locked=True`. Pass `type` or `delimiter` to override extension detection, or `update_config=False` to record only the `framework.db` hash.

## Reading data

[data_read()](ref:data_read) accepts either a dot-notation catalog key or a file path containing `/`:

```python
survey = fw.data_read("inputs.raw.survey")
lookup = fw.data_read("inputs/reference/codes.csv")
```

Catalog keys resolve from the project root and run `framework.db` hash verification. Relative file paths check the project root first and then the current working directory without checking `framework.db`.

Pass `delim` to override the delimiter, or pass reader-specific keyword arguments through to `pandas` or `pyreadstat`:

```python
fw.data_read("inputs.raw.survey", usecols=["id", "score"])
```

For Stata and SPSS files, `keep_attributes=True` converts value labels to categorical columns; by default `data_read()` returns underlying codes to match R.

### Missing values and whitespace

CSV, TSV, and Excel reads match R's `readr` and `readxl` defaults so identical files produce identical row sets across languages:

- **CSV and TSV:** only empty fields and `"NA"` become missing values (unlike default `pandas`, which also converts `"None"`, `"N/A"`, `"null"`, and `"NaN"`).
- **Excel:** only empty cells become missing values; literal `"NA"` cells remain strings.
- **Whitespace:** leading and trailing whitespace is trimmed from column names and text fields before parsing, so `"   NA"` in CSV becomes missing and padded numbers parse cleanly.

Override missing strings with `na=` or preserve whitespace with `trim_ws=False`:

```python
fw.data_read("inputs.raw.prams", na=["", "NA", "."])
fw.data_read("inputs.raw.fixed_width_export", trim_ws=False)
```

Native `pandas` arguments (`na_values`, `keep_default_na`) remain supported when passed directly.

### R-compatible reader arguments

For CSV, TSV, and Excel files, `data_read()` accepts `readr` and `readxl` argument names directly:

| Argument | Meaning |
|----------|---------|
| `sheet` | Excel sheet name or 1-based sheet index |
| `col_names` | `True` (header row), `False` (auto-named `X1, X2, ...` for text or `...1, ...2, ...` for Excel), or list of names |
| `col_types` | `readr` compact string (`"cid_"`) or dict (`{"zip": "c"}`) for text; `readxl` type string or list for Excel |
| `skip` | Rows to skip before reading headers or data |
| `n_max` | Maximum data rows to read |
| `na`, `trim_ws` | Missing-value strings and whitespace trimming |

```python
fw.data_read("inputs/raw/labs.xlsx", sheet="results", skip=1, col_names=False)
fw.data_read("inputs/raw/zips.csv", col_types={"zip": "c"})
```

`readr` type codes: `c` (string), `i` (integer), `d` or `n` (float), `l` (boolean), `f` (categorical), `D` (date), `T` (datetime), `t` (time as string), `?` (infer), and `_` or `-` (skip). `readxl` types: `"guess"`, `"text"`, `"numeric"`, `"logical"`, `"date"`, `"list"`, and `"skip"`. Mixing an R argument with its `pandas` counterpart (such as `sheet` and `sheet_name`) raises an error.

## Saving data

[data_save()](ref:data_save) writes a DataFrame, updates the catalog and `framework.db` hash, and returns the DataFrame:

```python
clean = survey.dropna()
fw.data_save(clean, "inputs.intermediate.survey_clean")
```

When given a catalog key, `data_save()` matches the longest prefix against `directories` (`inputs.intermediate.survey_clean` resolves `inputs_intermediate` and writes `inputs/intermediate/survey_clean.parquet`) and registers the catalog entry automatically.

Catalog saves default to Parquet. Pass `type="csv"` (and optional `delimiter`) or append `.csv` to the key (`inputs.final.analysis.csv`) to write CSV:

```python
fw.data_save(clean, "inputs.final.analysis", type="csv")
```

CSV output matches `readr::write_csv()` byte for byte: integers without `.0`, `TRUE`/`FALSE` booleans, `NA` for missing values, UTC ISO-8601 timestamps (`2020-01-02T03:04:05Z`), and `\n` newlines.

When given a file path containing `/` (ending in `.csv` or `.parquet`), `data_save()` writes to that project-relative path and records its `framework.db` hash without adding a catalog entry. Call [data_add()](ref:data_add) afterward if you also want a catalog key:

```python
fw.data_save(clean, "exports/patients_clean.parquet")
fw.data_add("exports/patients_clean.parquet", name="exports.patients_clean")
```

`data_save()` leaves saved files unlocked, so a pipeline can save them again. Pass `locked=True` to lock data you mean to keep as it is, or `force=True` to create parent directories outside configured project paths.

### Locked data protection

`data_save()` does not change locked data, matching R. Rewriting identical bytes works. New contents raise `IntegrityError` before the file is written, whether the save names the catalog entry or its path:

```text
IntegrityError: data_save() does not overwrite locked data.
  dataset: inputs.raw.survey (locked: true in the data catalog)
  file:    inputs/raw/survey.csv
Save the data under another name, or pass unlock=True to replace it.
The new file is then unlocked. Add locked=True to keep it locked.
```

Data is unlocked unless you lock it with `locked=True` or with `locked: true` on its catalog entry. A later save with the same contents keeps the lock.

Save derived data under another name. To replace locked data on purpose, pass `unlock=True`. The new file is unlocked unless you also pass `locked=True`. Setting `locked: false` on the catalog entry also unlocks it. `save_table()`, `save_model()`, and `cache()` never overwrite locked data.

```python
fw.data_save(final, "inputs.final.analysis", locked=True)
fw.data_save(corrected, "inputs.raw.survey.csv", unlock=True, locked=True)
fw.data_save(clean, "inputs.intermediate.survey_clean", unlock=True)
```

## Supported formats

`framework-py` reads ten formats and writes CSV and Parquet:

| Type | Extensions | Reader | Optional extra |
|------|------------|--------|----------------|
| `csv` | `.csv`, `.tsv` | `pandas.read_csv()` (`readr` rules) | |
| `tsv` | `.tsv`, `.txt`, `.dat` | `pandas.read_csv()` (`readr` rules) | |
| `parquet` | `.parquet` | `pandas.read_parquet()` | |
| `excel` | `.xlsx`, `.xls` | `pandas.read_excel()` (`readxl` rules) | `excel` (`.xlsx`) |
| `stata` | `.dta` | `pandas.read_stata()` | |
| `spss` | `.sav`, `.zsav` | `pyreadstat.read_sav()` | `spss` |
| `spss_por` | `.por` | `pyreadstat.read_por()` | `spss` |
| `sas` | `.sas7bdat` | `pandas.read_sas()` | |
| `sas_xpt` | `.xpt` | `pandas.read_sas()` or `pyreadstat` (v8) | `sas` (v8) |
| `rds` | `.rds` | `pyreadr.read_r()` (tabular objects) | `rds` |

Catalog entries with unrecognized extensions fall back to comma-delimited text with a warning unless `delimiter` is set; direct file paths with unrecognized extensions raise `UnsupportedFormatError`.

With `framework-py[rds]` installed, `.rds` files containing data frames, tibbles, vectors, or matrices read cleanly and verify against the SHA-256 hash recorded by R. Non-tabular `.rds` objects (such as fitted R models or nested lists) raise `UnsupportedFormatError`. Python writes CSV and Parquet rather than `.rds`.

## Data integrity

Framework tracks SHA-256 digests in `framework.db`:

1. `data_add()`, `data_save()`, or the first `data_read()` records the file's SHA-256 digest.
2. Subsequent `data_read()` calls recompute and compare the digest.
3. Unlocked mismatches emit `DataChangedWarning`, update the stored digest, and return the data.
4. Locked mismatches raise `IntegrityError` and block the read:

```text
IntegrityError: Hash mismatch for locked data 'inputs.raw.survey'
Expected hash: 71da0ff755e5
Current hash:  ddaa40dcf34b
```

After an intentional raw-data replacement, re-register the file with `data_add()` to record the new hash:

```python
fw.data_add("inputs/raw/survey.csv", name="inputs.raw.survey")
```

[data_verify()](ref:data_verify) checks a dataset against `framework.db` and, when linked, the Framework Cloud integrity ledger.

## Catalog inspection

[data_info()](ref:data_info) returns resolved path, format, delimiter, lock state, and description without reading rows:

```python
info = fw.data_info("inputs.raw.survey")
info.path
info.locked
```

Pass `columns=True` to inspect schema metadata (`name`, R-compatible `type`, pandas `dtype`, `missing` count, and total `info.rows`) without printing cell values or altering `framework.db` hashes. AI assistants operating under sensitive-data rules use this call to read dataset structure safely:

```python
info = fw.data_info("inputs.private.raw.patients", columns=True)
info.columns
info.rows
```

[data_list()](ref:data_list) returns all catalog entries as a DataFrame (`name`, `path`, `type`, `locked`, `description`):

```python
fw.data_list()
```

Unknown keys raise `DataNotFoundError` and list up to twenty available catalog names.

## Identifier column warnings

To catch accidental storage of identifiable records in git-tracked folders, Framework checks column names (never cell values) for common direct identifiers:

| Category | Example column names |
|----------|----------------------|
| Date of birth | `dob`, `patientDOB`, `date_of_birth`, `birth_date`, `BirthDate`, `birth_dt` |
| Postal code | `zip`, `ZIP`, `zip_code`, `zip5`, `postal_code`, `postcode` (excludes `zip3`) |
| Social Security number | `ssn`, `social_security_number` |
| Medical record number | `mrn`, `medical_record_number` |
| Name (with DOB) | `first_name`, `lastName`, `surname`, `patient_name` alongside a DOB column |

[data_save()](ref:data_save) emits `FrameworkWarning` when writing matching columns outside a `private`, `confidential`, or `sensitive` directory. The [security audit](guide:git) runs a matching header-only `identifier_columns` check on git-tracked files.
