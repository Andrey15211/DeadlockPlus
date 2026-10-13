"""Stage 4 Performance & Verification regression test suite."""

import sys
from pathlib import Path
import pytest
from starlette.testclient import TestClient

from deadlockforge.api.app import create_app
from deadlockforge.storage.db import get_database


@pytest.fixture(scope="module")
def client():
    app = create_app()
    return TestClient(app)


def test_catalog_etags_and_304(client):
    """Verify that catalog endpoints /api/heroes, /api/items, /api/map return ETag and 304 on If-None-Match."""
    for ep in ["/api/heroes", "/api/items", "/api/map"]:
        res = client.get(ep)
        assert res.status_code == 200
        etag = res.headers.get("etag")
        assert etag is not None, f"Missing ETag on {ep}"
        
        # Check 304 response
        res_304 = client.get(ep, headers={"If-None-Match": etag})
        assert res_304.status_code == 304, f"Expected 304 for {ep}"
        assert res_304.content == b""
        assert res_304.headers.get("etag") == etag


def test_static_cache_headers(client):
    """Verify Cache-Control headers for static assets, scripts, styles, and index."""
    # Index must not be cached
    res_index = client.get("/")
    assert res_index.status_code == 200
    cc = res_index.headers.get("cache-control", "")
    assert "no-cache" in cc and "no-store" in cc

    # Asset must have 1 day caching + SWR
    res_asset = client.get("/assets/map/Map_colored.png")
    assert res_asset.status_code == 200
    asset_cc = res_asset.headers.get("cache-control", "")
    assert "public" in asset_cc and "max-age=86400" in asset_cc and "stale-while-revalidate=3600" in asset_cc

    # JS & CSS files must serve ETags and 304
    for path in ["/js/app.js", "/js/calc.js", "/css/hud.css", "/css/styles.css"]:
        r = client.get(path)
        assert r.status_code == 200
        etag = r.headers.get("etag")
        assert etag is not None
        r304 = client.get(path, headers={"If-None-Match": etag})
        assert r304.status_code == 304
        assert r304.content == b""


def test_sqlite_pragmas_and_indexes():
    """Verify SQLite WAL mode, PRAGMA synchronous, PRAGMA temp_store, and indexes."""
    db = get_database()
    with db._lock:
        cur = db._conn.cursor()
        cur.execute("PRAGMA journal_mode;")
        assert cur.fetchone()[0].lower() == "wal"

        cur.execute("PRAGMA synchronous;")
        assert cur.fetchone()[0] in (1, "NORMAL", "normal")

        cur.execute("PRAGMA temp_store;")
        assert cur.fetchone()[0] in (2, "MEMORY", "memory")

        cur.execute("SELECT name FROM sqlite_master WHERE type='index';")
        indexes = {row[0] for row in cur.fetchall()}
        assert "idx_builds_hero" in indexes
        assert "idx_reviews_created" in indexes
        assert "idx_api_cache_key" in indexes
