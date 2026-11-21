"""DAGs: DAGitty text from networkx graphs, and publishing them as kind dag."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

import framework as fw
from framework import dags
from framework.errors import FrameworkError
from tests.test_cloud import FakeCloud, cloud_project, server  # noqa: F401

nx = pytest.importorskip("networkx")


def smoking() -> Any:
    graph = nx.DiGraph()
    graph.add_node("smoking", role="exposure", label="Smoking status")
    graph.add_node("cancer", role="outcome")
    graph.add_edges_from([("age", "smoking"), ("smoking", "cancer"), ("age", "cancer")])
    return graph


def published_document(cloud: FakeCloud) -> dict[str, Any]:
    """The dag.json part of the last multipart publish request."""
    body = cloud.requests[-1].content.decode("utf-8")
    part = body.split('filename="dag.json"', 1)[1].split("\r\n\r\n", 1)[1]
    return json.loads(part.rsplit("\r\n--", 1)[0])


@pytest.fixture
def documents(server: FakeCloud) -> FakeCloud:  # noqa: F811
    server.route(
        "POST",
        "/api/v1/documents",
        lambda r: httpx.Response(201, json={"id": 7, "url": "https://x/d/dag", "version": 1}),
    )
    return server


# DAGitty text --------------------------------------------------------------------------


def test_nodes_with_roles_then_edges() -> None:
    assert fw.dagitty(smoking()) == (
        "dag {\nsmoking [exposure]\ncancer [outcome]\nage\n"
        "smoking -> cancer\nage -> smoking\nage -> cancer\n}"
    )


@pytest.mark.parametrize(
    ("name", "written"),
    [
        ("income", "income"),
        ("x_1.lag", "x_1.lag"),
        ("parental SES", '"parental SES"'),
        ("2020", '"2020"'),
        ("a-b", '"a-b"'),
        ("dag", '"dag"'),
        ('say "hi"', '"say \\"hi\\""'),
        ("back\\slash", '"back\\\\slash"'),
        ("état", '"état"'),
    ],
)
def test_names_are_quoted_unless_plain(name: str, written: str) -> None:
    graph = nx.DiGraph([(name, "y")])
    assert fw.dagitty(graph).splitlines()[3] == f"{written} -> y"


def test_roles_in_dagitty_order_and_pgmpy_latents() -> None:
    graph = nx.DiGraph()
    graph.add_node("u", role=["latent", "adjusted"])
    graph.add_node("w", role=("selected", "exposure"))
    graph.latents = {"w"}  # as pgmpy's DAG keeps them
    lines = fw.dagitty(graph).splitlines()
    assert lines[1:3] == ["u [latent,adjusted]", "w [exposure,latent,selected]"]


def test_unknown_role_is_refused() -> None:
    graph = nx.DiGraph()
    graph.add_node("x", role="treatment")
    with pytest.raises(FrameworkError, match="role 'treatment'"):
        fw.dagitty(graph)


def test_positions_from_attributes_and_the_pos_mapping() -> None:
    graph = nx.DiGraph([("a", "b")])
    graph.nodes["a"]["pos"] = (0, 1.5)
    graph.nodes["b"]["pos"] = (9, 9)
    text = fw.dagitty(graph, pos={"b": (2, -0.25)})
    assert 'a [pos="0,1.5"]' in text
    assert 'b [pos="2,-0.25"]' in text


def test_positions_must_be_two_finite_numbers() -> None:
    graph = nx.DiGraph([("a", "b")])
    with pytest.raises(FrameworkError, match="two numbers"):
        fw.dagitty(graph, pos={"a": (1, 2, 3)})
    with pytest.raises(FrameworkError, match="finite"):
        fw.dagitty(graph, pos={"a": (1, float("nan"))})
    with pytest.raises(FrameworkError, match="doesn't have: c"):
        fw.dagitty(graph, pos={"c": (1, 2)})


def test_bidirected_edges_are_written_once_and_skip_the_cycle_check() -> None:
    graph = nx.DiGraph([("x", "y")])
    graph.add_edge("y", "x", bidirected=True)
    graph.add_edge("z", "y", bidirected=True)
    graph.add_edge("y", "z", bidirected=True)
    edges = [line for line in fw.dagitty(graph).splitlines() if " " in line and "{" not in line]
    assert edges == ["x -> y", "x <-> y", "y <-> z"]


def test_a_cycle_is_refused_with_its_path() -> None:
    graph = nx.DiGraph([("a", "b"), ("b", "c"), ("c", "a"), ("c", "d")])
    with pytest.raises(FrameworkError, match="cycle: a -> b -> c -> a"):
        fw.dagitty(graph)


def test_a_self_loop_is_refused() -> None:
    with pytest.raises(FrameworkError, match="points to itself"):
        fw.dagitty(nx.DiGraph([("a", "a")]))


def test_undirected_graphs_and_other_objects_are_refused() -> None:
    with pytest.raises(FrameworkError, match="undirected"):
        fw.dagitty(nx.Graph([("a", "b")]))
    with pytest.raises(FrameworkError, match=r"networkx\.DiGraph or DAGitty text"):
        fw.dagitty({"a": "b"})


def test_names_that_dagitty_cannot_hold_are_refused() -> None:
    with pytest.raises(FrameworkError, match="leading or trailing"):
        fw.dagitty(nx.DiGraph([(" a", "b")]))
    with pytest.raises(FrameworkError, match="control characters"):
        fw.dagitty(nx.DiGraph([("a\tb", "c")]))
    with pytest.raises(FrameworkError, match="both named 1"):
        fw.dagitty(nx.DiGraph([(1, "1")]))


def test_multidigraph_edges_are_written_once() -> None:
    graph = nx.MultiDiGraph([("a", "b"), ("a", "b")])
    assert fw.dagitty(graph).count("a -> b") == 1


# The document --------------------------------------------------------------------------


def test_document_from_a_graph() -> None:
    document = dags._document(
        smoking(),
        labels={"cancer": "Lung cancer"},
        code="g = nx.DiGraph()\n",
        analysis={"effect": "total", "adjustment_sets": [{"age"}], "implications": []},
    )
    assert document["schema"] == "framework.artifact/v1"
    assert document["kind"] == "dag"
    assert document["source"]["language"] == "python"
    assert document["source"]["class"][0] == "networkx.classes.digraph.DiGraph"
    assert document["dagitty"].startswith("dag {\nsmoking [exposure]")
    assert document["labels"] == {"smoking": "Smoking status", "cancer": "Lung cancer"}
    assert document["code"] == {"language": "python", "text": "g = nx.DiGraph()"}
    assert document["analysis"] == {
        "effect": "total",
        "adjustment_sets": [["age"]],
        "implications": [],
    }


def test_document_from_dagitty_text_and_a_file(tmp_path: Path) -> None:
    text = "dag {\nx [exposure]\ny [outcome]\nx -> y\n}"
    document = dags._document(text + "\n", labels={"x": "Dose"})
    assert document == {
        "schema": "framework.artifact/v1",
        "kind": "dag",
        "source": {"language": "python"},
        "dagitty": text,
        "labels": {"x": "Dose"},
    }
    (tmp_path / "model.dagitty").write_text(text, encoding="utf-8")
    assert dags._document(tmp_path / "model.dagitty")["dagitty"] == text


def test_document_refusals() -> None:
    with pytest.raises(FrameworkError, match="empty"):
        dags._document("  ")
    with pytest.raises(FrameworkError, match="pos is for networkx"):
        dags._document("dag { x -> y }", pos={"x": (0, 0)})
    with pytest.raises(FrameworkError, match="doesn't have: z"):
        dags._document(smoking(), labels={"z": "Zed"})
    with pytest.raises(FrameworkError, match="not adjustment"):
        dags._document(smoking(), analysis={"adjustment": [["age"]]})
    with pytest.raises(FrameworkError, match="total"):
        dags._document(smoking(), analysis={"effect": "indirect"})
    with pytest.raises(FrameworkError, match="Each implication"):
        dags._document(smoking(), analysis={"implications": [["a", "b"]]})


def test_no_adjustment_and_no_valid_set_are_kept_apart() -> None:
    assert dags._analysis({"adjustment_sets": [[]]}) == {"adjustment_sets": [[]]}
    assert dags._analysis({"adjustment_sets": []}) == {"adjustment_sets": []}


# Publishing ----------------------------------------------------------------------------


def test_publish_dag_sends_dag_json_with_kind_dag(
    cloud_project: Path,  # noqa: F811
    documents: FakeCloud,
) -> None:
    url = fw.publish_dag(
        smoking(),
        code="g = smoking()",
        title="Smoking and cancer",
        visibility="unlisted",
        to="cloud",
    )
    assert url == "https://x/d/dag"
    body = documents.requests[-1].content
    assert b'name="kind"\r\n\r\ndag' in body
    assert b'name="slug"\r\n\r\nsmoking-and-cancer' in body
    assert b'name="title"\r\n\r\nSmoking and cancer' in body
    document = published_document(documents)
    assert document["kind"] == "dag"
    assert document["code"] == {"language": "python", "text": "g = smoking()"}
    assert document["labels"] == {"smoking": "Smoking status"}


def test_publish_dag_sends_dagitty_text_as_is(
    cloud_project: Path,  # noqa: F811
    documents: FakeCloud,
) -> None:
    fw.publish_dag('dag { "parental SES" -> y }', slug="ses", to="cloud")
    body = documents.requests[-1].content
    assert b'name="slug"\r\n\r\nses' in body
    assert published_document(documents)["dagitty"] == 'dag { "parental SES" -> y }'


def test_publish_takes_a_networkx_graph(
    cloud_project: Path,  # noqa: F811
    documents: FakeCloud,
) -> None:
    fw.publish(smoking(), to="cloud", slug="smoking")
    body = documents.requests[-1].content
    assert b'name="kind"\r\n\r\ndag' in body
    assert published_document(documents)["dagitty"].startswith("dag {\nsmoking [exposure]")


def test_publish_refuses_a_cyclic_graph_before_sending(
    cloud_project: Path,  # noqa: F811
    documents: FakeCloud,
) -> None:
    with pytest.raises(FrameworkError, match="cycle"):
        fw.publish(nx.DiGraph([("a", "b"), ("b", "a")]), to="cloud")
    with pytest.raises(FrameworkError, match="cycle"):
        fw.publish_dag(nx.DiGraph([("a", "b"), ("b", "a")]), to="cloud")
    assert all(r.method == "GET" for r in documents.requests)
