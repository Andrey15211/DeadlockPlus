"""V2.0: тесты «сравнения с самим собой» (ТЗ §3.4/§3.6).

ai.progress агрегирует match_reviews из БД в тренд (player_trend) и профиль
слабостей (weakness_profile) — без сети и LLM, только строки из storage.db.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from deadlockforge.ai import progress as prog  # noqa: E402


def make_review(match_id, grade, kda, souls_at_10=1.0, result="win",
                deaths_hint=None, created_at="2026-01-01 00:00:00",
                key_mistake=None, item_corrections=None):
    k, d, a = kda.split("/")
    deaths = deaths_hint if deaths_hint is not None else int(d)
    return {
        "match_id": match_id,
        "hero_id": "infernus",
        "duration_seconds": 2100,
        "result": result,
        "kda": kda,
        "souls_curve": [],
        "performance_grade": grade,
        "key_mistake": key_mistake or {
            "time": "15:30", "location": "оранжевая линия",
            "event": "x", "why_fatal": "x", "correct_play": "x",
        },
        "coach_conclusion": ["a", "b", "c"],
        "stat_benchmarks": {
            "souls_at_10": souls_at_10,
            "lane_efficiency": 1.0,
            "fight_participation": 0.5,
        },
        "item_analysis": {"corrections": item_corrections or []},
        "created_at": created_at,
    }


def test_parse_kda_string_and_dict():
    assert prog.parse_kda("14/3/9") == (14, 3, 9)
    assert prog.parse_kda("2/5") == (2, 5, 0)
    assert prog.parse_kda({"kills": 1, "deaths": 2, "assists": 3}) == (1, 2, 3)
    assert prog.parse_kda("") == (0, 0, 0)


def test_grade_score():
    assert prog.grade_score("S+") == 5
    assert prog.grade_score("C") == 1
    assert prog.grade_score("unknown") == 0


def test_player_trend_improving():
    """Два матча: оценка выросла C -> A, смертей меньше -> improving."""
    reviews = [
        make_review("old", "C", "5/6/3", souls_at_10=0.8, result="loss",
                    created_at="2026-01-01 00:00:00"),
        make_review("new", "A", "14/3/9", souls_at_10=1.2, result="win",
                    created_at="2026-01-02 00:00:00"),
    ]
    trend = prog.player_trend(reviews)
    assert trend["count"] == 2
    assert trend["direction"] == "improving"
    assert trend["latest"]["match_id"] == "new"
    assert trend["latest"]["grade_label"] == "A"
    assert trend["previous"]["count"] == 1
    assert trend["previous"]["median_grade"] == 1.0  # C
    assert trend["deltas"]["grade"] == 2.0  # A(3) - C(1)
    assert trend["deltas"]["deaths"] < 0  # смертей стало меньше
    assert trend["deltas"]["souls_at_10"] > 0


def test_player_trend_declining():
    reviews = [
        make_review("old", "A", "12/2/8", souls_at_10=1.2, result="win",
                    created_at="2026-01-01 00:00:00"),
        make_review("new", "C", "3/9/2", souls_at_10=0.7, result="loss",
                    created_at="2026-01-02 00:00:00"),
    ]
    trend = prog.player_trend(reviews)
    assert trend["direction"] == "declining"
    assert trend["deltas"]["grade"] < 0
    assert trend["deltas"]["deaths"] > 0


def test_player_trend_insufficient_data():
    trend = prog.player_trend([])
    assert trend["direction"] == "insufficient_data"
    assert trend["latest"] is None
    one = prog.player_trend([make_review("only", "B", "8/4/4")])
    assert one["direction"] == "insufficient_data"
    assert one["latest"]["match_id"] == "only"
    assert one["previous"] is None


def test_player_trend_window_limits_previous():
    """Окно 3: для медианы берутся только 3 предыдущих матча."""
    reviews = [
        make_review(f"m{i}", "C", "5/6/3", created_at=f"2026-01-0{i + 1} 00:00:00")
        for i in range(6)
    ]
    trend = prog.player_trend(reviews, window=3)
    assert trend["previous"]["count"] == 3


def test_player_trend_mixed_direction():
    """Оценка выросла, но смертей стало больше -> mixed."""
    reviews = [
        make_review("old", "C", "5/2/3", created_at="2026-01-01 00:00:00"),
        make_review("new", "A", "14/6/9", created_at="2026-01-02 00:00:00"),
    ]
    trend = prog.player_trend(reviews)
    assert trend["direction"] == "mixed"


def test_weakness_profile_buckets_and_items():
    reviews = [
        make_review(
            "m1", "C", "3/9/2",
            key_mistake={"time": "05:30", "location": "оранжевая линия",
                         "event": "x", "why_fatal": "x", "correct_play": "x"},
            item_corrections=[
                {"slot": "weapon-2", "sold_or_skipped": "-",
                 "recommended": "Нокдаун", "reason": "против Семь"},
            ],
            created_at="2026-01-01 00:00:00",
        ),
        make_review(
            "m2", "B", "8/4/4",
            key_mistake={"time": "25:10", "location": "яма Мидбосса",
                         "event": "x", "why_fatal": "x", "correct_play": "x"},
            item_corrections=[
                {"slot": "vitality-1", "sold_or_skipped": "-",
                 "recommended": "Нокдаун", "reason": "против Семь"},
                {"slot": "vitality-2", "sold_or_skipped": "-",
                 "recommended": "Металлическая кожа", "reason": "против Хейз"},
            ],
            created_at="2026-01-02 00:00:00",
        ),
    ]
    prof = prog.weakness_profile(reviews)
    assert prof["total_reviews"] == 2
    buckets = prof["death_timing_buckets"]
    assert buckets["early_0_10"] == 1
    assert buckets["late_20_plus"] == 1
    assert buckets["mid_10_20"] == 0
    # Нокдаун упоминается дважды — топ-1 пропущенный предмет
    assert prof["top_missing_items"][0]["item"] == "Нокдаун"
    assert prof["top_missing_items"][0]["count"] == 2
    assert prof["top_missing_items"][1]["item"] == "Металлическая кожа"
    # частые места ошибок
    locations = {l["location"]: l["count"] for l in prof["top_key_mistake_locations"]}
    assert locations.get("оранжевая линия") == 1
    assert locations.get("яма Мидбосса") == 1


def test_build_player_context_shape():
    ctx = prog.build_player_context([
        make_review("m1", "C", "5/6/3", created_at="2026-01-01 00:00:00"),
        make_review("m2", "A", "14/3/9", created_at="2026-01-02 00:00:00"),
    ])
    assert "trend" in ctx and "weaknesses" in ctx
    assert ctx["trend"]["direction"] == "improving"


def test_context_to_prompt_ru_text():
    ctx = prog.build_player_context([
        make_review("m1", "C", "5/6/3", created_at="2026-01-01 00:00:00"),
        make_review("m2", "A", "14/3/9", created_at="2026-01-02 00:00:00"),
    ])
    text = prog.context_to_prompt(ctx)
    assert "ИСТОРИЯ МАТЧЕЙ ИГРОКА" in text
    assert "СЛАБОСТИ ИГРОКА" in text
    assert "Последний матч m2" in text
    assert "Тренд: improving" in text
    assert "Смерти по таймингам" in text
    # пустой контекст -> пустая строка
    assert prog.context_to_prompt({}) == ""
    assert prog.context_to_prompt(None) == ""
