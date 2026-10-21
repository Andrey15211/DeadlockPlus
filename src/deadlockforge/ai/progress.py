"""Player progress: trend and weakness profile from match_reviews history.

V2.0 (ТЗ §3.4/§3.6/§7): «сравнение с самим собой» — агрегируем сохранённые
разборы из SQLite (match_reviews) в два артефакта:
  - player_trend: последний матч vs медиана предыдущих (оценка, KDA,
    souls@10, deaths per match, винрейт);
  - weakness_profile: частые тайминги смертей, пропущенные контр-предметы,
    частые места ключевых ошибок.

Никакой сети и LLM: только агрегация строк из storage.db. Используется
коучем (контекст промпта) и эндпоинтом /api/match/analyze (поле progress).
"""

from __future__ import annotations

import json
from typing import Any, Optional

GRADE_SCORE: dict[str, int] = {"S+": 5, "S": 4, "A": 3, "B": 2, "C": 1}
GRADE_LABEL: dict[int, str] = {5: "S+", 4: "S", 3: "A", 2: "B", 1: "C"}


def _as_dict(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _as_list(value: Any) -> list:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def _as_str(value: Any) -> str:
    return "" if value is None else str(value)


def parse_kda(kda: Any) -> tuple[int, int, int]:
    """'14/3/9' или {'kills':..,'deaths':..,'assists':..} -> (k, d, a)."""
    if isinstance(kda, dict):
        return (
            int(kda.get("kills") or 0),
            int(kda.get("deaths") or 0),
            int(kda.get("assists") or 0),
        )
    parts = _as_str(kda).replace(" ", "").split("/")
    parts = (parts + ["0", "0", "0"])[:3]
    try:
        return int(parts[0] or 0), int(parts[1] or 0), int(parts[2] or 0)
    except (TypeError, ValueError):
        return 0, 0, 0


def grade_score(grade: Any) -> int:
    return GRADE_SCORE.get(_as_str(grade).strip().upper(), 0)


def _median(nums: list[float]) -> Optional[float]:
    if not nums:
        return None
    ordered = sorted(nums)
    n = len(ordered)
    mid = n // 2
    if n % 2:
        return float(ordered[mid])
    return (float(ordered[mid - 1]) + float(ordered[mid])) / 2.0


def _souls_at_10(review: dict) -> Optional[float]:
    """Коэффициент душ к 10:00 (1.0 = норма роли) из stat_benchmarks."""
    bm = _as_dict(review.get("stat_benchmarks"))
    val = bm.get("souls_at_10")
    if isinstance(val, (int, float)) and val > 0:
        return float(val)
    actual, target = bm.get("actual_souls"), bm.get("target_souls")
    if isinstance(actual, (int, float)) and isinstance(target, (int, float)) and target:
        return float(actual) / float(target)
    return None


def _row_metrics(review: dict) -> dict:
    k, d, a = parse_kda(review.get("kda"))
    return {
        "grade": grade_score(review.get("performance_grade")),
        "kda": f"{k}/{d}/{a}",
        "kills": k,
        "deaths": d,
        "assists": a,
        "kda_ratio": (k + a) / max(1, d),
        "souls_at_10": _souls_at_10(review),
        "win": _as_str(review.get("result")).lower() == "win",
        "duration_seconds": int(review.get("duration_seconds") or 0),
    }


def _sorted_reviews(reviews: list[dict]) -> list[dict]:
    """Сначала старые: created_at, при равенстве — match_id (стабильно)."""
    return sorted(
        reviews,
        key=lambda r: (_as_str(r.get("created_at")), _as_str(r.get("match_id"))),
    )


def player_trend(reviews: list[dict], window: int = 10) -> dict:
    """Сравнение последнего разбора с медианой предыдущих (окно window).

    Поля: count, window, direction (improving|declining|mixed|stable|
    insufficient_data), latest (метрики последнего матча), previous (медианы
    предыдущих + винрейт), deltas (последний − медиана).
    """
    ordered = _sorted_reviews(reviews)
    if not ordered:
        return {
            "count": 0, "window": window, "direction": "insufficient_data",
            "latest": None, "previous": None, "deltas": {},
        }
    last_row = ordered[-1]
    latest = _row_metrics(last_row)
    latest_full = {
        **latest,
        "match_id": _as_str(last_row.get("match_id")),
        "grade_label": GRADE_LABEL.get(latest["grade"], "C"),
    }
    prev_reviews = ordered[:-1][-window:]
    if not prev_reviews:
        return {
            "count": len(ordered), "window": window,
            "direction": "insufficient_data",
            "latest": latest_full, "previous": None, "deltas": {},
        }

    prev = [_row_metrics(r) for r in prev_reviews]
    souls_prev = [p["souls_at_10"] for p in prev if p["souls_at_10"] is not None]
    previous = {
        "count": len(prev),
        "median_grade": _median([float(p["grade"]) for p in prev]),
        "median_deaths": _median([float(p["deaths"]) for p in prev]),
        "median_kda_ratio": _median([p["kda_ratio"] for p in prev]),
        "median_souls_at_10": _median(souls_prev),
        "winrate": round(sum(1 for p in prev if p["win"]) / len(prev), 2),
    }

    deltas: dict[str, float] = {}
    if previous["median_grade"] is not None:
        deltas["grade"] = round(latest["grade"] - previous["median_grade"], 2)
    if previous["median_deaths"] is not None:
        deltas["deaths"] = round(latest["deaths"] - previous["median_deaths"], 2)
    if previous["median_kda_ratio"] is not None:
        deltas["kda_ratio"] = round(latest["kda_ratio"] - previous["median_kda_ratio"], 2)
    if previous["median_souls_at_10"] is not None and latest["souls_at_10"] is not None:
        deltas["souls_at_10"] = round(latest["souls_at_10"] - previous["median_souls_at_10"], 2)

    grade_delta = deltas.get("grade", 0.0)
    deaths_delta = deltas.get("deaths", 0.0)
    if grade_delta > 0 and deaths_delta <= 0:
        direction = "improving"
    elif grade_delta < 0 and deaths_delta >= 0:
        direction = "declining"
    elif grade_delta or deaths_delta:
        direction = "mixed"
    else:
        direction = "stable"

    return {
        "count": len(ordered), "window": window, "direction": direction,
        "latest": latest_full, "previous": previous, "deltas": deltas,
    }


def _death_bucket(minutes: float) -> str:
    if minutes < 10:
        return "early_0_10"
    if minutes < 20:
        return "mid_10_20"
    return "late_20_plus"


def weakness_profile(reviews: list[dict], top_n: int = 3) -> dict:
    """Агрегированный профиль слабостей из прошлых разборов.

    Источники: key_mistake.time (тайминг главной ошибки -> бакет времени),
    item_analysis.corrections[].recommended (пропущенные/недокупленные
    предметы), key_mistake.location (частые места ошибок).
    """
    total = len(reviews)
    buckets: dict[str, int] = {"early_0_10": 0, "mid_10_20": 0, "late_20_plus": 0}
    missing: dict[str, int] = {}
    locations: dict[str, int] = {}
    for r in reviews:
        km = _as_dict(r.get("key_mistake"))
        t = _as_str(km.get("time"))
        if t and ":" in t:
            try:
                mm, ss = t.split(":")
                minutes = int(mm) + int(ss) / 60.0
                buckets[_death_bucket(minutes)] += 1
            except (TypeError, ValueError):
                pass
        loc = _as_str(km.get("location")).strip()
        if loc and loc != "нет данных":
            locations[loc] = locations.get(loc, 0) + 1
        for corr in _as_list(_as_dict(r.get("item_analysis")).get("corrections")):
            if isinstance(corr, dict):
                rec = _as_str(corr.get("recommended")).strip()
                if rec:
                    missing[rec] = missing.get(rec, 0) + 1
    return {
        "total_reviews": total,
        "death_timing_buckets": buckets,
        "top_key_mistake_locations": [
            {"location": loc, "count": n}
            for loc, n in sorted(locations.items(), key=lambda kv: kv[1], reverse=True)[:top_n]
        ],
        "top_missing_items": [
            {"item": item, "count": n}
            for item, n in sorted(missing.items(), key=lambda kv: kv[1], reverse=True)[:top_n]
        ],
    }


def build_player_context(reviews: list[dict], window: int = 10) -> dict:
    """Полный контекст игрока для промпта и ответа API."""
    return {
        "trend": player_trend(reviews, window=window),
        "weaknesses": weakness_profile(reviews),
        "auto_context": build_auto_context(reviews),
    }


# --- V2.0 §6: «Тренер меня знает» -------------------------------------------

AUTO_CONTEXT_LIMIT = 5


def _repeat_phrase(patterns: dict[str, int]) -> tuple[str | None, int]:
    """Самый частый паттерн и его счётчик; (None, 0) если паттернов нет."""
    if not patterns:
        return None, 0
    text, count = max(patterns.items(), key=lambda kv: (kv[1], kv[0]))
    return text, count


def build_auto_context(reviews: list[dict], hero_slug: str | None = None,
                       limit: int = AUTO_CONTEXT_LIMIT) -> str:
    """V2.0 §6.1: автоматический контекст игрока из последних N разборов.

    Возвращает готовую строку для системного промпта коуча (или "" — если
    разборов нет). Игрок ничего не вводит руками: чем больше матчей
    разобрано, тем точнее контекст.

    Агрегируются повторяющиеся ошибки из key_mistake (тайминг главной
    ошибки + место) и coach_conclusion (пункт про главную причину исхода).
    """
    rows = _sorted_reviews(reviews)
    if hero_slug:
        filtered = [r for r in rows
                    if _as_str(r.get("hero_id")).lower() == str(hero_slug).lower()]
        if filtered:
            rows = filtered
    rows = rows[-limit:]
    total = len(rows)
    if total == 0:
        return ""

    early = mid = late = 0
    locations: dict[str, int] = {}
    missing_items: dict[str, int] = {}
    for r in rows:
        km = _as_dict(r.get("key_mistake"))
        minutes = _mmss_to_minutes(_as_str(km.get("time")))
        if minutes is not None:
            bucket = _death_bucket(minutes)
            early += bucket == "early_0_10"
            mid += bucket == "mid_10_20"
            late += bucket == "late_20_plus"
        loc = _as_str(km.get("location")).strip()
        if loc and loc != "нет данных":
            locations[loc] = locations.get(loc, 0) + 1
        for corr in _as_list(_as_dict(r.get("item_analysis")).get("corrections")):
            if isinstance(corr, dict):
                rec = _as_str(corr.get("recommended")).strip()
                if rec:
                    missing_items[rec] = missing_items.get(rec, 0) + 1

    parts: list[str] = []
    if early:
        parts.append(f"ранние ошибки/смерти до 10:00 (в {early} из {total} игр)")
    if mid:
        parts.append(f"ошибки в мидгейме 10–20 мин (в {mid} из {total} игр)")
    if late:
        parts.append(f"смерти в лейте после 20:00 (в {late} из {total} игр)")

    item, item_count = _repeat_phrase(missing_items)
    if item and item_count >= 2:
        parts.append(f"задержка с покупкой «{item}» (в {item_count} из {total} игр)")

    loc, loc_count = _repeat_phrase(locations)
    if loc and loc_count >= 2:
        parts.append(f"одно и то же место ошибок — «{loc}» (в {loc_count} из {total} игр)")

    if not parts:
        return (f"Исторический контекст игрока: разобрано {total} игр(ы), "
                f"устойчивых повторяющихся паттернов ошибок не выявлено.")

    return ("Исторический контекст игрока: в последних "
            f"{total} разборах зафиксированы: " + "; ".join(parts) +
            ". Отметь, повторяются ли эти паттерны в текущей игре.")


def _mmss_to_minutes(value: str) -> Optional[float]:
    """'MM:SS' -> минуты float. None — мусор/пусто."""
    if not value or ":" not in value:
        return None
    try:
        mm, ss = value.split(":", 1)
        return int(mm) + int(ss) / 60.0
    except (TypeError, ValueError):
        return None


def personal_benchmark(review: dict, trend: dict | None) -> dict:
    """V2.0 §4.1: сравнение текущего матча со средней нормой игрока на герое.

    На входе — сохранённый/отдаваемый MatchReview (dict) и результат
    storage.db.get_hero_player_trends. Возвращает структуру personal_benchmark
    с дельтами и человеческим резюме; при отсутствии истории — games: 0.
    """
    t = trend or {}
    games = int(t.get("games") or 0)
    if games <= 0:
        return {"games": 0, "trend_summary": "Недостаточно истории на этом герое для сравнения."}

    bm = _as_dict(review.get("stat_benchmarks"))
    actual_souls = bm.get("actual_souls")
    early_deaths = bm.get("early_deaths")
    k, d, a = parse_kda(review.get("kda"))
    kda_ratio = round((k + a) / max(1, d), 2)

    souls_diff = None
    avg_souls = t.get("avg_souls_at_10")
    if isinstance(actual_souls, (int, float)) and isinstance(avg_souls, (int, float)):
        souls_diff = int(round(float(actual_souls) - float(avg_souls)))

    deaths_diff = None
    avg_early = t.get("avg_early_deaths")
    if isinstance(early_deaths, (int, float)) and isinstance(avg_early, (int, float)):
        deaths_diff = int(round(float(early_deaths) - float(avg_early)))

    kda_diff = None
    avg_kda = t.get("avg_kda")
    if isinstance(avg_kda, (int, float)):
        kda_diff = round(kda_ratio - float(avg_kda), 2)

    bits: list[str] = []
    if souls_diff is not None:
        if souls_diff >= 0:
            bits.append(f"Отличный прогресс по фарму на линии (+{souls_diff} душ к вашей "
                        f"средней норме на этом герое).")
        else:
            bits.append(f"Фарм на линии просел ({souls_diff} душ к вашей средней норме "
                        f"на этом герое).")
    if deaths_diff is not None:
        if deaths_diff <= 0:
            bits.append(f"Ранних смертей меньше обычного ({early_deaths} против "
                        f"{round(float(avg_early), 1)} в среднем).")
        else:
            bits.append(f"Ранних смертей больше обычного ({early_deaths} против "
                        f"{round(float(avg_early), 1)} в среднем).")
    if kda_diff is not None:
        sign = "+" if kda_diff >= 0 else ""
        bits.append(f"KDA {'выше' if kda_diff >= 0 else 'ниже'} обычного на {sign}{kda_diff}.")
    if not bits:
        bits.append(f"Сравнение с {games} прошлыми играми на герое: метрик для дельт нет.")

    return {
        "games": games,
        "souls_at_10_diff": souls_diff,
        "early_deaths_diff": deaths_diff,
        "kda_diff": kda_diff,
        "current_kda": kda_ratio,
        "avg_souls_at_10": avg_souls,
        "avg_early_deaths": avg_early,
        "avg_kda": avg_kda,
        "trend_summary": " ".join(bits),
    }


def context_to_prompt(ctx: dict | None) -> str:
    """Русский текстовый блок «история игрока» для вставки в промпт коуча."""
    if not ctx:
        return ""
    trend = ctx.get("trend") or {}
    weaknesses = ctx.get("weaknesses") or {}
    lines: list[str] = ["ИСТОРИЯ МАТЧЕЙ ИГРОКА (ПРОГРЕСС, из БД):"]
    latest = trend.get("latest")
    previous = trend.get("previous")
    if latest and previous:
        lines.append(
            f"- Последний матч {latest.get('match_id', '')}: оценка "
            f"{latest.get('grade_label', 'C')}, KDA {latest.get('kda')}, "
            f"смертей {latest.get('deaths')}, душ@10 {latest.get('souls_at_10')}, "
            f"результат {'победа' if latest.get('win') else 'поражение'}."
        )
        lines.append(
            f"- Медиана предыдущих {previous.get('count')} матчей: оценка "
            f"{previous.get('median_grade')}, смертей {previous.get('median_deaths')}, "
            f"KDA-рейт {previous.get('median_kda_ratio')}, "
            f"душ@10 {previous.get('median_souls_at_10')}, "
            f"винрейт {previous.get('winrate')}."
        )
        deltas = trend.get("deltas") or {}
        parts = []
        if "grade" in deltas:
            parts.append(f"оценка {deltas['grade']:+.1f}")
        if "deaths" in deltas:
            parts.append(f"смертей {deltas['deaths']:+.1f}")
        if "kda_ratio" in deltas:
            parts.append(f"KDA {deltas['kda_ratio']:+.2f}")
        if "souls_at_10" in deltas:
            parts.append(f"душ@10 {deltas['souls_at_10']:+.2f}")
        if parts:
            lines.append("- Дельта: " + ", ".join(parts)
                         + f". Тренд: {trend.get('direction', '—')}.")
    elif latest:
        lines.append("- Пока только 1 разобранный матч — сравнения с прошлым нет.")

    if weaknesses:
        lines.append("СЛАБОСТИ ИГРОКА (из прошлых разборов):")
        buckets = weaknesses.get("death_timing_buckets") or {}
        lines.append(
            f"- Смерти по таймингам: до 10:00 — {buckets.get('early_0_10', 0)}, "
            f"10–20 мин — {buckets.get('mid_10_20', 0)}, "
            f"после 20:00 — {buckets.get('late_20_plus', 0)}."
        )
        if weaknesses.get("top_missing_items"):
            items = ", ".join(
                f"{i['item']} (×{i['count']})" for i in weaknesses["top_missing_items"]
            )
            lines.append(f"- Часто не хватает предметов: {items}.")
        if weaknesses.get("top_key_mistake_locations"):
            locs = ", ".join(
                f"{l['location']} (×{l['count']})" for l in weaknesses["top_key_mistake_locations"]
            )
            lines.append(f"- Частые места ошибок: {locs}.")

    # V2.0 §6: готовая строка авто-контекста («тренер меня знает»).
    auto = ctx.get("auto_context")
    if auto:
        lines.append(str(auto))

    lines.append(
        "УЧТИ ИСТОРИЮ: добавь в coach_conclusion пункт о прогрессе/просадке "
        "игрока относительно его прошлых матчей, если история есть."
    )
    return "\n".join(lines)


__all__ = [
    "player_trend",
    "weakness_profile",
    "build_player_context",
    "build_auto_context",
    "personal_benchmark",
    "context_to_prompt",
    "parse_kda",
    "grade_score",
]
