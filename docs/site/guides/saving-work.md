---
id: saving-work
title: Saving work
description: Saving tables, figures, models, reports, and notebooks with public/private routing and result tracking
section: features
position: 7
---

## Overview

Framework provides dedicated output functions for each artifact type. Each function writes to the configured output directory, records the SHA-256 digest in the `results` table of `framework.db`, and returns the saved `Path`.

| Function | Artifact | Directory key |
|----------|----------|---------------|
| [save_table()](ref:save_table) | DataFrame as CSV, Parquet, or Excel | `outputs_tables` |
| [save_figure()](ref:save_figure) | `matplotlib`, `seaborn`, or `plotnine` figure | `outputs_figures` |
| [save_model()](ref:save_model) | Fitted Python model (`pickle`) | `outputs_models` |
| [save_report()](ref:save_report) | Finished report document | `outputs_reports` |
| [save_notebook()](ref:save_notebook) | Rendered Quarto or Jupyter notebook | `outputs_notebooks` |

Existing files are overwritten by default; pass `overwrite=False` to raise an error instead.

## Tables

[save_table()](ref:save_table) writes a DataFrame without its index. Pass `name` without an extension:

```python
import framework as fw

fw.scaffold()
summary = fw.data_read("inputs.final.analysis").groupby("site").mean(numeric_only=True)

fw.save_table(summary.reset_index(), "site_summary")
fw.save_table(summary.reset_index(), "site_summary", format="parquet")
fw.save_table(summary.reset_index(), "site_summary", format="xlsx")
```

Include `/` in `name` to write into a subdirectory (created automatically), such as `fw.save_table(cohort, "meeting-2026-10-16/cohort")`.

Supported formats are `csv` (default), `parquet`, and `xlsx` (requires the `excel` extra). CSV output matches `readr::write_csv()` and [data_save()](ref:data_save): integers without `.0`, `TRUE`/`FALSE`, `NA` for missing values, and `\n` line endings across platforms. Additional keyword arguments pass through to the underlying `pandas` writer.

## Figures

[save_figure()](ref:save_figure) accepts any object with `.savefig()` (`matplotlib` or `seaborn` figures), `matplotlib` `Axes`, or `.save()` (`plotnine` plots and compositions), without requiring any plotting library as a package dependency. Passing `None` saves the active `matplotlib` figure:

```python
import matplotlib.pyplot as plt

fig, ax = plt.subplots()
ax.plot(summary.index, summary["score"])

fw.save_figure(fig, "score_by_site")
fw.save_figure(fig, "score_by_site", format="svg", width=10, height=4)
fw.save_figure(None, "current_plot")
```

Supported formats are `png` (default), `pdf`, `svg`, `eps`, `jpg`, `jpeg`, `tiff`, and `emf` (when supported by the backend; `matplotlib` raises `UnsupportedFormatError` for `emf`). Pass a list of formats to export multiple files in one call:

```python
fw.save_figure(fig, "figure_2", format=["pdf", "eps", "svg", "png"])
```

`width` and `height` default to `8` and `6` inches at `dpi=300` (matching R), resizing `matplotlib` figures only for export and restoring original dimensions afterward. Pass `width=fig.get_figwidth(), height=fig.get_figheight()` to keep a figure's existing size. Subdirectory prefixes in `name` create nested folders automatically.

## Models

[save_model()](ref:save_model) serializes a Python object with `pickle` to `<name>.pkl`:

```python
from sklearn.linear_model import LinearRegression

model = LinearRegression().fit(features, outcome)
fw.save_model(model, "regression_v1")
```

[load_model()](ref:load_model) reloads the saved object (checking `outputs_private_models` then `outputs_public_models` in sensitive projects unless `public` is specified) and handles `statsmodels` formula results cleanly at module top level:

```python
model = fw.load_model("regression_v1")
```

> [!WARNING]
> Unpickling executes arbitrary code. Load `.pkl` files only from trusted sources.

Fitted models often retain training rows (for example, `statsmodels` results). Calling `save_model(..., public=True)` warns when a model embeds estimation rows. Call `result.remove_data()` on array-fitted `statsmodels` results before saving publicly; formula-fitted `statsmodels` models retain their data frame even after `remove_data()`, so export coefficient tables with [save_table()](ref:save_table) instead.

R `.rds` models and Python `.pkl` models are language-specific and cannot be loaded across languages.

## Reports

[save_report()](ref:save_report) copies (or moves when `move=True`) a finished file into `outputs_reports`, renaming the stem while keeping the file extension:

```python
fw.save_report("draft/analysis.html", "quarterly_report")
fw.save_report("draft/analysis.html", "quarterly_report", move=True)
```

## Notebooks

[save_notebook()](ref:save_notebook) renders a `.qmd` or `.ipynb` file through Quarto into `outputs_notebooks`:

```python
fw.save_notebook("notebooks/exploration.qmd")
fw.save_notebook("notebooks/exploration.qmd", name="final_analysis", format="docx")
```

See [Quarto](guide:quarto) for rendering requirements and options.

## Public and private outputs

In `project_sensitive` projects, outputs default to `private/` (`public=False`), which git ignores and publishing functions guard. Pass `public=True` only for aggregated or de-identified artifacts:

```python
# outputs/private/tables/patient_outcomes.csv
fw.save_table(patient_outcomes, "patient_outcomes")

# outputs/public/tables/summary.csv
fw.save_table(aggregated, "summary", public=True)
```

When a session has read restricted data via [data_read()](ref:data_read) (any path under `private`, `confidential`, or `sensitive`, or a catalog entry with `private: true`), subsequent `save_table()`, `save_figure()`, and `save_model()` calls with `public=True` emit a `FrameworkWarning` naming the restricted inputs read during the session:

```text
FrameworkWarning: Saved summary.csv as public, and this session read private data (inputs.private.raw.visits). Check that it holds only aggregated or de-identified results before sharing it.
```

In non-sensitive project types, all outputs write to `outputs/<kind>/`, while still recording the `public` flag in `framework.db`.

## Output directories

Standard project defaults:

```text
outputs/
├── tables/      # outputs_tables
├── figures/     # outputs_figures
├── models/      # outputs_models
├── reports/     # outputs_reports
└── notebooks/   # outputs_notebooks
```

Privacy-sensitive project defaults (`outputs_private_*` and `outputs_public_*`):

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

Framework creates missing output directories on first save.

## Results registry

Every `save_*()` call upserts a record in `results`:

| Column | Contents |
|--------|----------|
| `name` | Project-relative path, such as `outputs/tables/site_summary.csv` |
| `type` | `table`, `figure`, `model`, `report`, or `notebook` |
| `public` | Public visibility flag |
| `hash` | SHA-256 digest |
| `created_at` | First save timestamp |
| `updated_at` | Latest save timestamp |

Query saved outputs with [result_list()](ref:result_list):

```python
fw.result_list()
fw.result_list(type="figure")
fw.result_list(type="table", public=True)
```
