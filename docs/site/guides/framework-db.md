---
id: framework-db
title: framework.db
description: The project database that records data hashes, cached values, and saved outputs
section: features
position: 2
---

## Overview

Every Framework project has a SQLite database named `framework.db` in its root folder. Framework uses it to record what it knows about the project: the hash of each data file, the cached values and their expiry, and the outputs you have saved.

You rarely work with the database directly. [data_read()](ref:data_read), [cache_remember()](ref:cache_remember), [save_table()](ref:save_table), and the other functions update it as they run. [scaffold()](ref:scaffold), [new()](ref:new), and [bootstrap()](ref:bootstrap) create it when it is missing.

The schema comes from the R package, so R and Python read and write the same tables. A file one language saved verifies in the other.

## Tables

| Table | Contents |
|-------|----------|
| `data` | Catalog name, path, type, delimiter, locked flag, and SHA-256 hash of each data file |
| `cache` | Name, file, hash, and expiry of each cached value |
| `results` | Name, type, public flag, and hash of each saved output |
| `connections` | Database connection metadata, part of the shared schema |
| `meta` | Key-value records, such as scaffold times and the last security audit |

Paths are stored relative to the project root, so a project still works after it is moved or cloned.

## Data Integrity

The `data` table holds the hash Framework compares each data file against. The first [data_read()](ref:data_read) of a catalog name records the file's SHA-256 hash. Later reads compare the file with that record.

```python
import framework as fw

fw.scaffold()
survey = fw.data_read("inputs.raw.survey")
```

A changed file raises a `DataChangedWarning` and the new hash is recorded. When the catalog entry has `locked: true`, a changed file raises an `IntegrityError` instead and the data is not read. [data_add()](ref:data_add) and [data_save()](ref:data_save) record a file's hash when they register it. The [Data Management](guide:data-management) guide covers locking.

## Cache Records

The `cache` table records each cached value's file, hash, and expiry time. [cache_get()](ref:cache_get) and [cache_remember()](ref:cache_remember) use it to decide whether a cached value exists, has expired, or no longer matches its file. The [Caching](guide:caching) guide describes the cache.

## The Results Registry

Each `save_*()` function records its file in the `results` table with the output type, whether it is public, and the file's hash. [result_list()](ref:result_list) returns the records, newest first.

```python
fw.result_list()
fw.result_list(type="figure")
fw.result_list(type="table", public=True)
```

Results are recorded by file name, such as `summary.csv`. The [Saving Work](guide:saving-work) guide covers the output functions.

## Querying the Database

Every new project defines a connection named `framework` that points at `framework.db`. [db_query()](ref:db_query) runs SQL against it like any other connection.

```python
fw.db_query("SELECT name, expire_at FROM cache", "framework")
fw.db_query("SELECT name, path, hash, locked FROM data", "framework")
fw.db_query("SELECT name, type, created_at FROM results", "framework")
```

Timestamps in the `data`, `cache`, and `meta` tables are seconds since the Unix epoch, which is how the R package stores them. The `results` table holds local date strings. The listing functions, such as [cache_list()](ref:cache_list) and [result_list()](ref:result_list), convert both forms for you.

## Location and Git

The database lives in the project root.

```text
vaccine-study/
├── framework.db
├── settings.yml
├── notebooks/
└── ...
```

Every project type's `.gitignore` excludes `framework.db` and its `-wal` and `-shm` files. The database holds local state, such as when you last read each file, which differs between collaborators. A clone gets a fresh database from [bootstrap()](ref:bootstrap) or the first [scaffold()](ref:scaffold).

To compare hashes across machines, use the Framework Cloud integrity ledger. [ledger_push()](ref:ledger_push) sends the hashes in `framework.db` to the ledger, and [data_verify()](ref:data_verify) checks a file against both records. The [Framework Cloud](guide:cloud) guide describes the ledger.
