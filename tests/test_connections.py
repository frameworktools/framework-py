from __future__ import annotations

import os
import sys
import types
from pathlib import Path
from typing import Any

import pytest

import framework as fw
from framework import connections


@pytest.fixture
def db_project(project: Path) -> Path:
    (project / "data").mkdir()
    (project / "settings.yml").write_text(
        "default:\n  connections: settings/connections.yml\n",
        encoding="utf-8",
    )
    (project / "settings").mkdir()
    (project / "settings/connections.yml").write_text(
        "connections:\n"
        "  default_database: local\n"
        "  databases:\n"
        "    local: {driver: sqlite, database: data/local.db}\n"
        "    duck: {driver: duckdb, database: data/local.duckdb}\n"
        "    warehouse:\n"
        "      driver: postgres\n"
        '      host: env("FW_TEST_PG_HOST", "db.example.org")\n'
        "      port: 5432\n"
        "      database: analytics\n"
        "      user: analyst\n"
        '      password: env("FW_TEST_PG_PASSWORD")\n'
        "  storage_buckets:\n"
        "    reports: {bucket: my-reports, region: us-east-2}\n"
        "  default_storage_bucket: reports\n",
        encoding="utf-8",
    )
    return project


def test_query_execute_and_transaction(db_project: Path) -> None:
    assert fw.db_execute("CREATE TABLE runs (id INTEGER, done INTEGER)", "local") in (-1, 0)
    assert fw.db_execute("INSERT INTO runs VALUES (?, ?)", "local", params=(1, 0)) == 1
    frame = fw.db_query("SELECT * FROM runs WHERE id = ?", "local", params=(1,))
    assert frame.to_dict("records") == [{"id": 1, "done": 0}]

    with fw.db_connect("local") as conn:
        assert conn.driver == "sqlite"
        with pytest.raises(fw.FrameworkError, match="rolled back"), fw.db_transaction(conn):
            conn.cursor().execute("UPDATE runs SET done = 1")
            with fw.db_transaction(conn):  # nested: reuses the outer one
                conn.cursor().execute("INSERT INTO runs VALUES (2, 0)")
            raise ValueError("boom")
    assert fw.db_query("SELECT count(*) AS n, sum(done) AS d FROM runs", "local").iloc[
        0
    ].tolist() == [1, 0]

    with fw.db_connect("local") as conn, fw.db_transaction(conn):
        conn.cursor().execute("UPDATE runs SET done = 1")
    assert fw.db_query("SELECT done FROM runs", "local")["done"].tolist() == [1]


def test_query_errors(db_project: Path) -> None:
    with pytest.raises(fw.FrameworkError, match="Failed to execute query on connection 'local'"):
        fw.db_query("SELECT * FROM missing", "local")
    with pytest.raises(fw.FrameworkError, match=r"No connection configuration found for 'nope'"):
        fw.db_connect("nope")


def test_duckdb(db_project: Path) -> None:
    fw.db_execute("CREATE TABLE t AS SELECT 42 AS answer", "duck")
    assert fw.db_query("SELECT answer FROM t", "duck")["answer"].tolist() == [42]


def test_read_only_sqlite(db_project: Path) -> None:
    fw.db_execute("CREATE TABLE t (a INTEGER)", "local")
    fw.db_execute("INSERT INTO t VALUES (1)", "local")
    (db_project / "settings/connections.yml").write_text(
        "connections:\n  databases:\n"
        "    study: {driver: sqlite, database: data/local.db, read_only: true}\n"
        "    missing: {driver: sqlite, database: data/none.db, read_only: true}\n",
        encoding="utf-8",
    )
    assert fw.db_query("SELECT a FROM t", "study")["a"].tolist() == [1]
    with pytest.raises(
        fw.FrameworkError, match=r"'study' is read-only \(read_only: true in its settings\)"
    ):
        fw.db_execute("DELETE FROM t", "study")
    with fw.db_connect("study") as conn:
        assert conn.read_only
        with (
            pytest.raises(fw.FrameworkError, match=r"db_transaction\(\) needs a writable"),
            fw.db_transaction(conn),
        ):
            pass
        with pytest.raises(Exception, match="readonly"):
            conn.cursor().execute("INSERT INTO t VALUES (2)")
    with pytest.raises(fw.FrameworkError, match="Read-only database does not exist"):
        fw.db_connect("missing")
    assert not (db_project / "data/none.db").exists()


def test_sqlite_path_is_relative_to_the_root(
    db_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (db_project / "notebooks").mkdir()
    monkeypatch.chdir(db_project / "notebooks")
    fw.db_execute("CREATE TABLE x (a INTEGER)", "local")
    assert (db_project / "data/local.db").is_file()


def test_postgres_settings_reach_the_driver(
    db_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: dict[str, Any] = {}

    class Cursor:
        def __enter__(self) -> Cursor:
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def execute(self, query: object) -> None:
            calls["search_path"] = str(query)

    class FakeConnection:
        def cursor(self) -> Cursor:
            return Cursor()

        def commit(self) -> None:
            pass

        def close(self) -> None:
            calls["closed"] = True

    def connect(**kwargs: Any) -> FakeConnection:
        calls.update(kwargs)
        return FakeConnection()

    class Composed:
        def __init__(self, text: str) -> None:
            self.text = text

        def format(self, identifier: object) -> str:
            return self.text.replace("{}", str(identifier))

    psycopg = types.ModuleType("psycopg")
    psycopg.connect = connect  # type: ignore[attr-defined]
    sql = types.ModuleType("psycopg.sql")
    sql.SQL = Composed  # type: ignore[attr-defined]
    sql.Identifier = lambda name: f'"{name}"'  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "psycopg", psycopg)
    monkeypatch.setitem(sys.modules, "psycopg.sql", sql)
    monkeypatch.setenv("FW_TEST_PG_PASSWORD", "from-env")

    with fw.db_connect("warehouse") as conn:
        assert conn.driver == "postgres"
    assert calls["host"] == "db.example.org"
    assert calls["port"] == 5432
    assert calls["dbname"] == "analytics"
    assert calls["password"] == "from-env"
    assert calls["search_path"] == 'SET search_path TO "public"'
    assert calls["closed"] is True


@pytest.mark.parametrize(
    ("entry", "message"),
    [
        ({"driver": "postgres", "host": "h", "port": 1}, "missing required fields: database"),
        (
            {
                "driver": "mysql",
                "host": "bad host",
                "port": 3306,
                "database": "d",
                "user": "u",
                "password": "p",
            },
            "Invalid host name",
        ),
        (
            {
                "driver": "mysql",
                "host": "h",
                "port": 99999,
                "database": "d",
                "user": "u",
                "password": "p",
            },
            "Invalid port number",
        ),
        ({"driver": "oracle"}, "Unsupported database driver"),
        ({"database": "x"}, "No driver specified"),
        ({"driver": "sqlite", "database": "missing/dir/x.db"}, "Database directory does not exist"),
    ],
)
def test_connection_errors(
    project: Path, monkeypatch: pytest.MonkeyPatch, entry: dict[str, Any], message: str
) -> None:
    monkeypatch.setitem(sys.modules, "psycopg", types.ModuleType("psycopg"))
    monkeypatch.setitem(sys.modules, "pymysql", types.ModuleType("pymysql"))
    monkeypatch.setattr(connections, "database_configs", lambda config=None: {"x": entry})
    with pytest.raises(fw.FrameworkError, match=message):
        fw.db_connect("x")


def test_missing_driver_names_the_extra(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "pyodbc", None)
    monkeypatch.setattr(
        connections,
        "database_configs",
        lambda config=None: {"x": {"driver": "sqlserver", "host": "h", "database": "d"}},
    )
    with pytest.raises(fw.FrameworkError, match=r"framework-py\[sqlserver\]"):
        fw.db_connect("x")


def test_sqlserver_connection_string(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}
    pyodbc = types.ModuleType("pyodbc")
    pyodbc.connect = lambda text, timeout: seen.update(text=text) or object()  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "pyodbc", pyodbc)
    entry = {
        "driver": "mssql",
        "host": "sql.example.org",
        "port": 1444,
        "database": "d",
        "user": "u",
        "password": "p};DROP",
    }
    monkeypatch.setattr(connections, "database_configs", lambda config=None: {"x": entry})
    fw.db_connect("x")
    assert seen["text"] == (
        "DRIVER={ODBC Driver 17 for SQL Server};SERVER={sql.example.org,1444};DATABASE={d};"
        "UID={u};PWD={p}};DROP};TrustServerCertificate={yes}"
    )
    entry["trust_server_certificate"] = False
    fw.db_connect("x")
    assert seen["text"].endswith("TrustServerCertificate={no}")


def test_lookup_order_and_legacy_shapes(project: Path) -> None:
    config = {
        "databases": {"a": {"driver": "sqlite", "database": "top.db"}},
        "connections": {
            "b": {"driver": "sqlite", "database": "flat.db"},
            "databases": {"a": {"driver": "sqlite", "database": "nested.db"}},
        },
    }
    found = connections.database_configs(config)
    assert found["a"]["database"] == "nested.db"
    assert found["b"]["database"] == "flat.db"


def test_listing(db_project: Path) -> None:
    assert fw.db_list()["name"].tolist() == ["local", "duck", "warehouse"]
    listed = fw.connections_list()
    assert listed[listed["default"]]["name"].tolist() == ["local", "reports"]
    status = fw.db_drivers_status()
    assert status[status["package"] == "duckdb"]["installed"].tolist() == [True]


def test_r_project_connections_work(r_project: Path) -> None:
    """R writes connections.yml in the shared shape, and Python can use it."""
    assert fw.db_query("SELECT 1 AS one", "framework")["one"].tolist() == [1]


# Audit 2026-11-05: DuckDB autocommitted, so rollback failed and the row stayed; SQLite
# kept a CREATE TABLE from a failed block.
@pytest.mark.parametrize("name", ["local", "duck"])
def test_failed_transactions_roll_back_rows_and_tables(db_project: Path, name: str) -> None:
    fw.db_execute("CREATE TABLE runs (id INTEGER)", name)
    fw.db_execute("INSERT INTO runs VALUES (1)", name)
    with fw.db_connect(name) as conn:
        with pytest.raises(fw.FrameworkError, match="rolled back"), fw.db_transaction(conn):
            cursor = conn.cursor()
            cursor.execute("INSERT INTO runs VALUES (2)")
            cursor.execute("CREATE TABLE extra (id INTEGER)")
            raise ValueError("boom")
        with fw.db_transaction(conn):
            conn.cursor().execute("INSERT INTO runs VALUES (3)")
    assert fw.db_query("SELECT id FROM runs ORDER BY id", name)["id"].tolist() == [1, 3]
    with pytest.raises(fw.FrameworkError):
        fw.db_query("SELECT * FROM extra", name)


@pytest.mark.parametrize("host", ["db_server", "::1", "[::1]", "10.0.0.5", "db.example.org"])
def test_host_names_docker_services_and_ipv6(host: str) -> None:
    # Audit 2026-11-05: IPv6 literals and Docker service names were rejected
    from framework.connections import _host

    assert _host(host) == host.strip("[]")


@pytest.mark.parametrize("host", ["bad host", "h;DROP", "a/b"])
def test_host_refuses_unsafe_values(host: str) -> None:
    from framework.connections import _host

    with pytest.raises(fw.FrameworkError, match="Invalid host name"):
        _host(host)


def test_missing_driver_hint_works_in_zsh(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from framework import connections

    def missing(name: str) -> None:
        raise ImportError(name)

    monkeypatch.setattr(connections, "import_module", missing)
    with pytest.raises(fw.FrameworkError, match=r'pip install "framework-py\[postgres\]"'):
        connections._require("psycopg", "postgres", "PostgreSQL")


@pytest.mark.skipif(
    sys.platform == "win32" or (hasattr(os, "geteuid") and os.geteuid() == 0),
    reason="needs POSIX file permissions and a non-root user",
)
@pytest.mark.parametrize("name", ["local", "duck"])
def test_a_file_without_write_permission_needs_read_only(db_project: Path, name: str) -> None:
    fw.db_execute("CREATE TABLE t AS SELECT 1 AS x", name)
    suffix = "db" if name == "local" else "duckdb"
    path = db_project / f"data/local.{suffix}"
    path.chmod(0o444)
    try:
        with pytest.raises(fw.FrameworkError, match="Add read_only: true"):
            fw.db_connect(name)
        driver = "sqlite" if name == "local" else "duckdb"
        (db_project / "settings/connections.yml").write_text(
            "connections:\n  databases:\n"
            f"    ro: {{driver: {driver}, database: data/local.{suffix}, read_only: true}}\n",
            encoding="utf-8",
        )
        assert fw.db_query("SELECT x FROM t", "ro")["x"].tolist() == [1]
    finally:
        path.chmod(0o644)
