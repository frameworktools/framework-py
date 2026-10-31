---
id: notebooks
title: Notebooks and Scripts
description: Creating notebooks, scripts, and presentations from stubs and templates
section: features
position: 5
---

## Overview

Framework creates new notebooks and scripts from stubs. A stub is a template file that already contains the front matter, the `scaffold()` call, and a starting outline. Each new file is named from the title you give and placed in the project's folder for its kind.

```python
import framework as fw

fw.make_notebook("Data Cleaning")
```

This call creates `notebooks/data-cleaning.qmd`.

## Notebooks

[make_notebook()](ref:make_notebook) creates a Quarto notebook with Python code chunks by default.

```python
fw.make_notebook("exploration")
fw.make_notebook("eda", subdir="exploration")
fw.make_notebook("explore", type="jupyter")
```

The arguments control the file.

- `type` is `"quarto"`, `"jupyter"`, or `"script"`. Without it, `notebook_format` in the `scaffold` settings decides, and the default is `quarto`. An extension in the name, such as `"explore.ipynb"`, also sets the type.
- `dir` names the folder. The default is `directories.notebooks`, resolved from the project root. An explicit `dir` is relative to the current folder.
- `subdir` adds a subfolder of that folder.
- `stub` names the stub to start from. The default is `"default"`.
- `overwrite=True` replaces an existing file. Without it, an existing file is an error.

The title is turned into a file name with lowercase letters, digits, and hyphens. The title itself goes into the front matter.

[make_qmd()](ref:make_qmd) is `make_notebook()` with the type fixed to Quarto.

```python
fw.make_qmd("summary", stub="minimal")
```

## Scripts

[make_script()](ref:make_script) creates a Python script in `directories.scripts`.

```python
fw.make_script("process-data")
```

The script stub has a docstring, the `scaffold()` call, and a `main()` function guarded by `if __name__ == "__main__":`.

```python
"""process-data

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

[make_presentation()](ref:make_presentation) creates a Quarto reveal.js presentation in the notebooks folder. [make_revealjs()](ref:make_revealjs) is the same function under another name.

```python
fw.make_presentation("Quarterly Review")
```

Presentation projects start with their own `presentation.qmd`. The [Quarto](guide:quarto) guide covers rendering.

## The Stubs

framework-py ships five stubs.

| Name | Type | Contents |
|------|------|----------|
| `default` | Quarto | HTML format with a table of contents, a hidden setup chunk, and Introduction, Analysis, and Results sections |
| `minimal` | Quarto | Front matter and the setup chunk only |
| `revealjs` | Quarto | A reveal.js presentation with a hidden setup chunk |
| `default` | Jupyter | A title cell, a setup cell, and an Analysis section |
| `default` | script | A docstring, the setup lines, and a `main()` function |

Every Quarto stub sets `jupyter: python3` and runs this setup chunk.

````markdown
```{python}
#| label: setup
#| include: false

import framework as fw

fw.scaffold()
```
````

[stubs_list()](ref:stubs_list) returns the available stubs with their type and source.

```python
fw.stubs_list()
fw.stubs_list("quarto")
```

## Customizing Stubs

A project can keep its own stubs in a `stubs/` folder in the root. Stub files are named `notebook-<name>.qmd`, `notebook-<name>.ipynb`, or `script-<name>.py`. [stubs_publish()](ref:stubs_publish) copies Framework's stubs into that folder so you can edit them.

```python
fw.stubs_publish()
fw.stubs_publish("scripts")
fw.stubs_publish(stubs=["default"])
```

Existing files in `stubs/` are kept unless you pass `overwrite=True`. [stubs_path()](ref:stubs_path) returns the folder stubs are read from. It returns the project's `stubs/` folder when one exists.

A new stub name becomes available as soon as its file exists. A file named `stubs/notebook-model.qmd` is used by `fw.make_notebook("logistic", stub="model")`.

Framework looks for a stub in three places, in this order.

1. The project's `stubs/` folder.
2. Your personal templates in the config folder, saved with [write_framework_template()](ref:write_framework_template). The [Setup](guide:setup) guide describes them.
3. The stubs that ship with framework-py.

## Placeholders

Stubs can use these placeholders, which are filled when the file is created.

| Placeholder | Value |
|-------------|-------|
| `{{ title }}` | The title you passed |
| `{{ filename }}` | The same title |
| `{{ date }}` | Today's date, as `YYYY-MM-DD` |
| `{{ author }}` | The author's name, or `Your Name` when none is set |
| `{{ email }}` | The author's email |
| `{{ affiliation }}` | The author's affiliation |

The author comes from the project's `author` settings, with your global `author` filling empty fields. In a Quarto stub, the line `author: "{{ author }}"` becomes a full Quarto author block when the author has an ORCID, a URL, or a list of affiliations.

```yaml
author:
  - name: "Ada Lovelace"
    email: "ada@example.org"
    orcid: "0000-0002-1825-0097"
    affiliations:
      - name: "Analytical Engines"
```
