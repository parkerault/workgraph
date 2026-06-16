"""C-1 — Store interface. Load/save graph.yaml; validation; atomic write; optimistic concurrency.

System of record. `transition` (C-3) is pure and never touches `base_hash`; the server holds it
from `load` and passes it to `save`. (The illustrative SPEC signature `save(Graph, base_hash)` is
threaded with `store_root` here so the store knows where to write.)
"""

from __future__ import annotations

import hashlib
import os

import yaml

from .errors import ConcurrencyError, ValidationError
from .models import Gate, GateKind, Graph, LastVerify, Node, Signoff, Status

_STORE_DIR = ".workgraph"
_GRAPH_FILE = "graph.yaml"


def _graph_path(store_root: str) -> str:
    return os.path.join(store_root, _STORE_DIR, _GRAPH_FILE)


def init_store(store_root: str) -> str:
    """Scaffold .workgraph/ with an empty valid graph.yaml + runs/ (idempotent). Returns the path."""
    runs = os.path.join(store_root, _STORE_DIR, "runs")
    os.makedirs(runs, exist_ok=True)
    gitignore = os.path.join(runs, ".gitignore")
    if not os.path.exists(gitignore):
        with open(gitignore, "w") as f:
            f.write("*\n!.gitignore\n")
    path = _graph_path(store_root)
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as f:
            f.write("version: 1\nworking_dir: .\nnodes: []\n")
    return path


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---- (de)serialization ------------------------------------------------------

def _node_from_dict(d: object) -> Node:
    if not isinstance(d, dict) or "id" not in d:
        raise ValidationError(None, "id", "node entry missing 'id'")
    nid = d["id"]
    gate_d = d.get("gate")
    if not isinstance(gate_d, dict) or "kind" not in gate_d:
        raise ValidationError(nid, "gate", "missing 'gate' (gate.kind required)")
    try:
        kind = GateKind(gate_d["kind"])
    except ValueError:
        raise ValidationError(nid, "gate.kind", f"unknown gate kind {gate_d['kind']!r}")
    gate = Gate(kind=kind, command=gate_d.get("command"), timeout=gate_d.get("timeout"))
    try:
        status = Status(d.get("status", "triage"))
    except ValueError:
        raise ValidationError(nid, "status", f"unknown status {d.get('status')!r}")
    signoff = None
    if d.get("signoff"):
        s = d["signoff"]
        signoff = Signoff(who=s.get("who"), at=s.get("at"), note=s.get("note"))
    last_verify = None
    if d.get("last_verify"):
        lv = d["last_verify"]
        last_verify = LastVerify(
            exit_code=lv.get("exit_code"), ran_at=lv.get("ran_at"), log=lv.get("log")
        )
    return Node(
        id=nid,
        gate=gate,
        kind=d.get("kind", "unit"),
        parent=d.get("parent"),
        deps=list(d.get("deps", [])),
        status=status,
        rationale=d.get("rationale"),
        signoff=signoff,
        last_verify=last_verify,
        updated_at=d.get("updated_at"),
        updated_by=d.get("updated_by"),
    )


def _node_to_dict(n: Node) -> dict:
    """Ordered, compact mapping — empty optionals omitted (NFR-4), stable key order (AC-16)."""
    d: dict = {"id": n.id, "kind": n.kind}
    if n.parent is not None:
        d["parent"] = n.parent
    d["deps"] = list(n.deps)
    d["status"] = n.status.value
    gate: dict = {"kind": n.gate.kind.value}
    if n.gate.command is not None:
        gate["command"] = n.gate.command
    if n.gate.timeout is not None:
        gate["timeout"] = n.gate.timeout
    d["gate"] = gate
    if n.rationale is not None:
        d["rationale"] = n.rationale
    if n.signoff is not None:
        s = {"who": n.signoff.who, "at": n.signoff.at}
        if n.signoff.note is not None:
            s["note"] = n.signoff.note
        d["signoff"] = s
    if n.last_verify is not None:
        lv = {"exit_code": n.last_verify.exit_code, "ran_at": n.last_verify.ran_at}
        if n.last_verify.log is not None:
            lv["log"] = n.last_verify.log
        d["last_verify"] = lv
    if n.updated_at is not None:
        d["updated_at"] = n.updated_at
    if n.updated_by is not None:
        d["updated_by"] = n.updated_by
    return d


def _serialize(graph: Graph) -> str:
    doc = {
        "version": graph.version,
        "working_dir": graph.working_dir,
        "nodes": [_node_to_dict(n) for n in graph.nodes.values()],
    }
    return yaml.dump(doc, sort_keys=False, default_flow_style=False, allow_unicode=True)


# ---- validation -------------------------------------------------------------

def _validate(graph: Graph) -> None:
    ids = set(graph.nodes)
    for nid, n in graph.nodes.items():
        if n.gate.kind == GateKind.COMMAND and not n.gate.command:
            raise ValidationError(nid, "gate.command", "command gate requires a command")
        for dep in n.deps:
            if dep not in ids:
                raise ValidationError(nid, "deps", f"unknown dependency {dep!r}")
        if n.parent is not None:
            if n.parent == nid:
                raise ValidationError(nid, "parent", "node cannot be its own parent")
            if n.parent not in ids:
                raise ValidationError(nid, "parent", f"unknown parent {n.parent!r}")
    _check_parent_acyclic(graph)
    from .graph import detect_cycle  # local import avoids a module-load cycle

    cyc = detect_cycle(graph)
    if cyc is not None:
        raise ValidationError(cyc[0], "deps", f"dependency cycle: {' -> '.join(cyc)}")


def _check_parent_acyclic(graph: Graph) -> None:
    for start in graph.nodes:
        seen = set()
        cur = graph.nodes[start].parent
        while cur is not None:
            if cur == start or cur in seen:
                raise ValidationError(start, "parent", "parent chain forms a cycle")
            seen.add(cur)
            cur = graph.nodes[cur].parent if cur in graph.nodes else None


# ---- C-1 API ----------------------------------------------------------------

def load(store_root: str) -> tuple[Graph, str]:
    path = _graph_path(store_root)
    with open(path, "rb") as f:
        raw = f.read()
    base_hash = _hash(raw)
    try:
        doc = yaml.safe_load(raw) or {}
    except yaml.YAMLError as e:
        raise ValidationError(None, _GRAPH_FILE, f"unparseable graph.yaml: {e}")
    if not isinstance(doc, dict):
        raise ValidationError(None, _GRAPH_FILE, "graph.yaml must be a mapping")
    graph = Graph(version=doc.get("version", 1), working_dir=doc.get("working_dir", "."))
    for entry in doc.get("nodes") or []:
        node = _node_from_dict(entry)
        if node.id in graph.nodes:
            raise ValidationError(node.id, "id", "duplicate node id")
        graph.nodes[node.id] = node
    _validate(graph)
    return graph, base_hash


def save(store_root: str, graph: Graph, base_hash: str) -> str:
    _validate(graph)
    path = _graph_path(store_root)
    with open(path, "rb") as f:
        current = f.read()
    if _hash(current) != base_hash:
        raise ConcurrencyError(
            "graph.yaml changed on disk since it was read; reload and retry"
        )
    text = _serialize(graph)
    data = text.encode("utf-8")
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)  # atomic on POSIX
    return _hash(data)


def write_rationale(store_root: str, node_id: str, text: str) -> str:
    rel = os.path.join("rationale", f"{node_id}.md")
    abspath = os.path.join(store_root, _STORE_DIR, rel)
    os.makedirs(os.path.dirname(abspath), exist_ok=True)
    with open(abspath, "w", encoding="utf-8") as f:
        f.write(text if text.endswith("\n") else text + "\n")
    graph, base_hash = load(store_root)
    if node_id in graph.nodes:
        graph.nodes[node_id].rationale = rel
        save(store_root, graph, base_hash)
    return rel
