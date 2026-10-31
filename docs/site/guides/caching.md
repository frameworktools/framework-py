---
id: caching
title: Caching
description: Caching the results of expensive computations between sessions
section: features
position: 8
---

## Overview

Framework's cache saves the result of an expensive computation so later sessions can reuse it. A cached value is stored as a file in the project's cache folder and recorded in the `cache` table of `framework.db` with its hash and an optional expiry time.

DataFrames are stored as Parquet. Other values are stored with pickle.

> [!WARNING]
> Loading a pickle can run arbitrary code. Load caches only from projects you trust.

## The Cache Folder

The cache folder is chosen in this order.

1. The `FW_CACHE_DIR` environment variable, relative to the project root unless absolute.
2. `directories.cache` in the settings.
3. `options.data.cache_dir` in the settings.
4. `outputs/cache`.

The project types set these defaults.

| Project type | Cache folder |
|--------------|--------------|
| Standard | `outputs/cache/` |
| Privacy-sensitive | `outputs/private/cache/` |
| Course | `cache/` |
| Presentation | `cache/` |

The folder is created the first time a value is cached. Every project type's `.gitignore` excludes `cache/` folders.

## Remembering a Result

[cache_remember()](ref:cache_remember) returns the cached value when one exists. Otherwise it calls a function, caches the result, and returns it. The function takes no arguments.

```python
import framework as fw

fw.scaffold()


def fit_model():
    survey = fw.data_read("inputs.final.analysis")
    return expensive_fit(survey)


model = fw.cache_remember("survey_model", fit_model)
```

A lambda works for short expressions.

```python
counts = fw.cache_remember("site_counts", lambda: survey.groupby("site").size().reset_index())
```

`cache_remember()` also works as a decorator. The decorated function takes no arguments, and calling it returns the cached value when one exists.

```python
@fw.cache_remember("survey_clean", expire_after="1 week")
def survey_clean():
    return fw.data_read("inputs.raw.survey").dropna()


clean = survey_clean()
```

## Expiration

`expire_after` sets how long a value stays valid. It takes a number of hours or a string with a unit.

```python
fw.cache_remember("exchange_rates", fetch_rates, expire_after=1)
fw.cache_remember("daily_extract", run_extract, expire_after="1 day")
fw.cache_remember("weekly_model", fit_model, expire_after="2 weeks")
```

The units are `hour`, `hours`, `hr`, `hrs`, and `h`; `day`, `days`, and `d`; and `week`, `weeks`, and `w`. A value without an expiry stays valid until it is removed. `options.data.cache_default_expire` in the settings sets an expiry for values that do not give one.

An expired value is removed when it is next looked up, and `cache_remember()` computes it again.

## Refreshing

`refresh=True` removes the cached value and computes it again.

```python
model = fw.cache_remember("survey_model", fit_model, refresh=True)
```

`refresh` can also be a function that returns `True` or `False`, which decides at call time.

```python
import os

model = fw.cache_remember(
    "survey_model",
    fit_model,
    refresh=lambda: os.environ.get("REFIT") == "1",
)
```

## Storing and Retrieving Values

[cache()](ref:cache) stores a value and returns it. [cache_get()](ref:cache_get) returns a stored value, or `default` when the value is missing, expired, or unreadable.

```python
fw.cache("site_lookup", lookup, expire_after=24)

lookup = fw.cache_get("site_lookup")
lookup = fw.cache_get("site_lookup", default={})
```

The `file` argument of these functions stores the value in a specific file instead of the cache folder.

## Removing Values

[cache_forget()](ref:cache_forget) removes one value and its record. [cache_flush()](ref:cache_flush) removes every cached file in the cache folder and every record.

```python
fw.cache_forget("site_lookup")
fw.cache_flush()
```

## Listing the Cache

[cache_list()](ref:cache_list) returns every cache record with its expiry, creation, update, and last-read times, and a `status` of `active` or `expired`.

```python
fw.cache_list()
```

## Integrity

Framework records each cached file's SHA-256 hash. When a file no longer matches its hash, the lookup issues a `CacheWarning` and treats the value as missing, so `cache_remember()` computes it again.

## Caches Shared with R

R and Python share the `cache` table. R stores values as `.rds` files, which Python cannot read. A name that R cached is a miss in Python, with a `CacheWarning`, and R's file is left in place. `cache_forget()` and `cache_flush()` in Python remove entries from either language.

## When to Cache

Cache computations that take long enough to slow down iteration, such as model fits, large joins, and database or API queries. Use descriptive names, such as `sales_2024_by_region`, so that `cache_list()` shows what each entry holds. Give values that come from outside sources an expiry, so they are fetched again.
