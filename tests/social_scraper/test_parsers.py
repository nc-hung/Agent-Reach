# -*- coding: utf-8 -*-
"""Normalizers: raw payloads → normalized resources, per platform."""

from agent_reach.social_scraper.models import Platform, RawPayload, ResourceKind
from agent_reach.social_scraper.parsing.facebook import FacebookNormalizer
from agent_reach.social_scraper.parsing.instagram import InstagramNormalizer
from agent_reach.social_scraper.parsing.tiktok import TikTokNormalizer

from .conftest import (
    FB_EVENT_PAYLOAD,
    FB_HYDRATION_PAYLOAD,
    FB_LIVE_PAYLOAD,
    FB_STORY_PAYLOAD,
    IG_FEED_PAYLOAD,
    IG_ITEMS_PAYLOAD,
    IG_PROFILE_PAYLOAD,
    TT_ITEMS_PAYLOAD,
    TT_LIVE_PAYLOAD,
    TT_PROFILE_PAYLOAD,
)


def _raw(bucket, url, data):
    return RawPayload(bucket=bucket, source_url=url, seq=1, data=data)


# ---------------------------------------------------------------------- #
# Instagram
# ---------------------------------------------------------------------- #
def test_instagram_profile():
    normalizer = InstagramNormalizer(handle="nasa")
    out = normalizer.normalize(
        _raw("profile", "https://www.instagram.com/api/v1/users/web_profile_info/", IG_PROFILE_PAYLOAD)
    )
    assert len(out) == 1
    profile = out[0]
    assert profile.kind is ResourceKind.PROFILE
    assert profile.platform is Platform.INSTAGRAM
    assert profile.handle == "nasa"
    assert profile.author == "NASA"
    assert profile.text == "We are NASA. 🚀"
    assert profile.metrics["followers"] == 1_000_000
    assert profile.media[0].media_type == "image"


def test_instagram_feed_posts_from_graphql_edges():
    normalizer = InstagramNormalizer(handle="nasa")
    out = normalizer.normalize(
        _raw("posts", "https://www.instagram.com/graphql/query/", IG_FEED_PAYLOAD)
    )
    assert len(out) == 2
    first, second = out
    assert first.kind is ResourceKind.POST
    assert first.native_id == "9001"
    assert first.url == "https://www.instagram.com/CAbc123/"
    assert first.text == "Hello from orbit 🌍"
    assert first.created_at.startswith("2024-09")
    assert first.metrics["likes"] == 5_000
    types = {m.media_type for m in first.media}
    assert {"image", "video"} <= types
    assert second.native_id == "9002"


def test_instagram_reels_bucket_yields_video_kind():
    normalizer = InstagramNormalizer(handle="nasa")
    out = normalizer.normalize(
        _raw("videos", "https://www.instagram.com/api/v1/clips/user/", IG_ITEMS_PAYLOAD)
    )
    assert len(out) == 1
    assert out[0].kind is ResourceKind.VIDEO
    assert out[0].native_id == "7001"
    assert any(m.media_type == "video" for m in out[0].media)


# ---------------------------------------------------------------------- #
# Facebook
# ---------------------------------------------------------------------- #
def test_facebook_profile_from_hydration():
    normalizer = FacebookNormalizer(handle="NASA")
    out = normalizer.normalize(
        _raw("posts", "https://www.facebook.com/NASA", FB_HYDRATION_PAYLOAD)
    )
    profiles = [r for r in out if r.kind is ResourceKind.PROFILE]
    assert len(profiles) == 1
    profile = profiles[0]
    assert profile.author == "NASA"
    assert profile.metrics["followers"] == 25_000_000
    assert profile.text == "NASA's page"


def test_facebook_story_post():
    normalizer = FacebookNormalizer(handle="NASA")
    out = normalizer.normalize(
        _raw("posts", "https://www.facebook.com/api/graphql/", FB_STORY_PAYLOAD)
    )
    posts = [r for r in out if r.kind is ResourceKind.POST]
    assert len(posts) == 1
    post = posts[0]
    assert post.native_id == "1122334455_998877"
    assert post.text == "Apollo tribute photo dump"  # HTML cleaned
    assert post.created_at.startswith("2024-09")
    assert post.metrics["reactions"] == 9_000
    assert post.metrics["comments"] == 300
    assert post.raw_ref.pointer == "data.node"
    assert any(m.media_type == "image" for m in post.media)


def test_facebook_event():
    normalizer = FacebookNormalizer(handle="NASA")
    out = normalizer.normalize(
        _raw("events", "https://www.facebook.com/NASA/events", FB_EVENT_PAYLOAD)
    )
    events = [r for r in out if r.kind is ResourceKind.EVENT]
    assert len(events) == 1
    event = events[0]
    assert event.native_id == "555888"
    assert event.text == "Artemis Watch Party"
    assert event.created_at.startswith("2024-10")
    assert event.metrics["attending"] == 1_200
    assert event.url == "https://www.facebook.com/events/555888"


def test_facebook_live():
    normalizer = FacebookNormalizer(handle="NASA")
    out = normalizer.normalize(
        _raw("lives", "https://www.facebook.com/api/live_video/", FB_LIVE_PAYLOAD)
    )
    lives = [r for r in out if r.kind is ResourceKind.LIVE]
    assert len(lives) == 1
    live = lives[0]
    assert live.native_id == "live123"
    assert live.text == "Launch day live"
    assert live.metrics["viewers"] == 4_444
    assert live.extra["status"] == "ACTIVE"
    assert any(m.media_type == "video" for m in live.media)


def test_facebook_unknown_payload_yields_nothing_but_never_raises():
    normalizer = FacebookNormalizer(handle="NASA")
    junk = {"a": 1, "b": [1, 2, {"c": "d"}], "e": None}
    assert normalizer.normalize(_raw("other", "https://x/", junk)) == []
    assert normalizer.normalize(_raw("other", "https://x/", None)) == []


# ---------------------------------------------------------------------- #
# TikTok
# ---------------------------------------------------------------------- #
def test_tiktok_profile():
    normalizer = TikTokNormalizer(handle="nasa")
    out = normalizer.normalize(
        _raw("profile", "https://www.tiktok.com/api/user/detail/", TT_PROFILE_PAYLOAD)
    )
    assert len(out) == 1
    profile = out[0]
    assert profile.kind is ResourceKind.PROFILE
    assert profile.handle == "nasa"
    assert profile.metrics["followers"] == 8_000_000
    assert profile.text.startswith("Exploring the universe")


def test_tiktok_videos():
    normalizer = TikTokNormalizer(handle="nasa")
    out = normalizer.normalize(
        _raw("videos", "https://www.tiktok.com/api/post/item_list/", TT_ITEMS_PAYLOAD)
    )
    assert len(out) == 1
    video = out[0]
    assert video.kind is ResourceKind.VIDEO
    assert video.native_id == "7300000000000000001"
    assert video.url.endswith("/@nasa/video/7300000000000000001")
    assert video.text == "Earth from space #space"
    assert video.created_at.startswith("2024-09")
    assert video.metrics["likes"] == 2_000_000
    assert video.metrics["plays"] == 30_000_000
    assert video.extra["hashtags"] == ["space"]
    video_assets = [m for m in video.media if m.media_type == "video"]
    assert video_assets and video_assets[0].url.endswith(".mp4")
    assert any(m.media_type == "image" for m in video.media)


def test_tiktok_live_room():
    normalizer = TikTokNormalizer(handle="nasa")
    out = normalizer.normalize(
        _raw("lives", "https://webcast.tiktok.com/webcast/room/user/", TT_LIVE_PAYLOAD)
    )
    lives = [r for r in out if r.kind is ResourceKind.LIVE]
    assert len(lives) == 1
    live = lives[0]
    assert live.native_id == "733444555"
    assert live.text == "Live from the ISS"
    assert live.metrics["viewers"] == 12_345
    assert live.handle == "nasa"
