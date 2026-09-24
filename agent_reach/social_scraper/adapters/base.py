# -*- coding: utf-8 -*-
"""Shared Playwright scraping flow (Template Method for all adapters).

An adapter only declares *where* to look (entry URLs, inline scripts,
extra API calls, capture classification); this base class handles the
recurring mechanics: navigate → let the platform XHR its own JSON →
capture every payload → scroll → normalize → trim.
"""

import asyncio
import json
import random
import time
from collections import Counter
from typing import Any, Dict, List, Optional, Sequence, Tuple

from agent_reach.utils.text import scrub_url_credentials

from ..browser.capture import ResponseCapture
from ..interfaces import ContentScraper, PayloadNormalizer
from ..models import (
    ALL_KINDS,
    Resource,
    ResourceKind,
    ScrapeBundle,
    ScrapeRequest,
)

_SCROLL_PAUSE = (0.6, 1.4)  # seconds between scroll ticks — be a polite client
_PAGE_SETTLE_MS = 1_800
_INLINE_SELECTORS: Tuple[str, ...] = (
    "script#__UNIVERSAL_DATA_FOR_REHYDRATION__",
    "script#__NEXT_DATA__",
    "script#__additionalDataLoaded",
    "script#SIGI_STATE",
    "script#__SSR_DATA__",
)


class PlaywrightScraperBase(ContentScraper):
    """Template-method base: subclass supplies platform specifics."""

    normalizer: PayloadNormalizer  # set in __init__

    def __init__(self, normalizer: Optional[PayloadNormalizer] = None):
        self.normalizer = self.build_normalizer(normalizer)

    def build_normalizer(self, normalizer: Optional[PayloadNormalizer]) -> PayloadNormalizer:
        """Create the platform normalizer (overridable for tests)."""
        raise NotImplementedError

    # ------------------------------------------------------------------ #
    # Scraping
    # ------------------------------------------------------------------ #
    async def scrape(self, request: ScrapeRequest, session: Any) -> ScrapeBundle:
        """Visit every entry page, capture payloads, normalize resources."""
        handle = self.target_of(request.url)
        started_at = time.monotonic()
        capture = ResponseCapture(self)
        await session.attach_capture(capture)

        errors: List[str] = list(getattr(session, "warnings", []) or [])
        page = await session.new_page()
        entries = self.entry_urls(handle, request)
        visited = 0
        try:
            for bucket, url in entries:
                capture.active_bucket = bucket
                try:
                    await page.goto(
                        url,
                        wait_until="domcontentloaded",
                        timeout=request.timeout_ms,
                    )
                    await page.wait_for_timeout(_PAGE_SETTLE_MS)
                    await self.scroll(page, request.max_scroll)
                    for inline_bucket, data in await self.extract_inline(page, bucket):
                        capture.add_inline(inline_bucket, page.url, data)
                    visited += 1
                except Exception as exc:  # one failing tab must not kill the run
                    errors.append(f"{url} → {scrub_url_credentials(exc)}")

            for fetch in self.extra_fetches(handle):
                body = await session.api_get(fetch.url, fetch.headers)
                if body is None:
                    errors.append(f"API fetch failed: {scrub_url_credentials(fetch.url)}")
                    continue
                try:
                    capture.add_inline(fetch.bucket, fetch.url, json.loads(body))
                except ValueError:
                    errors.append(
                        f"Non-JSON API response: {scrub_url_credentials(fetch.url)}"
                    )
        finally:
            try:
                await page.close()
            except Exception:
                pass
            capture.detach()

        profile, resources = self._normalize(handle, capture.raws, request)
        raws, capture_stats = capture.take()
        stats: Dict[str, Any] = {
            **capture_stats,
            "pages_visited": visited,
            "entries": [url for _bucket, url in entries],
            "duration_ms": int((time.monotonic() - started_at) * 1000),
            "resources_by_kind": dict(
                Counter(r.kind.value for r in resources)
            ),
            "profile_found": profile is not None,
        }
        return ScrapeBundle(
            platform=self.platform,
            handle=handle,
            source_url=request.url,
            profile=profile,
            resources=resources,
            raws=raws,
            errors=errors,
            stats=stats,
        )

    # ------------------------------------------------------------------ #
    # Steps (overridable)
    # ------------------------------------------------------------------ #
    async def scroll(self, page: Any, rounds: int) -> None:
        """Scroll to trigger incremental XHR loading (gentle pacing)."""
        previous = 0
        stagnant = 0
        for _ in range(max(0, rounds)):
            await page.mouse.wheel(0, 700)
            await asyncio.sleep(random.uniform(*_SCROLL_PAUSE))
            try:
                height = await page.evaluate("document.body.scrollHeight")
            except Exception:
                return
            if height == previous:
                stagnant += 1
                if stagnant >= 2:  # page stopped growing → nothing more to load
                    return
            else:
                stagnant = 0
            previous = height

    async def extract_inline(self, page: Any, bucket: str) -> List[Tuple[str, Any]]:
        """Parse embedded JSON state the platform ships inside <script> tags."""
        found: List[Tuple[str, Any]] = []
        for selector in _INLINE_SELECTORS:
            try:
                element = await page.query_selector(selector)
                if element is None:
                    continue
                text = await element.text_content()
            except Exception:
                continue
            if not text:
                continue
            text = text.strip()
            if text.startswith("<!--"):
                text = text[4:]
            if text.endswith("-->"):
                text = text[:-3]
            try:
                found.append((bucket, json.loads(text)))
            except ValueError:
                continue
        return found

    # ------------------------------------------------------------------ #
    # Normalization / selection
    # ------------------------------------------------------------------ #
    def _normalize(
        self,
        handle: str,
        raws: Sequence[Any],
        request: ScrapeRequest,
    ) -> Tuple[Optional[Resource], List[Resource]]:
        """Run the platform normalizer over every payload, dedup, trim."""
        normalizer = self.normalizer
        if hasattr(normalizer, "handle"):
            try:
                normalizer.handle = handle
            except Exception:
                pass

        profile: Optional[Resource] = None
        collected: List[Resource] = []
        seen: set = set()
        for raw in raws:
            if not raw.file:
                raw.file = f"raw/{raw.seq:04d}_{raw.bucket}.json"
            try:
                parsed = normalizer.normalize(raw)
            except Exception:
                continue
            for res in parsed:
                res.handle = res.handle or handle
                if res.kind is ResourceKind.PROFILE:
                    if profile is None:
                        res.raw_ref.file = res.raw_ref.file or raw.file
                        profile = res
                    continue
                key = (res.kind.value, res.native_id)
                if key in seen:
                    continue
                seen.add(key)
                res.raw_ref.file = res.raw_ref.file or raw.file
                collected.append(res)

        selected = self.select(collected, request.kinds or ALL_KINDS)
        return profile, self._trim(selected, request.max_items)

    @staticmethod
    def _trim(resources: List[Resource], max_items: int) -> List[Resource]:
        """Keep at most ``max_items`` per kind (platforms serve newest first)."""
        if max_items <= 0:
            return resources
        counts: Dict[ResourceKind, int] = {}
        kept: List[Resource] = []
        for res in resources:
            count = counts.get(res.kind, 0)
            if count >= max_items:
                continue
            counts[res.kind] = count + 1
            kept.append(res)
        return kept
