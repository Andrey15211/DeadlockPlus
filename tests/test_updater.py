import json
import time
from unittest.mock import MagicMock, patch
from pathlib import Path

import pytest
from deadlockforge.updater import (
    UpdateManager,
    is_newer_version,
    parse_semver,
)


def test_parse_semver():
    assert parse_semver("v1.0.0") == (1, 0, 0)
    assert parse_semver("1.2.3") == (1, 2, 3)
    assert parse_semver("v2.1.0-beta.1") == (2, 1, 0)
    assert parse_semver("3.4") == (3, 4, 0)
    assert parse_semver("") == (0, 0, 0)


def test_is_newer_version():
    assert is_newer_version("v1.0.1", "1.0.0") is True
    assert is_newer_version("1.1.0", "1.0.0") is True
    assert is_newer_version("2.0.0", "1.9.9") is True
    assert is_newer_version("1.0.0", "1.0.0") is False
    assert is_newer_version("0.9.9", "1.0.0") is False
    assert is_newer_version("v1.0.0-rc1", "1.0.0") is False


def test_check_for_updates_available():
    manager = UpdateManager(repo_slug="test/repo", current_version="1.0.0")
    fake_release = {
        "tag_name": "v1.1.0",
        "name": "Deadlock Plus 1.1.0",
        "body": "### New Features\n- Auto-update support\n- UI improvements",
        "published_at": "2026-10-25T00:00:00Z",
        "html_url": "https://github.com/test/repo/releases/tag/v1.1.0",
        "assets": [
            {
                "name": "DeadlockPlus.exe",
                "browser_download_url": "https://github.com/test/repo/releases/download/v1.1.0/DeadlockPlus.exe",
                "size": 65432100,
            }
        ],
    }

    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps(fake_release).encode("utf-8")
    mock_resp.__enter__.return_value = mock_resp

    with patch("urllib.request.urlopen", return_value=mock_resp):
        info = manager.check_for_updates(force=True)

    assert info["update_available"] is True
    assert info["latest_version"] == "1.1.0"
    assert info["current_version"] == "1.0.0"
    assert info["asset_name"] == "DeadlockPlus.exe"
    assert info["asset_url"].endswith("DeadlockPlus.exe")
    assert info["asset_size"] == 65432100
    assert "Auto-update support" in info["release_notes"]


def test_check_for_updates_not_available():
    manager = UpdateManager(repo_slug="test/repo", current_version="1.0.0")
    fake_release = {
        "tag_name": "v1.0.0",
        "name": "Deadlock Plus 1.0.0",
        "body": "Initial release",
        "published_at": "2026-10-24T00:00:00Z",
        "html_url": "https://github.com/test/repo/releases/tag/v1.0.0",
        "assets": [],
    }

    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps(fake_release).encode("utf-8")
    mock_resp.__enter__.return_value = mock_resp

    with patch("urllib.request.urlopen", return_value=mock_resp):
        info = manager.check_for_updates(force=True)

    assert info["update_available"] is False
    assert info["latest_version"] == "1.0.0"


def test_check_for_updates_404_no_releases():
    import urllib.error

    manager = UpdateManager(repo_slug="test/repo", current_version="1.0.0")
    err = urllib.error.HTTPError("url", 404, "Not Found", {}, None)

    with patch("urllib.request.urlopen", side_effect=err):
        info = manager.check_for_updates(force=True)

    assert info["update_available"] is False
    assert info["error"] is None
    assert "Официальных релизов пока не опубликовано" in info["release_notes"]


def test_download_and_progress(tmp_path):
    manager = UpdateManager(repo_slug="test/repo", current_version="1.0.0")
    content = b"DEADLOCK_EXE_BINARY_DATA" * 1000

    mock_resp = MagicMock()
    mock_resp.headers = {"Content-Length": str(len(content))}
    mock_resp.read.side_effect = [content[:5000], content[5000:], b""]
    mock_resp.__enter__.return_value = mock_resp

    with patch("tempfile.gettempdir", return_value=str(tmp_path)):
        with patch("urllib.request.urlopen", return_value=mock_resp):
            status = manager.start_download("https://fake.url/DeadlockPlus.exe")
            assert status["status"] == "downloading"

            # Wait for thread to finish
            if manager._download_thread:
                manager._download_thread.join(timeout=3)

            final_status = manager.get_download_status()
            assert final_status["status"] == "ready"
            assert final_status["progress_pct"] == 100
            assert final_status["downloaded_bytes"] == len(content)
            target = Path(final_status["target_file"])
            assert target.is_file()
            assert target.read_bytes() == content
