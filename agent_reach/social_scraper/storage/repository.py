# -*- coding: utf-8 -*-
"""ResourceRepository — the read side over the backup root.

Sessions are ``<platform>/<handle>/<stamp>`` directories; every session has
an ``index.jsonl``. The repository lazily loads them (mtime-validated cache)
and serves listing / lookup / search / raw-payload reads for the CLI and MCP.
"""

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from agent_reach.utils.paths import read_small_text_no_follow

from ..exceptions import StorageError
from ..interfaces import ResourceQuery
from ..models import (
    Platform,
    Resource,
    ResourceKind,
    resource_from_dict,
    resources_from_lines,
)

_INDEX_NAME = "index.jsonl"
_MAX_INDEX_BYTES = 64 * 1024 * 1024
_MAX_RAW_BYTES = 8 * 1024 * 1024


def _as_kinds(kinds: Optional[Sequence[Any]]) -> Optional[set]:
    if not kinds:
        return None
    out = set()
    for kind in kinds:
        out.add(kind.value if isinstance(kind, ResourceKind) else str(kind))
    return out


class ResourceRepository(ResourceQuery):
    """Read-side over a backup root (safe against path traversal)."""

    def __init__(self, root: Path):
        self.root = Path(root).expanduser()
        self._cache: Dict[str, Tuple[float, List[Resource]]] = {}

    # ------------------------------------------------------------------ #
    # Session discovery
    # ------------------------------------------------------------------ #
    def sessions(self) -> List[str]:
        """All session ids (relative), newest first."""
        found: List[str] = []
        if not self.root.is_dir():
            return found
        for platform_dir in sorted(self.root.iterdir()):
            if not platform_dir.is_dir():
                continue
            for handle_dir in sorted(platform_dir.iterdir()):
                if not handle_dir.is_dir():
                    continue
                for session_dir in handle_dir.iterdir():
                    if session_dir.is_dir() and (session_dir / _INDEX_NAME).is_file():
                        found.append(
                            session_dir.relative_to(self.root).as_posix()
                        )
        found.sort(reverse=True)  # ISO stamps sort lexicographically
        return found

    def _session_dir(self, session_rel: str) -> Path:
        candidate = (self.root / session_rel).resolve()
        root_resolved = self.root.resolve()
        if candidate != root_resolved and root_resolved not in candidate.parents:
            raise StorageError(f"Session escapes the backup root: {session_rel}")
        return candidate

    #: public alias (MCP server validates URIs through this)
    session_path = _session_dir

    def load_session(self, session_rel: str) -> List[Resource]:
        """Public accessor for one session's resources (uses the cache)."""
        return self._load_session(session_rel)

    def invalidate(self, session_rel: str) -> None:
        """Drop the cached copy of a session (after external rewrites)."""
        self._cache.pop(session_rel, None)

    def _load_session(self, session_rel: str) -> List[Resource]:
        """Load one session's resources (cached by index mtime)."""
        session_dir = self._session_dir(session_rel)
        index = session_dir / _INDEX_NAME
        try:
            mtime = index.stat().st_mtime
        except OSError:
            return []
        cached = self._cache.get(session_rel)
        if cached is not None and cached[0] == mtime:
            return cached[1]
        text = read_small_text_no_follow(index, max_bytes=_MAX_INDEX_BYTES)
        resources = resources_from_lines((text or "").splitlines())
        for res in resources:
            res.session = session_rel
        self._cache[session_rel] = (mtime, resources)
        return resources

    def _iter_all(
        self,
        platform: Optional[Platform] = None,
        handle: Optional[str] = None,
    ) -> Iterable[Tuple[str, Resource]]:
        """Yield (session_rel, resource) across matching sessions, newest first."""
        for session_rel in self.sessions():
            parts = session_rel.split("/")
            if len(parts) != 3:
                continue
            if platform is not None and parts[0] != platform.value:
                continue
            if handle is not None and parts[1].lower() != handle.lower():
                continue
            for res in self._load_session(session_rel):
                yield session_rel, res

    # ------------------------------------------------------------------ #
    # ResourceQuery contract
    # ------------------------------------------------------------------ #
    def targets(self, platform: Optional[Platform] = None) -> List[Dict[str, Any]]:
        """Known targets with totals and latest session info."""
        agg: Dict[Tuple[str, str], Dict[str, Any]] = {}
        for session_rel, res in self._iter_all(platform=platform):
            parts = session_rel.split("/")
            key = (parts[0], parts[1])
            entry = agg.setdefault(
                key,
                {
                    "platform": parts[0],
                    "handle": parts[1],
                    "resources": 0,
                    "kinds": {},
                    "latest_session": session_rel,
                },
            )
            entry["resources"] += 1
            kind = res.kind.value
            entry["kinds"][kind] = entry["kinds"].get(kind, 0) + 1
            if session_rel > entry["latest_session"]:
                entry["latest_session"] = session_rel
        out = list(agg.values())
        out.sort(key=lambda e: (e["platform"], e["handle"]))
        return out

    def list_resources(
        self,
        platform: Optional[Platform] = None,
        handle: Optional[str] = None,
        kinds: Optional[Sequence[ResourceKind]] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Dict[str, Any]:
        """Paginated listing filtered by platform/handle/kinds."""
        wanted = _as_kinds(kinds)
        matched: List[Resource] = []
        seen: set = set()
        for _session, res in self._iter_all(platform=platform, handle=handle):
            if wanted is not None and res.kind.value not in wanted:
                continue
            if res.id in seen:
                continue
            seen.add(res.id)
            matched.append(res)
        total = len(matched)
        offset = max(0, offset)
        page = matched[offset : offset + max(1, limit)] if limit > 0 else matched[offset:]
        return {"total": total, "items": page, "offset": offset, "limit": limit}

    def get(self, resource_id: str) -> Optional[Resource]:
        """Fetch one resource by id (newest session wins)."""
        for _session, res in self._iter_all():
            if res.id == resource_id:
                return res
        return None

    def get_in_session(self, session_rel: str, resource_id: str) -> Optional[Resource]:
        """Fetch a resource inside a specific session."""
        for res in self._load_session(session_rel):
            if res.id == resource_id:
                return res
        return None

    def read_raw(self, resource: Resource, max_bytes: int = 0) -> Any:
        """Return the raw payload a resource was parsed from (best effort)."""
        if not resource.raw_ref.file:
            return {"error": "resource has no raw back-reference"}
        if not resource.session:
            return {"error": "resource is not bound to a session"}
        session_dir = self._session_dir(resource.session)
        path = (session_dir / resource.raw_ref.file).resolve()
        if session_dir.resolve() not in path.parents:
            raise StorageError("Raw file escapes the session directory")
        limit = max_bytes or _MAX_RAW_BYTES
        text = read_small_text_no_follow(path, max_bytes=limit)
        if text is None:
            return {"error": f"raw file missing or larger than {limit} bytes",
                    "file": resource.raw_ref.file}
        try:
            data = json.loads(text)
        except ValueError as exc:
            return {"error": f"raw file is not valid JSON: {exc}"}
        pointer = resource.raw_ref.pointer
        if pointer:
            data = self._walk_pointer(data, pointer)
        return data

    @staticmethod
    def _walk_pointer(data: Any, pointer: str) -> Any:
        current = data
        for part in pointer.split("."):
            if isinstance(current, dict) and part in current:
                current = current[part]
            elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
                current = current[int(part)]
            else:
                return data  # pointer drifted → hand back the whole payload
        return current

    def search(
        self,
        query: str,
        kinds: Optional[Sequence[ResourceKind]] = None,
        limit: int = 20,
    ) -> List[Resource]:
        """Case-insensitive substring search over text/author/url/handle."""
        needle = (query or "").strip().lower()
        if not needle:
            return []
        wanted = _as_kinds(kinds)
        hits: List[Resource] = []
        seen: set = set()
        for _session, res in self._iter_all():
            if len(hits) >= limit:
                break
            if wanted is not None and res.kind.value not in wanted:
                continue
            haystack = " ".join(
                (res.text, res.author, res.url, res.handle)
            ).lower()
            if needle not in haystack:
                continue
            if res.id in seen:
                continue
            seen.add(res.id)
            hits.append(res)
        return hits

    def stats(self) -> Dict[str, Any]:
        """Aggregate counters for ``social_status``."""
        sessions = self.sessions()
        by_platform: Dict[str, int] = {}
        by_kind: Dict[str, int] = {}
        total = 0
        targets: set = set()
        for session_rel, res in self._iter_all():
            total += 1
            by_platform[res.platform.value] = by_platform.get(res.platform.value, 0) + 1
            by_kind[res.kind.value] = by_kind.get(res.kind.value, 0) + 1
            targets.add(f"{res.platform.value}/{res.handle}")
        return {
            "backup_dir": str(self.root),
            "sessions": len(sessions),
            "targets": len(targets),
            "resources": total,
            "by_platform": by_platform,
            "by_kind": by_kind,
        }

    def profile_of(self, session_rel: str) -> Optional[Resource]:
        """Load ``profile.json`` of a session when present."""
        session_dir = self._session_dir(session_rel)
        text = read_small_text_no_follow(session_dir / "profile.json", max_bytes=1_048_576)
        if not text:
            return None
        try:
            data = json.loads(text)
            res = resource_from_dict(data)
            res.session = session_rel
            return res
        except (ValueError, KeyError, TypeError):
            return None
