# -*- coding: utf-8 -*-
"""Domain models for the social scraper.

Everything the platforms return is preserved: ``RawPayload`` keeps the exact
JSON that was served, while ``Resource`` is the normalized, queryable view
(posts / videos / lives / events / profile …) exposed over MCP.
"""

import dataclasses
import hashlib
from dataclasses import dataclass, field, fields
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

SCHEMA_VERSION = 1


class Platform(str, Enum):
    """Supported platforms."""

    FACEBOOK = "facebook"
    INSTAGRAM = "instagram"
    TIKTOK = "tiktok"


class ResourceKind(str, Enum):
    """Kinds of resources extracted from a target."""

    PROFILE = "profile"
    POST = "post"
    VIDEO = "video"
    LIVE = "live"
    EVENT = "event"
    STORY = "story"
    COMMENT = "comment"
    OTHER = "other"


ALL_KINDS: Tuple[ResourceKind, ...] = tuple(ResourceKind)

#: user-facing alias → kind (CLI / MCP `kinds` argument)
_KIND_WORDS: Dict[str, ResourceKind] = {
    "profile": ResourceKind.PROFILE,
    "profiles": ResourceKind.PROFILE,
    "post": ResourceKind.POST,
    "posts": ResourceKind.POST,
    "video": ResourceKind.VIDEO,
    "videos": ResourceKind.VIDEO,
    "live": ResourceKind.LIVE,
    "lives": ResourceKind.LIVE,
    "event": ResourceKind.EVENT,
    "events": ResourceKind.EVENT,
    "story": ResourceKind.STORY,
    "stories": ResourceKind.STORY,
    "comment": ResourceKind.COMMENT,
    "comments": ResourceKind.COMMENT,
    "other": ResourceKind.OTHER,
}


def iso_now() -> str:
    """Current UTC time as ``YYYY-MM-DDTHH:MM:SSZ``."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def session_stamp() -> str:
    """Filesystem-safe, lexicographically sortable UTC session stamp."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")


def parse_kinds(value: Any = None) -> Tuple[ResourceKind, ...]:
    """Parse ``kinds`` from None / "all" / "posts,videos" / iterable.

    Raises ``ValueError`` on unknown words so bad input fails loudly.
    """
    if value is None:
        return ALL_KINDS
    if isinstance(value, str):
        parts = [p.strip() for p in value.split(",") if p.strip()]
    elif isinstance(value, (list, tuple, set)):
        parts = [str(p).strip() for p in value]
    else:
        parts = [str(value).strip()]
    if not parts or any(p.lower() == "all" for p in parts):
        return ALL_KINDS
    kinds: List[ResourceKind] = []
    for part in parts:
        kind = _KIND_WORDS.get(part.lower())
        if kind is None:
            raise ValueError(
                f"Unknown kind '{part}'. Use one of: "
                + ", ".join(sorted(_KIND_WORDS))
                + " (or 'all')"
            )
        if kind not in kinds:
            kinds.append(kind)
    return tuple(kinds)


def make_resource_id(platform: Platform, native_id: str, kind: ResourceKind) -> str:
    """Deterministic short id so re-scrapes stay idempotent."""
    seed = f"{platform.value}:{kind.value}:{native_id}"
    return hashlib.sha1(seed.encode("utf-8")).hexdigest()[:16]


def to_jsonable(obj: Any) -> Any:
    """Recursively convert dataclasses / enums / paths to JSON-safe values."""
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, Path):
        return str(obj)
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: to_jsonable(getattr(obj, f.name)) for f in fields(obj)}
    if isinstance(obj, dict):
        return {str(k): to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_jsonable(v) for v in obj]
    return obj


@dataclass
class MediaAsset:
    """A downloadable media file referenced by a resource."""

    url: str
    media_type: str = "unknown"  # image | video | audio | unknown
    resource_id: str = ""
    local_path: str = ""
    sha256: str = ""
    size_bytes: int = 0
    error: str = ""
    referer: str = ""


@dataclass
class RawRef:
    """Pointer back to the exact payload a resource was parsed from."""

    file: str = ""  # path relative to the session dir, e.g. raw/0003_posts.json
    pointer: str = ""  # dotted path inside the payload ("" = whole payload)


@dataclass
class Resource:
    """One normalized item scraped from a platform."""

    id: str
    platform: Platform
    kind: ResourceKind
    url: str = ""
    native_id: str = ""
    handle: str = ""
    author: str = ""
    text: str = ""
    created_at: str = ""  # ISO 8601 UTC when known
    media: List[MediaAsset] = field(default_factory=list)
    metrics: Dict[str, Any] = field(default_factory=dict)
    extra: Dict[str, Any] = field(default_factory=dict)
    raw_ref: RawRef = field(default_factory=RawRef)
    session: str = ""  # relative session path: platform/handle/<stamp>

    @property
    def uri(self) -> str:
        """Stable MCP-style URI for this resource."""
        if self.session:
            return f"social://{self.session}/{self.id}"
        return f"social://{self.platform.value}/-/ {self.id}".replace(" ", "")


@dataclass
class RawPayload:
    """One raw JSON response captured from a platform."""

    bucket: str  # profile | posts | videos | lives | events | stories | comments | other
    source_url: str
    seq: int = 0
    data: Any = None
    file: str = ""  # filled by the backup store: raw/0001_posts.json


@dataclass
class ApiFetch:
    """An extra direct API call an adapter wants (shares the session)."""

    bucket: str
    url: str
    headers: Dict[str, str] = field(default_factory=dict)


@dataclass
class ScrapeRequest:
    """Everything the engine needs to scrape one target."""

    url: str
    kinds: Tuple[ResourceKind, ...] = ALL_KINDS
    max_items: int = 100
    max_scroll: int = 15
    download_media: bool = True
    headless: bool = True
    timeout_ms: int = 45_000

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ScrapeRequest":
        """Build a request from loose (e.g. MCP tool) arguments."""
        defaults = cls(url=str(data.get("url", "")))
        return cls(
            url=str(data.get("url", defaults.url)),
            kinds=parse_kinds(data.get("kinds")),
            max_items=int(data.get("max_items", defaults.max_items)),
            max_scroll=int(data.get("max_scroll", defaults.max_scroll)),
            download_media=bool(data.get("download_media", defaults.download_media)),
            headless=bool(data.get("headless", defaults.headless)),
            timeout_ms=int(data.get("timeout_ms", defaults.timeout_ms)),
        )


@dataclass
class ScrapeBundle:
    """Result of one scrape run before/while it is persisted."""

    platform: Platform
    handle: str
    source_url: str
    profile: Optional[Resource] = None
    resources: List[Resource] = field(default_factory=list)
    raws: List[RawPayload] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    stats: Dict[str, Any] = field(default_factory=dict)
    started_at: str = field(default_factory=iso_now)
    finished_at: str = field(default_factory=iso_now)


@dataclass
class Manifest:
    """Inventory of one backup session (files, counts, digests)."""

    session: str  # relative path: platform/handle/<stamp>
    platform: Platform
    handle: str
    source_url: str
    started_at: str = ""
    finished_at: str = ""
    schema_version: int = SCHEMA_VERSION
    counts: Dict[str, int] = field(default_factory=dict)
    files: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    media: Dict[str, int] = field(default_factory=dict)
    stats: Dict[str, Any] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """JSON-safe representation."""
        return to_jsonable(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Manifest":
        """Rebuild a manifest from its JSON representation."""
        return cls(
            session=str(data.get("session", "")),
            platform=Platform(data.get("platform", Platform.FACEBOOK)),
            handle=str(data.get("handle", "")),
            source_url=str(data.get("source_url", "")),
            started_at=str(data.get("started_at", "")),
            finished_at=str(data.get("finished_at", "")),
            schema_version=int(data.get("schema_version", SCHEMA_VERSION)),
            counts=dict(data.get("counts") or {}),
            files=dict(data.get("files") or {}),
            media=dict(data.get("media") or {}),
            stats=dict(data.get("stats") or {}),
            errors=list(data.get("errors") or []),
        )


@dataclass
class ScrapeResult:
    """Engine output for one scrape run (safe to serialize to CLI/MCP)."""

    session: str  # relative backup session path
    platform: Platform
    handle: str
    source_url: str
    counts: Dict[str, int] = field(default_factory=dict)
    raw_count: int = 0
    media: Dict[str, int] = field(default_factory=dict)
    manifest_path: str = ""
    resources: List[Resource] = field(default_factory=list)  # preview sample
    errors: List[str] = field(default_factory=list)
    duration_ms: int = 0

    def to_dict(self) -> Dict[str, Any]:
        """JSON-safe representation."""
        return to_jsonable(self)


def media_from_dict(data: Dict[str, Any]) -> MediaAsset:
    """Rebuild a MediaAsset from JSON."""
    known = {f.name for f in fields(MediaAsset)}
    return MediaAsset(**{k: v for k, v in data.items() if k in known})


def resource_from_dict(data: Dict[str, Any]) -> Resource:
    """Rebuild a Resource from its JSON representation."""
    raw_ref = data.get("raw_ref") or {}
    return Resource(
        id=str(data.get("id", "")),
        platform=Platform(data.get("platform", Platform.FACEBOOK)),
        kind=ResourceKind(data.get("kind", ResourceKind.OTHER)),
        url=str(data.get("url", "")),
        native_id=str(data.get("native_id", "")),
        handle=str(data.get("handle", "")),
        author=str(data.get("author", "")),
        text=str(data.get("text", "")),
        created_at=str(data.get("created_at", "")),
        media=[media_from_dict(m) for m in (data.get("media") or [])],
        metrics=dict(data.get("metrics") or {}),
        extra=dict(data.get("extra") or {}),
        raw_ref=RawRef(
            file=str(raw_ref.get("file", "")),
            pointer=str(raw_ref.get("pointer", "")),
        ),
        session=str(data.get("session", "")),
    )


def resources_from_lines(lines: Sequence[str]) -> List[Resource]:
    """Parse an ``index.jsonl`` document into resources (skipping bad lines)."""
    import json

    resources: List[Resource] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            resources.append(resource_from_dict(json.loads(line)))
        except (ValueError, KeyError, TypeError):
            continue
    return resources
