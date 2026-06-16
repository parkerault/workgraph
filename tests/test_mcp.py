"""C-5 MCP binding — surface split (AC-11/AC-12), error envelope, handler wiring."""

from __future__ import annotations

from workgraph import mcp_server as M
from workgraph import store
from workgraph.errors import (
    ConcurrencyError,
    IllegalTransition,
    SurfaceDenied,
    ValidationError,
)
from workgraph.mcp_server import (
    EXECUTE_TOOLS,
    PLAN_TOOLS,
    READ_TOOLS,
    error_envelope,
    tool_handlers,
    tool_manifest,
)
from workgraph.service import Service


def test_surface_groups_partition_all_tools():
    m = tool_manifest()
    assert set(m["read"]).isdisjoint(m["execute"])
    assert set(m["execute"]).isdisjoint(m["plan"])
    assert set(m["read"]).isdisjoint(m["plan"])


def test_tool_schemas_cover_every_tool():
    from workgraph.mcp_server import tool_schemas

    schemas = tool_schemas()
    assert set(schemas) == set(READ_TOOLS) | set(EXECUTE_TOOLS) | set(PLAN_TOOLS)


def test_tool_annotations_classify_read_destructive_openworld():
    from workgraph.mcp_server import tool_annotations

    a = tool_annotations()
    assert set(a) == set(READ_TOOLS) | set(EXECUTE_TOOLS) | set(PLAN_TOOLS)
    for t in READ_TOOLS:
        assert a[t]["readOnlyHint"] is True
    for t in set(EXECUTE_TOOLS) | set(PLAN_TOOLS):
        assert a[t].get("readOnlyHint") is False
    assert a["wg_verify"]["openWorldHint"] is True  # runs an arbitrary shell command
    assert a["wg_remove_node"]["destructiveHint"] is True


def test_descriptions_are_substantive_and_state_key_constraints():
    from workgraph.mcp_server import tool_descriptions

    d = tool_descriptions()
    assert set(d) == set(READ_TOOLS) | set(EXECUTE_TOOLS) | set(PLAN_TOOLS)
    for name, text in d.items():
        assert len(text) >= 40, name  # not a one-word stub
    assert "done" in d["wg_signoff"].lower()  # the defining constraint of each
    assert "active" in d["wg_verify"].lower()
    assert "triage" in d["wg_set_gate"].lower()
    assert "deferred" in d["wg_defer"].lower()


def test_structured_arg_tools_declare_typed_properties():
    """Regression: a property-less object schema makes the harness string-encode array/object args."""
    from workgraph.mcp_server import tool_schemas

    s = tool_schemas()
    # the args that broke in-harness must be typed, not opaque objects
    assert s["wg_ingest"]["properties"]["nodes"]["type"] == "array"
    assert "nodes" in s["wg_ingest"]["required"]
    assert s["wg_add_node"]["properties"]["node"]["type"] == "object"
    assert "node" in s["wg_add_node"]["required"]
    assert s["wg_set_gate"]["properties"]["gate"]["type"] == "object"
    # every id-taking tool declares id:string
    for t in ("wg_show", "wg_claim", "wg_verify", "wg_signoff", "wg_set_gate", "wg_add_dep", "wg_resolve"):
        assert s[t]["properties"]["id"]["type"] == "string"
    # no tool may fall back to a property-less schema (the original bug)
    for name, sch in s.items():
        assert "properties" in sch and isinstance(sch["properties"], dict)


def test_execute_group_excludes_authorship_and_signoff():
    """AC-11/AC-12 — the execute surface can't create nodes, set gates, add deps, or sign off."""
    forbidden = {
        "wg_ingest",
        "wg_add_node",
        "wg_set_gate",
        "wg_add_dep",
        "wg_remove_node",
        "wg_signoff",
        "wg_resolve",
        "wg_defer",
        "wg_unblock",
        "wg_archive",
    }
    assert set(EXECUTE_TOOLS).isdisjoint(forbidden)
    assert "wg_signoff" not in EXECUTE_TOOLS  # the only door to `done` is plan-only


def test_wg_mermaid_is_a_read_tool():
    assert "wg_mermaid" in READ_TOOLS


def test_mermaid_handler_returns_mermaid(tmp_path):
    store.init_store(str(tmp_path))
    s = Service(str(tmp_path))
    s.ingest([{"id": "a", "gate": {"kind": "none"}}])
    out = tool_handlers(s)["wg_mermaid"]({})
    assert out["mermaid"].startswith("graph TD") and 'a["' in out["mermaid"]


def test_handlers_cover_exactly_the_manifest(tmp_path):
    store.init_store(str(tmp_path))
    handlers = tool_handlers(Service(str(tmp_path)))
    assert set(handlers) == set(READ_TOOLS) | set(EXECUTE_TOOLS) | set(PLAN_TOOLS)


def test_plan_handler_returns_waves(tmp_path):
    store.init_store(str(tmp_path))
    s = Service(str(tmp_path))
    s.ingest([{"id": "a", "gate": {"kind": "none"}}])
    handlers = tool_handlers(s)
    assert handlers["wg_plan"]({}) == {"waves": [["a"]]}


def test_error_envelope_maps_each_error_type():
    assert error_envelope(ConcurrencyError("x"))["retry"] is True
    it = error_envelope(IllegalTransition("a", "ready", ["claim"]))
    assert it["error"] == "illegal_transition" and it["current"] == "ready"
    assert it["allowed"] == ["claim"]
    sd = error_envelope(SurfaceDenied("signoff", "execute"))
    assert sd["action"] == "signoff" and sd["surface"] == "execute"
    ve = error_envelope(ValidationError("a", "deps", "bad"))
    assert ve["node"] == "a" and ve["field"] == "deps"


def test_build_server_constructs(tmp_path):
    store.init_store(str(tmp_path))
    assert M.build_server(str(tmp_path)) is not None
