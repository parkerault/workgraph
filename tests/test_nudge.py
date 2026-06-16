"""Reconciliation nudge: every state-changing mutation returns a `nudge` reminding the agent
to reconcile the project's prose docs / work logs with the workgraph (workgraph-is-truth / no-drift).

Reads never nudge; a *failed* verify (no state change) never nudges. Terminal transitions carry
sharper wording — the whole point of the tool is that 'deferred' is never mistaken for 'done'."""

from __future__ import annotations

from workgraph import store
from workgraph.service import Service


def svc(tmp_path):
    store.init_store(str(tmp_path))
    return Service(str(tmp_path))


def cmd(nid, **kw):
    kw.setdefault("gate", {"kind": "command", "command": "true"})
    return {"id": nid, **kw}


def test_claim_returns_a_nudge_naming_node_and_workgraph(tmp_path):
    s = svc(tmp_path)
    s.ingest([cmd("a")])
    out = s.claim("a")
    assert "a" in out["nudge"]
    assert "workgraph" in out["nudge"].lower()


def test_done_nudge_calls_out_completion(tmp_path):
    s = svc(tmp_path)
    s.ingest([cmd("a")])
    s.claim("a")
    s.verify("a")
    out = s.signoff("a", who="parker")
    assert out["status"] == "done"
    assert "complete" in out["nudge"].lower() or "done" in out["nudge"].lower()


def test_resolved_nudge_calls_out_completion(tmp_path):
    s = svc(tmp_path)
    s.ingest([{"id": "d", "gate": {"kind": "none"}}])
    out = s.resolve("d", rationale="chose YAML")
    assert out["status"] == "resolved"
    assert "complete" in out["nudge"].lower() or "resolved" in out["nudge"].lower()


def test_deferred_nudge_warns_not_completed(tmp_path):
    s = svc(tmp_path)
    s.ingest([cmd("a")])
    out = s.defer("a")
    low = out["nudge"].lower()
    assert "not completed" in low or "set aside" in low


def test_blocked_nudge_mentions_the_blocker(tmp_path):
    s = svc(tmp_path)
    # b has an unmet dep on a, so a reported block actually sticks (recompute_readiness would
    # self-heal a node whose deps are all terminal-good back to ready).
    s.ingest([cmd("a"), cmd("b", deps=["a"])])
    out = s.report_blocked("b")
    assert out["status"] == "blocked"
    assert "block" in out["nudge"].lower()


def test_ingest_returns_a_nudge(tmp_path):
    s = svc(tmp_path)
    out = s.ingest([cmd("a"), cmd("b")])
    assert "workgraph" in out["nudge"].lower()


def test_add_node_returns_a_nudge(tmp_path):
    s = svc(tmp_path)
    s.ingest([cmd("a")])
    out = s.add_node(cmd("b"))
    assert "workgraph" in out["nudge"].lower()


def test_remove_node_returns_a_nudge(tmp_path):
    s = svc(tmp_path)
    s.ingest([cmd("a"), cmd("b", deps=["a"])])  # b stays in triage (remove is triage-only)
    out = s.remove_node("b")
    assert out["removed"] == "b"
    assert "b" in out["nudge"]


def test_successful_verify_nudges(tmp_path):
    s = svc(tmp_path)
    s.ingest([cmd("a")])
    s.claim("a")
    out = s.verify("a")
    assert out["status"] == "awaiting-signoff"
    assert "nudge" in out


def test_failed_verify_does_not_nudge(tmp_path):
    s = svc(tmp_path)
    s.ingest([{"id": "a", "gate": {"kind": "command", "command": "false"}}])
    s.claim("a")
    out = s.verify("a")
    assert out["status"] == "active"
    assert "nudge" not in out  # nothing settled, nothing to reconcile


def test_reads_never_nudge(tmp_path):
    s = svc(tmp_path)
    s.ingest([cmd("a")])
    assert "nudge" not in s.status("a")
    assert "nudge" not in s.show("a")


def test_nudge_flows_through_the_mcp_handler(tmp_path):
    from workgraph.mcp_server import tool_handlers

    s = svc(tmp_path)
    h = tool_handlers(s)
    h["wg_ingest"]({"nodes": [cmd("a")]})
    out = h["wg_claim"]({"id": "a"})
    assert "nudge" in out
