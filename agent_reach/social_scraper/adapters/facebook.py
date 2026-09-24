# -*- coding: utf-8 -*-
"""Facebook adapter — timeline, videos, events, lives via the user's session."""

from typing import List, Optional, Tuple
from urllib.parse import parse_qs, urlparse

from ..interfaces import PayloadNormalizer
from ..models import ApiFetch, Platform, ResourceKind, ScrapeRequest
from ..parsing.facebook import FacebookNormalizer
from .base import PlaywrightScraperBase

_RESERVED = {
    "pages", "people", "pg", "profile.php", "watch", "marketplace", "groups",
    "gaming", "reel", "reels", "help", "login", "recover", "posts", "videos",
    "photos", "about", "events", "community", "reviews", "timeline", "live",
    "friends", "photos_of", "developer", "settings", "home", "menu",
}


class FacebookScraper(PlaywrightScraperBase):
    """Facebook pages / profiles / users."""

    platform = Platform.FACEBOOK
    display_name = "Facebook"
    hosts = ("facebook.com", "fb.com", "fb.watch")
    session_cookie = "c_user"
    login_page = "https://www.facebook.com/login"
    POST_IS_VIDEO = True

    # ------------------------------------------------------------------ #
    # URL routing
    # ------------------------------------------------------------------ #
    @classmethod
    def can_handle(cls, url: str) -> bool:
        return cls._matches_host(url)

    @classmethod
    def target_of(cls, url: str) -> str:
        """Extract page/user handle (``profile.php?id`` → ``id_<id>``)."""
        parsed = urlparse(url)
        if parsed.path.rstrip("/").endswith("/profile.php"):
            user_id = (parse_qs(parsed.query).get("id") or [""])[0]
            return f"id_{user_id}" if user_id else ""
        segments = [s for s in parsed.path.split("/") if s]
        if not segments:
            return ""
        if segments[0] in {"pages", "people", "pg"} and len(segments) > 1:
            segments = segments[1:]
        head = segments[0]
        if head.lower() in _RESERVED:
            return ""
        return head

    def login_url(self, handle: str) -> str:
        return self.login_page

    @staticmethod
    def profile_url(handle: str) -> str:
        """Reconstruct the canonical profile URL for a stored handle."""
        if handle.startswith("id_"):
            return f"https://www.facebook.com/profile.php?id={handle[3:]}"
        return f"https://www.facebook.com/{handle}"

    # ------------------------------------------------------------------ #
    # Capture policy
    # ------------------------------------------------------------------ #
    def classify(self, url: str) -> Optional[str]:
        lowered = url.lower()
        if "live_video" in lowered or "/live/" in lowered:
            return "lives"
        if "/events" in lowered:
            return "events"
        if "/videos" in lowered or "video_id" in lowered:
            return "videos"
        if "/comment" in lowered:
            return "comments"
        if "/story" in lowered or "/posts/" in lowered:
            return "posts"
        if "graphql" in lowered or "/api/" in lowered or "page_content" in lowered:
            return None  # ambiguous → capture into the active entry bucket
        return None

    # ------------------------------------------------------------------ #
    # Navigation
    # ------------------------------------------------------------------ #
    def entry_urls(self, handle: str, request: ScrapeRequest) -> List[Tuple[str, str]]:
        kinds = set(request.kinds)
        base = self.profile_url(handle)
        entries: List[Tuple[str, str]] = [("posts", base)]
        if ResourceKind.VIDEO in kinds and not handle.startswith("id_"):
            entries.append(("videos", f"{base}/videos"))
        if ResourceKind.EVENT in kinds and not handle.startswith("id_"):
            entries.append(("events", f"{base}/events"))
        return entries

    def extra_fetches(self, handle: str) -> List[ApiFetch]:
        return []

    def build_normalizer(self, normalizer: Optional[PayloadNormalizer]) -> PayloadNormalizer:
        return normalizer or FacebookNormalizer()
