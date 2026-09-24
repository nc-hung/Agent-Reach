# -*- coding: utf-8 -*-
"""Manifest IO — the inventory document of one backup session."""

import json
from pathlib import Path
from typing import Optional

from agent_reach.utils.paths import atomic_write_private_text, read_small_text_no_follow

from ..exceptions import StorageError
from ..models import Manifest

_MANIFEST_NAME = "manifest.json"
_MAX_MANIFEST_BYTES = 4 * 1024 * 1024


def write_manifest(session_dir: Path, manifest: Manifest) -> Path:
    """Atomically write ``manifest.json`` into the session directory."""
    target = Path(session_dir) / _MANIFEST_NAME
    payload = json.dumps(manifest.to_dict(), ensure_ascii=False, indent=2)
    atomic_write_private_text(target, payload)
    return target


def read_manifest(session_dir: Path) -> Optional[Manifest]:
    """Read ``manifest.json`` (None when missing/unreadable)."""
    path = Path(session_dir) / _MANIFEST_NAME
    text = read_small_text_no_follow(path, max_bytes=_MAX_MANIFEST_BYTES)
    if text is None:
        return None
    try:
        return Manifest.from_dict(json.loads(text))
    except (ValueError, KeyError, TypeError) as exc:
        raise StorageError(f"Corrupt manifest in {session_dir}: {exc}") from exc
