#!/usr/bin/env python
"""update_meta.py — актуализация меты героев из deadlock-api (S1/M1).

Тянет GET /v1/analytics/hero-stats (поля wins/losses/matches по hero_id),
считает winrate = wins/matches и pickrate = matches / sum(matches),
и обновляет в data/heroes.json ТОЛЬКО поля winrate, pickrate, meta_updated_at.
Роли, статы, threat_tags и сигнатуры не трогаются.

Rate limit: без DEADLOCK_API_KEY эндпоинт позволяет ~3 запроса/час.
Поэтому ответ кэшируется в .scratch/hero_stats_cache.json (по умолчанию) и
повторные запуски в течение CACHE_TTL_HOURS не дёргают API. Свежий кэш
используется и как мягкий фолбэк: если API недоступен (сеть/лимит/5xx),
скрипт завершается с кодом 0 и НЕ перезаписывает heroes.json.

Идемпотентность: повторный запуск с тем же кэшем/данными даёт те же
значения и не дублирует/не портит записи (обновление по api_id, in-place).

Примеры:
    python scripts/update_meta.py
    python scripts/update_meta.py --force          # игнорировать свежий кэш
    python scripts/update_meta.py --dry-run        # показать дифф, не писать
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path

HERO_STATS_API = "https://api.deadlock-api.com/v1/analytics/hero-stats"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) DeadlockForge/1.6"
HTTP_TIMEOUT = 45
CACHE_TTL_HOURS = 6  # rate limit ~3/час: не чаще одного запроса за 6 часов


def _http_get_json(url: str) -> object:
    req = urllib.request.Request(
        url, headers={"User-Agent": UA, "Accept": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def fetch_hero_stats() -> list[dict]:
    """Агрегаты по героям; берём только глобальный бакет (все ранги)."""
    raw = _http_get_json(HERO_STATS_API)
    if not isinstance(raw, list):
        raise RuntimeError(f"unexpected hero-stats payload: {type(raw).__name__}")
    return [e for e in raw if isinstance(e, dict) and e.get("bucket") == 0]


def _load_cache(cache_file: Path) -> dict | None:
    """Читаем кэш; любой повреждённый кэш трактуем как отсутствующий."""
    try:
        return json.loads(cache_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _is_fresh(cache: dict) -> bool:
    fetched_at = cache.get("fetched_at")
    if not isinstance(fetched_at, str):
        return False
    try:
        when = datetime.fromisoformat(fetched_at)
    except ValueError:
        return False
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    age_h = (datetime.now(timezone.utc) - when).total_seconds() / 3600
    return 0 <= age_h < CACHE_TTL_HOURS


def compute_stats(entries: list[dict]) -> dict[int, tuple[float, float]]:
    """{api_id: (winrate, pickrate)} — из wins/matches и доли матчей."""
    total_matches = sum(
        e.get("matches") or 0 for e in entries
        if isinstance(e.get("matches"), (int, float))
    )
    out: dict[int, tuple[float, float]] = {}
    for e in entries:
        eid = e.get("hero_id")
        wins = e.get("wins")
        matches = e.get("matches")
        if not isinstance(eid, int) or not isinstance(matches, (int, float)):
            continue
        if matches <= 0:
            continue
        wr = float(wins) / float(matches) if isinstance(wins, (int, float)) else None
        pr = float(matches) / total_matches if total_matches > 0 else None
        if wr is None and pr is None:
            continue
        out[int(eid)] = (wr, pr)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="не писать файлы")
    ap.add_argument("--force", action="store_true",
                    help="игнорировать свежий кэш и запросить API")
    ap.add_argument("--data-dir", default=None, help="каталог с heroes.json")
    ap.add_argument("--cache", default=None, help="путь кэша hero-stats")
    args = ap.parse_args()

    root = Path(__file__).resolve().parents[1]
    data_dir = Path(args.data_dir) if args.data_dir else root / "data"
    heroes_file = data_dir / "heroes.json"
    cache_file = Path(args.cache) if args.cache else root / ".scratch" / "hero_stats_cache.json"

    if not heroes_file.is_file():
        print(f"[meta] heroes.json не найден: {heroes_file}", file=sys.stderr)
        return 0

    heroes = json.loads(heroes_file.read_text(encoding="utf-8"))
    if not isinstance(heroes, list):
        print("[meta] heroes.json: ожидался список героев", file=sys.stderr)
        return 0

    cache = _load_cache(cache_file)
    entries: list[dict] | None = None
    source = "cache"

    if not args.force and cache is not None and _is_fresh(cache):
        entries = cache.get("entries")
        if not isinstance(entries, list):
            entries = None

    if entries is None:
        try:
            entries = fetch_hero_stats()
            source = "api"
            print(f"[meta] API OK: {HERO_STATS_API} ({len(entries)} записей)")
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError,
                OSError, json.JSONDecodeError, RuntimeError) as exc:
            # Мягкий фолбэк: если есть хоть какой-то кэш — берём его,
            # иначе выходим с кодом 0, ничего не перезаписывая.
            if cache is not None and isinstance(cache.get("entries"), list):
                entries = cache["entries"]
                source = "cache-stale"
                print(f"[meta] API недоступен ({exc}); фолбэк на кэш", file=sys.stderr)
            else:
                print(f"[meta] API недоступен ({exc}); данные не изменены", file=sys.stderr)
                return 0

    stats = compute_stats(entries or [])
    if not stats:
        print("[meta] нет статистики для обновления; данные не изменены", file=sys.stderr)
        return 0

    today = date.today().isoformat()
    updated = 0
    for hero in heroes:
        if not isinstance(hero, dict):
            continue
        api_id = hero.get("api_id")
        if not isinstance(api_id, int) or api_id not in stats:
            continue
        wr, pr = stats[api_id]
        if wr is not None:
            hero["winrate"] = round(wr, 4)
        if pr is not None:
            hero["pickrate"] = round(pr, 4)
        hero["meta_updated_at"] = today
        updated += 1

    print(f"[meta] источник: {source}; обновлено героев: {updated}; дата: {today}")
    if args.dry_run:
        print("[meta] dry-run: heroes.json и кэш не записаны")
        return 0

    heroes_file.write_text(
        json.dumps(heroes, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(
        json.dumps({
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "endpoint": HERO_STATS_API,
            "entries": entries or [],
        }, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"[meta] записано: {heroes_file}; кэш: {cache_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
