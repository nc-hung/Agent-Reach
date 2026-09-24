# -*- coding: utf-8 -*-
"""MCP server: tool specs, dispatch, v1 + v2 wiring, credential scrubbing."""

import asyncio
import json
from types import SimpleNamespace

import pytest

import agent_reach.social_scraper.mcp.server as mcp_server
from agent_reach.social_scraper.models import Platform, ScrapeResult

from .conftest import seed_session


# ---------------------------------------------------------------------- #
# Fakes for both SDK generations
# ---------------------------------------------------------------------- #
class _FakeV1Server:
    def __init__(self, name, **_kwargs):
        self.name = name
        self.list_tools_handler = None
        self.call_tool_handler = None
        self.list_resources_handler = None
        self.read_resource_handler = None

    def _reg(self, attr):
        def decorator(handler):
            setattr(self, attr, handler)
            return handler

        return decorator

    def list_tools(self):
        return self._reg("list_tools_handler")

    def call_tool(self):
        return self._reg("call_tool_handler")

    def list_resources(self):
        return self._reg("list_resources_handler")

    def read_resource(self):
        return self._reg("read_resource_handler")


class _FakeV2Server:
    def __init__(self, name, **kwargs):
        self.name = name
        self.kwargs = kwargs


@pytest.fixture
def seeded(settings):
    """A backup session with 3 posts + profile, plus a repository."""
    from agent_reach.social_scraper.storage.backup import BackupStoreImpl

    store = BackupStoreImpl(settings.backup_dir)
    session_dir, resources, profile = seed_session(store)
    return SimpleNamespace(
        settings=settings,
        session_rel=session_dir.relative_to(settings.backup_dir).as_posix(),
        resources=resources,
        profile=profile,
    )


def _deps(seeded):
    return mcp_server._build_deps(seeded.settings, None, None)


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------- #
# Tool specs & dispatch
# ---------------------------------------------------------------------- #
def test_tool_specs_are_eight_social_tools():
    specs = mcp_server._tool_specs()
    names = [s["name"] for s in specs]
    assert names == [
        "social_scrape",
        "social_list_targets",
        "social_list_resources",
        "social_get_resource",
        "social_read_raw",
        "social_search",
        "social_download_media",
        "social_status",
    ]
    for spec in specs:
        assert spec["inputSchema"]["type"] == "object"
        assert spec["description"]


def test_dispatch_list_targets_and_resources(seeded):
    deps = _deps(seeded)
    payload = _run(mcp_server._dispatch(deps, "social_list_targets", {}))
    assert payload["targets"][0]["handle"] == "nasa"
    assert payload["targets"][0]["resources"] == 3

    page = _run(
        mcp_server._dispatch(
            deps, "social_list_resources", {"platform": "instagram", "limit": 2}
        )
    )
    assert page["total"] == 3 and len(page["items"]) == 2
    assert page["items"][0]["platform"] == "instagram"


def test_dispatch_get_and_read_raw(seeded):
    deps = _deps(seeded)
    rid = seeded.resources[0].id
    resource = _run(mcp_server._dispatch(deps, "social_get_resource", {"id": rid}))
    assert resource["id"] == rid
    assert resource["session"] == seeded.session_rel

    raw = _run(mcp_server._dispatch(deps, "social_read_raw", {"id": rid}))
    assert raw == [{"id": "0"}, {"id": "1"}, {"id": "2"}]

    missing = _run(mcp_server._dispatch(deps, "social_get_resource", {"id": "zz"}))
    assert missing["error"] == "resource not found"


def test_dispatch_search_and_status(seeded):
    deps = _deps(seeded)
    hits = _run(mcp_server._dispatch(deps, "social_search", {"query": "launches"}))
    assert len(hits["items"]) == 3

    status = _run(mcp_server._dispatch(deps, "social_status", {}))
    assert status["sessions"] == 1
    assert status["resources"] == 3
    assert "playwright" in status["dependencies"]
    assert status["backup_dir"] == str(seeded.settings.backup_dir)


def test_dispatch_scrape_runs_engine_in_thread(seeded):
    class _FakeEngine:
        def scrape(self, request):
            return ScrapeResult(
                session="instagram/nasa/x",
                platform=Platform.INSTAGRAM,
                handle="nasa",
                source_url=request.url,
                counts={"post": 1},
                raw_count=1,
            )

    deps = mcp_server._Deps(
        settings=seeded.settings,
        engine=_FakeEngine(),
        repository=mcp_server.ResourceRepository(seeded.settings.backup_dir),
    )
    payload = _run(
        mcp_server._dispatch(
            deps, "social_scrape", {"url": "https://www.instagram.com/nasa/"}
        )
    )
    assert payload["handle"] == "nasa"
    assert payload["counts"] == {"post": 1}


def test_dispatch_unknown_tool():
    deps = SimpleNamespace(
        settings=None, engine=None, repository=None,
    )
    payload = _run(mcp_server._dispatch(deps, "nope", {}))
    assert payload["error"].startswith("Unknown tool")


# ---------------------------------------------------------------------- #
# v1 decorator wiring
# ---------------------------------------------------------------------- #
def test_v1_server_lists_and_calls_tools(monkeypatch, seeded):
    monkeypatch.setattr(mcp_server, "HAS_MCP", True)
    monkeypatch.setattr(mcp_server, "Server", _FakeV1Server, raising=False)
    monkeypatch.setattr(mcp_server, "_is_mcp_v2", lambda: False)

    server = mcp_server.create_server(settings=seeded.settings)
    tools = _run(server.list_tools_handler())
    assert [t.name for t in tools][0] == "social_scrape"

    result = _run(server.call_tool_handler("social_list_targets", {}))
    payload = json.loads(result[0].text)
    assert payload["targets"][0]["handle"] == "nasa"

    resources = _run(server.list_resources_handler())
    uris = [r.uri for r in resources]
    assert any(uri.startswith("social://instagram/nasa/") for uri in uris)
    assert any(uri.endswith("/profile") for uri in uris)

    profile_uri = next(u for u in uris if u.endswith("/profile"))
    text = _run(server.read_resource_handler(profile_uri))
    assert json.loads(text)["kind"] == "profile"


def test_v1_errors_scrub_credentials(monkeypatch, seeded):
    monkeypatch.setattr(mcp_server, "HAS_MCP", True)
    monkeypatch.setattr(mcp_server, "Server", _FakeV1Server, raising=False)
    monkeypatch.setattr(mcp_server, "_is_mcp_v2", lambda: False)

    class _ExplodingEngine:
        def scrape(self, request):
            raise RuntimeError(
                "request https://alice:password@example.test/x"
                "?token=top-secret failed"
            )

    repository = mcp_server.ResourceRepository(seeded.settings.backup_dir)
    server = mcp_server.create_server(
        settings=seeded.settings,
        engine=_ExplodingEngine(),
        repository=repository,
    )
    result = _run(
        server.call_tool_handler(
            "social_scrape", {"url": "https://www.instagram.com/nasa/"}
        )
    )
    text = result[0].text
    assert text.startswith("Error:")
    assert "alice" not in text
    assert "password" not in text
    assert "top-secret" not in text
    assert "https://***@example.test" in text


# ---------------------------------------------------------------------- #
# v2 callback wiring
# ---------------------------------------------------------------------- #
def test_v2_server_wires_callbacks(monkeypatch, seeded):
    monkeypatch.setattr(mcp_server, "HAS_MCP", True)
    monkeypatch.setattr(mcp_server, "Server", _FakeV2Server, raising=False)
    monkeypatch.setattr(mcp_server, "_is_mcp_v2", lambda: True)

    server = mcp_server.create_server(settings=seeded.settings)
    kwargs = server.kwargs

    tools_result = _run(kwargs["on_list_tools"](None, None))
    assert len(tools_result.tools) == 8

    call_result = _run(
        kwargs["on_call_tool"](
            None,
            SimpleNamespace(name="social_list_targets", arguments={}),
        )
    )
    payload = json.loads(call_result.content[0].text)
    assert payload["targets"][0]["platform"] == "instagram"
    assert call_result.is_error is False

    bad = _run(
        kwargs["on_call_tool"](
            None, SimpleNamespace(name="social_get_resource", arguments=None)
        )
    )
    # unknown id → structured error payload, not a transport failure
    assert json.loads(bad.content[0].text)["error"] == "resource not found"

    resources_result = _run(kwargs["on_list_resources"](None, None))
    assert any("social://" in str(r.uri) for r in resources_result.resources)

    rid = seeded.resources[0].id
    read = _run(
        kwargs["on_read_resource"](
            None, SimpleNamespace(uri=f"social://{seeded.session_rel}/{rid}")
        )
    )
    assert json.loads(read.contents[0].text)["id"] == rid
