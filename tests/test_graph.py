"""C-2 graph engine — AC-1, AC-2, AC-15, AC-20, AC-22, NFR-1/2. Pure functions."""

from __future__ import annotations

from workgraph import graph as G
from workgraph.models import Gate, GateKind, Graph, Node, Status


def node(nid, deps=None, status=Status.TRIAGE, parent=None):
    return Node(
        id=nid,
        gate=Gate(kind=GateKind.NONE),
        deps=list(deps or []),
        status=status,
        parent=parent,
    )


def make(*nodes):
    g = Graph()
    for n in nodes:
        g.nodes[n.id] = n
    return g


# ---- waves (AC-1) -----------------------------------------------------------

def test_waves_linear_chain():
    g = make(node("a"), node("b", deps=["a"]), node("c", deps=["b"]))
    assert G.waves(g) == [["a"], ["b"], ["c"]]


def test_waves_concurrent_generation():
    g = make(
        node("a"),
        node("b", deps=["a"]),
        node("c", deps=["a"]),
        node("d", deps=["b", "c"]),
    )
    assert G.waves(g) == [["a"], ["b", "c"], ["d"]]


def test_waves_every_node_after_its_deps():
    g = make(node("a"), node("b", deps=["a"]), node("c", deps=["a", "b"]))
    waves = G.waves(g)
    pos = {nid: i for i, w in enumerate(waves) for nid in w}
    for n in g.nodes.values():
        for d in n.deps:
            assert pos[d] < pos[n.id]


def test_waves_empty_graph():
    assert G.waves(Graph()) == []


# ---- cycle detection (AC-2) -------------------------------------------------

def test_detect_cycle_none_for_dag():
    g = make(node("a"), node("b", deps=["a"]))
    assert G.detect_cycle(g) is None


def test_detect_cycle_returns_a_cycle():
    g = make(node("a", deps=["b"]), node("b", deps=["a"]))
    cyc = G.detect_cycle(g)
    assert cyc is not None and set(cyc) <= {"a", "b"} and len(cyc) >= 2


def test_detect_self_dependency_is_a_cycle():
    g = make(node("a", deps=["a"]))
    assert G.detect_cycle(g) is not None


# ---- readiness (AC-15) ------------------------------------------------------

def test_ready_nodes_when_deps_terminal_good():
    g = make(
        node("a", status=Status.DONE),
        node("b", deps=["a"]),  # dep done -> deps satisfied
        node("d"),
        node("c", deps=["d"]),  # dep d is triage -> not satisfied
    )
    ready = set(G.ready_nodes(g))
    assert "b" in ready
    assert "c" not in ready


def test_resolved_counts_as_terminal_good():
    g = make(node("a", status=Status.RESOLVED), node("b", deps=["a"]))
    assert "b" in set(G.ready_nodes(g))


def test_deferred_dep_is_not_terminal_good():
    g = make(node("a", status=Status.DEFERRED), node("b", deps=["a"]))
    assert "b" not in set(G.ready_nodes(g))


# ---- rollup (AC-4 / AC-20 / AC-22) -----------------------------------------

def test_rollup_counts_whole_graph_by_status():
    g = make(
        node("a", status=Status.DONE),
        node("b", status=Status.READY),
        node("c", status=Status.READY),
    )
    assert G.rollup(g) == {"done": 1, "ready": 2}


def test_rollup_scoped_to_parent_children():
    g = make(
        node("m"),
        node("x", parent="m", status=Status.DONE),
        node("y", parent="m", status=Status.TRIAGE),
        node("z", status=Status.READY),  # not a child of m
    )
    assert G.rollup(g, "m") == {"done": 1, "triage": 1}


def test_rollup_empty_graph():
    assert G.rollup(Graph()) == {}
