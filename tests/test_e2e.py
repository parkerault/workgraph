"""WU-8 — end-to-end happy path + adversarial path over the real MCP tool handlers.

Covers the M-3 alpha gate: AC-1, AC-2, AC-6, AC-7, AC-8, AC-10, AC-11, AC-12, AC-13, AC-14,
AC-21, AC-24; NFR-1.
"""

from __future__ import annotations

import time

import pytest

from workgraph import store
from workgraph.errors import IllegalTransition, SurfaceDenied, ValidationError
from workgraph.mcp_server import EXECUTE_TOOLS, PLAN_TOOLS, tool_handlers
from workgraph.service import Service


def setup(tmp_path):
    store.init_store(str(tmp_path))
    s = Service(str(tmp_path))
    return s, tool_handlers(s)


def cmd(nid, command="true", deps=None):
    return {"id": nid, "gate": {"kind": "command", "command": command}, "deps": list(deps or [])}


# ---- happy path -------------------------------------------------------------

def test_full_happy_path_ingest_to_done(tmp_path):
    s, h = setup(tmp_path)
    h["wg_ingest"]({"nodes": [cmd("a"), cmd("b", deps=["a"])]})
    assert h["wg_plan"]({}) == {"waves": [["a"], ["b"]]}  # AC-1

    h["wg_claim"]({"id": "a"})
    res = h["wg_verify"]({"id": "a"})  # AC-6
    assert res["exit_code"] == 0 and res["status"] == "awaiting-signoff"

    h["wg_signoff"]({"id": "a", "who": "parker"})  # AC-8 (plan-only)
    assert h["wg_status"]({"id": "a"})["status"] == "done"
    assert h["wg_status"]({"id": "b"})["status"] == "ready"  # AC-23 readied b

    roll = h["wg_status"]({})
    assert roll["counts"]["done"] == 1 and roll["counts"]["ready"] == 1


def test_deferred_is_distinct_from_done(tmp_path):
    s, h = setup(tmp_path)
    h["wg_ingest"]({"nodes": [cmd("a")]})
    h["wg_defer"]({"id": "a"})  # AC-13
    assert h["wg_status"]({"id": "a"})["status"] == "deferred"
    with pytest.raises(IllegalTransition):  # no path deferred -> done
        s.signoff("a", who="x")


def test_none_gate_node_resolves_not_done(tmp_path):
    s, h = setup(tmp_path)
    h["wg_ingest"]({"nodes": [{"id": "d", "gate": {"kind": "none"}}]})  # ready (no deps)
    h["wg_resolve"]({"id": "d", "rationale": "decided to use YAML"})  # AC-14
    assert h["wg_status"]({"id": "d"})["status"] == "resolved"


# ---- adversarial path -------------------------------------------------------

def test_executor_surface_cannot_sign_off(tmp_path):
    """AC-12 defense-in-depth — even if wired to the tool, the execute surface is denied done."""
    s, _ = setup(tmp_path)
    s.ingest([{"id": "a", "gate": {"kind": "manual"}}])
    s.claim("a")
    s.request_signoff("a")
    with pytest.raises(SurfaceDenied):
        s.signoff("a", who="x", surface="execute")


def test_executor_surface_cannot_set_gate(tmp_path):
    s, _ = setup(tmp_path)
    s.ingest([cmd("a")])
    with pytest.raises(SurfaceDenied):
        s.set_gate("a", {"kind": "manual"}, surface="execute")


def test_gate_immutable_after_triage(tmp_path):
    """AC-10 — set_gate on a node that left triage is rejected."""
    s, _ = setup(tmp_path)
    s.ingest([cmd("a")])  # no deps -> auto-ready, left triage
    s.claim("a")
    with pytest.raises((ValidationError, IllegalTransition)):
        s.set_gate("a", {"kind": "manual"})


def test_signoff_is_plan_only_and_only_door_to_done():
    """AC-12 at the surface level."""
    assert "wg_signoff" in PLAN_TOOLS
    assert "wg_signoff" not in EXECUTE_TOOLS


def test_cycle_declaration_rejected(tmp_path):
    s, h = setup(tmp_path)
    with pytest.raises(ValidationError):  # AC-2
        h["wg_ingest"]({"nodes": [cmd("a", deps=["b"]), cmd("b", deps=["a"])]})
    assert h["wg_plan"]({}) == {"waves": []}  # store unchanged (AC-21 atomicity)


def test_remove_with_dependents_rejected(tmp_path):
    s, h = setup(tmp_path)
    # x stays unfinished (only ready), so a remains in `triage` yet still has dependent b —
    # isolating the has-dependents clause of AC-24 from the not-in-triage clause.
    h["wg_ingest"](
        {"nodes": [{"id": "x", "gate": {"kind": "none"}}, cmd("a", deps=["x"]), cmd("b", deps=["a"])]}
    )
    assert s.status("a")["status"] == "triage"
    with pytest.raises(ValidationError):  # AC-24 (has dependents)
        h["wg_remove_node"]({"id": "a"})


def test_remove_ready_node_allowed(tmp_path):
    s, h = setup(tmp_path)
    h["wg_ingest"]({"nodes": [cmd("a")]})  # no deps -> auto-ready; not yet started
    assert s.status("a")["status"] == "ready"
    assert h["wg_remove_node"]({"id": "a"})["removed"] == "a"  # AC-24: not-yet-started is removable


def test_remove_started_node_rejected(tmp_path):
    s, h = setup(tmp_path)
    h["wg_ingest"]({"nodes": [cmd("a")]})
    h["wg_claim"]({"id": "a"})  # active = started work
    with pytest.raises((ValidationError, IllegalTransition)):  # AC-24: retire started work, don't delete
        h["wg_remove_node"]({"id": "a"})


def test_failed_gate_holds_node_active(tmp_path):
    s, h = setup(tmp_path)
    h["wg_ingest"]({"nodes": [cmd("a", command="exit 7")]})
    h["wg_claim"]({"id": "a"})
    res = h["wg_verify"]({"id": "a"})  # AC-7
    assert res["exit_code"] == 7 and res["status"] == "active"
    assert h["wg_status"]({"id": "a"})["status"] == "active"


def test_ingest_forward_refs_atomic(tmp_path):
    s, h = setup(tmp_path)
    # AC-21 — declare a connected DAG in reverse order in one batch.
    h["wg_ingest"]({"nodes": [cmd("c", deps=["b"]), cmd("b", deps=["a"]), cmd("a")]})
    assert h["wg_plan"]({}) == {"waves": [["a"], ["b"], ["c"]]}


# ---- performance (NFR-1) ----------------------------------------------------

def test_plan_and_status_under_500ms_at_1000_nodes(tmp_path):
    s, h = setup(tmp_path)
    nodes = []
    for i in range(1000):
        deps = [f"n{j}" for j in (i - 1, i - 2, i - 3) if j >= 0]
        nodes.append({"id": f"n{i}", "gate": {"kind": "none"}, "deps": deps})
    s.ingest(nodes)

    t0 = time.perf_counter()
    h["wg_plan"]({})
    assert time.perf_counter() - t0 < 0.5

    t0 = time.perf_counter()
    h["wg_status"]({})
    assert time.perf_counter() - t0 < 0.5
