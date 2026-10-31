---
name: framework-outputs
description: Use when saving analysis outputs, caching expensive computations, running database queries, or publishing files in this Framework project. Covers save_table(), save_figure(), save_model(), cache_remember(), db_query(), and publish().
---

# Framework Outputs, Caching, Queries, and Publishing

## Saving outputs

Each `save_*()` function writes to the project's output folder for that kind
of result (created on first use) and records the file, so `result_list()`
finds it later.

```python
fw.save_table(summary, "quarterly_summary")  # outputs/tables/ (csv)
fw.save_table(report, "annual_report", format="xlsx")
fw.save_figure(fig, "enrollment_trend")  # outputs/figures/ (png)
fw.save_model(model, "regression_model")  # outputs/models/ (pickle)
fw.save_notebook("notebooks/analysis.qmd")  # render with Quarto and save

fw.result_list()  # everything saved so far
fw.result_list(type="model")  # models only
```

In a sensitive project, outputs are private by default. Pass `public=True`
only for outputs that are safe to share.

## Caching

```python
model = fw.cache_remember("fitted_model", lambda: fit(training), expire_after="7 days")

fw.cache("processed", frame)  # write
frame = fw.cache_get("processed")  # read (None if missing)
fw.cache_forget("processed")  # remove
```

Cache entries are tracked in `framework.db` with a hash and an expiry. Use
names that say what the value is.

## Database queries

Connections are defined in the settings (often `settings/connections.yml`).
Use Framework's helpers so credentials come from `.env`:

```python
users = fw.db_query("SELECT * FROM users WHERE active = 1", "main_db")
fw.db_execute("UPDATE runs SET done = 1 WHERE id = ?", "main_db", params=(42,))

with fw.db_connect("main_db") as conn:
    ...
```

## Publishing

`fw.publish()` and `fw.publish_notebook()` upload to the project's storage
bucket, or to Framework Cloud when the project was set up from it:

```python
fw.publish("outputs/report.html")
fw.publish_notebook("notebooks/analysis.qmd")
```

The publish functions warn about any file with "private" in its path and ask
the user to confirm. In a script they stop with an error. Never pass
`include_private=True` or answer the prompt on the user's behalf: only the
person who has reviewed the file can decide that it is safe to publish. See
the `framework-sensitive-data` skill if this project has one.
