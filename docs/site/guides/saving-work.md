---
id: saving-work
title: Saving work
description: Tables, figures, models, reports, and notebooks in the output folders, with public and private outputs
section: features
position: 7
---

## Overview

Framework saves analysis outputs with one function per kind. Each function writes the file to the project's output folder for that kind and records it in the `results` table of `framework.db` with its hash. Each returns the path of the saved file.

| Function | Saves | Folder key |
|----------|-------|------------|
| [save_table()](ref:save_table) | A DataFrame as CSV, Parquet, or Excel | `outputs_tables` |
| [save_figure()](ref:save_figure) | A matplotlib, seaborn, or plotnine figure | `outputs_figures` |
| [save_model()](ref:save_model) | A fitted model, with pickle | `outputs_models` |
| [save_report()](ref:save_report) | A finished report file | `outputs_reports` |
| [save_notebook()](ref:save_notebook) | A rendered Quarto or Jupyter notebook | `outputs_notebooks` |

An existing file of the same name is replaced. Pass `overwrite=False` to raise an error instead.

## Tables

[save_table()](ref:save_table) writes a DataFrame without its index. The `name` is the file name without an extension.

```python
import framework as fw

fw.scaffold()
summary = fw.data_read("inputs.final.analysis").groupby("site").mean(numeric_only=True)

fw.save_table(summary.reset_index(), "site_summary")
fw.save_table(summary.reset_index(), "site_summary", format="parquet")
fw.save_table(summary.reset_index(), "site_summary", format="xlsx")
```

The formats are `csv` (the default), `parquet`, and `xlsx`, which needs the `excel` extra. Other keyword arguments go to the pandas writer. CSV files use `\n` line endings on every platform, so their hashes match the ones R records.

## Figures

[save_figure()](ref:save_figure) saves any object with a `savefig()` method, such as a matplotlib or seaborn figure, a matplotlib Axes, which axes-level seaborn plots return, and any object with a `save()` method, such as a plotnine plot. Passing `None` saves the current matplotlib figure. framework-py does not depend on any plotting package.

```python
import matplotlib.pyplot as plt

fig, ax = plt.subplots()
ax.plot(summary.index, summary["score"])

fw.save_figure(fig, "score_by_site")
fw.save_figure(fig, "score_by_site", format="svg", width=10, height=4)
fw.save_figure(None, "current_plot")
```

The formats are `png` (the default), `pdf`, `svg`, `jpg`, `jpeg`, and `tiff`. `width` and `height` are in inches, with defaults of 8 and 6, and `dpi` defaults to 300.

## Models

[save_model()](ref:save_model) saves any Python object with pickle, as `<name>.pkl`.

```python
from sklearn.linear_model import LinearRegression

model = LinearRegression().fit(features, outcome)
fw.save_model(model, "regression_v1")
```

> [!WARNING]
> Loading a pickle can run arbitrary code. Load model files only from sources you trust.

R saves models as `.rds`, which Python cannot read. Models do not cross between the languages.

## Reports

[save_report()](ref:save_report) copies a finished file, such as an HTML report or a PDF, into the reports folder. `name` renames it and keeps its extension. `move=True` moves the file instead of copying it.

```python
fw.save_report("draft/analysis.html", "quarterly_report")
fw.save_report("draft/analysis.html", "quarterly_report", move=True)
```

## Notebooks

[save_notebook()](ref:save_notebook) renders a `.qmd` or `.ipynb` file with Quarto and saves the output in the notebooks folder.

```python
fw.save_notebook("notebooks/exploration.qmd")
fw.save_notebook("notebooks/exploration.qmd", name="final_analysis", format="docx")
```

Rendering needs the `quarto` command and, for Python chunks, Jupyter in the environment. The [Quarto](guide:quarto) guide covers rendering.

## Public and private outputs

Privacy-sensitive projects keep separate public and private folders for each kind of output. The `public` argument chooses between them. Outputs are private by default.

```python
# outputs/private/tables/patient_outcomes.csv
fw.save_table(patient_outcomes, "patient_outcomes")

# outputs/public/tables/summary.csv
fw.save_table(aggregated, "summary", public=True)
```

Private folders are ignored by git and refused by the publishing functions. Save to a public folder only after the data has been aggregated or de-identified.

In other project types, every output of a kind goes to the one folder for that kind. The `public` flag is still recorded in `framework.db`, so [result_list()](ref:result_list) can filter on it.

## Output folders

The folders come from the `directories` section of the settings. These are the defaults of a standard project.

```text
outputs/
├── tables/      # outputs_tables
├── figures/     # outputs_figures
├── models/      # outputs_models
├── reports/     # outputs_reports
└── notebooks/   # outputs_notebooks
```

A privacy-sensitive project uses keys such as `outputs_private_tables` and `outputs_public_tables`.

```text
outputs/
├── private/
│   ├── tables/
│   ├── figures/
│   ├── models/
│   ├── reports/
│   └── notebooks/
└── public/
    ├── tables/
    ├── figures/
    ├── models/
    ├── reports/
    └── notebooks/
```

A folder that does not exist yet is created the first time something is saved there. Change a folder by editing its key in `directories`.

## The results registry

Each save adds or updates a row in the `results` table with these fields.

| Field | Contents |
|-------|----------|
| `name` | The saved file's path from the project root, such as `outputs/tables/site_summary.csv` |
| `type` | `table`, `figure`, `model`, `report`, or `notebook` |
| `public` | Whether the output was saved as public |
| `hash` | The file's SHA-256 hash |
| `created_at` | When the file was first saved |
| `updated_at` | When the file was last saved |

[result_list()](ref:result_list) returns the rows, newest first, and filters by type and by the public flag.

```python
fw.result_list()
fw.result_list(type="figure")
fw.result_list(type="table", public=True)
```

Rows are keyed by the path from the project root. A public and a private `summary.csv` get separate rows.
