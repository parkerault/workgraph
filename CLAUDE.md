# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

`workgraph` — a project-agnostic **CLI + MCP** capability that gives an AI **coordinator** a
deterministic work-unit graph: declare nodes + dependencies → get topologically-ordered concurrent
**waves**; gate each node's completion on a deterministic command (exit 0) **and** a human sign-off;
query "where are we" cheaply. It exists to make **"deferred" structurally impossible to confuse with
"done."** It is a *tracker, not an executor* — it only runs the gate command on demand.

**`SPEC.md` is the ground-truth contract** (stable IDs: `AC-n` criteria, `C-n` contracts, `D-n`
decisions with rationale, `WU-n` work units). `HANDOFF.md` is the original ratified design. `README.md`
has the surface-split deployment snippets. When you change behaviour, update `SPEC.md` too — the
tool's own *workgraph-is-truth / no-drift* principle applies to itself.

## Commands

- `uv sync` — install (Python pinned to 3.13 via `mise`; deps via `uv`). Commit `mise.toml`,
  `pyproject.toml`, `uv.lock`.
- `uv run pytest` — full suite (~109 tests).
- `uv run pytest tests/test_lifecycle.py::test_signoff_awaiting_to_done_stamps` — one test.
- `uv run pytest tests/test_lifecycle.py` — one module.
- `uv run workgraph init [path]` — scaffold a `.workgraph/` store.
- `uv run workgraph serve [path]` — MCP server over stdio. **Must be launched from a `mise`-activated
  context** so gate subprocesses inherit `uv`/python on `PATH` (the gate inherits the server's env).

## Architecture (the big picture)

Strictly layered: the engine is **pure**, the MCP server is a **thin** surface over it. Each module
implements a numbered contract from `SPEC.md`. A mutation flows: **MCP tool → `service` method →
`store.load` → `lifecycle.transition` (pure) → `store.save`**.

- **`models.py`** — the schema (`Node`/`Graph`/`Gate`/`Status`) every other module operates on.
- **`store.py` (C-1)** — the **only** I/O. `graph.yaml` is the single source of truth; git history is
  the transition log. `load → (graph, base_hash)`; `save(store_root, graph, base_hash)` does atomic
  temp-rename + **optimistic concurrency** (rejects if the on-disk hash changed) and validates
  schema / dup-ids / parent refs / dep cycles.
- **`graph.py` (C-2)** — pure: `graphlib` waves, cycle detection, readiness, rollup. No I/O.
- **`lifecycle.py` (C-3)** — pure state machine. `transition(graph, id, action, surface)`
  **deep-copies and returns a NEW graph** (never mutates its input). Enforces the transition table,
  surface capability, gate/dep immutability, and runs `recompute_readiness` after every mutation.
- **`gate.py` (C-4)** — subprocess gate runner; own process group (timeout kills the whole group);
  output truncated to trailing 4 KB for evidence, full log written to `.workgraph/runs/`.
- **`render.py`** — pure projection of a graph/slice → mermaid text (status baked into labels).
  Backs `wg_mermaid` / `workgraph mermaid`. workgraph emits the text; the caller renders it (e.g.
  pipes to `mermaid-ascii`) — the core never shells out for presentation.
- **`service.py`** — where operation logic lives: composes `load → transition/graph-op/gate →
  write_rationale → save`, holds `base_hash` across the pure transition, stamps timestamps
  (lifecycle has no clock), and attaches a status-aware `nudge` to every mutation response
  (`_nudge`, D-14 — reconcile prose with the workgraph; reads/failed-verify carry none).
- **`mcp_server.py` (C-5)** — thin low-level `mcp` stdio binding. `tool_handlers(service)` maps each
  `wg_*` tool to a service call; `error_envelope` maps typed errors; `server_instructions()` is
  advertised at connect time (workgraph-is-truth + the nudge is actionable). Tools are partitioned into
  `READ_TOOLS` / `EXECUTE_TOOLS` / `PLAN_TOOLS`.
- **`cli.py`** — `init` / `serve` only (no CRUD CLI; the agent surface is MCP).

Tests mirror this (`tests/test_<module>.py`); `test_e2e.py` drives the real tool handlers and
`test_live_mcp.py` spawns the real stdio server through an MCP client.

## Load-bearing invariants — do not break these

1. **Gate-authorship governance (the entire point).** The doer can never author/weaken its own gate
   or reach `done`. Enforced by (a) the consumer granting wave agents only the read+execute tool
   groups via the harness's per-tool MCP allowlist (README snippets; the real boundary — `SPEC.md`
   D-10), and (b) in-tool clamps as defense-in-depth: **gates/edges immutable once a node leaves
   `triage`**, and the **only transition into `done` is plan-only `wg_signoff`**. If you add a tool,
   place it in the correct group and keep `EXECUTE_TOOLS` a strict subset excluding any
   create/set-gate/add-dep/remove-dep/sign-off tool (`test_mcp` asserts this), and add any new
   plan tool to the README/SPEC executor `permissions.deny` lists.
2. **The status vocabulary is structural.** `triage → ready → active → awaiting-signoff → done`;
   terminals `done / resolved / deferred / archived`; **terminal-good = `{done, resolved}`**.
   `deferred` is unreachable from `done` (and vice-versa) — that gap is the bug this tool prevents.
   `none`-gate (decision) nodes reach `resolved`, never `done`. Readiness is **server-driven**:
   `recompute_readiness` runs inside every transition (advances `triage`/`blocked → ready` when deps
   are terminal-good; blocks dependents when a dep is abandoned).
3. **Keep `graph.yaml` compact (NFR-4).** Never inline rationale prose or gate output. Rationale →
   `.workgraph/rationale/<id>.md` (git-tracked); gate logs → `.workgraph/runs/` (gitignored).
4. **Purity.** `lifecycle` and `graph` do no I/O and no mutation of inputs — the `service` wraps all
   store I/O. Don't leak `store` calls into them.
