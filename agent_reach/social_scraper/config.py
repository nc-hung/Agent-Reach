# -*- coding: utf-8 -*-
"""Environment-driven settings for the social scraper subsystem.

The scraper is deliberately independent from the channel ``Config``: it has
its own backup location, session storage and rate/pacing knobs. Every value
can be overridden through the environment (see ``.env.example``).
"""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Mapping, Optional

from agent_reach.utils.paths import home_dir

from .models import Platform

#: env var → cookie/storage file per platform
COOKIE_ENV: Dict[Platform, str] = {
    Platform.FACEBOOK: "SOCIAL_FB_COOKIES",
    Platform.INSTAGRAM: "SOCIAL_IG_COOKIES",
    Platform.TIKTOK: "SOCIAL_TIKTOK_COOKIES",
}

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)


def default_backup_dir() -> Path:
    """Root directory holding every scrape session (computed at call time)."""
    return home_dir() / ".agent-reach" / "social-backups"


def default_session_dir() -> Path:
    """Directory holding saved browser login states (storage_state.json)."""
    return home_dir() / ".agent-reach" / "social-sessions"


def _as_bool(value: Optional[str], default: bool) -> bool:
    if value is None or not value.strip():
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _as_int(value: Optional[str], default: int, minimum: int = 0) -> int:
    if value is None or not value.strip():
        return default
    try:
        return max(minimum, int(value.strip()))
    except ValueError:
        return default


@dataclass(frozen=True)
class ScraperSettings:
    """Immutable runtime configuration for the scraper engine."""

    backup_dir: Path
    session_dir: Path
    headless: bool = True
    max_scroll: int = 15
    max_items: int = 100
    timeout_ms: int = 45_000
    download_concurrency: int = 4
    download_retries: int = 3
    download_timeout: int = 60
    user_agent: str = DEFAULT_USER_AGENT
    cookie_files: Mapping[Platform, Path] = field(default_factory=dict)

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None) -> "ScraperSettings":
        """Build settings from a mapping (defaults to ``os.environ``)."""
        env = os.environ if env is None else env
        backup = Path(env.get("SOCIAL_BACKUP_DIR") or default_backup_dir()).expanduser()
        sessions = Path(env.get("SOCIAL_SESSION_DIR") or default_session_dir()).expanduser()
        cookie_files: Dict[Platform, Path] = {}
        for platform, key in COOKIE_ENV.items():
            raw = env.get(key)
            if raw:
                cookie_files[platform] = Path(raw).expanduser()
        return cls(
            backup_dir=backup,
            session_dir=sessions,
            headless=_as_bool(env.get("SOCIAL_HEADLESS"), True),
            max_scroll=_as_int(env.get("SOCIAL_MAX_SCROLL"), 15, minimum=1),
            max_items=_as_int(env.get("SOCIAL_MAX_ITEMS"), 100, minimum=1),
            timeout_ms=_as_int(env.get("SOCIAL_TIMEOUT_MS"), 45_000, minimum=1_000),
            download_concurrency=_as_int(env.get("SOCIAL_DOWNLOAD_CONCURRENCY"), 4, minimum=1),
            download_retries=_as_int(env.get("SOCIAL_DOWNLOAD_RETRIES"), 3, minimum=1),
            download_timeout=_as_int(env.get("SOCIAL_DOWNLOAD_TIMEOUT"), 60, minimum=5),
            user_agent=env.get("SOCIAL_USER_AGENT") or DEFAULT_USER_AGENT,
            cookie_files=cookie_files,
        )

    def describe(self) -> dict:
        """Non-secret summary of the active settings."""
        return {
            "backup_dir": str(self.backup_dir),
            "session_dir": str(self.session_dir),
            "headless": self.headless,
            "max_scroll": self.max_scroll,
            "max_items": self.max_items,
            "timeout_ms": self.timeout_ms,
            "download_concurrency": self.download_concurrency,
            "cookie_files": {p.value: str(path) for p, path in self.cookie_files.items()},
        }
