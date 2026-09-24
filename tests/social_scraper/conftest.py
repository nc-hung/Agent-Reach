# -*- coding: utf-8 -*-
"""Shared fixtures: realistic platform payloads + seeded backup stores."""

from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from agent_reach.social_scraper.config import ScraperSettings
from agent_reach.social_scraper.models import (
    Manifest,
    Platform,
    RawPayload,
    RawRef,
    Resource,
    ResourceKind,
    ScrapeBundle,
    make_resource_id,
)
from agent_reach.social_scraper.storage.backup import BackupStoreImpl

# ---------------------------------------------------------------------- #
# Instagram payloads
# ---------------------------------------------------------------------- #
IG_PROFILE_PAYLOAD: Dict[str, Any] = {
    "data": {
        "user": {
            "id": "1234567",
            "username": "nasa",
            "full_name": "NASA",
            "biography": "We are NASA. 🚀",
            "follower_count": 1_000_000,
            "following_count": 42,
            "media_count": 3_210,
            "is_verified": True,
            "is_private": False,
            "category": "Education",
            "profile_pic_url": "https://cdn.example.test/avatar.jpg",
        }
    }
}

IG_FEED_PAYLOAD: Dict[str, Any] = {
    "data": {
        "xdt_api__v1__feed__user__timeline_connection": {
            "edges": [
                {
                    "node": {
                        "id": "9001",
                        "pk": "9001",
                        "code": "CAbc123",
                        "caption": {"text": "Hello from orbit 🌍"},
                        "taken_at": 1_726_000_000,
                        "like_count": 5_000,
                        "comment_count": 120,
                        "media_type": 2,
                        "user": {"username": "nasa"},
                        "image_versions2": {
                            "candidates": [
                                {"url": "https://cdn.example.test/post1.jpg"}
                            ]
                        },
                        "video_versions": [
                            {"url": "https://cdn.example.test/post1.mp4"}
                        ],
                    }
                },
                {
                    "node": {
                        "id": "9002",
                        "pk": "9002",
                        "code": "CDef456",
                        "caption": {"text": "Second post"},
                        "taken_at": 1_725_000_000,
                        "like_count": 3_000,
                        "comment_count": 44,
                        "media_type": 1,
                        "user": {"username": "nasa"},
                        "image_versions2": {
                            "candidates": [
                                {"url": "https://cdn.example.test/post2.jpg"}
                            ]
                        },
                    }
                },
            ]
        }
    }
}

IG_ITEMS_PAYLOAD: Dict[str, Any] = {
    "items": [
        {
            "id": "7001",
            "pk": "7001",
            "code": "CReel1",
            "caption": {"text": "reel caption"},
            "taken_at": 1_724_000_000,
            "like_count": 11,
            "comment_count": 2,
            "media_type": 2,
            "user": {"username": "nasa"},
            "video_versions": [{"url": "https://cdn.example.test/reel.mp4"}],
            "image_versions2": {
                "candidates": [{"url": "https://cdn.example.test/reel.jpg"}]
            },
        }
    ]
}

# ---------------------------------------------------------------------- #
# Facebook payloads
# ---------------------------------------------------------------------- #
FB_HYDRATION_PAYLOAD: Dict[str, Any] = {
    "props": {
        "page": {
            "id": "777",
            "name": "NASA",
            "username": "NASA",
            "category": "Education",
            "follower_count": 25_000_000,
            "bio": "NASA's page",
            "profilePic": "https://fbcdn.example.test/pic.jpg",
        }
    }
}

FB_STORY_PAYLOAD: Dict[str, Any] = {
    "data": {
        "node": {
            "id": "1122334455",
            "post_id": "1122334455_998877",
            "creation_time": 1_726_100_000,
            "message": "Apollo tribute  <b>photo dump</b>",
            "react_count": 9_000,
            "comment_count": 300,
            "share_count": 55,
            "attachments": {
                "data": [
                    {
                        "media": {
                            "image": {
                                "uri": "https://fbcdn.example.test/story.jpg"
                            }
                        }
                    }
                ]
            },
        }
    }
}

FB_EVENT_PAYLOAD: Dict[str, Any] = {
    "event": {
        "event_id": "555888",
        "event_name": "Artemis Watch Party",
        "event_start_time": 1_730_000_000,
        "attending_count": 1_200,
        "interested_count": 3_400,
        "is_online_event": False,
        "place": {"name": "Kennedy Space Center"},
    }
}

FB_LIVE_PAYLOAD: Dict[str, Any] = {
    "broadcast": {
        "id": "live123",
        "video_id": "live123",
        "broadcast_status": "ACTIVE",
        "is_live": True,
        "title": "Launch day live",
        "creation_time": 1_726_200_000,
        "viewer_count": 4_444,
        "browser_native_hd_url": "https://fbcdn.example.test/live.m3u8",
    }
}

# ---------------------------------------------------------------------- #
# TikTok payloads
# ---------------------------------------------------------------------- #
TT_PROFILE_PAYLOAD: Dict[str, Any] = {
    "userInfo": {
        "user": {
            "id": "660001",
            "uniqueId": "nasa",
            "nickname": "NASA",
            "signature": "Exploring the universe 🌌",
            "avatarThumb": "https://ttcdn.example.test/avatar.png",
            "followerCount": 8_000_000,
            "followingCount": 12,
            "heartCount": 90_000_000,
            "videoCount": 500,
            "privateAccount": False,
        },
        "stats": {
            "followerCount": 8_000_000,
            "followingCount": 12,
            "heartCount": 90_000_000,
            "videoCount": 500,
        },
    }
}

TT_ITEMS_PAYLOAD: Dict[str, Any] = {
    "data": {
        "itemList": [
            {
                "id": "7300000000000000001",
                "desc": "Earth from space #space",
                "createTime": 1_726_300_000,
                "author": {"uniqueId": "nasa", "nickname": "NASA"},
                "stats": {
                    "diggCount": 2_000_000,
                    "commentCount": 5_000,
                    "shareCount": 900,
                    "playCount": 30_000_000,
                },
                "music": {"title": "Original sound - NASA"},
                "textExtra": [{"hashtagName": "space"}],
                "video": {
                    "duration": 18,
                    "playAddr": "https://ttcdn.example.test/video1.mp4",
                    "downloadAddr": "https://ttcdn.example.test/video1_dl.mp4",
                    "cover": "https://ttcdn.example.test/cover1.jpg",
                },
            }
        ]
    }
}

TT_LIVE_PAYLOAD: Dict[str, Any] = {
    "data": {
        "streamData": {
            "room_id": "733444555",
            "title": "Live from the ISS",
            "status": 2,
            "owner": {"uniqueId": "nasa", "nickname": "NASA"},
            "user_count": 12_345,
            "create_time": 1_726_400_000,
        }
    }
}


# ---------------------------------------------------------------------- #
# Fixtures
# ---------------------------------------------------------------------- #
@pytest.fixture
def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ScraperSettings:
    """ScraperSettings rooted in a temp backup/session dir."""
    backup = tmp_path / "backups"
    sessions = tmp_path / "sessions"
    monkeypatch.setenv("SOCIAL_BACKUP_DIR", str(backup))
    monkeypatch.setenv("SOCIAL_SESSION_DIR", str(sessions))
    return ScraperSettings.from_env()


@pytest.fixture
def store(settings: ScraperSettings) -> BackupStoreImpl:
    """A backup store rooted in the temp dir."""
    return BackupStoreImpl(settings.backup_dir)


def seed_session(
    store: BackupStoreImpl,
    *,
    platform: Platform = Platform.INSTAGRAM,
    handle: str = "nasa",
    resource_count: int = 3,
    source_url: str = "https://www.instagram.com/nasa/",
) -> "tuple[Path, List[Resource], Resource]":
    """Create a complete backup session (raw + profile + index + manifest)."""
    raw = RawPayload(
        bucket="posts",
        source_url=source_url,
        seq=1,
        data={"items": [{"id": str(i)} for i in range(resource_count)]},
    )
    profile = Resource(
        id=make_resource_id(platform, f"user-{handle}", ResourceKind.PROFILE),
        platform=platform,
        kind=ResourceKind.PROFILE,
        url=source_url,
        native_id=f"user-{handle}",
        handle=handle,
        author=handle.upper(),
        text="Profile bio of the target",
        metrics={"followers": 100},
    )
    resources: List[Resource] = []
    for index in range(resource_count):
        native = f"post-{index}"
        resources.append(
            Resource(
                id=make_resource_id(platform, native, ResourceKind.POST),
                platform=platform,
                kind=ResourceKind.POST,
                url=f"{source_url}p/{native}/",
                native_id=native,
                handle=handle,
                author=handle,
                text=f"Post {index} about launches and space news {index}",
                created_at="2026-09-20T10:00:00Z",
                metrics={"likes": index * 10},
                raw_ref=RawRef(file="raw/0001_posts.json", pointer="items"),
            )
        )

    session_dir = store.begin(platform, handle)
    store.write_raws(session_dir, [raw])
    store.write_profile(session_dir, profile)
    store.write_resources(session_dir, resources)
    bundle = ScrapeBundle(
        platform=platform,
        handle=handle,
        source_url=source_url,
        profile=profile,
        resources=resources,
        raws=[raw],
        stats={"duration_ms": 1},
    )
    store.finalize(
        session_dir, bundle, {"total": 0, "downloaded": 0, "skipped": 0, "failed": 0}
    )
    return session_dir, resources, profile


def manifest_of(session_dir: Path) -> Optional[Manifest]:
    """Read a session manifest (test convenience)."""
    from agent_reach.social_scraper.storage.manifest import read_manifest

    return read_manifest(session_dir)
