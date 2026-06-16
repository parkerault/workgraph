"""Service layer — the operations behind the MCP tools (C-5 core).

Each method composes the contracts: load (C-1) → graph-op (C-2) / transition (C-3) / gate (C-4) →
write_rationale (C-1) → save (C-1). Pure typed errors bubble up; the MCP binding maps them to the
structured error envelope. The `surface` argument carries the calling tool's group (execute/plan)
so the in-tool governance clamp (AC-12) holds even at this layer.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

from . import graph as G
from . import lifecycle as L
from . import render
from . import store
from .errors import IllegalTransition, ValidationError
from .gate import run_gate
from .models import Gate, GateKind, LastVerify, Status

DEFAULT_TIMEOUT = 120  # seconds (D-8/D-13)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stamp(graph, node_id: str, who: str | None, surface: str) -> None:
    """Record provenance on a node: when it was last changed, and by whom (the caller-supplied
    actor — a human handle or an agent role/task — falling back to the surface if unspecified)."""
    n = graph.nodes.get(node_id)
    if n is not None:
        n.updated_at = _now()
        n.updated_by = who or surface


def _nudge(node_id: str, status: str) -> str:
    """A reminder, returned with every state-changing mutation, to reconcile the project's prose
    (status docs, work logs, comments) with the workgraph. The workgraph is the source of truth and
    prose must not drift from it — the tool exists so 'deferred' can never be mistaken for 'done', so
    the terminal transitions carry the sharpest wording."""
    if status == "done":
        return (
            f"'{node_id}' is now done. Record it complete in local docs/work logs — with the gate "
            "evidence / sign-off — so completion is never left ambiguous. The workgraph is the source of truth."
        )
    if status == "resolved":
        return (
            f"'{node_id}' is now resolved. Record the decision and its rationale in local docs so it "
            "reads as settled, not still open. The workgraph is the source of truth."
        )
    if status in ("deferred", "archived"):
        return (
            f"'{node_id}' is now {status}: set aside, NOT completed. Update local docs/work logs so "
            "nothing implies it shipped. The workgraph is the source of truth."
        )
    if status == "blocked":
        return (
            f"'{node_id}' is now blocked. Note the blocker in the work log so the prose explains the "
            "stall. The workgraph is the source of truth."
        )
    return (
        f"'{node_id}' is now {status} in the workgraph. Reconcile local docs/work logs that describe "
        "this work so the prose matches the workgraph (the workgraph is the source of truth)."
    )


class Service:
    def __init__(self, store_root: str):
        self.root = store_root

    # ----- reads --------------------------------------------------------------

    def plan(self) -> list[list[str]]:
        g, _ = store.load(self.root)
        return G.waves(g)

    def ready(self) -> list[str]:
        g, _ = store.load(self.root)
        return [nid for nid, n in g.nodes.items() if n.status == Status.READY]

    def status(self, node_id: str | None = None, status: str | None = None) -> dict:
        g, _ = store.load(self.root)
        if status is not None:
            return {"status": status, "ids": [nid for nid, n in g.nodes.items() if n.status.value == status]}
        if node_id is None:
            parents = sorted({n.parent for n in g.nodes.values() if n.parent})
            return {
                "total": len(g.nodes),
                "counts": G.rollup(g),
                "by_parent": {p: G.rollup(g, p) for p in parents},
            }
        n = self._require(g, node_id)
        out = {
            "id": n.id,
            "status": n.status.value,
            "kind": n.kind,
            "gate_kind": n.gate.kind.value,
        }
        if n.updated_at is not None:
            out["updated_at"] = n.updated_at
        if n.updated_by is not None:
            out["updated_by"] = n.updated_by
        if n.last_verify is not None:
            out["last_verify"] = {
                "exit_code": n.last_verify.exit_code,
                "ran_at": n.last_verify.ran_at,
            }
        if n.signoff is not None:
            out["signoff"] = {"who": n.signoff.who, "at": n.signoff.at}
        children = G.rollup(g, n.id)
        if children:
            out["children"] = children
        return out

    def mermaid(
        self,
        direction: str = "TD",
        parent: str | None = None,
        status: str | None = None,
        node: str | None = None,
        depth: int = 1,
    ) -> dict:
        g, _ = store.load(self.root)
        text = render.to_mermaid(
            g, direction=direction, parent=parent, status=status, node=node, depth=depth
        )
        return {"mermaid": text}

    def show(self, node_id: str) -> dict:
        g, _ = store.load(self.root)
        n = self._require(g, node_id)
        gate = {"kind": n.gate.kind.value}
        if n.gate.command is not None:
            gate["command"] = n.gate.command
        if n.gate.timeout is not None:
            gate["timeout"] = n.gate.timeout
        return {
            "id": n.id,
            "status": n.status.value,
            "kind": n.kind,
            "parent": n.parent,
            "deps": list(n.deps),
            "gate": gate,
            "rationale": n.rationale,
            "signoff": (
                {"who": n.signoff.who, "at": n.signoff.at, "note": n.signoff.note}
                if n.signoff
                else None
            ),
            "last_verify": (
                {
                    "exit_code": n.last_verify.exit_code,
                    "ran_at": n.last_verify.ran_at,
                    "log": n.last_verify.log,
                }
                if n.last_verify
                else None
            ),
        }

    # ----- declaration (plan surface) ----------------------------------------

    def ingest(self, nodes: list[dict], who: str | None = None, surface: str = "plan") -> dict:
        if surface != L.PLAN:
            from .errors import SurfaceDenied

            raise SurfaceDenied("ingest", surface)
        g, h = store.load(self.root)
        ingested = []
        for d in nodes:
            node = store._node_from_dict(d)
            node.status = Status.TRIAGE
            if node.id in g.nodes:
                raise ValidationError(node.id, "id", "duplicate node id")
            g.nodes[node.id] = node
            ingested.append(node.id)
        L.recompute_readiness(g)
        for nid in ingested:
            _stamp(g, nid, who, surface)
        store.save(self.root, g, h)  # validates refs + cycle atomically (store unchanged on error)
        nudge = (
            f"Now tracking {len(ingested)} node(s) in the workgraph: {', '.join(ingested)}. "
            "Reflect them in local planning docs/work logs so the prose matches the workgraph "
            "(the workgraph is the source of truth)."
        )
        return {"ingested": ingested, "nudge": nudge}

    def add_node(self, node: dict, who: str | None = None, surface: str = "plan") -> dict:
        g, h = store.load(self.root)
        new = store._node_from_dict(node)
        g2 = L.add_node(g, new, surface)
        _stamp(g2, new.id, who, surface)
        store.save(self.root, g2, h)
        out = self.status(new.id)
        out["nudge"] = _nudge(new.id, out["status"])
        return out

    def set_gate(self, node_id: str, gate: dict, who: str | None = None, surface: str = "plan") -> dict:
        g, h = store.load(self.root)
        gobj = Gate(
            kind=GateKind(gate["kind"]),
            command=gate.get("command"),
            timeout=gate.get("timeout"),
        )
        g2 = L.transition(g, node_id, "set_gate", surface, gate=gobj)
        _stamp(g2, node_id, who, surface)
        store.save(self.root, g2, h)
        out = self.status(node_id)
        out["nudge"] = _nudge(node_id, out["status"])
        return out

    def add_dep(self, node_id: str, dep: str, who: str | None = None, surface: str = "plan") -> dict:
        return self._txn(node_id, "add_dep", surface, who=who, dep=dep)

    def remove_dep(self, node_id: str, dep: str, who: str | None = None, surface: str = "plan") -> dict:
        return self._txn(node_id, "remove_dep", surface, who=who, dep=dep)

    def remove_node(self, node_id: str, who: str | None = None, surface: str = "plan") -> dict:
        g, h = store.load(self.root)
        g2 = L.transition(g, node_id, "remove", surface)  # who: node is gone, nothing to stamp
        store.save(self.root, g2, h)
        nudge = (
            f"Removed '{node_id}' from the workgraph. Delete or update any local docs/work logs that "
            "referenced it so the prose doesn't drift. The workgraph is the source of truth."
        )
        return {"removed": node_id, "nudge": nudge}

    # ----- execution (execute surface) ---------------------------------------

    def claim(self, node_id: str, who: str | None = None, surface: str = "execute") -> dict:
        return self._txn(node_id, "claim", surface, who=who)

    def verify(self, node_id: str, who: str | None = None, surface: str = "execute") -> dict:
        g, h = store.load(self.root)
        n = self._require(g, node_id)
        if n.gate.kind != GateKind.COMMAND:
            raise ValidationError(node_id, "gate.kind", "verify requires a command gate")
        if n.status != Status.ACTIVE:
            raise IllegalTransition(
                node_id, n.status.value, L._allowed_actions(n.status, surface)
            )
        cwd = os.path.normpath(os.path.join(self.root, g.working_dir))
        runs = os.path.join(self.root, store._STORE_DIR, "runs")
        result = run_gate(
            n.gate.command, cwd=cwd, timeout=n.gate.timeout or DEFAULT_TIMEOUT, runs_dir=runs
        )
        log_rel = os.path.relpath(result.log_path, self.root)
        n.last_verify = LastVerify(exit_code=result.exit_code, ran_at=_now(), log=log_rel)
        if result.exit_code == 0:
            g2 = L.transition(g, node_id, "pass_gate", surface)  # carries last_verify forward
            _stamp(g2, node_id, who, surface)
            store.save(self.root, g2, h)
            new_status = "awaiting-signoff"
        else:
            _stamp(g, node_id, who, surface)
            store.save(self.root, g, h)  # evidence recorded; stays active (AC-7)
            new_status = "active"
        out = {
            "exit_code": result.exit_code,
            "output": result.output,
            "status": new_status,
            "log": log_rel,
        }
        if result.exit_code == 0:  # state changed; on failure nothing settled, so no nudge
            out["nudge"] = _nudge(node_id, new_status)
        return out

    def request_signoff(
        self, node_id: str, note: str | None = None, who: str | None = None, surface: str = "execute"
    ) -> dict:
        out = self._txn(node_id, "request_signoff", surface, who=who)
        if note:
            store.write_rationale(self.root, node_id, note)
        return out

    def reverify(self, node_id: str, who: str | None = None, surface: str = "execute") -> dict:
        return self._txn(node_id, "reverify", surface, who=who)

    def report_blocked(self, node_id: str, who: str | None = None, surface: str = "execute") -> dict:
        return self._txn(node_id, "block", surface, who=who)

    # ----- operator transitions (plan surface) -------------------------------

    def signoff(self, node_id: str, who: str, note: str | None = None, surface: str = "plan") -> dict:
        g, h = store.load(self.root)
        g2 = L.transition(g, node_id, "signoff", surface, who=who, at=_now(), note=note)
        _stamp(g2, node_id, who, surface)
        store.save(self.root, g2, h)
        out = self.status(node_id)
        out["nudge"] = _nudge(node_id, out["status"])
        return out

    def resolve(self, node_id: str, rationale: str, who: str | None = None, surface: str = "plan") -> dict:
        store.write_rationale(self.root, node_id, rationale)  # sets the field
        g, h = store.load(self.root)
        g2 = L.transition(g, node_id, "resolve", surface)
        _stamp(g2, node_id, who, surface)
        store.save(self.root, g2, h)
        out = self.status(node_id)
        out["nudge"] = _nudge(node_id, out["status"])
        return out

    def defer(self, node_id: str, who: str | None = None, surface: str = "plan") -> dict:
        return self._txn(node_id, "defer", surface, who=who)

    def unblock(self, node_id: str, who: str | None = None, surface: str = "plan") -> dict:
        return self._txn(node_id, "unblock", surface, who=who)

    def archive(self, node_id: str, who: str | None = None, surface: str = "plan") -> dict:
        return self._txn(node_id, "archive", surface, who=who)

    # ----- helpers -----------------------------------------------------------

    def _txn(self, node_id: str, action: str, surface: str, who: str | None = None, **args) -> dict:
        g, h = store.load(self.root)
        g2 = L.transition(g, node_id, action, surface, **args)
        _stamp(g2, node_id, who, surface)
        store.save(self.root, g2, h)
        out = self.status(node_id)
        out["nudge"] = _nudge(node_id, out["status"])
        return out

    @staticmethod
    def _require(graph, node_id):
        if node_id not in graph.nodes:
            raise ValidationError(node_id, "id", "unknown node")
        return graph.nodes[node_id]
