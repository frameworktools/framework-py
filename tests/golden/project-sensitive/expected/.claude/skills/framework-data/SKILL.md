---
name: framework-data
description: Use whenever reading or writing data files (CSV, Parquet, Excel, Stata, SPSS, SAS) in this Framework project. Read with fw.data_read() and write with fw.data_save(), which record a hash of each file in framework.db.
metadata:
  title: Reading and saving data
  summary: Read and save data files through Framework, which records each file's hash.
---

# Framework Data

`fw.data_read()` and `fw.data_save()` record a SHA-256 hash of each data file
in the project's `framework.db`. Reading the file directly with pandas skips
that record, so a changed or replaced file would go unnoticed.

## Reading

```python
survey = fw.data_read("inputs.raw.survey")  # by catalog name (preferred)
customers = fw.data_read("inputs/raw/customers.csv")  # by path
```

`data_read()` reads CSV, TSV, Parquet, Excel, Stata, SPSS, and SAS files.
Catalog files are checked against their recorded hash: a locked file that
changed raises an error, and an unlocked one warns. Use it instead of
`pd.read_csv()`, `pd.read_parquet()`, `pd.read_excel()`, `pd.read_stata()`,
or `pyreadstat`.

`.rds` files can only be read by R. Ask for a Parquet or CSV copy instead.

## Saving

```python
fw.data_save(clean, "inputs.intermediate.survey_clean")  # Parquet, added to the catalog
fw.data_save(final, "inputs/final/analysis_ready.csv", locked=True)
```

`data_save()` writes Parquet or CSV, which R can read too. Use it instead of
`DataFrame.to_csv()` or `DataFrame.to_parquet()` for project data.

## The data catalog

Datasets are listed under `data` in the settings (often `settings/data.yml`)
and read by dot-notation name. Use the catalog name when an entry exists.
`fw.data_list()` shows the catalog.

## Folders

Data moves from raw to intermediate to final. The folders are listed under
`directories` in the settings. Raw inputs are never changed, and only
Framework writes go to the intermediate and final folders.
