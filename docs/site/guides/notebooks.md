---
id: notebooks
title: Notebooks and scripts
description: Quarto notebooks, Jupyter notebooks, Python scripts, presentations, stubs, and placeholders
section: features
position: 5
---

## Overview

Framework scaffolds notebooks, scripts, and presentations from stub templates preloaded with front matter, `fw.scaffold()`, and section headings:

```python
import framework as fw

fw.make_notebook("Data Cleaning")
```

This creates `notebooks/data-cleaning.qmd` (or `.ipynb` when the project's `notebook_format` is `jupyter`).

## Notebooks

[make_notebook()](ref:make_notebook) creates a notebook in `directories.notebooks`:

```python
fw.make_notebook("exploration")
fw.make_notebook("eda", subdir="exploration")
fw.make_notebook("explore", type="jupyter")
```

Key arguments:

- `type`: `"quarto"`, `"jupyter"`, or `"script"`. Defaults to `scaffold.notebook_format` (`"quarto"` when unset), or follows an explicit file extension such as `"explore.ipynb"`.
- `dir`: target directory relative to the project root (defaults to `directories.notebooks`).
- `subdir`: optional subdirectory under `dir`.
- `stub`: template name (defaults to `"default"`).
- `overwrite`: set `True` to replace an existing file; defaults to `False`.

Framework converts the filename to lowercase kebab-case. For document front matter, names containing spaces or uppercase letters stay as typed (preserving acronyms like `"BP for LDL"`), while slug-style inputs such as `"setup-and-settings"` convert to sentence case (`"Setup and settings"`). Pass `title=` to set exact front-matter casing alongside a slug filename:

```python
fw.make_notebook("bp-for-ldl-and-hba1c", title="BP for LDL and HbA1c")
```

[make_qmd()](ref:make_qmd) always creates a Quarto `.qmd` document regardless of `notebook_format`:

```python
fw.make_qmd("summary", stub="minimal")
```

## Scripts

[make_script()](ref:make_script) creates a Python script in `directories.scripts` with a docstring, `fw.scaffold()`, and a `main()` entry point:

```python
fw.make_script("process-data")
```

```python
"""Process data

Author: Ada Lovelace
Date: 2026-10-31
"""

import framework as fw

fw.scaffold()


def main() -> None:
    # data = fw.data_read("inputs.raw.example")
    pass


if __name__ == "__main__":
    main()
```

## Presentations

[make_presentation()](ref:make_presentation) (and its alias [make_revealjs()](ref:make_revealjs)) creates a Quarto RevealJS presentation in `directories.notebooks`:

```python
fw.make_presentation("Quarterly Review")
```

`presentation` projects include a root `presentation.qmd` at creation time. See [Quarto](guide:quarto) for rendering options.

## Built-in stubs

`framework-py` ships five built-in stubs:

| Name | Format | Contents |
|------|--------|----------|
| `default` | Quarto | HTML front matter with TOC, hidden setup chunk, and Introduction, Analysis, and Results sections |
| `minimal` | Quarto | YAML front matter and hidden setup chunk |
| `revealjs` | Quarto | RevealJS slide deck with hidden setup chunk |
| `default` | Jupyter | Markdown title cell, `scaffold()` code cell, and Analysis section |
| `default` | Script | Module docstring, `scaffold()` call, and `main()` guard |

Every Quarto stub sets `jupyter: python3` and includes the hidden setup chunk:

````markdown
```{python}
#| label: setup
#| include: false

import framework as fw

fw.scaffold()
```
````

[stubs_list()](ref:stubs_list) inspects available stubs and their active source:

```python
fw.stubs_list()
fw.stubs_list("quarto")
```

## Project stubs

Place project-specific templates in `stubs/` at the project root using `notebook-<name>.qmd`, `notebook-<name>.ipynb`, or `script-<name>.py`. [stubs_publish()](ref:stubs_publish) copies built-in stubs into `stubs/` for customization:

```python
fw.stubs_publish()
fw.stubs_publish("scripts")
fw.stubs_publish(stubs=["default"])
```

Existing files in `stubs/` remain untouched unless `overwrite=True` is passed. [stubs_path()](ref:stubs_path) returns the active stub directory. Any new file such as `stubs/notebook-model.qmd` becomes immediately usable via `fw.make_notebook("logistic", stub="model")`.

Stub lookup precedence:

1. Project `stubs/` directory.
2. Personal templates in the global configuration directory ([write_framework_template()](ref:write_framework_template); see [Setup](guide:setup)).
3. Built-in `framework-py` stubs.

## Template placeholders

| Placeholder | Value |
|-------------|-------|
| `{{ title }}` | Resolved readable title |
| `{{ filename }}` | Input stem without extension |
| `{{ date }}` | Current date (`YYYY-MM-DD`) |
| `{{ author }}` | Author name from project or global settings (`Your Name` when unset) |
| `{{ email }}` | Author email |
| `{{ affiliation }}` | Author affiliation |

In Quarto stubs, `author: "{{ author }}"` expands automatically into a structured Quarto `author:` list when `orcid`, `url`, or `affiliations` metadata is configured:

```yaml
author:
  - name: "Ada Lovelace"
    email: "ada@example.org"
    orcid: "0000-0002-1825-0097"
    affiliations:
      - name: "Analytical Engines"
```
