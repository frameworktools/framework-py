---
id: connections
title: Database connections
description: Database connections in the settings, and queries with db_query(), db_execute(), and db_transaction()
section: features
position: 9
---

## Overview

Framework defines database connections once, in the project settings, and opens them by name. The query functions open a connection, run the statement, and close the connection again, so analysis code does not manage connections by hand. Credentials come from the project's `.env` file and never sit in the settings.

## Configuring connections

Connections live in the `connections` section, which standard and privacy-sensitive projects keep in `settings/connections.yml`. Each database is an entry under `databases`.

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

Every new project defines the `framework` connection, which points at the project's own `framework.db`.

`env()` reads a value from the environment, which Framework loads from `.env` before it reads the settings. New projects have empty `POSTGRES_*` entries in `.env` to fill in.

```sh
# .env (ignored by git)
POSTGRES_HOST=db.example.org
POSTGRES_PORT=5432
POSTGRES_DB=analytics
POSTGRES_SCHEMA=public
POSTGRES_USER=analyst
POSTGRES_PASSWORD=change-me
```

Framework also finds connections written directly under `connections` with a `driver` key, and in a top-level `databases` section, which older R projects use.

## Drivers

SQLite is built into Python. The other drivers come from optional extras of framework-py.

| Driver names | Python package | Extra | Required fields |
|--------------|----------------|-------|-----------------|
| `sqlite` | sqlite3 (built in) | | `database` |
| `postgres`, `postgresql` | psycopg | `postgres` | `host`, `port`, `database`, `user`, `password` |
| `mysql`, `mariadb` | PyMySQL | `mysql` | `host`, `port`, `database`, `user`, `password` |
| `duckdb` | duckdb | `duckdb` | `database` |
| `sqlserver`, `mssql` | pyodbc | `sqlserver` | `host`, `database`, `user`, `password` |

```sh
uv add "framework-py[postgres]"
```

A connection whose package is missing raises an error that names the install command. [db_drivers_status()](ref:db_drivers_status) reports which drivers are installed.

```python
import framework as fw

fw.db_drivers_status()
```

Each driver accepts a few more fields.

- **SQLite and DuckDB.** `database` is a file path, relative to the project root. Its folder must exist. Both take `read_only: true`, which opens an existing file read-only: writes fail, a file without write permission still opens, and `db_execute()` and `db_transaction()` refuse the connection. Without it, a file you can't write is an error that suggests `read_only: true`. DuckDB also takes `memory_limit` and `threads`.
- **PostgreSQL.** `schema` sets the search path and defaults to `public`.
- **SQL Server.** `port` defaults to 1433. `odbc_driver` names the ODBC driver and defaults to `ODBC Driver 17 for SQL Server`. `trust_server_certificate` defaults to `true`, which accepts self-signed certificates. The ODBC driver itself is installed separately.

PostgreSQL, MySQL, and SQL Server connections time out after ten seconds when the server does not answer.

## Running queries

[db_query()](ref:db_query) runs a query and returns the rows as a DataFrame. It takes the SQL and the connection name.

```python
visits = fw.db_query("SELECT * FROM visits WHERE active", "warehouse")
```

Pass values in `params`, never by formatting them into the SQL string. The placeholder style is the driver's: `?` for SQLite, DuckDB, and SQL Server, and `%s` for PostgreSQL and MySQL.

```python
fw.db_query("SELECT * FROM visits WHERE site = ?", "local", params=["north"])
fw.db_query("SELECT * FROM visits WHERE site = %s", "warehouse", params=["north"])
```

[db_execute()](ref:db_execute) runs a statement, commits it, and returns the number of rows affected. It returns -1 when the driver does not report a count.

```python
fw.db_execute("CREATE TABLE IF NOT EXISTS visits (id INTEGER, site TEXT)", "local")
fw.db_execute("INSERT INTO visits VALUES (?, ?)", "local", params=[1, "north"])
```

A failed query raises `FrameworkError` with the driver's message.

## Open connections

[db_connect()](ref:db_connect) opens a connection and returns a `Connection`. It behaves like the driver's own connection object, with `cursor()`, `commit()`, and the rest of the DB-API, and it closes when a `with` block ends.

```python
with fw.db_connect("warehouse") as conn:
    cursor = conn.cursor()
    cursor.execute("SELECT count(*) FROM visits")
    print(cursor.fetchone())
```

The driver's object is available as `conn.raw` for features outside the DB-API.

## Transactions

[db_transaction()](ref:db_transaction) groups statements so that they are committed together. When the block raises an exception, every statement in it is rolled back, and Framework raises `FrameworkError` with the original exception as its cause.

```python
with fw.db_connect("local") as conn, fw.db_transaction(conn):
    cursor = conn.cursor()
    cursor.execute("INSERT INTO visits VALUES (2, 'south')")
    cursor.execute("INSERT INTO visits VALUES (3, 'east')")
```

A transaction inside another transaction on the same connection joins the outer one.

On SQLite, DuckDB, PostgreSQL, and SQL Server, a rollback also undoes `CREATE TABLE` and other schema changes. MySQL and MariaDB commit schema changes at once, so a rollback cannot undo them.

## Listing connections

[db_list()](ref:db_list) returns the configured databases with their driver, host, port, and database. [connections_list()](ref:connections_list) returns databases and storage buckets together and marks the defaults.

```python
fw.db_list()
fw.connections_list()
```

Storage buckets are defined in the same section and used for publishing. The [Publishing](guide:publishing) guide covers them.

## Credentials

Keep every credential in `.env` and refer to it with `env()`. Settings files are committed to git, and `.env` is not.

```yaml
# Settings: a reference to the environment
password: env("POSTGRES_PASSWORD")
```

Framework refuses secret-looking values in project specs and in your global defaults, and it makes sure every project's `.gitignore` excludes `.env`. The [Environment](guide:environment) guide covers `.env` files.
