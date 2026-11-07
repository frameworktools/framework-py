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

A `name` with `/` saves into a subfolder of the tables folder, which is created: `fw.save_table(cohort, "meeting-2026-10-16/cohort")` writes `outputs/tables/meeting-2026-10-16/cohort.csv`.

The formats are `csv` (the default), `parquet`, and `xlsx`, which needs the `excel` extra. CSV files are written as R's `readr::write_csv()` writes them, as [data_save()](ref:data_save) writes them: whole numbers without `.0`, `TRUE` and `FALSE`, `NA` for missing values, and `\n` line endings on every platform. The same table saved from R and from Python gives the same bytes and the same hash. Other keyword arguments go to the pandas writer, on top of that form.

## Figures

[save_figure()](ref:save_figure) saves any object with a `savefig()` method, such as a matplotlib or seaborn figure, a matplotlib Axes, which axes-level seaborn plots return, and any object with a `save()` method, such as a plotnine plot or a plotnine composition (`p1 | p2`, `p1 / p2`). Passing `None` saves the current matplotlib figure. framework-py does not depend on any plotting package.

```python
import matplotlib.pyplot as plt

fig, ax = plt.subplots()
ax.plot(summary.index, summary["score"])

fw.save_figure(fig, "score_by_site")
fw.save_figure(fig, "score_by_site", format="svg", width=10, height=4)
fw.save_figure(None, "current_plot")
```

The formats are `png` (the default), `pdf`, `svg`, `eps`, `jpg`, `jpeg`, `tiff`, and `emf`. A list saves one file per format and returns the paths in that order, as journals often want:

```python
fw.save_figure(fig, "figure_2", format=["pdf", "eps", "svg", "png"])
```

Every format is checked, and every existing file with `overwrite=False`, before any file is written. `emf` needs a plotting backend that writes it; matplotlib has none, so Python raises `UnsupportedFormatError` for it. Save `svg` or `pdf` and convert them instead. `width` and `height` are in inches, with defaults of 8 and 6, as in R, and `dpi` defaults to 300. They set the size of the file, not of your figure: a matplotlib figure is resized only while it is saved and keeps its own size afterwards, and a plotnine composition is sized on a copy. To keep a figure's own size in the file, pass it, as in `width=fig.get_figwidth(), height=fig.get_figheight()`.

As for tables, a `name` with `/` saves into a subfolder, which is created: `fw.save_figure(fig, "meeting-2026-10-16/cohort-flow")`.

## Models

[save_model()](ref:save_model) saves any Python object with pickle, as `<name>.pkl`.

```python
from sklearn.linear_model import LinearRegression

model = LinearRegression().fit(features, outcome)
fw.save_model(model, "regression_v1")
```

[load_model()](ref:load_model) reads it back. In a privacy-sensitive project it looks in the private models folder, then the public one; `public=True` or `public=False` picks one. A statsmodels formula result loads anywhere, including the top level of a script, where a plain `pickle.load()` fails.

```python
model = fw.load_model("regression_v1")
```

> [!WARNING]
> Loading a pickle can run arbitrary code. Load model files only from sources you trust.

Many fitted models keep the data they were fitted on. A statsmodels result holds its estimation data, for example. `save_model(..., public=True)` warns when the model holds data rows, since they would go to the public folder with it. `result.remove_data()` drops them from a statsmodels result fitted from arrays. A formula result (`smf.ols("y ~ x", data)`) keeps its data frame even then, because statsmodels needs it to rebuild the formula on load; publish its estimates with [save_table()](ref:save_table) instead.

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

Framework doesn't track which data each result was made from, but it does remember whether this Python session has read private data with [data_read()](ref:data_read): a file in a folder named like `private`, `confidential`, or `sensitive`, or a catalog entry marked `private: true`. After such a read, [save_table()](ref:save_table), [save_figure()](ref:save_figure), and [save_model()](ref:save_model) with `public=True` warn, naming what was read:

```text
FrameworkWarning: Saved summary.csv as public, and this session read private data (inputs.private.raw.visits). Check that it holds only aggregated or de-identified results before sharing it.
```

The save still happens. The warning is a reminder to check the result, not a judgment of it, and it lasts until Python restarts. When `save_model()` also warns that the model holds data rows, the one warning names the private data too.

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
