"""Connections and publishing against real servers (tests/integration/compose.yml).

Start the servers with

    docker compose -f tests/integration/compose.yml up -d --wait

Each test skips when its server is not reachable, unless
FW_TEST_REQUIRE_SERVERS=1 (set in CI), which turns the skip into a failure so
a broken setup cannot pass quietly. Hosts and ports come from FW_TEST_*
environment variables, which default to the compose file's values.
"""

from __future__ import annotations

import contextlib
import os
import socket
import urllib.request
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

import framework as fw
from framework import _yaml

pytestmark = pytest.mark.integration

USER = "framework"
PASSWORD = "framework-test"
DATABASE = "framework_test"

SERVERS: dict[str, dict[str, Any]] = {
    "postgres": {"driver": "postgres", "env": "PG", "port": 55432, "param": "%s"},
    "mysql": {"driver": "mysql", "env": "MYSQL", "port": 53306, "param": "%s"},
    "mariadb": {"driver": "mariadb", "env": "MARIADB", "port": 53307, "param": "%s"},
    "sqlserver": {"driver": "sqlserver", "env": "MSSQL", "port": 51433, "param": "?"},
}
MSSQL_PASSWORD = "Framework-test-1"
ODBC_DRIVER = "ODBC Driver 18 for SQL Server"


def _address(env: str, port: int) -> tuple[str, int]:
    host = os.environ.get(f"FW_TEST_{env}_HOST", "127.0.0.1")
    return host, int(os.environ.get(f"FW_TEST_{env}_PORT", port))


def _unavailable(reason: str) -> None:
    if os.environ.get("FW_TEST_REQUIRE_SERVERS") == "1":
        pytest.fail(f"{reason} (FW_TEST_REQUIRE_SERVERS=1)")
    pytest.skip(reason)


def _reachable(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except OSError:
        return False


def _connection(name: str) -> dict[str, Any]:
    server = SERVERS[name]
    host, port = _address(server["env"], server["port"])
    if not _reachable(host, port):
        _unavailable(f"{name} is not running on {host}:{port}")
    entry: dict[str, Any] = {
        "driver": server["driver"],
        "host": host,
        "port": port,
        "database": DATABASE,
        "user": USER,
        "password": PASSWORD,
    }
    if name == "sqlserver":
        pyodbc = pytest.importorskip("pyodbc")
        if ODBC_DRIVER not in pyodbc.drivers():
            _unavailable(f"{ODBC_DRIVER} is not installed")
        _create_sqlserver_database(pyodbc, host, port)
        entry.update(user="sa", password=MSSQL_PASSWORD, odbc_driver=ODBC_DRIVER)
    return entry


def _create_sqlserver_database(pyodbc: Any, host: str, port: int) -> None:
    """SQL Server's image has no setting for a starting database, so make one."""
    connection = pyodbc.connect(
        f"DRIVER={{{ODBC_DRIVER}}};SERVER={host},{port};UID=sa;PWD={MSSQL_PASSWORD};"
        "TrustServerCertificate=yes",
        autocommit=True,
        timeout=10,
    )
    try:
        connection.cursor().execute(f"IF DB_ID('{DATABASE}') IS NULL CREATE DATABASE {DATABASE}")
    finally:
        connection.close()


def _write_connections(project: Path, connections: dict[str, Any]) -> None:
    _yaml.write(
        project / "settings.yml",
        {"default": {"project_name": "Integration", "connections": connections}},
    )


@pytest.fixture(params=list(SERVERS))
def database(request: pytest.FixtureRequest, project: Path) -> Iterator[tuple[str, str]]:
    """A configured connection name and its parameter placeholder, with a fresh table."""
    name = str(request.param)
    _write_connections(project, {"databases": {name: _connection(name)}})
    table = f"fw_{uuid.uuid4().hex[:12]}"
    fw.db_execute(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY, label VARCHAR(50))", name)
    try:
        yield name, table
    finally:
        fw.db_execute(f"DROP TABLE {table}", name)


def test_execute_query_and_parameters(database: tuple[str, str]) -> None:
    name, table = database
    mark = SERVERS[name]["param"]
    insert = f"INSERT INTO {table} (id, label) VALUES ({mark}, {mark})"
    assert fw.db_execute(insert, name, (1, "one")) == 1
    fw.db_execute(insert, name, (2, "it's two; DROP TABLE x"))
    frame = fw.db_query(f"SELECT id, label FROM {table} WHERE id > {mark} ORDER BY id", name, (0,))
    assert frame["label"].tolist() == ["one", "it's two; DROP TABLE x"]
    assert list(frame.columns) == ["id", "label"]


def test_transactions_commit_and_roll_back(database: tuple[str, str]) -> None:
    name, table = database
    mark = SERVERS[name]["param"]
    insert = f"INSERT INTO {table} (id, label) VALUES ({mark}, {mark})"
    with fw.db_connect(name) as connection:
        with fw.db_transaction(connection):
            connection.cursor().execute(insert, (1, "kept"))
        with pytest.raises(fw.FrameworkError, match="rolled back"), fw.db_transaction(connection):
            connection.cursor().execute(insert, (2, "dropped"))
            connection.cursor().execute(insert, (1, "duplicate key"))
    assert fw.db_query(f"SELECT label FROM {table}", name)["label"].tolist() == ["kept"]


def test_listing_and_errors(database: tuple[str, str]) -> None:
    name, _ = database
    listed = fw.db_list()
    assert listed["name"].tolist() == [name]
    with pytest.raises(fw.FrameworkError, match="Failed to execute query"):
        fw.db_query("SELECT * FROM no_such_table_here", name)


def test_wrong_password_is_a_framework_error(project: Path) -> None:
    entry = _connection("postgres")
    entry["password"] = "wrong"
    _write_connections(project, {"databases": {"pg": entry}})
    with pytest.raises(fw.FrameworkError, match="Failed to connect to 'pg'"):
        fw.db_connect("pg")


# S3 -------------------------------------------------------------------------------

BUCKET = "framework-test"


@pytest.fixture
def bucket(project: Path) -> str:
    """A storage bucket on the S3 server, configured as the project's default."""
    boto3 = pytest.importorskip("boto3")
    endpoint = os.environ.get("FW_TEST_S3_ENDPOINT", "http://127.0.0.1:58333")
    host = endpoint.split("://")[-1].split("/")[0]
    hostname, _, port = host.partition(":")
    if not _reachable(hostname, int(port or 80)):
        _unavailable(f"S3 is not running at {endpoint}")
    access = os.environ.get("FW_TEST_S3_ACCESS_KEY", USER)
    secret = os.environ.get("FW_TEST_S3_SECRET_KEY", PASSWORD)
    os.environ.update(S3_ACCESS_KEY=access, S3_SECRET_KEY=secret)
    client = boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access,
        aws_secret_access_key=secret,
        region_name="us-east-1",
    )
    with contextlib.suppress(client.exceptions.BucketAlreadyOwnedByYou):
        client.create_bucket(Bucket=BUCKET)
    prefix = f"run-{uuid.uuid4().hex[:8]}"
    _write_connections(
        project,
        {
            "default_storage_bucket": "test",
            "storage_buckets": {
                "test": {"bucket": BUCKET, "endpoint": endpoint, "prefix": prefix},
            },
        },
    )
    return prefix


def _get(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=10) as response:
        return bytes(response.read())


def test_publish_round_trip(project: Path, bucket: str) -> None:
    assert fw.storage_test() is True
    (project / "outputs/public").mkdir(parents=True)
    report = project / "outputs/public/report.html"
    report.write_text("<p>hello</p>", encoding="utf-8")
    url = fw.publish(report)
    assert url.endswith(f"/{BUCKET}/{bucket}/report.html")
    assert _get(url) == b"<p>hello</p>"

    data_url = fw.publish_data(pd.DataFrame({"x": [1, 2]}), "data/x.csv")
    assert _get(data_url) == b"x\n1\n2\n"

    keys = fw.publish_list()["key"].tolist()
    assert sorted(keys) == [f"{bucket}/data/x.csv", f"{bucket}/report.html"]


def test_private_files_never_reach_the_server(project: Path, bucket: str) -> None:
    secret = project / "outputs/private/ids.csv"
    secret.parent.mkdir(parents=True)
    secret.write_text("id\n1\n", encoding="utf-8")
    with pytest.raises(fw.FrameworkError, match="inside a private folder"):
        fw.publish(secret)
    assert fw.publish_list().empty
