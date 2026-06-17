"""Minimal CLI (D-2): `workgraph init` (scaffold a store) and `workgraph serve` (run the MCP server).

`serve` must be launched from a `mise`-activated context so gate commands inherit `uv`/python on
PATH (the gate subprocess inherits the server env — see SPEC Constraints / C-4).
"""

from __future__ import annotations

import argparse

from . import store


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="workgraph", description="deterministic work-unit graph")
    sub = parser.add_subparsers(dest="cmd")

    p_init = sub.add_parser("init", help="scaffold a .workgraph/ store")
    p_init.add_argument("path", nargs="?", default=".", help="store root (default: cwd)")

    p_serve = sub.add_parser("serve", help="launch the MCP server over stdio")
    p_serve.add_argument("path", nargs="?", default=".", help="store root (default: cwd)")

    p_mermaid = sub.add_parser("mermaid", help="print the graph (or a slice) as mermaid text")
    p_mermaid.add_argument("path", nargs="?", default=".", help="store root (default: cwd)")
    p_mermaid.add_argument("--direction", default="TD", help="TD or LR (default TD)")
    p_mermaid.add_argument("--parent", help="slice: a parent id and its children")
    p_mermaid.add_argument("--status", help="slice: nodes in this state, or several comma-separated (e.g. active,ready,blocked)")
    p_mermaid.add_argument("--node", help="slice: center node for a neighborhood view")
    p_mermaid.add_argument("--depth", type=int, default=1, help="neighborhood radius for --node")

    args = parser.parse_args(argv)

    if args.cmd == "init":
        path = store.init_store(args.path)
        print(f"initialized workgraph store at {path}")
        return 0

    if args.cmd == "serve":
        import asyncio

        from . import mcp_server

        asyncio.run(mcp_server.serve(args.path))
        return 0

    if args.cmd == "mermaid":
        from .service import Service

        out = Service(args.path).mermaid(
            direction=args.direction,
            parent=args.parent,
            status=args.status,
            node=args.node,
            depth=args.depth,
        )
        print(out["mermaid"], end="")
        return 0

    parser.print_help()
    return 1
