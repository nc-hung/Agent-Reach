# -*- coding: utf-8 -*-
"""ScrapeEngine orchestration with fakes (no browser, no network)."""

import json
from pathlib import Path

import pytest

from agent_reach.social_scraper.adapters import ScraperRegistry
from agent_reach.social_scraper.engine import ScrapeEngine
from agent_reach.social_scraper.exceptions import UnsupportedPlatformError
from agent_reach.social_scraper.interfaces import ContentScraper, MediaDownloader
from agent_reach.social_scraper.models import (
    MediaAsset,
    Platform,
    RawPayload,
    RawRef,
    Resource,
    ResourceKind,
    ScrapeBundle,
    ScrapeRequest,
)
from agent_reach.social_scraper.storage.backup import BackupStoreImpl
from agent_reach.social_scraper.storage.repository import ResourceRepository


# ---------------------------------------------------------------------- #
# Fakes (DIP: engine consumes abstractions only)
# ---------------------------------------------------------------------- #
class _FakeSession:
    warnings = ["no saved session (fake)"]

    async def attach_capture(self, capture):
        return None

    async def close(self):
        return None


class _FakeSessionFactory:
    async def open(self, platform, headless=True, session_cookie=""):
        return _FakeSession()


class _FakeScraper(ContentScraper):
    platform = Platform.INSTAGRAM
    display_name = "Fake"
    hosts = ("instagram.com",)
    session_cookie = "sessionid"

    @classmethod
    def can_handle(cls, url):
        return "instagram.com" in url

    @classmethod
    def target_of(cls, url):
        if "explore" in url:
            return ""
        return "nasa"

    async def scrape(self, request, session):
        raw = RawPayload(
            bucket="posts",
            source_url=request.url,
            seq=1,
            data={"items": [{"id": "1"}]},
        )
        raw.file = "raw/0001_posts.json"
        profile = Resource(
            id="profile0000000001",
            platform=Platform.INSTAGRAM,
            kind=ResourceKind.PROFILE,
            url=request.url,
            native_id="user-nasa",
            handle="nasa",
            text="bio",
        )
        resources = [
            Resource(
                id="post0000000000001",
                platform=Platform.INSTAGRAM,
                kind=ResourceKind.POST,
                url=f"{request.url}p/1/",
                native_id="1",
                handle="nasa",
                text="launch post",
                raw_ref=RawRef(file="raw/0001_posts.json"),
                media=[
                    MediaAsset(
                        url="https://cdn.example.test/v.mp4",
                        media_type="video",
                    )
                ],
            )
        ]
        return ScrapeBundle(
            platform=Platform.INSTAGRAM,
            handle="nasa",
            source_url=request.url,
            profile=profile,
            resources=resources,
            raws=[raw],
            errors=list(getattr(session, "warnings", [])),
            stats={"duration_ms": 7},
        )


class _RecordingDownloader(MediaDownloader):
    def __init__(self):
        self.calls = []

    def download(self, assets, dest_dir):
        self.calls.append((list(assets), Path(dest_dir)))
        for asset in assets:
            asset.local_path = str(Path(dest_dir) / "fake.mp4")
            asset.sha256 = "0" * 64
            asset.size_bytes = 1
        return {"total": len(assets), "downloaded": len(assets),
                "skipped": 0, "failed": 0}


def _engine(settings, downloader=None):
    registry = ScraperRegistry([_FakeScraper()])
    return ScrapeEngine(
        settings=settings,
        registry=registry,
        store=BackupStoreImpl(settings.backup_dir),
        downloader=downloader or _RecordingDownloader(),
        session_factory=_FakeSessionFactory(),
    )


# ---------------------------------------------------------------------- #
# Tests
# ---------------------------------------------------------------------- #
def test_scrape_persists_full_backup(settings):
    engine = _engine(settings)
    result = engine.scrape(
        ScrapeRequest(url="https://www.instagram.com/nasa/", download_media=False)
    )

    assert result.session.startswith("instagram/nasa/")
    assert result.platform is Platform.INSTAGRAM
    assert result.handle == "nasa"
    assert result.counts == {"post": 1, "profile": 1}
    assert result.raw_count == 1
    assert "no saved session (fake)" in result.errors

    session_dir = settings.backup_dir / result.session
    assert (session_dir / "manifest.json").is_file()
    assert (session_dir / "index.jsonl").is_file()
    assert (session_dir / "profile.json").is_file()
    assert (session_dir / "raw" / "0001_posts.json").is_file()

    manifest = json.loads((session_dir / "manifest.json").read_text())
    assert manifest["counts"]["post"] == 1
    assert manifest["stats"]["duration_ms"] == 7


def test_scrape_downloads_media_and_refreshes_index(settings):
    downloader = _RecordingDownloader()
    engine = _engine(settings, downloader)
    result = engine.scrape(
        ScrapeRequest(url="https://www.instagram.com/nasa/", download_media=True)
    )

    assert result.media == {"total": 1, "downloaded": 1, "skipped": 0, "failed": 0}
    assert len(downloader.calls) == 1
    assets, dest = downloader.calls[0]
    assert dest.name == "media"
    assert assets[0].resource_id == "post0000000000001"

    # index refreshed after download → asset path persisted
    index_lines = (
        settings.backup_dir / result.session / "index.jsonl"
    ).read_text().strip().splitlines()
    record = json.loads(index_lines[0])
    assert record["media"][0]["local_path"].endswith("fake.mp4")


def test_scrape_rejects_unknown_platform(settings):
    engine = _engine(settings)
    with pytest.raises(UnsupportedPlatformError):
        engine.scrape(ScrapeRequest(url="https://example.com/whatever"))


def test_scrape_rejects_url_without_handle(settings):
    engine = _engine(settings)
    with pytest.raises(UnsupportedPlatformError, match="handle"):
        engine.scrape(
            ScrapeRequest(url="https://www.instagram.com/explore/")
        )


def test_scrape_refuses_running_loop(settings):
    import asyncio

    engine = _engine(settings)

    async def inside_loop():
        with pytest.raises(RuntimeError, match="event loop"):
            engine.scrape(ScrapeRequest(url="https://www.instagram.com/nasa/"))

    asyncio.run(inside_loop())


def test_scraped_results_are_queryable(settings):
    engine = _engine(settings)
    result = engine.scrape(
        ScrapeRequest(url="https://www.instagram.com/nasa/", download_media=False)
    )
    repo = ResourceRepository(settings.backup_dir)
    found = repo.get(result.resources[0].id)
    assert found is not None
    assert found.text == "launch post"
    raw = repo.read_raw(found)
    assert raw == {"items": [{"id": "1"}]}
