"""C-3 — Lifecycle interface. The state machine: transitions, surface capability, immutability.

Pure over the Graph (no store I/O, no base_hash): `transition` deep-copies, mutates, returns a new
Graph, and re-runs the readiness recompute (AC-23). Structural edits (set_gate/add_dep/remove) and
creation (add_node) are surface-checked here too.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass

from .errors import IllegalTransition, SurfaceDenied, ValidationError
from .models import (
    NON_TERMINAL,
    TERMINAL,
    TERMINAL_GOOD,
    Gate,
    GateKind,
    Graph,
    Node,
    Signoff,
    Status,
)

EXECUTE = "execute"
PLAN = "plan"
S = Status

#: A dep in one of these states has been abandoned → dependents are blocked (AC-23).
_ABANDONED = frozenset({S.DEFERRED, S.ARCHIVED, S.BLOCKED})


@dataclass(frozen=True)
class TransitionSpec:
    from_states: frozenset
    to: Status
    surfaces: frozenset


# The status transition table (SPEC §Data & state model). Structural actions
# (set_gate/add_dep/remove) are triage-scoped and handled below; they share the table for the
# surface + from-state checks. `to=None` marks a non-status action.
TABLE: dict[str, TransitionSpec] = {
    "claim": TransitionSpec(frozenset({S.READY}), S.ACTIVE, frozenset({EXECUTE})),
    "pass_gate": TransitionSpec(frozenset({S.ACTIVE}), S.AWAITING_SIGNOFF, frozenset({EXECUTE})),
    "request_signoff": TransitionSpec(
        frozenset({S.ACTIVE}), S.AWAITING_SIGNOFF, frozenset({EXECUTE})
    ),
    "reverify": TransitionSpec(
        frozenset({S.AWAITING_SIGNOFF}), S.ACTIVE, frozenset({EXECUTE, PLAN})
    ),
    "signoff": TransitionSpec(frozenset({S.AWAITING_SIGNOFF}), S.DONE, frozenset({PLAN})),
    "resolve": TransitionSpec(frozenset({S.READY, S.ACTIVE}), S.RESOLVED, frozenset({PLAN})),
    "block": TransitionSpec(NON_TERMINAL, S.BLOCKED, frozenset({EXECUTE, PLAN})),
    "unblock": TransitionSpec(frozenset({S.BLOCKED}), S.TRIAGE, frozenset({PLAN})),
    "defer": TransitionSpec(NON_TERMINAL, S.DEFERRED, frozenset({PLAN})),
    "archive": TransitionSpec(TERMINAL, S.ARCHIVED, frozenset({PLAN})),
    # structural, triage-only:
    "set_gate": TransitionSpec(frozenset({S.TRIAGE}), S.TRIAGE, frozenset({PLAN})),
    "add_dep": TransitionSpec(frozenset({S.TRIAGE}), S.TRIAGE, frozenset({PLAN})),
    "remove": TransitionSpec(frozenset({S.TRIAGE}), S.TRIAGE, frozenset({PLAN})),
}


def _allowed_actions(status: Status, surface: str) -> list[str]:
    return [a for a, spec in TABLE.items() if status in spec.from_states and surface in spec.surfaces]


def recompute_readiness(graph: Graph) -> Graph:
    """Drive the auto-transitions (AC-23): triage/blocked→ready when deps terminal-good; any
    non-terminal node with an abandoned dep→blocked. Iterates to a fixpoint so cascades propagate."""
    changed = True
    while changed:
        changed = False
        for n in graph.nodes.values():
            if n.status in TERMINAL:
                continue
            deps = [graph.nodes[d] for d in n.deps if d in graph.nodes]
            if any(d.status in _ABANDONED for d in deps):
                new = S.BLOCKED
            elif n.status in (S.TRIAGE, S.BLOCKED) and all(
                d.status in TERMINAL_GOOD for d in deps
            ):
                new = S.READY
            else:
                new = n.status
            if new != n.status:
                n.status = new
                changed = True
    return graph


def _children(graph: Graph, node_id: str) -> list[Node]:
    return [n for n in graph.nodes.values() if n.parent == node_id]


def add_node(graph: Graph, node: Node, surface: str) -> Graph:
    """Create a node (plan surface). It enters `triage`; readiness then recomputes (AC-23)."""
    if surface != PLAN:
        raise SurfaceDenied("add_node", surface)
    if node.id in graph.nodes:
        raise ValidationError(node.id, "id", "duplicate node id")
    g = copy.deepcopy(graph)
    node = copy.deepcopy(node)
    node.status = S.TRIAGE
    g.nodes[node.id] = node
    return recompute_readiness(g)


def transition(graph: Graph, node_id: str, action: str, surface: str, **args) -> Graph:
    if node_id not in graph.nodes:
        raise ValidationError(node_id, "id", "unknown node")
    if action not in TABLE:
        raise ValidationError(node_id, "action", f"unknown action {action!r}")
    spec = TABLE[action]

    # Surface capability first — an executor must be *denied*, not merely blocked on status (AC-12).
    if surface not in spec.surfaces:
        raise SurfaceDenied(action, surface)

    g = copy.deepcopy(graph)
    node = g.nodes[node_id]

    # Gate-kind applicability (independent of status).
    if action == "pass_gate" and node.gate.kind != GateKind.COMMAND:
        raise ValidationError(node_id, "gate.kind", "pass_gate requires a command gate")
    if action == "request_signoff" and node.gate.kind != GateKind.MANUAL:
        raise ValidationError(node_id, "gate.kind", "request_signoff requires a manual gate")
    if action == "resolve" and node.gate.kind != GateKind.NONE:
        raise ValidationError(node_id, "gate.kind", "only a none-gate node can be resolved")

    # Status legality.
    if node.status not in spec.from_states:
        raise IllegalTransition(node_id, node.status.value, _allowed_actions(node.status, surface))

    # Per-action preconditions + side effects.
    if action == "signoff":
        if not args.get("who") or not args.get("at"):
            raise ValidationError(node_id, "signoff", "signoff requires who and at")
        if any(c.status not in TERMINAL_GOOD for c in _children(g, node_id)):
            raise ValidationError(node_id, "children", "child work is not terminal-good (AC-20)")
        node.signoff = Signoff(who=args["who"], at=args["at"], note=args.get("note"))
    elif action == "resolve":
        if not node.rationale:
            raise ValidationError(node_id, "rationale", "resolve requires a rationale")
    elif action == "reverify":
        node.last_verify = None
    elif action == "set_gate":
        gate = args.get("gate")
        if not isinstance(gate, Gate):
            raise ValidationError(node_id, "gate", "set_gate requires a gate")
        node.gate = gate
    elif action == "add_dep":
        dep = args.get("dep")
        if dep == node_id:
            raise ValidationError(node_id, "deps", "a node cannot depend on itself")
        if dep not in g.nodes:
            raise ValidationError(node_id, "deps", f"unknown dependency {dep!r}")
        if dep not in node.deps:
            node.deps.append(dep)
    elif action == "remove":
        dependents = [
            n.id for n in g.nodes.values() if node_id in n.deps or n.parent == node_id
        ]
        if dependents:
            raise ValidationError(node_id, "deps", f"has dependents {dependents}")
        del g.nodes[node_id]
        return recompute_readiness(g)

    # Apply the status change (structural actions keep TRIAGE; unblock→TRIAGE then recompute routes).
    node.status = spec.to
    return recompute_readiness(g)
