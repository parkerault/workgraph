"""Live MCP smoke test — drive the real `workgraph serve` stdio server through the protocol.

Exercises the async glue (`build_server`/`serve`, list_tools/call_tool) that the in-process tests
mark no-cover: a real ClientSession over a real subprocess, full happy path + error envelope.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from workgraph import store


def _payload(result):
    return json.loads(result.content[0].text)


async def _round_trip(root: str) -> dict:
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "workgraph", "serve", root],
        env=dict(os.environ),
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            listed = await session.list_tools()
            names = {t.name for t in listed.tools}
            # The live server must expose exactly the declared tool groups — this catches
            # handler/group drift (tool_handlers vs READ/EXECUTE/PLAN_TOOLS) over the real wire,
            # and isn't brittle to an intentional tool add/remove the way a hardcoded count is.
            from workgraph.mcp_server import EXECUTE_TOOLS, PLAN_TOOLS, READ_TOOLS

            assert names == set(READ_TOOLS) | set(EXECUTE_TOOLS) | set(PLAN_TOOLS)

            # The advertised schema must type structured args (else the harness string-encodes
            # them and the server iterates the string char-by-char). Check it over the real wire.
            by_name = {t.name: t for t in listed.tools}
            ingest_schema = by_name["wg_ingest"].inputSchema
            assert ingest_schema["properties"]["nodes"]["type"] == "array"
            assert "nodes" in ingest_schema.get("required", [])

            await session.call_tool(
                "wg_ingest",
                {
                    "nodes": [
                        {"id": "a", "gate": {"kind": "command", "command": "true"}},
                        {"id": "b", "gate": {"kind": "command", "command": "true"}, "deps": ["a"]},
                    ]
                },
            )
            assert _payload(await session.call_tool("wg_plan", {}))["waves"] == [["a"], ["b"]]

            await session.call_tool("wg_claim", {"id": "a"})
            verified = _payload(await session.call_tool("wg_verify", {"id": "a"}))
            assert verified["exit_code"] == 0 and verified["status"] == "awaiting-signoff"

            await session.call_tool("wg_signoff", {"id": "a", "who": "parker"})
            assert _payload(await session.call_tool("wg_status", {"id": "a"}))["status"] == "done"

            # Error envelope surfaces over the wire for an illegal op (b is not awaiting-signoff).
            err = _payload(await session.call_tool("wg_signoff", {"id": "b", "who": "x"}))
            assert err["error"] == "illegal_transition"
            return {"tools": len(names)}


def test_live_mcp_stdio_round_trip(tmp_path):
    store.init_store(str(tmp_path))

    async def runner():
        return await asyncio.wait_for(_round_trip(str(tmp_path)), timeout=30)

    from workgraph.mcp_server import EXECUTE_TOOLS, PLAN_TOOLS, READ_TOOLS

    result = asyncio.run(runner())
    assert result["tools"] == len(READ_TOOLS) + len(EXECUTE_TOOLS) + len(PLAN_TOOLS)
