# -*- coding: utf-8 -*-
"""
Agent Reach MCP Server — expose doctor/status as MCP tool.

Run: python -m agent_reach.integrations.mcp_server

Agent Reach is an installer + doctor tool. For actual reading/searching,
agents should call upstream tools directly (twitter-cli, yt-dlp, mcporter, etc.).

Supports both MCP SDK generations: the v1 decorator API (``@server.list_tools()``
of mcp 1.x) and the v2 constructor-callback API (``on_list_tools=...`` of mcp
2.x, where the decorators no longer exist).
"""

import asyncio
import inspect
import json
import sys
from typing import Any, Dict, List

from agent_reach.config import Config
from agent_reach.core import AgentReach
from agent_reach.utils.text import scrub_url_credentials

try:
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import TextContent, Tool

    HAS_MCP = True
except ImportError:
    HAS_MCP = False
    Server: Any = None  # type: ignore[no-redef]

_SERVER_NAME = "agent-reach"


def _is_mcp_v2() -> bool:
    """True when the installed MCP SDK uses the v2 constructor-callback API."""
    if not HAS_MCP or Server is None:
        return False
    try:
        return "on_call_tool" in inspect.signature(Server.__init__).parameters
    except (TypeError, ValueError):  # pragma: no cover
        return False


def _tool_specs() -> List[Dict[str, Any]]:
    """Tool definitions as MCP wire dicts (camelCase keys for both SDKs)."""
    return [
        {
            "name": "get_status",
            "description": "Get Agent Reach status: which channels are installed and active.",
            "inputSchema": {"type": "object", "properties": {}},
        },
    ]


def create_server():
    if not HAS_MCP:
        print(
            "MCP not installed. Install: python -m pip install "
            "'agent-reach[mcp] @ "
            "https://github.com/Panniantong/agent-reach/archive/main.zip'",
            file=sys.stderr,
        )
        sys.exit(1)

    config = Config(read_only=True)
    eyes = AgentReach(config)

    async def list_tools() -> List[Any]:
        specs: List[Dict[str, Any]] = _tool_specs()
        return [Tool(**spec) for spec in specs]

    async def call_tool(name: str, arguments: Dict[str, Any]) -> List[Any]:
        """One tool call; always returns v1-shaped text content (never raises)."""
        try:
            if name == "get_status":
                result = eyes.doctor_report()
            else:
                result = f"Unknown tool: {name}"

            text = (
                json.dumps(result, ensure_ascii=False, indent=2)
                if isinstance(result, (dict, list))
                else str(result)
            )
            return [TextContent(type="text", text=text)]
        except Exception as e:
            return [
                TextContent(
                    type="text",
                    text=f"Error: {scrub_url_credentials(e)}",
                )
            ]

    # ── v2 callback API (mcp >= 2.x) ────────────────────────────────── #
    if _is_mcp_v2():
        from mcp.types import CallToolResult, ListToolsResult

        async def on_list_tools(_ctx: Any, _params: Any) -> Any:
            return ListToolsResult(tools=await list_tools())

        async def on_call_tool(_ctx: Any, params: Any) -> Any:
            try:
                content = await call_tool(
                    params.name, dict(params.arguments or {})
                )
                return CallToolResult(content=content)
            except Exception as e:  # pragma: no cover - defense in depth
                return CallToolResult(
                    content=[
                        TextContent(
                            type="text",
                            text=f"Error: {scrub_url_credentials(e)}",
                        )
                    ],
                    is_error=True,
                )

        return Server(
            _SERVER_NAME,
            description="Agent Reach doctor/status as an MCP tool",
            on_list_tools=on_list_tools,
            on_call_tool=on_call_tool,
        )

    # ── v1 decorator API (mcp 1.x) ──────────────────────────────────── #
    server: Any = Server(_SERVER_NAME)

    @server.list_tools()
    async def _list_tools():
        return await list_tools()

    @server.call_tool()
    async def _call_tool(name: str, arguments: dict):
        return await call_tool(name, arguments)

    return server


async def main():
    server = create_server()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream, write_stream, server.create_initialization_options()
        )


if __name__ == "__main__":
    asyncio.run(main())
