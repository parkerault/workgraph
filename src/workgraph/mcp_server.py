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
from .models import Status
from .service import Service

# The surface partition (C-5). The execute group is a strict subset of read+execute, so an executor
# can never create a node, set a gate, add a dep, or sign off (AC-11/AC-12).
READ_TOOLS: tuple[str, ...] = ("wg_plan", "wg_status", "wg_show", "wg_ready", "wg_mermaid")
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
    "wg_remove_dep",
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
        "wg_status": lambda a: service.status(node_id=a.get("id"), status=a.get("status")),
        "wg_show": lambda a: service.show(a["id"]),
        "wg_ready": lambda a: {"ready": service.ready()},
        "wg_mermaid": lambda a: service.mermaid(
            direction=a.get("direction", "TD"),
            parent=a.get("parent"),
            status=a.get("status"),
            node=a.get("node"),
            depth=a.get("depth", 1),
        ),
        # execute
        "wg_claim": lambda a: service.claim(a["id"], who=a.get("who")),
        "wg_verify": lambda a: service.verify(a["id"], who=a.get("who")),
        "wg_request_signoff": lambda a: service.request_signoff(a["id"], a.get("note"), who=a.get("who")),
        "wg_reverify": lambda a: service.reverify(a["id"], who=a.get("who")),
        "wg_report_blocked": lambda a: service.report_blocked(a["id"], who=a.get("who")),
        # plan / operator
        "wg_ingest": lambda a: service.ingest(a["nodes"], who=a.get("who")),
        "wg_add_node": lambda a: service.add_node(a["node"], who=a.get("who")),
        "wg_set_gate": lambda a: service.set_gate(a["id"], a["gate"], who=a.get("who")),
        "wg_add_dep": lambda a: service.add_dep(a["id"], a["dep"], who=a.get("who")),
        "wg_remove_dep": lambda a: service.remove_dep(a["id"], a["dep"], who=a.get("who")),
        "wg_remove_node": lambda a: service.remove_node(a["id"], who=a.get("who")),
        "wg_signoff": lambda a: service.signoff(a["id"], a["who"], a.get("note")),
        "wg_resolve": lambda a: service.resolve(a["id"], a["rationale"], who=a.get("who")),
        "wg_defer": lambda a: service.defer(a["id"], who=a.get("who")),
        "wg_unblock": lambda a: service.unblock(a["id"], who=a.get("who")),
        "wg_archive": lambda a: service.archive(a["id"], who=a.get("who")),
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
    schemas = {
        "wg_plan": _obj({}),
        "wg_status": _obj(
            {
                "id": {"type": "string", "description": "a node id — its summary (omit for the project rollup)"},
                "status": {
                    "type": "string",
                    "enum": [s.value for s in Status],
                    "description": "return the ids of all nodes in this state",
                },
            }
        ),
        "wg_show": _obj(_id, ("id",)),
        "wg_ready": _obj({}),
        "wg_mermaid": _obj(
            {
                "direction": {"type": "string", "enum": ["TD", "LR"], "description": "layout direction (default TD)"},
                "parent": {"type": "string", "description": "slice: a parent id -> it and its children"},
                "status": {"type": "string", "enum": [s.value for s in Status], "description": "slice: only nodes in this state"},
                "node": {"type": "string", "description": "slice: center node for a dependency-neighborhood view"},
                "depth": {"type": "integer", "description": "neighborhood radius for `node` (default 1)"},
            }
        ),
        "wg_claim": _obj(_id, ("id",)),
        "wg_verify": _obj(_id, ("id",)),
        "wg_request_signoff": _obj({"id": _STR, "note": _STR}, ("id",)),
        "wg_reverify": _obj(_id, ("id",)),
        "wg_report_blocked": _obj(_id, ("id",)),
        "wg_ingest": _obj({"nodes": {"type": "array", "items": _NODE_SCHEMA}}, ("nodes",)),
        "wg_add_node": _obj({"node": _NODE_SCHEMA}, ("node",)),
        "wg_set_gate": _obj({"id": _STR, "gate": _GATE_SCHEMA}, ("id", "gate")),
        "wg_add_dep": _obj({"id": _STR, "dep": dict(_STR, description="dependency id")}, ("id", "dep")),
        "wg_remove_dep": _obj(
            {"id": _STR, "dep": dict(_STR, description="the dependency id to remove from this node's deps")},
            ("id", "dep"),
        ),
        "wg_remove_node": _obj(_id, ("id",)),
        "wg_signoff": _obj({"id": _STR, "who": _STR, "note": _STR}, ("id", "who")),
        "wg_resolve": _obj({"id": _STR, "rationale": _STR}, ("id", "rationale")),
        "wg_defer": _obj(_id, ("id",)),
        "wg_unblock": _obj(_id, ("id",)),
        "wg_archive": _obj(_id, ("id",)),
    }
    # Provenance: every mutating tool takes an optional actor recorded as the node's `updated_by`.
    who = {
        "type": "string",
        "description": "actor — your human handle (e.g. 'parker') or agent role/task "
        "(e.g. 'wg-executor:build-api'); recorded as the node's updated_by",
    }
    for name in EXECUTE_TOOLS + PLAN_TOOLS:
        schemas[name]["properties"].setdefault("who", who)
    return schemas


_DESCRIPTIONS = {
    "wg_plan": (
        "Return the orchestration plan: all nodes grouped into ordered concurrent waves. Nodes in "
        "the same wave have no dependency between them and can run in parallel; a node appears only "
        "after every node it depends on. Read-only."
    ),
    "wg_status": (
        "Answer 'where are we'. No argument -> a project rollup (counts of nodes by status, grouped "
        "by parent). `id` -> that node's summary (status, gate kind, last verify, sign-off, child "
        "rollup). `status` -> the ids of all nodes in that state. Read-only; never returns node bodies."
    ),
    "wg_show": (
        "Return full detail for one node: deps, gate (incl. command), rationale path, sign-off, and "
        "last-verify summary. Read-only."
    ),
    "wg_ready": (
        "Return the ids of nodes currently in `ready` — their dependencies are all terminal-good and "
        "they can be claimed. Read-only."
    ),
    "wg_mermaid": (
        "Render the graph (or a slice) as mermaid `graph` text, with each node's status baked into "
        "its label. Slice with `parent` (it + its children), `status` (nodes in that state), or "
        "`node`+`depth` (dependency neighborhood); default is the whole graph. Read-only — the caller "
        "renders the text itself (e.g. pipe it to the `mermaid-ascii` binary for a terminal view, or "
        "embed it in a doc)."
    ),
    "wg_claim": "Claim a `ready` node to start work on it (ready -> active). Execute surface.",
    "wg_verify": (
        "Run a `command`-gate node's gate command; the node must be `active`. On exit 0 it advances "
        "to `awaiting-signoff` and the run is recorded as evidence; on non-zero or timeout it stays "
        "`active` and the captured output is returned. Executes an arbitrary shell command in the "
        "store's working directory. Execute surface."
    ),
    "wg_request_signoff": (
        "For a `manual`-gate node, mark the executor's work ready for the operator to review "
        "(active -> awaiting-signoff). There is no command to run; the human vouch comes at sign-off. "
        "Execute surface."
    ),
    "wg_reverify": (
        "Return an `awaiting-signoff` node to `active` to re-run its gate when the evidence is stale "
        "(clears the recorded verify). Use before sign-off if the verified tree changed. Execute surface."
    ),
    "wg_report_blocked": (
        "Mark a node `blocked` (e.g. its gate keeps failing or a prerequisite was abandoned). "
        "Reversible via wg_unblock. Execute or operator surface."
    ),
    "wg_ingest": (
        "Declare a batch of nodes with their dependencies atomically (all-or-nothing). Deps may "
        "reference sibling nodes in the same batch, so a connected graph can be declared in any order. "
        "New nodes enter `triage`. Plan/operator surface."
    ),
    "wg_add_node": (
        "Add a single node (enters `triage`). Prefer wg_ingest to declare a connected graph in one "
        "call. Plan/operator surface."
    ),
    "wg_set_gate": (
        "Set or modify a node's gate. Allowed only while the node is in `triage` — gates lock once a "
        "node becomes `ready` (the gate-authorship clamp). Plan/operator surface."
    ),
    "wg_add_dep": (
        "Add a dependency edge to a node. Allowed only while the node is in `triage`. Plan/operator surface."
    ),
    "wg_remove_dep": (
        "Remove a dependency edge from a node (the inverse of wg_add_dep). Use it to release a "
        "dependent of an abandoned prerequisite — a `blocked` dependent returns to `ready` once its "
        "dead edge is gone — or to clear edges before deleting a wrongly-added node. Allowed while the "
        "dependent is `triage`, `ready`, or `blocked` (not once it is active/awaiting-signoff). "
        "Plan/operator surface."
    ),
    "wg_remove_node": (
        "Delete a node. Allowed only if it is not yet started (`triage` or `ready`) and no other node "
        "depends on it (clear edges first with wg_remove_dep); never leaves a dangling reference. To "
        "retire started or finished work use wg_defer/wg_archive. Plan/operator surface."
    ),
    "wg_signoff": (
        "Record the human operator's sign-off, moving an `awaiting-signoff` node to `done`. This is "
        "the ONLY transition into `done` and is plan/operator-only — an executor cannot reach `done`. "
        "Requires `who` (the human vouching); call only on the human's explicit instruction after the "
        "evidence is surfaced. Plan/operator surface."
    ),
    "wg_resolve": (
        "Resolve a `none`-gate (decision/coordination) node, recording the outcome as its rationale "
        "(ready/active -> resolved). A none-gate node reaches `resolved`, never `done`. Plan/operator surface."
    ),
    "wg_defer": (
        "Move a non-terminal node to `deferred` — a terminal state structurally distinct from `done` "
        "(deferred work can never be mistaken for finished). Plan/operator surface."
    ),
    "wg_unblock": (
        "Clear a `blocked` node and re-evaluate readiness: it returns to `ready` if its deps are "
        "terminal-good, otherwise stays `blocked`. Plan/operator surface."
    ),
    "wg_archive": (
        "Archive a terminal node, dropping it from the active set while keeping it queryable. "
        "Plan/operator surface."
    ),
}


def tool_descriptions() -> dict[str, str]:
    """One-paragraph, onboard-a-new-hire descriptions stating each tool's precondition + effect."""
    return dict(_DESCRIPTIONS)


def tool_annotations() -> dict[str, dict]:
    """MCP tool annotations (read-only / destructive / open-world hints) so the client can present
    risk and approval appropriately. Reads are read-only; `wg_verify` runs an arbitrary command
    (open-world); `wg_remove_node` deletes (destructive)."""
    ann: dict[str, dict] = {t: {"readOnlyHint": True, "openWorldHint": False} for t in READ_TOOLS}
    for t in EXECUTE_TOOLS + PLAN_TOOLS:
        ann[t] = {"readOnlyHint": False}
    ann["wg_verify"] = {"readOnlyHint": False, "openWorldHint": True}
    ann["wg_remove_dep"] = {"readOnlyHint": False, "destructiveHint": True}
    ann["wg_remove_node"] = {"readOnlyHint": False, "destructiveHint": True}
    return ann


_SERVER_INSTRUCTIONS = (
    "workgraph is the source of truth for this project's work — a deterministic graph of work "
    "units, their dependencies, and each one's completion gate. Keep the project's prose (status "
    "docs, work logs, READMEs, comments) reconciled with the workgraph, never the reverse.\n\n"
    "Every state-changing tool returns a `nudge` field. Treat it as an instruction, not "
    "decoration: after each transition, update the local docs that describe that work so the prose "
    "matches the workgraph. This is how 'deferred' is kept from being mistaken for 'done'."
)


def server_instructions() -> str:
    """Server-level instructions surfaced to the MCP client at connect time — they establish the
    workgraph-is-truth contract and tell the agent the per-mutation `nudge` is actionable."""
    return _SERVER_INSTRUCTIONS


def build_server(store_root: str):
    """Construct the low-level MCP server bound to a store (stdio transport)."""
    from mcp.server import Server
    import mcp.types as types

    service = Service(store_root)
    handlers = tool_handlers(service)
    schemas = tool_schemas()
    descriptions = tool_descriptions()
    annotations = tool_annotations()
    server = Server("workgraph", instructions=server_instructions())

    @server.list_tools()
    async def _list_tools():  # pragma: no cover - exercised via stdio at runtime
        return [
            types.Tool(
                name=name,
                description=descriptions[name],
                inputSchema=schemas[name],
                annotations=types.ToolAnnotations(**annotations[name]),
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
