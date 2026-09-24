# -*- coding: utf-8 -*-
"""``agent-reach-social`` — CLI for scraping, backing up and querying.

Subcommands:
    login       open a visible browser and save a login session
    scrape      scrape a page/profile URL → backup + manifest + media
    list        list backed-up resources (filter by platform/handle/kind)
    show        show one resource + its raw payload reference
    search      search backed-up resources
    download    download pending media (by id or by target)
    status      backup stats + dependency diagnostics
    serve-mcp   run the MCP server over stdio
"""

import argparse
import json
import sys
from typing import Any, Dict, List, Optional, Sequence

from agent_reach.utils.text import scrub_url_credentials

from .config import ScraperSettings
from .engine import ScrapeEngine
from .exceptions import SocialScraperError, UnsupportedPlatformError
from .models import (
    Platform,
    Resource,
    ScrapeRequest,
    parse_kinds,
    to_jsonable,
)

_EXIT_OK = 0
_EXIT_ERROR = 1
_EXIT_USAGE = 2

_PLATFORM_ALIASES = {
    "fb": Platform.FACEBOOK,
    "facebook": Platform.FACEBOOK,
    "ig": Platform.INSTAGRAM,
    "instagram": Platform.INSTAGRAM,
    "tt": Platform.TIKTOK,
    "tiktok": Platform.TIKTOK,
}


# ---------------------------------------------------------------------- #
# Output helpers
# ---------------------------------------------------------------------- #
def _console():
    from rich.console import Console

    return Console()


def _print_json(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def _resource_row(res: Resource) -> Dict[str, Any]:
    return {
        "id": res.id,
        "kind": res.kind.value,
        "handle": res.handle,
        "created_at": res.created_at,
        "text": (res.text or "")[:80],
        "media": len(res.media),
        "url": res.url,
    }


def _print_resources(rows: Sequence[Dict[str, Any]], title: str) -> None:
    from rich.table import Table

    console = _console()
    if not rows:
        console.print(f"[dim]{title}: no results[/dim]")
        return
    table = Table(title=f"{title} ({len(rows)})")
    table.add_column("id", style="cyan", no_wrap=True)
    table.add_column("kind", style="magenta")
    table.add_column("handle")
    table.add_column("created")
    table.add_column("media", justify="right")
    table.add_column("text", overflow="fold", max_width=48)
    for row in rows:
        table.add_row(
            row["id"],
            row["kind"],
            row["handle"],
            row["created_at"],
            str(row["media"]),
            row["text"],
        )
    console.print(table)


def _platform(value: Optional[str]) -> Optional[Platform]:
    if not value:
        return None
    platform = _PLATFORM_ALIASES.get(value.lower())
    if platform is None:
        raise SocialScraperError(
            f"Unknown platform '{value}'. Use: facebook, instagram, tiktok"
        )
    return platform


def _resources_of(page: Dict[str, Any]) -> List[Resource]:
    return list(page.get("items") or [])


# ---------------------------------------------------------------------- #
# Commands
# ---------------------------------------------------------------------- #
def cmd_login(args: argparse.Namespace, settings: ScraperSettings) -> int:
    """Interactive login → saved storage_state for later scrapes."""
    import asyncio

    from .adapters import get_default_registry

    platform = _platform(args.platform)
    assert platform is not None
    registry = get_default_registry()
    login_url = ""
    for scraper in registry.all():
        if scraper.platform == platform:
            login_url = scraper.login_url("")
            break
    if args.url:
        login_url = args.url

    from .browser.session import SessionFactory

    factory = SessionFactory(settings)
    console = _console()
    console.print(
        f"[bold]Login:[/bold] {platform.value} — complete the login in the "
        f"opened browser (timeout {args.timeout}s)…"
    )
    path = asyncio.run(
        factory.login(platform, login_url, timeout_s=args.timeout, headless=False)
    )
    console.print(f"[green]Session saved:[/green] {path}")
    return _EXIT_OK


def cmd_scrape(args: argparse.Namespace, settings: ScraperSettings) -> int:
    """Scrape one URL and persist everything."""
    request = ScrapeRequest(
        url=args.url,
        kinds=parse_kinds(args.kinds),
        max_items=args.max_items or settings.max_items,
        max_scroll=args.max_scroll or settings.max_scroll,
        download_media=not args.no_download,
        headless=not args.headed,
        timeout_ms=settings.timeout_ms,
    )
    engine = ScrapeEngine(settings=settings)
    result = engine.scrape(request)
    payload = result.to_dict()

    if args.json:
        _print_json(payload)
        return _EXIT_OK

    console = _console()
    console.print(
        f"[green]✔[/green] [bold]{payload['platform']}/{payload['handle']}[/bold] "
        f"→ session [cyan]{payload['session']}[/cyan] "
        f"({payload['duration_ms']} ms)"
    )
    counts = ", ".join(f"{k}={v}" for k, v in sorted(payload["counts"].items()))
    console.print(f"  resources: {counts or 'none'}")
    console.print(
        f"  raw payloads: {payload['raw_count']} | media: {payload['media']}"
    )
    console.print(f"  manifest: {payload['manifest_path']}")
    for err in payload["errors"][:10]:
        console.print(f"  [yellow]warn:[/yellow] {err}")
    from .models import resource_from_dict

    _print_resources(
        [
            _resource_row(
                resource_from_dict(r) if isinstance(r, dict) else r
            )
            for r in payload["resources"]
        ],
        "preview",
    )
    return _EXIT_OK


def cmd_list(args: argparse.Namespace, settings: ScraperSettings) -> int:
    """List backed-up resources."""
    from .storage.repository import ResourceRepository

    if args.targets:
        repo = ResourceRepository(settings.backup_dir)
        payload = {"targets": repo.targets(_platform(args.platform))}
        if args.json:
            _print_json(payload)
            return _EXIT_OK
        console = _console()
        for target in payload["targets"]:
            kinds_text = ", ".join(
                f"{k}={v}" for k, v in sorted(target["kinds"].items())
            )
            console.print(
                f"[cyan]{target['platform']}/{target['handle']}[/cyan] "
                f"— {target['resources']} resources ({kinds_text})"
            )
        if not payload["targets"]:
            console.print("[dim]no targets scraped yet[/dim]")
        return _EXIT_OK

    repo = ResourceRepository(settings.backup_dir)
    kinds = parse_kinds(args.kind) if args.kind else None
    page = repo.list_resources(
        platform=_platform(args.platform),
        handle=args.handle,
        kinds=kinds,
        limit=args.limit,
        offset=args.offset,
    )
    rows = [_resource_row(res) for res in _resources_of(page)]
    if args.json:
        _print_json({"total": page["total"], "items": rows})
        return _EXIT_OK
    _print_resources(rows, f"resources ({page['total']} total)")
    return _EXIT_OK


def cmd_show(args: argparse.Namespace, settings: ScraperSettings) -> int:
    """Show one resource by id."""
    from .storage.repository import ResourceRepository

    repo = ResourceRepository(settings.backup_dir)
    resource = repo.get(args.id)
    if resource is None:
        raise SocialScraperError(f"Resource not found: {args.id}")
    payload = to_jsonable(resource)
    if args.raw:
        payload["raw"] = repo.read_raw(resource, max_bytes=256 * 1024)
    if args.json:
        _print_json(payload)
        return _EXIT_OK

    console = _console()
    console.print(f"[bold cyan]{resource.kind.value}[/bold cyan] {resource.id}")
    console.print(f"  platform: {resource.platform.value}")
    console.print(f"  handle:   {resource.handle}")
    console.print(f"  url:      {resource.url}")
    if resource.created_at:
        console.print(f"  created:  {resource.created_at}")
    if resource.metrics:
        console.print(f"  metrics:  {resource.metrics}")
    if resource.media:
        console.print(f"  media:    {len(resource.media)} asset(s)")
        for asset in resource.media[:5]:
            state = asset.local_path or asset.url
            console.print(f"    - [{asset.media_type}] {state}")
    if resource.text:
        console.print(f"  text:     {resource.text[:400]}")
    if resource.raw_ref.file:
        console.print(f"  raw:      {resource.raw_ref.file} {resource.raw_ref.pointer}")
    return _EXIT_OK


def cmd_search(args: argparse.Namespace, settings: ScraperSettings) -> int:
    """Search backed-up resources."""
    from .storage.repository import ResourceRepository

    repo = ResourceRepository(settings.backup_dir)
    kinds = parse_kinds(args.kind) if args.kind else None
    hits = repo.search(args.query, kinds=kinds, limit=args.limit)
    rows = [_resource_row(res) for res in hits]
    if args.json:
        _print_json({"items": rows})
        return _EXIT_OK
    _print_resources(rows, f"search '{args.query}'")
    return _EXIT_OK


def cmd_download(args: argparse.Namespace, settings: ScraperSettings) -> int:
    """Download pending media for a resource or a target."""
    from .storage.repository import ResourceRepository

    repo = ResourceRepository(settings.backup_dir)
    engine = ScrapeEngine(settings=settings)

    resources: List[Resource] = []
    if args.id:
        found = repo.get(args.id)
        if found is None:
            raise SocialScraperError(f"Resource not found: {args.id}")
        resources = [found]
    else:
        platform = _platform(args.platform)
        if platform is None or not args.handle:
            raise SocialScraperError("Use --id, or --platform + --handle")
        page = repo.list_resources(platform=platform, handle=args.handle, limit=10_000)
        resources = _resources_of(page)

    from pathlib import Path

    groups: Dict[str, List[Resource]] = {}
    for res in resources:
        pending = [
            asset
            for asset in res.media
            if asset.url
            and not (asset.local_path and Path(asset.local_path).is_file())
        ]
        if pending:
            groups.setdefault(res.session, []).append(res)

    totals = {"total": 0, "downloaded": 0, "skipped": 0, "failed": 0}
    for session_rel, session_resources in groups.items():
        session_dir = repo.session_path(session_rel)
        assets = []
        for res in session_resources:
            for asset in res.media:
                if asset.url:
                    asset.resource_id = asset.resource_id or res.id
                    assets.append(asset)
        summary = engine.downloader.download(assets, session_dir / "media")
        for key in totals:
            totals[key] += int(summary.get(key, 0))
        repo.invalidate(session_rel)
        engine.store.write_resources(session_dir, repo.load_session(session_rel))
        if hasattr(engine.store, "update_manifest_media"):
            engine.store.update_manifest_media(session_dir, summary)

    if args.json:
        _print_json(totals)
        return _EXIT_OK
    console = _console()
    console.print(
        f"[green]✔[/green] downloaded={totals['downloaded']} "
        f"skipped={totals['skipped']} failed={totals['failed']} "
        f"(of {totals['total']})"
    )
    return _EXIT_OK


def cmd_status(args: argparse.Namespace, settings: ScraperSettings) -> int:
    """Backup stats + dependency diagnostics."""
    from .mcp.server import _build_deps, _status

    deps = _build_deps(settings, None, None)
    payload = _status(deps)
    if args.json:
        _print_json(payload)
        return _EXIT_OK

    console = _console()
    console.print(f"[bold]backup dir:[/bold] {payload['backup_dir']}")
    console.print(
        f"sessions: {payload['sessions']} | targets: {payload['targets']} | "
        f"resources: {payload['resources']}"
    )
    if payload["by_platform"]:
        console.print(f"by platform: {payload['by_platform']}")
    if payload["by_kind"]:
        console.print(f"by kind: {payload['by_kind']}")
    deps_ok = payload.get("dependencies", {})
    console.print(
        "dependencies: "
        + ", ".join(f"{k}={'✓' if v else '✗'}" for k, v in deps_ok.items())
    )
    logins = payload.get("login_sessions", {})
    console.print(
        "login sessions: "
        + ", ".join(f"{k}={'✓' if v else '✗'}" for k, v in logins.items())
    )
    return _EXIT_OK


def cmd_serve_mcp(args: argparse.Namespace, settings: ScraperSettings) -> int:
    """Run the MCP server over stdio."""
    import asyncio

    from .mcp.server import main as mcp_main

    del settings  # the server resolves its own settings
    asyncio.run(mcp_main())
    return _EXIT_OK


# ---------------------------------------------------------------------- #
# Parser
# ---------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    """Build the ``agent-reach-social`` argument parser."""
    parser = argparse.ArgumentParser(
        prog="agent-reach-social",
        description="Scrape, back up and serve Facebook / Instagram / TikTok "
        "content for AI agents (CLI + MCP).",
    )
    parser.add_argument(
        "--backup-dir",
        help="Backup root (default: ~/.agent-reach/social-backups "
        "or $SOCIAL_BACKUP_DIR)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_login = sub.add_parser("login", help="Log in interactively (saves session)")
    p_login.add_argument(
        "platform", help="facebook | instagram | tiktok (aliases: fb, ig, tt)"
    )
    p_login.add_argument("--url", help="Override the login URL")
    p_login.add_argument("--timeout", type=int, default=240)
    p_login.set_defaults(func=cmd_login)

    p_scrape = sub.add_parser("scrape", help="Scrape a page/profile URL")
    p_scrape.add_argument("url")
    p_scrape.add_argument(
        "--kinds",
        help="Comma list: posts,videos,lives,events,stories,comments,profile,all "
        "(default: all)",
    )
    p_scrape.add_argument("--max-items", type=int, default=0)
    p_scrape.add_argument("--max-scroll", type=int, default=0)
    p_scrape.add_argument("--no-download", action="store_true")
    p_scrape.add_argument("--headed", action="store_true")
    p_scrape.add_argument("--json", action="store_true")
    p_scrape.set_defaults(func=cmd_scrape)

    p_list = sub.add_parser("list", help="List backed-up resources")
    p_list.add_argument("--platform", help="facebook | instagram | tiktok")
    p_list.add_argument("--handle")
    p_list.add_argument("--kind")
    p_list.add_argument("--targets", action="store_true", help="List targets instead")
    p_list.add_argument("-n", "--limit", type=int, default=50)
    p_list.add_argument("--offset", type=int, default=0)
    p_list.add_argument("--json", action="store_true")
    p_list.set_defaults(func=cmd_list)

    p_show = sub.add_parser("show", help="Show one resource by id")
    p_show.add_argument("id")
    p_show.add_argument("--raw", action="store_true", help="Include the raw payload")
    p_show.add_argument("--json", action="store_true")
    p_show.set_defaults(func=cmd_show)

    p_search = sub.add_parser("search", help="Search backed-up resources")
    p_search.add_argument("query")
    p_search.add_argument("--kind")
    p_search.add_argument("-n", "--limit", type=int, default=20)
    p_search.add_argument("--json", action="store_true")
    p_search.set_defaults(func=cmd_search)

    p_download = sub.add_parser("download", help="Download pending media")
    p_download.add_argument("--id", help="Resource id")
    p_download.add_argument("--platform")
    p_download.add_argument("--handle")
    p_download.add_argument("--json", action="store_true")
    p_download.set_defaults(func=cmd_download)

    p_status = sub.add_parser("status", help="Backup stats + diagnostics")
    p_status.add_argument("--json", action="store_true")
    p_status.set_defaults(func=cmd_status)

    p_mcp = sub.add_parser("serve-mcp", help="Run the MCP server (stdio)")
    p_mcp.set_defaults(func=cmd_serve_mcp)

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    """CLI entry point (returns a process exit code)."""
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    settings = ScraperSettings.from_env()
    if args.backup_dir:
        from dataclasses import replace
        from pathlib import Path

        settings = replace(settings, backup_dir=Path(args.backup_dir).expanduser())

    try:
        return int(args.func(args, settings))
    except KeyboardInterrupt:
        _console().print("[yellow]Interrupted.[/yellow]")
        return _EXIT_USAGE
    except UnsupportedPlatformError as exc:
        _console().print(f"[red]error:[/red] {scrub_url_credentials(exc)}")
        return _EXIT_USAGE
    except (SocialScraperError, ValueError) as exc:
        _console().print(f"[red]error:[/red] {scrub_url_credentials(exc)}")
        return _EXIT_ERROR
    except Exception as exc:  # unexpected — still scrub before printing
        _console().print(f"[red]unexpected error:[/red] {scrub_url_credentials(exc)}")
        return _EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
