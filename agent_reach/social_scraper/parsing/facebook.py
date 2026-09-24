# -*- coding: utf-8 -*-
"""Facebook normalizer: raw payloads → normalized resources.

Facebook GraphQL shapes change often, so this normalizer walks payloads
structurally: events, live videos, feed stories and plain videos are each
recognized by marker keys and normalized best effort — anything unmatched
stays available as a raw payload.
"""

import re
from typing import Any, Dict, List, Optional

from ..interfaces import PayloadNormalizer
from ..models import Platform, RawPayload, Resource, ResourceKind
from .common import (
    clean_text,
    dig,
    first_num,
    first_str,
    make_asset,
    normalize_timestamp,
    numeric_metrics,
    resource,
    walk_dicts,
)

_BASE = "https://www.facebook.com"
_NUMERIC_ID_RE = re.compile(r"^\d{5,}$")

_VIDEO_KEYS = (
    "browser_native_hd_url",
    "browser_native_sd_url",
    "hd_src_no_h264",
    "hd_src",
    "sd_src",
    "fallback_url",
)
_IMAGE_KEYS = ("uri", "src", "url", "display_uri", "profilePic")

_METRIC_KEYS = {
    "react_count": "reactions",
    "reaction_count": "reactions",
    "like_count": "likes",
    "comment_count": "comments",
    "share_count": "shares",
    "view_count": "views",
    "play_count": "plays",
    "video_view_count": "views",
    "reaction_count_summary": "reactions",
    "favorite_count": "saves",
}

_TIME_KEYS = ("creation_time", "creation_timestamp", "publish_time", "timestamp_seconds", "timestamp")
_TEXT_KEYS = ("message", "story_attachment", "attachments", "comet_sections", "text", "title", "description")


def _message_text(node: Dict[str, Any]) -> str:
    value = node.get("message")
    if isinstance(value, dict):
        return clean_text(value.get("text") or value.get("content"))
    return clean_text(value)


def _collect_media(node: Dict[str, Any]) -> List:
    assets = []
    seen = set()
    for _path, inner in walk_dicts(node, max_depth=8):
        for key, value in inner.items():
            if not isinstance(value, str) or not value.startswith("http"):
                continue
            if value in seen:
                continue
            if key in _VIDEO_KEYS:
                assets.append(make_asset(value, "video"))
                seen.add(value)
            elif key in _IMAGE_KEYS and _is_imageish(value):
                assets.append(make_asset(value, "image"))
                seen.add(value)
            if len(assets) >= 24:
                return assets
    return assets


def _is_imageish(url: str) -> bool:
    lowered = url.lower()
    return any(
        marker in lowered
        for marker in ("fbcdn", "safe_image", ".jpg", ".png", ".webp", ".gif")
    )


def _looks_profile(node: Dict[str, Any]) -> bool:
    if not first_str(node, "name"):
        return False
    profile_markers = (
        "follower_count", "fan_count", "likes_count", "pageID", "page_id",
        "username", "category", "bio", "about",
    )
    return any(m in node for m in profile_markers)


def _looks_event(node: Dict[str, Any]) -> bool:
    event_markers = (
        "event_start_time", "event_end_time", "event_id", "start_timestamp",
        "is_online_event", "event_cover", "event_name",
    )
    if any(m in node for m in event_markers):
        return True
    return str(node.get("__typename", "")) == "Event"


def _looks_live(node: Dict[str, Any]) -> bool:
    if node.get("is_live") in (True, 1, "1"):
        return True
    status = str(node.get("broadcast_status", "")).upper()
    if status in {"ACTIVE", "IN_PROGRESS", "PUBLISHED"} and (
        "live" in str(node.get("__typename", "")).lower() or "video" in node
    ):
        return True
    typename = str(node.get("__typename", "")).lower()
    return "livevideo" in typename.replace("_", "").replace(" ", "")


def _looks_post(node: Dict[str, Any]) -> bool:
    has_time = any(key in node for key in _TIME_KEYS)
    has_text = any(key in node for key in _TEXT_KEYS)
    return has_time and has_text


class FacebookNormalizer(PayloadNormalizer):
    """Best-effort normalization of any Facebook JSON payload."""

    def __init__(self, handle: str = ""):
        self.handle = handle

    def normalize(self, payload: RawPayload) -> List[Resource]:
        data = payload.data
        if data is None:
            return []
        out: List[Resource] = []
        seen: set = set()
        for path, node in walk_dicts(data, max_depth=12):
            res = self._classify_node(node, path, payload)
            if res is None:
                continue
            key = (res.kind.value, res.native_id)
            if key in seen:
                continue
            seen.add(key)
            out.append(res)
        profile = [r for r in out if r.kind is ResourceKind.PROFILE]
        # keep at most one profile per payload
        if len(profile) > 1:
            first_id = profile[0].id
            out = [r for r in out if r.kind is not ResourceKind.PROFILE or r.id == first_id]
        return out

    # ------------------------------------------------------------------ #
    # node dispatch
    # ------------------------------------------------------------------ #
    def _classify_node(
        self, node: Dict[str, Any], path: str, payload: RawPayload
    ) -> Optional[Resource]:
        if _looks_profile(node):
            return self._profile(node, path, payload)
        if _looks_live(node):
            return self._live(node, path, payload)
        if _looks_event(node):
            return self._event(node, path, payload)
        if _looks_post(node):
            return self._post(node, path, payload)
        if payload.bucket == "videos":
            media = _collect_media(node)
            if any(a.media_type == "video" for a in media):
                return self._video(node, path, media, payload)
        return None

    # ------------------------------------------------------------------ #
    # shapes
    # ------------------------------------------------------------------ #
    def _profile(self, node: Dict[str, Any], path: str, payload: RawPayload) -> Resource:
        page_id = str(
            first_num(node, "pageID", "page_id", "id", "userID")
            or first_str(node, "pageID", "page_id", "id")
            or self.handle
        )
        username = first_str(node, "username", "vanity") or self.handle
        metrics = {
            k: v
            for k, v in {
                "followers": first_num(node, "follower_count", "fan_count"),
                "likes": first_num(node, "likes_count", "like_count"),
                "checkins": first_num(node, "checkins"),
            }.items()
            if v is not None
        }
        media = []
        avatar = first_str(node, "profilePic", "profile_picture", "picture")
        if isinstance(avatar, str) and avatar.startswith("http"):
            media = [make_asset(avatar, "image")]
        return resource(
            Platform.FACEBOOK,
            ResourceKind.PROFILE,
            f"page-{page_id}",
            url=f"{_BASE}/{username}",
            handle=username,
            author=first_str(node, "name"),
            text=clean_text(
                first_str(node, "bio", "about", "description", "short_description")
            ),
            media=media,
            metrics=metrics,
            extra={"category": first_str(node, "category", "category_name")},
            raw_file=payload.file,
            raw_pointer=path,
        )

    def _event(self, node: Dict[str, Any], path: str, payload: RawPayload) -> Resource:
        event_id = str(
            first_num(node, "event_id", "id")
            or first_str(node, "event_id", "id")
            or ""
        )
        title = clean_text(
            first_str(node, "event_name", "name", "title", "card_title")
        )
        start = normalize_timestamp(
            first_num(
                node,
                "event_start_time",
                "start_timestamp",
                "start_time",
                "start_seconds",
            )
        )
        metrics = numeric_metrics(
            node,
            {"attending_count": "attending", "interested_count": "interested",
             "maybe_count": "maybe", "share_count": "shares"},
            max_depth=4,
        )
        url = first_str(node, "url", "permalink", "event_url")
        if not url.startswith("http"):
            url = f"{_BASE}/events/{event_id}" if event_id else f"{_BASE}/{self.handle}/events"
        return resource(
            Platform.FACEBOOK,
            ResourceKind.EVENT,
            event_id or f"event-{path}",
            url=url,
            handle=self.handle,
            author=first_str(node, "host_name", "page_name") or self.handle,
            text=title,
            created_at=start,
            media=_collect_media(node)[:6],
            metrics=metrics,
            extra={
                "place": dig(node, "place.name"),
                "is_online": bool(node.get("is_online_event")),
            },
            raw_file=payload.file,
            raw_pointer=path,
        )

    def _live(self, node: Dict[str, Any], path: str, payload: RawPayload) -> Resource:
        video_id = str(
            first_num(node, "video_id", "broadcast_id", "id")
            or first_str(node, "video_id", "broadcast_id", "id")
            or ""
        )
        url = first_str(node, "permalink", "url", "share_url")
        if not url.startswith("http"):
            url = f"{_BASE}/{self.handle}/videos/{video_id}" if video_id else f"{_BASE}/{self.handle}"
        return resource(
            Platform.FACEBOOK,
            ResourceKind.LIVE,
            video_id or f"live-{path}",
            url=url,
            handle=self.handle,
            author=first_str(node, "broadcaster", "author") or self.handle,
            text=clean_text(first_str(node, "title", "message", "description")),
            created_at=normalize_timestamp(
                first_num(node, "creation_time", "publish_time", "start_time")
            ),
            media=_collect_media(node),
            metrics=numeric_metrics(
                node,
                {"viewer_count": "viewers", "live_viewers": "viewers",
                 "reaction_count": "reactions", "comment_count": "comments"},
                max_depth=5,
            ),
            extra={"status": first_str(node, "broadcast_status", "status")},
            raw_file=payload.file,
            raw_pointer=path,
        )

    def _post(self, node: Dict[str, Any], path: str, payload: RawPayload) -> Resource:
        post_id = str(
            first_str(node, "post_id", "story_fbid", "share_id")
            or first_num(node, "post_id", "story_fbid", "id")
            or ""
        )
        created = normalize_timestamp(first_num(node, *_TIME_KEYS))
        media = _collect_media(node)
        text = _message_text(node)
        if not text:
            text = clean_text(first_str(node, "title", "description", "text"))
        url = first_str(node, "share_url", "permalink", "url")
        if not url.startswith("http"):
            if post_id and _NUMERIC_ID_RE.match(post_id):
                url = f"{_BASE}/{self.handle}/posts/{post_id}"
            else:
                url = f"{_BASE}/{self.handle}"
        author = self.handle
        for key in ("author", "page", "owner", "actor"):
            node2 = node.get(key)
            if isinstance(node2, dict):
                author = first_str(node2, "name", "username", "short_name") or author
                break
        kind = (
            ResourceKind.VIDEO
            if payload.bucket == "videos" and any(m.media_type == "video" for m in media)
            else ResourceKind.POST
        )
        return resource(
            Platform.FACEBOOK,
            kind,
            post_id or f"post-{abs(hash((text, created))) % 10**12}",
            url=url,
            handle=self.handle,
            author=author,
            text=text,
            created_at=created,
            media=media,
            metrics=numeric_metrics(node, _METRIC_KEYS, max_depth=5),
            extra={
                "shares": dig(node, "share_count"),
                "reaction_types": [
                    r.get("reaction_type")
                    for r in (node.get("reaction_groups") or [])
                    if isinstance(r, dict)
                ],
            },
            raw_file=payload.file,
            raw_pointer=path,
        )

    def _video(
        self,
        node: Dict[str, Any],
        path: str,
        media: List[Any],
        payload: RawPayload,
    ) -> Resource:
        video_id = str(
            first_num(node, "video_id", "id") or first_str(node, "video_id", "id") or ""
        )
        url = first_str(node, "permalink", "url", "share_url")
        if not url.startswith("http") and video_id:
            url = f"{_BASE}/{self.handle}/videos/{video_id}"
        return resource(
            Platform.FACEBOOK,
            ResourceKind.VIDEO,
            video_id or f"video-{path}",
            url=url,
            handle=self.handle,
            text=clean_text(first_str(node, "title", "message", "description")),
            created_at=normalize_timestamp(
                first_num(node, "creation_time", "publish_time")
            ),
            media=media,
            metrics=numeric_metrics(node, _METRIC_KEYS, max_depth=5),
            extra={"duration_s": first_num(node, "duration")},
            raw_file=payload.file,
            raw_pointer=path,
        )
