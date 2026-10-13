"""Automated Performance & Verification Benchmark for DeadlockForge v1.8 (Stage 4)."""

import os
import sys
import time
import sqlite3
from pathlib import Path
from starlette.testclient import TestClient

from deadlockforge.api.app import create_app
from deadlockforge.storage.db import get_database

def run_benchmarks():
    print("=== DEADLOCKFORGE v1.8 PERFORMANCE & QA VERIFICATION ===")
    
    # 1. Cold start measurement
    t0 = time.perf_counter()
    app = create_app()
    app_boot_time_ms = (time.perf_counter() - t0) * 1000
    print(f"[1] Cold Server & App Boot Time: {app_boot_time_ms:.2f} ms")

    client = TestClient(app)

    # 2. Cold Endpoint Latency
    endpoints = ["/api/heroes", "/api/items", "/api/map"]
    cold_latencies = {}
    etags = {}
    
    print("\n[2] Cold Endpoint Requests:")
    for ep in endpoints:
        t_start = time.perf_counter()
        resp = client.get(ep)
        lat_ms = (time.perf_counter() - t_start) * 1000
        cold_latencies[ep] = lat_ms
        etag = resp.headers.get("etag")
        etags[ep] = etag
        print(f"  {ep:<15} -> Status: {resp.status_code}, ETag: {etag}, Latency: {lat_ms:.2f} ms, Size: {len(resp.content)} bytes")
        assert resp.status_code == 200, f"Expected 200 for {ep}, got {resp.status_code}"
        assert etag is not None, f"Expected ETag for {ep}"

    # 3. Hot Endpoint Latency (repeated requests without ETag)
    print("\n[3] Hot Endpoint Requests (without ETag, pre-serialized JSON):")
    hot_latencies = {}
    iterations = 50
    for ep in endpoints:
        times = []
        for _ in range(iterations):
            t_start = time.perf_counter()
            resp = client.get(ep)
            times.append((time.perf_counter() - t_start) * 1000)
            assert resp.status_code == 200
        avg_ms = sum(times) / len(times)
        min_ms = min(times)
        p95_ms = sorted(times)[int(len(times) * 0.95)]
        hot_latencies[ep] = {"avg": avg_ms, "min": min_ms, "p95": p95_ms}
        print(f"  {ep:<15} -> Avg: {avg_ms:.2f} ms | Min: {min_ms:.2f} ms | p95: {p95_ms:.2f} ms (over {iterations} reqs)")

    # 4. ETag & 304 Not Modified Verification
    print("\n[4] ETag 304 Not Modified Verification:")
    etag_latencies = {}
    for ep in endpoints:
        etag = etags[ep]
        times = []
        for _ in range(iterations):
            t_start = time.perf_counter()
            resp = client.get(ep, headers={"If-None-Match": etag})
            times.append((time.perf_counter() - t_start) * 1000)
            assert resp.status_code == 304, f"Expected 304 for {ep}, got {resp.status_code}"
            assert resp.content == b"", f"Expected empty body on 304, got {len(resp.content)} bytes"
            assert resp.headers.get("etag") == etag, f"ETag mismatch in 304 header"
        avg_ms = sum(times) / len(times)
        min_ms = min(times)
        p95_ms = sorted(times)[int(len(times) * 0.95)]
        etag_latencies[ep] = {"avg": avg_ms, "min": min_ms, "p95": p95_ms}
        print(f"  {ep:<15} + If-None-Match -> Status 304 OK | Avg: {avg_ms:.2f} ms | Min: {min_ms:.2f} ms | Body: 0 B")

    # 5. Static Files & Cache-Control Headers Verification
    print("\n[5] Static Files & Caching Headers:")
    # Index HTML
    resp_index = client.get("/")
    assert resp_index.status_code == 200
    index_cc = resp_index.headers.get("cache-control", "")
    print(f"  GET /               -> Status: {resp_index.status_code}, Cache-Control: '{index_cc}'")
    assert "no-cache" in index_cc and "no-store" in index_cc, f"Expected no-cache for index.html, got {index_cc}"

    # JS static files with ETag
    js_files = ["app.js", "ui.js", "calc.js", "deep_match.js", "audio.js", "api.js"]
    for jf in js_files:
        resp_js = client.get(f"/js/{jf}")
        assert resp_js.status_code == 200
        js_etag = resp_js.headers.get("etag")
        assert js_etag is not None, f"Expected ETag for /js/{jf}"
        assert resp_js.headers.get("content-type", "").startswith("application/javascript")
        
        # Test 304 Not Modified
        resp_304 = client.get(f"/js/{jf}", headers={"If-None-Match": js_etag})
        assert resp_304.status_code == 304, f"Expected 304 for /js/{jf}, got {resp_304.status_code}"
        assert resp_304.content == b""
        print(f"  GET /js/{jf:<12} -> 200 (ETag: {js_etag[:16]}...) | 304 Not Modified verified")

    # CSS static files with ETag
    css_files = ["hud.css", "styles.css", "animations.css"]
    for cf in css_files:
        resp_css = client.get(f"/css/{cf}")
        assert resp_css.status_code == 200
        css_etag = resp_css.headers.get("etag")
        assert css_etag is not None, f"Expected ETag for /css/{cf}"
        assert resp_css.headers.get("content-type", "").startswith("text/css")

        resp_304 = client.get(f"/css/{cf}", headers={"If-None-Match": css_etag})
        assert resp_304.status_code == 304, f"Expected 304 for /css/{cf}, got {resp_304.status_code}"
        assert resp_304.content == b""
        print(f"  GET /css/{cf:<11} -> 200 (ETag: {css_etag[:16]}...) | 304 Not Modified verified")

    # Assets static files with long-lived Cache-Control
    asset_paths = [
        "heroes/abrams.webp",
        "sounds/buy_sound.mp3",
        "sounds/background_music.mp3",
        "video/background_loop.mp4",
        "icons/favicon.ico"
    ]
    for ap in asset_paths:
        target = Path("web/assets") / ap
        if target.is_file():
            resp_asset = client.get(f"/assets/{ap}")
            assert resp_asset.status_code == 200
            cc = resp_asset.headers.get("cache-control", "")
            print(f"  GET /assets/{ap:<28} -> Status 200, Cache-Control: '{cc}'")
            assert "public" in cc and "max-age=86400" in cc and "stale-while-revalidate=3600" in cc
        else:
            print(f"  [Notice] Asset web/assets/{ap} not found on disk, skipping check")

    # Favicon.ico endpoint
    resp_fav = client.get("/favicon.ico")
    assert resp_fav.status_code == 200
    fav_cc = resp_fav.headers.get("cache-control", "")
    print(f"  GET /favicon.ico    -> Status: {resp_fav.status_code}, Cache-Control: '{fav_cc}'")

    # 6. SQLite Database PRAGMA & Indexes Verification
    print("\n[6] SQLite Database PRAGMA & Indexes Verification:")
    db = get_database()
    with db._lock:
        cursor = db._conn.cursor()
        
        # Check journal_mode
        cursor.execute("PRAGMA journal_mode;")
        journal_mode = cursor.fetchone()[0]
        print(f"  PRAGMA journal_mode: {journal_mode}")
        assert journal_mode.lower() == "wal"

        # Check synchronous
        cursor.execute("PRAGMA synchronous;")
        sync = cursor.fetchone()[0]
        # SQLite returns synchronous as integer: 0=OFF, 1=NORMAL, 2=FULL, 3=EXTRA
        sync_str = {0: "OFF", 1: "NORMAL", 2: "FULL", 3: "EXTRA"}.get(sync, str(sync))
        print(f"  PRAGMA synchronous: {sync_str} ({sync})")
        assert sync in (1, "NORMAL", "normal"), f"Expected NORMAL (1), got {sync}"

        # Check temp_store
        cursor.execute("PRAGMA temp_store;")
        temp_store = cursor.fetchone()[0]
        # 0=DEFAULT, 1=FILE, 2=MEMORY
        ts_str = {0: "DEFAULT", 1: "FILE", 2: "MEMORY"}.get(temp_store, str(temp_store))
        print(f"  PRAGMA temp_store: {ts_str} ({temp_store})")
        assert temp_store in (2, "MEMORY", "memory"), f"Expected MEMORY (2), got {temp_store}"

        # Check indexes
        cursor.execute("SELECT name, tbl_name FROM sqlite_master WHERE type='index';")
        indexes = {row[0]: row[1] for row in cursor.fetchall()}
        print(f"  Found indexes: {list(indexes.keys())}")
        assert "idx_builds_hero" in indexes, "idx_builds_hero missing"
        assert "idx_reviews_created" in indexes, "idx_reviews_created missing"
        assert "idx_api_cache_key" in indexes, "idx_api_cache_key missing"
        print("  All required performance indexes verified!")

    print("\n=== ALL PERFORMANCE & VERIFICATION CHECKS PASSED SUCCESSFULLY ===")
    return {
        "boot_ms": app_boot_time_ms,
        "cold": cold_latencies,
        "hot": hot_latencies,
        "etag": etag_latencies,
    }

if __name__ == "__main__":
    run_benchmarks()
