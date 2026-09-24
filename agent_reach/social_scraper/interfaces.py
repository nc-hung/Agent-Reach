# -*- coding: utf-8 -*-
"""Abstract contracts of the social scraper (SOLID seams).

- ``ContentScraper``   — one platform: URL routing, capture policy, scraping.
- ``PayloadNormalizer``— raw payload → normalized ``Resource`` list.
- ``MediaDownloader``  — stream media files to disk.
- ``BackupStore``      — organized, digested backup sessions.
- ``ResourceQuery``    — read side consumed by CLI and MCP.

The engine and the MCP server only depend on these abstractions; concrete
implementations are injected (Dependency Inversion).
"""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple
from urllib.parse import urlparse

from .models import (
    ALL_KINDS,
    ApiFetch,
    Manifest,
    MediaAsset,
    Platform,
    RawPayload,
    Resource,
    ResourceKind,
    ScrapeBundle,
    ScrapeRequest,
)


class ContentScraper(ABC):
    """One platform adapter (open for extension, closed for modification)."""

    platform: Platform
    display_name: str = ""
    hosts: Tuple[str, ...] = ()
    session_cookie: str = ""
    login_page: str = ""
    #: TikTok-style platforms whose feed items *are* videos → allow `posts`.
    VIDEO_IS_POST: bool = False
    #: Feed posts that embed video should also match a `videos` request.
    POST_IS_VIDEO: bool = False

    # ------------------------------------------------------------------ #
    # URL routing
    # ------------------------------------------------------------------ #
    @classmethod
    @abstractmethod
    def can_handle(cls, url: str) -> bool:
        """Return True when ``url`` belongs to this platform."""

    @classmethod
    @abstractmethod
    def target_of(cls, url: str) -> str:
        """Extract the handle / username / page id from ``url`` (""=unknown)."""

    @staticmethod
    def _host(url: str) -> str:
        return (urlparse(url).hostname or "").lower()

    @classmethod
    def _matches_host(cls, url: str) -> bool:
        host = ContentScraper._host(url)
        return any(host == h or host.endswith("." + h) for h in cls.hosts)

    def login_url(self, handle: str) -> str:
        """Where ``agent-reach-social login`` sends the user."""
        return self.login_page

    # ------------------------------------------------------------------ #
    # Capture policy: which responses are worth keeping, and where they go
    # ------------------------------------------------------------------ #
    def matches_host(self, url: str) -> bool:
        """True when the URL is served by the platform itself."""
        return self._matches_host(url)

    @staticmethod
    def is_noise(url: str) -> bool:
        """Telemetry endpoints are dropped — everything else JSON is kept."""
        lowered = url.lower()
        markers = (
            "analytics", "telemetry", "beacon", "tracing", "sentry",
            "doubleclick", "/perf", "logging.", "batbi", "fbrpc",
        )
        return any(m in lowered for m in markers)

    def classify(self, url: str) -> Optional[str]:
        """Map a response URL to a bucket; ``None`` → caller decides."""
        return None

    # ------------------------------------------------------------------ #
    # Scraping flow
    # ------------------------------------------------------------------ #
    def entry_urls(self, handle: str, request: ScrapeRequest) -> List[Tuple[str, str]]:
        """(bucket, url) pages to visit — default: the profile page."""
        return [("posts", f"https://www.{self.platform.value}/@{handle}")]

    def extra_fetches(self, handle: str) -> List[ApiFetch]:
        """Direct API calls issued through the same logged-in session."""
        return []

    async def extract_inline(self, page: Any, bucket: str) -> List[Tuple[str, Any]]:
        """Parse JSON embedded in ``<script>`` tags of the current page."""
        return []

    @abstractmethod
    async def scrape(self, request: ScrapeRequest, session: Any) -> ScrapeBundle:
        """Scrape one target and return everything captured."""

    # ------------------------------------------------------------------ #
    # Selection semantics
    # ------------------------------------------------------------------ #
    def select(
        self,
        resources: Iterable[Resource],
        kinds: Sequence[ResourceKind],
    ) -> List[Resource]:
        """Filter resources by requested kinds (profile is carried separately)."""
        requested = set(kinds) if kinds else set(ALL_KINDS)
        selected: List[Resource] = []
        seen: Set[str] = set()
        for res in resources:
            if res.kind is ResourceKind.PROFILE:
                continue
            keep = res.kind in requested
            if not keep and self.VIDEO_IS_POST and res.kind is ResourceKind.VIDEO:
                keep = ResourceKind.POST in requested
            if (
                not keep
                and self.POST_IS_VIDEO
                and res.kind is ResourceKind.POST
                and ResourceKind.VIDEO in requested
            ):
                keep = any(m.media_type == "video" for m in res.media)
            if keep and res.id not in seen:
                seen.add(res.id)
                selected.append(res)
        return selected

    def preview(self, resources: Iterable[Resource], limit: int = 20) -> List[Resource]:
        """First ``limit`` resources (platforms serve newest first)."""
        out: List[Resource] = []
        for res in resources:
            if len(out) >= limit:
                break
            out.append(res)
        return out


class PayloadNormalizer(ABC):
    """Turns one raw payload into normalized resources (best effort)."""

    @abstractmethod
    def normalize(self, payload: RawPayload) -> List[Resource]:
        """Return zero or more resources found in ``payload``."""


class MediaDownloader(ABC):
    """Downloads media assets into a destination directory."""

    @abstractmethod
    def download(self, assets: Sequence[MediaAsset], dest_dir: Path) -> Dict[str, int]:
        """Stream assets to disk in place; returns summary counters."""


class BackupStore(ABC):
    """Organized backup sessions: raw payloads + index + manifest + media."""

    @abstractmethod
    def begin(self, platform: Platform, handle: str) -> Path:
        """Create and return a fresh session directory."""

    @abstractmethod
    def session_rel(self, session_dir: Path) -> str:
        """Relative session id (``platform/handle/<stamp>``) for URIs."""

    @abstractmethod
    def write_raws(self, session_dir: Path, raws: Sequence[RawPayload]) -> None:
        """Persist every captured payload under ``raw/``."""

    @abstractmethod
    def write_profile(self, session_dir: Path, profile: Optional[Resource]) -> None:
        """Persist the target profile document."""

    @abstractmethod
    def write_resources(self, session_dir: Path, resources: Sequence[Resource]) -> None:
        """Persist (or refresh) ``index.jsonl``."""

    @abstractmethod
    def finalize(
        self,
        session_dir: Path,
        bundle: ScrapeBundle,
        media_summary: Dict[str, int],
    ) -> Manifest:
        """Compute digests/counts and write ``manifest.json``."""

    @abstractmethod
    def read_manifest(self, session_dir: Path) -> Optional[Manifest]:
        """Load the manifest of a session when present."""


class ResourceQuery(ABC):
    """Read side used by the CLI and the MCP server."""

    @abstractmethod
    def targets(self, platform: Optional[Platform] = None) -> List[Dict[str, Any]]:
        """Known scrape targets with per-target totals."""

    @abstractmethod
    def list_resources(
        self,
        platform: Optional[Platform] = None,
        handle: Optional[str] = None,
        kinds: Optional[Sequence[ResourceKind]] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Dict[str, Any]:
        """Paginated ``{"total": n, "items": [...]}`` resource listing."""

    @abstractmethod
    def get(self, resource_id: str) -> Optional[Resource]:
        """Fetch one resource by id (newest session wins)."""

    @abstractmethod
    def get_in_session(self, session_rel: str, resource_id: str) -> Optional[Resource]:
        """Fetch one resource inside a specific session."""

    @abstractmethod
    def read_raw(self, resource: Resource, max_bytes: int = 0) -> Any:
        """Return the raw payload a resource was parsed from (best effort).

        A missing or oversize payload yields an ``{"error": ...}`` dict
        instead of raising, so callers can render it inline.
        """

    @abstractmethod
    def search(
        self,
        query: str,
        kinds: Optional[Sequence[ResourceKind]] = None,
        limit: int = 20,
    ) -> List[Resource]:
        """Case-insensitive substring search over text/author/url."""

    @abstractmethod
    def stats(self) -> Dict[str, Any]:
        """Aggregate counters over the whole backup root."""

    @abstractmethod
    def sessions(self) -> List[str]:
        """All known session ids, newest first."""
