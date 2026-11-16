---
id: caching
title: Caching
description: Persistent computation caching with dependency hashing, expiration windows, and integrity checks
section: features
position: 8
---

## Overview

Framework caches expensive computations on disk so subsequent sessions reuse results without recomputing them. DataFrames serialize as Parquet, and other Python objects serialize with `pickle`, with SHA-256 digests and optional expiration times tracked in `framework.db`.

> [!WARNING]
> Unpickling executes arbitrary code. Load cached objects only from trusted projects.

## Cache directory resolution

Framework resolves the cache directory in priority order:

1. `FW_CACHE_DIR` environment variable (project-relative unless absolute).
2. `directories.cache` in `settings.yml`.
3. `options.data.cache_dir` in `settings.yml`.
4. `outputs/cache`.

Default cache directories by project type:

| Project type | Cache directory |
|--------------|-----------------|
| Standard | `outputs/cache/` |
| Privacy-sensitive | `outputs/private/cache/` |
| Course | `cache/` |
| Presentation | `cache/` |

Framework creates the directory on first write, and `.gitignore` excludes all `cache/` directories.

## Compute-once caching

[cache_remember()](ref:cache_remember) returns an existing valid cache entry or executes a zero-argument callable, caches the return value, and returns it:

```python
import framework as fw

fw.scaffold()


def fit_model():
    survey = fw.data_read("inputs.final.analysis")
    return expensive_fit(survey)


model = fw.cache_remember("survey_model", fit_model)
```

Short computations can pass a `lambda`:

```python
counts = fw.cache_remember("site_counts", lambda: survey.groupby("site").size().reset_index())
```

`cache_remember()` also works as a decorator on zero-argument functions:

```python
@fw.cache_remember("survey_clean", expire_after="1 week")
def survey_clean():
    return fw.data_read("inputs.raw.survey").dropna()


clean = survey_clean()
```

## Dependency invalidation

Pass `depends_on` to invalidate a cached entry automatically whenever upstream datasets, source files, or parameter values change:

```python
fit = fw.cache_remember(
    "lpa_fit",
    lambda: fit_lpa(fw.data_read("inputs.final.cohort"), k=4),
    depends_on=["inputs.final.cohort", "functions/lpa.py", {"k": 4}],
)
```

`depends_on` accepts a single item or a list (order-independent):

| Input | Hash source |
|-------|-------------|
| Catalog key (`"inputs.final.cohort"`) | Target dataset's SHA-256 digest |
| Project-relative file (`"functions/lpa.py"`) | File SHA-256 digest |
| Project-relative directory (`"functions"`) | Path and SHA-256 of every non-hidden file (excluding `__pycache__`) |
| Arbitrary Python object (`{"k": 4}`, scalar, DataFrame) | Value digest |

Bare strings always resolve as catalog keys, files, or directories and raise an error when unmatched, preventing typos from silently hashing as literal strings. Wrap literal string parameters in a dictionary such as `{"rule": ">= 0"}`.

Stored file and catalog dependencies are also recorded in `framework.db` (`meta` key `cache_depends:<name>`). Even if a later [cache_get()](ref:cache_get) or `cache_remember()` call omits `depends_on`, Framework rechecks the recorded files and emits `CacheWarning` on mismatch; `cache_get()` then returns `default`, and `cache_remember()` recomputes. [cache()](ref:cache) also accepts `depends_on`.

## Expiration

Pass `expire_after` as hours (numeric) or a duration string:

```python
fw.cache_remember("exchange_rates", fetch_rates, expire_after=1)
fw.cache_remember("live_counts", count_rows, expire_after="30 minutes")
fw.cache_remember("daily_extract", run_extract, expire_after="1 day")
fw.cache_remember("weekly_model", fit_model, expire_after="2 weeks")
```

Supported units: `second`/`seconds`/`sec`/`secs`/`s`, `minute`/`minutes`/`min`/`mins`, `hour`/`hours`/`hr`/`hrs`/`h`, `day`/`days`/`d`, and `week`/`weeks`/`w`. Entries without `expire_after` use `options.data.cache_default_expire` when configured or persist until removed. Expired entries delete automatically on next lookup.

## Forced refresh

Pass `refresh=True` (or a zero-argument predicate returning `bool`) to discard an existing entry and recompute:

```python
import os

model = fw.cache_remember("survey_model", fit_model, refresh=True)
model = fw.cache_remember(
    "survey_model",
    fit_model,
    refresh=lambda: os.environ.get("REFIT") == "1",
)
```

## Direct read, write, and eviction

- [cache()](ref:cache) writes a value directly and returns it.
- [cache_get()](ref:cache_get) reads a cached value or returns `default` (`None`) when missing, expired, or modified. Pass `file=` to target a custom path outside the cache directory.
- [cache_forget()](ref:cache_forget) deletes one entry and its database row.
- [cache_flush()](ref:cache_flush) deletes all cached files and records.
- [cache_list()](ref:cache_list) returns a DataFrame of cache entries with timestamps and `status` (`active` or `expired`).

```python
fw.cache("site_lookup", lookup, expire_after=24)
lookup = fw.cache_get("site_lookup", default={})
fw.cache_list()
fw.cache_forget("site_lookup")
fw.cache_flush()
```

## Integrity and R interoperability

Framework verifies each cache file's SHA-256 digest on read; modified files emit `CacheWarning` and trigger recomputation.

R and Python share the `cache` table and `cache_depends:<name>` metadata, while R serializes cache files as `.rds`. Looking up an R `.rds` entry from Python emits `CacheWarning` and treats it as a miss without deleting R's file. `cache_forget()` and `cache_flush()` remove entries written by either language.
