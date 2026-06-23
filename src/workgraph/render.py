"""Mermaid emitter — project a graph (or a slice) to deterministic mermaid `graph` text.

Pure, no I/O. Status is baked into each node's label so it survives every renderer (incl.
mermaid-ascii's `--ascii` mode and GitHub). The agent renders the text itself (e.g. pipes it to the
`mermaid-ascii` binary); workgraph never shells out. Node order follows insertion order for stable
diffs.
"""

from __future__ import annotations

from .models import Graph


def _neighborhood(graph: Graph, center: str, depth: int) -> set[str]:
    if center not in graph.nodes:
        return set()
    deps = {nid: set(n.deps) & set(graph.nodes) for nid, n in graph.nodes.items()}
    dependents: dict[str, set[str]] = {nid: set() for nid in graph.nodes}
    for nid, ds in deps.items():
        for d in ds:
            dependents[d].add(nid)
    seen, frontier = {center}, {center}
    for _ in range(max(0, depth)):
        nxt: set[str] = set()
        for x in frontier:
            nxt |= deps.get(x, set()) | dependents.get(x, set())
        nxt -= seen
        if not nxt:
            break
        seen |= nxt
        frontier = nxt
    return seen


def _select(graph: Graph, parent, status, node, depth) -> set[str]:
    if parent is not None:
        return {nid for nid, n in graph.nodes.items() if nid == parent or n.parent == parent}
    if status is not None:
        wanted = {p.strip() for p in status.split(",") if p.strip()}  # one or more states
        return {nid for nid, n in graph.nodes.items() if n.status.value in wanted}
    if node is not None:
        return _neighborhood(graph, node, depth)
    return set(graph.nodes)


def to_mermaid(
    graph: Graph,
    *,
    direction: str = "auto",
    parent: str | None = None,
    status: str | None = None,
    node: str | None = None,
    depth: int = 1,
) -> str:
    """Render the selected slice as mermaid. Selectors (first non-None wins): `parent` (it + its
    children), `status` (nodes in that state), `node`+`depth` (dependency neighborhood); default is
    the whole graph. Edges are emitted only when both endpoints are in the slice.

    `direction` is `TD` / `LR`, or `auto` (default): a slice with **no** in-slice edges — e.g. a
    status query of unrelated nodes — renders `LR`, which stacks the independent nodes into a
    vertical column (terminal-friendly); a connected slice renders `TD` (dependency chains run
    top-to-bottom). An explicit `TD`/`LR` always wins."""
    ids = _select(graph, parent, status, node, depth)
    edges = [
        (d, nid)
        for nid, n in graph.nodes.items()
        if nid in ids
        for d in n.deps
        if d in ids
    ]
    if direction == "auto":
        direction = "TD" if edges else "LR"
    lines = [f"graph {direction}"]
    for nid, n in graph.nodes.items():
        if nid in ids:
            label = f"{nid} [{n.status.value}]".replace('"', "'")
            lines.append(f'{nid}["{label}"]')
    for d, nid in edges:
        lines.append(f"{d} --> {nid}")
    return "\n".join(lines) + "\n"
