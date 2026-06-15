"""C-5 — MCP tool contract. Tools grouped read / execute / plan(operator) — the surface split (D-3).

Group membership is what the consumer's agent allowlist enforces (D-10 / Operational guardrails).
Each tool composes the C-1..C-4 ops via the Service and maps typed errors to a structured envelope.
The server uses local stdio transport only — no network listener, no bound port (NFR-5).
"""

from __future__ import annotations

import json
from typing import Any, Callable

from .errors import (
    ConcurrencyError,
    IllegalTransition,
    SurfaceDenied,
    ValidationError,
    WorkgraphError,
)
from .service import Service

# The surface partition (C-5). The execute group is a strict subset of read+execute, so an executor
# can never create a node, set a gate, add a dep, or sign off (AC-11/AC-12).
READ_TOOLS: tuple[str, ...] = ("wg_plan", "wg_status", "wg_show", "wg_ready")
EXECUTE_TOOLS: tuple[str, ...] = (
    "wg_claim",
    "wg_verify",
    "wg_request_signoff",
    "wg_reverify",
    "wg_report_blocked",
)
PLAN_TOOLS: tuple[str, ...] = (
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
)


def tool_manifest() -> dict[str, tuple[str, ...]]:
    """Machine-readable group manifest for the AC-11 subset test."""
    return {"read": READ_TOOLS, "execute": EXECUTE_TOOLS, "plan": PLAN_TOOLS}


def error_envelope(exc: Exception) -> dict[str, Any]:
    """Map a typed error to the structured tool-error result (C-5)."""
    if isinstance(exc, ConcurrencyError):
        return {"error": "concurrency", "message": "graph changed on disk; reload and retry", "retry": True}
    if isinstance(exc, IllegalTransition):
        return {
            "error": "illegal_transition",
            "node": exc.node_id,
            "current": exc.current,
            "allowed": exc.allowed,
        }
    if isinstance(exc, SurfaceDenied):
        return {"error": "surface_denied", "action": exc.action, "surface": exc.surface}
    if isinstance(exc, ValidationError):
        return {"error": "validation", "node": exc.node_id, "field": exc.field, "message": str(exc)}
    return {"error": "internal", "message": str(exc)}


def tool_handlers(service: Service) -> dict[str, Callable[[dict], dict]]:
    """Map each tool name to a handler over its arguments dict. Surfaces are fixed by group."""
    return {
        # read
        "wg_plan": lambda a: {"waves": service.plan()},
        "wg_status": lambda a: service.status(a.get("id")),
        "wg_show": lambda a: service.show(a["id"]),
        "wg_ready": lambda a: {"ready": service.ready()},
        # execute
        "wg_claim": lambda a: service.claim(a["id"]),
        "wg_verify": lambda a: service.verify(a["id"]),
        "wg_request_signoff": lambda a: service.request_signoff(a["id"], a.get("note")),
        "wg_reverify": lambda a: service.reverify(a["id"]),
        "wg_report_blocked": lambda a: service.report_blocked(a["id"]),
        # plan / operator
        "wg_ingest": lambda a: service.ingest(a["nodes"]),
        "wg_add_node": lambda a: service.add_node(a["node"]),
        "wg_set_gate": lambda a: service.set_gate(a["id"], a["gate"]),
        "wg_add_dep": lambda a: service.add_dep(a["id"], a["dep"]),
        "wg_remove_node": lambda a: service.remove_node(a["id"]),
        "wg_signoff": lambda a: service.signoff(a["id"], a["who"], a.get("note")),
        "wg_resolve": lambda a: service.resolve(a["id"], a["rationale"]),
        "wg_defer": lambda a: service.defer(a["id"]),
        "wg_unblock": lambda a: service.unblock(a["id"]),
        "wg_archive": lambda a: service.archive(a["id"]),
    }


_STR = {"type": "string"}

_GATE_SCHEMA = {
    "type": "object",
    "properties": {
        "kind": {"type": "string", "enum": ["command", "manual", "none"]},
        "command": {"type": "string", "description": "shell command; required iff kind == command"},
        "timeout": {"type": "integer", "description": "gate timeout in seconds (optional)"},
    },
    "required": ["kind"],
}

_NODE_SCHEMA = {
    "type": "object",
    "properties": {
        "id": {"type": "string", "description": "stable kebab id"},
        "kind": {"type": "string", "description": "free-form project string (milestone|epic|unit|decision|…)"},
        "parent": {"type": "string", "description": "membership only — NOT a dependency"},
        "deps": {"type": "array", "items": {"type": "string"}, "description": "ids this node depends on"},
        "gate": _GATE_SCHEMA,
    },
    "required": ["id", "gate"],
}


def _obj(properties: dict, required: tuple[str, ...] = ()) -> dict:
    # No additionalProperties:false — match the in-harness-verified minimal form; the point is
    # typed properties so structured args aren't string-encoded, not strict rejection of extras.
    return {"type": "object", "properties": properties, "required": list(required)}


def tool_schemas() -> dict[str, dict]:
    """Per-tool JSON-Schema `inputSchema`. Typed properties are REQUIRED — a property-less object
    schema makes Claude Code string-encode array/object args (e.g. `nodes`), which the server then
    iterates character-by-character. Keep this in sync with `tool_handlers` arg access."""
    _id = {"id": dict(_STR, description="node id")}
    return {
        "wg_plan": _obj({}),
        "wg_status": _obj({"id": {"type": "string", "description": "omit for the project rollup"}}),
        "wg_show": _obj(_id, ("id",)),
        "wg_ready": _obj({}),
        "wg_claim": _obj(_id, ("id",)),
        "wg_verify": _obj(_id, ("id",)),
        "wg_request_signoff": _obj({"id": _STR, "note": _STR}, ("id",)),
        "wg_reverify": _obj(_id, ("id",)),
        "wg_report_blocked": _obj(_id, ("id",)),
        "wg_ingest": _obj({"nodes": {"type": "array", "items": _NODE_SCHEMA}}, ("nodes",)),
        "wg_add_node": _obj({"node": _NODE_SCHEMA}, ("node",)),
        "wg_set_gate": _obj({"id": _STR, "gate": _GATE_SCHEMA}, ("id", "gate")),
        "wg_add_dep": _obj({"id": _STR, "dep": dict(_STR, description="dependency id")}, ("id", "dep")),
        "wg_remove_node": _obj(_id, ("id",)),
        "wg_signoff": _obj({"id": _STR, "who": _STR, "note": _STR}, ("id", "who")),
        "wg_resolve": _obj({"id": _STR, "rationale": _STR}, ("id", "rationale")),
        "wg_defer": _obj(_id, ("id",)),
        "wg_unblock": _obj(_id, ("id",)),
        "wg_archive": _obj(_id, ("id",)),
    }


_DESCRIPTIONS = {
    "wg_plan": "Return the orchestration plan: nodes grouped into ordered concurrent waves.",
    "wg_status": "Status: a project rollup (no id) or one node's summary (id).",
    "wg_show": "Full detail for one node.",
    "wg_ready": "Ids of nodes currently ready to claim.",
    "wg_claim": "Claim a ready node (ready -> active).",
    "wg_verify": "Run a command node's gate; on exit 0 -> awaiting-signoff.",
    "wg_request_signoff": "Mark a manual node's work ready for operator review.",
    "wg_reverify": "Return an awaiting-signoff node to active to re-run its gate.",
    "wg_report_blocked": "Mark a node blocked.",
    "wg_ingest": "Declare a batch of nodes+deps atomically (forward refs allowed).",
    "wg_add_node": "Add a single node (enters triage).",
    "wg_set_gate": "Set/modify a node's gate (triage only).",
    "wg_add_dep": "Add a dependency edge (triage only).",
    "wg_remove_node": "Remove a triage node with no dependents.",
    "wg_signoff": "Operator sign-off: awaiting-signoff -> done.",
    "wg_resolve": "Resolve a none-gate node (records a rationale).",
    "wg_defer": "Defer a node (terminal, distinct from done).",
    "wg_unblock": "Unblock a node (re-enters readiness recompute).",
    "wg_archive": "Archive a terminal node.",
}


def build_server(store_root: str):
    """Construct the low-level MCP server bound to a store (stdio transport)."""
    from mcp.server import Server
    import mcp.types as types

    service = Service(store_root)
    handlers = tool_handlers(service)
    schemas = tool_schemas()
    server = Server("workgraph")

    @server.list_tools()
    async def _list_tools():  # pragma: no cover - exercised via stdio at runtime
        return [
            types.Tool(
                name=name,
                description=_DESCRIPTIONS.get(name, name),
                inputSchema=schemas[name],
            )
            for name in handlers
        ]

    @server.call_tool()
    async def _call_tool(name: str, arguments: dict):  # pragma: no cover - runtime path
        try:
            if name not in handlers:
                result = {"error": "unknown_tool", "name": name}
            else:
                result = handlers[name](arguments or {})
        except WorkgraphError as e:
            result = error_envelope(e)
        return [types.TextContent(type="text", text=json.dumps(result))]

    return server


async def serve(store_root: str) -> None:  # pragma: no cover - runtime path
    """Run the MCP server over stdio."""
    from mcp.server.stdio import stdio_server

    server = build_server(store_root)
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())
