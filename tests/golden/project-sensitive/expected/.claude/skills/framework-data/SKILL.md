---
name: framework-data
description: Use when reading or writing data files (CSV, Parquet, Excel, Stata, SPSS, SAS) in a project that uses the Framework package. Covers fw.data_read(), fw.data_save(), the data catalog, and the data folders.
metadata:
  title: "Framework: reading and saving data"
  summary: Read and save data files through Framework, which records each file's hash.
---

# Framework: reading and saving data

`fw.data_read()` and `fw.data_save()` record a hash of each data file in the
project's `framework.db`. A file read directly with pandas or pyreadstat skips
that record, so a changed file goes unnoticed. Follow these conventions in
code that calls `fw.scaffold()`. Leave existing code as it is unless the user
asks you to change it.

## Reading

Prefer `fw.data_read()`. It reads CSV, TSV, Parquet, Excel, Stata, SPSS, and
SAS files.

```python
survey = fw.data_read("inputs.raw.survey")            # by catalog name
customers = fw.data_read("inputs/raw/customers.csv")  # by path
```

Datasets listed in the settings (often `settings/data.yml`) have a dotted
catalog name. Prefer it to the path when an entry exists. `fw.data_list()`
shows the catalog.

Python cannot read `.rds` files. Ask for a Parquet or CSV copy.

## Saving

Prefer `fw.data_save()` to `DataFrame.to_csv()` and `DataFrame.to_parquet()`
for project data. It writes Parquet or CSV, which R can also read.

```python
# Locked by default: a later read raises an error if the file changed
fw.data_save(final, "inputs/final/analysis_ready.csv")

# Data a pipeline rebuilds on every run: unlocked, so a rebuild doesn't warn
fw.data_save(clean, "inputs.intermediate.survey_clean", locked=False)
```

## Folders

Data moves from raw to intermediate to final. AGENTS.md lists this project's
folders. Raw inputs stay unchanged.
