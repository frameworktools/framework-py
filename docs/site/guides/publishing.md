---
id: publishing
title: Publishing
description: Publishing notebooks, datasets, figures, models, and files to storage buckets, Framework Cloud, GitHub Pages, and named destinations
section: features
position: 10
---

## Overview

Framework publishes rendered notebooks, datasets, models, figures, and files to shareable URLs across three destination types:

- **Storage buckets:** AWS S3 and S3-compatible services (such as MinIO or DigitalOcean Spaces) configured under `connections.storage_buckets`.
- **Framework Cloud:** `framework.pub` (1 GB free artifact storage) or a self-hosted Framework Cloud server.
- **GitHub Pages:** the project's GitHub repository via `to="github"`.

Every publishing function checks file paths and warns before uploading from restricted folders. See [Private paths](#private-paths).

## Destination selection

Without an explicit `to=` or `publish.default`, [publish()](ref:publish) and [publish_notebook()](ref:publish_notebook) resolve destinations in order:

1. Storage bucket when `connection` is passed or `connections.storage_buckets` defines at least one bucket.
2. Framework Cloud when `FW_PROJECT_TOKEN` is set.
3. Error explaining how to configure a destination.

Pass `to="cloud"` to target Framework Cloud even when buckets are configured, `to="bucket"` for raw bucket uploads, `to="github"` for GitHub Pages, or a destination key from `publish.destinations` (see [Named destinations](#named-destinations)).

[storage_test()](ref:storage_test) tests S3 buckets only. [publish_data()](ref:publish_data), [publish_dir()](ref:publish_dir), and [publish_list()](ref:publish_list) honor `to` and `publish.default`.

## Bucket configuration

Configure buckets under `connections.storage_buckets` (stored in `settings/connections.yml` in standard and privacy-sensitive projects):

```yaml
connections:
  default_storage_bucket: reports
  storage_buckets:
    reports:
      bucket: my-reports-bucket
      region: us-east-1
      prefix: projects/vaccine-study
      static_hosting: true
    minio:
      bucket: shared
      endpoint: https://minio.example.org:9000
```

| Field | Meaning |
|-------|---------|
| `bucket` | S3 bucket name |
| `region` | AWS region (`us-east-1` default) |
| `prefix` | Key prefix prepended to every upload |
| `endpoint` | Custom S3-compatible URL (`http://` disables TLS) |
| `static_hosting` | Uploads notebooks to `<dest>/index.html` when `true` |
| `default` | Marks the bucket as default when `default_storage_bucket` is omitted |

Install the `s3` extra for S3 uploads:

```sh
uv add "framework-py[s3]"
```

### S3 credentials

Store credentials in `.env`:

```sh
# .env (gitignored)
S3_ACCESS_KEY=AKIA...
S3_SECRET_KEY=...
```

Environment variables override bucket settings in order:

| Setting | Environment variables |
|---------|-----------------------|
| Access key | `S3_ACCESS_KEY`, `AWS_ACCESS_KEY_ID` |
| Secret key | `S3_SECRET_KEY`, `AWS_SECRET_ACCESS_KEY` |
| Session token | `S3_SESSION_TOKEN`, `AWS_SESSION_TOKEN` |
| Bucket | `S3_BUCKET` |
| Endpoint | `S3_ENDPOINT` |
| Region | `S3_REGION`, `AWS_REGION`, `AWS_DEFAULT_REGION` |

Leave `S3_BUCKET` unset in `.env` when configuring multiple buckets so it does not override per-bucket names. Verify connectivity with [storage_test()](ref:storage_test):

```python
import framework as fw

fw.storage_test()
fw.storage_test("minio")
```

## Bucket publishing

Raw bucket uploads apply a `public-read` ACL (falling back to bucket policy when ACLs are disabled) and return the uploaded URL.

### Notebooks

[publish_notebook()](ref:publish_notebook) renders `.qmd` or `.ipynb` files through Quarto and uploads the HTML output:

```python
fw.publish_notebook("notebooks/exploration.qmd")
fw.publish_notebook("notebooks/exploration.qmd", dest="reports/2026-q4")
fw.publish_notebook("notebooks/exploration.qmd", connection="minio")
```

With `static_hosting: false`, notebooks upload as self-contained `<dest>.html`. With `static_hosting: true`, they upload as `<dest>/index.html` (returning a trailing-slash URL), and `self_contained=False` uploads external `<name>_files/` assets alongside `index.html`.

### Data, files, and directories

[publish_data()](ref:publish_data) uploads a DataFrame (`csv`, `parquet`, or `json`) or an existing data file (`compress=True` gzips the upload and appends `.gz`):

```python
fw.publish_data(results, "datasets/results.csv")
fw.publish_data(results, "datasets/results.parquet", format="parquet")
fw.publish_data(results, "datasets/results.csv", compress=True)
fw.publish_data("outputs/public/tables/summary.csv", "datasets/summary.csv")
```

[publish_dir()](ref:publish_dir) uploads matching files in a directory (`pattern` filters relative paths by regex; `recursive=False` skips subdirectories), and [publish()](ref:publish) uploads a single file or directory:

```python
fw.publish_dir("outputs/figures", dest="figures/2026-q4")
fw.publish_dir("outputs/figures", pattern=r"\.png$")
fw.publish("outputs/reports/quarterly_report.html", dest="reports/final.html")
```

[publish_list()](ref:publish_list) lists bucket objects or folder-destination artifacts:

```python
fw.publish_list()
fw.publish_list(prefix="reports")
```

When targeting a folder, GitHub Pages, or Framework Cloud destination, `publish_data()` and `publish_dir()` delegate to `publish()` using a slug derived from `dest` (`tables/results.csv` becomes `tables-results`).

## Framework Cloud publishing

Projects linked via `FW_PROJECT_TOKEN` in `.env` publish artifacts directly to Framework Cloud (`framework.pub` includes 1 GB of free artifact storage):

```python
fw.publish_notebook("notebooks/exploration.qmd")
fw.publish("outputs/notebooks/exploration.html")
```

Supported file inputs:

- Self-contained `.html` files or `.html` files paired with sibling `<name>_files/` asset directories
- Directories of rendered output (`index.html` or first HTML file serves as entry point)
- `.qmd` and `.ipynb` notebooks (rendered to HTML before upload)
- Images, PDFs, CSV, JSON, text, and binary files

`publish()` also converts live Python objects into structured interactive representations without uploading raw training rows, residuals, fitted values, or pickled model objects:

- **Tables:** `pandas` and `polars` DataFrames/Series, 1D/2D `numpy` arrays, `pandas.crosstab()`, and `statsmodels` `anova_lm()` tables export all rows plus column profiles (missing counts, distinct counts, numeric histograms, and top values). Integers outside `±9,007,199,254,740,991` serialize as strings to preserve exact IDs. `pandas` `Styler` objects export as formatted HTML tables.
- **Hypothesis tests:** `scipy.stats` results (`ttest_ind()`, `pearsonr()`, `chi2_contingency()`, `mannwhitneyu()`, `shapiro()`, and others) export method, test statistic, degrees of freedom, p-value, and confidence intervals.
- **Statistical and ML models:** `statsmodels` results (OLS, WLS, GLM, Logit, Probit, Poisson, NegativeBinomial, MixedLM, QuantReg, MNLogit, and others) export coefficients, standard errors, p-values, 95% Wald intervals, fit statistics, formula, family/link, and variance components. Fitted `scikit-learn` linear models export coefficients; tree, `xgboost`, and `lightgbm` models export feature importances and hyperparameters.
- **Figures:** `matplotlib` Figures/Axes and `seaborn` plots/grids export as SVG (or PNG above 5 MB), `plotly` Figures export as HTML, and `altair` charts export as Vega-Lite JSON.

```python
fw.publish(figure, to="cloud", slug="treatment-effect", title="Treatment effect")
fw.publish(results_table, to="cloud", slug="estimates")
fw.publish(fitted_model, to="cloud", slug="adjusted-model", password="reviewer-password")
```

Each publish creates a new artifact version. Metadata arguments include `title`, `description`, `kind`, `password` (minimum 8 characters), and `visibility` (`"public"`, `"unlisted"`, or `"private"`). New string-slug artifacts default to `"unlisted"`, existing artifacts retain their visibility unless `visibility` is set, `slug=True` enables a public slug derived from the filename, and `slug=False` reverts to unlisted. Public artifacts resolve at `/@handle/project/slug` once a handle is set under **Account > Account details**.

### Collection attachment

Pass `collection` (slug or ID) to attach a published artifact to an existing Framework Cloud collection:

```python
fw.publish("outputs/effect.png", to="cloud", collection="main-findings", share_unlisted=True)
```

Public collections require public artifacts, and attaching an unlisted artifact to an unlisted collection requires `share_unlisted=True`. If collection attachment fails after upload, `publish()` warns and returns the artifact URL so you can attach it from the project **Artifacts** tab.

## GitHub Pages publishing

`to="github"` publishes portable HTML bundles directly from your machine to the repository's GitHub Pages site without sending content through Framework Cloud:

```python
fw.publish("outputs/report.html", to="github")
fw.publish_notebook("notebooks/analysis.qmd", to="github", slug="main-results")
```

GitHub Pages sites are public even on private repositories (unless GitHub Enterprise private Pages is verified via `gh`). Interactive sessions print that notice and prompt `Publish it? [y/N]` before modifying files; non-interactive scripts proceed automatically while still enforcing [private-path checks](#private-paths). Enable **Publish with GitHub Pages** on the project page in Framework Cloud before first publish to provision Pages and commit the deployment workflow for `published/`.

Each `to="github"` call:

1. Resolves the GitHub remote (branch upstream, `origin`, or another GitHub remote).
2. Renders `.qmd`/`.ipynb` inputs and builds a portable bundle under `published/<slug>/v/<number>/` and `published/<slug>/`, updating `published/index.html` and `published/index.json`.
3. Rejects sites exceeding 1 GB (warning above 800 MB).
4. Commits only `published/<slug>/` and the site index (`Publish <slug>`), leaving unrelated staged changes untouched, and pushes to the remote.

## Private paths

Publishing never runs automatically, and every publishing call checks candidate paths before upload. A path counts as private when:

- Any directory segment equals `private` or ends with `_private` (case-insensitive, ignoring macOS's `/private/tmp` and `/private/var` system prefixes), or
- A configured `directories` key contains `private` without `public` (such as `outputs_private_tables: results/held`).

Two additional checks trigger the same confirmation prompt:

- **Notebooks referencing private data:** `.qmd` and `.ipynb` code cells containing string literals for private paths (`"inputs/private/raw/ids.csv"`) or private catalog keys (`"inputs.private.raw.ids"`).
- **Sensitive projects:** any notebook or in-memory object (`DataFrame`, model, or figure) published from a project with `security.sensitive: true`.

Interactive sessions warn and prompt `[y/N]`; non-interactive sessions raise `FrameworkError` unless `include_private=True` is passed after human review:

```text
Warning: 1 file(s) to upload are in private folders:
  /home/me/study/outputs/private/tables/detail.csv
FrameworkError: Not uploading files from private folders without confirmation.
Review it, then pass include_private=True to upload it.
```

In sensitive projects, save reviewed outputs with `public=True` and publish from `outputs/public/`:

```python
fw.save_table(aggregated, "summary", public=True)
fw.publish_data("outputs/public/tables/summary.csv", "datasets/summary.csv")
```

## Named destinations

Define reusable destinations under `publish` in `settings.yml`:

```yaml
publish:
  default: lab
  destinations:
    lab:
      driver: folder
      path: /Volumes/research/published
      url: https://research.example.org/published
      visibility: public
    pages:
      driver: github
    cloud:
      driver: cloud
    results:
      driver: s3
      bucket: reports
```

```python
fw.publish(table, to="lab", slug="study-table", title="Study table")
fw.publish(model, to="pages", slug="adjusted-model")
```

Folder, GitHub, and named S3 destinations write self-contained portable bundles (`artifact.json` with byte sizes and SHA-256 checksums, offline `index.html`, original export, CSV for tables, and `representation.json`), retaining versions under `<slug>/v/<number>/` alongside a site index. Destination credentials must use `env("...")` references rather than literal secrets.

Static destinations reject `password`, `collection`, and `"unlisted"` visibility. Private S3 destinations require `visibility: private` (verified against bucket policy before upload), and linked projects record publication metadata (excluding artifact contents and local paths) on Framework Cloud after delivery.

### Site-root folder layout

Set `layout: site-root` on a `folder` destination to write only the latest artifact directly at the root directory (such as `docs/index.html` for GitHub Pages) without `<slug>/` subdirectories, version history, or a multi-artifact index:

```yaml
publish:
  destinations:
    pages:
      driver: folder
      path: docs
      url: https://me.github.io/my-talk
      visibility: public
      layout: site-root
```

```python
fw.publish_notebook("presentation.qmd", to="pages")
```

`site-root` tracks its managed files in `.framework-site.json` so subsequent publishes clean up prior assets while leaving manual files such as `CNAME` or `.nojekyll` untouched. Set `visibility: public` on committed site folders so `.gitignore` and `git_security_audit()` allow git tracking.
