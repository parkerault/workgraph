# workgraph — Spec

> Greenfield application tier. Derived by `spec-formalizer` from `HANDOFF.md` (a ratified design), then hardened by an `adversarial-review` pass (6 spec lenses + skeptic verification) whose confirmed findings are folded in (see the changelog at the foot of the Decision log).
> The ratified decisions in the handoff's §Ratified design are recorded here as fixed (Decision log) and were **not** relitigated. The handoff's eight open questions are resolved below (D-1…D-13, Assumptions, Human-decision gates).

## Problem statement & goals

`workgraph` is a **project-agnostic** capability that gives an AI **coordinator** a deterministic work-unit graph for multi-session, agent-driven builds. It replaces the current practice of coordinating through an ever-growing prose "spine" + parallel work-log, which has two recurring failure modes: (1) the artifacts **drift** — a node is marked "completed" in the spine when it was actually **deferred**; (2) reading the whole spine to learn status **burns tokens**.

It fixes both by applying two principles from gamedev-studio's *runtime* to the *meta-build process*:
- **Board-is-truth** — one git-tracked state file is the single source of truth; status is an instant query, never a prose read.
- **Only-a-lead-marks-done** — a node reaches `done` only on deterministic gate evidence **plus** a human sign-off; the doer can never judge its own doneness.

**Goals.** Give a coordinator: (a) **declare** nodes + dependencies → get a topologically-ordered plan *including the concurrent waves*; (b) **gate** each node's completion on a deterministic command (exit 0) and/or a human sign-off; (c) **query** "where are we" cheaply (project-level rollup or per-node); (d) make "deferred" structurally distinct from "done" so deferred work can never be mistaken for finished.

**Non-goals (explicitly out of scope).** It is a **tracker, not an executor** — it runs only the gate command, on demand; it does **not** run the work. No scheduler/cron, no auth server, no multi-repo federation, no web UI, no append-only event log (deferred — git history is the transition log). It knows nothing about any specific project (no slices/contracts/roles); `kind` is a free-form project string and the gate is an opaque command.

## Functional requirements

- **FR-1 — Declare graph & get plan** (headline op a). As a coordinator, I declare a set of nodes with `deps` in one shot via `wg_ingest` (an atomic batch op) — or incrementally via `wg_add_node` — and get back an orchestration plan: the nodes grouped into ordered **concurrent waves**. New nodes are created in `triage` (the only entry status; see state machine).
  - **AC-1:** Given ≥3 nodes whose `deps` form a DAG, ingested via `wg_ingest`, `wg_plan` returns a list of waves (lists of node ids) where every node appears in a later wave than all of its `deps`, computed via `graphlib.TopologicalSorter`. Nodes with no unsatisfied dep share the first wave.
  - **AC-2:** Given a declaration that introduces a dependency **cycle** (including a self-dependency `deps: [self]`), the mutation is rejected atomically with an error naming at least one node on the cycle; the store is left unchanged (`graph.yaml` byte-identical).
  - **AC-3:** Given a `dep` referencing an id unknown *after the batch is applied*, an unknown `gate.kind`, a missing required field, a **duplicate id**, or a `parent` that is unknown / self / forms a parent cycle, the mutation is rejected with a validation error naming the offending node id and field; the store is unchanged. (Within one `wg_ingest` batch, `deps`/`parent` **may** reference sibling nodes declared in the same batch — refs resolve against the post-batch node set, so a connected DAG can be declared in any order.)
  - **AC-21:** `wg_ingest` is atomic: a batch containing any node that violates AC-2/AC-3 is rejected whole — either all nodes are added or none.
  - **AC-22:** On an empty store, `wg_plan` returns `[]` (no waves) and `wg_status` (rollup) returns a zero/empty rollup — neither errors.

- **FR-2 — Status query** (headline op b). As a coordinator, I ask "where are we" and get only what I asked for, never the raw store.
  - **AC-4:** `wg_status` with no node id returns a **project-level rollup**: counts of nodes by status, grouped by `parent`, **and** carries none of `rationale` text, `gate.command`, or gate output (the two halves — correct counts, and no body leakage — are asserted separately).
  - **AC-5:** `wg_status <id>` returns that node's `status`, `kind`, `gate.kind`, `last_verify` summary (exit code + timestamp), `signoff` (key absent when none), and — only when the node is a `parent` — the child rollup (counts by status). Asserted field-by-field against a parent fixture and a leaf fixture.

- **FR-3 — Command-gate verification.** A `command`-gate node advances only when its gate command exits 0.
  - **AC-6:** For a `command`-gate node in status `active`, `wg_verify <id>` runs `gate.command` in the store's configured `working_dir`; a command finishing within `timeout` is treated as a real exit, one exceeding it is killed and routed to AC-7. On exit 0 the node moves to `awaiting-signoff` and `last_verify` records `{exit_code: 0, ran_at, log}`.
  - **AC-7:** On non-zero exit **or** timeout, the node stays `active`, `last_verify` records the failure, and the tool response returns the captured output truncated to its **trailing 4 KB** (prefixed with a `[truncated N bytes]` marker when truncation occurred) as evidence. A node never advances on a failed gate.

- **FR-4 — Human sign-off** (operator surface; the cheap-verification clamp). Completion requires a human ack on top of green evidence; the **coordinator agent records the ack on the human's instruction** after the evidence is surfaced.
  - **AC-8:** `wg_signoff <id> --who <name> [--note <text>]` on an `awaiting-signoff` node moves it to `done` and stamps `signoff: {who, at, note}`; the response echoes the `last_verify` evidence (or, for a `manual` gate, the recorded rationale) that justified it.
  - **AC-9:** `wg_signoff` on a node **not** in `awaiting-signoff` is rejected with the node's current status and allowed transitions (the gate cannot be skipped).
  - **AC-26:** A `manual`-gate node reaches `done` only through an operator `wg_signoff` (there is no command to pass); `wg_request_signoff` merely marks the executor's work ready for review (carrying an optional note → the node's `rationale`). The operator's sign-off stamp `{who, at, note}` is the visible human vouch that *is* the manual gate's evidence — an executor can never produce it (AC-12).

- **FR-5 — Gate-authorship governance** (the D-4 analog; the load-bearing adversarial clamp). The executor can never author/weaken its own gate or reach `done`.
  - **AC-10:** `wg_set_gate` or `wg_add_dep` on a node whose status is **not** `triage` is rejected (gates and edges are immutable once a node leaves `triage`); the store is unchanged.
  - **AC-11:** The execute-surface tool group exposes **no** tool that can create a node, set/modify a gate, add a dep, or perform sign-off. (Verified by a manifest test asserting the execute group's tool names are a subset of `{wg_plan, wg_status, wg_show, wg_ready, wg_claim, wg_verify, wg_request_signoff, wg_reverify, wg_report_blocked}`.)
  - **AC-12:** No execute-surface tool transitions a node to `done`; the only transition into `done` is `wg_signoff` on the operator surface.

- **FR-6 — Terminal vocabulary** (`deferred`/`resolved` structurally distinct from `done`).
  - **AC-13:** `wg_defer <id>` moves a non-terminal node to `deferred`. There is **no** transition between `deferred` and `done` in either direction (asserted over the full transition table).
  - **AC-14:** A `none`-gate node can reach `resolved` (via `wg_resolve` from `ready` or `active`, which requires a non-empty `rationale` recording the outcome) but **cannot** reach `done`; a `command`/`manual`-gate node cannot reach `resolved`. A `none`-gate node needs no executor `wg_claim` — the operator resolves it directly from `ready`.
  - **AC-15:** Readiness counts only `done` and `resolved` as terminal-good. A node with a `deferred`, `blocked`, or `archived` dependency does **not** become `ready`; it is flagged `blocked` (prerequisite abandoned).

- **FR-7 — Persistence & transition log.**
  - **AC-16:** Every successful mutation rewrites `graph.yaml` atomically (write-temp-then-rename) **and** (separately asserted) re-serializing after mutating node X yields a `graph.yaml` whose line-level diff against the prior file touches only lines belonging to X (deterministic, stable key ordering).
  - **AC-17:** With a fault injected at each write point — mid temp-file write, and after the temp write but before the rename — the store reloads to the last committed state, and an unparseable file is rejected with a clear error (never served as partial/empty) (NFR-3).
  - **AC-18:** A write whose on-disk base content hash no longer matches the hash read at load (a concurrent external edit or a second server) is rejected with a reload instruction; the operator's intended change is not silently lost (optimistic concurrency).

- **FR-8 — Node lifecycle, mutation integrity & rollup.**
  - **AC-19:** Transitions follow the state machine (Data & state model). An invalid transition (e.g. `ready → done`) is rejected with the node's current status and the list of allowed transitions.
  - **AC-20:** A `parent` is **membership only** (it creates no dependency edge; only `deps` order work). A parent node's status query reports its child rollup (counts by status); a `parent` that itself carries a `command`/`manual` gate cannot reach `done` while any child is non-terminal-good. A parent with **zero children** behaves as a normal leaf for its own gate.
  - **AC-23:** After every mutation the server recomputes readiness: a `triage` **or `blocked`** node whose deps are all terminal-good is advanced to `ready` (locking a `triage` node's gate + edges — a `blocked` node that left `triage` earlier is already locked); a non-terminal node one of whose deps just became non-terminal-good (`deferred`/`blocked`/`archived`) is flagged `blocked`. No executor tool performs this — it is intrinsic to each write.
  - **AC-24:** `wg_remove_node` is rejected unless the node is in `triage` **and** no other node lists it in `deps` or `parent`; removal never leaves a dangling reference. (To retire a started node, use `wg_defer`/`wg_archive`, not removal.)
  - **AC-25:** An `awaiting-signoff` node can be returned to `active` via `wg_reverify` when its evidence is stale (e.g. the verified tree changed); this clears `last_verify`, so the node must pass its gate again before re-reaching `awaiting-signoff`. Sign-off (AC-8) therefore always acts on current green evidence.

## Non-functional requirements

- **NFR-1 (performance):** On the dev box, after one warm-up call, the median of ≥20 consecutive `wg_plan` and `wg_status` (rollup) calls over a **1,000-node graph of mean fan-out ≈3** each returns in < 500 ms. (Figures are formalizer-set proceed-on targets — D-13.)
- **NFR-2 (scale):** Correct waves, readiness, and rollup for graphs up to **1,000 nodes** — the meta-build design horizon (hundreds of nodes), not millions. For a graph above 1,000 nodes, every mutation either completes leaving `graph.yaml` schema-valid and loadable, or is rejected — never a partial/invalid file (reuses the NFR-3 corruption invariant); it may be slow.
- **NFR-3 (durability / atomicity):** A forced `SIGKILL` at any point during a write leaves the previously committed `graph.yaml` valid and loadable; the load path never serves a partial file (it fails closed with a clear error if the file is unparseable).
- **NFR-4 (compactness):** `graph.yaml` contains **no** inline rationale prose and **no** inline gate output; status payloads return only the requested scope (a rollup returns counts, not bodies).
- **NFR-5 (security / trust):** Gate commands run under the operator's own OS identity, in the store's configured `working_dir`, with **no** privilege elevation. The MCP server uses local **stdio** transport only — no network listener, no bound port. The tool itself requires no secrets.

## Architecture / component decomposition

Single Python package, layered so the engine is a pure library and the MCP server is a thin surface over it. Reference: HANDOFF.md §Ratified design (this section does not relitigate it).

- **`models`** — node dataclasses + the YAML schema (the shape in Data & state model).
- **`store`** (→ C-1) — load/save `graph.yaml`: schema validation (incl. dup-id and `parent` refs), atomic write, optimistic-concurrency hash check, rationale-file writes. System of record.
- **`graph`** (→ C-2) — topo waves + cycle detection via `graphlib.TopologicalSorter`, readiness computation, parent rollup. No I/O.
- **`lifecycle`** (→ C-3) — the state machine: valid transitions, **surface-capability** enforcement, **gate-immutability-after-triage** enforcement, readiness recompute.
- **`gate`** (→ C-4) — the gate runner: execute `command` in `working_dir` (own process group, server env) with timeout, capture output to a gitignored run log.
- **`mcp_server`** (→ C-5) — MCP tools mapped to core ops, partitioned into **read / execute / plan(operator)** groups (the surface split); drives the per-write readiness recompute and the error envelope.
- **`cli`** — minimal: `workgraph init` (scaffold a store) and `workgraph serve` (launch the MCP server over stdio). No CRUD CLI in MVP (D-2).

## Data & state model

**System of record:** one git-tracked YAML file at `<store-root>/.workgraph/graph.yaml`. **Transition log:** git history of that file (every transition is a reviewable diff). Rationale prose lives in tracked markdown under `.workgraph/rationale/`; gate output lives in **gitignored** `.workgraph/runs/`.

Node schema (one flat node type; `kind` + `parent` are the only structural extras — not two tiers):

```yaml
version: 1
working_dir: .                 # gate-runner cwd, relative to store root
nodes:
  - id: build-core             # stable kebab id (unique key)
    kind: unit                 # free-form project string: milestone | epic | unit | decision | deferral | …
    parent: m-foundation       # optional — membership only (rollup); NOT a dependency
    deps: [decide-storage]     # depends_on edges (ids)
    status: triage             # entry status; see state machine below
    gate:
      kind: command            # command | manual | none
      command: "uv run pytest tests/core"   # required iff kind == command
      timeout: 120             # seconds; optional, default from config (D-13)
    rationale: rationale/build-core.md       # optional path to tracked .md (never inline)
    signoff:                   # key absent until done; stamped at sign-off
      who: parker
      at: 2026-06-15T12:00:00Z
      note: "evidence reviewed"
    last_verify:               # absent until first verify; summary only (NFR-4)
      exit_code: 0
      ran_at: 2026-06-15T11:59:00Z
      log: .workgraph/runs/build-core-1718456340.log   # gitignored; full captured output
```

**State machine.**
- **Entry:** `wg_add_node` / `wg_ingest` create a node in **`triage`** with its `gate`, `deps`, and `parent` supplied **inline in the create payload** (`gate.kind` required). The server then recomputes readiness (AC-23) in that same write, so a node whose deps are already terminal-good (e.g. a root node) advances straight to `ready`, locking its gate + edges. `wg_set_gate`/`wg_add_dep` therefore **amend** a node only while it is still `triage` (still waiting on a dep); a node that has reached `ready` is immutable (AC-10). Set gates at creation; amend only pre-readiness.
- **Active (non-terminal):** `triage` → `ready` → `active` → `awaiting-signoff`; plus `blocked` (a holding state, re-enterable).
- **Terminal:** `done` (gate-verified + signed off), `resolved` (a `none`-gate node settled — decision/coordination recorded), `deferred` (consciously postponed/abandoned), `archived` (dropped from the active set, still queryable).
- **Terminal-good (satisfies a dependency):** `done` **and** `resolved` only.

Transitions and the surface that may invoke each (X=execute, P=plan/operator; reads omitted):
| From | To | Trigger | Surface |
|---|---|---|---|
| *(none)* | `triage` | `wg_add_node` / `wg_ingest` (node created) | P |
| `triage` | `ready` | server readiness-recompute: all deps terminal-good (AC-23); **gate + edges lock** | server (each write) |
| `ready` | `active` | `wg_claim` (executor starts) | X |
| `active` | `awaiting-signoff` | `wg_verify` → `command` gate exit 0 | X |
| `active` | `awaiting-signoff` | `wg_request_signoff` → `manual` gate (rationale required, AC-26) | X |
| `awaiting-signoff` | `active` | `wg_reverify` (evidence stale; clears `last_verify`, AC-25) | X / P |
| `awaiting-signoff` | `done` | `wg_signoff` (operator records human ack) | **P only** |
| `ready`/`active` | `resolved` | `wg_resolve` (`none`-gate nodes only; rationale required) | P |
| `active` | `active` | `wg_verify` exit ≠ 0 / timeout → stays active, failure recorded | X |
| any non-terminal | `blocked` | `wg_report_blocked`, or server recompute when a dep becomes non-terminal-good (AC-23) | X / P / server |
| `blocked` | `ready` | `wg_unblock` (re-enters the readiness recompute → `ready` if deps good, else stays `blocked`) | P |
| any non-terminal | `deferred` | `wg_defer` | P |
| any terminal | `archived` | `wg_archive` | P |

Rules: `done` is reachable **only** via `wg_signoff` on the plan/operator surface (FR-5). `none`-gate nodes reach `resolved`, never `done`; `command`/`manual` nodes reach `done`, never `resolved`. A gated `parent` cannot reach `done` while any child is non-terminal-good (AC-20). The **server drives readiness** (AC-23) inside every write op — there is no executor-callable "recompute" tool, and a node never silently sits ready-but-not-flagged. `wg_remove_node` is constrained by AC-24 (triage + no dependents). `wg_unblock` clears the manual block and re-enters the readiness recompute (AC-23), which deterministically routes the node to `ready` if its deps are terminal-good or leaves it `blocked` otherwise — no operator choice between `ready`/`active`.

**Concurrency:** the MCP server is the single writer; in-process writes are serialized. Each mutation runs **load → transition → save**, the server holding the `base_hash` from `load` and passing it to `save` (the pure `transition` never touches it); `save` rejects on on-disk-hash mismatch (AC-18). A double `wg_claim` of the same `ready` node is thus safe by construction: the second writer's `save` fails the hash check, reloads, finds the node `active`, and its re-claim is an illegal transition (AC-19) — no `claimed_by` owner field is needed for correctness (owner attribution is deferred). Atomic temp-rename guarantees no torn file (AC-16/17).

## Interface contracts

- **C-1 — Store interface.** *(as built)* `load(store_root) -> (Graph, base_hash)`; `save(store_root, Graph, base_hash) -> new_hash` (validates schema incl. dup-id, `parent` refs, **and dep cycles**, checks `base_hash` vs on-disk, atomic temp-rename); `write_rationale(store_root, node_id, text) -> path` (creates/updates the tracked `rationale/<id>.md` and sets the node's `rationale` field); `init_store(store_root) -> path` (scaffold an empty store). Errors: `ValidationError(node_id, field)`, `ConcurrencyError`, `IOError`. The YAML schema above is the stable on-disk contract (hand-editable). *(`save`/`write_rationale` thread `store_root` — a refinement of the illustrative signature so the store knows where to write.)*
- **C-2 — Graph/plan interface.** `waves(Graph) -> list[list[id]]` (built from the `graphlib.TopologicalSorter` `get_ready()`/`done()` loop, snapshotting each generation as one wave); `detect_cycle(Graph) -> list[id] | None` (via `prepare()` catching `CycleError`; returns the nodes of **one** cycle — graphlib reports a single arbitrary cycle, which satisfies AC-2; there is no stdlib `find_cycle`); `ready_nodes(Graph) -> list[id]`; `rollup(Graph, parent_id|None) -> dict[status, count]`. Pure, no I/O.
- **C-3 — Lifecycle interface.** *(as built)* `transition(Graph, node_id, action, surface, **args) -> Graph` over actions `{claim, pass_gate, request_signoff, reverify, signoff, resolve, block, unblock, defer, archive, set_gate, add_dep, remove}`; plus `add_node(Graph, node, surface) -> Graph` (creation) and `recompute_readiness(Graph) -> Graph` (the AC-23 driver, run after every mutation). Validates: action legal from current status; `surface` may invoke it (table above); gate/dep/parent mutations only while `triage`. Raises `IllegalTransition(node_id, current, allowed)` or `SurfaceDenied(action, surface)`. Pure over the Graph (no store I/O, no `base_hash`).
- **C-4 — Gate-runner interface.** *(as built)* `run_gate(command, cwd, timeout, env=None, runs_dir=None) -> GateResult{exit_code, output, duration_s, log_path}`. Runs `command` via subprocess in its **own process group** (session), inheriting `env` (`None` → the server's environment, so a `mise`-activated launch puts `uv`/python on PATH; see Constraints); on timeout, **kills the whole process group** and sets `exit_code = -1` (treated as failure); captures stdout+stderr to a uniquely-named log in `runs_dir`; `output` is the trailing-4 KB evidence view.
- **C-5 — MCP tool contract.** Tools, grouped by surface (group membership is what the consumer's agent allowlist enforces — see Operational guardrails):
  - **Read:** `wg_plan`, `wg_status`, `wg_show`, `wg_ready`.
  - **Execute:** `wg_claim`, `wg_verify`, `wg_request_signoff`, `wg_reverify`, `wg_report_blocked`.
  - **Plan/operator:** `wg_ingest`, `wg_add_node`, `wg_set_gate`, `wg_add_dep`, `wg_remove_node`, `wg_signoff`, `wg_resolve`, `wg_defer`, `wg_unblock`, `wg_archive`.
  Each tool composes the C-1…C-4 ops (typically `load` → graph-op / `transition` → `write_rationale` when it carries prose → `save`) and returns only the requested scope (NFR-4). **Error envelope:** every tool maps the C-1/C-3 exceptions to a structured error result — `ConcurrencyError` → a "reload and retry" instruction (AC-18); `IllegalTransition` → current status + allowed transitions (AC-9/AC-19); `ValidationError`/`SurfaceDenied` → the offending node/field/surface.

## Task graph

### WU-1: Scaffold project + module skeleton
- kind: build
- depends-on: []
- role: backend-dev
- produces: [C-1, C-2, C-3, C-4, C-5]   # signatures/stubs only; bodies in later units
- consumes: []
- acceptance: []   # structural; verified by WU-8 wiring
- milestone: M-1
- summary: Per the `wsl-new-project` Python recipe: `mise use python@3.13`, `uv` project, committed `pyproject.toml` + `uv.lock` (pin the `mcp` SDK version), pytest harness. Lay down the full package skeleton (`models, store, graph, lifecycle, gate, mcp_server, cli`) with the C-1…C-5 signatures as stubs, plus `.workgraph/` example store with a `runs/.gitignore`. First unit pins every module boundary so later units edit only within their own file.

### WU-2: Store layer (C-1)
- kind: build
- depends-on: [WU-1]
- role: backend-dev
- produces: [C-1]
- consumes: []
- acceptance: [AC-3, AC-16, AC-17, AC-18, NFR-3, NFR-4]
- milestone: M-1
- summary: YAML load/save of the node schema; schema validation (unknown dep id, **duplicate id**, **`parent` exists / non-self / acyclic**, bad `gate.kind`, missing fields); atomic write (temp+rename, stable key order, minimal diff); optimistic-concurrency base-hash check; `write_rationale`; fail-closed load on unparseable file.

### WU-3: Graph engine (C-2)
- kind: build
- depends-on: [WU-1]
- role: backend-dev
- produces: [C-2]
- consumes: []                  # pure functions over the model from WU-1's skeleton; no store I/O
- acceptance: [AC-1, AC-2, AC-15, AC-20, AC-22, NFR-1, NFR-2]
- milestone: M-1
- summary: `graphlib.TopologicalSorter` wave generation (snapshot each `get_ready()` generation as a wave); `detect_cycle` via `prepare()`/`CycleError` (incl. self-dep; returns one cycle); readiness computation (terminal-good = `done`|`resolved`; abandoned-dep → `blocked`); parent rollup counts; empty-graph → empty waves/rollup. Pure — no load/save.

### WU-4: Lifecycle state machine + surface model (C-3)
- kind: build
- depends-on: [WU-3]
- role: backend-dev
- produces: [C-3]
- consumes: [C-2]               # pure over the Graph (uses C-2 readiness/rollup); no store I/O
- acceptance: [AC-8, AC-9, AC-10, AC-12, AC-13, AC-14, AC-15, AC-19, AC-20, AC-23, AC-24, AC-25, AC-26]
- milestone: M-1
- summary: `transition(Graph,…) -> Graph` — encode the transition table; enforce surface capability (X/P), gate/dep/parent-immutability-after-triage, the `done`-only-via-signoff clamp, the `deferred`↔`done` / `resolved`↔`done` exclusions, the readiness recompute (AC-23), the `wg_remove_node` constraint (AC-24), the `wg_reverify` re-open (AC-25), and the manual-gate rationale requirement (AC-26). No load/save — the MCP server wraps store I/O around this.

### WU-5: Gate runner (C-4)
- kind: build
- depends-on: [WU-1]
- role: backend-dev
- produces: [C-4]
- consumes: []
- acceptance: [AC-6, AC-7, NFR-5]
- milestone: M-1
- summary: Subprocess execution in `working_dir`, **own process group**, inheriting the server env; timeout → kill the whole process group, `exit_code = -1` (failure); capture stdout+stderr to a gitignored run log; truncate to trailing 4 KB for the evidence surface. No shell elevation.

### WU-6: MCP server + surface partition (C-5)
- kind: build
- depends-on: [WU-2, WU-3, WU-4, WU-5]
- role: backend-dev
- produces: [C-5]
- consumes: [C-1, C-2, C-3, C-4]
- acceptance: [AC-1, AC-4, AC-5, AC-6, AC-7, AC-8, AC-9, AC-11, AC-12, AC-21, AC-22, AC-23, NFR-4, NFR-5]
- milestone: M-2
- summary: Official `mcp` SDK server over stdio; define the read/execute/plan tool groups (C-5) incl. `wg_ingest`/`wg_reverify`; wire `load → transition → save` holding `base_hash`, drive the per-write readiness recompute (AC-23), and the structured error envelope; each tool returns only requested scope; expose a machine-readable group manifest for the AC-11 subset test; document the surface-split usage contract in the README — the executor `tools:` allowlist and `permissions.deny` snippets (D-10 / Operational guardrails).

### WU-7: Minimal CLI (init / serve)
- kind: build
- depends-on: [WU-2, WU-6]
- role: backend-dev
- produces: []
- consumes: [C-1, C-5]
- acceptance: [AC-27]
- milestone: M-2
- summary: `workgraph init` scaffolds `.workgraph/` with a valid empty `graph.yaml` + `runs/.gitignore` (**AC-27:** the result loads cleanly via C-1); `workgraph serve` launches the MCP server over stdio, documenting that it must run in a mise-activated context (C-4 env inheritance). No CRUD CLI (D-2).

### WU-8: Integration / e2e + adversarial-path tests
- kind: build
- depends-on: [WU-6, WU-7]
- role: backend-dev
- produces: []
- consumes: [C-5]
- acceptance: [AC-1, AC-2, AC-10, AC-11, AC-12, AC-13, AC-14, AC-21, AC-24, NFR-1, NFR-3]
- milestone: M-3
- summary: Drive a fresh store through the full happy path (`wg_ingest` a DAG → `wg_plan` waves → `wg_claim` → `wg_verify` exit 0 → `wg_signoff` → `done`; `wg_status` rollup) **and** the adversarial path (executor surface cannot set a gate / sign off / reach `done`; `wg_set_gate` on an active node rejected; cycle rejected; `wg_remove_node` rejected for a node with dependents; gate exit ≠ 0 holds at `active`). Perf check at 1,000 nodes (NFR-1).

## Milestones & alpha definition

- **Alpha = a fresh store can be driven end-to-end through both the happy path and the adversarial path entirely via MCP tools, with AC-1, AC-2, AC-4–AC-14, AC-21, AC-23, NFR-1, NFR-3, NFR-4 passing.** Concretely: `wg_ingest` ≥3 nodes with deps → `wg_plan` returns correct waves; a `command`-gate node goes `claim → verify(exit 0) → awaiting-signoff → signoff → done`; an execute-surface caller is structurally unable to set a gate, sign off, or reach `done`; gates immutable after `triage`; cycles rejected; `wg_status` answers "where are we" as a rollup without dumping the store; every transition is a `graph.yaml` git diff. A thin end-to-end vertical, not any one component gold-plated.
- **M-1 — Core engine:** WU-1…WU-5. Store + graph + lifecycle + gate runner working as a unit-tested library (no surface).
- **M-2 — MCP surface:** WU-6, WU-7. The two headline ops + full transition tool set over MCP with the surface split; minimal CLI.
- **M-3 — Alpha:** WU-8. End-to-end happy + adversarial paths green; perf threshold met.

## Autonomy envelope

### Decision log
- **D-1 — Stack: Python 3.13 + stdlib `graphlib` core, MCP via the official `mcp` SDK; mise-pinned, `uv`.** *Rationale:* topo sort + concurrent waves come from `graphlib`'s `get_ready()`/`done()` loop (you snapshot each generation — a few lines, not a built-in `waves()`); matches house WSL rules. *Rejected:* TS/Node + `graphology-dag` (no free-graphlib win). *(Ratified — handoff Q1.)*
- **D-2 — MCP-first; no CRUD CLI in MVP.** Minimal CLI only (`init`, `serve`). **Human sign-off is performed by the coordinator agent via the operator MCP tool, on the human's instruction**, after evidence is surfaced. *Rationale:* the consumer is an AI coordinator; sign-off needn't be a CLI. *Rejected:* CLI-first, both-surfaces-equally. *(Ratified — handoff Q2; safe only in combination with D-3, see R-1.)*
- **D-3 — Gate-authorship governance = surface split + gate immutability after `triage`.** A plan/operator surface (create nodes, set/modify gates, edit deps, sign off) is separate from an execute surface (claim, verify, request-signoff, reverify, report-blocked). Gates/edges immutable once a node leaves `triage`; `done` reachable only via `wg_signoff` on the operator surface. *Rationale:* mirrors the meeting-chair token clamp; kills the `command: "true"` self-gating hole. *Rejected:* operator-token gating (more friction), convention-only immutability (no in-tool clamp). Caller-side enforcement is a verified harness feature (D-10) — a real boundary **once the consumer applies the config** (R-1); enforcement is harness-side, not server-side. The in-tool clamps workgraph itself owns are AC-10 and AC-12. *(Ratified — handoff Q3.)*
- **D-10 — The surface split is enforced at the harness's per-tool MCP permission layer (verified against the Claude Code docs, 2026-06).** A subagent's `tools:` allowlist *"inherits all tools if omitted"* and can be restricted to a named subset; MCP permission rules support individual-tool granularity (`mcp__workgraph__wg_signoff`), prefix globs (`mcp__workgraph__wg_*`), and whole-server (`mcp__workgraph`); `deny` is evaluated before `allow`, a **bare-name deny removes the tool from the agent's context entirely**, and **managed-settings** deny rules cannot be overridden. *Rationale:* the original load-bearing assumption (handoff §Open-questions) is confirmed true — so a properly-configured executor *cannot see* `wg_set_gate`/`wg_signoff`. The "cannot see" guarantee is cleanest on the `permissions.deny` (bare-name) path, so the README leads with it. Enforcement is harness-side and **consumer-applied** (R-1), not an intrinsic property of workgraph. *Rejected fallbacks (unneeded):* two separate MCP servers, operator-token gating. *Sources:* `code.claude.com/docs/en/sub-agents`, `code.claude.com/docs/en/permissions`.
- **D-4 — Name stays `workgraph`** (project, package, CLI, MCP server). No short `wg` alias. *Rationale:* `wg`/`graphwork` taken and `wg` collides with WireGuard; the full word is clear; the directory already matches. *Rejected:* rename. *(Ratified — handoff Q4.)*
- **D-5 — Storage = single git-tracked YAML** (`.workgraph/graph.yaml`); git history is the transition log; rationale in tracked markdown; gate output in gitignored `runs/`. *Rationale:* one source of truth, drift impossible by construction, every transition a reviewable diff, hand-editable. *Rejected:* SQLite (documented escape-hatch behind C-1), TOML (worse for nested dep lists), inline prose/output (breaks NFR-4), `events.jsonl` (deferred — git gives history now). *(Ratified — handoff §7/§8.)*
- **D-6 — Lifecycle finalized** (formalizing the handoff's "provisional"): active {`triage`, `ready`, `active`, `awaiting-signoff`, `blocked`}; terminal {`done`, `resolved`, `deferred`, `archived`}; terminal-good = {`done`, `resolved`}. **`resolved`** is the terminal for `none`-gate (decision/coordination) nodes — satisfying the handoff's "they reach a terminal non-done state." *Rationale:* keeps `done` meaning *gate-verified work* everywhere; a settled decision is not "built," so it is `resolved`, not `done`, and not `deferred`. *Rejected:* collapsing decisions into `done` (reintroduces self-judged doneness) or `deferred` (semantically wrong).
- **D-7 — `done` requires gate evidence (command exit 0 and/or manual sign-off) AND an operator-surface ack.** Executors have no transition into `done`. Gated parents reach `done` only when all children are terminal-good. *Rationale:* the only-a-lead-marks-done principle, made structural.
- **D-8 — Gate runner is caller-driven** (runs on `wg_verify`), cwd = store `working_dir`, default timeout 120 s, output captured to a gitignored run log and summarized in the node — never inlined into `graph.yaml`. *Rationale:* compactness (NFR-4) + determinism on an explicit verify call.
- **D-9 — Concurrency = optimistic (base-hash check) + atomic temp-rename; single-server assumption.** *Rationale:* the meta-build write profile doesn't need CAS claims. *Rejected:* mandatory lockfile, SQLite CAS (overkill at meta scale).
- **D-11 — `parent` is membership + a derived rollup view, not a second dependency axis.** A parent orders nothing (only `deps` do); its rollup is computed from children at query time. A parent may carry its own gate, in which case it runs the normal lifecycle gated additionally on children-terminal-good (AC-20). *Rationale:* matches the handoff (`parent` = membership; rollup = a view); avoids a parent-vs-deps cycle axis and a separate parent state machine. *Rejected:* parents as dependencies (a second cycle surface), parents with a bespoke auto-done terminal (re-introduces self-judged doneness for the parent). *(Folded from adversarial review — completeness lens.)*
- **D-12 — Graph declaration is an atomic batch op (`wg_ingest`) with intra-batch forward refs.** `wg_add_node` remains for incremental adds. *Rationale:* headline op (a) is "ingest nodes+deps → plan"; a single-node-add-only surface with strict unknown-dep rejection makes declaring a connected DAG order-dependent and clumsy. *Rejected:* add-one-at-a-time-only (forces reverse-topo authoring), non-atomic batch (partial graphs). *(Folded from adversarial review — completeness/task-graph lenses.)*
- **D-13 — The NFR thresholds (500 ms, 1,000 nodes, fan-out ≈3, 120 s timeout, 4 KB output cap) are formalizer-set proceed-on targets, not handoff-mandated.** *Rationale:* the handoff specifies none; these operationalize "instant status" and "bounded evidence" at meta-build scale. *Adjust freely* if the build shows them wrong — targets, not contracts. *(Folded from adversarial review — invented-decision lens; records the numbers as conscious assumptions rather than silent guesses.)*

### Assumptions (proceed-on)
- The consuming orchestrator grants **wave/executor agents only the read + execute tool groups**, and the **coordinator/operator agent the plan group**, using the harness's per-tool MCP restriction (D-10) — e.g. an executor subagent whose `tools:` lists only `mcp__workgraph__{wg_plan,wg_status,wg_show,wg_ready,wg_claim,wg_verify,wg_request_signoff,wg_reverify,wg_report_blocked}`, or (preferred, for the "cannot see" guarantee) a `permissions.deny` of `mcp__workgraph__wg_signoff`, `mcp__workgraph__wg_set_gate`, etc. The tool exposes cleanly separable, distinctly-named tools; applying the restriction is the consumer's config step. Verified enforceable (D-10); the in-tool gate-immutability rule (AC-10) is defense-in-depth.
- The store lives inside a git repo and the operator commits transitions (the tool writes the file; committing is the operator's habit/automation — a `--commit` convenience is a possible later add, not MVP).
- Gate commands are authored to be deterministic enough that exit 0 is meaningful evidence (the planner's responsibility at gate-authoring time).
- `mise`, `uv`, and the official Python `mcp` SDK are available on the box; no project secrets are required, so the `op://` path is N/A here.
- Minimal CLI is `init` + `serve` only; a fuller CLI is deferred, not part of alpha.

### Human-decision gates (stop & escalate)
- *(Resolved — was the one load-bearing gate.)* The surface split's dependence on the harness restricting an agent to a **subset** of an MCP server's tools is **verified true** against the Claude Code docs (D-10). No fallback needed; WU-6/WU-8 still assert it concretely (AC-11). The residual is purely that the consumer must apply the config (R-1).
- All four handoff forks are resolved (D-1…D-4); no open blocker remains. Low-stakes defaults (D-13 thresholds, final tool names, whether to auto-`block` after N consecutive gate failures) are proceed-on; surface them in the build if one conflicts, otherwise take the documented default.

## Operational guardrails
- **Surface enforcement (D-10):** the wave/executor subagent is granted only the read+execute tools — either a `tools:` allowlist naming exactly `mcp__workgraph__{wg_plan,wg_status,wg_show,wg_ready,wg_claim,wg_verify,wg_request_signoff,wg_reverify,wg_report_blocked}`, or (preferred) a `permissions.deny` of the plan-group tools (`mcp__workgraph__wg_ingest`, `wg_set_gate`, `wg_add_dep`, `wg_add_node`, `wg_remove_node`, `wg_signoff`, `wg_resolve`, `wg_defer`, `wg_unblock`, `wg_archive`). Deny is evaluated before allow and a bare-name deny removes the tool from the agent's context entirely; the hardest guarantee is a **managed-settings** deny (non-overridable). The coordinator/operator agent keeps the full set. The README ships these copy-pasteable snippets, authored with the MCP surface (WU-6).
- **Budget / turn caps:** N/A internally — the tool runs no agents. The only thing it executes is the gate command, bounded by the configured **timeout** (default 120 s); a runaway gate (and its child process group) is killed at timeout and recorded as a failure. The consuming orchestrator's own per-agent caps apply to its fleet, not here.
- **Identity & secrets:** gate commands run under the **operator's own OS user**, in `working_dir`, with no elevation (NFR-5). The tool holds no secrets; if a gate command needs credentials, that is the operator's `op run` responsibility, outside tool scope. MCP transport is **stdio only** — no bound network port.
- **Loop guard:** `wg_verify` is caller-driven and never auto-retries, so the tool cannot loop. A node stuck failing its gate is surfaced via `wg_status`; the orchestrator decides. Optional hardening (proceed-on default = off): auto-transition to `blocked` after N consecutive failed verifies.

## Risk register & spikes
- **R-1 — The caller-side surface split is a config step the consumer must apply.** *Enforceability is verified* (D-10) — the residual risk is only that an operator forgets to restrict an executor agent's tool set. *Likelihood: low / Impact: high.* *Mitigation:* ship a documented usage contract + copy-pasteable executor `tools:`/`permissions.deny` block; keep the in-tool gate-immutability (AC-10) and `done`-only-via-operator-surface (AC-12) clamps as defense-in-depth; WU-8's manifest test (AC-11) asserts the groups are cleanly separable. Strongest deployment uses **managed-settings** deny rules (non-overridable).
- **R-2 — Arbitrary shell in gate commands = code execution.** *Likelihood: low / Impact: high.* *Mitigation:* runs under the operator's identity with no elevation (NFR-5); gates are authored at plan time by the operator/coordinator (D-3), never by executors; trust boundary documented.
- **R-3 — Concurrent writers** (a second server instance or a human hand-edit) could clobber. *Likelihood: low / Impact: med.* *Mitigation:* optimistic base-hash check + atomic temp-rename (AC-16/18); single-server assumption documented (D-9).
- **R-4 — Coordinator records a sign-off the human never gave** (hallucinated ack). *Likelihood: low / Impact: med.* *Mitigation:* sign-off stamps `who` + is a reviewable git diff the human can audit; evidence is surfaced so the real ack is cheap; the operator surface is held only by the human's direct delegate, not the fleet. Ratified by Parker (D-2).
- **R-5 — LOC underestimate.** The handoff's "~500 LOC" is light given the full lifecycle, surface split, and the mutation-integrity/validation rules folded from review. *Likelihood: high / Impact: low.* *Mitigation:* alpha is a thin vertical; `resolved`/decision niceties, `wg_reverify`, parent-own-gates, and the optional failure-auto-block can be trimmed without touching the alpha path.
- **R-6 — `manual`-gate evidence is weaker than `command`** (a human vouch, not a reproducible exit 0). *Likelihood: low / Impact: med.* *Mitigation:* `manual` is opt-in and *visibly* un-automated (the point — a human vouches); the vouch is the operator's `wg_signoff` stamp (P-only, AC-12/AC-26) recorded as a git diff, which executors can never produce; prefer `command` gates wherever a deterministic test exists.
- No `spike` units: `graphlib` waves and the `mcp` SDK are stdlib/official and well-understood; the governance model is design-settled and its harness enforcement verified (D-10). No remaining unknown warrants a code spike.

## Test / verification strategy
- **Unit (WU-2…WU-5):** store round-trip + validation (unknown dep, dup id, `parent` refs) + atomicity + minimal-diff + concurrency + rationale write (AC-3, AC-16–18, NFR-3/4); graph waves / cycle (incl. self-dep) / readiness / rollup / empty-graph (AC-1, AC-2, AC-15, AC-20, AC-22, NFR-1/2); lifecycle transition table incl. surface denial, gate immutability, readiness recompute, remove-constraint, reverify, manual-rationale, `deferred`/`resolved`/`done` exclusions (AC-8–10, AC-12–14, AC-19, AC-20, AC-23–26); gate runner exit-0 / non-zero / timeout + process-group kill + env inheritance + log capture (AC-6, AC-7, NFR-5).
- **Surface / status tests (WU-6):** `wg_status` rollup (correct counts; no body leakage) and per-node payload over parent + leaf fixtures (AC-4, AC-5); `wg_ingest` atomic batch with forward refs (AC-21); empty-store responses (AC-22); server-driven readiness on a tool call (AC-23); the error envelope (ConcurrencyError/IllegalTransition mapping, AC-9/AC-18/AC-19); the manifest test that the execute-surface tool set is a strict subset excluding create/set-gate/add-dep/signoff (AC-11, AC-12).
- **CLI (WU-7):** `workgraph init` produces a store that loads cleanly (AC-27).
- **Integration / e2e (WU-8):** full happy path and adversarial path over the real MCP tools (AC-1, AC-2, AC-10–14, AC-21); `wg_remove_node` rejected for a node with dependents (AC-24); perf at 1,000 nodes (NFR-1); forced-kill durability (NFR-3).
- **Review gate before a unit is `done`:** all its `acceptance` IDs have a passing test, and the executor-surface restriction (R-1) is asserted (AC-11). Every AC (AC-1…AC-27) and NFR (NFR-1…5) above is referenced by at least one work unit *and* covered by a test class here.

## Constraints
- Code under `~/projects/workgraph` (Linux fs), **never** `/mnt/*`. Runtime via **mise** (`python@3.13`), deps via **uv**; commit `mise.toml`, `pyproject.toml`, `uv.lock` (pin the `mcp` SDK). No `apt`-installed Python.
- `workgraph serve` must be launched from the project's **mise-activated** context so the gate subprocess inherits `uv`/python on PATH (C-4 passes the server env to the gate); document this in the README.
- Global, project-agnostic capability — **not** wired into `gamedev-studio/` or any project runtime. A future global Claude Code skill may wrap it.
- No driver/GPU/secret concerns (none used). Lean output: no commercial/legal ceremony.
- **Cost caution (from the handoff):** any research/build dispatch here uses the smallest-capability agent; if a `general-purpose` agent is used, forbid further delegation and cap agent count + scope. This is a single focused build, not a fleet.

---
*Adversarial-review changelog (folded findings):* added `wg_ingest` atomic batch declaration + intra-batch forward refs (D-12, AC-21); `triage` as the explicit entry status and the **server-driven readiness recompute** (AC-23); `wg_remove_node` constraint (AC-24) and `parent` validation/semantics (D-11, AC-3/AC-20); `wg_reverify` stale-evidence path (AC-25); manual-gate rationale requirement (AC-26); base_hash threading + error envelope + `write_rationale` + `detect_cycle` correction in the contracts (C-1/C-2/C-3/C-5); gate process-group kill + env inheritance (C-4, Constraints); duplicate-id/empty-graph/self-dep handling (AC-2/AC-3/AC-22); deterministic 4 KB output truncation (AC-7) and NFR-1 operating conditions; AC-4/AC-5 added to the test strategy (were orphaned); NFR numbers recorded as proceed-on targets (D-13); D-3/D-10 boundary-strength wording corrected to "consumer-applied." Refuted/no-change: double-claim safe by construction (optimistic concurrency); `resolved` already grounded (D-6); DAG + consumes/produces clean; external facts (graphlib waves, `mcp`/stdio, all D-10 permission clauses) verified to hold.
*Post-fold insurance pass (delta consistency):* fixed a governance contradiction where AC-26 required operator-authored manual-gate evidence but `wg_request_signoff` is an execute tool → manual-gate evidence is now the operator `wg_signoff` stamp (AC-26/R-6); extended the AC-23 readiness recompute to cover `blocked → ready` and de-ambiguated `wg_unblock`; made gate/deps inline-at-creation explicit so the auto-advance lock (AC-23) doesn't strand `wg_set_gate` on root nodes. Dangling-ref and state-reachability checks: clean.

*Build changelog (as built, alpha = M-3 met):* implemented test-first across WU-1…WU-8 — modules `models, store (C-1), graph (C-2), lifecycle (C-3), gate (C-4), service, mcp_server (C-5), cli`. A `Service(store_root)` layer holds the operation logic (compose `load → transition/graph-op/gate → write_rationale → save`, drive the AC-23 recompute, stamp timestamps) and the low-level `mcp` server is a thin stdio binding over `tool_handlers(service)` + `error_envelope`. Signature refinements recorded inline above (C-1 `save(store_root, …)`, C-4 `runs_dir`, C-3 action vocabulary + `add_node`/`recompute_readiness`). **108 tests pass** (store 18, graph 13, lifecycle 34, gate 7, service 14, mcp 6, cli 3, e2e 13). Alpha-gate ACs all covered by tests: AC-1, AC-2, AC-4–AC-14, AC-17, AC-21, AC-23, NFR-1/3/4.

*In-harness fix (found running the human-in-the-loop test inside Claude Code):* the MCP tools advertised a property-less `inputSchema`, so the harness string-encoded array/object args (`wg_ingest` `nodes` arrived as a string, iterated char-by-char). Added `tool_schemas()` with typed properties per tool and wired it into `list_tools`; the unit suite asserts the structured-arg tools are typed and the live test asserts it over the wire (now 111 tests). The in-process ClientSession smoke test couldn't catch this because it hand-feeds structured args, bypassing the model's schema-driven encoding — only a real in-harness run exercises that path.
