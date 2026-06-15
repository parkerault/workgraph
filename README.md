# workgraph

A coordinator's **deterministic work-unit graph** (CLI + MCP). Declare nodes with dependencies,
get a topologically-ordered plan *including the concurrent waves*, gate each node's completion on a
**deterministic command (exit 0) + a human sign-off**, and answer "where are we" as an instant
query — so an AI coordinator stops burning tokens on a growing prose spine and can never mark
deferred work "done."

See [`SPEC.md`](SPEC.md) for the full specification.

## Status

Under construction (alpha). Core engine → MCP surface → e2e. Built test-first.

## Develop

```sh
uv sync
uv run pytest
```

Run the MCP server (must be launched from a `mise`-activated context so gate commands inherit
`uv`/python on PATH):

```sh
uv run workgraph init        # scaffold a .workgraph/ store
uv run workgraph serve       # launch the MCP server over stdio
```

## The surface split (governance)

`workgraph` exposes three tool groups — **read**, **execute**, **plan/operator**. The gate-authorship
clamp (a doer can never author or weaken its own gate, nor reach `done`) is enforced by giving wave
agents only the read+execute groups. This is enforced at the Claude Code per-tool MCP permission
layer (verified — see `SPEC.md` D-10), with two in-tool clamps as defense-in-depth: gates are
immutable once a node leaves `triage`, and the only transition into `done` is the plan-only
`wg_signoff`.

**Wave / executor agent** — grant only read + execute. Either an allowlist in the subagent's
frontmatter:

```yaml
tools: mcp__workgraph__wg_plan, mcp__workgraph__wg_status, mcp__workgraph__wg_show, mcp__workgraph__wg_ready, mcp__workgraph__wg_claim, mcp__workgraph__wg_verify, mcp__workgraph__wg_request_signoff, mcp__workgraph__wg_reverify, mcp__workgraph__wg_report_blocked
```

…or (preferred, because a bare-name deny removes the tool from the agent's context entirely) a
`permissions.deny` of the plan group in `settings.json` — strongest as **managed settings**, which
cannot be overridden:

```json
{
  "permissions": {
    "deny": [
      "mcp__workgraph__wg_ingest", "mcp__workgraph__wg_add_node",
      "mcp__workgraph__wg_set_gate", "mcp__workgraph__wg_add_dep",
      "mcp__workgraph__wg_remove_node", "mcp__workgraph__wg_signoff",
      "mcp__workgraph__wg_resolve", "mcp__workgraph__wg_defer",
      "mcp__workgraph__wg_unblock", "mcp__workgraph__wg_archive"
    ]
  }
}
```

**Coordinator / operator agent** keeps the full set (it records the human's sign-off on instruction,
after the evidence is surfaced).
