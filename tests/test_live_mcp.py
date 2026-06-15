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
            assert "wg_signoff" in names and "wg_plan" in names
            assert len(names) == 19, sorted(names)

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

    result = asyncio.run(runner())
    assert result["tools"] == 19
