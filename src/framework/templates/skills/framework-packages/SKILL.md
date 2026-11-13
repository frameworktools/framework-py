---
name: framework-packages
description: Use when adding, installing, or managing Python packages in a project that uses the Framework package. Covers fw.package_add(), pyproject.toml, and uv.lock.
metadata:
  title: "Framework: package management"
  summary: Add packages through Framework so the project records them.
---

# Framework: package management

The project's dependencies are listed in `pyproject.toml`. When the project
uses uv, `uv.lock` records the exact versions. A package installed with a bare
`pip install` is not recorded, so the next machine will not have it.

## Adding packages

Prefer `fw.package_add()`, or `uv add` from a terminal.

```python
fw.package_add("statsmodels")  # record in pyproject.toml and install
fw.package_add("polars>=1.0")  # with a version requirement
```

## Checking and restoring

```python
fw.packages_list()      # what pyproject.toml declares
fw.packages_status()    # missing or out-of-date packages
fw.packages_install()   # install everything, for example after cloning
fw.packages_snapshot()  # update uv.lock
fw.packages_restore()   # install exactly what uv.lock records
```

Import packages in each notebook or script where they are used.
