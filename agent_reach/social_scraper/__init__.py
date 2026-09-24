# -*- coding: utf-8 -*-
"""Agent Reach Social Scraper — scrape, back up and serve social content.

Public API::

    from agent_reach.social_scraper import (
        ScrapeEngine, ScrapeRequest, ScraperSettings,
    )

    result = ScrapeEngine().scrape(
        ScrapeRequest(url="https://www.instagram.com/nasa/")
    )

MCP server::

    python -m agent_reach.social_scraper.mcp.server
    # or: agent-reach-social serve-mcp
"""

from .config import ScraperSettings
from .engine import ScrapeEngine
from .exceptions import (
    DependencyError,
    DownloadError,
    SessionError,
    SocialScraperError,
    StorageError,
    UnsupportedPlatformError,
)
from .models import (
    ALL_KINDS,
    Manifest,
    MediaAsset,
    Platform,
    RawPayload,
    Resource,
    ResourceKind,
    ScrapeBundle,
    ScrapeRequest,
    ScrapeResult,
    parse_kinds,
)

__version__ = "1.0.0"

__all__ = [
    "ALL_KINDS",
    "DependencyError",
    "DownloadError",
    "Manifest",
    "MediaAsset",
    "Platform",
    "RawPayload",
    "Resource",
    "ResourceKind",
    "SCRAPE_VERSION",
    "ScrapeBundle",
    "ScrapeEngine",
    "ScrapeRequest",
    "ScrapeResult",
    "ScraperSettings",
    "SessionError",
    "SocialScraperError",
    "StorageError",
    "UnsupportedPlatformError",
    "__version__",
    "parse_kinds",
]

SCRAPE_VERSION = __version__
