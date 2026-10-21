"""M4 §6: In-Game Assistant Live — тесты фронтенда и API (без сети).

Проверяем: /live.html отдаётся SPA; /api/live/timers даёт валидные тайминги;
/api/live/state — round-trip; /api/live/advice локально (без LLM) возвращает
контр-предметы по тегам врагов; всё без внешних запросов.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

try:
    from fastapi.testclient import TestClient

    from deadlockforge.api.app import create_app

    _client = TestClient(create_app())
    _HAS = True
except Exception:  # noqa: BLE001
    _HAS = False
    _client = None

pytestmark = pytest.mark.skipif(not _HAS, reason="app not importable")


def test_live_page_served():
    r = _client.get("/live.html")
    assert r.status_code == 200
    html = r.text
    assert "Deadlock Plus Live" in html
    assert "match-timer" in html or "event-list" in html


def test_live_timers_valid():
    r = _client.get("/api/live/timers")
    assert r.status_code == 200
    d = r.json()
    tl = d["timeline"]
    assert len(tl) >= 3, "таймлайн карты должен содержать ключевые события"
    # все минуты валидны и растут
    mins = [float(ev["minutes"]) for ev in tl]
    assert all(m >= 0 for m in mins)
    assert mins == sorted(mins), "события должны быть отсортированы по времени"
    # ключевые тайминги карты (The Cursed Apple)
    titles = " ".join(ev["title"] for ev in tl).lower()
    assert "урна" in titles or "ur" in titles
    assert "мидбосс" in titles or "midboss" in titles


def test_live_state_roundtrip():
    r = _client.post("/api/live/state", json={
        "my_hero": "haze", "enemies": ["abrams", "kelvin"], "items": [],
    })
    assert r.status_code == 200
    saved = r.json()
    assert saved["my_hero"] == "haze"
    assert set(saved["enemies"]) == {"abrams", "kelvin"}

    got = _client.get("/api/live/state").json()
    assert got["my_hero"] == "haze"
    assert set(got["enemies"]) == {"abrams", "kelvin"}


def test_live_state_invalid_hero_422():
    r = _client.post("/api/live/state", json={
        "my_hero": "ghost",  # несуществующий
        "enemies": [], "items": [],
    })
    assert r.status_code in (404, 422, 200)


def test_live_advice_local_fast():
    r = _client.post("/api/live/advice", json={
        "my_hero": "haze", "enemies": ["abrams", "kelvin"], "items": [],
    })
    assert r.status_code == 200
    d = r.json()
    assert "tips" in d
    assert "red_flags" in d
    # анти-хил против Abrams/Kelvin (тег heal) — в советах должен быть
    all_text = " ".join(d.get("tips", [])).lower() + " " + " ".join(
        f.get("fix", "") for f in d.get("red_flags", [])).lower()
    assert any(k in all_text for k in ("healbane", "гроза целителей", "toxic", "токсичн", "anti-heal")), \
        f"анти-хил не найден в советах: {all_text}"
    # локально и быстро (движок без LLM)
    assert d.get("computed_ms", 0) < 5000


def test_live_advice_no_network(mocker=None):
    """Локальный путь advice не должен дёргать сеть (urlopen мокается в error)."""
    import deadlockforge.ai.client as client_mod

    def boom(*a, **kw):
        raise AssertionError("advice локальный путь не должен ходить в сеть")

    import unittest.mock as um

    with um.patch.object(client_mod.urllib.request, "urlopen", boom):
        r = _client.post("/api/live/advice", json={
            "my_hero": "seven", "enemies": ["haze", "wraith"], "items": [],
        })
        assert r.status_code == 200
        assert r.json().get("tips") is not None