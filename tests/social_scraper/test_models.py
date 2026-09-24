# -*- coding: utf-8 -*-
"""Domain model behavior: ids, kinds, serialization."""

import json

import pytest

from agent_reach.social_scraper.models import (
    ALL_KINDS,
    MediaAsset,
    Platform,
    Resource,
    ResourceKind,
    ScrapeRequest,
    make_resource_id,
    parse_kinds,
    resource_from_dict,
    to_jsonable,
)


def test_resource_id_is_deterministic():
    a = make_resource_id(Platform.INSTAGRAM, "123", ResourceKind.POST)
    b = make_resource_id(Platform.INSTAGRAM, "123", ResourceKind.POST)
    c = make_resource_id(Platform.INSTAGRAM, "123", ResourceKind.VIDEO)
    assert a == b
    assert a != c
    assert len(a) == 16


@pytest.mark.parametrize(
    "value,expected",
    [
        (None, ALL_KINDS),
        ("all", ALL_KINDS),
        ("posts,videos", (ResourceKind.POST, ResourceKind.VIDEO)),
        (["lives"], (ResourceKind.LIVE,)),
        ("events", (ResourceKind.EVENT,)),
    ],
)
def test_parse_kinds(value, expected):
    assert parse_kinds(value) == expected


def test_parse_kinds_rejects_unknown():
    with pytest.raises(ValueError, match="Unknown kind"):
        parse_kinds("banana")


def test_scrape_request_from_dict_defaults():
    request = ScrapeRequest.from_dict({"url": "https://www.tiktok.com/@nasa"})
    assert request.url.endswith("@nasa")
    assert request.kinds == ALL_KINDS
    assert request.download_media is True
    assert request.max_items == 100


def test_scrape_request_from_dict_overrides():
    request = ScrapeRequest.from_dict(
        {
            "url": "https://www.instagram.com/nasa/",
            "kinds": ["posts"],
            "max_items": 5,
            "download_media": False,
            "headless": False,
        }
    )
    assert request.kinds == (ResourceKind.POST,)
    assert request.max_items == 5
    assert request.download_media is False
    assert request.headless is False


def test_resource_roundtrip_through_json():
    original = Resource(
        id="abc123def4567890",
        platform=Platform.TIKTOK,
        kind=ResourceKind.VIDEO,
        url="https://www.tiktok.com/@nasa/video/1",
        native_id="1",
        handle="nasa",
        text="desc",
        media=[
            MediaAsset(
                url="https://cdn.example.test/v.mp4",
                media_type="video",
                sha256="ff" * 32,
                size_bytes=1024,
            )
        ],
        metrics={"likes": 3},
        extra={"duration_s": 12},
    )
    payload = to_jsonable(original)
    json.dumps(payload)  # must be JSON-safe as-is
    restored = resource_from_dict(json.loads(json.dumps(payload)))

    assert restored.id == original.id
    assert restored.platform is Platform.TIKTOK
    assert restored.kind is ResourceKind.VIDEO
    assert restored.media[0].sha256 == "ff" * 32
    assert restored.metrics == {"likes": 3}
    assert restored.extra == {"duration_s": 12}


def test_to_jsonable_converts_enums_and_paths(tmp_path):
    data = to_jsonable(
        {"platform": Platform.FACEBOOK, "kind": ResourceKind.LIVE, "p": tmp_path}
    )
    assert data["platform"] == "facebook"
    assert data["kind"] == "live"
    assert data["p"] == str(tmp_path)
