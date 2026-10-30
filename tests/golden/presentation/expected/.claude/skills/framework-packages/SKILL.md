---
name: framework-packages
description: Use when adding, installing, or managing Python packages in this Framework project. Dependencies are recorded in pyproject.toml; add them with fw.package_add() or uv add instead of a bare pip install.
---

# Framework Packages

The project's Python dependencies are listed in `pyproject.toml`, and
`uv.lock` records the exact versions when the project uses uv. A package
installed with a bare `pip install` is not recorded, so the next person (or
the next machine) will not have it.

## Adding packages

```python
fw.package_add("statsmodels")  # records it in pyproject.toml and installs it
fw.package_add("polars>=1.0")  # with a version requirement
```

From a terminal, `uv add statsmodels` does the same.

## Checking and restoring

```python
fw.packages_list()  # what pyproject.toml declares
fw.packages_status()  # missing or out-of-date packages
fw.packages_install()  # install everything (for example after cloning)
fw.packages_snapshot()  # update uv.lock
fw.packages_restore()  # install exactly what uv.lock records
```

Import packages normally in each notebook or script. Framework does not
import them for you.
