---
id: publishing
title: Publishing
description: Publishing notebooks, data, and files to S3-compatible storage or Framework Cloud
section: features
position: 10
---

## Overview

Framework publishes rendered notebooks, datasets, and other files so that colleagues can open them by URL. It publishes to two kinds of destination.

- **Storage buckets.** AWS S3 and S3-compatible services, such as MinIO and DigitalOcean Spaces, configured in the project's settings.
- **Framework Cloud.** The framework.pub service or a self-hosted server, for projects created from or registered with it.

Every publishing function refuses files inside a `private` folder of the project. The [Private Folders](#private-folders) section explains the rule.

## Choosing a Destination

[publish()](ref:publish) and [publish_notebook()](ref:publish_notebook) choose the destination in this order.

1. When you pass `connection`, or the settings define any storage bucket, they publish to a bucket.
2. Otherwise, when the project has a Framework Cloud project token, they publish to Framework Cloud.
3. Otherwise, they raise an error that shows how to add a bucket.

[publish_data()](ref:publish_data), [publish_dir()](ref:publish_dir), [publish_list()](ref:publish_list), and [storage_test()](ref:storage_test) work with buckets only.

## Configuring a Bucket

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

## Publishing to a Bucket

Uploaded files are public. Framework uploads with a public-read ACL, and for buckets that have ACLs turned off, the bucket's policy decides access. Each function returns the URL of what it uploaded.

### Notebooks

[publish_notebook()](ref:publish_notebook) renders a notebook with Quarto and uploads the HTML.

```python
fw.publish_notebook("notebooks/exploration.qmd")
fw.publish_notebook("notebooks/exploration.qmd", dest="reports/2026-q4")
fw.publish_notebook("notebooks/exploration.qmd", connection="minio")
```

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

### Folders and Files

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

## Publishing to Framework Cloud

A project created with `fw.new(key=...)`, or registered with the cloud when it was created, has `FW_PROJECT_TOKEN` and `FW_CLOUD_URL` in its `.env`. When the settings define no storage bucket, [publish()](ref:publish) and [publish_notebook()](ref:publish_notebook) send documents to that server.

```python
fw.publish_notebook("notebooks/exploration.qmd")
fw.publish("outputs/notebooks/exploration.html")
```

Framework Cloud accepts four kinds of source.

- A self-contained `.html` file.
- An `.html` file with its sibling `<name>_files/` folder, uploaded together.
- A folder of rendered output, whose `index.html` or first HTML file is the entry page.
- A `.qmd` file, which Framework renders to HTML first.

Each publish adds a new version of the document. The `slug` argument names the document and turns on its readable URL. `slug=True` uses the name derived from the file, and `slug=False` turns the readable URL off. `publish_notebook()` publishes HTML only when the destination is Framework Cloud.

Publishing sends only the document you name. The [Framework Cloud](guide:cloud) guide describes what else the cloud receives.

## Private Folders

Every publishing function refuses a source inside a folder named `private` within the project, such as `outputs/private/tables/` or `inputs/private/raw/`. Those folders hold restricted data.

```text
FrameworkError: Refusing to publish 'outputs/private/tables/detail.csv': it is inside a
private folder, which holds restricted data. Pass allow_private=True if you really mean
to publish it.
```

`allow_private=True` overrides the check. Only a person who has reviewed the file should pass it. A DataFrame passed to `publish_data()` has no path, so the check does not apply to it. Review the data before you publish it.

In privacy-sensitive projects, save shareable results with `public=True` and publish them from the public folders.

```python
fw.save_table(aggregated, "summary", public=True)
fw.publish_data("outputs/public/tables/summary.csv", "datasets/summary.csv")
```

## Organizing Published Files

A bucket `prefix` per project keeps a shared bucket organized.

```text
team/vaccine-study/reports/...
team/vaccine-study/datasets/...
```

Dates or versions in destination names keep earlier releases available.

```python
fw.publish_notebook("notebooks/exploration.qmd", dest="reports/exploration-2026-10-31")
```
