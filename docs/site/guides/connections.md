---
id: connections
title: Database connections
description: Named database connections, parameterized queries, transactions, SQL scripts, and bulk CSV loading
section: features
position: 9
---

## Overview

Framework defines database connections once in `settings.yml` (or `settings/connections.yml`) and opens them by name. Query helpers open a connection, execute the statement, and close the connection automatically, pulling credentials from `.env` via `env()`.

## Connection configuration

Define database entries under `connections.databases`:

```yaml
connections:
  default_database: framework
  databases:
    framework:
      driver: sqlite
      database: framework.db
    warehouse:
      driver: postgres
      host: env("POSTGRES_HOST")
      port: env("POSTGRES_PORT", "5432")
      database: env("POSTGRES_DB")
      schema: env("POSTGRES_SCHEMA", "public")
      user: env("POSTGRES_USER")
      password: env("POSTGRES_PASSWORD")
    local:
      driver: duckdb
      database: inputs/final/warehouse.duckdb
  storage_buckets: {}
```

Every new project includes the `framework` SQLite connection pointing to `framework.db`, plus empty `POSTGRES_*` placeholders in `.env`:

```sh
# .env (gitignored)
POSTGRES_HOST=db.example.org
POSTGRES_PORT=5432
POSTGRES_DB=analytics
POSTGRES_SCHEMA=public
POSTGRES_USER=analyst
POSTGRES_PASSWORD=change-me
```

Framework also reads legacy entries placed directly under `connections` (with a `driver` key) or under a top-level `databases` key.

## Supported drivers

SQLite uses Python's standard library; other engines install via optional extras:

| Driver names | Package | Extra | Required fields |
|--------------|---------|-------|-----------------|
| `sqlite` | `sqlite3` (built-in) | | `database` |
| `postgres`, `postgresql` | `psycopg` | `postgres` | `host`, `port`, `database`, `user`, `password` |
| `mysql`, `mariadb` | `PyMySQL` | `mysql` | `host`, `port`, `database`, `user`, `password` |
| `duckdb` | `duckdb` | `duckdb` | `database` |
| `sqlserver`, `mssql` | `pyodbc` | `sqlserver` | `host`, `database`, `user`, `password` |

```sh
uv add "framework-py[postgres]"
```

Missing driver packages raise an error with the install command. Check installed drivers with [db_drivers_status()](ref:db_drivers_status):

```python
import framework as fw

fw.db_drivers_status()
```

Driver-specific options:

- **SQLite and DuckDB:** `database` resolves relative to the project root (its parent directory must exist). Setting `read_only: true` opens existing files read-only and blocks `db_execute()` and `db_transaction()`; missing files raise an error instructing you to initialize the database with a writable connection first. DuckDB also accepts `memory_limit` and `threads`.
- **PostgreSQL:** `schema` sets `search_path` (defaults to `public`).
- **SQL Server:** `port` defaults to `1433`, `odbc_driver` defaults to `ODBC Driver 17 for SQL Server`, and `trust_server_certificate` defaults to `true`. Install the system ODBC driver separately.

Network connections (`postgres`, `mysql`, `sqlserver`) use a 10-second connection timeout.

## Queries and statements

[db_query()](ref:db_query) executes a `SELECT` query and returns a DataFrame:

```python
visits = fw.db_query("SELECT * FROM visits WHERE active", "warehouse")
```

Always pass dynamic values via `params` using the driver's parameter marker (`?` for SQLite, DuckDB, and SQL Server; `%s` for PostgreSQL and MySQL):

```python
fw.db_query("SELECT * FROM visits WHERE site = ?", "local", params=["north"])
fw.db_query("SELECT * FROM visits WHERE site = %s", "warehouse", params=["north"])
```

[db_execute()](ref:db_execute) executes a write or DDL statement, commits it, and returns the affected row count (`-1` when not reported by the driver):

```python
fw.db_execute("CREATE TABLE IF NOT EXISTS visits (id INTEGER, site TEXT)", "local")
fw.db_execute("INSERT INTO visits VALUES (?, ?)", "local", params=[1, "north"])
```

Driver errors raise `FrameworkError`.

## Managed connections

[db_connect()](ref:db_connect) returns a DB-API `Connection` context manager (`cursor()`, `commit()`, and `conn.raw` for driver-native methods):

```python
with fw.db_connect("warehouse") as conn:
    cursor = conn.cursor()
    cursor.execute("SELECT count(*) FROM visits")
    print(cursor.fetchone())
```

### Per-call read-only mode

Pass `read_only=True` to [db_connect()](ref:db_connect) or [db_query()](ref:db_query) on SQLite or DuckDB to restrict that call to read-only access (allowing concurrent multi-process DuckDB reads). Calls cannot downgrade a `read_only: true` setting via `read_only=False`; define a separate writable connection entry for build scripts instead:

```yaml
connections:
  databases:
    vizient: {driver: duckdb, database: data/vizient.duckdb, read_only: true}
    vizient_build: {driver: duckdb, database: data/vizient.duckdb}
```

```python
with fw.db_connect("vizient_build") as conn:
    fw.db_execute_file(conn, "sql/build.sql")

fw.db_query("SELECT count(*) AS n FROM encounters", "vizient")
```

Passing `read_only=True` on server databases (`postgres`, `mysql`, `sqlserver`) raises an error because server roles govern write permissions.

## Transactions

[db_transaction()](ref:db_transaction) commits a block atomically or rolls back all statements on exception (nested blocks join the outer transaction):

```python
with fw.db_connect("local") as conn, fw.db_transaction(conn):
    cursor = conn.cursor()
    cursor.execute("INSERT INTO visits VALUES (2, 'south')")
    cursor.execute("INSERT INTO visits VALUES (3, 'east')")
```

SQLite, DuckDB, PostgreSQL, and SQL Server roll back DDL statements inside transactions; MySQL and MariaDB implicitly commit DDL immediately.

## SQL script execution

[db_execute_file()](ref:db_execute_file) executes all statements in a project-relative `.sql` file inside one transaction and returns the executed statement count:

```python
fw.db_execute_file("omop", "sql/omop_cdm_ddl.sql")

with fw.db_connect("omop") as conn:
    fw.db_execute_file(conn, "sql/constraints.sql")
```

The splitter respects quoted strings, comments, PostgreSQL `$$` blocks, SQLite `BEGIN ... END` triggers, and SQL Server `GO` batch separators (MySQL client `DELIMITER` directives are unsupported). Failures roll back the file and report the statement index and line number. Pass `transaction=False` for statements such as PostgreSQL `CREATE INDEX CONCURRENTLY`.

## Bulk CSV loading

[db_copy_from()](ref:db_copy_from) streams a delimited file into an existing table inside a single transaction and returns the inserted row count. Header names map to target columns, and empty fields plus `"NA"` load as `NULL` (override via `na=`):

```python
fw.db_copy_from("omop", "cdm.concept", "inputs/raw/vocabulary/CONCEPT.csv", delim="tab")
```

PostgreSQL uses `COPY ... FROM STDIN`, DuckDB inserts parsed chunks per statement, SQL Server enables `fast_executemany`, and SQLite/MySQL use chunked `executemany`.

## Connection inspection and credentials

[db_list()](ref:db_list) lists configured databases, and [connections_list()](ref:connections_list) lists both databases and S3 storage buckets (see [Publishing](guide:publishing)):

```python
fw.db_list()
fw.connections_list()
```

Keep passwords in `.env` and reference them with `env("...")`. See [Environment](guide:environment).
