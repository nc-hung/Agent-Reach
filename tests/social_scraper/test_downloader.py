# -*- coding: utf-8 -*-
"""StreamingMediaDownloader against a local HTTP server."""

import hashlib
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from agent_reach.social_scraper.media.downloader import (
    StreamingMediaDownloader,
    _extension,
)

PAYLOAD = b"video-bytes-" * 512


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 - http.server API
        if self.path.startswith("/video.mp4"):
            self.send_response(200)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Length", str(len(PAYLOAD)))
            self.end_headers()
            self.wfile.write(PAYLOAD)
        elif self.path.startswith("/flaky"):
            # first request truncates the body, later ones are complete
            count = getattr(self.server, "flaky_hits", 0)
            self.server.flaky_hits = count + 1
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(PAYLOAD)))
            self.end_headers()
            self.wfile.write(PAYLOAD if count > 0 else PAYLOAD[:10])
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *args):  # silence
        return


@pytest.fixture
def media_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    server.flaky_hits = 0
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()


def _asset(url, resource_id="res1", media_type="video"):
    from agent_reach.social_scraper.models import MediaAsset

    return MediaAsset(
        url=url, media_type=media_type, resource_id=resource_id,
        referer="https://www.tiktok.com/@nasa",
    )


def test_download_streams_verifies_and_hashes(settings, media_server, tmp_path):
    downloader = StreamingMediaDownloader(settings)
    asset = _asset(f"{media_server}/video.mp4")
    summary = downloader.download([asset], tmp_path / "media")

    assert summary == {"total": 1, "downloaded": 1, "skipped": 0, "failed": 0}
    path = Path(asset.local_path)
    assert path.is_file()
    assert path.read_bytes() == PAYLOAD
    assert asset.size_bytes == len(PAYLOAD)
    assert asset.sha256 == hashlib.sha256(PAYLOAD).hexdigest()
    assert path.suffix == ".mp4"


def test_download_skips_existing_file(settings, media_server, tmp_path):
    downloader = StreamingMediaDownloader(settings)
    dest = tmp_path / "media"
    first = _asset(f"{media_server}/video.mp4")
    downloader.download([first], dest)

    # a fresh asset object, same deterministic filename → skipped, not refetched
    second = _asset(f"{media_server}/video.mp4")
    summary = downloader.download([second], dest)
    assert summary["skipped"] == 1
    assert summary["downloaded"] == 0
    assert Path(second.local_path).is_file()


def test_download_404_fails_without_retrying(settings, media_server, tmp_path):
    downloader = StreamingMediaDownloader(settings)
    asset = _asset(f"{media_server}/missing.mp4")
    summary = downloader.download([asset], tmp_path / "media")
    assert summary["failed"] == 1
    assert "404" in asset.error
    assert not asset.local_path


def test_download_retries_incomplete_body(settings, media_server, tmp_path):
    downloader = StreamingMediaDownloader(settings)
    asset = _asset(f"{media_server}/flaky.jpg", media_type="image")
    summary = downloader.download([asset], tmp_path / "media")
    assert summary["downloaded"] == 1
    assert Path(asset.local_path).read_bytes() == PAYLOAD


def test_download_handles_many_assets(settings, media_server, tmp_path):
    downloader = StreamingMediaDownloader(settings)
    assets = [_asset(f"{media_server}/video.mp4?i={i}", resource_id=f"r{i}")
              for i in range(5)]
    summary = downloader.download(assets, tmp_path / "media")
    assert summary["downloaded"] == 5
    files = {Path(a.local_path).name for a in assets}
    assert len(files) == 5  # collision-free naming


def test_extension_picks_url_then_type():
    assert _extension("https://x.test/a.MOV?sig=1", "", "video") in {".mov", ".mp4"}
    assert _extension("https://x.test/a", "image/png", "unknown") == ".png"
    assert _extension("https://x.test/a", "", "audio") == ".m4a"
    assert _extension("https://x.test/a", "", "unknown") == ".bin"
