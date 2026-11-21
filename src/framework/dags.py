"""Causal diagrams (DAGs) as DAGitty model text, published to Framework Cloud.

A DAG is a ``framework.artifact/v1`` document with ``kind: "dag"``. Its ``dagitty`` field
holds DAGitty model text, which R's dagitty and ggdag and dagitty.net read. Framework Cloud
validates the graph, lays it out, and draws it, so this module only writes the text and
collects the labels, code, and analysis that ride along.

A graph comes from a ``networkx.DiGraph`` (or a subclass, such as pgmpy's ``DAG``) or
from DAGitty text. networkx is never imported: graphs are recognized by class name.
"""

from __future__ import annotations

import json
import math
import re
import tempfile
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from framework.errors import FrameworkError

SCHEMA = "framework.artifact/v1"
KIND = "dag"
ROLES = ("exposure", "outcome", "latent", "adjusted", "selected")
MAX_NAME = 100
# Names DAGitty can read without quotes. Graph-type words are quoted so they never read
# as a header.
_BARE = re.compile(r"[A-Za-z_][A-Za-z0-9_.]*")
_KEYWORDS = {"dag", "pdag", "mag", "pag", "graph", "digraph", "node", "edge"}
_EFFECTS = ("total", "direct")


# ---------------------------------------------------------------------------
# DAGitty text


def _quote(name: str) -> str:
    """A node name as DAGitty reads it: bare when it can be, else double-quoted."""
    if _BARE.fullmatch(name) and name.lower() not in _KEYWORDS:
        return name
    return '"' + name.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _number(value: float) -> str:
    return str(int(value)) if value.is_integer() and abs(value) < 1e15 else format(value, ".10g")


def _name(node: Any) -> str:
    name = str(node)
    if not name or name != name.strip() or len(name) > MAX_NAME:
        raise FrameworkError(
            f"DAG variable names are 1 to {MAX_NAME} characters without leading or trailing "
            f"spaces: {name!r}."
        )
    if any(ord(char) < 32 or ord(char) == 127 for char in name):
        raise FrameworkError(f"DAG variable names can't hold control characters: {name!r}.")
    return name


def _roles(name: str, value: Any) -> list[str]:
    """A node's ``role`` attribute: one role or a list of them."""
    if value is None:
        return []
    values = [value] if isinstance(value, str) else value
    if not isinstance(values, Iterable):
        raise FrameworkError(f'The role of {name} must be a string or a list, such as "exposure".')
    roles = set()
    for role in values:
        if role not in ROLES:
            raise FrameworkError(
                f"{name} has the role {role!r}. A role is one of {', '.join(ROLES)}."
            )
        roles.add(role)
    return [role for role in ROLES if role in roles]


def _position(name: str, value: Any) -> tuple[float, float] | None:
    if value is None:
        return None
    try:
        x, y = (float(part) for part in value)
    except (TypeError, ValueError):
        raise FrameworkError(
            f"The position of {name} must be two numbers, such as (1, 0)."
        ) from None
    if not (math.isfinite(x) and math.isfinite(y)):
        raise FrameworkError(f"The position of {name} must be two finite numbers.")
    return x, y


def _cycle(nodes: list[str], edges: list[tuple[str, str, str]]) -> list[str] | None:
    """A directed cycle as a path that ends where it starts, or None."""
    children: dict[str, list[str]] = {name: [] for name in nodes}
    for start, end, kind in edges:
        if kind == "->":
            children[start].append(end)
    state: dict[str, int] = {}
    for root in nodes:
        if state.get(root):
            continue
        path = [root]
        state[root] = 1
        stack = [iter(children[root])]
        while stack:
            child = next(stack[-1], None)
            if child is None:
                state[path.pop()] = 2
                stack.pop()
            elif state.get(child) == 1:
                return [*path[path.index(child) :], child]
            elif not state.get(child):
                state[child] = 1
                path.append(child)
                stack.append(iter(children[child]))
    return None


class _Graph:
    """A graph's nodes, roles, positions, labels, and edges, checked to be a DAG."""

    def __init__(self, source: Any, pos: Mapping[Any, Any] | None = None) -> None:
        if not _is_digraph(source):
            if _is_graph(source):
                raise FrameworkError(
                    "This networkx graph is undirected. A DAG needs a networkx.DiGraph."
                )
            raise FrameworkError(
                "A DAG is a networkx.DiGraph or DAGitty text, such as "
                'publish_dag("dag { x -> y }").'
            )
        if pos is not None and not isinstance(pos, Mapping):
            raise FrameworkError("pos must map node names to (x, y) positions.")
        latents = {str(node) for node in (getattr(source, "latents", None) or ())}
        self.names: list[str] = []
        self.roles: dict[str, list[str]] = {}
        self.positions: dict[str, tuple[float, float] | None] = {}
        self.labels: dict[str, str] = {}
        names: dict[Any, str] = {}
        for node, attributes in source.nodes(data=True):
            name = _name(node)
            if name in self.roles:
                raise FrameworkError(f"Two nodes are both named {name}.")
            names[node] = name
            self.names.append(name)
            roles = _roles(name, attributes.get("role"))
            if name in latents and "latent" not in roles:
                roles = [role for role in ROLES if role in {*roles, "latent"}]
            self.roles[name] = roles
            given = pos.get(node, pos.get(name)) if pos is not None else None
            if given is None:
                given = attributes.get("pos")
            self.positions[name] = _position(name, given)
            label = attributes.get("label")
            if label is not None and str(label).strip():
                self.labels[name] = str(label)
        if pos is not None:
            unknown = [str(key) for key in pos if key not in names and str(key) not in self.roles]
            if unknown:
                raise FrameworkError(
                    f"pos names nodes the graph doesn't have: {', '.join(unknown)}."
                )
        self.edges: list[tuple[str, str, str]] = []
        seen: set[tuple[str, str, str]] = set()
        for start, end, attributes in source.edges(data=True):
            a, b = names[start], names[end]
            if a == b:
                raise FrameworkError(f"{a} points to itself. A DAG has no loops.")
            if attributes.get("bidirected"):
                a, b = sorted((a, b))
                edge = (a, b, "<->")
            else:
                edge = (a, b, "->")
            if edge not in seen:
                seen.add(edge)
                self.edges.append(edge)
        cycle = _cycle(self.names, self.edges)
        if cycle is not None:
            raise FrameworkError(
                f"The graph has a cycle: {' -> '.join(cycle)}. A DAG has none. "
                "Mark an edge that stands for a latent common cause with bidirected=True."
            )

    def text(self) -> str:
        lines = ["dag {"]
        for name in self.names:
            attributes = list(self.roles[name])
            position = self.positions[name]
            if position is not None:
                attributes.append(f'pos="{_number(position[0])},{_number(position[1])}"')
            lines.append(_quote(name) + (f" [{','.join(attributes)}]" if attributes else ""))
        lines += [f"{_quote(a)} {kind} {_quote(b)}" for a, b, kind in self.edges]
        lines.append("}")
        return "\n".join(lines)


def _classes(source: Any) -> list[tuple[str, str]]:
    return [(cls.__module__.split(".")[0], cls.__name__) for cls in type(source).__mro__]


def _is_digraph(source: Any) -> bool:
    return ("networkx", "DiGraph") in _classes(source)


def _is_graph(source: Any) -> bool:
    return ("networkx", "Graph") in _classes(source)


def dagitty(graph: Any, *, pos: Mapping[Any, Any] | None = None) -> str:
    """DAGitty model text for a networkx graph.

    ::

        import networkx as nx

        g = nx.DiGraph()
        g.add_node("smoking", role="exposure", label="Smoking status")
        g.add_node("cancer", role="outcome")
        g.add_edges_from([("age", "smoking"), ("age", "cancer"), ("smoking", "cancer")])
        print(fw.dagitty(g))

    The text opens with ``dag {``, lists every node with its roles and position, then
    every edge. R's dagitty and ggdag and dagitty.net read it. A name that isn't a plain
    identifier, such as ``parental SES``, is double-quoted.

    The graph's node and edge attributes say what DAGitty needs:

    - ``role``: ``"exposure"``, ``"outcome"``, ``"latent"``, ``"adjusted"``, or
      ``"selected"``, or a list of them. A pgmpy ``DAG``'s ``latents`` are latent too.
    - ``pos``: an ``(x, y)`` position, with y pointing down as in DAGitty. networkx's
      layouts point y up, so negate y to keep their picture.
    - ``label``: the name to show. ``dagitty()`` leaves labels out; ``publish_dag()``
      sends them.
    - ``bidirected=True`` on an edge: ``a <-> b``, a latent common cause. The edge's
      direction doesn't matter, and it isn't part of the cycle check.

    Args:
        graph: A ``networkx.DiGraph``, or a subclass such as pgmpy's ``DAG``.
        pos: Positions by node, such as ``{"age": (0, 0), "smoking": (1, 1)}``. They win
            over ``pos`` node attributes.

    Returns:
        The DAGitty model text.

    Raises:
        FrameworkError: The graph is undirected or has a directed cycle, a node points to
            itself, or a role or position isn't valid.
    """
    return _Graph(graph, pos).text()


# ---------------------------------------------------------------------------
# The document


def _code(code: str | None) -> dict[str, str] | None:
    if code is None:
        return None
    if not isinstance(code, str):
        raise FrameworkError("code must be the Python code that built the graph, as a string.")
    text = code.strip("\n")
    return {"language": "python", "text": text} if text.strip() else None


def _labels(labels: Mapping[Any, Any] | None) -> dict[str, str]:
    if labels is None:
        return {}
    if not isinstance(labels, Mapping):
        raise FrameworkError('labels must map node names to labels: {"ses": "Parental SES"}.')
    return {str(name): str(label) for name, label in labels.items()}


def _set(value: Any, what: str) -> list[str]:
    if isinstance(value, str) or not isinstance(value, Iterable):
        raise FrameworkError(f"{what} must be a list of node names.")
    return [str(name) for name in value]


def _analysis(analysis: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """The producer's analysis, checked for shape. Framework computes none of it."""
    if analysis is None:
        return None
    if not isinstance(analysis, Mapping):
        raise FrameworkError('analysis must be a dict, such as {"adjustment_sets": [["age"]]}.')
    unknown = sorted(set(analysis) - {"effect", "adjustment_sets", "implications"})
    if unknown:
        raise FrameworkError(
            f"analysis takes effect, adjustment_sets, and implications, not {', '.join(unknown)}."
        )
    out: dict[str, Any] = {}
    effect = analysis.get("effect")
    if effect is not None:
        if effect not in _EFFECTS:
            raise FrameworkError('analysis["effect"] must be "total" or "direct".')
        out["effect"] = effect
    if analysis.get("adjustment_sets") is not None:
        sets = analysis["adjustment_sets"]
        if isinstance(sets, str) or not isinstance(sets, Iterable):
            raise FrameworkError(
                'analysis["adjustment_sets"] must be a list of sets of node names: '
                "[[]] when no adjustment is needed, [] when no set works."
            )
        out["adjustment_sets"] = [_set(item, "Each adjustment set") for item in sets]
    if analysis.get("implications") is not None:
        implications = []
        for item in analysis["implications"]:
            if not isinstance(item, Mapping) or "x" not in item or "y" not in item:
                raise FrameworkError(
                    'Each implication is {"x": "a", "y": "b", "given": ["c"]}: x is '
                    "independent of y given the nodes in given."
                )
            implications.append(
                {
                    "x": str(item["x"]),
                    "y": str(item["y"]),
                    "given": _set(item.get("given") or [], "given"),
                }
            )
        out["implications"] = implications
    return out or None


def _document(
    graph: Any,
    *,
    labels: Mapping[Any, Any] | None = None,
    code: str | None = None,
    analysis: Mapping[str, Any] | None = None,
    pos: Mapping[Any, Any] | None = None,
) -> dict[str, Any]:
    """The ``dag`` document for a graph or DAGitty text."""
    shown = _labels(labels)
    if isinstance(graph, Path):
        if not graph.is_file():
            raise FrameworkError(f"DAGitty file not found: {graph}")
        graph = graph.read_text(encoding="utf-8")
    if isinstance(graph, str):
        if pos is not None:
            raise FrameworkError('pos is for networkx graphs. In DAGitty text, write pos="x,y".')
        text = graph.strip()
        if not text:
            raise FrameworkError("The DAGitty text is empty.")
        source: dict[str, Any] = {"language": "python"}
    else:
        parsed = _Graph(graph, pos)
        unknown = sorted(set(shown) - set(parsed.names))
        if unknown:
            raise FrameworkError(
                f"labels names nodes the graph doesn't have: {', '.join(unknown)}."
            )
        shown = {**parsed.labels, **shown}
        text = parsed.text()
        source = {
            "language": "python",
            "class": [
                f"{cls.__module__}.{cls.__name__}"
                for cls in type(graph).__mro__
                if cls is not object
            ],
        }
    document: dict[str, Any] = {"schema": SCHEMA, "kind": KIND, "source": source, "dagitty": text}
    if shown:
        document["labels"] = shown
    written = _code(code)
    if written is not None:
        document["code"] = written
    checked = _analysis(analysis)
    if checked is not None:
        document["analysis"] = checked
    return document


def structured(graph: Any) -> dict[str, Any]:
    """The document ``publish()`` sends for a networkx graph."""
    return _document(graph)


# ---------------------------------------------------------------------------
# Publishing


def publish_dag(
    graph: Any,
    *,
    labels: Mapping[Any, Any] | None = None,
    code: str | None = None,
    analysis: Mapping[str, Any] | None = None,
    pos: Mapping[Any, Any] | None = None,
    slug: str | None = None,
    title: str | None = None,
    description: str | None = None,
    visibility: str | None = None,
    password: str | None = None,
    collection: str | int | None = None,
    share_unlisted: bool = False,
    to: str | None = None,
    include_private: bool = False,
) -> str | list[str]:
    """Publish a causal diagram (DAG) to Framework Cloud.

    ::

        g = nx.DiGraph()
        g.add_node("education", role="exposure")
        g.add_node("health", role="outcome")
        g.add_edges_from([("income", "education"), ("income", "health"),
                          ("education", "health")])
        fw.publish_dag(g, slug="education-health", code=CODE,
                       analysis={"adjustment_sets": [["income"]]})

        fw.publish_dag("dag { smoking [exposure] cancer [outcome] smoking -> cancer }",
                       slug="smoking")

    A ``networkx.DiGraph`` becomes DAGitty text as ``dagitty()`` writes it: roles,
    positions, and labels come from node attributes, and ``bidirected=True`` on an edge
    makes it ``<->``. A string is DAGitty text, sent as it is; a ``Path`` names a
    ``.dagitty`` file. ``publish(graph)`` publishes a networkx graph too, without code
    or analysis.

    Framework Cloud checks the graph, lays it out, and draws it, with the DAGitty text
    and the code to copy. A graph with a cycle, an undirected edge, more than 100 nodes,
    or more than 400 edges is refused. Framework computes no adjustment sets or
    implications. Pass what another tool found in ``analysis``.

    Args:
        graph: A ``networkx.DiGraph`` (or pgmpy ``DAG``), DAGitty text, or a ``Path`` to a
            ``.dagitty`` file.
        labels: Display labels by node name. They win over ``label`` node attributes.
        code: The Python code that built the graph, shown with it.
        analysis: What the producer computed: ``effect`` (``"total"`` or ``"direct"``),
            ``adjustment_sets`` (a list of lists of names: ``[[]]`` when no adjustment is
            needed, ``[]`` when no set works), and ``implications`` (``{"x", "y",
            "given"}`` conditional independencies).
        pos: Positions by node, such as ``{"age": (0, 0)}``, with y pointing down. For
            networkx graphs.
        slug: Artifact slug. Default: from ``title``, else ``dag``. Name each DAG, or a
            second one publishes as a new version of the first.
        title: Artifact title. Default: the slug.
        description: Artifact description.
        visibility: "public", "unlisted", or "private". Default: a new artifact is
            unlisted and an existing one keeps its setting.
        password: Password required to view the DAG.
        collection: Project collection ID or slug.
        share_unlisted: Allow collection readers to see an unlisted DAG.
        to: A configured destination name, or "cloud".
        include_private: Publish without asking about private folders.

    Returns:
        The artifact's URL.
    """
    from framework.publish_bundle import artifact_slug
    from framework.storage import publish

    document = _document(graph, labels=labels, code=code, analysis=analysis, pos=pos)
    slug = slug or (artifact_slug(title) if title else None)
    with tempfile.TemporaryDirectory(prefix="fw_dag_") as temp:
        path = Path(temp) / "dag.json"
        path.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
        return publish(
            path,
            kind=KIND,
            slug=slug,
            title=title,
            description=description,
            visibility=visibility,
            password=password,
            collection=collection,
            share_unlisted=share_unlisted,
            to=to,
            include_private=include_private,
        )
