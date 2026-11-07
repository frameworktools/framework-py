"""db_execute_file(), db_copy_from(), and the per-call read_only override."""

from __future__ import annotations

from pathlib import Path

import pytest

import framework as fw
from framework import _sql


@pytest.fixture
def db_project(project: Path) -> Path:
    (project / "data").mkdir()
    (project / "sql").mkdir()
    (project / "settings.yml").write_text(
        "default:\n"
        "  connections:\n"
        "    databases:\n"
        "      local: {driver: sqlite, database: data/local.db}\n"
        "      duck: {driver: duckdb, database: data/local.duckdb}\n"
        "      shared: {driver: duckdb, database: data/local.duckdb, read_only: true}\n"
        "      warehouse: {driver: postgres, host: db, port: 5432, database: a, user: u,"
        " password: p}\n",
        encoding="utf-8",
    )
    return project


DDL = """\
-- The study schema
CREATE TABLE person (
  person_id INTEGER PRIMARY KEY,
  note VARCHAR(50) DEFAULT 'a; b'  -- a ; inside a string
);
/* A block comment; with a semicolon */
CREATE TABLE visit (visit_id INTEGER, person_id INTEGER, visit_date DATE);
INSERT INTO person (person_id) VALUES (1);
"""


def test_split_respects_quotes_comments_and_dollar_quotes() -> None:
    statements = _sql.split(DDL, "postgres")
    assert [s.line for s in statements] == [2, 7, 8]
    assert statements[0].sql.endswith("-- a ; inside a string\n);")

    body = "CREATE FUNCTION f() RETURNS int AS $body$ SELECT 1; $body$ LANGUAGE sql;\nSELECT 2;"
    assert len(_sql.split(body, "postgres")) == 2
    assert len(_sql.split("SELECT 'it''s; fine'; SELECT \"a;b\" FROM t;", "duckdb")) == 2
    assert len(_sql.split("SELECT 'a\\'; b'; SELECT 1; # done;\n", "mysql")) == 2
    assert _sql.split("-- only a comment\n;\n", "postgres") == []


def test_split_sqlite_triggers_and_sqlserver_batches() -> None:
    trigger = (
        "CREATE TABLE t (a INTEGER);\n"
        "CREATE TRIGGER tr AFTER INSERT ON t BEGIN UPDATE t SET a = 1; SELECT 1; END;\n"
        "INSERT INTO t VALUES (2);\n"
    )
    assert [s.line for s in _sql.split(trigger, "sqlite")] == [1, 2, 3]
    batches = (
        "CREATE TABLE t (a INT);\nINSERT INTO t VALUES (1);\nGO\n"
        "CREATE VIEW v AS SELECT a FROM t\ngo -- end\n"
    )
    assert [s.sql for s in _sql.split(batches, "sqlserver")] == [
        "CREATE TABLE t (a INT);\nINSERT INTO t VALUES (1);",
        "CREATE VIEW v AS SELECT a FROM t",
    ]


@pytest.mark.parametrize("name", ["local", "duck"])
def test_execute_file_runs_every_statement(db_project: Path, name: str) -> None:
    if name == "duck":
        pytest.importorskip("duckdb")
    (db_project / "sql/schema.sql").write_text(DDL, encoding="utf-8")
    assert fw.db_execute_file(name, "sql/schema.sql") == 3
    assert fw.db_query("SELECT person_id, note FROM person", name).to_dict("records") == [
        {"person_id": 1, "note": "a; b"}
    ]

    (db_project / "sql/broken.sql").write_text(
        "INSERT INTO person (person_id) VALUES (2);\n\nINSERT INTO nowhere VALUES (1);\n",
        encoding="utf-8",
    )
    with (
        fw.db_connect(name) as conn,
        pytest.raises(fw.FrameworkError, match=r"Statement 2 of 2 in broken\.sql \(line 3\)"),
    ):
        fw.db_execute_file(conn, "sql/broken.sql")
    # Rolled back: person 2 was not kept
    assert fw.db_query("SELECT count(*) AS n FROM person", name)["n"].tolist() == [1]


def test_execute_file_without_a_transaction_keeps_earlier_statements(db_project: Path) -> None:
    (db_project / "sql/steps.sql").write_text(
        "CREATE TABLE a (x INTEGER);\nINSERT INTO a VALUES (1);\nINSERT INTO nowhere VALUES (1);\n",
        encoding="utf-8",
    )
    with pytest.raises(fw.FrameworkError, match="Statement 3 of 3"):
        fw.db_execute_file("local", "sql/steps.sql", transaction=False)
    assert fw.db_query("SELECT x FROM a", "local")["x"].tolist() == [1]


def test_execute_file_errors(db_project: Path) -> None:
    with pytest.raises(fw.FrameworkError, match=r"File not found: sql/none\.sql"):
        fw.db_execute_file("local", "sql/none.sql")
    fw.db_execute("CREATE TABLE t (a INTEGER)", "local")
    (db_project / "sql/x.sql").write_text("DELETE FROM t;", encoding="utf-8")
    with (
        fw.db_connect("local", read_only=True) as conn,
        pytest.raises(fw.FrameworkError, match=r"'local' is read-only.*needs a writable"),
    ):
        fw.db_execute_file(conn, "sql/x.sql")


CSV = 'person_id,name,visits,score\n1,Ann,3,2.5\n2,NA,,\n3,"Lee, Jr.",7,1\n'


@pytest.mark.parametrize("name", ["local", "duck"])
def test_copy_from_loads_a_csv(db_project: Path, name: str) -> None:
    if name == "duck":
        pytest.importorskip("duckdb")
    fw.db_execute(
        "CREATE TABLE people (person_id INTEGER, name VARCHAR(40), visits INTEGER, "
        "score DOUBLE PRECISION, source VARCHAR(10) DEFAULT 'csv')",
        name,
    )
    (db_project / "data/people.csv").write_text(CSV, encoding="utf-8")
    assert fw.db_copy_from(name, "people", "data/people.csv") == 3
    rows = fw.db_query("SELECT * FROM people ORDER BY person_id", name)
    assert rows["name"].tolist()[0] == "Ann" and rows["name"].tolist()[2] == "Lee, Jr."
    assert rows["name"].isna().tolist() == [False, True, False]
    assert rows["visits"].isna().tolist() == [False, True, False]
    assert rows["score"].tolist()[0] == 2.5
    assert rows["source"].tolist() == ["csv"] * 3
    assert fw.db_query("SELECT sum(visits) AS n FROM people", name)["n"].tolist() == [10]


def test_copy_from_loads_nothing_on_failure(
    db_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from framework import connections

    monkeypatch.setattr(connections, "_CHUNK_ROWS", 1)
    fw.db_execute("CREATE TABLE people (person_id INTEGER PRIMARY KEY, name TEXT)", "local")
    (db_project / "data/dupes.csv").write_text("person_id,name\n1,a\n2,b\n1,c\n", "utf-8")
    with pytest.raises(fw.FrameworkError, match="nothing was loaded"):
        fw.db_copy_from("local", "people", "data/dupes.csv")
    assert fw.db_query("SELECT count(*) AS n FROM people", "local")["n"].tolist() == [0]

    (db_project / "data/semi.csv").write_text("person_id;name\n5;.\n", "utf-8")
    assert fw.db_copy_from("local", "people", "data/semi.csv", delim="semicolon", na=["."]) == 1
    assert fw.db_query("SELECT name FROM people", "local")["name"].isna().tolist() == [True]


def test_read_only_override(db_project: Path) -> None:
    pytest.importorskip("duckdb")
    fw.db_execute("CREATE TABLE t (a INTEGER)", "duck")
    fw.db_execute("INSERT INTO t VALUES (1)", "duck")
    assert fw.db_query("SELECT a FROM t", "shared")["a"].tolist() == [1]
    assert fw.db_query("SELECT a FROM t", "shared", read_only=True)["a"].tolist() == [1]
    assert fw.db_query("SELECT a FROM t", "duck", read_only=True)["a"].tolist() == [1]
    with fw.db_connect("duck", read_only=True) as conn:
        assert conn.read_only
    with pytest.raises(fw.FrameworkError, match="read_only=True applies to sqlite and duckdb"):
        fw.db_connect("warehouse", read_only=True)


@pytest.mark.parametrize("name", ["local", "duck"])
def test_settings_read_only_wins_over_the_call(db_project: Path, name: str) -> None:
    if name == "duck":
        pytest.importorskip("duckdb")
    fw.db_execute("CREATE TABLE t (a INTEGER)", name)
    driver = "sqlite" if name == "local" else "duckdb"
    suffix = "db" if name == "local" else "duckdb"
    (db_project / "settings.yml").write_text(
        "default:\n  connections:\n    databases:\n"
        f"      ro: {{driver: {driver}, database: data/local.{suffix}, read_only: true}}\n",
        encoding="utf-8",
    )
    (db_project / "sql/x.sql").write_text("INSERT INTO t VALUES (2);", encoding="utf-8")
    (db_project / "data/t.csv").write_text("a\n3\n", encoding="utf-8")
    refused = r"'ro' is read-only in its settings; read_only=False can't override that"
    with pytest.raises(fw.FrameworkError, match=refused):
        fw.db_connect("ro", read_only=False)
    with pytest.raises(fw.FrameworkError, match=refused):
        fw.db_query("SELECT a FROM t", "ro", read_only=False)
    with pytest.raises(fw.FrameworkError, match="'ro' is read-only"):
        fw.db_execute("INSERT INTO t VALUES (1)", "ro")
    with (
        fw.db_connect("ro") as conn,
        pytest.raises(fw.FrameworkError, match="'ro' is read-only"),
        fw.db_transaction(conn),
    ):
        pass
    with pytest.raises(fw.FrameworkError, match="'ro' is read-only"):
        fw.db_execute_file("ro", "sql/x.sql")
    with pytest.raises(fw.FrameworkError, match="'ro' is read-only"):
        fw.db_copy_from("ro", "t", "data/t.csv")
    # Adding read-only is always allowed
    with fw.db_connect("ro", read_only=True) as conn:
        assert conn.read_only
        with (
            pytest.raises(fw.FrameworkError, match=r"read_only: true in its settings"),
            fw.db_transaction(conn),
        ):
            pass
    assert fw.db_query("SELECT count(*) AS n FROM t", "ro")["n"].tolist() == [0]


def test_read_only_messages_say_where_the_mode_came_from(db_project: Path) -> None:
    fw.db_execute("CREATE TABLE t (a INTEGER)", "local")
    (db_project / "sql/x.sql").write_text("INSERT INTO t VALUES (2);", encoding="utf-8")
    (db_project / "data/t.csv").write_text("a\n3\n", encoding="utf-8")
    from_call = r"'local' is read-only \(read_only=True in this call\)"
    with fw.db_connect("local", read_only=True) as conn:
        with (
            pytest.raises(fw.FrameworkError, match=from_call + r".*db_transaction"),
            fw.db_transaction(conn),
        ):
            pass
        with pytest.raises(fw.FrameworkError, match=from_call + r".*db_execute_file"):
            fw.db_execute_file(conn, "sql/x.sql")
        with pytest.raises(fw.FrameworkError, match=from_call + r".*db_copy_from"):
            fw.db_copy_from(conn, "t", "data/t.csv")
