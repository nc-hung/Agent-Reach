# -*- coding: utf-8 -*-
"""BackupStore implementation — professional, digested, tamper-evident.

Directory layout (one session per scrape run)::

    <root>/<platform>/<handle>/<2026-09-24T12-00-00Z>/
        manifest.json     inventory: counts, sha256, media stats, errors
        profile.json      the target's profile document
        index.jsonl       one normalized resource per line (queryable)
        raw/0001_*.json   every payload the platform returned, verbatim
        media/*           downloaded media files (sha256 in asset fields)
"""

import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from agent_reach.utils.paths import atomic_write_private_text, make_private_dir

from ..exceptions import StorageError
from ..interfaces import BackupStore
from ..models import (
    Manifest,
    Platform,
    RawPayload,
    Resource,
    ResourceKind,
    ScrapeBundle,
    iso_now,
    session_stamp,
    to_jsonable,
)
from .manifest import read_manifest, write_manifest

_SAFE_SEGMENT = re.compile(r"[^A-Za-z0-9._-]")
_MAX_SEGMENT = 80


def sanitize_segment(segment: str) -> str:
    """Make a single path segment safe (no traversal, no surprises)."""
    cleaned = _SAFE_SEGMENT.sub("_", (segment or "").strip())
    cleaned = cleaned.strip("._")
    if not cleaned or cleaned in {".", ".."}:
        cleaned = "_"
    return cleaned[:_MAX_SEGMENT]


def sha256_file(path: Path) -> str:
    """sha256 of a file, streamed."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class BackupStoreImpl(BackupStore):
    """Filesystem-backed backup store under a configurable root."""

    def __init__(self, root: Path):
        self.root = Path(root).expanduser()

    # ------------------------------------------------------------------ #
    # Session lifecycle
    # ------------------------------------------------------------------ #
    def begin(self, platform: Platform, handle: str) -> Path:
        """Create ``<root>/<platform>/<handle>/<stamp>/`` (+ raw/ media/)."""
        if not handle:
            raise StorageError("Cannot start a backup session without a handle")
        session_dir = (
            self.root
            / sanitize_segment(platform.value)
            / sanitize_segment(handle)
            / session_stamp()
        )
        make_private_dir(session_dir)
        make_private_dir(session_dir / "raw")
        make_private_dir(session_dir / "media")
        return session_dir

    def session_rel(self, session_dir: Path) -> str:
        """Relative session id used in URIs/manifests."""
        try:
            return Path(session_dir).resolve().relative_to(self.root.resolve()).as_posix()
        except ValueError:
            return Path(session_dir).name

    def session_dir_for(self, session_rel: str) -> Path:
        """Resolve a relative session id, refusing traversal."""
        candidate = (self.root / session_rel).resolve()
        if self.root.resolve() not in candidate.parents and candidate != self.root.resolve():
            raise StorageError(f"Session path escapes the backup root: {session_rel}")
        return candidate

    # ------------------------------------------------------------------ #
    # Writers
    # ------------------------------------------------------------------ #
    def write_raws(self, session_dir: Path, raws: Sequence[RawPayload]) -> None:
        """Persist every captured payload verbatim under ``raw/``."""
        raw_dir = Path(session_dir) / "raw"
        make_private_dir(raw_dir)
        for raw in raws:
            if not raw.file:
                raw.file = f"raw/{raw.seq:04d}_{raw.bucket}.json"
            target = Path(session_dir) / raw.file
            payload = json.dumps(
                to_jsonable(raw.data), ensure_ascii=False, indent=2, default=str
            )
            atomic_write_private_text(target, payload)

    def write_profile(self, session_dir: Path, profile: Optional[Resource]) -> None:
        """Persist ``profile.json`` (or remove a stale one)."""
        target = Path(session_dir) / "profile.json"
        if profile is None:
            target.unlink(missing_ok=True)
            return
        payload = json.dumps(to_jsonable(profile), ensure_ascii=False, indent=2)
        atomic_write_private_text(target, payload)

    def write_resources(self, session_dir: Path, resources: Sequence[Resource]) -> None:
        """Persist (or refresh) ``index.jsonl`` with session back-references."""
        rel = self.session_rel(session_dir)
        lines: List[str] = []
        for res in resources:
            res.session = rel
            lines.append(json.dumps(to_jsonable(res), ensure_ascii=False, default=str))
        target = Path(session_dir) / "index.jsonl"
        atomic_write_private_text(target, "\n".join(lines) + ("\n" if lines else ""))

    # ------------------------------------------------------------------ #
    # Finalization
    # ------------------------------------------------------------------ #
    def finalize(
        self,
        session_dir: Path,
        bundle: ScrapeBundle,
        media_summary: Dict[str, int],
    ) -> Manifest:
        """Compute digests/counts and write ``manifest.json``."""
        session_dir = Path(session_dir)
        counts: Dict[str, int] = dict(
            Counter(res.kind.value for res in bundle.resources)
        )
        if bundle.profile is not None:
            counts[ResourceKind.PROFILE.value] = 1

        files: Dict[str, Dict[str, Any]] = {}
        for name in ("index.jsonl", "profile.json", "manifest.json"):
            path = session_dir / name
            if path.is_file() and name != "manifest.json":
                files[name] = {
                    "bytes": path.stat().st_size,
                    "sha256": sha256_file(path),
                }
        raw_dir = session_dir / "raw"
        raw_files: List[str] = []
        provenance = {raw.file: raw for raw in bundle.raws}
        if raw_dir.is_dir():
            for path in sorted(raw_dir.glob("*.json")):
                rel = f"raw/{path.name}"
                raw_files.append(rel)
                entry: Dict[str, Any] = {
                    "bytes": path.stat().st_size,
                    "sha256": sha256_file(path),
                }
                source = provenance.get(rel)
                if source is not None:
                    entry["bucket"] = source.bucket
                    entry["source_url"] = source.source_url
                files[rel] = entry

        media = {
            "total": int(media_summary.get("total", 0)),
            "downloaded": int(media_summary.get("downloaded", 0)),
            "skipped": int(media_summary.get("skipped", 0)),
            "failed": int(media_summary.get("failed", 0)),
        }

        manifest = Manifest(
            session=self.session_rel(session_dir),
            platform=bundle.platform,
            handle=bundle.handle,
            source_url=bundle.source_url,
            started_at=bundle.started_at,
            finished_at=bundle.finished_at or iso_now(),
            counts=counts,
            files=files,
            media=media,
            stats=dict(bundle.stats),
            errors=list(bundle.errors),
        )
        write_manifest(session_dir, manifest)
        return manifest

    def read_manifest(self, session_dir: Path) -> Optional[Manifest]:
        """Load a session manifest when present."""
        return read_manifest(session_dir)

    def update_manifest_media(self, session_dir: Path, media_summary: Dict[str, int]) -> None:
        """Refresh media counters after a later download run."""
        manifest = self.read_manifest(session_dir)
        if manifest is None:
            return
        for key in ("total", "downloaded", "skipped", "failed"):
            if key in media_summary:
                manifest.media[key] = int(media_summary[key])
        write_manifest(session_dir, manifest)
