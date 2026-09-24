# -*- coding: utf-8 -*-
"""Instagram adapter — profile, feed, reels, stories, lives."""

from typing import List, Optional, Tuple
from urllib.parse import urlparse

from ..interfaces import PayloadNormalizer
from ..models import ApiFetch, Platform, ScrapeRequest
from ..parsing.instagram import InstagramNormalizer
from .base import PlaywrightScraperBase

_RESERVED = {
    "explore", "p", "reel", "reels", "stories", "direct", "accounts", "about",
    "developer", "legal", "login", "signup", "web", "api", "challenge",
}

#: Public app id used by instagram.com itself for web_profile_info.
_WEB_APP_ID = "936619743392459"


class InstagramScraper(PlaywrightScraperBase):
    """Instagram users and profile pages."""

    platform = Platform.INSTAGRAM
    display_name = "Instagram"
    hosts = ("instagram.com", "instagr.am")
    session_cookie = "sessionid"
    login_page = "https://www.instagram.com/accounts/login/"
    POST_IS_VIDEO = True

    # ------------------------------------------------------------------ #
    # URL routing
    # ------------------------------------------------------------------ #
    @classmethod
    def can_handle(cls, url: str) -> bool:
        return cls._matches_host(url)

    @classmethod
    def target_of(cls, url: str) -> str:
        """Extract the username (first non-reserved path segment)."""
        parsed = urlparse(url)
        segments = [s for s in parsed.path.split("/") if s]
        for segment in segments:
            head = segment
            if head.lower() in _RESERVED:
                continue
            return head
        return ""

    def login_url(self, handle: str) -> str:
        return self.login_page

    # ------------------------------------------------------------------ #
    # Capture policy
    # ------------------------------------------------------------------ #
    def classify(self, url: str) -> Optional[str]:
        lowered = url.lower()
        if "web_profile_info" in lowered:
            return "profile"
        if "clips" in lowered or "reels" in lowered:
            return "videos"
        if "story" in lowered:
            return "stories"
        if "live" in lowered:
            return "lives"
        if "comment" in lowered:
            return "comments"
        return None  # graphql / api/v1 → active entry bucket

    # ------------------------------------------------------------------ #
    # Navigation
    # ------------------------------------------------------------------ #
    def entry_urls(self, handle: str, request: ScrapeRequest) -> List[Tuple[str, str]]:
        del request
        return [("posts", f"https://www.instagram.com/{handle}/")]

    def extra_fetches(self, handle: str) -> List[ApiFetch]:
        """Direct profile fetch — reliable even when the SPA hides it."""
        return [
            ApiFetch(
                bucket="profile",
                url=(
                    "https://www.instagram.com/api/v1/users/"
                    f"web_profile_info/?username={handle}"
                ),
                headers={
                    "x-ig-app-id": _WEB_APP_ID,
                    "x-requested-with": "XMLHttpRequest",
                    "accept": "application/json",
                },
            )
        ]

    def build_normalizer(self, normalizer: Optional[PayloadNormalizer]) -> PayloadNormalizer:
        return normalizer or InstagramNormalizer()
