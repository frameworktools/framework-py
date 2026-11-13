---
name: framework-workflow
description: Use when writing or editing a Python notebook or script in a project that uses the Framework package. Covers fw.scaffold() and creating notebooks and scripts with make_notebook() and make_script().
metadata:
  title: "Framework: notebooks and scripts"
  summary: Start notebooks and scripts with scaffold(), and create new ones through Framework.
---

# Framework: notebooks and scripts

This project uses the Framework Python package, imported as
`import framework as fw`. Follow its conventions in code that calls
`fw.scaffold()`. Leave existing code as it is unless the user asks you to
change it.

## scaffold()

Start each notebook and script with:

```python
import framework as fw

fw.scaffold()
```

`scaffold()` finds the project root from any subfolder, loads `.env` and the
settings, sets the project's seed, puts the `functions/` folder on the import
path, and runs `scaffold.py`.

- Import helpers from `functions/` with a normal `import`. `scaffold()` has
  already put the folder on the path.
- Prefer the project's seed. When an operation needs its own seed, say why in
  a comment.
- Settings paths are relative to the project root, and Framework resolves
  them.

## New notebooks and scripts

Prefer Framework's helpers. They put the file in the project's folder and use
its template.

```python
fw.make_notebook("01-data-cleaning")       # notebooks/01-data-cleaning.qmd
fw.make_script("data-processing")          # scripts/data-processing.py
fw.make_notebook("talk", stub="revealjs")  # slides
```
