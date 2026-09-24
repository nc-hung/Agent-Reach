# -*- coding: utf-8 -*-
"""Streaming media downloader — professional, resumable, verified.

Design:
- streams from the platform CDN with ``requests`` (referer + UA headers),
- writes to a ``.part`` file and atomically renames on success,
- computes sha256 while streaming so the manifest can verify backups,
- skips files already present (natural resume),
- bounded concurrency via a thread pool, polite retry with backoff.
"""

import hashlib
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import requests

from agent_reach.utils.text import scrub_url_credentials

from ..config import ScraperSettings
from ..interfaces import MediaDownloader
from ..models import MediaAsset

_CHUNK = 64 * 1024
_BACKOFF_BASE = 1.5
_EXT_BY_TYPE = {
    "image": ".jpg",
    "video": ".mp4",
    "audio": ".m4a",
    "unknown": ".bin",
}
_CONTENT_TYPE_EXT = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "video/mp4": ".mp4",
    "video/webm": ".webm",
    "video/quicktime": ".mov",
    "audio/mpeg": ".mp3",
    "audio/mp4": ".m4a",
    "application/json": ".json",
}


def _extension(url: str, content_type: str, media_type: str) -> str:
    """Pick a file extension: URL suffix → content-type → media type default."""
    suffix = Path(url.split("?", 1)[0]).suffix.lower()
    if suffix in {".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic", ".avif",
                  ".mp4", ".mov", ".m4v", ".webm", ".mkv", ".mp3", ".m4a",
                  ".aac", ".wav"}:
        return suffix if suffix != ".jpeg" else ".jpg"
    lowered = (content_type or "").split(";", 1)[0].strip().lower()
    if lowered in _CONTENT_TYPE_EXT:
        return _CONTENT_TYPE_EXT[lowered]
    return _EXT_BY_TYPE.get(media_type, ".bin")


class StreamingMediaDownloader(MediaDownloader):
    """Downloads assets into a destination directory, in place."""

    def __init__(self, settings: ScraperSettings):
        self.settings = settings

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def download(self, assets: Sequence[MediaAsset], dest_dir: Path) -> Dict[str, int]:
        """Stream every asset to ``dest_dir``; mutates assets in place."""
        dest_dir = Path(dest_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)

        targets: List[Tuple[MediaAsset, Path]] = []
        summary = {"total": 0, "downloaded": 0, "skipped": 0, "failed": 0}
        for index, asset in enumerate(self._dedup(assets)):
            summary["total"] += 1
            if not asset.url:
                asset.error = asset.error or "empty url"
                summary["failed"] += 1
                continue
            path = self._path_for(asset, index, dest_dir)
            if path.is_file() and path.stat().st_size > 0 and not asset.local_path:
                asset.local_path = str(path)
                asset.size_bytes = path.stat().st_size
                summary["skipped"] += 1
                continue
            if asset.local_path and Path(asset.local_path).is_file():
                summary["skipped"] += 1
                continue
            targets.append((asset, path))

        if not targets:
            return summary

        workers = min(self.settings.download_concurrency, max(1, len(targets)))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(self._fetch, asset, path): asset
                for asset, path in targets
            }
            for future in as_completed(futures):
                asset = futures[future]
                ok = future.result()
                if ok:
                    summary["downloaded"] += 1
                else:
                    summary["failed"] += 1
        return summary

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #
    @staticmethod
    def _dedup(assets: Sequence[MediaAsset]) -> List[MediaAsset]:
        """Drop repeated URLs while keeping one asset per resource slot."""
        seen = set()
        out: List[MediaAsset] = []
        for asset in assets:
            key = asset.url
            if key in seen:
                continue
            seen.add(key)
            out.append(asset)
        return out

    @staticmethod
    def _path_for(asset: MediaAsset, index: int, dest_dir: Path) -> Path:
        """Deterministic, collision-free filename for an asset."""
        import re

        base = asset.resource_id or hashlib.sha1(asset.url.encode("utf-8")).hexdigest()[:12]
        base = re.sub(r"[^A-Za-z0-9._-]", "_", base)[:64]
        ext = _extension(asset.url, "", asset.media_type)
        return dest_dir / f"{base}_{index:03d}{ext}"

    def _fetch(self, asset: MediaAsset, path: Path) -> bool:
        """Stream one asset with retries; returns success."""
        headers = {
            "User-Agent": self.settings.user_agent,
            "Accept": "*/*",
        }
        referer = asset.referer or ""
        if referer:
            headers["Referer"] = referer

        last_error = ""
        for attempt in range(self.settings.download_retries):
            try:
                with requests.get(
                    asset.url,
                    headers=headers,
                    stream=True,
                    timeout=(15, self.settings.download_timeout),
                ) as response:
                    if response.status_code >= 400:
                        last_error = f"HTTP {response.status_code}"
                        if response.status_code in {403, 404, 410}:
                            break  # permanent for this URL — don't retry
                    else:
                        ok = self._write_response(response, asset, path)
                        if ok:
                            return True
                        last_error = "incomplete body"
            except requests.RequestException as exc:
                last_error = scrub_url_credentials(exc)
            if attempt < self.settings.download_retries - 1:
                import time

                time.sleep(_BACKOFF_BASE ** attempt)
        asset.error = last_error or "download failed"
        return False

    @staticmethod
    def _write_response(response: requests.Response, asset: MediaAsset, path: Path) -> bool:
        """Stream response body to a .part file, verify size, rename."""
        tmp = path.with_name(path.name + ".part")
        digest = hashlib.sha256()
        written = 0
        expected: Optional[int] = None
        raw_length = response.headers.get("Content-Length")
        if raw_length and str(raw_length).isdigit():
            expected = int(raw_length)
        content_type = response.headers.get("Content-Type", "")
        try:
            with open(tmp, "wb") as handle:
                for chunk in response.iter_content(chunk_size=_CHUNK):
                    if not chunk:
                        continue
                    handle.write(chunk)
                    digest.update(chunk)
                    written += len(chunk)
                handle.flush()
                os.fsync(handle.fileno())
            if expected is not None and written != expected:
                tmp.unlink(missing_ok=True)
                return False
            if written == 0:
                tmp.unlink(missing_ok=True)
                return False
            os.replace(tmp, path)
        except OSError:
            tmp.unlink(missing_ok=True)
            return False

        asset.local_path = str(path)
        asset.size_bytes = written
        asset.sha256 = digest.hexdigest()
        if asset.media_type == "unknown":
            lowered = (content_type or "").lower()
            if "video" in lowered:
                asset.media_type = "video"
            elif "image" in lowered:
                asset.media_type = "image"
            elif "audio" in lowered:
                asset.media_type = "audio"
        asset.error = ""
        return True
