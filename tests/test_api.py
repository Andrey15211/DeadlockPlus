"""Интеграция REST API DeadlockForge через FastAPI TestClient.

Пропускаются, пока движок и датасет ещё не готовы (try-import через
sys.path на src, как в pyproject pythonpath).
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
    from deadlockforge.engine import dataset as dataset_mod
    from deadlockforge.engine import advisor as advisor_mod  # noqa: F401
    from deadlockforge.engine import calculator as calculator_mod  # noqa: F401
    from deadlockforge.engine import builds as builds_mod  # noqa: F401
    from deadlockforge.engine import build_codec as build_codec_mod  # noqa: F401
    from deadlockforge.models import BuildSlots

    _client = TestClient(create_app())
    _HAS_ENGINE = True
except ImportError:
    _HAS_ENGINE = False
    _client = None

pytestmark = pytest.mark.skipif(
    not _HAS_ENGINE, reason="engine modules not importable yet"
)


def test_heroes_endpoint():
    resp = _client.get("/api/heroes")
    assert resp.status_code == 200
    heroes = resp.json()
    assert len(heroes) >= 20
    ids = [h["id"] for h in heroes]
    assert len(ids) == len(set(ids))


def test_items_endpoint():
    resp = _client.get("/api/items")
    assert resp.status_code == 200
    items = resp.json()
    assert len(items) >= 48
    categories = {i["category"] for i in items}
    assert {"weapon", "vitality", "spirit"} <= categories


def test_advice_heal_comp():
    resp = _client.get(
        "/api/advice", params={"my_hero": "abrams", "enemies": "kelvin,infernus,wraith"}
    )
    assert resp.status_code == 200
    advice = resp.json()
    assert advice["threats"], "threats must be non-empty"
    assert advice["recommendations"], "recommendations must be non-empty"


def test_calc_snapshot():
    items = _client.get("/api/items").json()
    a_few = [i["id"] for i in items[:3]]
    heroes = _client.get("/api/heroes").json()
    resp = _client.post(
        "/api/calc", json={"hero_id": heroes[0]["id"], "item_ids": a_few}
    )
    assert resp.status_code == 200
    snap = resp.json()
    for key in (
        "hp", "bullet_shield", "spirit_shield", "weapon_damage", "fire_rate",
        "weapon_dps", "spirit_power", "stamina", "move_speed",
        "cooldown_reduction_pct", "souls_spent",
    ):
        assert key in snap, f"StatsSnapshot missing field {key}"


def test_calc_unknown_ids():
    resp = _client.post("/api/calc", json={"hero_id": "nope", "item_ids": []})
    assert resp.status_code == 404
    heroes = _client.get("/api/heroes").json()
    resp = _client.post(
        "/api/calc", json={"hero_id": heroes[0]["id"], "item_ids": ["ghost_item"]}
    )
    assert resp.status_code == 404


def test_advice_unknown_and_duplicate():
    resp = _client.get("/api/advice", params={"my_hero": "ghost", "enemies": "abrams"})
    assert resp.status_code == 404
    resp = _client.get(
        "/api/advice", params={"my_hero": "abrams", "enemies": "abrams,kelvin"}
    )
    assert resp.status_code == 422
    resp = _client.get(
        "/api/advice", params={"my_hero": "abrams", "enemies": "kelvin,kelvin"}
    )
    assert resp.status_code == 422


def test_map_three_lanes():
    resp = _client.get("/api/map")
    assert resp.status_code == 200
    assert len(resp.json()["lanes"]) == 3


def test_map_markers_come_from_api_source():
    """Маркеры карты собраны из deadlock-api, а не вписаны вручную.

    Регрессия: 4 из 14 прежних ручных маркеров лежали вне игровой зоны,
    а слои camps/urn/towers не покрывались данными.
    """
    guide = _client.get("/api/map").json()
    markers = guide["markers"]
    assert len(markers) > 20, "маркеры должны покрывать лагеря и объективы карты"
    layers = {m["layer"] for m in markers}
    assert {"camps", "towers", "urn"} <= layers
    assert "walkers" not in layers, "слой walkers заменён на towers"
    for m in markers:
        assert 0.0 <= m["x"] <= 1.0
        assert 0.0 <= m["y"] <= 1.0
    source = guide.get("markers_source") or {}
    assert "assets/map" in str(source.get("endpoint", ""))


def test_assets_route_rejects_path_traversal():
    """Обход каталога через /assets/ закрыт (403/404, но не выдача файла)."""
    for path in (
        "/assets/..%2f..%2fpyproject.toml",
        "/assets/%2e%2e/%2e%2e/pyproject.toml",
        "/assets/../data/heroes.json",
    ):
        resp = _client.get(path)
        assert resp.status_code in (403, 404), f"{path} -> {resp.status_code}"
        assert b"deadlockforge" not in resp.content[:200]


def test_catalog_endpoints_require_revalidation():
    """Каталоги отдают ETag и запрещают эвристический кэш."""
    for endpoint in ("/api/heroes", "/api/items", "/api/map"):
        resp = _client.get(endpoint)
        assert resp.status_code == 200
        etag = resp.headers.get("etag")
        assert etag, f"{endpoint} без ETag"
        cache = resp.headers.get("cache-control", "")
        assert "no-cache" in cache or "must-revalidate" in cache, (
            f"{endpoint}: Cache-Control={cache!r} допускает устаревание"
        )
        again = _client.get(endpoint, headers={"If-None-Match": etag})
        assert again.status_code == 304


def test_build_validate():
    resp = _client.post(
        "/api/build/validate",
        json={"weapon": [None]*12, "vitality": [None]*12, "spirit": [None]*12},
    )
    assert resp.status_code == 200
    assert resp.json() == {"valid": True}

    # category violation: weapon item placed into a vitality slot
    items = _client.get("/api/items").json()
    weapon_id = next(i["id"] for i in items if i["category"] == "weapon")
    resp = _client.post(
        "/api/build/validate",
        json={
            "weapon": [None]*12, "vitality": [weapon_id] + [None]*11,
            "spirit": [None]*12,
        },
    )
    assert resp.status_code == 422
    assert "detail" in resp.json()



def test_index_served():
    resp = _client.get("/")
    assert resp.status_code == 200
    assert b"DeadlockForge" in resp.content or "text/html" in resp.headers["content-type"]


def test_dataset_loaded():
    """Smoke: cached catalog exposes the frozen engine surface."""
    catalog = dataset_mod.get_catalog()
    assert catalog.heroes
    assert catalog.items
    assert catalog.map_guide.lanes
    assert callable(advisor_mod.recommend)
    assert callable(calculator_mod.compute)
    assert callable(builds_mod.validate)


# --- Фаза 3: зеркальная дуэль (allow_mirror) ------------------------------


def test_advice_mirror_rejected_without_allow_mirror():
    """Контракт: allow_mirror=False (по умолчанию) → 422 при my_hero в enemies."""
    resp = _client.get(
        "/api/advice", params={"my_hero": "abrams", "enemies": "abrams"}
    )
    assert resp.status_code == 422


def test_advice_mirror_allowed_with_flag():
    """Контракт: allow_mirror=True → 200 на зеркале (abrams vs abrams),
    угрозы непустые."""
    resp = _client.get(
        "/api/advice",
        params={"my_hero": "abrams", "enemies": "abrams", "allow_mirror": "true"},
    )
    assert resp.status_code == 200
    advice = resp.json()
    assert advice["threats"], "threats must be non-empty in mirror mode"


def test_advice_enemy_duplicates_still_rejected_with_allow_mirror():
    """Контракт: дубликаты врагов между собой → 422 всегда,
    даже при allow_mirror=True (зеркало ≠ повтор в списке)."""
    resp = _client.get(
        "/api/advice",
        params={
            "my_hero": "abrams",
            "enemies": "kelvin,kelvin",
            "allow_mirror": "true",
        },
    )
    assert resp.status_code == 422


# --- Фаза 3: export / import билдов ----------------------------------------


def _empty_slots() -> dict:
    return {
        "weapon": [None] * 12, "vitality": [None] * 12,
        "spirit": [None] * 12,
    }


def _sample_slots() -> dict:
    """Валидный билд: по одному предмету каждой категории в своём слоте
    + один ДРУГОЙ spirit-предмет (дубликаты запрещены правилами)."""
    items = _client.get("/api/items").json()
    weapon_id = next(i["id"] for i in items if i["category"] == "weapon")
    vitality_id = next(i["id"] for i in items if i["category"] == "vitality")
    spirit_ids = [i["id"] for i in items if i["category"] == "spirit"]
    return {
        "weapon": [weapon_id] + [None] * 11,
        "vitality": [vitality_id] + [None] * 11,
        "spirit": [spirit_ids[0], spirit_ids[1]] + [None] * 10,
    }


def test_export_empty_build_returns_code():
    resp = _client.post("/api/builds/export", json=_empty_slots())
    assert resp.status_code == 200
    code = resp.json()["code"]
    assert isinstance(code, str)
    assert code, "code must be non-empty"


def test_export_invalid_category_violation():
    """weapon-предмет в vitality-слоте → 422."""
    items = _client.get("/api/items").json()
    weapon_id = next(i["id"] for i in items if i["category"] == "weapon")
    resp = _client.post(
        "/api/builds/export",
        json={
            "weapon": [None] * 12, "vitality": [weapon_id] + [None] * 11,
            "spirit": [None] * 12,
        },
    )
    assert resp.status_code == 422


def test_export_duplicate_item():
    """Дубликат предмета между слотами → 422."""
    items = _client.get("/api/items").json()
    weapon_id = next(i["id"] for i in items if i["category"] == "weapon")
    resp = _client.post(
        "/api/builds/export",
        json={
            # один и тот же weapon-предмет в weapon[0] и weapon[1]
            "weapon": [weapon_id, weapon_id] + [None] * 10,
            "vitality": [None] * 12,
            "spirit": [None] * 12,
        },
    )
    assert resp.status_code == 422


def test_import_round_trip():
    """export → import возвращает исходные слоты (поэлементно)."""
    slots = _sample_slots()
    exported = _client.post("/api/builds/export", json=slots)
    assert exported.status_code == 200
    code = exported.json()["code"]

    imported = _client.post("/api/builds/import", json={"code": code})
    assert imported.status_code == 200
    restored = imported.json()
    for group in ("weapon", "vitality", "spirit"):
        assert restored[group] == slots[group], f"group {group} mismatch after round-trip"


@pytest.mark.parametrize(
    "bad_code",
    [
        "not-a-code!!!",
        "",
        "!!!не-base64!!!",
        "####",
        "aGVsbG8gd29ybGQ=",  # валидный base64 "hello world", но не JSON-схема билда
    ],
)
def test_import_garbage_codes_rejected(bad_code):
    """Битый base64 / JSON / схема → 422."""
    resp = _client.post("/api/builds/import", json={"code": bad_code})
    assert resp.status_code == 422


def test_import_valid_codec_unknown_item_id():
    """Валидный код с неизвестным item id → 422.

    Код генерируется самим кодеком от BuildSlots с "ghost-item",
    чтобы гарантировать структурно корректный payload.
    """
    ghost_slots = BuildSlots(
        weapon=["ghost-item"] + [None] * 11,
        vitality=[None] * 12,
        spirit=[None] * 12,
    )
    code = build_codec_mod.encode(ghost_slots)
    assert code, "codec must produce a non-empty code"
    resp = _client.post("/api/builds/import", json={"code": code})
    assert resp.status_code == 422


def test_ai_config_multi_provider_roundtrip():
    """Тест поддержки мультивендорности в /api/ai/config (OpenAI, Anthropic, DeepSeek)."""
    # 1. Смена на OpenAI: дефолтная модель glm-5.3-flash (V2.0 М4, роутер)
    r = _client.post("/api/ai/config", json={"provider": "openai", "api_key": "sk-test12345678"})
    assert r.status_code == 200
    cfg = _client.get("/api/ai/config").json()
    assert cfg["provider"] == "openai"
    assert cfg["model_name"] == "glm-5.3-flash"
    assert cfg["has_key"] is True

    # 2. Смена на Anthropic: дефолтная модель claude-3-7-sonnet-latest
    r = _client.post("/api/ai/config", json={"provider": "anthropic"})
    assert r.status_code == 200
    cfg = _client.get("/api/ai/config").json()
    assert cfg["provider"] == "anthropic"
    assert cfg["model_name"] == "claude-3-7-sonnet-latest"

    # Возвращаем дефолт владельца (glm-5.3-flash, роутер) — тест не должен
    # оставлять прод-БД на легаси-конфиге (V2.0 М4).
    _client.post("/api/ai/config", json={
        "provider": "openai", "model_name": "glm-5.3-flash",
        "base_url": "http://127.0.0.1:8089/v1",
        "reasoning_effort": "low",
    })


def test_ai_config_reasoning_effort_roundtrip():
    """Тест сохранения и чтения reasoning_effort (off/low/medium/high)."""
    r = _client.post("/api/ai/config", json={"reasoning_effort": "off"})
    assert r.status_code == 200
    cfg = _client.get("/api/ai/config").json()
    assert cfg["reasoning_effort"] == "off"

    r = _client.post("/api/ai/config", json={"reasoning_effort": "low"})
    assert r.status_code == 200
    cfg = _client.get("/api/ai/config").json()
    assert cfg["reasoning_effort"] == "low"


def test_match_analyze_strict_key_requirement_and_offline(monkeypatch):
    """Строгий запрет имитации (ТЗ v1.5 Трек 4): без ключа 422; с mode='offline' 200."""
    from deadlockforge.engine import match_loader as ml
    from deadlockforge.storage.db import get_database

    raw = {"match_info": {"players": [{
        "account_id": 7, "hero_id": 1, "team": 0, "kills": 2, "deaths": 1,
        "assists": 4, "level": 12, "net_worth": 25000,
        "stats": [{"time_stamp_s": 60, "net_worth": 1200}],
    }], "winning_team": 0, "duration_s": 1800}, "match_id": "888"}

    monkeypatch.setenv("DEADLOCKFORGE_AI_KEY", "")
    monkeypatch.setattr(ml, "fetch_match", lambda mid, db=None: raw)
    monkeypatch.setattr(ml, "load_hero_map", lambda: {})

    # Очищаем ключ в БД
    _client.post("/api/ai/config", json={"api_key": ""})

    # Запрос без ключа в режиме по умолчанию -> 422
    resp = _client.post("/api/match/analyze", json={"match_id": "888", "account_id": 7})
    assert resp.status_code == 422
    assert "Для анализа матча нейросетью укажите API-ключ в профиле" in resp.json()["detail"]

    # Запрос с явным mode='offline' -> 200 (эвристика)
    resp_off = _client.post("/api/match/analyze", json={"match_id": "888", "account_id": 7, "mode": "offline"})
    assert resp_off.status_code == 200
    assert resp_off.json()["match_id"] == "888"


def test_get_match_roster_and_hero_slug_pov(monkeypatch):
    """Проверка GET /api/match/{id}/roster и выбора точки зрения через hero_slug."""
    from deadlockforge.engine import match_loader as ml

    raw = {
        "match_info": {
            "players": [
                {
                    "account_id": 101, "hero_id": 1, "team": 0, "kills": 5, "deaths": 2,
                    "assists": 8, "level": 14, "net_worth": 18000,
                    "stats": [{"time_stamp_s": 600, "net_worth": 4500}],
                },
                {
                    "account_id": 102, "hero_id": 2, "team": 0, "kills": 12, "deaths": 1,
                    "assists": 4, "level": 16, "net_worth": 24000,
                    "stats": [{"time_stamp_s": 600, "net_worth": 6200}],
                },
            ],
            "winning_team": 0,
            "duration_s": 1500,
        },
        "match_id": "999",
    }

    monkeypatch.setattr(ml, "fetch_match", lambda mid, db=None: raw)
    monkeypatch.setattr(ml, "load_hero_map", lambda: {1: "infernus", 2: "seven"})

    # 1. GET /api/match/999/roster
    roster_resp = _client.get("/api/match/999/roster")
    assert roster_resp.status_code == 200
    roster = roster_resp.json()
    assert roster["match_id"] == "999"
    assert len(roster["players"]) == 2
    assert roster["players"][0]["hero_slug"] == "infernus"
    assert roster["players"][1]["hero_slug"] == "seven"

    # 2. Анализ с указанием hero_slug="seven" без знания account_id
    # Без hero_slug defaulted бы к players[0] ("infernus").
    # С hero_slug="seven" должен построить отчёт от лица Seven!
    analyze_resp = _client.post("/api/match/analyze", json={
        "match_id": "999",
        "hero_slug": "seven",
        "mode": "offline",
    })
    assert analyze_resp.status_code == 200
    review = analyze_resp.json()
    assert review["hero_id"] == "seven"
    assert review["kda"] == "12/1/4"


def test_recent_matches_force_and_refresh(monkeypatch):
    """Тест обновления списка последних матчей: force=True и POST /api/profile/recent-matches/refresh."""
    import time
    from deadlockforge.engine import steam_profile as sp

    orig_acc = _client.get("/api/profile").json().get("account_id")
    unique_acc = str(int(time.time() * 1000) % 1_000_000_000)
    try:
        _client.post("/api/profile", json={"account_id": unique_acc})

        calls = {"count": 0}
        sample_matches = [
            {
                "match_id": 105179800,
                "hero_id": 1,
                "start_time": 1791655351,
                "player_kills": 10,
                "player_deaths": 2,
                "player_assists": 8,
                "player_match_outcome": 1,
            }
        ]

        def fake_fetch_recent_matches(acc_id):
            calls["count"] += 1
            return sample_matches

        monkeypatch.setattr(sp, "fetch_recent_matches", fake_fetch_recent_matches)

        # 1. Первый вызов (без force) — запрашивает сеть и кэширует
        r1 = _client.get("/api/profile/recent-matches")
        assert r1.status_code == 200
        assert r1.json()["cached"] is False
        assert calls["count"] == 1

        # 2. Второй вызов без force — возвращает из кэша
        r2 = _client.get("/api/profile/recent-matches")
        assert r2.status_code == 200
        assert r2.json()["cached"] is True
        assert calls["count"] == 1

        # 3. Вызов с force=true — обходит кэш и делает запрос
        r3 = _client.get("/api/profile/recent-matches?force=true")
        assert r3.status_code == 200
        assert r3.json()["cached"] is False
        assert calls["count"] == 2

        # 4. POST /api/profile/recent-matches/refresh — также обходит кэш
        r4 = _client.post("/api/profile/recent-matches/refresh")
        assert r4.status_code == 200
        assert r4.json()["cached"] is False
        assert calls["count"] == 3
    finally:
        _client.post("/api/profile", json={"account_id": orig_acc or "156067414"})


def test_buy_sound_asset_served():
    """TZ v1.7 Задача 2.1: Проверка отдачи звука покупки с MIME audio/mpeg."""
    resp = _client.get("/assets/sounds/buy_sound.mp3")
    assert resp.status_code == 200
    assert resp.headers.get("content-type") == "audio/mpeg"
    assert len(resp.content) == 32985



def test_background_music_asset_served():
    """Проверка отдачи фоновой музыки background_music.opus с MIME audio/ogg."""
    resp = _client.get("/assets/sounds/background_music.opus")
    assert resp.status_code == 200
    assert resp.headers.get("content-type") == "audio/ogg"
    assert len(resp.content) == 14051526



def test_map_colored_asset_served():
    """TZ v1.7 Задача 3.1: Проверка отдачи ассета цветной карты с MIME image/png."""
    resp = _client.get("/assets/map/Map_colored.png")
    assert resp.status_code == 200
    assert resp.headers.get("content-type") == "image/png"
    assert len(resp.content) == 179400


def test_background_video_asset_served():
    """Проверка отдачи фонового видео background_loop.mp4 с MIME video/mp4."""
    resp = _client.get("/assets/video/background_loop.mp4")
    assert resp.status_code == 200
    assert resp.headers.get("content-type") == "video/mp4"
    assert len(resp.content) == 1951704


def test_http_caching_and_etag():
    """TZ v1.8 Этап 1: ETag, 304 Not Modified и Cache-Control для ассетов."""
    # 1. Assets Cache-Control
    resp_asset = _client.get("/assets/map/Map_colored.png")
    assert resp_asset.status_code == 200
    assert resp_asset.headers.get("cache-control") == "public, max-age=86400, stale-while-revalidate=3600"

    # 2. JS / CSS ETag + 304 Not Modified
    resp_js = _client.get("/js/app.js")
    assert resp_js.status_code == 200
    etag = resp_js.headers.get("etag")
    assert etag is not None

    resp_304 = _client.get("/js/app.js", headers={"If-None-Match": etag})
    assert resp_304.status_code == 304

    resp_css = _client.get("/css/styles.css")
    assert resp_css.status_code == 200
    css_etag = resp_css.headers.get("etag")
    assert css_etag is not None
    resp_css_304 = _client.get("/css/styles.css", headers={"If-None-Match": css_etag})
    assert resp_css_304.status_code == 304


def test_ai_models_endpoint():
    """Проверка эндпоинта /api/ai/models."""
    resp = _client.get("/api/ai/models")
    assert resp.status_code == 200
    data = resp.json()
    assert "models" in data
    assert "current" in data


def test_draft_parse_screenshot_requires_key_or_handles_call():
    """Проверка валидации эндпоинта /api/draft/parse-screenshot."""
    # Некорректный base64
    resp_bad = _client.post("/api/draft/parse-screenshot", json={"image_b64": "!!!not_base64!!!"})
    assert resp_bad.status_code in (422, 502)


# --- V2.0: глубина разбора / переразбор / прогресс --------------------------


def _make_raw_match(match_id):
    return {
        "match_id": match_id,
        "match_info": {
            "players": [
                {"account_id": 11, "hero_id": 1, "team": 0, "kills": 5, "deaths": 2,
                 "assists": 8, "level": 14, "net_worth": 18000,
                 "stats": [{"time_stamp_s": 600, "net_worth": 4500}]},
                {"account_id": 22, "hero_id": 2, "team": 1, "kills": 10, "deaths": 1,
                 "assists": 4, "level": 16, "net_worth": 24000,
                 "stats": [{"time_stamp_s": 600, "net_worth": 6200}]},
            ],
            "winning_team": 0,
            "duration_s": 1500,
        },
    }


def test_match_analyze_invalid_depth_422(monkeypatch):
    """Неизвестная глубина -> 422 ещё до сети/LLM (offline тоже)."""
    from deadlockforge.engine import match_loader as ml

    raw = _make_raw_match("depth-bad-1")
    monkeypatch.setenv("DEADLOCKFORGE_AI_KEY", "")
    monkeypatch.setattr(ml, "fetch_match", lambda mid, db=None: raw)
    monkeypatch.setattr(ml, "load_hero_map", lambda: {1: "infernus", 2: "seven"})
    resp = _client.post(
        "/api/match/analyze",
        json={"match_id": "depth-bad-1", "account_id": 11,
              "mode": "offline", "depth": "ultra"},
    )
    assert resp.status_code == 422
    assert "Неизвестная глубина" in resp.json()["detail"]


def test_match_reanalyze_invalid_depth_422(monkeypatch):
    """Переразбор с неизвестной глубиной -> 422."""
    from deadlockforge.engine import match_loader as ml

    raw = _make_raw_match("depth-bad-2")
    monkeypatch.setenv("DEADLOCKFORGE_AI_KEY", "")
    monkeypatch.setattr(ml, "fetch_match", lambda mid, db=None: raw)
    monkeypatch.setattr(ml, "load_hero_map", lambda: {1: "infernus", 2: "seven"})
    first = _client.post("/api/match/analyze", json={
        "match_id": "depth-bad-2", "account_id": 11, "mode": "offline",
    })
    assert first.status_code == 200
    resp = _client.post(
        "/api/match/reviews/depth-bad-2/reanalyze",
        json={"mode": "offline", "depth": "insane"},
    )
    assert resp.status_code == 422
    assert "Неизвестная глубина" in resp.json()["detail"]


def test_match_analyze_offline_accepts_all_depths(monkeypatch):
    """Все три глубины проходят offline-разбор и возвращают MatchReview."""
    from deadlockforge.engine import match_loader as ml

    raw = _make_raw_match("depth-offline-1")
    monkeypatch.setenv("DEADLOCKFORGE_AI_KEY", "")
    monkeypatch.setattr(ml, "fetch_match", lambda mid, db=None: raw)
    monkeypatch.setattr(ml, "load_hero_map", lambda: {1: "infernus", 2: "seven"})
    for depth in ("short", "medium", "deep"):
        resp = _client.post(
            "/api/match/analyze",
            json={"match_id": "depth-offline-1", "account_id": 11,
                  "mode": "offline", "depth": depth},
        )
        assert resp.status_code == 200, f"depth={depth}: {resp.text}"
        review = resp.json()
        assert review["match_id"] == "depth-offline-1"
        assert review["hero_id"] == "infernus"
        assert review["kda"] == "5/2/8"


def test_match_analyze_ai_mode_passes_depth_and_player_context(monkeypatch):
    """В AI-режиме depth и player_context доезжают до coach.analyze_match."""
    from deadlockforge.engine import match_loader as ml
    from deadlockforge.ai import coach as coach_mod
    from deadlockforge.models import Benchmarks, CoachReport, KeyMistake

    raw = _make_raw_match("depth-ai-1")
    monkeypatch.setenv("DEADLOCKFORGE_AI_KEY", "sk-test-ai-mode-12345678")
    monkeypatch.setattr(ml, "fetch_match", lambda mid, db=None: raw)
    monkeypatch.setattr(ml, "load_hero_map", lambda: {1: "infernus", 2: "seven"})

    report = CoachReport(
        performance_grade="A",
        key_mistake=KeyMistake(time="05:00", location="оранжевая линия",
                               event="e", why_fatal="f", correct_play="c"),
        coach_conclusion=["раз", "два", "три"],
        benchmarks=Benchmarks(souls_at_10=1.1, lane_efficiency=1.0,
                              fight_participation=0.5),
    )
    captured: dict = {}

    def fake_analyze_match(match, account_id, client, **kwargs):
        captured.update({"account_id": account_id, "kwargs": kwargs})
        return report

    monkeypatch.setattr(coach_mod, "analyze_match", fake_analyze_match)
    resp = _client.post(
        "/api/match/analyze",
        json={"match_id": "depth-ai-1", "account_id": 11, "depth": "deep"},
    )
    assert resp.status_code == 200, resp.text
    assert captured["account_id"] == 11
    assert captured["kwargs"]["depth"] == "deep"
    assert isinstance(captured["kwargs"].get("player_context"), dict)
    assert "trend" in captured["kwargs"]["player_context"]
    review = resp.json()
    assert review["match_id"] == "depth-ai-1"
    assert review["performance_grade"] == "A"
    # прогресс «с собой» приложен к ответу
    assert isinstance(review.get("progress"), dict)
    assert "trend" in review["progress"]


def test_match_reanalyze_rebuilds_review(monkeypatch):
    """Переразбор сохранённого матча: 200, разбор обновился и сохранился."""
    from deadlockforge.engine import match_loader as ml

    monkeypatch.setenv("DEADLOCKFORGE_AI_KEY", "")
    monkeypatch.setattr(ml, "fetch_match", lambda mid, db=None: _make_raw_match(mid))
    monkeypatch.setattr(ml, "load_hero_map", lambda: {1: "infernus", 2: "seven"})

    # Первый разбор -> сохраняется в БД
    r1 = _client.post("/api/match/analyze", json={
        "match_id": "reanalyze-1", "account_id": 11,
        "mode": "offline", "depth": "short",
    })
    assert r1.status_code == 200

    # Переразбор с другой глубиной/точкой зрения
    r2 = _client.post("/api/match/reviews/reanalyze-1/reanalyze", json={
        "account_id": 11, "mode": "offline", "depth": "deep",
    })
    assert r2.status_code == 200, r2.text
    review = r2.json()
    assert review["match_id"] == "reanalyze-1"
    assert review["hero_id"] == "infernus"
    assert review["kda"] == "5/2/8"

    # В БД лежит обновлённый разбор (upsert, а не дубль)
    stored = _client.get("/api/match/reviews?limit=200").json()
    versions = [r for r in stored if r["match_id"] == "reanalyze-1"]
    assert len(versions) == 1
    assert versions[0]["performance_grade"] == review["performance_grade"]


def test_match_reanalyze_resolves_perspective_from_saved_hero(monkeypatch):
    """Без account_id переразбор берёт точку зрения героя из сохранённого разбора."""
    from deadlockforge.engine import match_loader as ml

    monkeypatch.setenv("DEADLOCKFORGE_AI_KEY", "")
    monkeypatch.setattr(ml, "fetch_match", lambda mid, db=None: _make_raw_match(mid))
    monkeypatch.setattr(ml, "load_hero_map", lambda: {1: "infernus", 2: "seven"})

    r1 = _client.post("/api/match/analyze", json={
        "match_id": "reanalyze-2", "hero_slug": "seven", "mode": "offline",
    })
    assert r1.status_code == 200
    assert r1.json()["hero_id"] == "seven"

    r2 = _client.post("/api/match/reviews/reanalyze-2/reanalyze", json={
        "mode": "offline", "depth": "deep",
    })
    assert r2.status_code == 200, r2.text
    assert r2.json()["hero_id"] == "seven"
    assert r2.json()["kda"] == "10/1/4"


def test_match_reanalyze_unknown_match_404(monkeypatch):
    """Неизвестный match_id -> 404 до сетевых вызовов."""
    from deadlockforge.engine import match_loader as ml

    called = {"fetch": 0}

    def fake_fetch(mid, db=None):
        called["fetch"] += 1
        raise AssertionError("не должен идти в сеть")

    monkeypatch.setattr(ml, "fetch_match", fake_fetch)
    resp = _client.post(
        "/api/match/reviews/no-such-match-xyz/reanalyze", json={"mode": "offline"})
    assert resp.status_code == 404
    assert called["fetch"] == 0


def test_match_reanalyze_screenshot_rejected(monkeypatch):
    """Скриншот-разборы (без телеметрии) переразобрать нельзя -> 404."""
    from deadlockforge.engine import match_loader as ml

    monkeypatch.setenv("DEADLOCKFORGE_AI_KEY", "")
    monkeypatch.setattr(ml, "parse_screenshot", lambda img: {"mode": "screenshot"})
    monkeypatch.setattr(ml, "fetch_match", lambda mid, db=None: {})

    r = _client.post("/api/match/analyze", json={
        "image_b64": "QUJD", "mode": "offline",
    })
    assert r.status_code == 200
    mid = r.json()["match_id"]
    assert mid.startswith("screenshot-")

    rr = _client.post(f"/api/match/reviews/{mid}/reanalyze", json={"mode": "offline"})
    assert rr.status_code == 404
    assert "переразбор невозможен" in rr.json()["detail"]




