# -*- coding: utf-8 -*-
"""Browser session factory — Playwright launch, login state, API fetches.

Session state lives in ``<session_dir>/<platform>.json`` (Playwright
storage_state). It can be created two ways:

1. ``agent-reach-social login <platform>`` — headed browser, user logs in.
2. A cookie file (Cookie-Editor export) via ``SOCIAL_<XX>_COOKIES``.
"""

import asyncio
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent_reach.utils.paths import (
    atomic_write_private_text,
    make_private_dir,
    read_small_text_no_follow,
)

from ..config import ScraperSettings
from ..exceptions import DependencyError, SessionError
from ..models import Platform
from .cookies import load_cookies, storage_state_from_cookies

_STORAGE_STATE_MAX_BYTES = 4 * 1024 * 1024


def ensure_playwright() -> Any:
    """Import playwright's async API or explain how to install it."""
    try:
        from playwright.async_api import async_playwright  # noqa: PLC0415
    except ImportError as exc:
        raise DependencyError(
            "Playwright is required for Facebook/Instagram/TikTok scraping.\n"
            "Install with:\n"
            "  pip install 'agent-reach[social]'\n"
            "  playwright install chromium"
        ) from exc
    return async_playwright


class BrowserSession:
    """A live browser context bound to one platform."""

    def __init__(
        self,
        playwright: Any,
        browser: Any,
        context: Any,
        platform: Platform,
        warnings: Optional[List[str]] = None,
    ):
        self._playwright = playwright
        self.browser = browser
        self.context = context
        self.platform = platform
        self.warnings: List[str] = warnings or []

    async def new_page(self) -> Any:
        """Open a fresh page in this context."""
        return await self.context.new_page()

    async def attach_capture(self, capture: Any) -> None:
        """Wire a ResponseCapture to every response of this session."""
        capture.attach(self.context)

    async def api_get(self, url: str, headers: Optional[Dict[str, str]] = None) -> Optional[str]:
        """GET through the same logged-in session; returns body text or None."""
        try:
            response = await self.context.request.get(url, headers=headers or {})
            if response.status >= 400:
                return None
            return await response.text()
        except Exception:
            return None

    async def cookie_names(self) -> List[str]:
        """Names of the cookies currently set in the context."""
        try:
            return [c.get("name", "") for c in await self.context.cookies()]
        except Exception:
            return []

    async def close(self) -> None:
        """Close context, browser and the playwright driver."""
        for closer in (self.context, self.browser):
            try:
                await closer.close()
            except Exception:
                pass
        try:
            await self._playwright.stop()
        except Exception:
            pass


class SessionFactory:
    """Creates logged-in browser sessions for a platform."""

    def __init__(self, settings: ScraperSettings):
        self.settings = settings

    # ------------------------------------------------------------------ #
    # State helpers
    # ------------------------------------------------------------------ #
    def storage_state_path(self, platform: Platform) -> Path:
        """Where the saved login state for ``platform`` lives."""
        return Path(self.settings.session_dir) / f"{platform.value}.json"

    def _storage_state(self, platform: Platform) -> Optional[Dict[str, Any]]:
        """Load saved state, falling back to a configured cookie file."""
        state_path = self.storage_state_path(platform)
        text = read_small_text_no_follow(state_path, max_bytes=_STORAGE_STATE_MAX_BYTES)
        if text is not None:
            try:
                payload = json.loads(text)
                if isinstance(payload, dict) and payload.get("cookies"):
                    return payload
            except ValueError:
                pass
        cookie_file = self.settings.cookie_files.get(platform)
        if cookie_file is not None:
            cookies = load_cookies(Path(cookie_file))
            if cookies:
                return storage_state_from_cookies(cookies)
        return None

    # ------------------------------------------------------------------ #
    # Launch
    # ------------------------------------------------------------------ #
    async def open(
        self,
        platform: Platform,
        headless: bool = True,
        session_cookie: str = "",
    ) -> BrowserSession:
        """Launch a browser context for ``platform`` (anonymous if no state)."""
        async_playwright = ensure_playwright()
        warnings: List[str] = []
        state = self._storage_state(platform)
        if state is None and session_cookie:
            warnings.append(
                f"No saved session for {platform.value}; scraping public data "
                f"only. Run `agent-reach-social login {platform.value}` to "
                "unlock everything the platform serves to logged-in users."
            )

        playwright = await async_playwright().start()
        browser = await playwright.chromium.launch(
            headless=headless,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context_kwargs: Dict[str, Any] = {
            "user_agent": self.settings.user_agent,
            "locale": "en-US",
            "viewport": {"width": 1366, "height": 900},
        }
        if state is not None:
            context_kwargs["storage_state"] = state
        try:
            context = await browser.new_context(**context_kwargs)
        except Exception as exc:
            await browser.close()
            await playwright.stop()
            raise SessionError(f"Could not create browser context: {exc}") from exc

        if session_cookie:
            names = await BrowserSession(
                playwright, browser, context, platform
            ).cookie_names()
            if session_cookie not in names:
                warnings.append(
                    f"Session cookie '{session_cookie}' missing for "
                    f"{platform.value}; results may be incomplete. "
                    f"Run `agent-reach-social login {platform.value}`."
                )
        return BrowserSession(playwright, browser, context, platform, warnings)

    # ------------------------------------------------------------------ #
    # Interactive login
    # ------------------------------------------------------------------ #
    async def login(
        self,
        platform: Platform,
        login_url: str,
        timeout_s: int = 240,
        headless: bool = False,
    ) -> Path:
        """Open a visible browser, wait for the user to log in, save state.

        Polls for the platform's session cookie; returns the saved path.
        """
        async_playwright = ensure_playwright()
        expected = {
            Platform.FACEBOOK: {"c_user", "xs"},
            Platform.INSTAGRAM: {"sessionid"},
            Platform.TIKTOK: {"sessionid", "sid_guard"},
        }[platform]

        playwright = await async_playwright().start()
        browser = await playwright.chromium.launch(headless=headless)
        context = await browser.new_context(
            user_agent=self.settings.user_agent,
            locale="en-US",
            viewport={"width": 1280, "height": 900},
        )
        page = await context.new_page()
        try:
            await page.goto(login_url, wait_until="domcontentloaded", timeout=60_000)
            deadline = asyncio.get_event_loop().time() + timeout_s
            while asyncio.get_event_loop().time() < deadline:
                names = set(await BrowserSession(
                    playwright, browser, context, platform
                ).cookie_names())
                if expected & names:
                    return await self._save_state(platform, context)
                await asyncio.sleep(2)
            raise SessionError(
                f"Login for {platform.value} timed out after {timeout_s}s. "
                "Re-run the command and complete the login in the opened window."
            )
        finally:
            for closer in (context, browser):
                try:
                    await closer.close()
                except Exception:
                    pass
            try:
                await playwright.stop()
            except Exception:
                pass

    async def _save_state(self, platform: Platform, context: Any) -> Path:
        """Persist the context's storage_state with private permissions."""
        state = await context.storage_state()
        target = self.storage_state_path(platform)
        make_private_dir(target.parent)
        atomic_write_private_text(target, json.dumps(state, indent=2))
        return target
