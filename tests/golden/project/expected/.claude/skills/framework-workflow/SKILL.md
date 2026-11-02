---
name: framework-workflow
description: Use when writing or editing any Python notebook or script in this Framework project. Every notebook and script starts with fw.scaffold(). Covers project setup, the analysis workflow, and creating notebooks and scripts with make_notebook() and make_script().
metadata:
  title: Notebooks and scripts
  summary: Start every notebook and script with scaffold(), and create new ones through Framework.
---

# Framework Workflow

This project uses Framework for reproducible data analysis in Python. Import
it as `import framework as fw`.

## Start with scaffold()

Begin every notebook and script with:

```python
import framework as fw

fw.scaffold()
```

`scaffold()` finds the project root from any subfolder, loads `.env` and the
settings, sets the random seed when the project asks for one, puts the
project's `functions/` folder on the import path, and runs `scaffold.py`.
Without it, settings paths and environment variables are not available.

- Import helpers from `functions/` with a normal `import`. `scaffold()` has
  already put the folder on the path, so do not edit `sys.path` yourself.
- Do not reseed after `scaffold()` when the project sets a seed, unless an
  operation needs its own seed. Say why in a comment if so.
- Paths in the settings are relative to the project root. Framework resolves
  them for you, so do not change the working directory.

## Analysis workflow

1. Read raw data with `fw.data_read()`.
2. Clean it and save intermediate data with `fw.data_save()`.
3. Analyze the final, analysis-ready data.
4. Save results with `fw.save_table()`, `fw.save_figure()`, or `fw.save_model()`.

The `framework-data` skill covers data rules, and the `framework-outputs`
skill covers results, caching, and publishing.

## New notebooks and scripts

Create files through Framework so they go in the right folder with the
project's template:

```python
fw.make_notebook("01-data-cleaning")  # notebooks/01-data-cleaning.qmd
fw.make_script("data-processing")  # scripts/data-processing.py
fw.make_notebook("talk", stub="revealjs")
```

## Habits

- Raw data is never modified. Write cleaned versions to the intermediate folder.
- Notebooks explain each transformation.
- Commit notebooks and code, not rendered outputs.
