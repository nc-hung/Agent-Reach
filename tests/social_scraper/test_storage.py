# -*- coding: utf-8 -*-
"""Backup store + repository: layout, digests, queries, traversal safety."""

import json

import pytest

from agent_reach.social_scraper.exceptions import StorageError
from agent_reach.social_scraper.models import Platform, ResourceKind
from agent_reach.social_scraper.storage.backup import sanitize_segment
from agent_reach.social_scraper.storage.repository import ResourceRepository

from .conftest import manifest_of, seed_session


# ---------------------------------------------------------------------- #
# Layout & manifest
# ---------------------------------------------------------------------- #
def test_begin_creates_organized_layout(store, settings):
    session_dir = store.begin(Platform.INSTAGRAM, "nasa")
    root = settings.backup_dir.resolve()
    assert session_dir.resolve().is_relative_to(root)
    assert (session_dir / "raw").is_dir()
    assert (session_dir / "media").is_dir()
    rel = store.session_rel(session_dir)
    parts = rel.split("/")
    assert parts[0] == "instagram" and parts[1] == "nasa"


@pytest.mark.parametrize(
    "handle,expected",
    [
        ("nasa", "nasa"),
        ("../../etc", "etc"),
        ("a b/c", "a_b_c"),
        ("..", "_"),
        ("", "_"),
    ],
)
def test_sanitize_segment_blocks_traversal(handle, expected):
    assert sanitize_segment(handle) == expected


def test_full_session_writes_everything(store, settings):
    session_dir, resources, profile = seed_session(store)
    rel_files = sorted(
        p.relative_to(session_dir).as_posix()
        for p in session_dir.rglob("*")
        if p.is_file()
    )
    assert "manifest.json" in rel_files
    assert "profile.json" in rel_files
    assert "index.jsonl" in rel_files
    assert "raw/0001_posts.json" in rel_files

    manifest = manifest_of(session_dir)
    assert manifest is not None
    assert manifest.platform is Platform.INSTAGRAM
    assert manifest.handle == "nasa"
    assert manifest.counts["post"] == 3
    assert manifest.counts["profile"] == 1
    # digests + provenance for raw payloads
    raw_entry = manifest.files["raw/0001_posts.json"]
    assert len(raw_entry["sha256"]) == 64
    assert raw_entry["bucket"] == "posts"
    assert raw_entry["source_url"].startswith("https://")
    assert len(manifest.files["index.jsonl"]["sha256"]) == 64
    assert manifest.media["total"] == 0

    # raw payload is preserved verbatim
    raw_data = json.loads((session_dir / "raw" / "0001_posts.json").read_text())
    assert raw_data == {"items": [{"id": "0"}, {"id": "1"}, {"id": "2"}]}


def test_write_resources_rewrites_atomically(store):
    session_dir, resources, _profile = seed_session(store, resource_count=2)
    store.write_resources(session_dir, resources[:1])
    lines = (session_dir / "index.jsonl").read_text().strip().splitlines()
    assert len(lines) == 1


# ---------------------------------------------------------------------- #
# Repository queries
# ---------------------------------------------------------------------- #
def test_repository_list_get_search(store, settings):
    _session_dir, resources, profile = seed_session(store)
    repo = ResourceRepository(settings.backup_dir)

    assert repo.sessions() and len(repo.sessions()) == 1

    page = repo.list_resources(platform=Platform.INSTAGRAM, handle="nasa")
    assert page["total"] == 3
    assert all(isinstance(r.platform, Platform) for r in page["items"])

    page2 = repo.list_resources(limit=2)
    assert len(page2["items"]) == 2 and page2["total"] == 3
    page3 = repo.list_resources(limit=2, offset=2)
    assert len(page3["items"]) == 1

    found = repo.get(resources[1].id)
    assert found is not None
    assert found.text == "Post 1 about launches and space news 1"
    assert found.session == repo.sessions()[0]

    assert repo.get("does-not-exist") is None
    assert repo.get_in_session(repo.sessions()[0], resources[0].id) is not None

    hits = repo.search("launches")
    assert len(hits) == 3
    assert repo.search("LAUNCHES")  # case-insensitive
    assert repo.search("nonexistent-string") == []

    profile_loaded = repo.profile_of(repo.sessions()[0])
    assert profile_loaded is not None
    assert profile_loaded.kind is ResourceKind.PROFILE

    stats = repo.stats()
    assert stats["sessions"] == 1
    assert stats["targets"] == 1
    assert stats["resources"] == 3
    assert stats["by_platform"] == {"instagram": 3}


def test_repository_read_raw_with_pointer(store, settings):
    _session_dir, resources, _profile = seed_session(store)
    repo = ResourceRepository(settings.backup_dir)
    raw = repo.read_raw(resources[0])
    assert raw == [{"id": "0"}, {"id": "1"}, {"id": "2"}]

    # pointer drift falls back to the whole payload
    resources[0].raw_ref.pointer = "nope.here"
    whole = repo.read_raw(resources[0])
    assert isinstance(whole, dict) and "items" in whole


def test_repository_rejects_traversal(store, settings):
    seed_session(store)
    repo = ResourceRepository(settings.backup_dir)
    with pytest.raises(StorageError):
        repo.session_path("../../etc")
    with pytest.raises(StorageError):
        repo.get_in_session("../..", "x")


def test_targets_grouping(store, settings):
    seed_session(store, resource_count=2)
    repo = ResourceRepository(settings.backup_dir)
    targets = repo.targets()
    assert len(targets) == 1
    assert targets[0]["platform"] == "instagram"
    assert targets[0]["handle"] == "nasa"
    assert targets[0]["resources"] == 2
    assert targets[0]["kinds"]["post"] == 2
    assert repo.targets(Platform.TIKTOK) == []
