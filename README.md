# workgraph

A coordinator's **deterministic work-unit graph** (CLI + MCP). Declare nodes with dependencies,
get a topologically-ordered plan *including the concurrent waves*, gate each node's completion on a
**deterministic command (exit 0) + a human sign-off**, and answer "where are we" as an instant
query — so an AI coordinator stops burning tokens on a growing prose spine and can never mark
deferred work "done."

See [`SPEC.md`](SPEC.md) for the full specification.

## Status

Under construction (alpha). Core engine → MCP surface → e2e. Built test-first.

## Install

Requires [`uv`](https://docs.astral.sh/uv/) (and Python 3.13, which `uv`/`mise` can provide). The
repo lives at `~/projects/workgraph` in the examples below.

**Option A — run without installing** (what the MCP config below uses; nothing lands on your PATH):

```sh
uv run --project ~/projects/workgraph workgraph --help
```

**Option B — install a global `workgraph` command:**

```sh
uv tool install --editable ~/projects/workgraph     # tracks src; or drop --editable for a snapshot
# from git instead of a local clone:
uv tool install git+ssh://git@github.com/parkerault/workgraph.git
workgraph --help                                    # now on PATH (~/.local/bin)
```

**Use it in a project.** Scaffold a store, then register the MCP server with Claude Code so the
coordinator drives it. A project-scoped `.mcp.json` points the server at that project's store via
`${CLAUDE_PROJECT_DIR}`:

```sh
cd /path/to/your/project
uv run --project ~/projects/workgraph workgraph init .     # creates ./.workgraph/
```

```json
// .mcp.json
{
  "mcpServers": {
    "workgraph": {
      "command": "uv",
      "args": ["run", "--project", "/home/parker/projects/workgraph", "workgraph", "serve", "${CLAUDE_PROJECT_DIR:-.}"]
    }
  }
}
```

Reconnect the session (`/mcp`) to confirm `workgraph` is connected, then see
[The surface split](#the-surface-split-governance) to restrict wave agents to the read+execute
tools. (Launch the session from a `mise`-activated shell so gate commands inherit `uv`/python on
PATH.)

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

## Visualize

`workgraph` emits **mermaid** text for the graph (or a slice), with each node's status baked into
its label. It never renders the diagram itself — the caller pipes the text to a renderer of its
choice. Over MCP this is the read-only `wg_mermaid` tool; from the shell, run it **from the project
whose graph you want to see** (the directory holding `.workgraph/`):

```sh
# Launch workgraph the same way as in Install. If you `uv tool install`ed it (Option B):
workgraph mermaid | mermaid-ascii --ascii
# …or, without a global install, point uv at the repo (Option A) — note `--project` keeps your
# current directory, so workgraph still reads *this* project's ./.workgraph/:
uv run --project ~/projects/workgraph workgraph mermaid | mermaid-ascii --ascii

# slices: a milestone's children · the active frontier · one node's neighborhood
workgraph mermaid --parent m-foundation | mermaid-ascii --ascii
workgraph mermaid --status active        | mermaid-ascii --ascii
workgraph mermaid --node build-core --depth 1
```

It reads `./.workgraph/` by default; point it at another store with a path argument
(`workgraph mermaid /path/to/project`). (Bare `uv run workgraph …` only works *inside* the workgraph
repo — from any other project use one of the two launch forms above.)

The raw mermaid renders richly on GitHub and mermaid.live too; [`mermaid-ascii`](https://github.com/AlexanderGrooff/mermaid-ascii)
is just the terminal renderer. Prefer slices for large graphs (the ASCII renderer doesn't wrap to
terminal width), and embed the mermaid (not the ASCII) in committed docs — it diffs cleanly and
renders in-place.

## Provenance

Every transition stamps the touched node with `updated_at` and `updated_by`. Pass `who` on any
mutating tool/CLI call to record the actor — suggested convention:

- **Humans:** a handle — `parker`.
- **Agents:** your role, optionally with context — `coordinator`, `wg-executor`, or
  `wg-executor:build-api` (`role:node`) for traceability.

If omitted, `who` falls back to the calling surface (`plan` / `execute`), so it's never empty.
`signoff` remains the distinct human vouch that opens `done`.

## Reconciliation nudge

The workgraph is the source of truth — so when an agent changes it, the surrounding prose (status docs,
work logs, comments) can go stale. Every state-changing tool therefore returns a **`nudge`**: a short,
status-aware reminder to reconcile the local docs with the workgraph. Terminal transitions are sharpest —
`done`/`resolved` say "record it complete," `deferred`/`archived` say "set aside, **not** completed —
nothing should imply it shipped," `blocked` says "note the blocker." A *failed* `wg_verify` settles
nothing and returns no nudge; reads never nudge. The MCP server also advertises this contract in its
connect-time `instructions`, so the agent treats the nudge as actionable, not decorative. The nudge is
advisory and lives only in the response — it is never written into `graph.yaml`.

## Revising the graph

Plans change. Two plan-surface moves cover the common cases (an abandoned `deferred`/`archived`
prerequisite **blocks** its dependents by design — that's the safety default, and `wg_remove_dep`
is the explicit release):

- **A planned task is no longer needed** — `wg_remove_dep <dependent> <task>` for each dependent
  (a `blocked` dependent returns to `ready` as its dead edge is dropped), then `wg_defer`/`wg_archive`
  the task to keep it as an abandoned record.
- **A node was added by mistake** — clear its inbound edges with `wg_remove_dep <dependent> <node>`,
  then `wg_remove_node <node>`. (Removal is allowed only while a node is not-yet-started — `triage`
  or `ready` — with no remaining dependents; started/finished work is retired with `defer`/`archive`.)

`wg_remove_dep` is the inverse of `wg_add_dep`, but the rules are asymmetric on purpose: you can
*remove* a wrong prerequisite from a not-yet-started or blocked node, but you can never *add* one to
work that's already cleared to run (that would retroactively gate it).

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
      "mcp__workgraph__wg_remove_dep", "mcp__workgraph__wg_remove_node",
      "mcp__workgraph__wg_signoff",
      "mcp__workgraph__wg_resolve", "mcp__workgraph__wg_defer",
      "mcp__workgraph__wg_unblock", "mcp__workgraph__wg_archive"
    ]
  }
}
```

**Coordinator / operator agent** keeps the full set (it records the human's sign-off on instruction,
after the evidence is surfaced).
