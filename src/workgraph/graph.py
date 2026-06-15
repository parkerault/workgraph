"""C-2 — Graph/plan interface. Topo waves + cycle detection (graphlib), readiness, rollup.

Pure, no I/O.
"""

from __future__ import annotations

from graphlib import CycleError, TopologicalSorter

from .models import TERMINAL_GOOD, Graph, Status


def _sorter(graph: Graph) -> TopologicalSorter:
    ts: TopologicalSorter = TopologicalSorter()
    for nid, n in graph.nodes.items():
        ts.add(nid, *n.deps)  # n depends on each dep -> deps come first
    return ts


def waves(graph: Graph) -> list[list[str]]:
    """Topo generations: each `get_ready()` generation snapshotted as one wave (AC-1)."""
    ts = _sorter(graph)
    ts.prepare()  # raises CycleError on a cyclic graph
    result: list[list[str]] = []
    while ts.is_active():
        gen = sorted(ts.get_ready())  # sort for deterministic output
        result.append(gen)
        for nid in gen:
            ts.done(nid)
    return result


def detect_cycle(graph: Graph) -> list[str] | None:
    """Return the nodes of one cycle via prepare()/CycleError, else None (AC-2)."""
    try:
        _sorter(graph).prepare()
    except CycleError as e:
        # e.args[1] is a list of nodes forming one (arbitrary) cycle.
        return list(e.args[1])
    return None


def ready_nodes(graph: Graph) -> list[str]:
    """Ids whose every dependency is terminal-good (AC-15). Nodes with no deps qualify."""
    out = []
    for nid, n in graph.nodes.items():
        if all(
            dep in graph.nodes and graph.nodes[dep].status in TERMINAL_GOOD for dep in n.deps
        ):
            out.append(nid)
    return out


def rollup(graph: Graph, parent_id: str | None = None) -> dict[str, int]:
    """Counts of nodes by status — whole graph (parent_id=None) or children of parent_id."""
    counts: dict[str, int] = {}
    for n in graph.nodes.values():
        if parent_id is not None and n.parent != parent_id:
            continue
        counts[n.status.value] = counts.get(n.status.value, 0) + 1
    return counts
