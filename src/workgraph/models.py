"""The shared data model — the YAML schema as dataclasses (SPEC §Data & state model).

This is the substrate every other module operates on. Behaviour (parsing, validation,
transitions, waves) lives in store/graph/lifecycle and is built test-first; this file is
just the shape.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Status(str, Enum):
    TRIAGE = "triage"
    READY = "ready"
    ACTIVE = "active"
    AWAITING_SIGNOFF = "awaiting-signoff"
    BLOCKED = "blocked"
    DONE = "done"
    RESOLVED = "resolved"
    DEFERRED = "deferred"
    ARCHIVED = "archived"


#: Statuses a node never leaves (D-6).
TERMINAL = frozenset({Status.DONE, Status.RESOLVED, Status.DEFERRED, Status.ARCHIVED})
#: Statuses that satisfy a dependency (D-6).
TERMINAL_GOOD = frozenset({Status.DONE, Status.RESOLVED})
#: Active (non-terminal) statuses.
NON_TERMINAL = frozenset(
    {Status.TRIAGE, Status.READY, Status.ACTIVE, Status.AWAITING_SIGNOFF, Status.BLOCKED}
)


class GateKind(str, Enum):
    COMMAND = "command"
    MANUAL = "manual"
    NONE = "none"


@dataclass
class Gate:
    kind: GateKind
    command: str | None = None  # required iff kind == command
    timeout: int | None = None  # seconds; None → config default (D-8/D-13)


@dataclass
class Signoff:
    who: str
    at: str  # ISO-8601 timestamp
    note: str | None = None


@dataclass
class LastVerify:
    exit_code: int
    ran_at: str  # ISO-8601 timestamp
    log: str | None = None  # path to gitignored run log


@dataclass
class Node:
    id: str
    gate: Gate
    kind: str = "unit"  # free-form project string
    parent: str | None = None  # membership only — NOT a dependency (D-11)
    deps: list[str] = field(default_factory=list)
    status: Status = Status.TRIAGE  # entry status
    rationale: str | None = None  # path to tracked .md; never inline (NFR-4)
    signoff: Signoff | None = None
    last_verify: LastVerify | None = None


@dataclass
class Graph:
    """In-memory representation of `graph.yaml`. `nodes` is insertion-ordered for stable diffs."""

    version: int = 1
    working_dir: str = "."  # gate-runner cwd, relative to store root
    nodes: dict[str, Node] = field(default_factory=dict)
