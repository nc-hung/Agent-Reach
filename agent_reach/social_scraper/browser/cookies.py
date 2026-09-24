# -*- coding: utf-8 -*-
"""Cookie file loading — Cookie-Editor JSON and Netscape formats.

Both formats are converted to Playwright ``storage_state`` cookies so users
can either export cookies from the browser (project convention) or run
``agent-reach-social login`` once and reuse the saved state.
"""

import json
from pathlib import Path
from typing import Any, Dict, List

from agent_reach.utils.paths import read_small_text_no_follow

from ..exceptions import SessionError

_MAX_COOKIE_FILE_BYTES = 2 * 1024 * 1024
_SAMESITE_OK = {"Strict", "Lax", "None"}


def _normalize_cookie(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Validate one cookie dict from Cookie-Editor / storage-state JSON."""
    name = raw.get("name")
    value = raw.get("value")
    domain = raw.get("domain")
    if not isinstance(name, str) or not name:
        raise SessionError("Cookie file contains a cookie without a name")
    if not isinstance(value, str):
        raise SessionError(f"Cookie '{name}' has a non-string value")
    if not isinstance(domain, str) or not domain:
        raise SessionError(f"Cookie '{name}' has no domain")

    expires = raw.get("expires", -1)
    try:
        expires = float(expires)
    except (TypeError, ValueError):
        expires = -1.0

    same_site = raw.get("sameSite") or "Lax"
    if same_site not in _SAMESITE_OK:
        same_site = "Lax"

    return {
        "name": name,
        "value": value,
        "domain": domain,
        "path": str(raw.get("path") or "/"),
        "expires": expires,
        "httpOnly": bool(raw.get("httpOnly", False)),
        "secure": bool(raw.get("secure", True)),
        "sameSite": same_site,
    }


def _parse_cookie_editor(payload: Any) -> List[Dict[str, Any]]:
    if not isinstance(payload, list):
        raise SessionError("Cookie-Editor JSON must be a list of cookies")
    return [_normalize_cookie(item) for item in payload if isinstance(item, dict)]


def _parse_storage_state(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    cookies = payload.get("cookies")
    if not isinstance(cookies, list):
        raise SessionError("storage_state JSON must contain a 'cookies' list")
    return [_normalize_cookie(item) for item in cookies if isinstance(item, dict)]


def _parse_netscape(text: str) -> List[Dict[str, Any]]:
    cookies: List[Dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 7:
            continue
        domain, _include, path, secure, expires, name, value = parts[:7]
        cookies.append(
            _normalize_cookie(
                {
                    "name": name,
                    "value": value,
                    "domain": domain,
                    "path": path,
                    "secure": secure.upper() == "TRUE",
                    "expires": float(expires) if expires.isdigit() else -1,
                    "sameSite": "Lax",
                }
            )
        )
    return cookies


def load_cookies(path: Path) -> List[Dict[str, Any]]:
    """Load cookies from Cookie-Editor JSON, storage-state JSON or Netscape."""
    path = Path(path)
    if not path.is_file():
        raise SessionError(f"Cookie file not found: {path}")
    text = read_small_text_no_follow(path, max_bytes=_MAX_COOKIE_FILE_BYTES)
    if text is None:
        raise SessionError(f"Cookie file is missing or too large: {path}")
    stripped = text.lstrip()
    if stripped.startswith("{"):
        payload = json.loads(text)
        if isinstance(payload, dict) and "cookies" in payload:
            return _parse_storage_state(payload)
        raise SessionError(
            f"Unsupported cookie JSON object in {path} (expected a list "
            "or a storage_state with 'cookies')"
        )
    if stripped.startswith("["):
        return _parse_cookie_editor(json.loads(text))
    cookies = _parse_netscape(text)
    if not cookies:
        raise SessionError(f"No cookies found in {path}")
    return cookies


def storage_state_from_cookies(cookies: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Wrap plain cookies into a Playwright storage_state document."""
    return {"cookies": cookies, "origins": []}
