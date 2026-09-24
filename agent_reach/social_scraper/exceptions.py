# -*- coding: utf-8 -*-
"""Domain exceptions for the social scraper subsystem.

Every message is user-facing and safe to print (scrub credentials before
logging remote errors — see agent_reach.utils.text.scrub_url_credentials).
"""


class SocialScraperError(Exception):
    """Base class for all social scraper errors."""


class DependencyError(SocialScraperError):
    """An optional dependency (playwright / mcp) is missing."""


class UnsupportedPlatformError(SocialScraperError):
    """No registered scraper can handle the given URL."""


class SessionError(SocialScraperError):
    """A browser session could not be created or logged in."""


class StorageError(SocialScraperError):
    """The backup store failed to persist data."""


class DownloadError(SocialScraperError):
    """A media asset could not be downloaded."""
