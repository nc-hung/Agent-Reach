# -*- coding: utf-8 -*-
"""ScrapeEngine — orchestrates scrape → backup → download.

Depends only on abstractions (``ContentScraper`` registry, ``BackupStore``,
``MediaDownloader``); concrete pieces are injected through the constructor
(Dependency Inversion). The synchronous ``scrape()`` is thread-safe and is
what the MCP server calls via ``asyncio.to_thread``.
"""

import asyncio
from collections import Counter
from typing import Any, Dict, Optional

from .adapters import ScraperRegistry, get_default_registry
from .browser.session import SessionFactory
from .config import ScraperSettings
from .exceptions import UnsupportedPlatformError
from .interfaces import BackupStore, ContentScraper, MediaDownloader
from .media.downloader import StreamingMediaDownloader
from .models import MediaAsset, ScrapeBundle, ScrapeRequest, ScrapeResult, to_jsonable
from .storage.backup import BackupStoreImpl

_PREVIEW = 20


class ScrapeEngine:
    """Runs one scrape request end to end and persists the results."""

    def __init__(
        self,
        settings: Optional[ScraperSettings] = None,
        registry: Optional[ScraperRegistry] = None,
        store: Optional[BackupStore] = None,
        downloader: Optional[MediaDownloader] = None,
        session_factory: Optional[SessionFactory] = None,
    ):
        self.settings = settings or ScraperSettings.from_env()
        self.registry = registry or get_default_registry()
        self.store = store or BackupStoreImpl(self.settings.backup_dir)
        self.downloader = downloader or StreamingMediaDownloader(self.settings)
        self.session_factory = session_factory or SessionFactory(self.settings)

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def scrape(self, request: ScrapeRequest) -> ScrapeResult:
        """Synchronous scrape; never call from inside a running event loop."""
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.scrape_async(request))
        raise RuntimeError(
            "ScrapeEngine.scrape() cannot run inside an event loop — "
            "call scrape_async() or run it in a worker thread."
        )

    async def scrape_async(self, request: ScrapeRequest) -> ScrapeResult:
        """Scrape one target, back everything up, download media."""
        scraper = self.registry.resolve(request.url)
        handle = scraper.target_of(request.url)
        if not handle:
            raise UnsupportedPlatformError(
                f"Could not determine the profile handle from: {request.url}. "
                "Use a page/profile URL like "
                "https://www.instagram.com/<username>/ or "
                "https://www.tiktok.com/@<username>."
            )

        factory = self.session_factory
        session = await factory.open(
            scraper.platform,
            headless=request.headless,
            session_cookie=scraper.session_cookie,
        )
        try:
            bundle = await scraper.scrape(request, session)
        finally:
            await session.close()

        return await self._persist(bundle, request)

    # ------------------------------------------------------------------ #
    # Persistence
    # ------------------------------------------------------------------ #
    async def _persist(self, bundle: ScrapeBundle, request: ScrapeRequest) -> ScrapeResult:
        """Write raw payloads, index and manifest; then download media."""
        session_dir = self.store.begin(bundle.platform, bundle.handle)
        session_rel = self.store.session_rel(session_dir)

        self.store.write_raws(session_dir, bundle.raws)
        self.store.write_profile(session_dir, bundle.profile)
        self.store.write_resources(session_dir, bundle.resources)

        media_summary: Dict[str, int] = {
            "total": 0, "downloaded": 0, "skipped": 0, "failed": 0,
        }
        assets = self._bind_assets(bundle)
        if request.download_media and assets:
            media_summary = await asyncio.to_thread(
                self.downloader.download, assets, session_dir / "media"
            )
            # refresh the index so media paths/sha256 are queryable
            self.store.write_resources(session_dir, bundle.resources)

        self.store.finalize(session_dir, bundle, media_summary)

        counts: Dict[str, int] = dict(
            Counter(res.kind.value for res in bundle.resources)
        )
        if bundle.profile is not None:
            counts["profile"] = 1
        return ScrapeResult(
            session=session_rel,
            platform=bundle.platform,
            handle=bundle.handle,
            source_url=bundle.source_url,
            counts=counts,
            raw_count=len(bundle.raws),
            media=media_summary,
            manifest_path=str(session_dir / "manifest.json"),
            resources=bundle.resources[:_PREVIEW],
            errors=list(bundle.errors),
            duration_ms=int(bundle.stats.get("duration_ms", 0)),
        )

    @staticmethod
    def _bind_assets(bundle: ScrapeBundle) -> list:
        """Flat asset list with owning resource ids bound (for filenames)."""
        assets = []
        for res in bundle.resources:
            for asset in res.media:
                if isinstance(asset, MediaAsset) and asset.url:
                    asset.resource_id = asset.resource_id or res.id
                    assets.append(asset)
        return assets

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    def scraper_for(self, url: str) -> ContentScraper:
        """Resolve the adapter for a URL (used by CLI/MCP validation)."""
        return self.registry.resolve(url)

    @staticmethod
    def to_dict(result: ScrapeResult) -> Dict[str, Any]:
        """JSON-safe scrape result."""
        return to_jsonable(result)
