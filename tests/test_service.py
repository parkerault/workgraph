"""Service layer behind the MCP tools (C-5 core) — AC-1,2,4,5,6,7,8,9,21,22,23; NFR-4."""

from __future__ import annotations

import pytest

from workgraph import store
from workgraph.errors import IllegalTransition, ValidationError
from workgraph.service import Service


def svc(tmp_path):
    store.init_store(str(tmp_path))
    return Service(str(tmp_path))


def _node(nid, gate_kind="command", command="true", deps=None, kind="unit", parent=None):
    n = {"id": nid, "kind": kind, "deps": list(deps or []), "gate": {"kind": gate_kind}}
    if gate_kind == "command":
        n["gate"]["command"] = command
    if parent:
        n["parent"] = parent
    return n


# ---- ingest + plan (AC-1, AC-2, AC-21) -------------------------------------

def test_ingest_then_plan_returns_waves(tmp_path):
    s = svc(tmp_path)
    s.ingest([_node("a"), _node("b", deps=["a"]), _node("c", deps=["a"])])
    assert s.plan() == [["a"], ["b", "c"]]


def test_ingest_resolves_forward_refs_in_any_order(tmp_path):
    s = svc(tmp_path)
    # b declared before its dependency a — forward ref within the batch.
    s.ingest([_node("b", deps=["a"]), _node("a")])
    assert s.plan() == [["a"], ["b"]]


def test_ingest_rejects_cycle_atomically(tmp_path):
    s = svc(tmp_path)
    with pytest.raises(ValidationError):
        s.ingest([_node("a", deps=["b"]), _node("b", deps=["a"])])
    assert s.plan() == []  # store unchanged


def test_ingest_rejects_duplicate_id_atomically(tmp_path):
    s = svc(tmp_path)
    s.ingest([_node("a")])
    with pytest.raises(ValidationError):
        s.ingest([_node("a")])


# ---- status (AC-4, AC-5, AC-22, NFR-4) -------------------------------------

def test_status_empty_store_is_zero_rollup(tmp_path):
    s = svc(tmp_path)
    roll = s.status()
    assert roll["counts"] == {}


def test_plan_empty_store(tmp_path):
    assert svc(tmp_path).plan() == []


def test_status_rollup_counts_and_no_body_leak(tmp_path):
    s = svc(tmp_path)
    s.ingest([_node("a", command="secret-command"), _node("b", deps=["a"])])
    roll = s.status()
    assert roll["counts"].get("ready") == 1  # a has no deps -> ready
    assert roll["counts"].get("triage") == 1  # b waits on a
    assert "secret-command" not in repr(roll)  # NFR-4: no gate command leaks


def test_status_per_node_summary_fields(tmp_path):
    s = svc(tmp_path)
    s.ingest([_node("a")])
    st = s.status("a")
    assert st["id"] == "a" and st["status"] == "ready" and st["kind"] == "unit"
    assert st["gate_kind"] == "command"
    assert "signoff" not in st  # absent when none (AC-5)
    assert "secret" not in repr(st)


def test_status_parent_reports_child_rollup(tmp_path):
    s = svc(tmp_path)
    s.ingest([_node("m", gate_kind="none"), _node("c", parent="m")])
    st = s.status("m")
    assert st["children"]  # rollup present for a parent


def test_mermaid_returns_text(tmp_path):
    s = svc(tmp_path)
    s.ingest([_node("a", gate_kind="none"), _node("b", gate_kind="none", deps=["a"])])
    m = s.mermaid()["mermaid"]
    assert m.startswith("graph TD")
    assert 'a["a [ready]"]' in m and "a --> b" in m


def test_mermaid_status_slice(tmp_path):
    s = svc(tmp_path)
    s.ingest([_node("a", gate_kind="none"), _node("b", gate_kind="none", deps=["a"])])
    m = s.mermaid(status="ready")["mermaid"]  # a is ready, b is triage
    assert 'a["' in m and 'b["' not in m


def test_status_filter_returns_ids_in_that_state(tmp_path):
    s = svc(tmp_path)
    s.ingest([_node("a"), _node("b", deps=["a"])])  # a -> ready, b -> triage
    out = s.status(status="ready")
    assert out["status"] == "ready" and out["ids"] == ["a"]
    assert s.status(status="triage")["ids"] == ["b"]
    assert s.status(status="done")["ids"] == []  # empty, not an error


def test_status_filter_accepts_comma_separated_states(tmp_path):
    s = svc(tmp_path)
    s.ingest([_node("rdy"), _node("act"), _node("dep"), _node("blk", deps=["dep"])])
    s.claim("act")  # active
    s.defer("dep")  # -> blk becomes blocked
    out = s.status(status="active,ready,blocked")
    assert set(out["ids"]) == {"rdy", "act", "blk"}  # union; deferred `dep` excluded
    assert s.status(status="active")["ids"] == ["act"]  # single value unchanged
    assert set(s.status(status="active, ready")["ids"]) == {"rdy", "act"}  # whitespace tolerated


# ---- command gate happy path (AC-6, AC-7, AC-8) ----------------------------

def test_verify_pass_then_signoff_reaches_done(tmp_path):
    s = svc(tmp_path)
    s.ingest([_node("a", command="true")])
    s.claim("a")
    res = s.verify("a")
    assert res["exit_code"] == 0
    assert s.status("a")["status"] == "awaiting-signoff"
    s.signoff("a", who="parker")
    assert s.status("a")["status"] == "done"


def test_verify_failure_holds_active_and_surfaces_output(tmp_path):
    s = svc(tmp_path)
    s.ingest([_node("a", command="echo NOPE 1>&2; exit 1")])
    s.claim("a")
    res = s.verify("a")
    assert res["exit_code"] == 1
    assert "NOPE" in res["output"]
    assert s.status("a")["status"] == "active"  # never advances on a failed gate


def test_signoff_rejected_when_not_awaiting(tmp_path):
    s = svc(tmp_path)
    s.ingest([_node("a")])
    s.claim("a")  # active, not awaiting-signoff
    with pytest.raises(IllegalTransition):
        s.signoff("a", who="parker")


# ---- readiness driver (AC-23) ----------------------------------------------

def test_ingesting_against_a_done_dep_auto_readies(tmp_path):
    s = svc(tmp_path)
    s.ingest([_node("a", command="true")])
    s.claim("a")
    s.verify("a")
    s.signoff("a", who="p")  # a is done
    s.ingest([_node("b", deps=["a"])])
    assert s.status("b")["status"] == "ready"  # server recompute readied b


# ---- gate-authorship governance reaches the service (AC-12 via surface) -----

def test_execute_surface_cannot_signoff(tmp_path):
    from workgraph.errors import SurfaceDenied

    s = svc(tmp_path)
    s.ingest([_node("a", gate_kind="manual")])
    s.claim("a")
    s.request_signoff("a")
    with pytest.raises(SurfaceDenied):
        s.signoff("a", who="p", surface="execute")
