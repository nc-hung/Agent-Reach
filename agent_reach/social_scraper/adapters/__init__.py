# -*- coding: utf-8 -*-
"""Platform adapters — one file per platform, registered in a registry."""

from typing import List, Optional
from urllib.parse import urlparse

from ..exceptions import UnsupportedPlatformError
from ..interfaces import ContentScraper


class ScraperRegistry:
    """Ordered collection of scrapers; resolves URLs to an adapter (DIP)."""

    def __init__(self, scrapers: Optional[List[ContentScraper]] = None):
        self._scrapers: List[ContentScraper] = list(scrapers or [])

    def register(self, scraper: ContentScraper) -> None:
        """Add an adapter (later registrations are checked first)."""
        self._scrapers.insert(0, scraper)

    def all(self) -> List[ContentScraper]:
        """Every registered adapter, in resolution order."""
        return list(self._scrapers)

    def resolve(self, url: str) -> ContentScraper:
        """Return the adapter that can handle ``url``."""
        host = (urlparse(url).hostname or "").lower()
        for scraper in self._scrapers:
            if scraper.can_handle(url):
                return scraper
        raise UnsupportedPlatformError(
            f"No scraper for '{host or url}'. Supported: "
            + ", ".join(sorted(s.platform.value for s in self._scrapers))
        )


def default_registry() -> ScraperRegistry:
    """Build the registry with all built-in platform adapters."""
    from .facebook import FacebookScraper
    from .instagram import InstagramScraper
    from .tiktok import TikTokScraper

    return ScraperRegistry(
        [TikTokScraper(), InstagramScraper(), FacebookScraper()]
    )


DEFAULT_REGISTRY = None  # populated lazily by get_default_registry()


def get_default_registry() -> ScraperRegistry:
    """Process-wide default registry (lazy to avoid import cycles)."""
    global DEFAULT_REGISTRY
    if DEFAULT_REGISTRY is None:
        DEFAULT_REGISTRY = default_registry()
    return DEFAULT_REGISTRY
