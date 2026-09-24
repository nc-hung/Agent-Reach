# -*- coding: utf-8 -*-
"""Adapter routing: can_handle / target_of / classify / entry_urls / registry."""

import pytest

from agent_reach.social_scraper.adapters import (
    default_registry,
    get_default_registry,
)
from agent_reach.social_scraper.adapters.facebook import FacebookScraper
from agent_reach.social_scraper.adapters.instagram import InstagramScraper
from agent_reach.social_scraper.adapters.tiktok import TikTokScraper
from agent_reach.social_scraper.exceptions import UnsupportedPlatformError
from agent_reach.social_scraper.models import ALL_KINDS, Platform, ResourceKind, ScrapeRequest


# ---------------------------------------------------------------------- #
# URL routing
# ---------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "url,platform,handle",
    [
        ("https://www.facebook.com/nasa", Platform.FACEBOOK, "nasa"),
        ("https://m.facebook.com/nasa/videos/", Platform.FACEBOOK, "nasa"),
        ("https://www.facebook.com/pages/Space/12345", Platform.FACEBOOK, "Space"),
        ("https://www.facebook.com/profile.php?id=1029384756", Platform.FACEBOOK, "id_1029384756"),
        ("https://www.instagram.com/nasa/", Platform.INSTAGRAM, "nasa"),
        ("https://instagram.com/nasa", Platform.INSTAGRAM, "nasa"),
        ("https://www.tiktok.com/@nasa", Platform.TIKTOK, "nasa"),
        ("https://www.tiktok.com/@nasa/video/73001", Platform.TIKTOK, "nasa"),
    ],
)
def test_registry_resolves_and_extracts_handle(url, platform, handle):
    scraper = get_default_registry().resolve(url)
    assert scraper.platform is platform
    assert scraper.target_of(url) == handle


@pytest.mark.parametrize(
    "url",
    [
        "https://www.instagram.com/explore/",
        "https://www.tiktok.com/discover",
        "https://www.facebook.com/videos",
        "https://example.com/nasa",
    ],
)
def test_unsupported_or_reserved_urls(url):
    registry = default_registry()
    if "example.com" in url:
        with pytest.raises(UnsupportedPlatformError):
            registry.resolve(url)
    else:
        scraper = registry.resolve(url)
        assert scraper.target_of(url) == ""


def test_host_spoofing_does_not_match():
    registry = default_registry()
    with pytest.raises(UnsupportedPlatformError):
        registry.resolve("https://facebook.com.evil.test/nasa")
    with pytest.raises(UnsupportedPlatformError):
        registry.resolve("https://notinstagram.com/nasa/")


# ---------------------------------------------------------------------- #
# Capture classification
# ---------------------------------------------------------------------- #
def test_facebook_classify():
    scraper = FacebookScraper()
    assert scraper.classify("https://www.facebook.com/api/graphql/") is None
    assert scraper.classify("https://www.facebook.com/nasa/events") == "events"
    assert scraper.classify("https://www.facebook.com/nasa/videos") == "videos"
    assert scraper.classify(
        "https://www.facebook.com/api/live_video/info/"
    ) == "lives"
    assert scraper.is_noise(
        "https://www.facebook.com/tr/collect?x=1"
    ) is False  # tracking endpoint still host-matched; capture keeps JSON only


def test_instagram_classify():
    scraper = InstagramScraper()
    assert (
        scraper.classify(
            "https://www.instagram.com/api/v1/users/web_profile_info/?username=nasa"
        )
        == "profile"
    )
    assert (
        scraper.classify(
            "https://www.instagram.com/graphql/query/?query_hash=abc"
        )
        is None
    )
    assert scraper.classify("https://www.instagram.com/api/v1/clips/user/") == "videos"


def test_tiktok_classify():
    scraper = TikTokScraper()
    assert (
        scraper.classify("https://www.tiktok.com/api/user/detail/?uniqueId=nasa")
        == "profile"
    )
    assert (
        scraper.classify("https://www.tiktok.com/api/post/item_list/?count=30")
        == "videos"
    )
    assert (
        scraper.classify("https://webcast.tiktok.com/webcast/room/user/")
        == "lives"
    )


# ---------------------------------------------------------------------- #
# Entry URLs / kind semantics
# ---------------------------------------------------------------------- #
def test_facebook_entries_cover_videos_and_events():
    scraper = FacebookScraper()
    request = ScrapeRequest(url="https://www.facebook.com/nasa", kinds=ALL_KINDS)
    entries = dict(scraper.entry_urls("nasa", request))
    assert entries["posts"] == "https://www.facebook.com/nasa"
    assert entries["videos"] == "https://www.facebook.com/nasa/videos"
    assert entries["events"] == "https://www.facebook.com/nasa/events"

    posts_only = ScrapeRequest(
        url="https://www.facebook.com/nasa", kinds=(ResourceKind.POST,)
    )
    assert dict(scraper.entry_urls("nasa", posts_only)).keys() == {"posts"}


def test_tiktok_live_entry_only_when_requested():
    scraper = TikTokScraper()
    posts_only = ScrapeRequest(
        url="https://www.tiktok.com/@nasa", kinds=(ResourceKind.POST,)
    )
    assert len(scraper.entry_urls("nasa", posts_only)) == 1
    with_live = ScrapeRequest(
        url="https://www.tiktok.com/@nasa", kinds=ALL_KINDS
    )
    entries = dict(scraper.entry_urls("nasa", with_live))
    assert entries["lives"].endswith("/@nasa/live")


def test_kind_selection_semantics():
    from agent_reach.social_scraper.models import MediaAsset

    tiktok = TikTokScraper()
    video = ResourceKind.VIDEO
    fb = FacebookScraper()

    def res(kind, media=()):
        from agent_reach.social_scraper.models import Resource

        return Resource(
            id="x", platform=Platform.TIKTOK, kind=kind,
            media=list(media),
        )

    # TikTok: videos also count as posts
    selected = tiktok.select([res(video)], (ResourceKind.POST,))
    assert len(selected) == 1

    # Facebook: a POST with video media matches a `videos` request
    post_with_video = res(
        ResourceKind.POST,
        [MediaAsset(url="https://x.test/v.mp4", media_type="video")],
    )
    post_image_only = res(ResourceKind.POST)
    selected = fb.select(
        [post_with_video, post_image_only], (ResourceKind.VIDEO,)
    )
    assert selected == [post_with_video]
