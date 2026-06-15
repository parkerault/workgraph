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
from . import store
from .errors import IllegalTransition, ValidationError
from .gate import run_gate
from .models import Gate, GateKind, LastVerify, Status

DEFAULT_TIMEOUT = 120  # seconds (D-8/D-13)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


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

    def ingest(self, nodes: list[dict], surface: str = "plan") -> dict:
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
        store.save(self.root, g, h)  # validates refs + cycle atomically (store unchanged on error)
        return {"ingested": ingested}

    def add_node(self, node: dict, surface: str = "plan") -> dict:
        g, h = store.load(self.root)
        new = store._node_from_dict(node)
        g2 = L.add_node(g, new, surface)
        store.save(self.root, g2, h)
        return self.status(new.id)

    def set_gate(self, node_id: str, gate: dict, surface: str = "plan") -> dict:
        g, h = store.load(self.root)
        gobj = Gate(
            kind=GateKind(gate["kind"]),
            command=gate.get("command"),
            timeout=gate.get("timeout"),
        )
        g2 = L.transition(g, node_id, "set_gate", surface, gate=gobj)
        store.save(self.root, g2, h)
        return self.status(node_id)

    def add_dep(self, node_id: str, dep: str, surface: str = "plan") -> dict:
        return self._txn(node_id, "add_dep", surface, dep=dep)

    def remove_node(self, node_id: str, surface: str = "plan") -> dict:
        g, h = store.load(self.root)
        g2 = L.transition(g, node_id, "remove", surface)
        store.save(self.root, g2, h)
        return {"removed": node_id}

    # ----- execution (execute surface) ---------------------------------------

    def claim(self, node_id: str, surface: str = "execute") -> dict:
        return self._txn(node_id, "claim", surface)

    def verify(self, node_id: str, surface: str = "execute") -> dict:
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
            store.save(self.root, g2, h)
            new_status = "awaiting-signoff"
        else:
            store.save(self.root, g, h)  # evidence recorded; stays active (AC-7)
            new_status = "active"
        return {
            "exit_code": result.exit_code,
            "output": result.output,
            "status": new_status,
            "log": log_rel,
        }

    def request_signoff(self, node_id: str, note: str | None = None, surface: str = "execute") -> dict:
        out = self._txn(node_id, "request_signoff", surface)
        if note:
            store.write_rationale(self.root, node_id, note)
        return out

    def reverify(self, node_id: str, surface: str = "execute") -> dict:
        return self._txn(node_id, "reverify", surface)

    def report_blocked(self, node_id: str, surface: str = "execute") -> dict:
        return self._txn(node_id, "block", surface)

    # ----- operator transitions (plan surface) -------------------------------

    def signoff(self, node_id: str, who: str, note: str | None = None, surface: str = "plan") -> dict:
        g, h = store.load(self.root)
        g2 = L.transition(g, node_id, "signoff", surface, who=who, at=_now(), note=note)
        store.save(self.root, g2, h)
        return self.status(node_id)

    def resolve(self, node_id: str, rationale: str, surface: str = "plan") -> dict:
        store.write_rationale(self.root, node_id, rationale)  # sets the field
        g, h = store.load(self.root)
        g2 = L.transition(g, node_id, "resolve", surface)
        store.save(self.root, g2, h)
        return self.status(node_id)

    def defer(self, node_id: str, surface: str = "plan") -> dict:
        return self._txn(node_id, "defer", surface)

    def unblock(self, node_id: str, surface: str = "plan") -> dict:
        return self._txn(node_id, "unblock", surface)

    def archive(self, node_id: str, surface: str = "plan") -> dict:
        return self._txn(node_id, "archive", surface)

    # ----- helpers -----------------------------------------------------------

    def _txn(self, node_id: str, action: str, surface: str, **args) -> dict:
        g, h = store.load(self.root)
        g2 = L.transition(g, node_id, action, surface, **args)
        store.save(self.root, g2, h)
        return self.status(node_id)

    @staticmethod
    def _require(graph, node_id):
        if node_id not in graph.nodes:
            raise ValidationError(node_id, "id", "unknown node")
        return graph.nodes[node_id]
