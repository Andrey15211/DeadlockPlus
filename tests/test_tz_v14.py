"""TZ v1.4 D-трек: тесты на 429-парсинг, кэш match_raw, rank_hidden, SSE.

Сеть мокается (monkeypatch urllib.request.urlopen) — исходящих запросов
в тестах нет. Акцептанс D3: повторный разбор не делает исходящий запрос.
"""

from __future__ import annotations

import io
import json
import sys
import urllib.error
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from deadlockforge.engine import match_loader as ml
from deadlockforge.engine import steam_profile as sp
from deadlockforge.storage.db import Database

_HAS_FASTAPI = True
try:
    from fastapi.testclient import TestClient
    from deadlockforge.api.app import create_app
except ImportError:
    _HAS_FASTAPI = False


# --- парсер 429 ------------------------------------------------------------


def test_parse_429_wait_seconds():
    body = json.dumps({"next_request_in": 1840})
    assert ml._parse_429_wait(body) == 1840.0
    assert sp._parse_429_wait(body) == 1840.0


def test_parse_429_wait_missing_field():
    assert ml._parse_429_wait(json.dumps({"detail": "rate limited"})) is None
    assert ml._parse_429_wait("not json") is None


def test_rate_limit_error_message():
    exc = ml.RateLimitError(1840)
    assert "3 запроса/час" in str(exc)
    assert "31 мин" in str(exc)
    assert sp.RateLimitError(None).wait_seconds is None


def test_fetch_match_429_no_retry(monkeypatch):
    calls = {"n": 0}

    def fake_urlopen(req, timeout=None):
        calls["n"] += 1
        raise urllib.error.HTTPError(
            req.full_url, 429, "Too Many Requests", {},
            io.BytesIO(json.dumps({"next_request_in": 600}).encode()))

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(ml.RateLimitError):
        ml._fetch_match_http("123")
    assert calls["n"] == 1  # ретраев на 429 нет


# --- кэш match_raw ---------------------------------------------------------


@pytest.fixture()
def db(tmp_path):
    return Database(tmp_path / "test.db")


def test_match_raw_roundtrip(db):
    payload = {"match_info": {"players": []}, "match_id": "42"}
    db.set_match_raw("42", payload)
    assert db.get_match_raw("42") == payload
    db.set_match_raw("42", {"other": 1})  # ON CONFLICT DO NOTHING
    assert db.get_match_raw("42") == payload


def test_fetch_match_cached_no_http(db, monkeypatch):
    calls = {"n": 0}

    def fake_fetch(match_id):
        calls["n"] += 1
        return {"match_info": {"players": []}, "match_id": str(match_id)}

    monkeypatch.setattr(ml, "_fetch_match_http", fake_fetch)
    first = ml.fetch_match("42", db=db)
    second = ml.fetch_match("42", db=db)  # из кэша, HTTP нет
    assert calls["n"] == 1
    assert first == second


# --- rank_hidden -----------------------------------------------------------


def test_fetch_rank_hidden(monkeypatch):
    class FakeResp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None):
        return FakeResp(json.dumps(
            {"rank": 0, "subrank": 0, "badge": "",
             "last_match": None}).encode())

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    result = sp.fetch_rank(1)
    assert result["rank_hidden"] is True
    assert result["rank"] == 0


def test_fetch_rank_visible(monkeypatch):
    class FakeResp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None):
        return FakeResp(json.dumps(
            {"rank": 12, "subrank": 2, "badge": "Emissary II",
             "last_match": 12345}).encode())

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    result = sp.fetch_rank(1)
    assert result["rank_hidden"] is False
    assert result["badge"] == "Emissary II"


def test_fetch_steam_info_found(monkeypatch):
    class FakeResp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None):
        return FakeResp(json.dumps([{
            "account_id": 7, "personaname": "Tester",
            "avatarfull": "https://x/a.jpg"}]).encode())

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    info = sp.fetch_steam_info(7)
    assert info["found"] is True
    assert info["persona_name"] == "Tester"
    assert info["avatar"] == "https://x/a.jpg"


# --- SSE-эндпоинт ----------------------------------------------------------


@pytest.mark.skipif(not _HAS_FASTAPI, reason="fastapi not installed")
def test_sse_analyze_without_key_error_and_offline(tmp_path, monkeypatch):
    """Без ключа LLM: запрос по умолчанию возвращает ошибку; с mode='offline' отдаёт эвристику."""
    raw = {"match_info": {"players": [{
        "account_id": 7, "hero_id": 1, "team": 0, "kills": 1, "deaths": 2,
        "assists": 3, "level": 10, "net_worth": 20000,
        "stats": [{"time_stamp_s": 60, "net_worth": 1000}],
    }], "winning_team": 0, "duration_s": 1800}, "match_id": "999"}

    monkeypatch.setenv("DEADLOCKFORGE_AI_KEY", "")
    monkeypatch.setattr(
        "deadlockforge.storage.db.get_database",
        lambda path=None: Database(tmp_path / "db.sqlite"))
    monkeypatch.setattr(ml, "fetch_match", lambda mid, db=None: raw)
    monkeypatch.setattr(ml, "load_hero_map", lambda: {})

    client = TestClient(create_app())

    # 1. Запрос без ключа и без mode="offline" должен вернуть ошибку (запрет скрытой эвристики)
    with client.stream(
        "POST", "/api/match/analyze/stream",
        json={"match_id": "999", "account_id": 7},
    ) as resp:
        assert resp.status_code == 200
        events = [json.loads(line[5:])
                  for line in resp.iter_lines()
                  if line and line.startswith("data:")]
    assert any(e.get("type") == "error" and "API-ключ" in e.get("detail", "") for e in events)

    # 2. Запрос с явным mode="offline" должен выполнить эвристический разбор
    with client.stream(
        "POST", "/api/match/analyze/stream",
        json={"match_id": "999", "account_id": 7, "mode": "offline"},
    ) as resp:
        assert resp.status_code == 200
        events = [json.loads(line[5:])
                  for line in resp.iter_lines()
                  if line and line.startswith("data:")]
    types = [e["type"] for e in events]
    assert "stage" in types
    assert "result" in types
    assert "error" not in types
    result = next(e for e in events if e["type"] == "result")
    assert result["match_id"] == "999"
    assert result["kda"] == "1/2/3"


@pytest.mark.skipif(not _HAS_FASTAPI, reason="fastapi not installed")
def test_match_analyze_rate_limit_error(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "deadlockforge.storage.db.get_database",
        lambda path=None: Database(tmp_path / "db.sqlite"))

    def fake_fetch(mid, db=None):
        raise ml.RateLimitError(1840)

    monkeypatch.setattr(ml, "fetch_match", fake_fetch)

    client = TestClient(create_app())
    resp = client.post(
        "/api/match/analyze",
        json={"match_id": "999", "account_id": 7},
    )
    assert resp.status_code == 429
    assert "Лимит API (3 запроса/час)" in resp.json()["detail"]
    assert "31 мин" in resp.json()["detail"]


@pytest.mark.skipif(not _HAS_FASTAPI, reason="fastapi not installed")
def test_match_analyze_match_load_error(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "deadlockforge.storage.db.get_database",
        lambda path=None: Database(tmp_path / "db.sqlite"))

    def fake_fetch(mid, db=None):
        raise ml.MatchLoadError("network failure")

    monkeypatch.setattr(ml, "fetch_match", fake_fetch)

    client = TestClient(create_app())
    resp = client.post(
        "/api/match/analyze",
        json={"match_id": "999", "account_id": 7},
    )
    assert resp.status_code == 502
    assert "network failure" in resp.json()["detail"]

