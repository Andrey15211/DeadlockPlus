"""Tests for the automatic daily meta updater."""

import json
import shutil
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from deadlockforge.api.app import app
from deadlockforge.engine.meta_updater import MetaUpdater


@pytest.fixture
def temp_meta_dir():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        heroes_src = Path(__file__).resolve().parents[1] / "data" / "heroes.json"
        shutil.copyfile(heroes_src, tmp_path / "heroes.json")
        yield tmp_path


def test_meta_updater_detects_changes(temp_meta_dir):
    updater = MetaUpdater(temp_meta_dir)

    mock_payload = [
        {"hero_id": 1, "bucket": 0, "wins": 600000, "matches": 1000000},
        {"hero_id": 2, "bucket": 0, "wins": 500000, "matches": 1000000},
    ]

    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(mock_payload).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        res = updater.check_and_update(force=True)

        assert res["updated"] is True
        assert res["changes_count"] >= 2
        assert res["error"] is None

        # Verify file was written
        heroes = json.loads((temp_meta_dir / "heroes.json").read_text(encoding="utf-8"))
        infernus = next(h for h in heroes if h["id"] == "infernus")
        assert infernus["winrate"] == 0.6


def test_meta_updater_no_changes_does_not_update(temp_meta_dir):
    updater = MetaUpdater(temp_meta_dir)

    # First update to match mock
    mock_payload = [
        {"hero_id": 1, "bucket": 0, "wins": 500000, "matches": 1000000},
    ]

    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(mock_payload).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        updater.check_and_update(force=True)

        # Second run with exact same numbers
        res2 = updater.check_and_update(force=True)
        assert res2["updated"] is False
        assert res2["changes_count"] == 0


def test_meta_api_endpoints():
    client = TestClient(app)
    r_status = client.get("/api/meta/status")
    assert r_status.status_code == 200
    data = r_status.json()
    assert "is_updating" in data
    assert "updated" in data
