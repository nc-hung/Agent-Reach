# -*- coding: utf-8 -*-
"""TikTok adapter — profile, videos/photo posts, live rooms."""

from typing import List, Optional, Tuple
from urllib.parse import urlparse

from ..interfaces import PayloadNormalizer
from ..models import ApiFetch, Platform, ResourceKind, ScrapeRequest
from ..parsing.tiktok import TikTokNormalizer
from .base import PlaywrightScraperBase

_RESERVED = {"discover", "following", "foryou", "live", "upload", "login", "api"}


class TikTokScraper(PlaywrightScraperBase):
    """TikTok profiles and their feeds."""

    platform = Platform.TIKTOK
    display_name = "TikTok"
    hosts = ("tiktok.com",)
    session_cookie = "sessionid"
    login_page = "https://www.tiktok.com/login"
    #: every TikTok feed item is a video post — `kinds=posts` includes them.
    VIDEO_IS_POST = True

    # ------------------------------------------------------------------ #
    # URL routing
    # ------------------------------------------------------------------ #
    @classmethod
    def can_handle(cls, url: str) -> bool:
        return cls._matches_host(url)

    @classmethod
    def target_of(cls, url: str) -> str:
        """Extract ``@handle`` from the first path segment."""
        parsed = urlparse(url)
        segments = [s for s in parsed.path.split("/") if s]
        if not segments:
            return ""
        head = segments[0]
        if head.startswith("@"):
            return head[1:]
        if head.lower() in _RESERVED:
            return ""
        return head

    def login_url(self, handle: str) -> str:
        return self.login_page

    # ------------------------------------------------------------------ #
    # Capture policy
    # ------------------------------------------------------------------ #
    def classify(self, url: str) -> Optional[str]:
        lowered = url.lower()
        if "user/detail" in lowered:
            return "profile"
        if "item_list" in lowered or "/api/post" in lowered:
            return "videos"
        if "webcast" in lowered or "/live" in lowered:
            return "lives"
        if "comment" in lowered:
            return "comments"
        return None  # other /api/ endpoints → active entry bucket

    # ------------------------------------------------------------------ #
    # Navigation
    # ------------------------------------------------------------------ #
    def entry_urls(self, handle: str, request: ScrapeRequest) -> List[Tuple[str, str]]:
        entries: List[Tuple[str, str]] = [
            ("posts", f"https://www.tiktok.com/@{handle}")
        ]
        if ResourceKind.LIVE in set(request.kinds):
            entries.append(("lives", f"https://www.tiktok.com/@{handle}/live"))
        return entries

    def extra_fetches(self, handle: str) -> List[ApiFetch]:
        """Direct user-detail fetch — works anonymously for public profiles."""
        return [
            ApiFetch(
                bucket="profile",
                url=f"https://www.tiktok.com/api/user/detail/?uniqueId={handle}",
                headers={"accept": "application/json"},
            )
        ]

    def build_normalizer(self, normalizer: Optional[PayloadNormalizer]) -> PayloadNormalizer:
        return normalizer or TikTokNormalizer()
