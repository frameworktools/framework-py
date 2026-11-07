"""Database connections defined in the project settings.

Connections live under ``connections`` in the settings (usually
``settings/connections.yml``)::

    connections:
      default_database: framework
      databases:
        framework: {driver: sqlite, database: framework.db}
        warehouse:
          driver: postgres
          host: env("POSTGRES_HOST")
          port: 5432
          database: analytics
          user: env("POSTGRES_USER")
          password: env("POSTGRES_PASSWORD")

Credentials come from ``.env`` through ``env()``, so they never sit in the
settings. Drivers: sqlite (built in), postgres (``postgres`` extra), mysql or
mariadb (``mysql``), duckdb (``duckdb``), and sqlserver (``sqlserver``).
``read_only: true`` on a sqlite or duckdb connection opens the file read-only:
writes fail, and ``db_execute()`` and ``db_transaction()`` refuse it.
"""

from __future__ import annotations

import os
import re
import sqlite3
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from importlib import import_module, metadata
from pathlib import Path
from types import TracebackType
from typing import Any

import pandas as pd

from framework._paths import in_root
from framework.errors import FrameworkError
from framework.project import project_root
from framework.settings import settings_read

# Host names (Docker service names have underscores), IPv4, and IPv6 literals, with or
# without brackets. Spaces, ';', '/', and the like are refused.
_HOST = re.compile(r"^(?:[A-Za-z0-9._-]+|\[?[0-9A-Fa-f:.]+\]?)$")

# Driver name -> (canonical driver, Python module, extra)
DRIVERS: dict[str, tuple[str, str, str | None]] = {
    "sqlite": ("sqlite", "sqlite3", None),
    "postgres": ("postgres", "psycopg", "postgres"),
    "postgresql": ("postgres", "psycopg", "postgres"),
    "mysql": ("mysql", "pymysql", "mysql"),
    "mariadb": ("mysql", "pymysql", "mysql"),
    "duckdb": ("duckdb", "duckdb", "duckdb"),
    "sqlserver": ("sqlserver", "pyodbc", "sqlserver"),
    "mssql": ("sqlserver", "pyodbc", "sqlserver"),
}
_DISTRIBUTIONS = {
    "sqlite3": None,
    "psycopg": "psycopg",
    "pymysql": "PyMySQL",
    "duckdb": "duckdb",
    "pyodbc": "pyodbc",
}


# Finding connections ------------------------------------------------------------


def _section(config: Mapping[str, Any], key: str) -> dict[str, Any]:
    value = config.get(key)
    return value if isinstance(value, dict) else {}


def database_configs(config: Mapping[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    """Every database connection in the settings, by name.

    Looked up in ``connections.databases``, then in ``connections`` itself
    (the older flat form), then in a top-level ``databases`` section, where
    projects created by R before 2.0 have it.
    """
    config = config if config is not None else settings_read()
    connections = _section(config, "connections")
    found: dict[str, dict[str, Any]] = {}
    for source in (
        _section(config, "databases"),
        {k: v for k, v in connections.items() if isinstance(v, dict) and "driver" in v},
        _section(connections, "databases"),
    ):
        found.update(
            {name: dict(entry) for name, entry in source.items() if isinstance(entry, dict)}
        )
    return found


def default_database(config: Mapping[str, Any] | None = None) -> str | None:
    config = config if config is not None else settings_read()
    value = _section(config, "connections").get("default_database") or config.get(
        "default_database"
    )
    return str(value) if value else None


def _database_config(name: str) -> dict[str, Any]:
    configs = database_configs()
    if name not in configs:
        available = ", ".join(configs) or "none"
        raise FrameworkError(
            f"No connection configuration found for '{name}' (available: {available})"
        )
    entry = configs[name]
    if not entry.get("driver"):
        raise FrameworkError(f"No driver specified for connection '{name}'")
    return entry


# Connecting -----------------------------------------------------------------------


def _require(module: str, extra: str | None, what: str) -> Any:
    try:
        return import_module(module)
    except ImportError as error:
        hint = f' Install it with: pip install "framework-py[{extra}]"' if extra else ""
        raise FrameworkError(f"{what} connections need the {module} package.{hint}") from error


def _missing(config: Mapping[str, Any], fields: Sequence[str], what: str) -> None:
    missing = [field for field in fields if config.get(field) in (None, "")]
    if missing:
        raise FrameworkError(f"{what} configuration missing required fields: {', '.join(missing)}")


def _port(value: Any, default: int | None = None) -> int:
    if value in (None, "") and default is not None:
        return default
    try:
        port = int(str(value).strip())
    except ValueError:
        port = 0
    if not 1 <= port <= 65535:
        raise FrameworkError(f"Invalid port number: {value}")
    return port


def _host(value: Any) -> str:
    host = str(value)
    if not _HOST.match(host):
        raise FrameworkError(f"Invalid host name: {host}")
    return host.removeprefix("[").removesuffix("]")


def _file_path(config: Mapping[str, Any], what: str) -> str:
    if not config.get("database"):
        raise FrameworkError(f"{what} configuration must include 'database' path")
    path = in_root(str(config["database"]), project_root())
    if not path.parent.is_dir():
        raise FrameworkError(f"Database directory does not exist: {path.parent}")
    return str(path)


def _writable(path: str) -> str:
    """``path``, after checking that an existing file can be written, as R checks."""
    if Path(path).is_file() and not os.access(path, os.W_OK):
        raise FrameworkError(
            f"No write permission for database: {path}. Add read_only: true to the "
            "connection to open it read-only."
        )
    return path


def _connect_sqlite(config: Mapping[str, Any]) -> Any:
    path = _file_path(config, "SQLite")
    if not config.get("read_only"):
        return sqlite3.connect(_writable(path))
    # mode=ro opens the file without write access, so every write fails and a file
    # without write permission opens
    if not Path(path).is_file():
        raise FrameworkError(f"Read-only database does not exist: {path}")
    return sqlite3.connect(f"{Path(path).resolve().as_uri()}?mode=ro", uri=True)


def _connect_postgres(config: Mapping[str, Any]) -> Any:
    psycopg = _require("psycopg", "postgres", "PostgreSQL")
    _missing(config, ("host", "port", "database", "user", "password"), "PostgreSQL")
    host, port = _host(config["host"]), _port(config["port"])
    connection = psycopg.connect(
        host=host,
        port=port,
        dbname=config["database"],
        user=config["user"],
        password=config["password"],
        connect_timeout=10,
    )
    schema = config.get("schema") or "public"
    sql = import_module("psycopg.sql")
    with connection.cursor() as cursor:
        cursor.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
    connection.commit()
    return connection


def _connect_mysql(config: Mapping[str, Any]) -> Any:
    pymysql = _require("pymysql", "mysql", "MySQL/MariaDB")
    _missing(config, ("host", "port", "database", "user", "password"), "MySQL/MariaDB")
    host, port = _host(config["host"]), _port(config["port"])
    return pymysql.connect(
        host=host,
        port=port,
        database=config["database"],
        user=config["user"],
        password=config["password"],
        connect_timeout=10,
    )


def _connect_duckdb(config: Mapping[str, Any]) -> Any:
    duckdb = _require("duckdb", "duckdb", "DuckDB")
    options = {
        key: config[key] for key in ("memory_limit", "threads") if config.get(key) is not None
    }
    read_only = bool(config.get("read_only"))
    path = _file_path(config, "DuckDB")
    # A read-only database needs no write permission
    return duckdb.connect(
        path if read_only else _writable(path), read_only=read_only, config=options
    )


def _odbc_value(value: Any) -> str:
    """An ODBC connection string value, braced so ';' and '}' cannot break out of it."""
    return "{" + str(value).replace("}", "}}") + "}"


def _connect_sqlserver(config: Mapping[str, Any]) -> Any:
    pyodbc = _require("pyodbc", "sqlserver", "SQL Server")
    _missing(config, ("host", "database", "user", "password"), "SQL Server")
    port = _port(config.get("port"), default=1433)
    host = _host(config["host"])
    server = host if port == 1433 else f"{host},{port}"
    parts = {
        "DRIVER": config.get("odbc_driver") or "ODBC Driver 17 for SQL Server",
        "SERVER": server,
        "DATABASE": config["database"],
        "UID": config["user"],
        "PWD": config["password"],
        # As in R. ODBC Driver 18 otherwise refuses servers with self-signed
        # certificates, which local and test servers have.
        "TrustServerCertificate": "yes"
        if config.get("trust_server_certificate", True) is not False
        else "no",
    }
    text = ";".join(f"{key}={_odbc_value(value)}" for key, value in parts.items())
    return pyodbc.connect(text, timeout=10)


_CONNECTORS = {
    "sqlite": _connect_sqlite,
    "postgres": _connect_postgres,
    "mysql": _connect_mysql,
    "duckdb": _connect_duckdb,
    "sqlserver": _connect_sqlserver,
}


class _SharedCursor:
    """A DuckDB cursor that runs on its connection; closing it leaves the connection open."""

    def __init__(self, raw: Any) -> None:
        self._raw = raw

    def __getattr__(self, attribute: str) -> Any:
        return getattr(self._raw, attribute)

    def execute(self, *args: Any, **kwargs: Any) -> _SharedCursor:
        self._raw.execute(*args, **kwargs)
        return self

    def close(self) -> None:
        """Nothing to close: the connection closes with its ``with`` block."""


class Connection:
    """A DB-API connection from ``db_connect()``.

    It behaves like the driver's own connection (``cursor()``, ``commit()``,
    and so on) and closes when a ``with`` block ends::

        with fw.db_connect("warehouse") as conn:
            cursor = conn.cursor()

    Attributes:
        name: The connection name in the settings.
        driver: sqlite, postgres, mysql, duckdb, or sqlserver.
        raw: The driver's connection object.
        read_only: Whether it was opened with ``read_only: true`` (sqlite and duckdb).
    """

    def __init__(self, name: str, driver: str, raw: Any, read_only: bool = False) -> None:
        self.name = name
        self.driver = driver
        self.raw = raw
        self.read_only = read_only
        self._in_transaction = False

    def __getattr__(self, attribute: str) -> Any:
        return getattr(self.raw, attribute)

    def cursor(self, *args: Any, **kwargs: Any) -> Any:
        """A cursor on this connection.

        DuckDB's own ``cursor()`` opens a second connection with its own transaction, so
        ``db_transaction()`` could not roll it back. For DuckDB, Framework's cursor runs on
        this connection, as DB-API cursors do.
        """
        if self.driver == "duckdb":
            return _SharedCursor(self.raw)
        return self.raw.cursor(*args, **kwargs)

    def __enter__(self) -> Connection:
        return self

    def __exit__(
        self,
        kind: type[BaseException] | None,
        error: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.raw.close()

    def __repr__(self) -> str:
        return f"<framework Connection {self.name!r} ({self.driver})>"


def db_connect(name: str) -> Connection:
    """Open a database connection defined in the settings.

    Args:
        name: The connection's name under ``connections`` in the settings.

    Returns:
        A ``Connection`` that wraps the driver's connection object. Use it in a ``with`` block
        to close it afterward.

    Raises:
        FrameworkError: The connection is not configured, its driver package
            is not installed, or connecting failed.
    """
    entry = _database_config(name)
    driver_name = str(entry["driver"]).lower()
    if driver_name not in DRIVERS:
        raise FrameworkError(f"Unsupported database driver for '{name}': {entry['driver']}")
    driver = DRIVERS[driver_name][0]
    try:
        raw = _CONNECTORS[driver](entry)
    except FrameworkError:
        raise
    except Exception as error:
        raise FrameworkError(f"Failed to connect to '{name}': {error}") from error
    read_only = driver in ("sqlite", "duckdb") and bool(entry.get("read_only"))
    return Connection(name, driver, raw, read_only)


# Queries ------------------------------------------------------------------------------


def _frame(cursor: Any) -> pd.DataFrame:
    columns = [column[0] for column in cursor.description or []]
    return pd.DataFrame.from_records([tuple(row) for row in cursor.fetchall()], columns=columns)


def db_query(
    query: str, connection_name: str, params: Sequence[Any] | Mapping[str, Any] | None = None
) -> pd.DataFrame:
    """Run a query and return the rows as a DataFrame.

    Pass values in ``params``, never by formatting them into the SQL. The
    placeholder style is the driver's: ``?`` for sqlite, duckdb, and
    sqlserver, and ``%s`` for postgres and mysql.

    Args:
        query: The SQL query.
        connection_name: The connection's name in the settings. The connection is opened for
            this query and closed afterward.
        params: Values for the query's placeholders, as a sequence or a mapping.

    Returns:
        The result rows, with the query's column names.

    Raises:
        FrameworkError: When the connection cannot be opened or the query fails.
    """
    with db_connect(connection_name) as connection:
        cursor = connection.cursor()
        try:
            cursor.execute(query, params) if params is not None else cursor.execute(query)
            return _frame(cursor)
        except Exception as error:
            raise FrameworkError(
                f"Failed to execute query on connection '{connection_name}': {error}"
            ) from error
        finally:
            cursor.close()


def db_execute(
    query: str, connection_name: str, params: Sequence[Any] | Mapping[str, Any] | None = None
) -> int:
    """Run a statement, commit it, and return the number of rows affected.

    Args:
        query: The SQL statement.
        connection_name: The connection's name in the settings. The connection is opened for
            this statement and closed afterward.
        params: Values for the statement's placeholders, as a sequence or a mapping. The
            placeholder style is the driver's, as in ``db_query()``.

    Returns:
        The driver's count of affected rows, or -1 when the driver does not report one.

    Raises:
        FrameworkError: When the connection cannot be opened, is read-only, or the
            statement fails.
    """
    with db_connect(connection_name) as connection:
        if connection.read_only:
            raise FrameworkError(
                f"Connection '{connection_name}' is read-only (read_only: true in its "
                "settings). db_execute() can't change it."
            )
        cursor = connection.cursor()
        try:
            cursor.execute(query, params) if params is not None else cursor.execute(query)
            count = cursor.rowcount
            connection.commit()
            return int(count if count is not None else -1)
        except Exception as error:
            raise FrameworkError(
                f"Failed to execute statement on connection '{connection_name}': {error}"
            ) from error
        finally:
            cursor.close()


def _begin(connection: Connection) -> None:
    """Start a transaction where the driver would not, so rollback covers the whole block.

    DuckDB autocommits each statement. Python's sqlite3 starts a transaction only before
    INSERT, UPDATE, and DELETE, so a CREATE TABLE would be kept. PostgreSQL and SQL Server
    drivers already open one. MySQL commits DDL implicitly, whatever the client does.
    """
    raw = connection.raw
    if connection.driver == "duckdb":
        raw.begin()
    elif connection.driver == "sqlite" and not raw.in_transaction:
        raw.execute("BEGIN")


@contextmanager
def db_transaction(connection: Connection) -> Iterator[Connection]:
    """Commit the block's statements together, or roll all of them back on an error.

    A transaction inside another reuses the outer one::

        with fw.db_connect("warehouse") as conn, fw.db_transaction(conn):
            conn.cursor().execute("UPDATE ...")

    Args:
        connection: An open connection from ``db_connect()``.

    Returns:
        A context manager that yields ``connection``.

    Raises:
        FrameworkError: When the connection is read-only, or the block raises an exception.
            The transaction is rolled back and the original exception is chained as the cause.
    """
    if connection.read_only:
        raise FrameworkError(
            f"Connection '{connection.name}' is read-only (read_only: true in its settings). "
            "db_transaction() needs a writable connection."
        )
    if connection._in_transaction:
        yield connection
        return
    connection._in_transaction = True
    try:
        _begin(connection)
        yield connection
        connection.commit()
    except Exception as error:
        try:
            connection.rollback()
        except Exception as rollback_error:  # pragma: no cover (driver failure)
            raise FrameworkError(f"Failed to roll back transaction: {rollback_error}") from error
        raise FrameworkError(f"Transaction failed and was rolled back: {error}") from error
    finally:
        connection._in_transaction = False


# Listing ------------------------------------------------------------------------------


def db_list() -> pd.DataFrame:
    """Database connections in the settings: name, driver, host, port, and database."""
    rows = [
        {
            "name": name,
            "driver": entry.get("driver"),
            "host": entry.get("host"),
            "port": entry.get("port"),
            "database": entry.get("database") or entry.get("dbname"),
        }
        for name, entry in database_configs().items()
    ]
    return pd.DataFrame(rows, columns=["name", "driver", "host", "port", "database"])


def connections_list() -> pd.DataFrame:
    """Every connection: databases and storage buckets, with the defaults marked."""
    from framework.storage import bucket_configs, default_bucket

    config = settings_read()
    rows = [
        {
            "kind": "database",
            "name": name,
            "driver": entry.get("driver"),
            "location": entry.get("host") or entry.get("database"),
            "default": name == default_database(config),
        }
        for name, entry in database_configs(config).items()
    ]
    rows += [
        {
            "kind": "storage",
            "name": name,
            "driver": entry.get("provider") or entry.get("driver") or "s3",
            "location": entry.get("bucket"),
            "default": name == default_bucket(config),
        }
        for name, entry in bucket_configs(config).items()
    ]
    return pd.DataFrame(rows, columns=["kind", "name", "driver", "location", "default"])


def db_drivers_status() -> pd.DataFrame:
    """Which database drivers are installed, and the extra that installs each one."""
    rows = []
    for module in dict.fromkeys(entry[1] for entry in DRIVERS.values()):
        drivers = sorted({name for name, entry in DRIVERS.items() if entry[1] == module})
        extra = next(entry[2] for entry in DRIVERS.values() if entry[1] == module)
        distribution = _DISTRIBUTIONS[module]
        try:
            version = metadata.version(distribution) if distribution else sqlite3.sqlite_version
        except metadata.PackageNotFoundError:
            version = None
        rows.append(
            {
                "drivers": ", ".join(drivers),
                "package": distribution or "sqlite3 (built in)",
                "installed": version is not None,
                "version": version,
                "install": f'pip install "framework-py[{extra}]"' if extra else "",
            }
        )
    return pd.DataFrame(rows, columns=["drivers", "package", "installed", "version", "install"])
