#!/usr/bin/env python
"""refresh_meta.py — актуализация тир-листа (TZ A2).

Тянет статистику героев из deadlock-api (heroes endpoint), обновляет
winrate/pickrate в data/heroes.json и проставляет meta_updated_at.

Правила:
- ростер не меняется: heroes.json только дополняется полями, никаких
  удалений/добавлений героев;
- heroes без api_id или без статистики в API остаются нетронутыми;
- offline-режим не ломается: при сбое сети скрипт завершается с кодом 1
  и внятным сообщением, файл не трогает.

Запуск: python scripts/refresh_meta.py [--dry-run] [--data-dir path]
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

HERO_STATS_API = "https://api.deadlock-api.com/v1/analytics/hero-stats"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) DeadlockForge/1.1"
HTTP_TIMEOUT = 30


def _http_get_json(url: str) -> object:
    req = urllib.request.Request(url, headers={
        "User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def _hero_stats_payload() -> list[dict]:
    """Агрегаты по героям: [{hero_id, bucket, wins, losses, matches, ...}].

    bucket=0 — все ранги (глобальная статистика); берём только его,
    чтобы не смешивать MMR-бакеты. Пикрейт считаем от доли матчей героя
    в сумме матчей всех героев (12 игроков на матч — делят на 12).
    """
    raw = _http_get_json(HERO_STATS_API)
    if not isinstance(raw, list):
        raise RuntimeError(f"unexpected hero-stats payload: {type(raw).__name__}")
    return [entry for entry in raw if entry.get("bucket") == 0]


def _extract_winrate(entry: dict) -> tuple[float | None, float | None]:
    """Winrate из wins/matches; pickrate — доля матчей героя (ниже)."""
    wins = entry.get("wins")
    matches = entry.get("matches")
    wr = None
    if isinstance(wins, (int, float)) and isinstance(matches, (int, float)) and matches > 0:
        wr = float(wins) / float(matches)
    return wr, None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="показать изменения, не писать файл")
    parser.add_argument("--data-dir", default=None,
                        help="каталог с heroes.json (по умолчанию data/ проекта)")
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    data_dir = Path(args.data_dir) if args.data_dir else root / "data"
    heroes_file = data_dir / "heroes.json"
    if not heroes_file.is_file():
        print(f"heroes.json не найден: {heroes_file}", file=sys.stderr)
        return 1

    try:
        entries = _hero_stats_payload()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        print(f"Сеть недоступна, мета не обновлена: {exc}", file=sys.stderr)
        return 1
    except (json.JSONDecodeError, RuntimeError) as exc:
        print(f"Некорректный ответ API: {exc}", file=sys.stderr)
        return 1

    stats_by_api_id: dict[int, dict] = {}
    for entry in entries:
        eid = entry.get("hero_id") or entry.get("id")
        if isinstance(eid, int) or (isinstance(eid, str) and eid.isdigit()):
            stats_by_api_id[int(eid)] = entry

    # Пикрейт: доля матчей с участием героя. В Deadlock 12 героев на матч,
    # поэтому сумма matches по героям ≈ 12x матчей; нормируем на это.
    total_hero_matches = sum(
        e.get("matches") or 0 for e in entries
        if isinstance(e.get("matches"), (int, float))
    )

    heroes = json.loads(heroes_file.read_text(encoding="utf-8"))
    today = date.today().isoformat()
    updated, untouched = 0, 0
    for hero in heroes:
        api_id = hero.get("api_id")
        entry = stats_by_api_id.get(int(api_id)) if api_id else None
        if entry is None:
            untouched += 1
            continue
        wr, _pr = _extract_winrate(entry)
        pr = None
        matches = entry.get("matches")
        if total_hero_matches > 0 and isinstance(matches, (int, float)) and matches > 0:
            pr = float(matches) / total_hero_matches
        if wr is None and pr is None:
            untouched += 1
            continue
        if wr is not None:
            hero["winrate"] = round(wr, 4)
        if pr is not None:
            hero["pickrate"] = round(pr, 4)
        hero["meta_updated_at"] = today
        updated += 1

    print(f"Героев обновлено: {updated}; без статистики: {untouched}")
    print(f"meta_updated_at: {today}")
    if args.dry_run:
        print("dry-run: файл не записан")
        return 0

    heroes_file.write_text(
        json.dumps(heroes, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")
    print(f"Записано: {heroes_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
