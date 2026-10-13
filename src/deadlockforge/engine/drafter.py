"""6v6 Draft Engine: Red Flags, synergies, counter-picks.

Pure logic over the Catalog — no I/O, no globals. Mirrors the advisor's
scoring philosophy (stacking bonus per extra hero sharing a tag).

Interface:
    analyze_draft(my_team, enemy_team, catalog, allow_mirror) -> DraftAnalysis
    suggest_build(hero_id, enemy_tags, catalog) -> list[str] (item ids)
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from ..models import (
    CounterRule,
    Hero,
    RedFlag,
    ThreatTag,
)
from .dataset import Catalog

MAX_TEAM = 6

# Threat score weights: tag stacking across the enemy roster.
STACKING_BONUS = 0.5

# Thresholds for Red Flag detection.
HEAL_OVERLOAD_HEROES = 2       # >= 2 heal-tagged enemies
ULT_OVERLOAD_HEROES = 2        # >= 2 uninterruptible ults
MOBILITY_OVERLOAD_HEROES = 3   # >= 3 mobile heroes
SPIRIT_OVERLOAD_HEROES = 3     # >= 3 spirit casters
BULLET_SHARE_CRITICAL = 0.7    # my team damage profile share


class CounterPick(BaseModel):
    hero_id: str
    name: str
    reason: str


class SynergyNote(BaseModel):
    title: str
    detail: str


class DraftAnalysis(BaseModel):
    """Structured output of the 6v6 draft engine."""

    red_flags: list[RedFlag] = Field(default_factory=list)
    synergy: list[SynergyNote] = Field(default_factory=list)
    counter_picks: list[CounterPick] = Field(default_factory=list)
    threat_summary: list[dict] = Field(default_factory=list)
    recommended_items: list[dict] = Field(default_factory=list)


# --- tag helpers -----------------------------------------------------------


def _tags_of(heroes: list[Hero]) -> dict[ThreatTag, list[str]]:
    contributors: dict[ThreatTag, list[str]] = {}
    for hero in heroes:
        for tag in hero.threat_tags:
            contributors.setdefault(tag, []).append(hero.id)
    return contributors


def _enemy_tag_scores(enemy_tags: dict[ThreatTag, list[str]]) -> dict[ThreatTag, float]:
    return {
        tag: TAG_WEIGHT * sum(1.0 + STACKING_BONUS * i for i in range(len(ids)))
        for tag, ids in enemy_tags.items()
    }


TAG_WEIGHT = 1.0


# --- red flags -------------------------------------------------------------


def _red_flags(
    my_team: list[Hero], enemy_team: list[Hero], catalog: Catalog
) -> list[RedFlag]:
    flags: list[RedFlag] = []
    enemy_tags = _tags_of(enemy_team)
    my_tags = _tags_of(my_team)
    rules: dict[ThreatTag, CounterRule] = {r.tag: r for r in catalog.counter_rules}

    def _top_item(tag: ThreatTag) -> str:
        rule = rules.get(tag)
        if rule and rule.entries:
            top = rule.entries[0]
            item = catalog.item_by_id.get(top.item_id)
            # TZ v1.4 F3: англ. имя только при отсутствии русского локала.
            if item:
                return item.name if item.name != item.name_en else item.name_en
            return top.item_id
        return "—"

    heal_heroes = enemy_tags.get(ThreatTag.HEAL, [])
    if len(heal_heroes) >= HEAL_OVERLOAD_HEROES:
        flags.append(RedFlag(
            severity="critical",
            title="Вражеский пик перегружен лечением",
            detail=(
                f"{len(heal_heroes)} героя с устойчивым отхилом "
                f"({', '.join(heal_heroes)}) — без анти-хила файты затягиваются, "
                "а их сустейн перебивает ваш бурст."
            ),
            fix=f"Купи {_top_item(ThreatTag.HEAL)} в первые 10 минут — режет лечение вдвое.",
        ))

    ult_heroes = enemy_tags.get(ThreatTag.UNINTERRUPTIBLE_ULT, [])
    if len(ult_heroes) >= ULT_OVERLOAD_HEROES:
        flags.append(RedFlag(
            severity="critical",
            title="Неуязвимые ульты у врага",
            detail=(
                f"Канальные ульты ({', '.join(ult_heroes)}) нельзя прервать "
                "обычным контролем — нужен жёсткий стоп."
            ),
            fix=f"{_top_item(ThreatTag.UNINTERRUPTIBLE_ULT)} обязателен: без него ульта отрабатывает полностью.",
        ))

    # My team bullet-damage share: heroes whose damage profile is gun-first.
    bullet_mine = [h.id for h in my_team if ThreatTag.BULLET_DPS in h.threat_tags]
    if my_team and len(bullet_mine) / len(my_team) >= BULLET_SHARE_CRITICAL:
        flags.append(RedFlag(
            severity="warning",
            title="Пулевой урон доминирует в вашей команде",
            detail=(
                f"{len(bullet_mine)}/{len(my_team)} героев наносят преимущественно "
                "пулевой урон — вражеский Metal Skin закроет весь ваш DPS."
            ),
            fix="Собери Spirit-скейлинг (Echo Shard / Spirit предметы) хотя бы на двух героях.",
        ))

    mobile = enemy_tags.get(ThreatTag.MOBILITY, [])
    if len(mobile) >= MOBILITY_OVERLOAD_HEROES:
        flags.append(RedFlag(
            severity="warning",
            title="Враг мобильнее вас",
            detail=(
                f"{len(mobile)} мобильных героев ({', '.join(mobile)}) — "
                "они выбирают размены и уходят от ганков."
            ),
            fix=f"Лок-даун: {_top_item(ThreatTag.MOBILITY)} + позиционный контроль линии.",
        ))

    spirit = enemy_tags.get(ThreatTag.SPIRIT_CASTER, [])
    if len(spirit) >= SPIRIT_OVERLOAD_HEROES:
        flags.append(RedFlag(
            severity="warning",
            title="Spirit-кастеры перегружают врага",
            detail=(
                f"{len(spirit)} спелл-дамагера ({', '.join(spirit)}) — "
                "спирит-щиты прошьют вашу живучесть."
            ),
            fix=f"{_top_item(ThreatTag.SPIRIT_CASTER)} на всех, кто ловит их касты.",
        ))

    # Enemy tank+heal front vs our sustained damage check.
    enemy_tank_heal = (ThreatTag.TANK in enemy_tags) and (ThreatTag.HEAL in enemy_tags)
    my_sustain = any(ThreatTag.BULLET_DPS in h.threat_tags for h in my_team)
    if enemy_tank_heal and not my_sustain:
        flags.append(RedFlag(
            severity="info",
            title="Фронтлайн врага слишком жирный",
            detail=(
                "Танк + саппорт-хил: их фронт переживёт ваш разовый бурст "
                "и выйдет из файта с полным ХП."
            ),
            fix="Добавь %-ый урон (Bullet Resist Shred / Colossus) вместо плоских статов.",
        ))

    return flags


# --- synergies ---------------------------------------------------------------

_SYNERGY_RULES = [
    # (tag pair present in my team) -> note
    ((ThreatTag.BULLET_DPS, ThreatTag.SPIRIT_CASTER),
     "Смешанный урон",
     "Пулевой + спирит-дамаг: враг не закрыться ни Metal Skin, ни Spirit Armor."),
    ((ThreatTag.TANK, ThreatTag.HEAL),
     "Фронтлайн с саппортом",
     "Танк держит урон, хил возвращает его — затяжные файты ваши."),
    ((ThreatTag.CC_CHAIN, ThreatTag.BURST),
     "Контроль + бурст",
     "Цепочка CC даёт окно на килл-бурст до отхила."),
    ((ThreatTag.MOBILITY, ThreatTag.CC_CHAIN),
     "Мобильный контроль",
     "Роум-инициация с любой линии: ганк -> контроль -> добивание."),
]


def _synergy(my_team: list[Hero]) -> list[SynergyNote]:
    my_tags = _tags_of(my_team)
    notes: list[SynergyNote] = []
    for (a, b), title, detail in _SYNERGY_RULES:
        if a in my_tags and b in my_tags:
            notes.append(SynergyNote(title=title, detail=detail))
    return notes


# Русские лейблы тегов для пользовательских строк (фронт показывает
# их же через TAG_LABEL; здесь — для reason counter-picks и Red Flags).
TAG_LABELS_RU: dict[str, str] = {
    "heal": "лечение",
    "bullet_dps": "пулевой DPS",
    "spirit_caster": "Spirit-кастеры",
    "burst": "бурст",
    "mobility": "мобильность",
    "stealth": "стелс",
    "tank": "танки",
    "cc_chain": "цепочки контроля",
    "uninterruptible_ult": "непрерываемые ульты",
}


def _tag_ru(tag) -> str:
    return TAG_LABELS_RU.get(tag.value, tag.value)


# --- counter-picks -------------------------------------------------------------

# What beats what: enemy tag -> my hero tags that answer it.
_COUNTER_MATRIX: dict[ThreatTag, list[ThreatTag]] = {
    ThreatTag.HEAL: [ThreatTag.SPIRIT_CASTER, ThreatTag.BURST],
    ThreatTag.BULLET_DPS: [ThreatTag.TANK, ThreatTag.CC_CHAIN],
    ThreatTag.SPIRIT_CASTER: [ThreatTag.BURST, ThreatTag.MOBILITY],
    ThreatTag.BURST: [ThreatTag.TANK, ThreatTag.HEAL],
    ThreatTag.MOBILITY: [ThreatTag.CC_CHAIN, ThreatTag.BULLET_DPS],
    ThreatTag.STEALTH: [ThreatTag.CC_CHAIN, ThreatTag.BULLET_DPS],
    ThreatTag.TANK: [ThreatTag.SPIRIT_CASTER, ThreatTag.BURST],
    ThreatTag.CC_CHAIN: [ThreatTag.MOBILITY, ThreatTag.TANK],
    ThreatTag.UNINTERRUPTIBLE_ULT: [ThreatTag.BURST],
}


def _counter_picks(
    my_team: list[Hero], enemy_team: list[Hero], catalog: Catalog
) -> list[CounterPick]:
    enemy_tags = _tags_of(enemy_team)
    picked = {h.id for h in my_team}
    scores: dict[str, float] = {}
    reasons: dict[str, list[str]] = {}

    for tag, enemies in enemy_tags.items():
        answers = _COUNTER_MATRIX.get(tag, [])
        for hero in catalog.heroes:
            if hero.id in picked or hero.in_development:
                continue
            overlap = [t for t in hero.threat_tags if t in answers]
            if not overlap:
                continue
            scores[hero.id] = scores.get(hero.id, 0.0) + len(overlap) * len(enemies)
            tag_names = ", ".join(t.value for t in overlap)
            reasons.setdefault(hero.id, []).append(
                f"против {_tag_ru(tag)} ({', '.join(enemies)})"
            )

    ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))[:5]
    result: list[CounterPick] = []
    for hero_id, _score in ranked:
        hero = catalog.by_id[hero_id]
        result.append(CounterPick(
            hero_id=hero_id,
            name=hero.name,  # TZ v1.4 F3: без англ. дубля
            reason="; ".join(reasons[hero_id][:3]),
        ))
    return result


# --- items ----------------------------------------------------------------------


def suggest_build(hero_id: str, enemy_tags: list[ThreatTag], catalog: Catalog) -> list[str]:
    """Ordered counter item ids for the given enemy threat tags."""
    rules = {r.tag: r for r in catalog.counter_rules}
    seen: list[str] = []
    for tag in sorted(enemy_tags, key=lambda t: -len(_COUNTER_MATRIX.get(t, []))):
        rule = rules.get(tag)
        if not rule:
            continue
        for entry in rule.entries:
            if entry.item_id not in seen and entry.item_id in catalog.item_by_id:
                seen.append(entry.item_id)
    return seen[:8]


# --- entry point -----------------------------------------------------------------


def analyze_draft(
    my_team_ids: list[str],
    enemy_team_ids: list[str],
    catalog: Catalog,
    allow_mirror: bool = False,
) -> DraftAnalysis:
    """Analyze a 6v6 draft. Raises ValueError on rule violations."""
    if len(my_team_ids) > MAX_TEAM or len(enemy_team_ids) > MAX_TEAM:
        raise ValueError(f"Teams are {MAX_TEAM}v{MAX_TEAM} maximum.")

    overlap = set(my_team_ids) & set(enemy_team_ids)
    if overlap and not allow_mirror:
        raise ValueError(
            f"Mirror pick forbidden in competitive mode: {', '.join(sorted(overlap))}."
        )
    if len(my_team_ids) != len(set(my_team_ids)):
        raise ValueError("Duplicate heroes inside your team.")
    if len(enemy_team_ids) != len(set(enemy_team_ids)):
        raise ValueError("Duplicate heroes inside the enemy team.")

    def _resolve(ids: list[str]) -> list[Hero]:
        heroes: list[Hero] = []
        for hid in ids:
            hero = catalog.by_id.get(hid)
            if hero is None:
                raise ValueError(f"Unknown hero id: {hid!r}.")
            heroes.append(hero)
        return heroes

    my_team = _resolve(my_team_ids)
    enemy_team = _resolve(enemy_team_ids)

    enemy_tags = _tags_of(enemy_team)
    tag_scores = _enemy_tag_scores(enemy_tags)
    top_tags = sorted(tag_scores.items(), key=lambda kv: (-kv[1], kv[0].value))[:4]

    return DraftAnalysis(
        red_flags=_red_flags(my_team, enemy_team, catalog),
        synergy=_synergy(my_team),
        counter_picks=_counter_picks(my_team, enemy_team, catalog),
        threat_summary=[
            {
                "tag": tag.value,
                "score": round(score, 2),
                "contributors": enemy_tags[tag],
            }
            for tag, score in top_tags
        ],
        recommended_items=[
            {
                "item_id": item_id,
                "name_en": catalog.item_by_id[item_id].name_en,
                "name": catalog.item_by_id[item_id].name,
            }
            for item_id in suggest_build(
                my_team_ids[0] if my_team_ids else "",
                [t for t, _ in top_tags],
                catalog,
            )
        ],
    )
