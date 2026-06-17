"""Mermaid emitter — pure projection of a graph/slice to mermaid text."""

from __future__ import annotations

from workgraph import render
from workgraph.models import Gate, GateKind, Graph, Node, Status


def nd(nid, status=Status.TRIAGE, deps=None, parent=None):
    return Node(id=nid, gate=Gate(kind=GateKind.NONE), status=status, deps=list(deps or []), parent=parent)


def g(*nodes):
    G = Graph()
    for n in nodes:
        G.nodes[n.id] = n
    return G


def test_whole_graph_header_nodes_edges():
    m = render.to_mermaid(g(nd("a", Status.DONE), nd("b", Status.READY, deps=["a"])))
    assert m.splitlines()[0] == "graph TD"
    assert 'a["a [done]"]' in m
    assert 'b["b [ready]"]' in m
    assert "a --> b" in m


def test_direction_lr():
    assert render.to_mermaid(g(nd("a")), direction="LR").splitlines()[0] == "graph LR"


def test_status_baked_into_label():
    assert "[blocked]" in render.to_mermaid(g(nd("x", Status.BLOCKED)))


def test_parent_slice_includes_parent_and_children_only():
    m = render.to_mermaid(
        g(nd("m"), nd("c1", parent="m"), nd("c2", parent="m"), nd("other")), parent="m"
    )
    assert 'm["' in m and 'c1["' in m and 'c2["' in m
    assert 'other["' not in m


def test_status_slice():
    m = render.to_mermaid(
        g(nd("a", Status.ACTIVE), nd("b", Status.BLOCKED), nd("c", Status.ACTIVE)), status="active"
    )
    assert 'a["' in m and 'c["' in m and 'b["' not in m


def test_status_slice_accepts_comma_separated_states():
    G = g(nd("a", Status.ACTIVE), nd("b", Status.BLOCKED), nd("c", Status.READY), nd("d", Status.DONE))
    m = render.to_mermaid(G, status="active, blocked , ready")  # whitespace tolerated
    assert all(f'{x}["' in m for x in ("a", "b", "c"))
    assert 'd["' not in m  # done excluded


def test_node_neighborhood_depth_1():
    G = g(nd("a"), nd("b", deps=["a"]), nd("c", deps=["b"]), nd("d", deps=["c"]))
    m = render.to_mermaid(G, node="b", depth=1)  # {a,b,c}, not d
    assert all(f'{x}["' in m for x in ("a", "b", "c"))
    assert 'd["' not in m


def test_edges_only_within_slice():
    # a is outside the 'active' slice, so the a->b edge must not be emitted (no dangling ref)
    m = render.to_mermaid(g(nd("a", Status.DONE), nd("b", Status.ACTIVE, deps=["a"])), status="active")
    assert "a --> b" not in m
    assert 'b["' in m


def test_empty_slice_is_just_the_header():
    assert render.to_mermaid(g(nd("a", Status.DONE)), status="blocked").strip() == "graph TD"


def test_node_order_is_stable_insertion_order():
    m = render.to_mermaid(g(nd("first"), nd("second"), nd("third")))
    body = [ln for ln in m.splitlines() if ln.endswith('"]')]
    assert body == ['first["first [triage]"]', 'second["second [triage]"]', 'third["third [triage]"]']
