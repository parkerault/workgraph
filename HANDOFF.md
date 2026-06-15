# Handoff — "workgraph": a coordinator's deterministic work-graph tool

*Provisional name — `wg`/`graphwork` already exists; pick a distinct one. Provisional home: `~/projects/workgraph/`. Both are trivial to change.*

## For the next session (read first)

You are picking up a **ratified design** to formalize and build. Sequence:

1. Run **`spec-formalizer`** (greenfield-application tier) on this handoff → a shovel-ready spec.
2. Run **`adversarial-review`** on the emitted spec.
3. **Build** it. This is small (~500 LOC + tests), so it is likely a **single focused build session**, not a full orchestrated fleet.

Do **not** relitigate the ratified decisions in §Ratified design. **Do** surface and resolve the items in §Open questions — escalate to Parker, never silently guess (Parker delegates design calls but wants real UX/env/tooling forks raised). Heed §Cost caution.

This is a **global, project-agnostic** capability, kept logically separate from any project runtime — it is **not** a gamedev-studio artifact. Do not wire it into `gamedev-studio/`.

## What it is (one line)

A project-agnostic **CLI + MCP** capability (a future global Claude Code skill) that gives an AI **coordinator** a deterministic work-unit graph: declare nodes with dependencies, get a topologically-ordered plan **including the concurrent waves**, gate each node's completion on a **deterministic command (exit 0) + a human sign-off**, and answer "where are we" as an instant query — so the coordinator stops burning tokens reading an ever-growing prose spine, and can never mistakenly mark deferred work "done."

## Why (the problem)

In multi-session, agent-driven builds, agents coordinate through a growing prose "spine" plus a parallel work-log. Two recurring failures: (1) the artifacts **drift** — a node gets marked "completed" in the spine when the work-log says it was **deferred**; (2) reading the whole spine just to learn status **burns tokens** on slow, fuzzy prose. Root cause: coordinating through prose, and letting the **doer judge its own doneness**. gamedev-studio's *runtime* already solved both for its workers — **board-is-truth (D-3)** and **only-a-lead-marks-done (D-4)**. This tool applies those two principles to the **meta-build process itself** (what `BOOTSTRAP.md` is about).

## Ratified design — do NOT relitigate

1. **Project-agnostic.** Knows nothing about any specific project (no slices/contracts/roles). **Generic mechanism; the project supplies specifics** — the gate is an opaque command, `kind` is a free-form project string.
2. **The gate kills self-judged doneness (the D-4 analog).** A `command`-gated node completes only when a **shell command exits 0** — the agent never judges "is it done"; the runner does. Zero tokens spent deciding doneness.
3. **Human sign-off on top of green evidence (the cheap-verification principle).** Completion = the deterministic gate passes **then** a human acks — but make the ack **cheap** by surfacing the evidence (passing command + output, asserted-absence checks, diff). The doer **cannot** reach `done` itself.
4. **Gate taxonomy:** `command` (exit 0 = pass) · `manual` (operator sign-off, explicitly un-automated so it's *visible* a human vouched) · `none` (for `decision`/`deferral` nodes that have no test — they reach a *terminal non-done* state).
5. **"deferred" is a first-class terminal state, unreachable from "done."** Half the original bug is vocabulary: in prose the only states are vibes. A real lifecycle makes "we deferred X" structurally distinct from "X is finished."
6. **Status lifecycle (provisional — formalizer finalizes):** `triage → ready → active → awaiting-signoff → done`, plus terminal `deferred`, `blocked`, `archived`. `ready` = all deps terminal-good. A `command` node: `active → (runner exit 0) → awaiting-signoff → (human ack) → done`; gate failure keeps it `active`/`blocked`.
7. **Storage = ONE git-tracked, human-readable state file (YAML or TOML).** Git history **is** the work-log/transition log → one source of truth, drift impossible by construction, every transition a reviewable diff, operator can hand-edit. **NO SQLite** — the meta-build write profile (coordinator is primary writer; wave-agents mostly read) doesn't need CAS claims; SQLite stays the documented escape-hatch behind the accessor interface. A Temporal-style append-only `events.jsonl` is a **deferred** upgrade (YAGNI — git commits give transition history now).
8. **Rationale is split out, linked by ID.** Per-node prose lives in separate markdown files the state file points to — **never inline**. This is the state-vs-prose split that makes "break up the monolithic spine" real: the state file stays compact (ids/status/edges/gates), and nobody reads it for status (they query the accessor).
9. **Data model = flat, single node type** + two fields: **`kind`** (project-supplied string — `milestone`/`epic`/`unit`/`decision`/… — enabling the coarse "project-level" view + child-status rollup) and **`parent`** (membership). **Not** two tiers. Lifetime (durable vs ephemeral) is captured by status (`archived` drops from the active set, stays queryable).
10. **Topo + concurrent waves come free from stdlib** — Python `graphlib.TopologicalSorter` (its `get_ready()`/`done()` loop *is* the wave scheduler) or JS `graphology-dag` `topologicalGenerations()`. This is **not** where design effort goes; the value is the gate + the status vocabulary.
11. **Access = a thin CLI and/or MCP server that returns only what's asked** (cheap status; the agent never reads the raw store). Two headline operations: **(a)** ingest nodes+deps → return the orchestration plan (ordered concurrent waves); **(b)** "where are we" → status, by project-level rollup or per-node.

Node shape (illustrative, not final):

```
id:        <stable kebab id>
kind:      <project string>        # milestone | epic | unit | decision | ...
parent:    <id?>                   # membership → enables project-level rollup
deps:      [<id>...]               # depends_on edges
status:    ready                   # see lifecycle
gate:      { kind: command|manual|none, command: "<shell; exit 0 = pass>" }
rationale: <path to .md>           # prose linked, never inline
signoff:   <{who, when}?>          # stamped at done
```

## Reference designs to STUDY, not adopt

- **Dagu** — single Go binary, local-first, built-in MCP server, shell-command steps + a native `approval:` gate. The **existence proof** that command-gate + human-gate + MCP fit in one lean tool. We build (not adopt) because it's an *executor* (it runs the steps) and we want a *tracker* with a richer node schema.
- **jpicklyk/task-orchestrator** (MCP) — **server-enforced** dependency gating: it *denies the transition at the tool layer* if deps aren't satisfied. That "deny, don't trust" enforcement is the determinism we want against the marked-done-when-deferred bug.
- **wg / graphwork** (`.wg/graph.jsonl`) and **Backlog.md / taskmd** (markdown-in-git) — existence proofs of the git-diffable storage model.
- Borrowed idea (deferred): **Temporal's append-only event log** as the eventual transition-log upgrade.

## Open questions — formalizer MUST resolve (don't silently guess)

- **Stack:** Python (graphlib free; mise-pinned, `uv`) vs TS/Node (if MCP-first ergonomics dominate). *Lean: Python core + graphlib, thin MCP wrapper — confirm with Parker.*
- **Accessor surface:** CLI-first, MCP-first, or both? (MVP could be CLI; MCP is the agent-ergonomic surface.)
- **Gate-authorship governance — the load-bearing adversarial hole.** The gate command must be authored at **plan time** (planner/operator), **not self-set by the executing agent** — else the agent games its own gate (`command: "true"`). Mirror D-4 / the meeting-chair token clamp. Pin **who may create nodes and set/modify gates.**
- **Gate-runner execution model:** where/when the command runs (on a `verify`/transition call), working directory, timeout, and how output is captured for the human-evidence surface.
- **Sign-off mechanism:** how a human acks (CLI command / MCP tool / file edit) and how it is recorded + surfaced.
- **Concurrency / write strategy:** single-writer coordinator vs lockfile if wave-agents update concurrently (low risk at meta scale — but name it).
- **Validation:** cycle detection + behavior on malformed declarations.
- **Final name** (provisional `workgraph`; `wg`/`graphwork` exists).

## Onward routing

`spec-formalizer` (greenfield-application tier) → `adversarial-review` of the emitted spec → build. Likely a **single focused build session** (~500 LOC + tests), not a full fleet. Scaffold per the `wsl-new-project` recipe (mise-pinned runtime, lockfile, secrets-as-`op://` if any).

## Cost caution

This effort began with a research dispatch that **fanned out to ~130 agents and burned several million tokens** (see the `subagent-fanout-blowup` memory). For any research/build dispatch here: prefer the **smallest-capability** agent — do web work yourself, or use read-only / tool-restricted agents that cannot recursively spawn. If you must use a `general-purpose` agent, **explicitly forbid further delegation** and cap agent count + scope.
