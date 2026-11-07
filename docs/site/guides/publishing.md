---
id: publishing
title: Publishing
description: Notebook, data, and file publishing to storage buckets, Framework Cloud, GitHub Pages, and named destinations
section: features
position: 10
---

## Overview

Framework publishes rendered notebooks, datasets, and other files so that colleagues can open them by URL. It publishes to three kinds of destination.

- **Storage buckets.** AWS S3 and S3-compatible services, such as MinIO and DigitalOcean Spaces, configured in the project's settings.
- **Framework Cloud.** The framework.pub service or a self-hosted server, for projects created from or registered with it.
- **GitHub Pages.** The project's own GitHub repository, with `to="github"`.

Every publishing function warns about files with `private` in their path and asks before it uploads them. The [Private paths](#private-paths) section explains the rule.

## Choosing a destination

[publish()](ref:publish) and [publish_notebook()](ref:publish_notebook) choose the destination in this order.

1. When you pass `connection`, or the settings define any storage bucket, they publish to a bucket.
2. Otherwise, when the project has a Framework Cloud project token, they publish to Framework Cloud.
3. Otherwise, they raise an error that shows how to add a bucket.

`to="cloud"` explicitly selects Framework Cloud, including when a bucket is configured.
`to="bucket"` explicitly selects storage, and `to="github"` selects GitHub Pages.

[storage_test()](ref:storage_test) works with buckets only. [publish_data()](ref:publish_data), [publish_dir()](ref:publish_dir), and [publish_list()](ref:publish_list) follow `to` and `publish.default` as `publish()` does (see [Named destinations](#named-destinations)), and use the bucket with an S3 destination, a `connection`, or no `publish.default`.

## Configuring a bucket

Buckets live under `connections.storage_buckets`, which standard and privacy-sensitive projects keep in `settings/connections.yml`.

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

| Option | Meaning |
|--------|---------|
| `bucket` | The bucket name |
| `region` | The AWS region. Default: `us-east-1` |
| `prefix` | A path prefix for every upload |
| `endpoint` | The address of an S3-compatible service. `http://` turns off TLS. |
| `static_hosting` | When `true`, notebooks upload as `<dest>/index.html` |
| `default` | When `true`, marks the bucket as the default |

The default bucket is the one named by `default_storage_bucket`, else the one marked `default: true`, else the first one defined. Publishing to S3 needs the `s3` extra.

```sh
uv add "framework-py[s3]"
```

### Credentials

Credentials come from the environment, usually the project's `.env` file. New projects have empty `S3_*` entries there to fill in.

```sh
# .env (ignored by git)
S3_ACCESS_KEY=AKIA...
S3_SECRET_KEY=...
```

Environment variables take precedence over values in the settings.

| Setting | Environment variables, in order |
|---------|---------------------------------|
| access key | `S3_ACCESS_KEY`, `AWS_ACCESS_KEY_ID` |
| secret key | `S3_SECRET_KEY`, `AWS_SECRET_ACCESS_KEY` |
| session token | `S3_SESSION_TOKEN`, `AWS_SESSION_TOKEN` |
| bucket | `S3_BUCKET` |
| endpoint | `S3_ENDPOINT` |
| region | `S3_REGION`, `AWS_REGION`, `AWS_DEFAULT_REGION` |

Because environment variables take precedence, a value in `S3_BUCKET` applies to every bucket in the project. Leave it empty when the settings name more than one bucket.

[storage_test()](ref:storage_test) checks that the bucket exists and the credentials reach it.

```python
import framework as fw

fw.storage_test()
fw.storage_test("minio")
```

## Publishing to a bucket

Uploaded files are public. Framework uploads with a public-read ACL, and for buckets that have ACLs turned off, the bucket's policy decides access. Each function returns the URL of what it uploaded.

### Notebooks

[publish_notebook()](ref:publish_notebook) renders a notebook with Quarto and uploads the HTML.

```python
fw.publish_notebook("notebooks/exploration.qmd")
fw.publish_notebook("notebooks/exploration.qmd", dest="reports/2026-q4")
fw.publish_notebook("notebooks/exploration.qmd", connection="minio")
```

On Framework Cloud and named destinations, the published title is the `title` in the document's YAML front matter (the first raw cell of a Jupyter notebook), else the file name. Pass `title` and `description` to set them. [publish()](ref:publish) uses the same default for a `.qmd` or `.ipynb` file.

Without static hosting, the notebook is uploaded as one self-contained file, `<dest>.html`. With `static_hosting: true`, it is uploaded as `<dest>/index.html`, and the returned URL ends in `/`.

```text
static_hosting: true   https://my-reports-bucket.s3.us-east-1.amazonaws.com/projects/vaccine-study/reports/2026-q4/
static_hosting: false  https://my-reports-bucket.s3.us-east-1.amazonaws.com/projects/vaccine-study/reports/2026-q4.html
```

On a static-hosting bucket, `self_contained=False` uploads the HTML with its supporting files instead of embedding them.

### Data

[publish_data()](ref:publish_data) uploads a DataFrame as CSV, Parquet, or JSON, or uploads an existing file.

```python
fw.publish_data(results, "datasets/results.csv")
fw.publish_data(results, "datasets/results.parquet", format="parquet")
fw.publish_data(results, "datasets/results.csv", compress=True)
fw.publish_data("outputs/public/tables/summary.csv", "datasets/summary.csv")
```

With `compress=True`, the file is gzipped and `.gz` is added to the destination.

### Folders and files

[publish_dir()](ref:publish_dir) uploads every file in a folder. `pattern` is a regular expression on the file paths, and `recursive=False` skips subfolders.

```python
fw.publish_dir("outputs/figures", dest="figures/2026-q4")
fw.publish_dir("outputs/figures", pattern=r"\.png$")
```

[publish()](ref:publish) uploads one file or a folder. A folder returns a list of URLs.

```python
fw.publish("outputs/reports/quarterly_report.html")
fw.publish("outputs/reports/quarterly_report.html", dest="reports/final.html")
```

### Listing

[publish_list()](ref:publish_list) returns the objects under the bucket's prefix with their key, size, modification time, and URL.

```python
fw.publish_list()
fw.publish_list(prefix="reports")
```

With a folder, GitHub, or Cloud destination, `publish_data()` and `publish_dir()` publish through `publish()`, with the file or folder name of `dest` as the slug, and `publish_list()` lists a folder destination's artifacts, one row per slug:

```python
fw.publish_data(results, "tables/results.csv", to="site")  # slug "results"
fw.publish_list(to="site")
```

## Publishing to Framework Cloud

A project created with `fw.new_project(key=...)`, or registered with the cloud when it was created, has `FW_PROJECT_TOKEN` and `FW_CLOUD_URL` in its `.env`. When the settings define no storage bucket, [publish()](ref:publish) and [publish_notebook()](ref:publish_notebook) send documents to that server.

```python
fw.publish_notebook("notebooks/exploration.qmd")
fw.publish("outputs/notebooks/exploration.html")
```

Framework Cloud accepts the following sources.

- An image, PDF, CSV, JSON, text, binary, or other file.
- A self-contained `.html` file.
- An `.html` file with its sibling `<name>_files/` folder, uploaded together.
- A folder of rendered output, whose `index.html` or first HTML file is the entry page.
- A `.qmd` file, which Framework renders to HTML first.

Each publish adds a new version of the document. A string `slug` names the document and leaves its visibility alone. A new artifact starts unlisted, and an existing one keeps its setting. `slug=True` turns the readable URL on with the name derived from the file, and `slug=False` turns it off. Pass `visibility="public"` to make an artifact public. `publish_notebook()` publishes HTML only when the destination is Framework Cloud.

You can also publish an object from your Python session. On Framework Cloud,
each object is converted to a format that matches its type.

- pandas and polars DataFrames and Series, and numpy arrays with one or two
  dimensions, become tables. Every row is kept, along with a summary of each
  column (missing values, distinct values, numeric statistics with a histogram,
  and the most common values), computed over all rows. An index other than the
  default becomes the first column. `pandas.crosstab()` output and
  `statsmodels` `anova_lm()` tables publish the same way.
- A pandas `Styler` publishes as a formatted HTML table.
- scipy.stats test results (`ttest_ind()`, `pearsonr()`, `chi2_contingency()`,
  `mannwhitneyu()`, `shapiro()`, and others) become a test summary with the
  method, statistic, degrees of freedom, p-value, and confidence interval when
  the result has one.
- statsmodels results (OLS, WLS, GLM, Logit, Probit, Poisson, NegativeBinomial,
  MixedLM, QuantReg, MNLogit, and others) become model summaries: coefficients
  with standard errors, p-values, and 95% Wald intervals, fit statistics, the
  formula, and the model family and link. Mixed models add their variance
  components.
- Fitted scikit-learn linear models publish their coefficients. Tree models,
  xgboost, and lightgbm models publish feature importance and their main
  settings.
- matplotlib Figures and Axes and seaborn plots and grids publish as SVG, or as
  PNG when the SVG would be larger than 5 MB. plotly Figures publish as HTML,
  and altair charts as Vega-Lite with their data included.

Model exports never include the observations a model was fit on, residuals,
fitted values, or the serialized model. Integer columns with values outside the
browser-safe range (±9,007,199,254,740,991) are exported as text to keep
identifiers exact. When the destination is a bucket, tables upload as CSV,
figures as PNG, and statsmodels results as an HTML summary.

```python
fw.publish(figure, to="cloud", slug="treatment-effect", title="Treatment effect")
fw.publish(results_table, to="cloud", slug="estimates")
fw.publish(fitted_model, to="cloud", slug="adjusted-model", password="reviewer-password")
```

`title`, `description`, `kind`, `password`, and `visibility` set artifact metadata.
Passwords require at least eight characters. Visibility can be `"public"`, `"unlisted"`, or `"private"`.
Anyone with an unlisted link can open it unless it is password protected.
An explicit `visibility` always wins. Without one, `slug=True` makes the document public
and `slug=False` makes it unlisted. A string slug changes nothing: a new artifact is unlisted,
and an existing one keeps its visibility.

A public artifact gets a readable URL, `/@handle/project/slug`, once you set a handle in
Account > Account details. Until then its share link works.

Publishing sends only the document you name. The [Framework Cloud](guide:cloud) guide describes what else the cloud receives.

## Publishing to GitHub Pages

`to="github"` publishes a page to the project's GitHub Pages site. The documents go from your computer to GitHub and never pass through Framework Cloud.

```python
fw.publish("outputs/report.html", to="github")
fw.publish_notebook("notebooks/analysis.qmd", to="github", slug="main-results")
```

GitHub Pages sites are public, even when the repository is private. Every run prints that notice, and an interactive session asks `Publish it? [y/N]` before it changes anything. A script goes ahead without asking, because the call itself is the explicit choice. The [private-path check](#private-paths) still applies.

Before the first publish, turn on "Publish with GitHub Pages" for the project under Projects on Framework Cloud. That turns on Pages for the repository and commits a workflow that deploys the repository's `published/` folder from the default branch.

Each publish takes these steps.

1. Find the project's git repository and its GitHub remote: the branch's upstream remote, then `origin`, then any other GitHub remote. Without one, it raises an error that says to create the project on GitHub first.
2. Build a portable bundle, as for the other [named destinations](#named-destinations). A `.qmd` or `.ipynb` file is rendered to HTML first. An `.html` file's `<name>_files/` folder is copied beside it under the same name, so relative links keep working.
3. Keep every publish. Each one is copied to `published/<slug>/v/<number>/`, and the latest one also sits at `published/<slug>/`. The site index, `published/index.html` and `published/index.json`, lists every artifact.
4. Commit only `published/<slug>/` and the site index, with the message `Publish <slug>`. Anything else you have staged stays staged and out of the commit. When nothing changed, it reports that and neither commits nor pushes.
5. Push. A branch with no upstream is pushed with `git push -u <remote> HEAD`. On a branch other than the default branch, it warns that the page does not appear until the branch is merged.
6. Return and report the URL.

The slug defaults to the file name. Framework lowercases it and replaces each run of other characters with `-`, so `Final Report (v2).html` becomes `final-report-v2`. An explicit slug is changed the same way: `slug="Main Results"` publishes to `main-results`. `dest`, `connection`, and `slug=True` or `slug=False` apply only to other destinations and raise an error with `to="github"`.

A project with a Framework Cloud project token asks the cloud for the site's URL. When the cloud reports that Pages is off, publishing still commits and pushes, and it warns you to turn Pages on. Otherwise the URL comes from the remote: `https://<owner>.github.io/<repo>/<slug>/`, or `https://<owner>.github.io/<slug>/` for a repository named `<owner>.github.io`.

## Private paths

Publishing happens only when you call a publishing function, and framework-py never uploads anything on its own. Every publishing function checks the full path of each file it is about to upload. A path that contains `private` in any letter case, such as `outputs/private/tables/detail.csv` or `~/Private/notes.html`, gets a warning that lists the files.

In an interactive session, the function then asks before it uploads anything. Answer `y` to upload, and anything else cancels. A session with no terminal, such as a script, a scheduled job, CI, or a notebook run by an assistant, cannot answer, so the function raises an error instead.

```text
Warning: 1 file(s) to upload have 'private' in their path:
  /home/me/study/outputs/private/tables/detail.csv
FrameworkError: Not uploading files with 'private' in their path without confirmation.
Review them, then pass include_private=True to upload them.
```

`include_private=True` uploads without asking. Only a person who has reviewed the files should pass it. The older name `allow_private` still works but warns that it is deprecated. An object has no path, so the check does not apply to a DataFrame, model, or figure passed to `publish()` or `publish_data()`, even one made from private data. Review it before you publish it.

On macOS, the system resolves `/tmp` and `/var` to paths under `/private/`. That leading `/private/` does not count. A folder you named `private` always counts.

In privacy-sensitive projects, save shareable results with `public=True` and publish them from the public folders.

```python
fw.save_table(aggregated, "summary", public=True)
fw.publish_data("outputs/public/tables/summary.csv", "datasets/summary.csv")
```

## Organizing published files

A bucket `prefix` per project keeps a shared bucket organized.

```text
team/vaccine-study/reports/...
team/vaccine-study/datasets/...
```

Dates or versions in destination names keep earlier releases available.

```python
fw.publish_notebook("notebooks/exploration.qmd", dest="reports/exploration-2026-10-31")
```


## Adding an artifact to a collection

Select an existing collection in the current project by its ID or slug. The
client resolves the collection before uploading and does not create collections.
The collection follows the artifact's latest version.

```python
fw.publish("outputs/effect.png", to="cloud", collection="main-findings", share_unlisted=True)
```

Public collections require public artifacts. Sharing an unlisted artifact with
readers of an unlisted collection requires explicit consent through
`share_unlisted`.
If attachment fails after the artifact uploads, publishing returns its URL and
warns that collection attachment failed. Attach the artifact from the project
Outputs page. Publishing again creates another version.

## Named destinations

`publish()` and `publish_notebook()` accept destination names from project settings.
An explicit `to` takes priority over `publish.default`. Without either, Framework
keeps the existing bucket-first, then Cloud behavior. An explicit `connection`
selects the legacy bucket path.

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

A folder `path` may start with `~`. A relative path starts at the project root, even
when a notebook runs from `notebooks/`. Publishing to a folder or a named S3 destination
prints the version and where it went.

Folder, GitHub, and named S3 destinations receive portable bundles. Each bundle
contains `artifact.json`, an offline `index.html`, the original export, and typed
`representation.json` when available. Tables also retain a CSV export. The reader
works from a local file without PHP or a network connection. Bundle manifests list
each file's byte count and SHA-256 checksum.

Folder and GitHub sites retain every publish under `<slug>/v/<number>/`. The
artifact URL shows the latest version. Their site indexes list published artifacts.
Named S3 destinations retain the same version layout and an artifact index.
The index of a private destination stays private. A public index lists only public artifacts.
GitHub Pages warns above 800 MB and rejects sites above 1 GB before committing
or pushing. The size includes retained versions and the rest of the published site.

Static destinations reject passwords, collections, and unlisted access before
delivery. Folder access follows the share's permissions. Configure its `visibility`
to match the share before requesting explicit public or private access. Private
S3 publishing requires `visibility: private` in the destination. `private: true`
is a compatible alias. Framework verifies that the bucket policy is not public
before uploading private artifacts. Public policies and unavailable policy checks
reject the publish. GitHub private Pages requires a verified private Pages
setting from GitHub. Repository privacy alone is insufficient. Verification uses
an authenticated `gh` command.

Connected projects record external publication metadata in Cloud after delivery.
Artifact contents and folder paths are never included. A tracking failure produces
a warning and preserves the delivered artifact.

Legacy bucket uploads, including `to="bucket"`, retain their existing raw-file
layout. `publish_data()` and `publish_dir()` upload to the bucket file by file with an S3
destination, a `connection`, or no `publish.default`.

For legacy raw bucket uploads, connected projects record metadata
with version 1 and a content checksum. A conflicting replacement produces a tracking
warning after delivery. Use a named S3 destination for retained versions and portable bundles.

Publishing destination credentials must use environment references such as
`env("RESULTS_ACCESS_KEY")`. Literal credentials are rejected.
