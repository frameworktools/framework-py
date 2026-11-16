---
id: framework-db
title: framework.db
description: SQLite tracking database for data hashes, cached values, and saved outputs
section: features
position: 2
---

## Overview

Every Framework project maintains a SQLite database named `framework.db` in its root directory. Framework uses it to record SHA-256 hashes for cataloged datasets, cache metadata and expiration timestamps, and saved output records.

Framework functions such as [data_read()](ref:data_read), [cache_remember()](ref:cache_remember), and [save_table()](ref:save_table) update `framework.db` automatically. [scaffold()](ref:scaffold), [new_project()](ref:new_project), and [bootstrap()](ref:bootstrap) create or migrate it when missing.

`framework-py` and the R package share the exact SQLite schema, so data hashes recorded in one language verify in the other.

## Schema tables

| Table | Contents |
|-------|----------|
| `data` | Catalog key, relative path, format, delimiter, `locked` flag, and SHA-256 hash per dataset |
| `cache` | Cache key, backing file path, SHA-256 hash, and expiration timestamp |
| `results` | Relative output path, output type, `public` flag, and SHA-256 hash |
| `connections` | Database connection metadata in the shared schema |
| `meta` | Key-value state such as scaffold timestamps, cache dependency manifests, and audit history |

All stored file paths are relative to the project root, keeping `framework.db` portable across directory moves.

## Data integrity tracking

The `data` table stores the reference SHA-256 digest for each cataloged file. Calling [data_read()](ref:data_read) on a catalog name records the hash on first read and compares the current file against it on later reads:

```python
import framework as fw

fw.scaffold()
survey = fw.data_read("inputs.raw.survey")
```

Modified unlocked files emit `DataChangedWarning` and update the stored hash. Modified files marked `locked: true` raise `IntegrityError` before reading any rows. [data_add()](ref:data_add) and [data_save()](ref:data_save) record hashes immediately upon registration. See [Data management](guide:data-management).

## Cache metadata

The `cache` table tracks each cached file, its SHA-256 digest, and its expiration time. [cache_get()](ref:cache_get) and [cache_remember()](ref:cache_remember) check those records to detect missing, expired, or modified cache entries. See [Caching](guide:caching).

## Results registry

Each `save_*()` call inserts or updates a row in `results` keyed by project-relative path (such as `outputs/tables/summary.csv`), recording output type, `public` flag, and SHA-256 digest. [result_list()](ref:result_list) returns rows newest first:

```python
fw.result_list()
fw.result_list(type="figure")
fw.result_list(type="table", public=True)
```

Legacy entries keyed by bare filename upgrade to project-relative paths on their next save. See [Saving work](guide:saving-work).

## Direct SQL queries

Every project defines a `framework` database connection pointing to `framework.db`. Query it with [db_query()](ref:db_query):

```python
fw.db_query("SELECT name, expire_at FROM cache", "framework")
fw.db_query("SELECT name, path, hash, locked FROM data", "framework")
fw.db_query("SELECT name, type, created_at FROM results", "framework")
```

Framework stores timestamps in `data`, `cache`, `meta`, and `results` as Unix epoch seconds (matching R); rows written by older versions may hold date strings. Listing helpers such as [cache_list()](ref:cache_list) and [result_list()](ref:result_list) normalize both formats.

## Git and multi-machine verification

Every project type's `.gitignore` excludes `framework.db`, `framework.db-wal`, and `framework.db-shm` because read timestamps and local caches vary across collaborators. Fresh clones initialize their own `framework.db` via [bootstrap()](ref:bootstrap) or [scaffold()](ref:scaffold).

To verify dataset hashes across machines, push local digests to the Framework Cloud integrity ledger with [ledger_push()](ref:ledger_push) and verify files against both local and Cloud records with [data_verify()](ref:data_verify). See [Framework Cloud](guide:cloud).
