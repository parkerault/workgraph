"""C-3 lifecycle state machine — AC-8,9,10,12,13,14,15,19,20,23,24,25,26."""

from __future__ import annotations

import pytest

from workgraph import lifecycle as L
from workgraph.errors import IllegalTransition, SurfaceDenied, ValidationError
from workgraph.models import Gate, GateKind, Graph, LastVerify, Node, Status

S = Status
EXEC, PLAN = "execute", "plan"


def nd(nid, kind="command", status=S.TRIAGE, deps=None, parent=None, rationale=None):
    gk = {"command": GateKind.COMMAND, "manual": GateKind.MANUAL, "none": GateKind.NONE}[kind]
    gate = Gate(kind=gk, command="true" if gk == GateKind.COMMAND else None)
    return Node(
        id=nid, gate=gate, status=status, deps=list(deps or []), parent=parent, rationale=rationale
    )


def gw(*nodes):
    g = Graph()
    for n in nodes:
        g.nodes[n.id] = n
    return g


# ---- claim / purity ---------------------------------------------------------

def test_claim_ready_to_active_is_pure():
    g = gw(nd("a", status=S.READY))
    g2 = L.transition(g, "a", "claim", EXEC)
    assert g2.nodes["a"].status == S.ACTIVE
    assert g.nodes["a"].status == S.READY  # original untouched


def test_claim_from_triage_is_illegal():
    g = gw(nd("a", status=S.TRIAGE))
    with pytest.raises(IllegalTransition):
        L.transition(g, "a", "claim", EXEC)


# ---- command gate verify path ----------------------------------------------

def test_pass_gate_command_active_to_awaiting():
    g = gw(nd("a", "command", status=S.ACTIVE))
    g2 = L.transition(g, "a", "pass_gate", EXEC)
    assert g2.nodes["a"].status == S.AWAITING_SIGNOFF


def test_pass_gate_requires_command_kind():
    g = gw(nd("a", "manual", status=S.ACTIVE))
    with pytest.raises((IllegalTransition, ValidationError)):
        L.transition(g, "a", "pass_gate", EXEC)


# ---- sign-off (AC-8/AC-9/AC-12) --------------------------------------------

def test_signoff_awaiting_to_done_stamps():
    g = gw(nd("a", "command", status=S.AWAITING_SIGNOFF))
    g2 = L.transition(g, "a", "signoff", PLAN, who="parker", at="2026-06-15T00:00:00Z")
    assert g2.nodes["a"].status == S.DONE
    assert g2.nodes["a"].signoff.who == "parker"


def test_signoff_rejected_when_not_awaiting():
    g = gw(nd("a", "command", status=S.ACTIVE))
    with pytest.raises(IllegalTransition):
        L.transition(g, "a", "signoff", PLAN, who="p", at="t")


def test_executor_cannot_signoff():
    g = gw(nd("a", "command", status=S.AWAITING_SIGNOFF))
    with pytest.raises(SurfaceDenied):
        L.transition(g, "a", "signoff", EXEC, who="p", at="t")


def test_no_execute_action_reaches_done():
    # The only action whose target is DONE is signoff, and it is plan-only.
    for action, spec in L.TABLE.items():
        if spec.to == S.DONE:
            assert EXEC not in spec.surfaces


# ---- gate-authorship immutability (AC-10) ----------------------------------

def test_set_gate_allowed_in_triage():
    g = gw(nd("a", "command", status=S.TRIAGE, deps=["d"]), nd("d", "command", status=S.ACTIVE))
    g2 = L.transition(g, "a", "set_gate", PLAN, gate=Gate(kind=GateKind.MANUAL))
    assert g2.nodes["a"].gate.kind == GateKind.MANUAL
    assert g2.nodes["a"].status == S.TRIAGE  # still waiting on d, not auto-readied


def test_set_gate_rejected_after_triage():
    g = gw(nd("a", "command", status=S.READY))
    with pytest.raises((IllegalTransition, ValidationError)):
        L.transition(g, "a", "set_gate", PLAN, gate=Gate(kind=GateKind.MANUAL))


def test_executor_cannot_set_gate():
    g = gw(nd("a", "command", status=S.TRIAGE))
    with pytest.raises(SurfaceDenied):
        L.transition(g, "a", "set_gate", EXEC, gate=Gate(kind=GateKind.MANUAL))


def test_add_dep_only_in_triage():
    g = gw(nd("a", "command", status=S.TRIAGE, deps=[]), nd("b", "command", status=S.DONE))
    g2 = L.transition(g, "a", "add_dep", PLAN, dep="b")
    assert "b" in g2.nodes["a"].deps
    g3 = gw(nd("a", "command", status=S.READY), nd("b", "command", status=S.DONE))
    with pytest.raises((IllegalTransition, ValidationError)):
        L.transition(g3, "a", "add_dep", PLAN, dep="b")


# ---- terminal vocabulary (AC-13/AC-14) -------------------------------------

def test_defer_to_deferred():
    g = gw(nd("a", "command", status=S.ACTIVE))
    assert L.transition(g, "a", "defer", PLAN).nodes["a"].status == S.DEFERRED


def test_no_path_from_deferred_to_done():
    g = gw(nd("a", "command", status=S.DEFERRED))
    with pytest.raises(IllegalTransition):
        L.transition(g, "a", "signoff", PLAN, who="p", at="t")


def test_resolve_none_gate_from_ready():
    g = gw(nd("a", "none", status=S.READY, rationale="rationale/a.md"))
    assert L.transition(g, "a", "resolve", PLAN).nodes["a"].status == S.RESOLVED


def test_resolve_requires_rationale():
    g = gw(nd("a", "none", status=S.READY))
    with pytest.raises(ValidationError):
        L.transition(g, "a", "resolve", PLAN)


def test_command_gate_cannot_resolve():
    g = gw(nd("a", "command", status=S.READY, rationale="r"))
    with pytest.raises((IllegalTransition, ValidationError)):
        L.transition(g, "a", "resolve", PLAN)


# ---- manual gate (AC-26) ----------------------------------------------------

def test_manual_gate_request_signoff_then_signoff():
    g = gw(nd("a", "manual", status=S.ACTIVE))
    g2 = L.transition(g, "a", "request_signoff", EXEC)
    assert g2.nodes["a"].status == S.AWAITING_SIGNOFF
    g3 = L.transition(g2, "a", "signoff", PLAN, who="parker", at="t")
    assert g3.nodes["a"].status == S.DONE


def test_command_gate_cannot_request_signoff():
    g = gw(nd("a", "command", status=S.ACTIVE))
    with pytest.raises((IllegalTransition, ValidationError)):
        L.transition(g, "a", "request_signoff", EXEC)


# ---- reverify (AC-25) -------------------------------------------------------

def test_reverify_clears_last_verify_and_returns_active():
    n = nd("a", "command", status=S.AWAITING_SIGNOFF)
    n.last_verify = LastVerify(exit_code=0, ran_at="t", log="x.log")
    g = gw(n)
    g2 = L.transition(g, "a", "reverify", EXEC)
    assert g2.nodes["a"].status == S.ACTIVE
    assert g2.nodes["a"].last_verify is None


# ---- readiness recompute (AC-23/AC-15) -------------------------------------

def test_recompute_advances_triage_to_ready_when_deps_terminal_good():
    g = gw(nd("a", "command", status=S.DONE), nd("b", "command", status=S.TRIAGE, deps=["a"]))
    L.recompute_readiness(g)
    assert g.nodes["b"].status == S.READY


def test_defer_blocks_dependent_via_recompute():
    g = gw(nd("a", "command", status=S.READY), nd("b", "command", status=S.READY, deps=["a"]))
    g2 = L.transition(g, "a", "defer", PLAN)
    assert g2.nodes["a"].status == S.DEFERRED
    assert g2.nodes["b"].status == S.BLOCKED


def test_blocked_cascades_to_transitive_dependent():
    g = gw(
        nd("a", "command", status=S.READY),
        nd("b", "command", status=S.READY, deps=["a"]),
        nd("c", "command", status=S.READY, deps=["b"]),
    )
    g2 = L.transition(g, "a", "defer", PLAN)
    assert g2.nodes["b"].status == S.BLOCKED
    assert g2.nodes["c"].status == S.BLOCKED


# ---- illegal transition error (AC-19) --------------------------------------

def test_illegal_transition_reports_allowed():
    g = gw(nd("a", "command", status=S.READY))
    with pytest.raises(IllegalTransition) as ei:
        L.transition(g, "a", "signoff", PLAN, who="p", at="t")
    assert ei.value.current == "ready"
    assert isinstance(ei.value.allowed, list)


# ---- remove constraint (AC-24) ---------------------------------------------

def test_remove_triage_no_dependents():
    g = gw(nd("a", "command", status=S.TRIAGE))
    assert "a" not in L.transition(g, "a", "remove", PLAN).nodes


def test_remove_rejected_when_not_triage():
    g = gw(nd("a", "command", status=S.ACTIVE))
    with pytest.raises((IllegalTransition, ValidationError)):
        L.transition(g, "a", "remove", PLAN)


def test_remove_rejected_with_dependents():
    g = gw(nd("a", "command", status=S.TRIAGE), nd("b", "command", status=S.TRIAGE, deps=["a"]))
    with pytest.raises(ValidationError):
        L.transition(g, "a", "remove", PLAN)


def test_remove_allowed_from_ready():
    # a no-dep node auto-advances triage->ready, so removal must work from ready too (else a
    # mistakenly-added node is unremovable). Started/terminal work still uses defer/archive.
    g = gw(nd("a", "command", status=S.READY))
    assert "a" not in L.transition(g, "a", "remove", PLAN).nodes


# ---- remove_dep: the plan-surface inverse of add_dep (AC-31) ----------------

def test_remove_dep_unblocks_dependent():
    # b depends on a; a deferred -> b blocked. Removing the edge releases b (recompute -> ready).
    g = gw(nd("a", "command", status=S.DEFERRED), nd("b", "command", status=S.BLOCKED, deps=["a"]))
    g2 = L.transition(g, "b", "remove_dep", PLAN, dep="a")
    assert "a" not in g2.nodes["b"].deps
    assert g2.nodes["b"].status == S.READY
    assert g.nodes["b"].deps == ["a"]  # original untouched (purity)


def test_remove_dep_unknown_edge_rejected():
    g = gw(nd("a", status=S.TRIAGE), nd("b", status=S.TRIAGE))
    with pytest.raises(ValidationError):
        L.transition(g, "b", "remove_dep", PLAN, dep="a")  # b does not depend on a


def test_remove_dep_denied_on_execute_surface():
    g = gw(nd("a", status=S.DEFERRED), nd("b", status=S.BLOCKED, deps=["a"]))
    with pytest.raises(SurfaceDenied):
        L.transition(g, "b", "remove_dep", EXEC, dep="a")


def test_remove_dep_rejected_on_inflight_dependent():
    # an active dependent's edges stay immutable — not in remove_dep's from_states.
    g = gw(nd("a", status=S.DONE), nd("b", status=S.ACTIVE, deps=["a"]))
    with pytest.raises(IllegalTransition):
        L.transition(g, "b", "remove_dep", PLAN, dep="a")


# ---- parent gating (AC-20) --------------------------------------------------

def test_parent_with_gate_cannot_signoff_while_child_pending():
    g = gw(
        nd("m", "command", status=S.AWAITING_SIGNOFF),
        nd("c", "command", status=S.ACTIVE, parent="m"),
    )
    with pytest.raises((IllegalTransition, ValidationError)):
        L.transition(g, "m", "signoff", PLAN, who="p", at="t")


def test_parent_signoff_ok_when_children_terminal_good():
    g = gw(
        nd("m", "command", status=S.AWAITING_SIGNOFF),
        nd("c", "command", status=S.DONE, parent="m"),
    )
    assert L.transition(g, "m", "signoff", PLAN, who="p", at="t").nodes["m"].status == S.DONE


def test_parent_signoff_ok_when_child_archived():
    # `archived` = explicitly removed from scope (e.g. a superseded-and-rebuilt epic's old
    # children) — it is not "owed" work, so it must NOT block the parent's sign-off (AC-20).
    g = gw(
        nd("m", "command", status=S.AWAITING_SIGNOFF),
        nd("c", "command", status=S.ARCHIVED, parent="m"),
    )
    assert L.transition(g, "m", "signoff", PLAN, who="p", at="t").nodes["m"].status == S.DONE


def test_parent_signoff_ok_when_child_archived_alongside_done():
    g = gw(
        nd("m", "command", status=S.AWAITING_SIGNOFF),
        nd("old", "command", status=S.ARCHIVED, parent="m"),
        nd("new", "command", status=S.DONE, parent="m"),
    )
    assert L.transition(g, "m", "signoff", PLAN, who="p", at="t").nodes["m"].status == S.DONE


def test_parent_signoff_blocked_when_child_deferred():
    # `deferred` = postponed but still in scope / still owed — it DOES block, else the parent's
    # `done` would overclaim the deferred child (the exact "deferred mistaken for done" failure).
    g = gw(
        nd("m", "command", status=S.AWAITING_SIGNOFF),
        nd("c", "command", status=S.DEFERRED, parent="m"),
    )
    with pytest.raises(ValidationError):
        L.transition(g, "m", "signoff", PLAN, who="p", at="t")


# ---- unblock (deterministic routing) ---------------------------------------

def test_unblock_routes_to_ready_when_deps_good():
    g = gw(nd("a", "command", status=S.DONE), nd("b", "command", status=S.BLOCKED, deps=["a"]))
    assert L.transition(g, "b", "unblock", PLAN).nodes["b"].status == S.READY


def test_unblock_stays_blocked_when_dep_abandoned():
    g = gw(nd("a", "command", status=S.DEFERRED), nd("b", "command", status=S.BLOCKED, deps=["a"]))
    assert L.transition(g, "b", "unblock", PLAN).nodes["b"].status == S.BLOCKED


# ---- add_node (entry + auto-advance) ---------------------------------------

def test_add_node_auto_readies_when_deps_done():
    g = gw(nd("a", "command", status=S.DONE))
    g2 = L.add_node(g, nd("b", "command", status=S.TRIAGE, deps=["a"]), PLAN)
    assert g2.nodes["b"].status == S.READY


def test_add_node_stays_triage_with_pending_dep():
    g = gw(nd("a", "command", status=S.ACTIVE))
    g2 = L.add_node(g, nd("b", "command", status=S.TRIAGE, deps=["a"]), PLAN)
    assert g2.nodes["b"].status == S.TRIAGE


def test_unknown_node_rejected():
    g = gw(nd("a", "command", status=S.READY))
    with pytest.raises(ValidationError):
        L.transition(g, "ghost", "claim", EXEC)
