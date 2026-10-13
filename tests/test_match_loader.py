"""Тесты match_loader: normalize_match на срезе реального API-ответа,
team_curve, kda, нормализация позиций, hero_map, MatchLoadError на 404."""

from __future__ import annotations

import json
import sys
import urllib.error
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from deadlockforge.engine import match_loader as ml  # noqa: E402
from deadlockforge.models import SoulsPoint  # noqa: E402


HERO_MAP = {1: "infernus", 2: "abrams"}


def _raw_match() -> dict:
    """Компактный сэмпл реального /v1/matches/{id}/metadata: 2 игрока,
    4 сэмпла stats, 2 смерти, 3 предмета."""
    return {
        "match_id": 105179800,
        "match_info": {
            "duration_s": 2400,
            "winning_team": 0,
            "team_score": {"team_score": [42, 30]},
            "mid_boss": [{"game_time_s": 900, "team": 1}],
            "objectives": [
                {"game_time_s": 780, "type": "walker", "team": 0},
                {"game_time_s": 60, "type": "guardian", "team": 1},
            ],
            "players": [
                {
                    "account_id": 111,
                    "player_slot": 0,
                    "team": 0,
                    "hero_id": 1,
                    "kills": 14,
                    "deaths": 3,
                    "assists": 9,
                    "level": 25,
                    "net_worth": 82000,
                    "items": [
                        {"game_time_s": 120, "item_id": 101, "sold_time_s": 0},
                        {"game_time_s": 600, "item_id": 102, "sold_time_s": 0},
                        {"game_time_s": 1500, "item_id": 103, "sold_time_s": 1600},
                    ],
                    "death_details": [
                        {"game_time_s": 300, "killer_player_slot": 5,
                         "death_pos": {"x": 10752, "y": -10752}},
                        {"game_time_s": 700, "killer_player_slot": 5,
                         "death_pos": {"x": 0, "y": 0}},
                    ],
                    "stats": [
                        {"time_stamp_s": 0, "net_worth": 0},
                        {"time_stamp_s": 180, "net_worth": 5000},
                        {"time_stamp_s": 360, "net_worth": 11000},
                        {"time_stamp_s": 540, "net_worth": 17000},
                    ],
                },
                {
                    "account_id": 222,
                    "player_slot": 5,
                    "team": 1,
                    "hero_id": 2,
                    "kills": 3,
                    "deaths": 14,
                    "assists": 2,
                    "level": 21,
                    "net_worth": 64000,
                    "items": [
                        {"game_time_s": 300, "item_id": 201, "sold_time_s": 0},
                    ],
                    "death_details": [],
                    "stats": [
                        {"time_stamp_s": 0, "net_worth": 0},
                        {"time_stamp_s": 180, "net_worth": 4000},
                        {"time_stamp_s": 360, "net_worth": 8500},
                        {"time_stamp_s": 540, "net_worth": 13000},
                    ],
                },
            ],
        },
    }


# --- normalize_match -------------------------------------------------------


def test_normalize_basic_fields():
    m = ml.normalize_match(_raw_match(), HERO_MAP)
    assert m.match_id == "105179800"
    assert m.duration_s == 2400
    assert len(m.players) == 2
    assert m.team_score == {"team_score": [42, 30]}
    assert m.objectives[0]["type"] == "walker"
    assert m.mid_boss_kills[0]["game_time_s"] == 900


def test_normalize_hero_map_conversion():
    m = ml.normalize_match(_raw_match(), HERO_MAP)
    assert m.players[0].hero_id_slug == "infernus"
    assert m.players[1].hero_id_slug == "abrams"


def test_normalize_without_hero_map_keeps_numeric_id():
    m = ml.normalize_match(_raw_match(), None)
    assert m.players[0].hero_id_slug == "1"
    assert m.players[1].hero_id_slug == "2"


def test_team_curve_calculation():
    m = ml.normalize_match(_raw_match(), HERO_MAP)
    # team0 - team1 по сэмплам 180s
    assert m.team_curve == [
        (0, 0), (180, 1000), (360, 2500), (540, 4000),
    ]


def test_result_per_player():
    m = ml.normalize_match(_raw_match(), HERO_MAP)
    assert m.result_per_player[111] is True    # team 0 won
    assert m.result_per_player[222] is False


def test_kda_string_by_account_and_hero():
    m = ml.normalize_match(_raw_match(), HERO_MAP)
    assert m.kda(account_id=111) == "14/3/9"
    assert m.kda(hero_id="abrams") == "3/14/2"
    with pytest.raises(ml.MatchLoadError):
        m.kda(account_id=999)


def test_death_positions_normalized_0_1():
    m = ml.normalize_match(_raw_match(), HERO_MAP)
    deaths = m.players[0].deaths_timeline
    assert len(deaths) == 2
    for d in deaths:
        assert 0.0 <= d.pos_x <= 1.0
        assert 0.0 <= d.pos_y <= 1.0
    # (10752, -10752) -> corner (1.0, 0.0), (0,0) -> center (0.5, 0.5)
    assert deaths[0].pos_x == pytest.approx(1.0)
    assert deaths[0].pos_y == pytest.approx(0.0)
    assert deaths[1].pos_x == pytest.approx(0.5)
    assert deaths[1].pos_y == pytest.approx(0.5)
    assert deaths[0].t == 300
    assert deaths[0].killer_hero == "abrams"  # slot 5 -> player 222


def test_items_timeline_with_sold_flag():
    m = ml.normalize_match(_raw_match(), HERO_MAP)
    items = m.players[0].items_timeline
    assert len(items) == 3
    assert items[0].t == 120 and items[0].item_id == 101 and items[0].sold is False
    assert items[2].item_id == 103 and items[2].sold is True


def test_networth_curve_per_player():
    m = ml.normalize_match(_raw_match(), HERO_MAP)
    assert [p.networth for p in m.players[0].networth_curve] == [0, 5000, 11000, 17000]


# --- build_souls_curve -----------------------------------------------------


def test_build_souls_curve_points_and_events():
    m = ml.normalize_match(_raw_match(), HERO_MAP)
    curve = ml.build_souls_curve(m)
    assert all(isinstance(p, SoulsPoint) for p in curve)
    assert [p.networth_delta for p in curve] == [0.0, 1000.0, 2500.0, 4000.0]
    # события привязываются к БЛИЖАЙШЕЙ точке сетки сэмплов (0/180/360/540):
    # midboss 900 -> 540 (конец сетки), walker 780 -> 540 — но точка уже
    # занята Midboss (setdefault), поэтому Walker не аннотируется.
    events = {p.time_sec: p.event for p in curve if p.event}
    assert events.get(540) == "Убийство Midboss"
    assert len(events) == 1


def test_build_souls_curve_fight_event():
    raw = _raw_match()
    # 3 смерти в окне 60s у второго игрока на 180s
    raw["match_info"]["players"][1]["death_details"] = [
        {"game_time_s": 175, "killer_player_slot": 0, "death_pos": {"x": 0, "y": 0}},
        {"game_time_s": 200, "killer_player_slot": 0, "death_pos": {"x": 0, "y": 0}},
        {"game_time_s": 230, "killer_player_slot": 0, "death_pos": {"x": 0, "y": 0}},
    ]
    m = ml.normalize_match(raw, HERO_MAP)
    curve = ml.build_souls_curve(m)
    events = {p.time_sec: p.event for p in curve if p.event}
    assert events.get(180) == "Командный файт"


def test_build_souls_curve_midboss_walker_events():
    raw = _raw_match()
    # совмещаем события с сеткой сэмплов
    raw["match_info"]["mid_boss"] = [{"game_time_s": 360, "team": 1}]
    raw["match_info"]["objectives"] = [{"game_time_s": 540, "type": "walker", "team": 0}]
    m = ml.normalize_match(raw, HERO_MAP)
    curve = ml.build_souls_curve(m)
    events = {p.time_sec: p.event for p in curve if p.event}
    assert events.get(360) == "Убийство Midboss"
    assert events.get(540) == "Падение Walker"


# --- fetch_match (mock urlopen) --------------------------------------------


class _FakeHTTPError(urllib.error.HTTPError):
    def __init__(self, code: int):
        super().__init__("https://x", code, "msg", hdrs=None, fp=None)

    def read(self) -> bytes:
        return b'{"error": "match not found"}'


def test_fetch_match_http_error(monkeypatch):
    def fake_urlopen(req, timeout=None):
        raise _FakeHTTPError(404)

    monkeypatch.setattr(ml.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(ml.MatchLoadError, match="404"):
        ml.fetch_match("999999999")


def test_fetch_match_success(monkeypatch):
    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self) -> bytes:
            return json.dumps(_raw_match()).encode()

    def fake_urlopen(req, timeout=None):
        assert "Mozilla/5.0" in req.headers.get("User-agent", "")
        assert str(req.full_url).endswith("/v1/matches/105179800/metadata")
        return _Resp()

    monkeypatch.setattr(ml.urllib.request, "urlopen", fake_urlopen)
    raw = ml.fetch_match(105179800)
    assert raw["match_info"]["duration_s"] == 2400


# --- parse_screenshot ------------------------------------------------------


def test_parse_screenshot_returns_b64():
    payload = ml.parse_screenshot(b"png-bytes")
    assert payload["mode"] == "screenshot"
    assert ml.base64.b64decode(payload["image_b64"]) == b"png-bytes"


def test_parse_screenshot_empty_raises():
    with pytest.raises(ml.MatchLoadError):
        ml.parse_screenshot(b"")


# --- normalize_match errors ------------------------------------------------


def test_normalize_bad_shape_raises():
    with pytest.raises(ml.MatchLoadError):
        ml.normalize_match({"nope": 1})
