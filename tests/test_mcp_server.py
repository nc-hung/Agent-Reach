"""Security boundaries for the optional Agent Reach MCP server."""

import asyncio
import json
from types import SimpleNamespace

import pytest

import agent_reach.integrations.mcp_server as mcp_server


class _FakeServer:
    def __init__(self, name):
        self.name = name
        self.list_tools_handler = None
        self.call_tool_handler = None

    def list_tools(self):
        def register(handler):
            self.list_tools_handler = handler
            return handler

        return register

    def call_tool(self):
        def register(handler):
            self.call_tool_handler = handler
            return handler

        return register


def _install_fake_mcp(monkeypatch):
    monkeypatch.setattr(mcp_server, "HAS_MCP", True)
    monkeypatch.setattr(mcp_server, "Server", _FakeServer, raising=False)
    monkeypatch.setattr(
        mcp_server,
        "Tool",
        lambda **kwargs: SimpleNamespace(**kwargs),
        raising=False,
    )
    monkeypatch.setattr(
        mcp_server,
        "TextContent",
        lambda **kwargs: SimpleNamespace(**kwargs),
        raising=False,
    )


def test_mcp_status_uses_read_only_config(monkeypatch):
    _install_fake_mcp(monkeypatch)
    created_configs = []

    class _RecordingConfig:
        def __init__(self, *, read_only=False):
            self.read_only = read_only
            created_configs.append(self)

    class _AgentReach:
        def __init__(self, config):
            self.config = config

        def doctor_report(self):
            return "ok"

    monkeypatch.setattr(mcp_server, "Config", _RecordingConfig)
    monkeypatch.setattr(mcp_server, "AgentReach", _AgentReach)

    server = mcp_server.create_server()
    result = asyncio.run(server.call_tool_handler("get_status", {}))

    assert len(created_configs) == 1
    assert created_configs[0].read_only is True
    assert result[0].text == "ok"


class _FakeV2Server:
    """mcp 2.x style: handlers arrive as constructor callbacks."""

    def __init__(self, name, **kwargs):
        self.name = name
        self.kwargs = kwargs


def test_mcp_v2_constructor_callbacks(monkeypatch):
    """With mcp >= 2.x the server registers on_list_tools/on_call_tool."""
    mcp_types = pytest.importorskip("mcp.types")  # real pydantic models needed
    monkeypatch.setattr(mcp_server, "HAS_MCP", True)
    monkeypatch.setattr(mcp_server, "Server", _FakeV2Server, raising=False)
    monkeypatch.setattr(mcp_server, "_is_mcp_v2", lambda: True)

    class _Config:
        def __init__(self, *, read_only=False):
            self.read_only = read_only

    class _AgentReach:
        def __init__(self, config):
            self.config = config

        def doctor_report(self):
            return {"web": "ok"}

    monkeypatch.setattr(mcp_server, "Config", _Config)
    monkeypatch.setattr(mcp_server, "AgentReach", _AgentReach)

    server = mcp_server.create_server()
    assert set(server.kwargs) == {"description", "on_list_tools", "on_call_tool"}

    tools = asyncio.run(server.kwargs["on_list_tools"](None, None))
    assert [t.name for t in tools.tools] == ["get_status"]

    result = asyncio.run(
        server.kwargs["on_call_tool"](
            None, SimpleNamespace(name="get_status", arguments=None)
        )
    )
    assert isinstance(result, mcp_types.CallToolResult)
    assert result.is_error is False
    assert json.loads(result.content[0].text) == {"web": "ok"}

    unknown = asyncio.run(
        server.kwargs["on_call_tool"](
            None, SimpleNamespace(name="nope", arguments={})
        )
    )
    assert "Unknown tool: nope" in unknown.content[0].text


def test_mcp_status_exception_credentials_are_scrubbed(monkeypatch):
    _install_fake_mcp(monkeypatch)

    class _Config:
        def __init__(self, *, read_only=False):
            self.read_only = read_only

    class _ExplodingAgentReach:
        def __init__(self, config):
            self.config = config

        def doctor_report(self):
            raise RuntimeError(
                "request https://alice:password@example.test/data"
                "?token=top-secret failed"
            )

    monkeypatch.setattr(mcp_server, "Config", _Config)
    monkeypatch.setattr(mcp_server, "AgentReach", _ExplodingAgentReach)

    server = mcp_server.create_server()
    result = asyncio.run(server.call_tool_handler("get_status", {}))
    text = result[0].text

    assert "alice" not in text
    assert "password" not in text
    assert "top-secret" not in text
    assert "https://***@example.test/data?token=***" in text
