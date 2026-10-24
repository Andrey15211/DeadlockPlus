"""Сборка data/synergies.json из tier-list синергий Mobalytics.

Источник: https://mobalytics.gg/deadlock/tier-list/synergies

Страница закрыта Cloudflare managed challenge, поэтому прямой запрос почти
всегда отдаёт «Один момент…». Скрипт сначала пробует прямой fetch, а если
данных в HTML нет — берёт тот же URL через текстовый прокси r.jina.ai
(`x-respond-with: html`). В HTML лежит dehydrated-кэш react-query с полным
набором пар (44 героя -> 946 дуо) и метриками.

Tier на сайте не приходит с сервера: он считается из винрейта функцией
`u(l)` в чанке 29910 (C < 45%, B 45-50%, A 50-55%, S >= 55%). Здесь та же
формула, чтобы сорт совпадал с сайтом.

Имена героев берутся из data/heroes.json по name_en: на сайте английские
имена, в программе — официальные русские.

Usage:
    python scripts/build_synergies.py [--input <html>]
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import re
import sys
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "synergies.json"
HEROES = ROOT / "data" / "heroes.json"
SOURCE_URL = "https://mobalytics.gg/deadlock/tier-list/synergies"
PROXY_URL = "https://r.jina.ai/" + SOURCE_URL
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# Порог винрейта (в процентах), начиная с которого сайт ставит сорт.
TIER_RULES = {"S": 55.0, "A": 50.0, "B": 45.0, "C": 0.0}

METHODOLOGY = [
    "Дуо попадает в статистику, только если оба героя на старте матча "
    "играли на одной линии.",
    "Данные собраны из реальных матчей за последние 7 дней: цикл обновления "
    "намеренно короткий, потому что Deadlock часто патчат.",
    "WRF (Win Rate Factor) — прирост винрейта дуо относительно среднего "
    "винрейта этих же героев: плюс означает, что связка сильнее суммы частей.",
    "Уровень игроков в выборке не нормирован, поэтому цифры — ориентир для "
    "собственных выводов, а не гарантия результата.",
]

RECORD_RE = re.compile(
    r'\{"hero1":\{"name":"[^"]+","slug":"[^"]+"\},'
    r'"hero2":\{"name":"[^"]+","slug":"[^"]+"\},'
    r'"winRate":[^}]*?"slug":"[^"]+"\}'
)


def fetch_html() -> str:
    """HTML страницы: прямой запрос, при Cloudflare-заглушке — через прокси."""
    for url, headers in (
        (SOURCE_URL, {"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"}),
        (PROXY_URL, {"User-Agent": UA, "x-respond-with": "html"}),
    ):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=120) as resp:
                html = resp.read().decode("utf-8", "replace")
        except OSError as exc:
            print(f"[synergies] {url}: {exc}", file=sys.stderr)
            continue
        if '"hero1"' in html and '"winRate"' in html:
            print(f"[synergies] данные получены: {url}")
            return html
        print(f"[synergies] {url}: данные не найдены (Cloudflare?)", file=sys.stderr)
    raise SystemExit(
        "[synergies] не удалось получить страницу. Повторите позже или "
        "сохраните HTML и передайте его через --input."
    )


def parse_records(html: str) -> list[dict]:
    records: dict[str, dict] = {}
    for raw in RECORD_RE.findall(html):
        try:
            rec = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if rec.get("hero1") and rec.get("hero2") and rec.get("slug"):
            records[rec["slug"]] = rec
    if not records:
        raise SystemExit("[synergies] в HTML не найдено ни одной пары героев.")
    return list(records.values())


def tier_of(winrate: float) -> str:
    for tier in ("S", "A", "B", "C"):
        if winrate >= TIER_RULES[tier]:
            return tier
    return "C"


def load_hero_map() -> dict[str, dict]:
    heroes = json.loads(HEROES.read_text(encoding="utf-8"))
    norm = lambda value: re.sub(r"[^a-z0-9]", "", str(value).lower())
    return {norm(h.get("name_en") or h["name"]): h for h in heroes}


def build(html: str) -> dict:
    hero_map = load_hero_map()
    duos = []
    missing = set()

    for rec in parse_records(html):
        pair = []
        for side in ("hero1", "hero2"):
            src = rec[side]
            hero = hero_map.get(re.sub(r"[^a-z0-9]", "", src["name"].lower()))
            if hero is None:
                missing.add(src["name"])
                continue
            pair.append(
                {
                    "id": hero["id"],
                    "name": hero["name"],
                    "name_en": hero.get("name_en", ""),
                    "portrait": hero.get("portrait", ""),
                }
            )
        if len(pair) != 2:
            continue
        winrate = round(100 * float(rec["winRate"]), 1)
        duos.append(
            {
                "tier": tier_of(winrate),
                "hero1": pair[0],
                "hero2": pair[1],
                "winrate": winrate,
                "wrf": round(100 * float(rec["winRateFactor"]), 1),
                "pickrate": round(100 * float(rec["pickRate"]), 2),
                "cs_per_min": round(float(rec["avgLastHitPerMinute"]), 1),
                "denies": round(float(rec["avgDenies"]), 1),
                "nw_per_min": round(float(rec["avgNetWorthPerMinute"]), 1),
                "kda": round(float(rec["avgKda"]), 2),
            }
        )

    if missing:
        raise SystemExit(
            "[synergies] нет русских имён в data/heroes.json для: "
            + ", ".join(sorted(missing))
        )

    duos.sort(key=lambda d: (-d["winrate"], d["hero1"]["id"], d["hero2"]["id"]))
    for rank, duo in enumerate(duos, start=1):
        duo["rank"] = rank
    # Ключи в порядке чтения: rank первым, как в таблице сайта.
    duos = [{"rank": d.pop("rank"), **d} for d in duos]

    return {
        "version": 1,
        "source": SOURCE_URL,
        "collected_at": dt.date.today().isoformat(),
        "tier_rules": TIER_RULES,
        "methodology": METHODOLOGY,
        "duos": duos,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Собрать data/synergies.json")
    parser.add_argument("--input", type=pathlib.Path, help="готовый HTML страницы")
    args = parser.parse_args()

    html = args.input.read_text(encoding="utf-8", errors="replace") if args.input else fetch_html()
    payload = build(html)
    OUT.write_text(
        json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    tiers = {}
    for duo in payload["duos"]:
        tiers[duo["tier"]] = tiers.get(duo["tier"], 0) + 1
    print(
        f"[synergies] {len(payload['duos'])} дуо -> {OUT.relative_to(ROOT)} "
        f"(S={tiers.get('S', 0)}, A={tiers.get('A', 0)}, "
        f"B={tiers.get('B', 0)}, C={tiers.get('C', 0)})"
    )


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
