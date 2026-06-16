"""Per-node provenance: every transition stamps updated_at; `who` records the actor."""

from __future__ import annotations

from workgraph import store
from workgraph.service import Service


def svc(tmp_path):
    store.init_store(str(tmp_path))
    return Service(str(tmp_path))


def cmd(nid, **kw):
    kw.setdefault("gate", {"kind": "command", "command": "true"})
    return {"id": nid, **kw}


def test_claim_stamps_updated_at_and_who(tmp_path):
    s = svc(tmp_path)
    s.ingest([cmd("a")])
    s.claim("a", who="wg-executor:a")
    st = s.status("a")
    assert st["updated_by"] == "wg-executor:a"
    assert st["updated_at"]  # an ISO timestamp is present


def test_who_falls_back_to_surface_when_omitted(tmp_path):
    s = svc(tmp_path)
    s.ingest([cmd("a")])
    s.claim("a")  # no who supplied
    assert s.status("a")["updated_by"] == "execute"


def test_signoff_sets_updated_by_to_who(tmp_path):
    s = svc(tmp_path)
    s.ingest([cmd("a")])
    s.claim("a", who="wg-executor:a")
    s.verify("a", who="wg-executor:a")
    s.signoff("a", who="parker")
    st = s.status("a")
    assert st["status"] == "done" and st["updated_by"] == "parker"


def test_resolve_stamps_who(tmp_path):
    s = svc(tmp_path)
    s.ingest([{"id": "d", "gate": {"kind": "none"}}])
    s.resolve("d", rationale="chose YAML", who="coordinator")
    st = s.status("d")
    assert st["status"] == "resolved" and st["updated_by"] == "coordinator" and st["updated_at"]


def test_defer_stamps_who(tmp_path):
    s = svc(tmp_path)
    s.ingest([cmd("a")])
    s.defer("a", who="parker")
    assert s.status("a")["updated_by"] == "parker"


def test_ingest_stamps_created_nodes(tmp_path):
    s = svc(tmp_path)
    s.ingest([cmd("a")], who="coordinator")
    st = s.status("a")
    assert st["updated_by"] == "coordinator" and st["updated_at"]


def test_updated_fields_round_trip_through_store(tmp_path):
    s = svc(tmp_path)
    s.ingest([cmd("a")], who="coordinator")
    g, _ = store.load(str(tmp_path))
    assert g.nodes["a"].updated_by == "coordinator" and g.nodes["a"].updated_at


def test_every_mutation_tool_accepts_who():
    from workgraph.mcp_server import EXECUTE_TOOLS, PLAN_TOOLS, tool_schemas

    s = tool_schemas()
    for t in set(EXECUTE_TOOLS) | set(PLAN_TOOLS):
        assert "who" in s[t]["properties"], t


def test_who_threads_through_the_mcp_handler(tmp_path):
    from workgraph.mcp_server import tool_handlers

    s = svc(tmp_path)
    h = tool_handlers(s)
    h["wg_ingest"]({"nodes": [cmd("a")], "who": "coordinator"})
    h["wg_claim"]({"id": "a", "who": "wg-executor:a"})
    assert s.status("a")["updated_by"] == "wg-executor:a"
