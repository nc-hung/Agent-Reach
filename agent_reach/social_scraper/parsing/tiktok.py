# -*- coding: utf-8 -*-
"""TikTok normalizer: raw payloads → normalized resources."""

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

_HANDLE = "tiktok"
_BASE = "https://www.tiktok.com"

_METRIC_KEYS = {
    "diggCount": "likes",
    "commentCount": "comments",
    "shareCount": "shares",
    "playCount": "plays",
    "viewCount": "views",
    "user_count": "viewers",
}


def _looks_profile(node: Dict[str, Any]) -> bool:
    if not first_str(node, "uniqueId"):
        return False
    profile_markers = ("signature", "followerCount", "avatarThumb", "stats")
    return any(k in node for k in profile_markers)


def _item_lists(data: Any):
    from .common import iter_lists

    for path, lst in iter_lists(data):
        if not lst:
            continue
        dict_items = [x for x in lst if isinstance(x, dict)]
        if len(dict_items) < max(1, len(lst) // 2):
            continue
        sample = dict_items[0]
        if "video" in sample or "imagePost" in sample:
            yield path, dict_items


class TikTokNormalizer(PayloadNormalizer):
    """Best-effort normalization of any TikTok JSON payload."""

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
        unique_id = first_str(node, "uniqueId", "unique_id") or self.handle
        user_id = str(first_num(node, "id", "uid") or unique_id)
        stats_raw = node.get("stats")
        stats: Dict[str, Any] = stats_raw if isinstance(stats_raw, dict) else {}
        metrics = {
            "followers": first_num(node, "followerCount") or stats.get("followerCount"),
            "following": first_num(node, "followingCount") or stats.get("followingCount"),
            "likes": first_num(node, "heartCount", "heart") or stats.get("heartCount"),
            "videos": first_num(node, "videoCount") or stats.get("videoCount"),
            "views": first_num(node, "totalPlayCount") or stats.get("totalPlayCount"),
        }
        metrics = {k: v for k, v in metrics.items() if v is not None}
        media = []
        avatar = first_str(node, "avatarThumb", "avatarLarger", "avatarMedium")
        if avatar:
            media = [make_asset(avatar, "image")]
        return [
            resource(
                Platform.TIKTOK,
                ResourceKind.PROFILE,
                f"user-{user_id}",
                url=f"{_BASE}/@{unique_id}",
                handle=unique_id,
                author=first_str(node, "nickname") or unique_id,
                text=clean_text(first_str(node, "signature", "desc")),
                media=media,
                metrics=metrics,
                extra={"private": bool(node.get("privateAccount"))},
                raw_file=payload.file,
            )
        ]

    # ------------------------------------------------------------------ #
    # videos / photo posts
    # ------------------------------------------------------------------ #
    def _items(self, data: Any, payload: RawPayload) -> List[Resource]:
        out: List[Resource] = []
        for _path, lst in _item_lists(data):
            for item in lst:
                res = self._item(item, payload)
                if res is not None:
                    out.append(res)
        return out

    def _item(self, item: Dict[str, Any], payload: RawPayload) -> Optional[Resource]:
        native = str(item.get("id") or item.get("itemId") or "")
        if not native:
            return None
        video_raw = item.get("video")
        video: Dict[str, Any] = video_raw if isinstance(video_raw, dict) else {}
        is_photo = isinstance(item.get("imagePost"), dict) and not video
        kind = ResourceKind.POST if is_photo else ResourceKind.VIDEO

        author_handle = self.handle
        author_node = item.get("author")
        if isinstance(author_node, str) and author_node.startswith("@"):
            author_handle = author_node.lstrip("@")
        elif isinstance(author_node, dict):
            author_handle = first_str(author_node, "uniqueId") or author_handle

        assets = []
        for key in ("playAddr", "downloadAddr"):
            url = video.get(key)
            if isinstance(url, str) and url.startswith("http"):
                assets.append(make_asset(url, "video", f"{_BASE}/@{author_handle}"))
                break
        if not assets:
            bitrate = video.get("bitrateInfo")
            if isinstance(bitrate, list):
                for entry in bitrate:
                    url = dig(entry, "PlayAddr.UrlList.0")
                    if isinstance(url, str) and url.startswith("http"):
                        assets.append(make_asset(url, "video"))
                        break
        for key in ("cover", "originCover", "dynamicCover"):
            url = video.get(key) if isinstance(video, dict) else None
            if isinstance(url, str) and url.startswith("http"):
                assets.append(make_asset(url, "image"))
                break
        photo = item.get("imagePost")
        if isinstance(photo, dict):
            images = photo.get("images")
            if isinstance(images, list):
                for entry in images:
                    url = dig(entry, "imageURL.URLList.0")
                    if isinstance(url, str) and url.startswith("http"):
                        assets.append(make_asset(url, "image"))

        stats_raw = item.get("stats")
        item_stats: Dict[str, Any] = (
            stats_raw if isinstance(stats_raw, dict) else {}
        )
        metrics = numeric_metrics({**item, **item_stats}, _METRIC_KEYS, max_depth=4)
        created = normalize_timestamp(first_num(item, "createTime", "createTimeMs"))
        url = f"{_BASE}/@{author_handle}/video/{native}"
        return resource(
            Platform.TIKTOK,
            kind,
            native,
            url=url,
            handle=author_handle,
            author=author_handle,
            text=clean_text(first_str(item, "desc")),
            created_at=created,
            media=assets,
            metrics=metrics,
            extra={
                "duration_s": first_num(video, "duration"),
                "music": dig(item, "music.title"),
                "hashtags": [
                    t.get("hashtagName")
                    for t in (item.get("textExtra") or [])
                    if isinstance(t, dict) and t.get("hashtagName")
                ],
            },
            raw_file=payload.file,
        )

    # ------------------------------------------------------------------ #
    # live rooms
    # ------------------------------------------------------------------ #
    def _lives(self, data: Any, payload: RawPayload) -> List[Resource]:
        out: List[Resource] = []
        for _path, node in walk_dicts(data, max_depth=8):
            if not self._looks_live(node):
                continue
            native = str(
                first_num(node, "room_id", "roomId", "id")
                or node.get("id_str")
                or ""
            )
            if not native:
                continue
            owner_raw = node.get("owner")
            owner: Dict[str, Any] = owner_raw if isinstance(owner_raw, dict) else {}
            out.append(
                resource(
                    Platform.TIKTOK,
                    ResourceKind.LIVE,
                    native,
                    url=f"{_BASE}/@{self.handle}/live",
                    handle=first_str(owner, "uniqueId") or self.handle,
                    author=first_str(owner, "nickname") or self.handle,
                    text=clean_text(first_str(node, "title", "desc")),
                    created_at=normalize_timestamp(
                        first_num(node, "create_time", "createTime")
                    ),
                    metrics={
                        k: v
                        for k, v in {
                            "viewers": first_num(
                                node, "user_count", "viewer_count", "viewerCount"
                            ),
                            "likes": first_num(node, "like_count", "total_likes"),
                        }.items()
                        if v is not None
                    },
                    extra={"status": first_num(node, "status") or str(node.get("status", ""))},
                    raw_file=payload.file,
                )
            )
        return out

    @staticmethod
    def _looks_live(node: Dict[str, Any]) -> bool:
        markers = ("room_id", "roomId", "streamData", "live_room")
        has_room = any(m in node for m in markers)
        has_live_ctx = any(
            k in node for k in ("owner", "status", "title", "viewer_count", "user_count")
        )
        return has_room and has_live_ctx
