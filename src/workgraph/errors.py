"""Typed errors raised across the contracts (C-1, C-3) and mapped to the MCP error envelope (C-5)."""

from __future__ import annotations


class WorkgraphError(Exception):
    """Base for all workgraph errors."""


class ValidationError(WorkgraphError):
    """Schema/declaration invalid (C-1). Names the offending node and field (AC-3)."""

    def __init__(self, node_id: str | None, field: str, message: str | None = None):
        self.node_id = node_id
        self.field = field
        super().__init__(message or f"invalid {field!r} on node {node_id!r}")


class ConcurrencyError(WorkgraphError):
    """On-disk base hash no longer matches the hash read at load (AC-18)."""


class IllegalTransition(WorkgraphError):
    """Action not legal from the node's current status (AC-19). Carries the allowed set."""

    def __init__(self, node_id: str, current: str, allowed: list[str]):
        self.node_id = node_id
        self.current = current
        self.allowed = allowed
        super().__init__(
            f"node {node_id!r} is {current!r}; cannot apply this action "
            f"(allowed transitions: {', '.join(allowed) or 'none'})"
        )


class SurfaceDenied(WorkgraphError):
    """The calling surface (execute/plan) may not invoke this action (FR-5 / D-3)."""

    def __init__(self, action: str, surface: str):
        self.action = action
        self.surface = surface
        super().__init__(f"surface {surface!r} may not invoke {action!r}")
