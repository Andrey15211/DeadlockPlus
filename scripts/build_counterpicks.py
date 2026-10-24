"""Контрпики: сборка датасета из deadlockitembuilder.com для DeadlockForge.

Источник: https://deadlockitembuilder.com (открытые JSON: /data/db.json, /data/duo.json).
Цель: data/counterpicks.json — русский контент для экрана «Контрпики».

Этапы:
    python scripts/build_counterpicks.py --extract    # сайт -> source_en.json + glossary.json
    python scripts/build_counterpicks.py --assemble   # ru/*.json -> counterpicks.json
    python scripts/build_counterpicks.py --report     # отчёт покрытия глоссария и чанков

Перевод идёт между --extract и --assemble: чанки лежат в
data/counterpicks/ru/<hero-id>.json, предметы — data/counterpicks/ru/_items.json.
Имена героев, предметов и способностей берутся из официальных русских данных
deadlock-api (тот же источник, что scripts/sync_valve_russian_data.py), а не
переводятся вручную — расхождений с клиентом быть не должно.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
OUT_DIR = DATA_DIR / "counterpicks"
RU_DIR = OUT_DIR / "ru"
SOURCE_JSON = OUT_DIR / "source_en.json"
GLOSSARY_JSON = OUT_DIR / "glossary.json"
RAW_JSON = OUT_DIR / "site_db.json"
TARGET_JSON = DATA_DIR / "counterpicks.json"

SITE_DATA_URL = "https://deadlockitembuilder.com/data/db.json"
API_ITEMS = "https://api.deadlock-api.com/v1/assets/items?language={lang}"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

# Названия предметов сайта, разошедшиеся с официальным английским именем.
ITEM_NAME_ALIASES = {
    "Silence Wave": "silence-glyph",
    "Tank Buster": "magic-shock",
}

# Ключ группы сайта -> тег угрозы проекта (models.ThreatTag), где связь однозначна.
GROUP_TAGS = {
    "anti_heal": "heal",
    "anti_cc": "cc_chain",
    "anti_mobility": "mobility",
    "gun_defense": "bullet_dps",
    "spirit_defense": "spirit_caster",
    "interrupt": "uninterruptible_ult",
    "fire_rate_control": "bullet_dps",
    "debuff_cleanse": "cc_chain",
    "lane_sustain": "heal",
}

# Порядок панели быстрого ответа на сайте фиксирован, перевод его сохраняет.
GLANCE_ORDER = ("main_threat", "your_opening", "buy_priority")

STAGES = ("early", "mid", "late")

# Консоль Windows по умолчанию cp1251 — русский вывод иначе падает с UnicodeEncodeError.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass


def log(msg: str) -> None:
    print(msg, flush=True)


def http_json(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def norm(text: str) -> str:
    """Нормализация имени для сопоставления: регистр, амперсанды, пунктуация."""
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower().replace("&", " and ")).strip()


def markdown_bold(text: str) -> str:
    """**жирный** -> <b>жирный</b>: в интерфейсе рендерим как разметку Deadlock."""
    return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)


# --- карты соответствия ----------------------------------------------------


def build_reference():
    """Герои/предметы проекта + официальные EN/RU имена предметов и способностей."""
    heroes = read_json(DATA_DIR / "heroes.json")
    items = read_json(DATA_DIR / "items.json")
    api_en = http_json(API_ITEMS.format(lang="english"))
    api_ru = http_json(API_ITEMS.format(lang="russian"))

    ru_by_class = {x["class_name"]: x.get("name") for x in api_ru if x.get("class_name")}
    abilities_en: dict[str, str] = {}
    for rec in api_en:
        cname = rec.get("class_name") or ""
        if not cname.startswith(("ability_", "citadel_ability_", "citadel_weapon_")):
            continue
        name_en = rec.get("name") or ""
        name_ru = ru_by_class.get(cname) or ""
        if name_en and name_ru and name_ru != name_en:
            abilities_en.setdefault(name_en, name_ru)

    hero_by_en = {norm(h.get("name_en") or ""): h for h in heroes}
    hero_by_ru = {norm(h.get("name") or ""): h for h in heroes}
    item_by_en = {norm(i.get("name_en") or ""): i for i in items}
    item_by_id = {i["id"]: i for i in items}
    return heroes, items, hero_by_en, hero_by_ru, item_by_en, item_by_id, abilities_en


def map_site_heroes(db: dict, hero_by_en: dict, hero_by_ru: dict) -> dict[str, str]:
    """site slug -> id героя проекта. Сопоставление по отпечатку top+groups."""
    helper = db["helper"]["heroes"]
    display_by_fingerprint: dict[tuple, str] = {}
    for name, payload in helper.items():
        key = (tuple(payload.get("top") or ()), tuple(payload.get("groups") or ()))
        display_by_fingerprint[key] = name

    mapping: dict[str, str] = {}
    unknown: list[str] = []
    for slug, payload in db["heroes"].items():
        key = (tuple(payload.get("top") or ()), tuple(payload.get("groups") or ()))
        display = display_by_fingerprint.get(key)
        hero = hero_by_en.get(norm(display or "")) or hero_by_ru.get(norm(display or ""))
        if hero is None:
            # Фолбэк: display-имя из заголовка гайда.
            title = payload.get("title") or ""
            guess = re.sub(r"\s*\|.*$", "", re.sub(r"^How to Counter\s+", "", title))
            guess = re.sub(r"\s+in Deadlock$", "", guess)
            hero = hero_by_en.get(norm(guess)) or hero_by_ru.get(norm(guess))
        if hero is None:
            unknown.append(slug)
            continue
        mapping[slug] = hero["id"]
    if unknown:
        raise ValueError(f"Не сопоставлены герои сайта: {unknown}")
    return mapping


def map_site_items(db: dict, item_by_en: dict, item_by_id: dict) -> dict[str, str]:
    """Название предмета на сайте -> id предмета проекта."""
    mapping: dict[str, str] = {}
    unknown: list[str] = []
    for name in db["helper"]["items"]:
        item = item_by_en.get(norm(name))
        if item is None and name in ITEM_NAME_ALIASES:
            item = item_by_id.get(ITEM_NAME_ALIASES[name])
        if item is None:
            unknown.append(name)
            continue
        mapping[name] = item["id"]
    if unknown:
        raise ValueError(
            f"Не сопоставлены предметы сайта: {unknown}. "
            "Добавьте соответствие в ITEM_NAME_ALIASES."
        )
    return mapping


# --- извлечение ------------------------------------------------------------


def split_ref(ref: str) -> tuple[str, int | None]:
    """'drifter#problem-4' -> ('drifter', 4)."""
    if "#problem-" in ref:
        slug, _, idx = ref.partition("#problem-")
        return slug, int(idx) if idx.isdigit() else None
    return ref, None


def extract() -> None:
    log("Загрузка db.json с deadlockitembuilder.com ...")
    db = http_json(SITE_DATA_URL)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    write_json(RAW_JSON, db)
    log(f"  сохранено {RAW_JSON.relative_to(ROOT)} ({RAW_JSON.stat().st_size // 1024} КБ)")

    log("Загрузка официальных русских данных deadlock-api ...")
    heroes, items, hero_by_en, hero_by_ru, item_by_en, item_by_id, abilities_en = build_reference()
    log(f"  героев проекта: {len(heroes)}, предметов: {len(items)}, способностей EN→RU: {len(abilities_en)}")

    slug_to_hero = map_site_heroes(db, hero_by_en, hero_by_ru)
    name_to_item = map_site_items(db, item_by_en, item_by_id)
    hero_by_id = {h["id"]: h for h in heroes}
    log(f"  сопоставлено героев: {len(slug_to_hero)}, предметов: {len(name_to_item)}")

    def hero_id(slug: str) -> str:
        try:
            return slug_to_hero[slug]
        except KeyError as exc:
            raise ValueError(f"Неизвестный герой сайта в ссылке: {slug!r}") from exc

    def item_id(name: str) -> str:
        """Имя предмета сайта -> id проекта. Новые имена из текста добираются по name_en."""
        if name in name_to_item:
            return name_to_item[name]
        item = item_by_en.get(norm(name)) or item_by_id.get(ITEM_NAME_ALIASES.get(name, ""))
        if item is None:
            unknown_items.add(name)
            return name
        name_to_item[name] = item["id"]
        return item["id"]

    labels: dict[str, str] = {}
    ability_hits: dict[str, int] = {}
    unknown_items: set[str] = set()

    def scan(text: str) -> str:
        """Регистрирует метки для перевода и находит упоминания способностей."""
        if not text:
            return text
        for name in abilities_en:
            if len(name) > 3 and re.search(rf"\b{re.escape(name)}\b", text):
                ability_hits[name] = ability_hits.get(name, 0) + 1
        return text

    js = lambda v: json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else v

    counter_heroes: dict[str, dict] = {}
    for slug, src in db["heroes"].items():
        proj = hero_by_id[hero_id(slug)]
        helper = db["helper"]["heroes"].get(proj["name_en"]) or db["helper"]["heroes"].get(proj["name"])

        stages: dict[str, list] = {}
        for stage in STAGES:
            rows = []
            for row in src.get("stages", {}).get(stage, []):
                name, label = row[0], row[1]
                alt = bool(row[2]) if len(row) > 2 else False
                scan(label)
                rows.append({"item": item_id(name), "label": label, "alt": alt})
            stages[stage] = rows

        problems = []
        for row in src.get("problems", []):
            text, name, tag_list = row[0], row[1], row[2]
            when, how = row[3], row[4]
            label = tag_list[0] if tag_list else ""
            for chunk in (text, label, when, how):
                scan(chunk)
            problems.append(
                {
                    "problem": text,
                    "item": item_id(name),
                    "label": label,
                    "when": when,
                    "how": how,
                }
            )

        glance = [[label, scan(text)] for label, text in src.get("glance", [])]
        for label, _ in glance:
            labels[label] = label

        threats = []
        for label, key_ in src.get("threats", []):
            labels[label] = label
            threats.append({"label": label, "key": key_})

        faq = [[scan(q), scan(a)] for q, a in src.get("faq", [])]
        lane = [scan(js(x)) if not isinstance(x, str) else scan(x) for x in src.get("lane", [])]
        fight = [scan(x) for x in src.get("fight", [])]
        if src.get("note"):
            scan(src["note"])

        why = {}
        for name, text in (src.get("why") or {}).items():
            why[item_id(name)] = scan(text)

        stage_of = {item_id(k): v for k, v in (src.get("stageOf") or {}).items()}
        roles = [r for r in src.get("roles", [])]
        for r in roles:
            labels[r] = r
        role_text = [scan(x) for x in src.get("roleText", [])]

        helper_extra = {}
        if helper:
            # helper.ib с сайта — тот же список проблем, сгруппированный по
            # предметам (194 из 359 строк совпадают с sits дословно), поэтому
            # в датасет он не идёт: интерфейс берёт sits.
            helper_extra = {
                "summary": scan(helper.get("summary", "")),
                "sits": [
                    {"problem": scan(r[0]), "item": item_id(r[1]), "how": scan(r[2])}
                    for r in (helper.get("sits") or [])
                ],
                "tip": scan(helper.get("extra", "")),
            }

        counter_heroes[proj["id"]] = {
            "id": proj["id"],
            "name": proj["name"],
            "name_en": proj["name_en"],
            "portrait": proj.get("portrait", ""),
            "intro": scan(src.get("sub", "")),
            "roles": roles,
            "role_text": role_text,
            "threats": threats,
            "groups": src.get("groups", []),
            "group_tags": sorted({GROUP_TAGS[g] for g in src.get("groups", []) if g in GROUP_TAGS}),
            "glance": glance,
            "note": src.get("note", ""),
            "top": [item_id(n) for n in src.get("top", [])],
            "stages": stages,
            "problems": problems,
            "why": why,
            "stage_of": stage_of,
            "lane": lane,
            "fight": fight,
            "faq": faq,
            "related": [hero_id(r) for r in src.get("related", []) if r in slug_to_hero],
            "related_guides": [r for r in src.get("related", []) if r not in slug_to_hero],
            "helper": helper_extra,
        }

    counter_items: dict[str, dict] = {}
    for slug, src in db["items"].items():
        proj = item_by_id.get(slug)
        if proj is None:
            item = item_by_en.get(norm(src.get("name", ""))) or item_by_id.get(ITEM_NAME_ALIASES.get(src.get("name", ""), ""))
            if item is None:
                raise ValueError(f"Неизвестный предмет сайта: {slug!r} ({src.get('name')!r})")
            proj = item

        best = []
        for row in src.get("best", []):
            ref, note = row[0], row[1]
            methods = row[2] if len(row) > 2 else []
            target, problem_idx = split_ref(ref)
            entry = {
                "hero": hero_id(target),
                "problem_index": problem_idx,
                "note": scan(note),
                "methods": [scan(m) for m in methods],
            }
            best.append(entry)

        all_rows = []
        for row in src.get("all", []):
            all_rows.append({"hero": hero_id(row[0]), "label": row[1]})
            labels[row[1]] = row[1]

        alts = []
        for row in src.get("alts", []):
            name, text = row[0], row[1]
            alts.append({"item": item_id(name), "text": scan(text)})

        timing = [[scan(t[0]), scan(t[1])] for t in src.get("timing", [])]
        avg = src.get("avg") or ""
        # "**Average purchase: 28 minutes**" -> 28.0
        avg_match = re.search(r"([0-9]+(?:\.[0-9]+)?)", avg)
        avg_minutes = float(avg_match.group(1)) if avg_match else None

        for name in src.get("roles", []) + src.get("stages", []):
            labels[name] = name

        counter_items[proj["id"]] = {
            "id": proj["id"],
            "name": proj["name"],
            "name_en": proj["name_en"],
            "category": proj.get("category", ""),
            "tier": proj.get("tier"),
            "cost": proj.get("cost"),
            "image": f"assets/items/{proj['id']}.webp",
            "roles": src.get("roles", []),
            "stages": src.get("stages", []),
            "short": scan(src.get("short", "")),
            "intro": scan(src.get("bestIntro", "")),
            "who": scan(src.get("who", "")),
            "when": scan(src.get("when", "")),
            "example": scan(src.get("example", "")),
            "watch": scan(src.get("watch", "")),
            "buy": scan(src.get("buy", "")),
            "why": [scan(x) for x in src.get("why", [])],
            "timing": timing,
            "avg_minutes": avg_minutes,
            "best": best,
            "all": all_rows,
            "alts": alts,
        }

    # Герои, которых нет на сайте-источнике (свежий релиз), всё равно нужны
    # в датасете: иначе их нельзя выбрать противником в разборе состава.
    # Карточка без гайда — интерфейс сам сообщает, что разбор не собран.
    for hero in heroes:
        if hero["id"] in counter_heroes:
            continue
        counter_heroes[hero["id"]] = {
            "id": hero["id"],
            "name": hero["name"],
            "name_en": hero.get("name_en", ""),
            "portrait": hero.get("portrait", ""),
            "intro": "",
            "roles": [],
            "role_text": [],
            "threats": [],
            "groups": [],
            "group_tags": [],
            "glance": {},
            "note": "",
            "top": [],
            "stages": {stage: [] for stage in STAGES},
            "problems": [],
            "why": {},
            "lane": [],
            "fight": [],
            "faq": [],
            "related": [],
            "helper": {"summary": "", "tip": "", "sits": []},
        }
        log(f"  без гайда на источнике: {hero['id']} ({hero.get('name_en', '')})")

    groups = {
        key: {"name": val["name"], "items": [item_id(i) for i in val.get("items", [])]}
        for key, val in db["groups"].items()
    }

    source = {
        "version": 1,
        "generated_from": SITE_DATA_URL,
        "extracted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "stages": list(STAGES),
        "groups": groups,
        "heroes": counter_heroes,
        "items": counter_items,
        "label_vocabulary": sorted(labels),
    }
    write_json(SOURCE_JSON, source)
    write_json(
        GLOSSARY_JSON,
        {
            "heroes": {v["name_en"]: v["name"] for v in counter_heroes.values()},
            "items": {v["name_en"]: v["name"] for v in counter_items.values()},
            "abilities": {k: abilities_en[k] for k in sorted(ability_hits, key=lambda k: -ability_hits[k])},
            "labels": sorted(labels),
            "groups": {k: v["name"] for k, v in groups.items()},
        },
    )

    heroes_words = sum(len(json.dumps(v, ensure_ascii=False).split()) for v in counter_heroes.values())
    items_words = sum(len(json.dumps(v, ensure_ascii=False).split()) for v in counter_items.values())
    if unknown_items:
        raise ValueError(
            f"Имена предметов без соответствия в data/items.json: {sorted(unknown_items)}. "
            "Добавьте их в ITEM_NAME_ALIASES."
        )
    log(f"Готово: {SOURCE_JSON.relative_to(ROOT)}")
    log(f"  героев: {len(counter_heroes)} (~{heroes_words} сл. JSON), предметов: {len(counter_items)} (~{items_words} сл. JSON)")
    log(f"  способностей найдено в текстах: {len(ability_hits)} из {len(abilities_en)} в API")
    top_abilities = sorted(ability_hits.items(), key=lambda kv: -kv[1])[:15]
    log("  самые частые: " + ", ".join(f"{k}×{v}" for k, v in top_abilities))


# --- сборка ----------------------------------------------------------------


def _walk_strings(payload):
    if isinstance(payload, str):
        yield payload
    elif isinstance(payload, list):
        for x in payload:
            yield from _walk_strings(x)
    elif isinstance(payload, dict):
        for v in payload.values():
            yield from _walk_strings(v)


def _hero_payload(src: dict) -> dict:
    """Английская структура героя -> форма CounterHero.

    Ярлыки панели быстрого ответа в русском чанке уже переведены, поэтому
    пара «ярлык -> ключ» берётся не из текста, а по порядку следования:
    на сайте порядок фиксирован (главная угроза, окно, приоритет покупки),
    и перевод его сохраняет.
    """
    glance: dict[str, str] = {}
    for index, (_label, text) in enumerate(src.get("glance") or []):
        key = GLANCE_ORDER[index] if index < len(GLANCE_ORDER) else f"extra_{index}"
        glance[key] = text
    return {
        "id": src["id"],
        "name": src["name"],
        "name_en": src["name_en"],
        "portrait": src["portrait"],
        "intro": src["intro"],
        "roles": src["roles"],
        "role_text": src["role_text"],
        "threats": src["threats"],
        "groups": src["groups"],
        "group_tags": src["group_tags"],
        "glance": glance,
        "note": src["note"],
        "top": src["top"],
        "stages": {
            stage: [
                {"item_id": row["item"], "label": row["label"], "alt": row["alt"]}
                for row in src["stages"].get(stage, [])
            ]
            for stage in STAGES
        },
        "problems": [
            {
                "problem": p["problem"],
                "item_id": p["item"],
                "label": p["label"],
                "when": p["when"],
                "how": p["how"],
            }
            for p in src["problems"]
        ],
        "why": src["why"],
        "lane": src["lane"],
        "fight": src["fight"],
        "faq": src["faq"],
        "related": src["related"],
        "helper": {
            "summary": src["helper"].get("summary", ""),
            "tip": src["helper"].get("tip", ""),
            "sits": [
                {"problem": s["problem"], "item_id": s["item"], "how": s["how"]}
                for s in src["helper"].get("sits") or []
            ],
        },
    }


def _item_payload(src: dict) -> dict:
    """Английская структура предмета -> форма CounterItemGuide."""
    return {
        "id": src["id"],
        "name": src["name"],
        "name_en": src["name_en"],
        "category": src["category"],
        "tier": src["tier"],
        "cost": src["cost"],
        "image": src["image"],
        "roles": src["roles"],
        "stages": src["stages"],
        "short": src["short"],
        "intro": src["intro"],
        "who": src["who"],
        "when": src["when"],
        "example": src["example"],
        "watch": src["watch"],
        "buy": src["buy"],
        "why": src["why"],
        "timing": src["timing"],
        "avg_minutes": src["avg_minutes"],
        "best": [
            {
                "hero_id": row["hero"],
                "problem_index": row["problem_index"],
                "note": row["note"],
                "methods": row["methods"],
            }
            for row in src["best"]
        ],
        "all": [{"hero_id": row["hero"], "label": row["label"]} for row in src["all"]],
        "alts": [{"item_id": row["item"], "text": row["text"]} for row in src["alts"]],
    }


def assemble() -> None:
    source = read_json(SOURCE_JSON)

    ru_heroes: dict[str, dict] = {}
    missing: list[str] = []
    for hero_id in source["heroes"]:
        path = RU_DIR / f"{hero_id}.json"
        if not path.is_file():
            missing.append(hero_id)
            continue
        ru_heroes[hero_id] = read_json(path)
    if missing:
        raise FileNotFoundError(
            f"Нет переведённых чанков ({len(missing)}): {', '.join(missing)}. "
            f"Ожидаются в {RU_DIR.relative_to(ROOT)}."
        )

    items_path = RU_DIR / "_items.json"
    if not items_path.is_file():
        raise FileNotFoundError(f"Нет переведённого файла предметов: {items_path}")
    ru_items = read_json(items_path)

    # Полнота: чанк обязан покрывать все поля источника.
    for hero_id, chunk in ru_heroes.items():
        absent = set(source["heroes"][hero_id]) - set(chunk)
        if absent:
            raise ValueError(f"Чанк {hero_id!r} не покрывает поля: {sorted(absent)}")
    absent_items = set(source["items"]) - set(ru_items)
    if absent_items:
        raise ValueError(f"Файл предметов не покрывает: {sorted(absent_items)}")

    heroes = [_hero_payload(ru_heroes[h]) for h in source["heroes"]]
    items = [_item_payload(ru_items[i]) for i in source["items"]]
    groups_path = RU_DIR / "_groups.json"
    ru_groups = read_json(groups_path) if groups_path.is_file() else {}
    groups = [
        {"key": key, "name": ru_groups.get(key) or value["name"], "items": value["items"]}
        for key, value in source["groups"].items()
    ]

    payload = {
        "version": 1,
        "source": source["generated_from"],
        "extracted_at": source["extracted_at"],
        "stages": source["stages"],
        "groups": groups,
        "heroes": heroes,
        "items": items,
    }
    write_json(TARGET_JSON, payload)
    log(f"Собрано: {TARGET_JSON.relative_to(ROOT)}")
    log(f"  героев {len(heroes)}, предметов {len(items)}, "
        f"размер {TARGET_JSON.stat().st_size // 1024} КБ")


# --- отчёт -----------------------------------------------------------------


def report() -> None:
    source = read_json(SOURCE_JSON)
    glossary = read_json(GLOSSARY_JSON)
    ru_done = sorted(p.stem for p in RU_DIR.glob("*.json")) if RU_DIR.is_dir() else []

    def words(o) -> int:
        return sum(len(s.split()) for s in _walk_strings(o))

    log(f"Источник: героев {len(source['heroes'])}, предметов {len(source['items'])}")
    log(f"Слов в английском источнике: герои {words(source['heroes'])}, предметы {words(source['items'])}")
    log(f"Глоссарий: героев {len(glossary['heroes'])}, предметов {len(glossary['items'])}, способностей {len(glossary['abilities'])}, меток {len(glossary['labels'])}")
    log(f"Переведено чанков: {len(ru_done)} из {len(source['heroes']) + 1}")
    for hero_id, chunk in source["heroes"].items():
        mark = "готов" if hero_id in ru_done else "  —  "
        log(f"  {mark} {hero_id:16s} {words(chunk):6d} сл.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--extract", action="store_true", help="собрать source_en.json и glossary.json с сайта")
    parser.add_argument("--assemble", action="store_true", help="собрать data/counterpicks.json из ru/*.json")
    parser.add_argument("--report", action="store_true", help="отчёт по покрытию")
    args = parser.parse_args()
    if not (args.extract or args.assemble or args.report):
        parser.print_help()
        return 2
    if args.extract:
        extract()
    if args.assemble:
        assemble()
    if args.report:
        report()
    return 0


if __name__ == "__main__":
    sys.exit(main())