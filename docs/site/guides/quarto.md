---
id: quarto
title: Quarto
description: Quarto render directories, configuration files, table display, and Plotly embedding
section: features
position: 6
---

## Overview

Framework uses [Quarto](https://quarto.org) to render `.qmd` and `.ipynb` notebooks, documents, and presentations. Project creation writes `_quarto.yml` so rendered outputs land in dedicated output directories separate from source files.

Quarto is enabled by default for every project type except `bare`. Passing `tools={"quarto": False}` to [new_project()](ref:new_project) omits `_quarto.yml`, records `tools.quarto: false` in `settings.yml`, defaults [make_notebook()](ref:make_notebook) to `.ipynb`, and disables [quarto_regenerate()](ref:quarto_regenerate). See [New projects](guide:new-project#tools-and-sets).

## Render directories

`render_dirs` in `settings.yml` maps source directories to rendered output directories:

| Project type | Source directory | Render directory | Format |
|--------------|------------------|------------------|--------|
| Standard | `notebooks/` | `outputs/notebooks/` | HTML |
| Standard | `docs/` | `outputs/docs/` | HTML |
| Privacy-sensitive | `notebooks/` | `outputs/private/notebooks/` | HTML |
| Privacy-sensitive | `docs/` | `outputs/private/docs/` | HTML |
| Course | `slides/` | `rendered/slides/` | RevealJS |
| Course | `assignments/` | `rendered/assignments/` | HTML |
| Course | `course_docs/` | `rendered/course_docs/` | HTML |
| Course | `modules/` | `rendered/modules/` | HTML |
| Presentation | project root | project root | RevealJS |

Directory keys containing `slide` render as RevealJS; all others render as HTML. Privacy-sensitive projects route rendered notebooks to `outputs/private/notebooks/` so outputs containing restricted data stay out of git.

## Generated configuration

Framework writes a single root `_quarto.yml` whose `output-dir` points to the shared parent output directory (such as `outputs` in a standard project), preserving source subfolder paths (`notebooks/exploration.qmd` renders to `outputs/notebooks/exploration.html`):

```text
vaccine-study/
├── _quarto.yml          # project-wide settings; output-dir: outputs
├── notebooks/
│   └── exploration.qmd
├── docs/
└── outputs/
    └── notebooks/       # rendered HTML output
```

```yaml
project:
  type: default
  output-dir: outputs

format:
  html:
    theme: default
    toc: true
    toc-depth: 3
    code-fold: false
    code-tools: false
    embed-resources: true
    highlight-style: github
```

Subdirectories that use a different format (such as `slides/` in a course project) receive a minimal `_metadata.yml` overriding `format` while inheriting root settings. Render targets outside the shared `output-dir` receive their own `_quarto.yml` with a relative `output-dir`, and `presentation` projects omit `output-dir` to render in place.

Framework writes `_quarto.yml` once at project creation and never modifies it unless you call [quarto_regenerate()](ref:quarto_regenerate).

## Rendering defaults

Initial `_quarto.yml` values come from the `quarto` section in `settings.yml` (underscores in `settings.yml` map to hyphens in `_quarto.yml`):

```yaml
quarto:
  html:
    theme: default
    embed_resources: true
    toc: true
    toc_depth: 3
    code_fold: false
    code_tools: false
    highlight_style: github
  revealjs:
    theme: default
    incremental: false
    slide_number: true
    transition: slide
    background_transition: fade
    controls: true
    progress: true
    center: true
    highlight_style: github
```

After editing `quarto` in `settings.yml`, call `quarto_regenerate()` to apply changes to `_quarto.yml`.

## Configuration regeneration

[quarto_regenerate()](ref:quarto_regenerate) reconciles Quarto files with `settings.yml`. By default (`overwrite=False`), it creates missing files and adds only missing structural keys (`project.type`, `project.output-dir`, and `format`) while preserving custom themes, execution flags, and comments:

```python
import framework as fw

fw.quarto_regenerate(".", dry_run=True)  # preview changes
result = fw.quarto_regenerate(".")
result.regenerated, result.kept

fw.quarto_regenerate(".", overwrite=True)  # rewrite files and fold legacy per-folder configs
```

Pass `overwrite=True` to replace `_quarto.yml` from `settings.yml` and remove legacy per-folder `_quarto.yml` files, backing up modified or removed files under `.framework/backups/<timestamp>/` (unless `backup=False` is set). [quarto_generate_all()](ref:quarto_generate_all) generates Quarto configs directly from explicit parameters during project creation.

## Rendering notebooks

Quarto executes Python chunks through Jupyter (`ipykernel`, `nbformat`, and `nbclient`, included automatically when the `quarto` tool is active). Render from the project environment with `uv run`:

```sh
uv run quarto render notebooks/exploration.qmd
```

[save_notebook()](ref:save_notebook) renders `.qmd` or `.ipynb` files into `outputs_notebooks` and registers the output in `framework.db`:

```python
fw.save_notebook("notebooks/exploration.qmd")
fw.save_notebook("notebooks/exploration.qmd", name="exploration-2026-10", format="pdf")
```

Supported formats are `html`, `pdf`, and `docx`. HTML renders self-contained (`embed_resources=True`) unless `embed_resources=False` is passed to write `<name>_files/` beside the HTML file. Pass extra CLI flags via `quarto_args`.

## Tables and Plotly figures

[show_table()](ref:show_table) formats a DataFrame as a Markdown pipe table (matching `knitr::kable()` in R) that Quarto styles natively across HTML, PDF, and Word and that Jupyter, VS Code, and Positron render inline:

````markdown
```{python}
#| label: tbl-arms
#| tbl-cap: Patients by arm
fw.show_table(counts, digits=1, big_mark=",")
```

@tbl-arms shows the arms.
````

Use `digits` for decimal precision, `big_mark` for thousands separators, `na` for missing-value text, and `caption` when no `#| tbl-cap` option is set. Summarize large DataFrames before calling `show_table()`, since it renders every row.

### Compact Plotly output with embed-resources

With `embed-resources: true`, Quarto inlines every script tag on the page. Default `fig.show()` embeds `plotly.js` and MathJax inside every figure, pushing multi-figure notebooks past 100 MB. Emit `plotly.js` once in the first figure and skip MathJax unless needed:

```python
from IPython.display import HTML, display

_plotly_loaded = False


def show_plotly(fig):
    global _plotly_loaded
    html = fig.to_html(
        full_html=False,
        include_plotlyjs=not _plotly_loaded,
        include_mathjax=False,
    )
    display(HTML(html))
    _plotly_loaded = True
```

Store `show_plotly()` in `functions/` and call it instead of `fig.show()`. If a figure uses LaTeX math labels, pass `include_mathjax="cdn"` on that figure.

## Publishing

[publish_notebook()](ref:publish_notebook) renders a notebook and uploads it to Framework Cloud, S3-compatible storage, or GitHub Pages. See [Publishing](guide:publishing).
