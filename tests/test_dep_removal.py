"""wg_remove_dep — the plan-surface inverse of wg_add_dep. Lets the operator release dependents of
an abandoned prerequisite, and delete a wrongly-added node (clear its edges first). Closes the gap
where a deferred dependency blocked its dependents forever and a no-dep node was unremovable."""

from __future__ import annotations

import pytest

from workgraph import store
from workgraph.errors import ValidationError
from workgraph.mcp_server import EXECUTE_TOOLS, PLAN_TOOLS, tool_handlers
from workgraph.service import Service


def svc(tmp_path):
    store.init_store(str(tmp_path))
    return Service(str(tmp_path))


def cmd(nid, **kw):
    kw.setdefault("gate", {"kind": "command", "command": "true"})
    return {"id": nid, **kw}


def test_abandon_prereq_and_unblock_dependent(tmp_path):
    s = svc(tmp_path)
    s.ingest([cmd("prereq"), cmd("dependent", deps=["prereq"])])
    s.defer("prereq")  # planned but no longer needed -> dependent is blocked
    assert s.status("dependent")["status"] == "blocked"
    s.remove_dep("dependent", "prereq")  # dissolve the edge
    assert s.status("dependent")["status"] == "ready"  # auto-unblocked by recompute
    s.archive("prereq")  # keep the abandoned node as a record
    assert s.status("prereq")["status"] == "archived"


def test_remove_wrongly_added_node_with_dependents(tmp_path):
    s = svc(tmp_path)
    s.ingest([cmd("oops"), cmd("real", deps=["oops"])])
    with pytest.raises(ValidationError):
        s.remove_node("oops")  # rejected: `real` still depends on it
    s.remove_dep("real", "oops")  # clear the edge first
    assert s.remove_node("oops")["removed"] == "oops"  # now removable (was ready, no dependents)
    assert "oops" not in store.load(s.root)[0].nodes


def test_remove_node_with_dependents_error_points_to_the_fix(tmp_path):
    # The reactive path must be self-remediating: name the blocking dependent AND the remedy
    # (remove the dependency edge first) so an agent recovers without reading the docs.
    s = svc(tmp_path)
    s.ingest([cmd("watchdog"), cmd("alpha", deps=["watchdog"])])
    with pytest.raises(ValidationError) as ei:
        s.remove_node("watchdog")
    msg = str(ei.value).lower()
    assert "alpha" in msg  # which node blocks the removal
    assert "dependency" in msg or "edge" in msg  # the remedy: drop the edge first


def test_remove_dep_is_plan_surface_only():
    assert "wg_remove_dep" in PLAN_TOOLS
    assert "wg_remove_dep" not in EXECUTE_TOOLS  # an executor can never reshape deps


def test_remove_dep_handler_wired_and_nudges(tmp_path):
    s = svc(tmp_path)
    s.ingest([cmd("a"), cmd("b", deps=["a"])])
    s.defer("a")
    out = tool_handlers(s)["wg_remove_dep"]({"id": "b", "dep": "a"})
    assert s.status("b")["status"] == "ready"
    assert "nudge" in out
