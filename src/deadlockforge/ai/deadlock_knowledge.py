"""Тактическая база знаний Deadlock для AI-тренера.

Содержит ключевые тайминги карты The Cursed Apple, паттерны позиционирования,
каталог контр-предметов по сигнатурам героев, нормативы темпа фарма и
структурированный prompt-пак для системного промпта.
"""

from __future__ import annotations

from typing import Any

# --- 1. Тайминги карты The Cursed Apple ---
MAP_TIMINGS: dict[str, str] = {
    "0:00": "Старт матча: расстановка 2-2-2 (или соло на дальних линиях). Борьба за ластхиты и денай душ.",
    "3:00": "Спавн первых лесных лагерей (T1 Neutral Camps). Обязательно зачищать лесные кемпы между пачками крипов.",
    "10:00": "Спавн Урны душ (Soul Urn) в центре карты + открытие Flex-слотов после сноса первых Шагоходов (Walkers). Доставка Урны даёт +25% бонусных душ всей команде.",
    "12:00": "Активация фазы Мидбосса (Midboss). Возможность раннего забора при численном перевесе или сносе вражеского Шагохода (Walker).",
    "15:00": "Пик фазы Мидбосса (Midboss). Контроль ямы босса, обязательный обзор вокруг. Смерть в соло на чужой половине строго наказуема.",
    "18:00+": "Поздний Мидбосс / Омолодитель (Rejuvenator). Бафф на супер-крипов, ускоренное возрождение тиммейтов и финальный пуш базы.",
}

# --- 2. Критические ошибки позиционирования и макро ---
FATAL_MISTAKES_CATALOG: dict[str, dict[str, str]] = {
    "solo_enemy_half_post_15": {
        "title": "Смерть в соло на чужой половине после 15:00",
        "why_fatal": "Дарит вражеской команде бесплатный численный перевес 5v6, потерю Шагохода или бесплатного Мидбосса.",
        "correct_play": "Не пушить соло за реку без обзора всех 6 врагов на миникарте. Фармить треугольник своего леса и пушить только с союзниками.",
    },
    "late_death_long_respawn_post_25": {
        "title": "Смерть в лейтгейме (таймер респавна 60+ секунд) после 25:00",
        "why_fatal": "В Deadlock НЕТ выкупа/байбека! Таймер возрождения 50-70 секунд в лейтгейме оставляет команду 5v6 без шанса вернуться в драку, что позволяет врагу забрать Покровителя за один заход.",
        "correct_play": "После 25-й минуты играть строго в группе с командой и контролем Омолодителя (Rejuvenator). В Deadlock нет выкупа — любая смерть соло оставляет команду в меньшинстве на минуту и стоит игры.",
    },
    "urn_delivery_ignored": {
        "title": "Игнорирование доставки Урны душ",
        "why_fatal": "Упущенный командный темп на 3000-5000 душ и отставание по открытию дополнительных слотов.",
        "correct_play": "Каждые 5 минут начиная с 10:00 смещаться к точке спавна Урны, обеспечивать прикрытие курьеру или доставлять самому.",
    },
    "fight_under_numbers_deficit": {
        "title": "Драка в меньшинстве при отставании по душам",
        "why_fatal": "Приём файта 3v5 или 4v5 без ключевых ультов гарантирует командный вайп и потерю базы.",
        "correct_play": "Сбросить агрессию, удерживать узкие проходы под своими турелями/Покровителем (Patron), дожидаться возрождения тиммейтов и отката ультимейтов.",
    },
    "overextend_no_vision_midboss": {
        "title": "Глубокий пуш без обзора при живом Мидбоссе у врага",
        "why_fatal": "Враг забирает Мидбосса (Midboss), ловит игрока с тыла и реализует Омолодитель (Rejuvenator) для разрушения всех внешних линий.",
        "correct_play": "Контролировать яму Мидбосса, проверять проходы и пушить только при оттянутых линиях противника.",
    },
}

# --- 3. Ключевые контр-предметы ---
COUNTER_ITEMS_KNOWLEDGE: dict[str, dict[str, Any]] = {
    "heal_sustain": {
        "targets": ["abrams", "kelvin", "infernus", "ivy", "абрамс", "кельвин", "инфернус", "айви"],
        "description": "Против тяжелого лечения, вампиризма и сустейна",
        "primary_counters": [
            {
                "item": "Healbane",
                "name_ru": "Гроза целителей",
                "tier": 3,
                "effect": "Попадания накладывают запрет лечения (-60%), подавляя пассивный отхил и вампиризм.",
            },
            {
                "item": "Toxic Bullets",
                "name_ru": "Токсичные пули",
                "tier": 2,
                "effect": "Отравляет пулями и режет входящее лечение на 35%. Доступный ранний анти-хил.",
            },
        ],
    },
    "bullet_dps": {
        "targets": ["haze", "wraith", "ivy", "yamato", "хейз", "тень", "айви", "ямато"],
        "description": "Против плотного пулевого урона и скорострельных керри",
        "primary_counters": [
            {
                "item": "Metal Skin",
                "name_ru": "Металлическая кожа",
                "tier": 3,
                "effect": "Полная невосприимчивость к пулевому урону на время действия активной способности (против Хейз и Тени).",
            },
            {
                "item": "Return Fire",
                "name_ru": "Обратный огонь",
                "tier": 3,
                "effect": "Отражает 50% входящего пулевого урона обратно в стрелка, наказывая фокус Хейз и Тени.",
            },
        ],
    },
    "casters_uninterruptible_ult": {
        "targets": ["seven", "mcginnis", "dynamo", "warden", "vindicta", "семь", "макгиннис", "динамо", "дозорный", "виндикта"],
        "description": "Против сильных кастеров и непрерываемых ультимейтов",
        "primary_counters": [
            {
                "item": "Knockdown",
                "name_ru": "Нокдаун",
                "tier": 3,
                "effect": "Сбивает с ног и оглушает, прерывая Грозовое облако Севена, турельные заграждения Макгиннис и полёт Виндикты.",
            },
            {
                "item": "Curse",
                "name_ru": "Проклятие",
                "tier": 4,
                "effect": "Запрещает применение способностей, сайленсит и срезает урон сквозь любые защитные баффы.",
            },
        ],
    },
    "tank_frontline": {
        "targets": ["abrams", "mo-and-krill", "warden", "viscous", "абрамс", "мо и мелкий", "дозорный", "вязкус"],
        "description": "Против плотного бронированного фронтлайна",
        "primary_counters": [
            {
                "item": "Colossus",
                "name_ru": "Колосс",
                "tier": 4,
                "effect": "Даёт гигантский запас здоровья, замедляет врагов вокруг и снижает их урон.",
            },
            {
                "item": "Bullet Resist Shredder",
                "name_ru": "Измельчитель пулестойкости",
                "tier": 3,
                "effect": "Срезает пулевую броню бронированных танков при нанесении спирит-урона.",
            },
        ],
    },
}

# --- 4. Эталоны темпа фарма на 10-й минуте (Souls at 10:00) ---
FARM_BENCHMARKS_ROLE: dict[str, dict[str, Any]] = {
    "carry": {
        "benchmark_10m": 5000,
        "description": "Керри (Haze, Wraith, Infernus, Seven, Grey Talon)",
        "grade_s": 5500,
        "grade_a": 4800,
        "grade_b": 4000,
        "grade_c": 3200,
    },
    "duelist_initiator": {
        "benchmark_10m": 4200,
        "description": "Дуэлянт/Инициатор (Abrams, Shiv, Lash, Yamato, Mo & Krill)",
        "grade_s": 4600,
        "grade_a": 4100,
        "grade_b": 3500,
        "grade_c": 2800,
    },
    "support_controller": {
        "benchmark_10m": 3500,
        "description": "Саппорт/Контроллер (Kelvin, Ivy, Dynamo, McGinnis, Viscous)",
        "grade_s": 4000,
        "grade_a": 3400,
        "grade_b": 2900,
        "grade_c": 2300,
    },
}


def get_hero_category(hero_slug: str | None) -> str:
    """Определить макро-роль героя для норматива темпа."""
    if not hero_slug:
        return "carry"
    slug = hero_slug.lower()
    if slug in {"haze", "wraith", "infernus", "seven", "grey-talon", "vindicta"}:
        return "carry"
    if slug in {"kelvin", "ivy", "dynamo", "mcginnis", "viscous", "paradox"}:
        return "support_controller"
    return "duelist_initiator"


def evaluate_souls_pace(souls_at_10: float | int, hero_slug: str | None) -> dict[str, Any]:
    """Сравнить нетворк игрока на 10-й минуте с эталоном роли."""
    cat = get_hero_category(hero_slug)
    spec = FARM_BENCHMARKS_ROLE[cat]
    target = spec["benchmark_10m"]
    diff = int(souls_at_10) - target
    pct = round((souls_at_10 / target) * 100, 1) if target else 100.0
    status = "отличный" if diff >= 300 else ("норма" if diff >= -300 else "отставание")
    return {
        "role_category": cat,
        "role_description": spec["description"],
        "target_souls": target,
        "actual_souls": int(souls_at_10),
        "diff_souls": diff,
        "efficiency_pct": pct,
        "status": status,
    }


def get_counter_recommendations_for_enemies(enemy_heroes: list[str]) -> list[dict[str, str]]:
    """Подобрать критические контр-предметы под сетап врагов."""
    enemies = {e.lower() for e in enemy_heroes if e}
    recs: list[dict[str, str]] = []
    for cat_key, info in COUNTER_ITEMS_KNOWLEDGE.items():
        matched = enemies.intersection(set(info["targets"]))
        if matched:
            for item_info in info["primary_counters"]:
                recs.append({
                    "item": item_info["name_ru"],
                    "item_en": item_info["item"],
                    "name_ru": item_info["name_ru"],
                    "threat_target": ", ".join(sorted(matched)),
                    "reason": f"{item_info['name_ru']}: {item_info['effect']} (контрит {', '.join(sorted(matched))})",
                })
    return recs


def get_tactical_knowledge_prompt() -> str:
    """Формирует компактный, емкий текстовый блок тактической базы знаний

    для включения в системный промпт тренера.
    """
    timings_block = "\n".join(f"- **{t}**: {desc}" for t, desc in MAP_TIMINGS.items())

    mistakes_block = "\n".join(
        f"- **{info['title']}**: {info['why_fatal']} Верная игра: {info['correct_play']}"
        for info in FATAL_MISTAKES_CATALOG.values()
    )

    counters_block = """\
- Против хила и сустейна (Кельвин, Абрамс, Инфернус, Айви / Kelvin, Abrams, Infernus, Ivy) -> Гроза целителей (Healbane, -60% лечения) / Токсичные пули (Toxic Bullets, -35% лечения).
- Против пулевого DPS (Хейз, Тень, Айви / Haze, Wraith, Ivy) -> Металлическая кожа (Metal Skin, пулевой иммунитет) / Обратный огонь (Return Fire, 50% рефлект).
- Против кастеров и ультимейтов (Семь, Макгиннис, Динамо, Виндикта / Seven, McGinnis, Dynamo) -> Нокдаун (Knockdown, стан и сбитие полёта/ченнелинга) / Проклятие (Curse, запрет скиллов).
- Против бронированного фронтлайна (Абрамс, Мо и Мелкий, Дозорный / Abrams, Mo & Krill, Warden) -> Колосс (Colossus, хп и слоу) / Измельчитель пулестойкости (Bullet Resist Shredder)."""

    benchmarks_block = """\
- Керри (Хейз, Тень, Инфернус, Семь, Виндикта / Haze, Wraith, Infernus, Seven): эталон 5000+ душ к 10:00 (крипы линии + стаки кемпов).
- Дуэлянт/Инициатор (Абрамс, Шив, Хлыст, Ямато / Abrams, Shiv, Lash, Yamato): эталон 4200+ душ к 10:00.
- Саппорт/Контроллер (Кельвин, Айви, Динамо, Макгиннис / Kelvin, Ivy, Dynamo, McGinnis): эталон 3500+ душ к 10:00."""

    return f"""\
=== ТАКТИЧЕСКАЯ БАЗА ЗНАНИЙ DEADLOCK (The Cursed Apple) ===

1. ТАЙМИНГИ КАРТЫ:
{timings_block}

2. КРИТИЧЕСКИЕ ОШИБКИ ПОЗИЦИОНИРОВАНИЯ И МАКРО:
{mistakes_block}

3. СИСТЕМА КОНТР-ПРЕДМЕТОВ:
{counters_block}

4. ЭТАЛОНЫ ТЕМПА ФАРМА НА 10-Й МИНУТЕ (SOULS AT 10:00):
{benchmarks_block}
==========================================================="""


# --- 5. Словарь полной русской локализации и очистки отчётов ---
import re

LOCALIZATION_REPLACEMENTS: list[tuple[str, str]] = [
    # Сложные фразы синергий способностей и контекстные падежи
    (r"под Flight \+ Assassinate от Vindicta", "под Полёт + Ликвидацию от Виндикты"),
    (r"Flight \+ Assassinate от Vindicta", "Полёт + Ликвидация от Виндикты"),
    (r"Flight \+ Assassinate", "Полёт + Ликвидация"),
    (r"сбития Flight и Death Slam", "сбития Полёта и Смертельного броска"),
    (r"сбитие Flight и Death Slam", "сбитие Полёта и Смертельного броска"),
    (r"сбития Flight", "сбития Полёта"),
    (r"сбитие Flight", "сбитие Полёта"),
    (r"приземлить Vindicta из Flight", "приземлить Виндикту из Полёта"),
    (r"приземлить Vindicta", "приземлить Виндикту"),
    (r"вынуждая Vindicta", "вынуждая Виндикту"),
    (r"прокаст с Crow Familiar", "прокаст с Ручным вороном"),
    (r"с Crow Familiar", "с Ручным вороном"),
    (r"Life Drain у Lady Geist", "Вытягивания жизни у Леди Гайст"),
    (r"Life Drain", "Вытягивание жизни"),
    (r"Crow Familiar", "Ручной ворон"),
    (r"Death Slam", "Смертельный бросок"),
    (r"Ground Strike", "Удар оземь"),
    (r"Storm Cloud", "Грозовое облако"),
    (r"Bullet Dance", "Танец пуль"),
    (r"Sleep Dagger", "Усыпляющий кинжал"),
    (r"Smoke Bomb", "Дымовая шашка"),
    (r"Full Auto", "Огонь на поражение"),
    (r"Soul Exchange", "Обмен душами"),
    (r"Essence Bomb", "Бомба эссенции"),
    (r"Heavy Barrage", "Тяжелый обстрел"),
    (r"Mini Turret", "Мини-турель"),
    (r"Arctic Beam", "Арктический луч"),
    (r"Frozen Shelter", "Ледяное убежище"),
    (r"Ice Path", "Ледяная дорожка"),
    (r"Frost Grenade", "Ледяная граната"),
    (r"Seismic Impact", "Сейсмический удар"),
    (r"Shoulder Charge", "Удар плечом"),
    (r"Siphon Life", "Вытягивание жизни"),
    (r"Hyper Beam", "Гиперлуч"),
    (r"Guided Owl", "Управляемая сова"),
    (r"Rain of Fire", "Огненный дождь"),
    (r"Charged Shot", "Заряженный выстрел"),
    (r"Flame Dash", "Огненный рывок"),
    (r"Assassinate", "Ликвидация"),

    # Объекты карты, постройки и термины
    (r"секретный магазин \(Secret Shop\)", "секретный магазин"),
    (r"Secret Shop", "Секретный магазин"),
    (r"Base Shop", "Базовый магазин"),
    (r"T2/Base Guardians", "Стражей базы/T2"),
    (r"Base Guardians", "Стражей базы"),
    (r"Base Guardian", "Стража базы"),
    (r"T1 Guardians", "Стражей T1"),
    (r"T2 Guardians", "Стражей T2"),
    (r"T1 Guardian", "Страж T1"),
    (r"T2 Guardian", "Страж T2"),
    (r"\bGuardians\b", "Стражи"),
    (r"\bGuardian\b", "Страж"),
    (r"T2 Walkers", "Шагоходов T2"),
    (r"Т2 Walkers", "Шагоходов T2"),
    (r"T1 Walkers", "Шагоходов T1"),
    (r"Т1 Walkers", "Шагоходов T1"),
    (r"T2 Walker", "Шагоход T2"),
    (r"Т2 Walker", "Шагоход T2"),
    (r"T1 Walker", "Шагоход T1"),
    (r"Т1 Walker", "Шагоход T1"),
    (r"\bWalkers\b", "Шагоходы"),
    (r"\bWalker\b", "Шагоход"),
    (r"активной фазе Midboss", "активной фазе Мидбосса"),
    (r"фазе Midboss", "фазе Мидбосса"),
    (r"яму Midboss", "яму Мидбосса"),
    (r"яме Midboss", "яме Мидбосса"),
    (r"забрать Midboss", "забрать Мидбосса"),
    (r"убить Midboss", "убить Мидбосса"),
    (r"живом Midboss", "живом Мидбоссе"),
    (r"живого Midboss", "живого Мидбосса"),
    (r"от Midboss", "от Мидбосса"),
    (r"\bMidboss\b", "Мидбосс"),
    (r"\bMid-Boss\b", "Мидбосс"),
    (r"через Rejuvenator", "через Омолодитель"),
    (r"баффа Rejuvenator", "баффа Омолодителя"),
    (r"баффом Rejuvenator", "баффом Омолодителя"),
    (r"бафф Rejuvenator", "бафф Омолодителя"),
    (r"\bRejuvenator\b", "Омолодитель"),
    (r"Soul Urn", "Урна душ"),
    (r"Weakened Patron", "Ослабленный Покровитель"),
    (r"\bPatron\b", "Покровитель"),
    (r"готового Buyback", "контроля Омолодителя"),
    (r"готовым Buyback", "контролем Омолодителя"),
    (r"наличия Buyback", "контроля Омолодителя"),
    (r"резерв на Buyback", "контроль Омолодителя"),
    (r"\bBuyback\b", "Омолодитель"),

    # Герои (падежные формы и базовые имена)
    (r"Против Vindicta и Lash", "Против Виндикты и Хлыста"),
    (r"против Vindicta и Lash", "против Виндикты и Хлыста"),
    (r"Против Vindicta", "Против Виндикты"),
    (r"против Vindicta", "против Виндикты"),
    (r"от Vindicta", "от Виндикты"),
    (r"с Vindicta", "с Виндиктой"),
    (r"у Vindicta", "у Виндикты"),
    (r"под Vindicta", "под Виндикту"),
    (r"для Vindicta", "для Виндикты"),
    (r"\bVindicta\b", "Виндикта"),

    (r"Против Lash", "Против Хлыста"),
    (r"против Lash", "против Хлыста"),
    (r"и Lash", "и Хлыста"),
    (r"от Lash", "от Хлыста"),
    (r"с Lash", "с Хлыстом"),
    (r"у Lash", "у Хлыста"),
    (r"\bLash\b", "Хлыст"),

    (r"у Lady Geist", "у Леди Гайст"),
    (r"от Lady Geist", "от Леди Гайст"),
    (r"против Lady Geist", "против Леди Гайст"),
    (r"с Lady Geist", "с Леди Гайст"),
    (r"\bLady Geist\b", "Леди Гайст"),

    (r"сноуболлящей Haze", "сноуболлящей Хейз"),
    (r"фокуса Haze", "фокуса Хейз"),
    (r"фокус Haze", "фокус Хейз"),
    (r"против Haze", "против Хейз"),
    (r"от Haze", "от Хейз"),
    (r"с Haze", "с Хейз"),
    (r"\bHaze\b", "Хейз"),

    (r"против Seven", "против Семи"),
    (r"от Seven", "от Семи"),
    (r"у Seven", "у Семи"),
    (r"Севена", "Семи"),
    (r"\bSeven\b", "Семь"),

    (r"против Infernus", "против Инфернуса"),
    (r"от Infernus", "от Инфернуса"),
    (r"с Infernus", "с Инфернусом"),
    (r"\bInfernus\b", "Инфернус"),

    (r"против Abrams", "против Абрамса"),
    (r"от Abrams", "от Абрамса"),
    (r"с Abrams", "с Абрамсом"),
    (r"\bAbrams\b", "Абрамс"),

    (r"против Kelvin", "против Кельвина"),
    (r"от Kelvin", "от Кельвина"),
    (r"с Kelvin", "с Кельвином"),
    (r"\bKelvin\b", "Кельвин"),

    (r"против Wraith", "против Тени"),
    (r"или Wraith\b", "или Тени"),
    (r"или Тень\b", "или Тени"),
    (r"от Wraith", "от Тени"),
    (r"с Wraith", "с Тенью"),
    (r"фокус Wraith", "фокус Тени"),
    (r"\bWraith\b", "Тень"),

    (r"или Lash\b", "или Хлыста"),
    (r"или Vindicta\b", "или Виндикты"),
    (r"или Seven\b", "или Семи"),
    (r"или Infernus\b", "или Инфернуса"),
    (r"или Abrams\b", "или Абрамса"),
    (r"или Kelvin\b", "или Кельвина"),

    (r"против McGinnis", "против Макгиннис"),
    (r"от McGinnis", "от Макгиннис"),
    (r"\bMcGinnis\b", "Макгиннис"),

    (r"против Paradox", "против Парадокса"),
    (r"от Paradox", "от Парадокса"),
    (r"\bParadox\b", "Парадокс"),

    (r"против Dynamo", "против Динамо"),
    (r"от Dynamo", "от Динамо"),
    (r"\bDynamo\b", "Динамо"),

    (r"против Grey Talon", "против Серого Когтя"),
    (r"от Grey Talon", "от Серого Когтя"),
    (r"Грей Тэлона", "Серого Когтя"),
    (r"Грей Тэлон", "Серый Коготь"),
    (r"\bGrey Talon\b", "Серый Коготь"),

    (r"против Mo & Krill", "против Мо и Мелкого"),
    (r"от Mo & Krill", "от Мо и Мелкого"),
    (r"\bMo & Krill\b", "Мо и Мелкий"),
    (r"\bMo and Krill\b", "Мо и Мелкий"),

    (r"против Shiv", "против Шива"),
    (r"от Shiv", "от Шива"),
    (r"\bShiv\b", "Шив"),

    (r"против Ivy", "против Айви"),
    (r"от Ivy", "от Айви"),
    (r"\bIvy\b", "Айви"),

    (r"против Warden", "против Дозорного"),
    (r"от Warden", "от Дозорного"),
    (r"\bWarden\b", "Дозорный"),

    (r"против Yamato", "против Ямато"),
    (r"от Yamato", "от Ямато"),
    (r"\bYamato\b", "Ямато"),

    (r"против Viscous", "против Вязкуса"),
    (r"от Viscous", "от Вязкуса"),
    (r"\bViscous\b", "Вязкус"),

    (r"против Pocket", "против Кармана"),
    (r"от Pocket", "от Кармана"),
    (r"\bPocket\b", "Карман"),

    (r"против Mirage", "против Миража"),
    (r"от Mirage", "от Миража"),
    (r"\bMirage\b", "Мираж"),

    (r"против Bebop", "против Бебопа"),
    (r"от Bebop", "от Бебопа"),
    (r"\bBebop\b", "Бебоп"),

    (r"\bHolliday\b", "Холлидей"),
    (r"\bCalico\b", "Калико"),
    (r"\bSinclair\b", "Синклер"),
    (r"\bMina\b", "Мина"),
    (r"\bBilly\b", "Билли"),
    (r"\bpunkgoat\b", "Билли"),
    (r"\bGraves\b", "Грейвс"),
    (r"\bnecro\b", "Грейвс"),

    # Предметы (падежные формы и базовые названия)
    (r"через Knockdown", "через Нокдаун"),
    (r"с Knockdown", "с Нокдауном"),
    (r"\bKnockdown\b", "Нокдаун"),
    (r"\bHealbane\b", "Гроза целителей"),
    (r"без купленного Metal Skin или Return Fire", "без купленной Металлической кожи или Обратного огня"),
    (r"без купленной Металлической кожи или Return Fire", "без купленной Металлической кожи или Обратного огня"),
    (r"без купленного Metal Skin", "без купленной Металлической кожи"),
    (r"без Metal Skin или Return Fire", "без Металлической кожи или Обратного огня"),
    (r"без Metal Skin", "без Металлической кожи"),
    (r"приобрести Metal Skin", "приобрести Металлическую кожу"),
    (r"купить Metal Skin", "купить Металлическую кожу"),
    (r"покупку Metal Skin", "покупку Металлической кожи"),
    (r"купленного Metal Skin", "купленной Металлической кожи"),
    (r"купленной Metal Skin", "купленной Металлической кожи"),
    (r"или Metal Skin\b", "или Металлической кожи"),
    (r"или Металлическая кожа\b", "или Металлической кожи"),
    (r"\bMetal Skin\b", "Металлическая кожа"),
    (r"без Return Fire", "без Обратного огня"),
    (r"покупку Return Fire", "покупку Обратного огня"),
    (r"приобрести Return Fire", "приобрести Обратный огонь"),
    (r"\bReturn Fire\b", "Обратный огонь"),
    (r"\bToxic Bullets\b", "Токсичные пули"),
    (r"\bCurse\b", "Проклятие"),
    (r"\bColossus\b", "Колосс"),
    (r"\bLeech\b", "Кровопийца"),
    (r"\bGlass Cannon\b", "Стеклянная пушка"),
    (r"\bBullet Resist Shredder\b", "Измельчитель пулестойкости"),
    (r"\bExtended Magazine\b", "Увеличенный магазин"),
    (r"\bMonster Rounds\b", "Чудовищные патроны"),
    (r"\bClose Quarters\b", "Ближняя дистанция"),
    (r"\bSprint Boots\b", "Беговые ботинки"),
    (r"\bFleetfoot\b", "Проворная поступь"),
    (r"\bSharpshooter\b", "Прицельная стрельба"),
    (r"\bPoint Blank\b", "Стрельба в упор"),
    (r"\bBerserker\b", "Берсерк"),
    (r"\bMelee Lifesteal\b", "Ударная кража здоровья"),
    (r"\bDivine Barrier\b", "Божественный барьер"),
    (r"\bWarp Stone\b", "Камень искривления"),
    (r"\bEthereal Shift\b", "Эфирный сдвиг"),
    (r"\bMajestic Leap\b", "Величественный прыжок"),
    (r"\bSilence Glyph\b", "Символ безмолвия"),
    (r"\bDebuff Remover\b", "Снятие ослаблений"),
    (r"\bUnstoppable\b", "Неудержимость"),
    (r"\bRefresher\b", "Восстановитель"),
    (r"\bSpell Shield\b", "Щит заклинаний"),
    (r"\bSpirit Armor\b", "Духовная броня"),
    (r"\bBullet Armor\b", "Пулевая броня"),
    (r"\bSuperior Stamina\b", "Превосходная выносливость"),
    (r"\bExtra Stamina\b", "Дополнительная выносливость"),

    # Способности (падежные формы)
    (r"из Flight", "из Полёта"),
    (r"в Flight", "в Полёт"),
    (r"под Flight", "под Полёт"),
    (r"\bFlight\b", "Полёт"),
]

_COMPILED_REPLACEMENTS = [(re.compile(p), r) for p, r in LOCALIZATION_REPLACEMENTS]


def localize_text(text: str) -> str:
    """Заменяет английские названия героев, предметов, способностей и объектов Deadlock на русские."""
    if not isinstance(text, str) or not text:
        return text
    for rx, repl in _COMPILED_REPLACEMENTS:
        text = rx.sub(repl, text)
    return text


def localize_report_dict(obj: Any) -> Any:
    """Рекурсивно локализует все строковые поля в словарях и списках отчёта тренера."""
    if isinstance(obj, str):
        return localize_text(obj)
    if isinstance(obj, dict):
        return {k: localize_report_dict(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [localize_report_dict(x) for x in obj]
    return obj
