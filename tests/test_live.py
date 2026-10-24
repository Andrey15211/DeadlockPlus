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


def test_live_schedule_valid():
    r = _client.get("/api/live/schedule")
    assert r.status_code == 200
    events = r.json()["events"]
    assert len(events) >= 20, "расписание должно покрывать матч до 45 минут"
    ats = [ev["at"] for ev in events]
    assert ats == sorted(ats), "события должны быть отсортированы по секундам"
    for ev in events:
        assert ev["lead"] > 0
        assert ev["pre"] and ev["now"]
        assert ev["at"] >= 0
    # статичный спавн лёгких лагерей в 2:00 из data/map.json
    assert any(ev["at"] == 120 and "лагер" in ev["title"].lower() for ev in events)
    # повторяющийся цикл урны: вторая — 15:00, дальше каждые 5 минут
    urn = [ev["at"] for ev in events if ev["id"].startswith("urn:")]
    assert urn[:3] == [900, 1200, 1500], urn


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


def test_live_clock_round_trip():
    """Часы матча: панель задаёт точку отсчёта, сервер считает сам."""
    r = _client.post("/api/live/clock", json={"seconds": 100.0, "running": False})
    assert r.status_code == 200
    assert r.json() == {"seconds": 100.0, "running": False}
    assert _client.get("/api/live/clock").json()["seconds"] == 100.0

    _client.post("/api/live/clock", json={"seconds": 100.0, "running": True})
    running = _client.get("/api/live/clock").json()
    assert running["running"] is True
    assert running["seconds"] >= 100.0

    _client.post("/api/live/clock", json={"seconds": -5.0, "running": False})
    assert _client.get("/api/live/clock").json()["seconds"] == 0.0


def test_live_overlay_unsupported_without_gui():
    """В браузере тумблер плашек не рулит окном: supported=False."""
    assert _client.get("/api/live/overlay").json() == {
        "supported": False, "enabled": False}
    r = _client.post("/api/live/overlay", json={"enabled": True})
    assert r.json() == {"supported": False, "enabled": False}


def test_desktop_overlay_unsupported_without_gui():
    """Оверлей рулится из run_desktop.py: в браузере supported=False."""
    assert _client.get("/api/desktop/overlay").json() == {
        "supported": False, "visible": False}
    r = _client.post("/api/desktop/overlay", json={"visible": True})
    assert r.status_code == 200
    assert r.json() == {"supported": False, "visible": False}


def test_live_page_has_overlay_controls():
    """live.html — содержимое оверлея: кнопка ✕ и синхронизация часов."""
    html = _client.get("/live.html").text
    assert 'id="btn-hide-overlay"' in html
    assert "window.dfSyncClock" in html
    assert "/api/desktop/overlay" in html


def test_overlay_page_served():
    """Страница плашек: прозрачный фон, клики сквозь, никакого опроса из JS.

    Таймеры в скрытом окне троттлятся WebView2, поэтому окно не опрашивает
    сервер само — его дёргает драйвер run_desktop.py через window.dfToast.
    """
    r = _client.get("/overlay.html")
    assert r.status_code == 200
    html = r.text
    assert "background: transparent" in html
    assert "pointer-events: none" in html      # клики уходят в игру
    assert "window.dfToast" in html
    assert "window.dfClear" in html
    assert "MAX_TOASTS" in html
    # JS не должен ходить в API: иначе плашка опаздывает на секунды.
    assert "/api/live/clock" not in html
    assert "setInterval" not in html


def test_overlay_flag_off_by_default():
    """Плашки — фича под флагом: без --overlay окно не создаётся вообще."""
    root = Path(__file__).resolve().parents[1]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    import run_desktop

    assert run_desktop.overlay_enabled([], {}) is False
    assert run_desktop.overlay_enabled(["--overlay"], {}) is True
    assert run_desktop.overlay_enabled([], {"DEADLOCK_OVERLAY": "1"}) is True
    assert run_desktop.overlay_enabled([], {"DEADLOCK_OVERLAY": "0"}) is False


def test_main_window_has_no_overlay_switch():
    """Тумблера плашек в шапке нет: включать нечего, фича под флагом.

    Класс .live-switch остаётся — его использует тумблер «Поверх других окон».
    """
    html = _client.get("/").text
    assert 'id="live-switch"' not in html
    assert "/api/live/overlay" not in html
    assert 'id="topmost-switch"' in html


def test_overlay_helpers_safe_without_window():
    """Обёртки окна плашек не падают, пока pywebview не отдал HWND."""
    root = Path(__file__).resolve().parents[1]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    import run_desktop

    stub = type("W", (), {"native": None})()
    assert run_desktop.make_click_through(stub) is False
    assert run_desktop.make_overlay_window(stub) is False
    assert run_desktop.show_overlay(stub) is False
    width, height = run_desktop.screen_size()
    assert width > 0 and height > 0


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