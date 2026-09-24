# -*- coding: utf-8 -*-
"""Instagram normalizer: raw payloads → normalized resources."""

from typing import Any, Dict, List, Optional

from ..interfaces import PayloadNormalizer
from ..models import Platform, RawPayload, Resource, ResourceKind
from .common import (
    clean_text,
    dig,
    find_first,
    first_num,
    first_str,
    make_asset,
    normalize_timestamp,
    numeric_metrics,
    resource,
    walk_dicts,
)

_HANDLE = "instagram"
_BASE = "https://www.instagram.com"

_METRIC_KEYS = {
    "like_count": "likes",
    "comment_count": "comments",
    "play_count": "plays",
    "video_view_count": "views",
    "media_count": "posts",
    "viewer_count": "viewers",
}


def _looks_profile(node: Dict[str, Any]) -> bool:
    if not first_str(node, "username"):
        return False
    profile_keys = (
        "biography", "follower_count", "edge_followed_by", "edge_follow",
        "media_count", "is_business", "is_professional_account",
    )
    return any(key in node for key in profile_keys)


def _item_media(item: Dict[str, Any], handle: str) -> List:
    """Extract media assets (images/videos, incl. carousels) from an item."""
    assets = []
    code = first_str(item, "code", "shortcode")
    referer = f"{_BASE}/{code}/" if code else ""

    def _from(item_node: Dict[str, Any]) -> None:
        video_versions = item_node.get("video_versions")
        if isinstance(video_versions, list):
            for version in video_versions:
                if isinstance(version, dict) and version.get("url"):
                    assets.append(make_asset(version["url"], "video", referer))
                    break
        image_versions = dig(item_node, "image_versions2.candidates")
        if isinstance(image_versions, list):
            for candidate in image_versions:
                url = candidate.get("url") if isinstance(candidate, dict) else None
                if isinstance(url, str) and url.startswith("http"):
                    assets.append(make_asset(url, "image", referer))
                    break
        display = first_str(item_node, "display_url", "thumbnail_src")
        if display and not assets:
            assets.append(make_asset(display, "image", referer))

    _from(item)
    carousel = item.get("carousel_media")
    if isinstance(carousel, list):
        for child in carousel:
            if isinstance(child, dict):
                _from(child)
    del handle
    return assets


class InstagramNormalizer(PayloadNormalizer):
    """Best-effort normalization of any Instagram JSON payload."""

    def __init__(self, handle: str = ""):
        self.handle = handle

    def normalize(self, payload: RawPayload) -> List[Resource]:
        data = payload.data
        if data is None:
            return []
        out: List[Resource] = []
        out.extend(self._profile(data, payload))
        out.extend(self._items(data, payload))
        out.extend(self._lives(data, payload))
        return [r for r in out if r is not None]

    # ------------------------------------------------------------------ #
    # profile
    # ------------------------------------------------------------------ #
    def _profile(self, data: Any, payload: RawPayload) -> List[Resource]:
        node = find_first(data, _looks_profile)
        if node is None:
            return []
        username = first_str(node, "username") or self.handle
        user_id = str(first_num(node, "id", "pk") or username)
        bio = clean_text(first_str(node, "biography", "bio"))
        metrics = {
            "followers": first_num(node, "follower_count")
            or dig(node, "edge_followed_by.count"),
            "following": first_num(node, "following_count")
            or dig(node, "edge_follow.count"),
            "posts": first_num(node, "media_count")
            or dig(node, "edge_owner_to_timeline_media.count"),
            "verified": bool(node.get("is_verified")),
        }
        metrics = {k: v for k, v in metrics.items() if v is not None}
        media = []
        avatar = first_str(node, "profile_pic_url_hd", "profile_pic_url")
        if avatar:
            media = [make_asset(avatar, "image")]
        res = resource(
            Platform.INSTAGRAM,
            ResourceKind.PROFILE,
            f"user-{user_id}",
            url=f"{_BASE}/{username}/",
            handle=username,
            author=first_str(node, "full_name") or username,
            text=bio,
            media=media,
            metrics=metrics,
            extra={
                "private": bool(node.get("is_private")),
                "category": first_str(node, "category"),
            },
            raw_file=payload.file,
        )
        return [res]

    # ------------------------------------------------------------------ #
    # feed items (posts / reels / stories)
    # ------------------------------------------------------------------ #
    def _items(self, data: Any, payload: RawPayload) -> List[Resource]:
        kind = self._item_kind(payload.bucket, payload.source_url)
        items: List[Dict[str, Any]] = []
        for _path, lst in _iter_item_lists(data):
            for node in lst:
                if isinstance(node, dict) and self._looks_item(node):
                    items.append(node)
        out = []
        for item in items:
            res = self._item(item, kind, payload)
            if res is not None:
                out.append(res)
        return out

    @staticmethod
    def _looks_item(node: Dict[str, Any]) -> bool:
        media_keys = ("media_type", "image_versions2", "video_versions", "carousel_media")
        return bool(first_num(node, "pk", "id")) and any(k in node for k in media_keys)

    @staticmethod
    def _item_kind(bucket: str, source_url: str) -> ResourceKind:
        lowered = source_url.lower()
        if bucket == "stories" or "story" in lowered:
            return ResourceKind.STORY
        if bucket == "videos" or "reel" in lowered:
            return ResourceKind.VIDEO
        if bucket == "lives":
            return ResourceKind.LIVE
        return ResourceKind.POST

    def _item(
        self,
        item: Dict[str, Any],
        kind: ResourceKind,
        payload: RawPayload,
    ) -> Optional[Resource]:
        native = str(first_num(item, "pk", "id") or "")
        if not native:
            return None
        code = first_str(item, "code", "shortcode")
        url = f"{_BASE}/{code}/" if code else f"{_BASE}/{self.handle}/"
        caption = dig(item, "caption.text") or clean_text(item.get("caption"))
        author = first_str(item, "user", "owner") or self.handle
        if isinstance(item.get("user"), dict):
            author = first_str(item["user"], "username") or author
        if isinstance(item.get("owner"), dict):
            author = first_str(item["owner"], "username") or author
        created = normalize_timestamp(
            first_num(item, "taken_at", "taken_at_posted", "timestamp", "created_at")
        )
        metrics = numeric_metrics(item, _METRIC_KEYS, max_depth=4)
        extra = {
            "product_type": first_str(item, "product_type"),
            "media_type": first_num(item, "media_type"),
            "like_count": first_num(item, "like_count"),
        }
        extra = {k: v for k, v in extra.items() if v is not None}
        return resource(
            Platform.INSTAGRAM,
            kind,
            native,
            url=url,
            handle=self.handle,
            author=author,
            text=clean_text(caption),
            created_at=created,
            media=_item_media(item, self.handle),
            metrics=metrics,
            extra=extra,
            raw_file=payload.file,
        )

    # ------------------------------------------------------------------ #
    # lives
    # ------------------------------------------------------------------ #
    def _lives(self, data: Any, payload: RawPayload) -> List[Resource]:
        out: List[Resource] = []
        for _path, node in walk_dicts(data, max_depth=8):
            if not self._looks_live(node):
                continue
            native = str(first_num(node, "id", "broadcast_id", "pk") or "")
            if not native:
                continue
            title = clean_text(
                first_str(node, "title", "live_video_title", "broadcast_title")
            )
            metrics = {
                k: v
                for k, v in {
                    "viewers": first_num(
                        node, "viewer_count", "user_count", "total_viewer_count"
                    ),
                    "likes": first_num(node, "like_count"),
                }.items()
                if v is not None
            }
            out.append(
                resource(
                    Platform.INSTAGRAM,
                    ResourceKind.LIVE,
                    native,
                    url=f"{_BASE}/{self.handle}/live/",
                    handle=self.handle,
                    text=title,
                    created_at=normalize_timestamp(
                        first_num(node, "start_time", "creation_time", "broadcast_creation_time")
                    ),
                    metrics=metrics,
                    extra={
                        "status": first_str(node, "broadcast_status", "status"),
                    },
                    raw_file=payload.file,
                )
            )
        return out

    @staticmethod
    def _looks_live(node: Dict[str, Any]) -> bool:
        live_markers = (
            "broadcast_id", "broadcast_status", "live_video_title",
            "broadcast_title", "total_viewer_count",
        )
        return any(m in node for m in live_markers)


def _iter_item_lists(data: Any):
    """Yield lists that look like feeds of Instagram media items.

    Handles both plain ``items`` arrays and GraphQL ``edges`` whose entries
    wrap the media in a ``node`` object.
    """
    media_keys = (
        "media_type", "image_versions2", "video_versions", "carousel_media",
    )
    for path, lst in walk_lists(data):
        if not lst:
            continue
        dict_items = [x for x in lst if isinstance(x, dict)]
        if not dict_items:
            continue
        sample = dict_items[0]
        node = sample.get("node")
        if isinstance(node, dict) and any(k in node for k in media_keys):
            unwrapped = [
                x["node"] for x in dict_items if isinstance(x.get("node"), dict)
            ]
            if unwrapped:
                yield path, unwrapped
            continue
        if any(k in sample for k in media_keys):
            yield path, dict_items


def walk_lists(obj: Any):
    """Yield ``(path, list)`` pairs for nested lists (thin wrapper)."""
    from .common import iter_lists

    yield from iter_lists(obj)
