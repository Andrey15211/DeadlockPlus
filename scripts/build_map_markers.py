#!/usr/bin/env python
"""build_map_markers.py — маркеры карты из авторитетных данных deadlock-api.

Зачем: прежние маркеры в data/map.json были вписаны вручную «на глаз», и
4 из 14 («шагоходы») не попадали в игровую зону; урна и мидбосс стояли в
центре карты, где на самом деле находится символ Патрона.

Источник истины — GET https://api.deadlock-api.com/v1/assets/map:
    radius            — 10752 (полуразмер мира);
    neutral_camps[]   — 51 лагерь с position и left/top_relative;
    objective_positions — 16 башен/ядер/титанов;
    entities.soul_urn_spawns / soul_urn_pads — точки урны.
Формула привязки подтверждена на всех объектах: left = x/(2R)+0.5,
top = 0.5 - y/(2R) (см. match_loader._norm_coord, тест-якоря).

Координаты НЕ выдумываются: слои camps/urn/towers берутся 1:1 из API.
Мидбосс помечается отдельно и включается только при --with-midboss,
потому что его позиции в API нет (есть лишь spawner
neutral_camp_midboss без координат) — по умолчанию маркер не ставится,
чтобы не показывать выдуманную точку.

Использование:
    python scripts/build_map_markers.py [--dry-run] [--with-midboss] [--data-dir path]
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

MAP_API = "https://api.deadlock-api.com/v1/assets/map"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) DeadlockForge/1.2"
HTTP_TIMEOUT = 45

# Линии: карта делится по X, районам Йорк / Бродвей / Гринвич соответствуют
# кластеры лагерей из API (проверено по именам: slums/plaza/dock -> Йорк и
# Бродвей, theater/chinatown/park -> Гринвич).
LANE_SPLIT_YORK = 0.34
LANE_SPLIT_GREENWICH = 0.62

# Локализация имён лагерей: держим узнаваемые названия районов, иначе
# собираем читаемую подпись из слага.
NAME_RU = {
    "dock": "Док",
    "dock_authority": "Портовая администрация",
    "slums": "Трущобы",
    "plaza": "Плаза",
    "hotel": "Отель",
    "dept_store": "Универмаг",
    "oracle": "Оракул",
    "drug_store": "Аптека",
    "chinatown": "Чайна-таун",
    "theater": "Театр",
    "park": "Парк",
    "nursery": "Питомник",
    "bank": "Банк",
    "construction": "Стройка",
    "hq": "Штаб",
    "basketball": "Баскетбол",
    "asylum": "Лечебница",
    "garage": "Гараж",
}

KIND_RU = {
    "weak": "слабый",
    "medium": "средний",
    "strong": "сильный",
    "vault": "хранилище",
}


def fetch_map_assets() -> dict:
    req = urllib.request.Request(MAP_API, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def lane_of(left: float) -> str:
    if left < LANE_SPLIT_YORK:
        return "york"
    if left < LANE_SPLIT_GREENWICH:
        return "broadway"
    return "greenwich"


def humanize_camp(name: str, kind: str = "") -> str:
    """chinatown_bell_1 -> «Чайна-таун, колокол 1 (хранилище)»; dock_camp -> «Док».

    Переводим только те токены, для которых есть словарь; незнакомые
    оставляем как есть — подпись никогда не бывает выдуманной.
    """
    tokens = name.split("_")
    district = next((NAME_RU[t] for t in tokens if t in NAME_RU), None)
    detail_tokens = [
        t for t in tokens
        if t not in NAME_RU and t not in _GENERIC_TOKENS
    ]
    detail = " ".join(DETAIL_RU.get(t, t) for t in detail_tokens)
    if district and detail:
        label = f"{district}, {detail}"
    elif district:
        label = district
    else:
        # Район незнаком: показываем исходное имя, чтобы не соврать.
        label = name.replace("_", " ")
    kind_ru = KIND_RU.get(kind)
    # Тип не дублируем, если он уже прозвучал в описании места.
    if kind_ru and kind_ru not in label.lower():
        label = f"{label} ({kind_ru})"
    return label


# Токены, не несущие смысла для подписи.
_GENERIC_TOKENS = {
    "camp", "camps", "neutrals", "nw", "ne", "sw", "se",
}

DETAIL_RU = {
    "bell": "колокол",
    "roof": "крыша",
    "alley": "переулок",
    "street": "улица",
    "downstairs": "нижний этаж",
    "upstairs": "верхний этаж",
    "stage": "сцена",
    "lobby": "лобби",
    "balcony": "балкон",
    "library": "библиотека",
    "chapel": "часовня",
    "gate": "ворота",
    "mansion": "особняк",
    "club": "клуб",
    "fish": "рыбный",
    "corner": "угол",
    "rink": "каток",
    "pit": "яма",
    "storage": "склад",
    "warehouse": "пакгауз",
    "garage": "гараж",
    "vault": "хранилище",
    "authority": "администрация",
    "bodega": "лабаз",
    "authority": "администрация",
    "radio": "радиовышка",
    "bank": "банк",
    "hq": "штаб",
    "railway": "железная дорога",
    "down": "нижняя",
    "up": "верхняя",
}


def build_markers(payload: dict, *, with_midboss: bool) -> list[dict]:
    """Маркеры строго из API: лагеря, урна, башни (+ мидбосс по флагу)."""
    camps = payload.get("neutral_camps") or []
    objectives = payload.get("objective_positions") or {}
    entities = payload.get("entities") or {}
    urn_spawns = entities.get("soul_urn_spawns") or []

    markers: list[dict] = []

    for camp in camps:
        left, top = camp.get("left_relative"), camp.get("top_relative")
        if left is None or top is None:
            continue
        markers.append({
            "layer": "camps",
            "lane": lane_of(float(left)),
            "x": round(float(left), 4),
            "y": round(float(top), 4),
            "label": humanize_camp(str(camp.get("name", "лагерь")), str(camp.get("kind", ""))),
        })

    # Башни и ядра: из objective_positions (16 объектов).
    for key, obj in objectives.items():
        left, top = obj.get("left_relative"), obj.get("top_relative")
        if left is None or top is None:
            continue
        markers.append({
            "layer": "towers",
            "lane": lane_of(float(left)),
            "x": round(float(left), 4),
            "y": round(float(top), 4),
            "label": humanize_objective(key),
        })

    # Точки спавна урны душ.
    for i, spawn in enumerate(urn_spawns, start=1):
        left, top = spawn.get("left_relative"), spawn.get("top_relative")
        if left is None or top is None:
            continue
        markers.append({
            "layer": "urn",
            "lane": lane_of(float(left)),
            "x": round(float(left), 4),
            "y": round(float(top), 4),
            "label": f"Урна душ: точка {i}",
        })

    if with_midboss:
        # Позиции мидбосса в API нет — ставим только по явному запросу
        # и помечаем как приблизительную (центр карты, где он и живёт по гайду).
        markers.append({
            "layer": "midboss",
            "lane": "broadway",
            "x": 0.5,
            "y": 0.5,
            "label": "Мидбосс (позиция ориентировочная: в API координат нет)",
        })

    markers.sort(key=lambda m: (m["layer"], m["x"], m["y"]))
    return markers


OBJECTIVE_RU = {
    "core": "Древний (ядро)",
    "titan": "Титан",
    "tier1": "Башня T1",
    "tier2": "Башня T2",
}


def humanize_objective(key: str) -> str:
    """team0_tier2_1 -> «Тревога (команда 0): Башня T2 №1»."""
    parts = key.split("_")
    team = parts[0].replace("team", "Команда ") if parts and parts[0].startswith("team") else ""
    rest = parts[1:] if team else parts
    kind = OBJECTIVE_RU.get(rest[0], rest[0]) if rest else key
    num = rest[-1] if len(rest) > 1 and rest[-1].isdigit() else ""
    label = f"{kind} {num}".strip()
    return f"{label} ({team.strip()})" if team else label


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="показать результат, не записывая файл")
    ap.add_argument("--with-midboss", action="store_true",
                    help="добавить маркер мидбосса с ориентировочной позицией")
    ap.add_argument("--data-dir", default=None, help="каталог data/ (по умолчанию корень проекта)")
    args = ap.parse_args()

    root = Path(__file__).resolve().parents[1]
    data_dir = Path(args.data_dir) if args.data_dir else root / "data"
    map_file = data_dir / "map.json"

    try:
        payload = fetch_map_assets()
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        print(f"[markers] не удалось получить {MAP_API}: {exc}", file=sys.stderr)
        print("[markers] файл не изменён", file=sys.stderr)
        return 1

    markers = build_markers(payload, with_midboss=args.with_midboss)
    by_layer: dict[str, int] = {}
    for m in markers:
        by_layer[m["layer"]] = by_layer.get(m["layer"], 0) + 1
    print(f"[markers] собрано {len(markers)}: " + ", ".join(f"{k}={v}" for k, v in sorted(by_layer.items())))

    if args.dry_run:
        for m in markers[:10]:
            print("   ", m)
        print("   ... (dry-run: файл не записан)")
        return 0

    guide = json.loads(map_file.read_text(encoding="utf-8"))
    guide["markers"] = markers
    # Примечание о проекции переписываем: прежний текст описывал формулу
    # БЕЗ инверсии оси Y, из-за чего точки уезжали за пределы карты.
    guide["coordinate_system"] = {
        "projection": "world_norm_0_1",
        "world_radius": payload.get("radius", 10752),
        "note": "x,y в 0..1 от левого-верхнего угла миникарты; мировые "
                "координаты конвертируются как left = x/(2*radius)+0.5, "
                "top = 0.5 - y/(2*radius) — та же проекция, что "
                "match_loader._norm_coord для точек смертей Deep Match.",
    }
    guide["markers_source"] = {
        "endpoint": MAP_API,
        "radius": payload.get("radius"),
        "formula": "left = x/(2*radius)+0.5; top = 0.5 - y/(2*radius)",
        "note": "Маркеры собраны из neutral_camps, objective_positions и "
                "entities.soul_urn_spawns; ручные координаты удалены.",
    }
    if args.with_midboss:
        guide["markers_source"]["midboss_note"] = (
            "Позиция мидбосса в API отсутствует: маркер ориентировочный."
        )
    map_file.write_text(
        json.dumps(guide, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"[markers] записано в {map_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())