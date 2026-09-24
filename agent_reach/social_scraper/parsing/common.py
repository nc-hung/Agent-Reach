# -*- coding: utf-8 -*-
"""Tolerant helpers shared by all platform normalizers.

Platform payloads mutate often; these helpers walk them defensively instead
of trusting a fixed schema — anything unmatched is still preserved raw.
"""

import html
import json
import re
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, Iterator, List, Optional, Tuple

from ..models import MediaAsset, Platform, ResourceKind, make_resource_id

_MAX_WALK_DEPTH = 12
_WS_RE = re.compile(r"\s+")
_TAG_RE = re.compile(r"<[^>]+>")
_URL_RE = re.compile(r"^https?://", re.I)
_IMAGE_EXT = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic", ".avif")
_VIDEO_EXT = (".mp4", ".mov", ".m4v", ".webm", ".mkv", ".m3u8")


def dig(obj: Any, *paths: str, default: Any = None) -> Any:
    """Return the first non-None value reachable via dotted paths."""
    for path in paths:
        current = obj
        ok = True
        for part in path.split("."):
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                ok = False
                break
        if ok and current is not None:
            return current
    return default


def walk_dicts(obj: Any, max_depth: int = _MAX_WALK_DEPTH) -> Iterator[Tuple[str, Dict[str, Any]]]:
    """Yield ``(dotted_path, dict)`` for every dict nested in ``obj``."""

    def _walk(node: Any, path: str, depth: int) -> Iterator[Tuple[str, Dict[str, Any]]]:
        if depth > max_depth:
            return
        if isinstance(node, dict):
            yield path, node
            for key, value in node.items():
                yield from _walk(value, f"{path}.{key}" if path else str(key), depth + 1)
        elif isinstance(node, list):
            for index, value in enumerate(node):
                yield from _walk(value, f"{path}.{index}", depth + 1)

    yield from _walk(obj, "", 0)


def iter_lists(obj: Any) -> Iterator[Tuple[str, List[Any]]]:
    """Yield ``(path, list)`` for every list nested in ``obj`` (depth-capped)."""

    def _walk(node: Any, path: str, depth: int) -> Iterator[Tuple[str, List[Any]]]:
        if depth > _MAX_WALK_DEPTH:
            return
        if isinstance(node, dict):
            for key, value in node.items():
                yield from _walk(value, f"{path}.{key}" if path else str(key), depth + 1)
        elif isinstance(node, list):
            yield path, node
            for index, value in enumerate(node):
                yield from _walk(value, f"{path}.{index}", depth + 1)

    yield from _walk(obj, "", 0)


def first_str(d: Dict[str, Any], *keys: str) -> str:
    """First non-empty string among ``keys``."""
    for key in keys:
        value = d.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def first_num(d: Dict[str, Any], *keys: str) -> Optional[float]:
    """First numeric value among ``keys`` (ints preferred over floats)."""
    for key in keys:
        value = d.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            return value
        if isinstance(value, str):
            text = value.strip()
            if text.isdigit():
                return int(text)
            try:
                parsed = float(text)
            except ValueError:
                parsed = None
            if parsed is not None:
                return int(parsed) if parsed.is_integer() else parsed
            continue
        if isinstance(value, dict):
            inner = value.get("count")
            if isinstance(inner, (int, float)):
                return inner
    return None


def clean_text(value: Any, max_len: int = 8_000) -> str:
    """Normalize text: unescape HTML, collapse whitespace, cap length."""
    if value is None:
        return ""
    if isinstance(value, dict):
        value = value.get("text") or value.get("content") or ""
    if not isinstance(value, str):
        value = str(value)
    text = html.unescape(value)
    text = _TAG_RE.sub(" ", text)
    text = _WS_RE.sub(" ", text).strip()
    return text[:max_len]


def normalize_timestamp(value: Any) -> str:
    """Epoch seconds/millis or ISO string → ISO 8601 UTC (best effort)."""
    if value is None or value == "":
        return ""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        ts = float(value)
        if ts > 10_000_000_000:  # epoch milliseconds
            ts /= 1000.0
        try:
            return datetime.fromtimestamp(ts, tz=timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
        except (OverflowError, OSError, ValueError):
            return ""
    if isinstance(value, str):
        text = value.strip()
        if text.isdigit():
            return normalize_timestamp(int(text))
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            return ""
    return ""


def looks_like_url(value: Any) -> bool:
    """True for http(s) URL strings."""
    return isinstance(value, str) and bool(_URL_RE.match(value.strip()))


def guess_media_type(url: str, default: str = "unknown") -> str:
    """Infer media type from a URL extension."""
    lowered = url.lower().split("?", 1)[0]
    if lowered.endswith(_VIDEO_EXT):
        return "video"
    if lowered.endswith(_IMAGE_EXT):
        return "image"
    if lowered.endswith((".mp3", ".m4a", ".aac", ".wav", ".ogg")):
        return "audio"
    return default


def make_asset(url: str, media_type: str = "unknown", referer: str = "") -> MediaAsset:
    """Build a MediaAsset, inferring the type from the URL when unknown."""
    if media_type == "unknown":
        media_type = guess_media_type(url)
    return MediaAsset(url=url, media_type=media_type, referer=referer)


def collect_keyed_urls(
    obj: Any,
    *,
    video_keys: Iterable[str],
    image_keys: Iterable[str],
    max_assets: int = 24,
) -> List[MediaAsset]:
    """Recursively collect media URLs stored under well-known keys."""
    video_keys = tuple(video_keys)
    image_keys = tuple(image_keys)
    assets: List[MediaAsset] = []
    seen: set = set()

    for path, node in walk_dicts(obj):
        for key, value in node.items():
            if not looks_like_url(value):
                continue
            url = value.strip()
            if url in seen:
                continue
            if key in video_keys:
                assets.append(make_asset(url, "video"))
                seen.add(url)
            elif key in image_keys:
                assets.append(make_asset(url, "image"))
                seen.add(url)
            if len(assets) >= max_assets:
                return assets
        del path
    return assets


def numeric_metrics(
    obj: Any,
    key_map: Dict[str, str],
    max_depth: int = 8,
) -> Dict[str, float]:
    """Harvest known metric keys anywhere inside a payload.

    ``key_map`` maps payload key → output name, e.g.
    ``{"like_count": "likes", "comment_count": "comments"}``.
    """
    metrics: Dict[str, float] = {}
    for path, node in walk_dicts(obj, max_depth=max_depth):
        del path
        for key, out in key_map.items():
            if out in metrics:
                continue
            value = first_num(node, key)
            if value is not None:
                metrics[out] = int(value) if float(value).is_integer() else value
    return metrics


def resource(
    platform: Platform,
    kind: ResourceKind,
    native_id: str,
    *,
    url: str = "",
    handle: str = "",
    author: str = "",
    text: str = "",
    created_at: str = "",
    media: Optional[List[MediaAsset]] = None,
    metrics: Optional[Dict[str, Any]] = None,
    extra: Optional[Dict[str, Any]] = None,
    raw_file: str = "",
    raw_pointer: str = "",
) -> Any:
    """Build a Resource with a deterministic id and a raw back-reference."""
    from ..models import RawRef, Resource

    seed = native_id or f"{url or text or author}|{created_at}"
    return Resource(
        id=make_resource_id(platform, seed, kind),
        platform=platform,
        kind=kind,
        url=url,
        native_id=native_id or seed,
        handle=handle,
        author=author,
        text=text,
        created_at=created_at,
        media=media or [],
        metrics=metrics or {},
        extra=extra or {},
        raw_ref=RawRef(file=raw_file, pointer=raw_pointer),
    )


def find_first(
    obj: Any,
    predicate: Callable[[Dict[str, Any]], bool],
    max_depth: int = 10,
) -> Optional[Dict[str, Any]]:
    """First dict in the payload satisfying ``predicate``."""
    for _path, node in walk_dicts(obj, max_depth=max_depth):
        try:
            if predicate(node):
                return node
        except Exception:
            continue
    return None


def json_size(data: Any) -> int:
    """Approximate serialized size of a payload (bytes)."""
    try:
        return len(json.dumps(data, ensure_ascii=False))
    except (TypeError, ValueError):
        return 0
