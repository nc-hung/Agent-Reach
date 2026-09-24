# -*- coding: utf-8 -*-
"""Social Reach MCP server — expose scraped resources to any MCP client.

Run: ``python -m agent_reach.social_scraper.mcp.server`` or
``agent-reach-social serve-mcp``.

Supports both MCP SDK generations:
- v1 decorator API (``@server.list_tools()``) when installed,
- v2 callback API (``on_list_tools=...`` constructor kwargs) otherwise.

Tools (resources are also exposed as ``social://…`` URIs):
- social_scrape          scrape a page/profile URL (posts/videos/lives/events)
- social_list_targets    list backed-up targets
- social_list_resources  paginated resource listing
- social_get_resource    one resource by id
- social_read_raw        the exact payload the resource was parsed from
- social_search          full-text-ish search over backed-up resources
- social_download_media  (re)download media for a resource/session
- social_status          backup stats + dependency diagnostics
"""

import asyncio
import inspect
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent_reach.config import Config
from agent_reach.core import AgentReach
from agent_reach.utils.text import scrub_url_credentials

from ..config import ScraperSettings
from ..engine import ScrapeEngine
from ..models import Platform, Resource, ScrapeRequest, parse_kinds, to_jsonable
from ..storage.backup import BackupStoreImpl
from ..storage.repository import ResourceRepository

try:
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import (
        CallToolResult,
        ListResourcesResult,
        ListToolsResult,
        ReadResourceResult,
        TextContent,
        TextResourceContents,
        Tool,
    )
    from mcp.types import (
        Resource as McpResource,
    )

    HAS_MCP = True
except ImportError:  # pragma: no cover - exercised via tests' fake module
    HAS_MCP = False
    Server: Any = None  # type: ignore[no-redef]

#: max resources returned by social_list_resources / list_resources()
_MAX_LIST = 100
#: max raw payload bytes returned by social_read_raw
_MAX_RAW_READ = 512 * 1024
_SERVER_NAME = "agent-reach-social"
_SERVER_VERSION = "1.0.0"


def _is_mcp_v2() -> bool:
    """True when the installed MCP SDK uses the v2 constructor-callback API."""
    if not HAS_MCP or Server is None:
        return False
    try:
        return "on_call_tool" in inspect.signature(Server.__init__).parameters
    except (TypeError, ValueError):  # pragma: no cover
        return False


@dataclass
class _Deps:
    """Injected dependencies (DIP — the server never builds them itself)."""

    settings: ScraperSettings
    engine: ScrapeEngine
    repository: ResourceRepository


def _build_deps(
    settings: Optional[ScraperSettings],
    engine: Optional[ScrapeEngine],
    repository: Optional[ResourceRepository],
) -> _Deps:
    resolved = settings or ScraperSettings.from_env()
    resolved_repo = repository or ResourceRepository(resolved.backup_dir)
    resolved_engine = engine or ScrapeEngine(
        settings=resolved, store=BackupStoreImpl(resolved.backup_dir)
    )
    return _Deps(
        settings=resolved,
        engine=resolved_engine,
        repository=resolved_repo,
    )


# ---------------------------------------------------------------------- #
# Tool definitions
# ---------------------------------------------------------------------- #
def _tool_specs() -> List[Dict[str, Any]]:
    return [
        {
            "name": "social_scrape",
            "description": (
                "Scrape a Facebook page / Instagram profile / TikTok profile URL: "
                "captures every payload the platform returns (posts, videos, live, "
                "events), backs it up with manifest+sha256, downloads media, and "
                "returns the normalized resources."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "Target page/profile URL"},
                    "kinds": {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "enum": ["posts", "videos", "lives", "events",
                                     "stories", "comments", "profile", "all"],
                        },
                        "description": "Resource kinds to collect (default: all)",
                    },
                    "max_items": {"type": "integer", "description": "Max items per kind (default 100)"},
                    "max_scroll": {"type": "integer", "description": "Scroll rounds per page (default 15)"},
                    "download_media": {"type": "boolean", "description": "Download media files (default true)"},
                    "headless": {"type": "boolean", "description": "Run browser headless (default true)"},
                },
                "required": ["url"],
            },
        },
        {
            "name": "social_list_targets",
            "description": "List every backed-up target (page/profile) with totals.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "platform": {
                        "type": "string",
                        "enum": ["facebook", "instagram", "tiktok"],
                    },
                },
            },
        },
        {
            "name": "social_list_resources",
            "description": "List backed-up resources (newest sessions first).",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "platform": {
                        "type": "string",
                        "enum": ["facebook", "instagram", "tiktok"],
                    },
                    "handle": {"type": "string"},
                    "kind": {"type": "string"},
                    "limit": {"type": "integer"},
                    "offset": {"type": "integer"},
                },
            },
        },
        {
            "name": "social_get_resource",
            "description": "Fetch one resource by id (includes media + raw back-reference).",
            "inputSchema": {
                "type": "object",
                "properties": {"id": {"type": "string"}},
                "required": ["id"],
            },
        },
        {
            "name": "social_read_raw",
            "description": (
                "Return the exact JSON payload a resource was parsed from "
                "(the platform's own response, preserved verbatim)."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {"id": {"type": "string"}},
                "required": ["id"],
            },
        },
        {
            "name": "social_search",
            "description": "Search backed-up resources by text/author/url.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "kind": {"type": "string"},
                    "limit": {"type": "integer"},
                },
                "required": ["query"],
            },
        },
        {
            "name": "social_download_media",
            "description": (
                "Download media for one resource (id) or every pending asset "
                "of a target (platform+handle)."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "platform": {
                        "type": "string",
                        "enum": ["facebook", "instagram", "tiktok"],
                    },
                    "handle": {"type": "string"},
                },
            },
        },
        {
            "name": "social_status",
            "description": "Backup statistics and dependency diagnostics.",
            "inputSchema": {"type": "object", "properties": {}},
        },
    ]


# ---------------------------------------------------------------------- #
# Dispatch (shared by both SDK generations)
# ---------------------------------------------------------------------- #
def _platform_arg(value: Any) -> Optional[Platform]:
    if not value:
        return None
    return Platform(str(value).lower())


async def _dispatch(deps: _Deps, name: str, arguments: Dict[str, Any]) -> Any:
    """Execute one tool call; returns a JSON-safe payload."""
    repo = deps.repository
    args = arguments or {}

    if name == "social_scrape":
        request = ScrapeRequest.from_dict(
            {
                **args,
                "kinds": args.get("kinds") or "all",
            }
        )
        result = await asyncio.to_thread(deps.engine.scrape, request)
        return result.to_dict()

    if name == "social_list_targets":
        return {"targets": repo.targets(_platform_arg(args.get("platform")))}

    if name == "social_list_resources":
        kinds = parse_kinds(args.get("kind")) if args.get("kind") else None
        page = repo.list_resources(
            platform=_platform_arg(args.get("platform")),
            handle=args.get("handle"),
            kinds=kinds,
            limit=min(int(args.get("limit") or 50), _MAX_LIST),
            offset=int(args.get("offset") or 0),
        )
        return {
            "total": page["total"],
            "offset": page["offset"],
            "limit": page["limit"],
            "items": [to_jsonable(r) for r in page["items"]],
        }

    if name == "social_get_resource":
        resource = repo.get(str(args.get("id", "")))
        if resource is None:
            return {"error": "resource not found", "id": args.get("id")}
        return to_jsonable(resource)

    if name == "social_read_raw":
        resource = repo.get(str(args.get("id", "")))
        if resource is None:
            return {"error": "resource not found", "id": args.get("id")}
        return repo.read_raw(resource, max_bytes=_MAX_RAW_READ)

    if name == "social_search":
        kinds = parse_kinds(args.get("kind")) if args.get("kind") else None
        hits = repo.search(
            str(args.get("query", "")),
            kinds=kinds,
            limit=min(int(args.get("limit") or 20), _MAX_LIST),
        )
        return {"items": [to_jsonable(r) for r in hits]}

    if name == "social_download_media":
        return _download_media(deps, args)

    if name == "social_status":
        return _status(deps)

    return {"error": f"Unknown tool: {name}"}


def _pending_assets(resource: Resource) -> List[Resource]:
    """The resource when at least one asset is missing on disk."""
    for asset in resource.media:
        if not asset.url:
            continue
        if asset.local_path and Path(asset.local_path).is_file():
            continue
        return [resource]
    return []


def _download_media(deps: _Deps, args: Dict[str, Any]) -> Any:
    """Download pending media for one id or one target; update the index."""
    repo = deps.repository
    groups: Dict[str, List[Resource]] = {}

    if args.get("id"):
        resource = repo.get(str(args["id"]))
        if resource is None:
            return {"error": "resource not found", "id": args.get("id")}
        for hit in _pending_assets(resource):
            groups.setdefault(hit.session, []).append(hit)
    else:
        platform = _platform_arg(args.get("platform"))
        handle = args.get("handle")
        if platform is None or not handle:
            return {"error": "provide id, or platform+handle"}
        listing = repo.list_resources(platform=platform, handle=handle, limit=_MAX_LIST)
        for hit in listing["items"]:
            for pending in _pending_assets(hit):
                groups.setdefault(pending.session, []).append(pending)

    if not groups:
        return {"downloaded": 0, "skipped": 0, "failed": 0, "note": "nothing pending"}

    totals = {"total": 0, "downloaded": 0, "skipped": 0, "failed": 0}
    for session_rel, resources in groups.items():
        session_dir = repo.session_path(session_rel)  # validated, no traversal
        assets = []
        for res in resources:
            for asset in res.media:
                if asset.url:
                    asset.resource_id = asset.resource_id or res.id
                    assets.append(asset)
        summary = deps.engine.downloader.download(assets, session_dir / "media")
        for key in totals:
            totals[key] += int(summary.get(key, 0))
        # rewrite the session index with updated media fields
        repo.invalidate(session_rel)
        deps.engine.store.write_resources(session_dir, repo.load_session(session_rel))
        if hasattr(deps.engine.store, "update_manifest_media"):
            deps.engine.store.update_manifest_media(session_dir, summary)
    return totals


def _status(deps: _Deps) -> Any:
    """Backup stats + dependency diagnostics."""
    import importlib.util

    repo = deps.repository
    stats = repo.stats()
    stats["settings"] = deps.settings.describe()
    stats["dependencies"] = {
        "mcp": HAS_MCP,
        "playwright": importlib.util.find_spec("playwright") is not None,
        "requests": importlib.util.find_spec("requests") is not None,
    }
    stats["login_sessions"] = {
        platform.value: (
            deps.settings.session_dir / f"{platform.value}.json"
        ).is_file()
        for platform in Platform
    }
    try:
        stats["channels"] = AgentReach(Config(read_only=True)).doctor_report()
    except Exception as exc:  # doctor must never break the MCP server
        stats["channels"] = f"unavailable: {scrub_url_credentials(exc)}"
    return stats


# ---------------------------------------------------------------------- #
# Builders for the two SDK generations
# ---------------------------------------------------------------------- #
def _json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str)


def _text_result(payload: Any) -> str:
    return _json(payload) if isinstance(payload, (dict, list)) else str(payload)


def create_server(
    settings: Optional[ScraperSettings] = None,
    engine: Optional[ScrapeEngine] = None,
    repository: Optional[ResourceRepository] = None,
) -> Any:
    """Build the MCP server (v2 callback API when available, else v1)."""
    if not HAS_MCP:
        print(
            "MCP not installed. Install: python -m pip install 'agent-reach[mcp] @ "
            "https://github.com/Panniantong/agent-reach/archive/main.zip'",
            file=sys.stderr,
        )
        raise SystemExit(1)

    deps = _build_deps(settings, engine, repository)

    # shared handlers ------------------------------------------------ #
    async def list_tools() -> List[Dict[str, Any]]:
        return _tool_specs()

    async def call_tool(name: str, arguments: Dict[str, Any]) -> str:
        payload = await _dispatch(deps, name, arguments)
        return _text_result(payload)

    async def list_resources() -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for session_rel in deps.repository.sessions()[:20]:
            profile = deps.repository.profile_of(session_rel)
            if profile is None:
                continue
            out.append(
                {
                    "uri": f"social://{session_rel}/profile",
                    "name": f"{profile.handle} profile ({session_rel})",
                    "mimeType": "application/json",
                    "description": (profile.text or "Scraped profile")[:200],
                }
            )
        listing = deps.repository.list_resources(limit=_MAX_LIST)
        for res in listing["items"]:
            out.append(
                {
                    "uri": res.uri,
                    "name": f"{res.kind.value} {res.id} @ {res.handle or res.platform.value}",
                    "mimeType": "application/json",
                    "description": (res.text or res.url)[:200],
                }
            )
        return out

    async def read_resource(uri: str) -> str:
        prefix = "social://"
        if not uri.startswith(prefix):
            return _json({"error": "unsupported uri scheme", "uri": uri})
        tail = uri[len(prefix):]
        parts = [p for p in tail.split("/") if p]
        if len(parts) < 2:
            return _json({"error": "malformed social:// uri", "uri": uri})
        resource_id = parts[-1]
        session_rel = "/".join(parts[:-1])
        if resource_id == "profile" or len(parts) == 3:
            profile = deps.repository.profile_of(session_rel)
            if profile is not None:
                return _json(to_jsonable(profile))
        resource = deps.repository.get_in_session(session_rel, resource_id)
        if resource is None:
            return _json({"error": "resource not found", "uri": uri})
        return _json(to_jsonable(resource))

    # v2 callback API ------------------------------------------------ #
    if _is_mcp_v2():
        from mcp.types import CallToolRequestParams, ReadResourceRequestParams

        async def on_list_tools(_ctx: Any, _params: Any) -> Any:
            return ListToolsResult(tools=[Tool(**spec) for spec in await list_tools()])

        async def on_call_tool(_ctx: Any, params: CallToolRequestParams) -> Any:
            try:
                text = await call_tool(params.name, dict(params.arguments or {}))
                return CallToolResult(
                    content=[TextContent(type="text", text=text)],
                )
            except Exception as exc:
                return CallToolResult(
                    content=[
                        TextContent(
                            type="text",
                            text=f"Error: {scrub_url_credentials(exc)}",
                        )
                    ],
                    is_error=True,
                )

        async def on_list_resources(_ctx: Any, _params: Any) -> Any:
            return ListResourcesResult(
                resources=[McpResource(**item) for item in await list_resources()]
            )

        async def on_read_resource(_ctx: Any, params: ReadResourceRequestParams) -> Any:
            try:
                text = await read_resource(params.uri)
                return ReadResourceResult(
                    contents=[
                        TextResourceContents(
                            uri=params.uri,
                            mime_type="application/json",
                            text=text,
                        )
                    ]
                )
            except Exception as exc:
                return ReadResourceResult(
                    contents=[
                        TextResourceContents(
                            uri=params.uri,
                            mime_type="application/json",
                            text=_json({"error": scrub_url_credentials(exc)}),
                        )
                    ]
                )

        return Server(
            _SERVER_NAME,
            version=_SERVER_VERSION,
            description="Scrape & serve Facebook / Instagram / TikTok resources",
            on_list_tools=on_list_tools,
            on_call_tool=on_call_tool,
            on_list_resources=on_list_resources,
            on_read_resource=on_read_resource,
        )

    # v1 decorator API ----------------------------------------------- #
    server: Any = Server(_SERVER_NAME)

    @server.list_tools()
    async def _list_tools():
        return [Tool(**spec) for spec in await list_tools()]

    @server.call_tool()
    async def _call_tool(name: str, arguments: dict):
        try:
            return [TextContent(type="text", text=await call_tool(name, arguments))]
        except Exception as exc:
            return [
                TextContent(
                    type="text", text=f"Error: {scrub_url_credentials(exc)}"
                )
            ]

    @server.list_resources()
    async def _list_resources():
        return [McpResource(**item) for item in await list_resources()]

    @server.read_resource()
    async def _read_resource(uri: str):
        return await read_resource(uri)

    return server


async def main() -> None:
    """Serve over stdio (the standard MCP transport for local agents)."""
    server = create_server()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream, write_stream, server.create_initialization_options()
        )


if __name__ == "__main__":
    asyncio.run(main())
