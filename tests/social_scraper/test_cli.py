# -*- coding: utf-8 -*-
"""CLI end-to-end behaviors over a seeded backup (no browser needed)."""

import json

import pytest

from agent_reach.social_scraper import cli
from agent_reach.social_scraper.storage.backup import BackupStoreImpl

from .conftest import seed_session


@pytest.fixture
def seeded_backup(settings):
    store = BackupStoreImpl(settings.backup_dir)
    session_dir, resources, _profile = seed_session(store)
    return session_dir, resources


def test_status_json(settings, capsys):
    code = cli.main(["status", "--json"])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["backup_dir"] == str(settings.backup_dir)
    assert payload["sessions"] == 0
    assert "playwright" in payload["dependencies"]


def test_list_and_targets(settings, seeded_backup, capsys):
    assert cli.main(["list", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["total"] == 3

    assert cli.main(["list", "--targets", "--json"]) == 0
    targets = json.loads(capsys.readouterr().out)["targets"]
    assert targets[0]["handle"] == "nasa"

    assert cli.main(["list", "--platform", "tiktok", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["total"] == 0


def test_list_table_output(settings, seeded_backup, capsys):
    assert cli.main(["list", "-n", "2"]) == 0
    out = capsys.readouterr().out
    assert "launches" in out  # table shows resource text
    assert "resources" in out.lower()


def test_show_with_raw(settings, seeded_backup, capsys):
    _session, resources = seeded_backup
    assert cli.main(["show", resources[0].id, "--json", "--raw"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["id"] == resources[0].id
    assert payload["raw"] == [{"id": "0"}, {"id": "1"}, {"id": "2"}]


def test_show_raw_accepts_large_payload(settings, seeded_backup, capsys):
    # real GraphQL payloads exceed 256 KiB — show --raw must not crash on them
    session_dir, resources = seeded_backup
    raw_path = session_dir / resources[0].raw_ref.file
    raw_path.write_text(
        json.dumps({"items": [{"id": "0", "pad": "x" * (300 * 1024)}]})
    )
    assert cli.main(["show", resources[0].id, "--json", "--raw"]) == 0
    payload = json.loads(capsys.readouterr().out)
    # raw_ref pointer "items" → the payload resolves to that list
    assert payload["raw"][0]["id"] == "0"


def test_show_missing_id(settings, capsys):
    assert cli.main(["show", "nope", "--json"]) == 1
    assert "not found" in capsys.readouterr().out.lower()


def test_search(settings, seeded_backup, capsys):
    assert cli.main(["search", "launches", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert len(payload["items"]) == 3

    assert cli.main(["search", "nothing-matches", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["items"] == []


def test_download_target_without_media(settings, seeded_backup, capsys):
    _session, _resources = seeded_backup
    assert (
        cli.main(
            ["download", "--platform", "instagram", "--handle", "nasa", "--json"]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["total"] == 0  # seeded resources carry no media


def test_download_requires_args(settings, capsys):
    assert cli.main(["download"]) == 1
    assert "--id" in capsys.readouterr().out


def test_unknown_platform_is_usage_error(settings, capsys):
    assert cli.main(["list", "--platform", "youtube"]) == 1
    assert "Unknown platform" in capsys.readouterr().out


def test_unknown_kind_is_reported(settings, capsys):
    assert cli.main(["list", "--kind", "banana"]) == 1
    assert "Unknown kind" in capsys.readouterr().out


def test_scrape_unsupported_url(settings, capsys):
    assert cli.main(["scrape", "https://example.com/x", "--json"]) == 2
    out = capsys.readouterr().out
    assert "error" in out.lower()
