# -*- coding: utf-8 -*-
"""Network response capture — "everything the platform returns, kept raw".

``ResponseCapture`` subscribes to browser/API responses, keeps every JSON
payload that is not telemetry, and buckets it (profile/posts/videos/…)
for later normalization and raw archival.
"""

import hashlib
import json
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple

from ..models import RawPayload

DEFAULT_MAX_PAYLOADS = 1_500
_MAX_PAYLOAD_CHARS = 8 * 1024 * 1024  # hard safety cap per payload


class ResponseCapture:
    """Captures JSON responses through a platform-specific capture policy.

    The policy is the scraper itself (``classify`` / ``is_noise`` /
    ``matches_host``), keeping this class free of platform knowledge.
    """

    def __init__(self, policy: Any, max_payloads: int = DEFAULT_MAX_PAYLOADS):
        self.policy = policy
        self.max_payloads = max_payloads
        self.active_bucket = "other"
        self.raws: List[RawPayload] = []
        self.duplicates = 0
        self.dropped = 0
        self.failed = 0
        self._fingerprints: Dict[str, None] = {}
        self._seq = 0
        self._detachers: List[Any] = []

    # ------------------------------------------------------------------ #
    # Attach / detach
    # ------------------------------------------------------------------ #
    def attach(self, context: Any) -> None:
        """Attach to a BrowserContext (pages) and its APIRequestContext."""
        context.on("response", self._on_response)
        self._detachers.append(("context", context))
        api = getattr(context, "request", None)
        if api is not None:
            try:
                api.on("response", self._on_response)
                self._detachers.append(("api", api))
            except Exception:  # pragma: no cover - depends on playwright version
                pass

    def detach(self) -> None:
        """Best-effort unsubscribe (contexts are closed right after anyway)."""
        self._detachers.clear()

    # ------------------------------------------------------------------ #
    # Feeding
    # ------------------------------------------------------------------ #
    def add_inline(self, bucket: str, source_url: str, data: Any) -> None:
        """Register a payload extracted from embedded page JSON."""
        self._add(bucket, source_url, data)

    def _add(self, bucket: str, source_url: str, data: Any) -> None:
        try:
            serialized = json.dumps(data, ensure_ascii=False)
        except (TypeError, ValueError):
            serialized = repr(data)
        if len(serialized) > _MAX_PAYLOAD_CHARS:
            self.dropped += 1
            return
        fingerprint = hashlib.sha1(
            f"{bucket}|{serialized}".encode("utf-8", "replace")
        ).hexdigest()
        if fingerprint in self._fingerprints:
            self.duplicates += 1
            return
        if len(self.raws) >= self.max_payloads:
            self.dropped += 1
            return
        self._fingerprints[fingerprint] = None
        self._seq += 1
        self.raws.append(
            RawPayload(bucket=bucket, source_url=source_url, seq=self._seq, data=data)
        )

    # ------------------------------------------------------------------ #
    # Event handlers
    # ------------------------------------------------------------------ #
    async def _on_response(self, response: Any) -> None:
        """Playwright response callback — must never raise."""
        try:
            url = getattr(response, "url", "") or ""
            if not url or self.policy.is_noise(url):
                return
            if not self.policy.matches_host(url):
                return
            bucket = self.policy.classify(url)
            if bucket is None:
                headers = getattr(response, "headers", {}) or {}
                content_type = str(headers.get("content-type", "")).lower()
                if "json" not in content_type:
                    # Not JSON and not an interesting endpoint → skip.
                    if "/api" not in url.lower() and "graphql" not in url.lower():
                        return
                bucket = self.active_bucket
            data = await self._load_json(response)
            if data is None:
                self.failed += 1
                return
            self._add(bucket, url, data)
        except Exception:
            self.failed += 1

    @staticmethod
    async def _load_json(response: Any) -> Optional[Any]:
        try:
            return await response.json()
        except Exception:
            pass
        try:
            text = await response.text()
        except Exception:
            return None
        try:
            return json.loads(text)
        except (ValueError, TypeError):
            return None

    # ------------------------------------------------------------------ #
    # Introspection
    # ------------------------------------------------------------------ #
    def bucket_counts(self) -> Dict[str, int]:
        """How many payloads were captured per bucket."""
        counter: Counter = Counter(raw.bucket for raw in self.raws)
        return dict(counter)

    def summary(self) -> Dict[str, Any]:
        """Capture statistics for the scrape manifest."""
        return {
            "raw_payloads": len(self.raws),
            "raw_by_bucket": self.bucket_counts(),
            "raw_duplicates": self.duplicates,
            "raw_dropped": self.dropped,
            "raw_failed": self.failed,
        }

    def take(self) -> Tuple[List[RawPayload], Dict[str, Any]]:
        """Return captured payloads plus their statistics."""
        return list(self.raws), self.summary()
