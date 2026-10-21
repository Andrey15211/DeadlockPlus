"""S5: Fast in-match builds from an enemy draft (30–40 s budget).

Local-first engine: the whole plan is computed from the existing catalog with
pure functions — advisor (threat ranking + counter items), drafter (red flags)
and calculator (aggregated stats). No network, no LLM on the main path.

Interface:
    build_plan(my_hero_id, enemy_ids, catalog, role=None, mode="local") -> DraftBuildPlan
    llm_notes(plan, client, timeout) -> dict[str, str] | None   (optional, never blocks)

Response shape (see DraftBuildPlan):
    starter      — 3–4 Tier-1 items for the lane, with price and a one-line reason
    variants     — "farm" / "teamfight" / "counter": item ids, total price,
                   target stats from calculator, 36-slot board, rationale
    counters     — counter items for the enemy tags (advisor ranking + per-tag cover)
    red_flags    — from drafter.analyze_draft
    timing_hint  — when to buy what, by models.TIER_COST_BANDS
"""

from __future__ import annotations

import time
from typing import Any, Optional

from pydantic import BaseModel, Field

from ..models import (
    BuildSlots,
    CounterRule,
    Hero,
    Item,
    ItemCategory,
    RedFlag,
    TIER_COST_BANDS,
    ThreatTag,
)
from . import advisor as advisor_mod
from . import calculator as calc_mod
from . import drafter as drafter_mod
from .builds import validate as validate_slots
from .dataset import Catalog

MAX_ENEMIES = 6
STARTER_MIN = 3
STARTER_MAX = 4
VARIANT_TARGET_ITEMS = 10
COUNTER_LIMIT = 8

# T5 items (9100+ souls) are situational late-game luxury; the in-match quick
# build wants the T1–T4 core that actually fits a 30–40 min match.
MAX_ITEM_TIER = 4

# Buy windows per tier — the cost bands themselves stay in models.TIER_COST_BANDS.
TIER_WINDOWS: dict[int, str] = {
    1: "0–8 мин",
    2: "8–16 мин",
    3: "16–24 мин",
    4: "24+ мин",
    5: "35+ мин",
}

MODES = ("local", "offline", "ai")
ROLE_ALIASES: dict[str, str] = {
    "carry": "carry",
    "support": "support",
    "tank": "initiator",          # ввод пользователя: «танк»
    "initiator": "initiator",
    "duelist": "duelist",
    "controller": "controller",
    "spirit": "carry",            # spirit-керри: тот же скейл-профиль
}


# --- response contracts ----------------------------------------------------


class StarterItem(BaseModel):
    item_id: str
    name: str
    name_en: str
    category: str
    tier: int
    cost: int
    reason: str


class VariantItem(BaseModel):
    item_id: str
    name: str
    name_en: str
    category: str
    tier: int
    cost: int
    tier_band: str


class BuyWindow(BaseModel):
    tier: int
    cost_range: str
    window: str
    item_ids: list[str] = Field(default_factory=list)


class BuildVariant(BaseModel):
    key: str
    title: str
    summary: str
    item_ids: list[str] = Field(default_factory=list)
    items: list[VariantItem] = Field(default_factory=list)
    total_cost: int = 0
    focus: list[str] = Field(default_factory=list)
    stats: dict[str, Any] = Field(default_factory=dict)
    slots: dict[str, list[Optional[str]]] = Field(default_factory=dict)
    buy_order: list[BuyWindow] = Field(default_factory=list)
    rationale: list[str] = Field(default_factory=list)
    llm_note: Optional[str] = None


class CounterItem(BaseModel):
    item_id: str
    name: str
    name_en: str
    category: str
    tier: int
    cost: int
    priority: int
    timing: str
    covered_tags: list[str] = Field(default_factory=list)
    explanation: str = ""


class ThreatOut(BaseModel):
    tag: str
    label: str
    score: float
    contributors: list[str] = Field(default_factory=list)


class TimingHint(BaseModel):
    bands: list[BuyWindow] = Field(default_factory=list)
    summary: str = ""


class EngineMeta(BaseModel):
    mode: str = "local"
    local_ms: float = 0.0
    llm_used: bool = False
    llm_ms: Optional[float] = None


class DraftBuildPlan(BaseModel):
    """POST /api/draft/builds response."""

    my_hero: dict[str, Any]
    enemies: list[dict[str, Any]] = Field(default_factory=list)
    role_hint: Optional[str] = None
    starter: list[StarterItem] = Field(default_factory=list)
    starter_cost: int = 0
    variants: list[BuildVariant] = Field(default_factory=list)
    counters: list[CounterItem] = Field(default_factory=list)
    red_flags: list[RedFlag] = Field(default_factory=list)
    threat_summary: list[ThreatOut] = Field(default_factory=list)
    timing_hint: TimingHint = Field(default_factory=TimingHint)
    notes: list[str] = Field(default_factory=list)
    engine: EngineMeta = Field(default_factory=EngineMeta)


# --- item scoring ----------------------------------------------------------

_STAT_ORDER = (
    "weapon_damage", "fire_rate_pct", "base_hp", "bullet_shield",
    "spirit_shield", "stamina", "cooldown_reduction_pct", "spirit_power",
    "move_speed",
)

# Stats whose dataset units are inconsistent (fractions for most rows, whole
# percent points for a few — e.g. Heroic Aura fire_rate_pct=34 vs 0.34).
# Self-calibrated: any magnitude above this is read as percent points.
_PERCENT_STATS = {"fire_rate_pct", "cooldown_reduction_pct"}
_PERCENT_GUARD = 2.0

# One-fight shields (Battle Vest 300, Divine Barrier 600, Diviner's Kevlar 1000)
# must not out-weigh the armor-type rolls they compete with.
_UNIT_CEILING: dict[str, float] = {
    "bullet_shield": 60.0,
    "spirit_shield": 60.0,
}

_STAT_LABELS_RU: dict[str, str] = {
    "weapon_damage": "урон оружия",
    "fire_rate_pct": "скорострельность",
    "base_hp": "запас HP",
    "bullet_shield": "пулевой щит",
    "spirit_shield": "спирит-щит",
    "stamina": "выносливость",
    "cooldown_reduction_pct": "перезарядка способностей",
    "spirit_power": "сила духа",
    "move_speed": "скорость движения",
}
_STAT_ORDER = (
    "weapon_damage", "fire_rate_pct", "base_hp", "bullet_shield",
    "spirit_shield", "stamina", "cooldown_reduction_pct", "spirit_power",
    "move_speed",
)

# Stat weight profile per variant: what the build is buying. Kept polarized —
# a farm build must not silently drift into a tank build.
_VARIANT_PROFILES: dict[str, dict[str, float]] = {
    "farm": {
        "weapon_damage": 1.0, "fire_rate_pct": 0.9, "spirit_power": 0.85,
        "cooldown_reduction_pct": 0.45, "move_speed": 0.3, "stamina": 0.2,
        "base_hp": 0.15, "bullet_shield": 0.05, "spirit_shield": 0.05,
    },
    "teamfight": {
        "base_hp": 1.0, "bullet_shield": 0.9, "spirit_shield": 0.9,
        "cooldown_reduction_pct": 0.7, "stamina": 0.45, "spirit_power": 0.35,
        "weapon_damage": 0.3, "fire_rate_pct": 0.15, "move_speed": 0.2,
    },
    "counter": {
        "base_hp": 0.55, "bullet_shield": 0.5, "spirit_shield": 0.5,
        "cooldown_reduction_pct": 0.45, "weapon_damage": 0.4,
        "spirit_power": 0.4, "stamina": 0.3, "move_speed": 0.3,
        "fire_rate_pct": 0.25,
    },
}

# How much enemy threat coverage is worth inside each variant. Profile
# scores live in roughly [-2.8, 2.8], so these stay comparable by design:
# farm barely chases counters, teamfight partly, "counter" prioritizes them.
_COUNTER_WEIGHT: dict[str, float] = {"farm": 0.25, "teamfight": 0.8, "counter": 2.6}
_COUNTER_CAP = 2.5   # max summed threat magnitude credited per item

# Item-count budget per variant (bucketed by tier) — keeps variants distinct
# and the board inside the 12 slots per category.
_VARIANT_LIMITS: dict[str, dict[str, int]] = {
    "farm": {"t1": 3, "t2": 4, "t34": 3},
    "teamfight": {"t1": 3, "t2": 4, "t34": 3},
    "counter": {"t1": 2, "t2": 3, "t34": 5},
}

# Curated anchors: T1 items whose identity the dataset stats do not carry
# (farm value / lane durability). Small and explicit on purpose.
_VARIANT_ANCHORS: dict[str, dict[str, float]] = {
    "farm": {
        "monster-rounds": 0.9,      # T1: урон по нейтралам — лес фармится быстрее
        "basic-magazine": 0.6,      # T1: урон оружия + патроны
        "high-velocity-mag": 0.5,   # T1: пуля быстрее — проще добивать
        "extra-charge": 0.4,        # T1: доп. заряд — темп фарма способности
    },
    "teamfight": {
        "extra-health": 0.9,        # T1: базовый HP на файты
        "extra-stamina": 0.6,       # T1: рывки из-под контроля
        "health-stimpak": 0.5,      # T1: отхил без базы
        "life-strike": 0.4,         # T1: вампиризм в разменах
    },
    "counter": {"sprint-boots": 0.5},  # T1: выйти из-под инициации
}

# Counter items seeded into a variant up front, taken in advisor priority
# order: the "counter" variant is literally built around their answers.
_COUNTER_SEED: dict[str, int] = {"farm": 0, "teamfight": 2, "counter": 5}

_STARTER_ANCHORS: dict[str, tuple[str, ...]] = {
    # hero threat tag → preferred starter T1 item ids (first existing wins)
    "bullet_dps": ("basic-magazine", "high-velocity-mag", "monster-rounds"),
    "spirit_caster": ("extra-charge", "mystic-reach", "fortune-hand"),
    "burst": ("extra-charge", "improved-spirit", "mystic-reach"),
    "tank": ("extra-health", "grit", "extra-stamina"),
    "heal": ("extra-health", "life-strike", "health-stimpak"),
    "mobility": ("sprint-boots", "extra-stamina", "endurance"),
    "stealth": ("sprint-boots", "high-velocity-mag", "basic-magazine"),
    "cc_chain": ("extra-stamina", "extra-charge", "mystic-reach"),
    "uninterruptible_ult": ("extra-health", "extra-stamina", "grit"),
}

_STARTER_FALLBACK = ("extra-health", "basic-magazine", "extra-stamina", "sprint-boots")

_VARIANT_TITLES = {
    "farm": "Под фарм",
    "teamfight": "Под драки",
    "counter": "Против их состава",
}

_ROLE_STAT_BOOST: dict[str, dict[str, float]] = {
    "carry": {"weapon_damage": 1.5, "fire_rate_pct": 1.3, "spirit_power": 1.3},
    "support": {"cooldown_reduction_pct": 1.4, "spirit_shield": 1.2, "base_hp": 1.2},
    "initiator": {"base_hp": 1.4, "bullet_shield": 1.2, "spirit_shield": 1.2},
    "duelist": {"move_speed": 1.3, "stamina": 1.3, "weapon_damage": 1.2},
    "controller": {"cooldown_reduction_pct": 1.3, "spirit_power": 1.2, "base_hp": 1.1},
}

_TAG_STAT_BOOST: dict[ThreatTag, dict[str, float]] = {
    ThreatTag.BULLET_DPS: {"weapon_damage": 1.4, "fire_rate_pct": 1.3},
    ThreatTag.SPIRIT_CASTER: {"spirit_power": 1.4, "cooldown_reduction_pct": 1.2},
    ThreatTag.BURST: {"spirit_power": 1.3, "weapon_damage": 1.2},
    ThreatTag.TANK: {"base_hp": 1.4, "bullet_shield": 1.2, "spirit_shield": 1.2},
    ThreatTag.HEAL: {"base_hp": 1.3, "weapon_damage": 1.1},
    ThreatTag.MOBILITY: {"move_speed": 1.3, "stamina": 1.2},
    ThreatTag.STEALTH: {"move_speed": 1.2, "weapon_damage": 1.1},
    ThreatTag.CC_CHAIN: {"cooldown_reduction_pct": 1.2, "base_hp": 1.1},
    ThreatTag.UNINTERRUPTIBLE_ULT: {"base_hp": 1.2, "stamina": 1.1},
}


def resolve_role(role: Optional[str]) -> Optional[str]:
    """Normalize a role hint; raise ValueError on an unknown value."""
    if role is None or not str(role).strip():
        return None
    key = str(role).strip().lower()
    if key in ROLE_ALIASES:
        return ROLE_ALIASES[key]
    allowed = ", ".join(sorted(set(ROLE_ALIASES)))
    raise ValueError(f"Unknown role hint {role!r}: expected one of {allowed}.")


def normalize_mode(mode: Optional[str]) -> str:
    """Normalize the mode flag; raise ValueError on an unknown value."""
    key = (mode or "local").strip().lower()
    if key not in MODES:
        raise ValueError(
            f"Unknown mode {mode!r}: expected one of {', '.join(MODES)}."
        )
    return "ai" if key == "ai" else "local"


# --- helpers ---------------------------------------------------------------


def _band_text(tier: int) -> str:
    low, high = TIER_COST_BANDS[tier]
    if high >= 10**8:
        return f"{low}+ душ"
    return f"{low}–{high} душ"


def _tier_bucket(tier: int) -> str:
    return "t1" if tier <= 1 else ("t2" if tier == 2 else "t34")


def _weights_for(
    hero: Hero, variant: str, role: Optional[str]
) -> dict[str, float]:
    """Stat weights: variant profile × hero tag affinity × role hint."""
    weights = dict(_VARIANT_PROFILES[variant])
    for tag in hero.threat_tags:
        for stat, mult in _TAG_STAT_BOOST.get(tag, {}).items():
            weights[stat] = weights.get(stat, 0.0) * mult
    if role:
        for stat, mult in _ROLE_STAT_BOOST.get(role, {}).items():
            weights[stat] = weights.get(stat, 0.0) * mult
    return {stat: w for stat, w in weights.items() if w > 0}


_STAT_ORDER = (
    "weapon_damage", "fire_rate_pct", "base_hp", "bullet_shield",
    "spirit_shield", "stamina", "cooldown_reduction_pct", "spirit_power",
    "move_speed",
)


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


class _Scale:
    """Unit normalizer calibrated on the catalog's real stat distributions.

    The dataset mixes units inside single stats: `fire_rate_pct` is mostly a
    fraction (0.20) but a few rows carry whole percent points (34); "shield"
    stats mix ~15-point armor rolls with 300–1000 one-fight shields. A hand
    written table of typicals silently rewards whichever item happens to use
    the loud unit, so the normalization is derived from median magnitudes and
    a cap instead.
    """

    def __init__(self, items: list[Item]) -> None:
        self._ceiling: dict[str, float] = dict(_UNIT_CEILING)
        for stat in _STAT_ORDER:
            values = []
            for item in items:
                if item.tier > MAX_ITEM_TIER:
                    continue
                raw = abs(float(getattr(item.stats, stat, 0) or 0))
                if raw:
                    values.append(raw)
            if not values:
                self._ceiling.setdefault(stat, 1.0)
                continue
            self._ceiling.setdefault(stat, _median(values) * 3.0)

    def value(self, stat: str, raw: float) -> float:
        """Magnitude of one stat roll in units of its own typical scale."""
        if not raw:
            return 0.0
        if stat in _PERCENT_STATS and abs(raw) > _PERCENT_GUARD:
            raw = raw / 100.0
        ceiling = self._ceiling.get(stat) or 1.0
        return max(-3.0, min(3.0, raw / ceiling))


def _scale_for(catalog: Catalog) -> _Scale:
    return _Scale(catalog.items)


def _normalized(item: Item, weights: dict[str, float], scale: _Scale):
    """Yield (stat, weight, normalized value) for every non-zero term."""
    for stat, weight in weights.items():
        raw = float(getattr(item.stats, stat, 0) or 0)
        if raw:
            yield stat, weight, scale.value(stat, raw)


def _stat_score(item: Item, weights: dict[str, float], scale: _Scale) -> float:
    """How much the item *is* what the profile buys.

    The direction ratio dominates: a profile must pick items pointing the same
    way, not merely items with a big stat budget. Magnitude only breaks ties.
    """
    positive = negative = magnitude = 0.0
    for _stat, weight, value in _normalized(item, weights, scale):
        magnitude += weight * abs(value)
        if value > 0:
            positive += weight * value
        else:
            negative += weight * abs(value)
    if magnitude == 0:
        return 0.0
    direction = (positive - 2.0 * negative) / magnitude
    return direction * 2.0 + magnitude * 0.35


def _focus_labels(weights: dict[str, float]) -> list[str]:
    """Headline stats of the profile: top 3 by weight, deterministic ties."""
    order = [
        "base_hp", "weapon_damage", "spirit_power", "cooldown_reduction_pct",
        "bullet_shield", "spirit_shield", "fire_rate_pct", "stamina", "move_speed",
    ]
    ranked = sorted(
        (s for s, w in weights.items() if w > 0),
        key=lambda s: (-weights[s], order.index(s) if s in order else 99),
    )
    return [_STAT_LABELS_RU[s] for s in ranked[:3]]


def _stat_reason(item: Item, weights: dict[str, float], scale: _Scale) -> str:
    """One-line RU reason built from the item's best matching stat."""
    best: tuple[float, str, float] | None = None
    for stat, weight, value in _normalized(item, weights, scale):
        contribution = weight * value
        if best is None or contribution > best[0]:
            best = (contribution, stat, float(getattr(item.stats, stat, 0) or 0))
    if best is None or best[0] <= 0:
        return "закрывает раннюю экономику и выживаемость на линии"
    _, stat, raw = best
    if stat in _PERCENT_STATS:
        value = raw / 100.0 if abs(raw) > _PERCENT_GUARD else raw
        return f"{_STAT_LABELS_RU[stat]}: +{value * 100:.0f}%"
    if stat == "stamina":
        return f"{_STAT_LABELS_RU[stat]}: +{int(raw)}"
    return f"{_STAT_LABELS_RU[stat]}: +{raw:.0f}"


def _threat_scores(enemies: list[Hero]) -> dict[ThreatTag, float]:
    """Tag → normalized threat magnitude (1 hero ≈ 0.67 … 4+ ≈ 2.0)."""
    counts: dict[ThreatTag, int] = {}
    for hero in enemies:
        for tag in hero.threat_tags:
            counts[tag] = counts.get(tag, 0) + 1
    return {
        tag: min(2.0, max(0.6, count / 1.5)) for tag, count in counts.items()
    }


def _covered_tags(
    present: set[ThreatTag], catalog: Catalog
) -> dict[str, list[ThreatTag]]:
    """Counter item id → enemy tags it actually answers (per counter_rules)."""
    mapping: dict[str, list[ThreatTag]] = {}
    for rule in catalog.counter_rules:
        if rule.tag not in present:
            continue
        for entry in rule.entries:
            tags = mapping.setdefault(entry.item_id, [])
            if rule.tag not in tags:
                tags.append(rule.tag)
    return mapping


def _rule_entry(catalog: Catalog, tag: ThreatTag, item_id: str):
    for rule in catalog.counter_rules:
        if rule.tag != tag:
            continue
        for entry in rule.entries:
            if entry.item_id == item_id:
                return entry
    return None


# --- starter ---------------------------------------------------------------


def _starter(
    hero: Hero, catalog: Catalog, weights: dict[str, float], scale: _Scale
) -> list[StarterItem]:
    """3–4 Tier-1 items for the lane, ordered by relevance to the hero."""
    tier1 = [i for i in catalog.items if i.tier == 1]
    preferred: list[Item] = []
    for tag in hero.threat_tags:
        for item_id in _STARTER_ANCHORS.get(tag.value, ()):
            item = catalog.item_by_id.get(item_id)
            if item and item.tier == 1 and item not in preferred:
                preferred.append(item)

    def size(item: Item) -> str:
        return "weapon" if item.category is ItemCategory.WEAPON else (
            "vitality" if item.category is ItemCategory.VITALITY else "spirit"
        )

    picked: list[Item] = []
    # 1. role/tag anchors, one per category first — a usable lane spread.
    for item in preferred:
        if len(picked) >= STARTER_MAX:
            break
        if any(size(other) == size(item) for other in picked):
            continue
        picked.append(item)
    # 2. fill by stat affinity, then by generic fallback ids.
    rest = sorted(
        (i for i in tier1 if i not in picked),
        key=lambda i: (-_stat_score(i, weights, scale), i.cost, i.id),
    )
    for item in rest:
        if len(picked) >= STARTER_MIN:
            break
        if any(size(other) == size(item) for other in picked):
            continue
        picked.append(item)
    for item_id in _STARTER_FALLBACK:
        if len(picked) >= STARTER_MIN:
            break
        item = catalog.item_by_id.get(item_id)
        if item and item.tier == 1 and item not in picked:
            picked.append(item)

    return [
        StarterItem(
            item_id=item.id,
            name=item.name,
            name_en=item.name_en,
            category=item.category.value,
            tier=item.tier,
            cost=item.cost,
            reason=_stat_reason(item, weights, scale),
        )
        for item in picked[:STARTER_MAX]
    ]


# --- variants --------------------------------------------------------------


def _variant_slots(items: list[Item]) -> dict[str, list[Optional[str]]]:
    """Order the picks into the 12-per-category board (cheap items first)."""
    groups: dict[str, list[Optional[str]]] = {
        "weapon": [None] * 12,
        "vitality": [None] * 12,
        "spirit": [None] * 12,
    }
    ordered = sorted(items, key=lambda i: (i.cost, i.id))
    for item in ordered:
        slots = groups[item.category.value]
        try:
            slots[slots.index(None)] = item.id
        except ValueError:  # >12 items in one category: cannot happen with limits
            continue
    slots_model = BuildSlots(weapon=groups["weapon"], vitality=groups["vitality"],
                             spirit=groups["spirit"])
    validate_slots(slots_model, {i.id: i for i in ordered})  # sanity: no dup/mismatch
    return slots_model.model_dump()


def _select_items(
    variant: str,
    catalog: Catalog,
    hero: Hero,
    role: Optional[str],
    threats: dict[ThreatTag, float],
    cover: dict[str, list[ThreatTag]],
    counters: list[CounterItem],
    scale: _Scale,
) -> list[Item]:
    """Greedy pick inside the variant's tier budget.

    The "counter" variant is pre-seeded with the advisor's highest-priority
    counter items, so the variant that promises to answer their draft
    provably contains those answers instead of merely favouring tanky stats.
    """
    weights = _weights_for(hero, variant, role)
    limits = _VARIANT_LIMITS[variant]
    anchors = _VARIANT_ANCHORS.get(variant, {})
    counter_scale = _COUNTER_WEIGHT[variant]

    def score(item: Item) -> float:
        bonus = anchors.get(item.id, 0.0)
        presence = sum(threats.get(tag, 1.0) for tag in cover.get(item.id, ()))
        bonus += min(presence, _COUNTER_CAP) * counter_scale
        return _stat_score(item, weights, scale) + bonus

    seed: list[Item] = []
    for counter in counters[:_COUNTER_SEED[variant]]:
        item = catalog.item_by_id[counter.item_id]
        if item.tier <= MAX_ITEM_TIER:
            seed.append(item)

    candidates = [
        i for i in catalog.items if i.tier <= MAX_ITEM_TIER and i.cost > 0
    ]
    candidates.sort(key=lambda i: (-score(i), i.cost, i.id))

    used: dict[str, int] = {"t1": 0, "t2": 0, "t34": 0}
    per_category: dict[str, int] = {"weapon": 0, "vitality": 0, "spirit": 0}
    picked: list[Item] = []
    for item in seed + candidates:
        if len(picked) >= VARIANT_TARGET_ITEMS or item in picked:
            continue
        bucket = _tier_bucket(item.tier)
        if used[bucket] >= limits[bucket]:
            continue
        category = item.category.value
        if per_category[category] >= 12:
            continue
        used[bucket] += 1
        per_category[category] += 1
        picked.append(item)
    return picked


def _variant_rationale(
    variant: str,
    picked: list[Item],
    counters: list[CounterItem],
    stats: dict[str, Any],
    threats: dict[ThreatTag, float],
) -> list[str]:
    names = [i.name for i in picked]
    counter_ids = {c.item_id for c in counters}
    used_counters = [i.name for i in picked if i.id in counter_ids]
    lines: list[str] = []
    if variant == "farm":
        lines.append(
            "Ставка на скейл: урон и темп важнее живучести — "
            f"ядро сборки {', '.join(names[:4])}."
        )
        lines.append(
            f"Целевой урон в секунду {stats['weapon_dps']:.0f}, "
            f"сила духа {stats['spirit_power']:.0f}, "
            f"суммарно {stats['souls_spent']} душ."
        )
    elif variant == "teamfight":
        lines.append(
            "Ставка на файты: живучесть и откат способностей — "
            f"ядро {', '.join(names[:4])}."
        )
        lines.append(
            f"Эффективное HP: против пуль {stats['effective_hp_bullet']:.0f}, "
            f"против спирита {stats['effective_hp_spirit']:.0f}; "
            f"перезарядка -{stats['cooldown_reduction_pct'] * 100:.0f}%."
        )
    else:
        top_tags = sorted(threats, key=lambda t: (-threats[t], t.value))[:3]
        lines.append(
            "Сначала закрываем их угрозы: "
            + (", ".join(drafter_mod._tag_ru(t) for t in top_tags) or "—")
            + "."
        )
        if used_counters:
            lines.append(
                f"Контр-предметы в основе: {', '.join(used_counters[:5])}."
            )
        else:
            lines.append(
                "Явных контр-предметов под их теги нет — берём универсальные ответы."
            )
        lines.append(
            f"Всё равно выходим на {stats['effective_hp_bullet']:.0f} "
            f"эффективного HP против пуль и {stats['weapon_dps']:.0f} урона в секунду."
        )
    if used_counters and variant != "counter":
        lines.append(f"Учтены их теги: {', '.join(used_counters[:4])}.")
    return lines


def _build_variant(
    variant: str,
    hero: Hero,
    catalog: Catalog,
    role: Optional[str],
    threats: dict[ThreatTag, float],
    cover: dict[str, list[ThreatTag]],
    counters: list[CounterItem],
    scale: _Scale,
) -> BuildVariant:
    picked = _select_items(variant, catalog, hero, role, threats, cover, counters, scale)
    stats = calc_mod.compute(hero, picked).model_dump()
    items = [
        VariantItem(
            item_id=i.id,
            name=i.name,
            name_en=i.name_en,
            category=i.category.value,
            tier=i.tier,
            cost=i.cost,
            tier_band=_band_text(i.tier),
        )
        for i in picked
    ]
    by_tier: dict[int, list[str]] = {}
    for item in picked:
        by_tier.setdefault(item.tier, []).append(item.id)
    buy_order = [
        BuyWindow(
            tier=tier,
            cost_range=_band_text(tier),
            window=TIER_WINDOWS[tier],
            item_ids=sorted(ids),
        )
        for tier, ids in sorted(by_tier.items())
    ]
    weights = _weights_for(hero, variant, role)
    return BuildVariant(
        key=variant,
        title=_VARIANT_TITLES[variant],
        summary=(
            f"{len(picked)} предметов · {stats['souls_spent']} душ · "
            f"фокус: {', '.join(_focus_labels(weights))}"
        ),
        item_ids=[i.id for i in picked],
        items=items,
        total_cost=stats["souls_spent"],
        focus=_focus_labels(weights),
        stats=stats,
        slots=_variant_slots(picked),
        buy_order=buy_order,
        rationale=_variant_rationale(variant, picked, counters, stats, threats),
    )


# --- counters / red flags --------------------------------------------------


def _counters(
    hero: Hero, enemies: list[Hero], catalog: Catalog
) -> tuple[list[CounterItem], list[ThreatOut], dict[ThreatTag, float]]:
    advice = advisor_mod.recommend(hero, enemies, catalog.counter_rules, catalog.item_by_id)
    present = {t.tag for t in advice.threats}
    cover = _covered_tags(present, catalog)
    out: list[CounterItem] = []
    seen: set[str] = set()

    for rec in advice.recommendations:
        tags = cover.get(rec.item.id) or [
            t for t in rec.item.counters if t in present
        ]
        out.append(CounterItem(
            item_id=rec.item.id,
            name=rec.item.name,
            name_en=rec.item.name_en,
            category=rec.item.category.value,
            tier=rec.tier,
            cost=rec.cost,
            priority=rec.priority,
            timing=rec.timing,
            covered_tags=[t.value for t in tags],
            explanation=rec.explanation,
        ))
        seen.add(rec.item.id)

    # Guarantee per-tag coverage: the advisor ranks only the top-3 threats,
    # so a lower-ranked tag (e.g. heal behind mobility) still gets its answer.
    rules: dict[ThreatTag, CounterRule] = {r.tag: r for r in catalog.counter_rules}
    for assessment in advice.threats:
        rule = rules.get(assessment.tag)
        if rule is None:
            continue
        if any(assessment.tag.value in c.covered_tags for c in out):
            continue
        entry = rule.entries[0]
        item = catalog.item_by_id.get(entry.item_id)
        if item is None or item.id in seen:
            continue
        out.append(CounterItem(
            item_id=item.id,
            name=item.name,
            name_en=item.name_en,
            category=item.category.value,
            tier=item.tier,
            cost=item.cost,
            priority=entry.priority,
            timing=entry.timing,
            covered_tags=[assessment.tag.value],
            explanation=entry.explanation,
        ))
        seen.add(item.id)

    out = out[:COUNTER_LIMIT]
    threats = {
        t.tag: min(2.0, max(0.6, t.score / 1.5)) for t in advice.threats
    }
    summary = [
        ThreatOut(
            tag=t.tag.value,
            label=drafter_mod._tag_ru(t.tag),
            score=t.score,
            contributors=t.contributors,
        )
        for t in advice.threats[:6]
    ]
    return out, summary, threats


def _timing_hint(variants: list[BuildVariant]) -> TimingHint:
    per_tier: dict[int, set[str]] = {}
    for variant in variants:
        for item in variant.items:
            per_tier.setdefault(item.tier, set()).add(item.item_id)
    bands = [
        BuyWindow(
            tier=tier,
            cost_range=_band_text(tier),
            window=TIER_WINDOWS[tier],
            item_ids=sorted(ids),
        )
        for tier, ids in sorted(per_tier.items())
    ]
    first = min(per_tier) if per_tier else 1
    summary = (
        "Старт (T1, " + _band_text(1) + ") → ядро (T2, " + _band_text(2)
        + ") → мид-игра (T3, " + _band_text(3) + ") → лейт (T4, "
        + _band_text(4) + "). Бери T1 на линии сразу, T2 — к 8–16 мин, "
        "T3/T4 — после первых крупных файтов."
        if first == 1 else "Покупай по возрастанию цены: дешёвые слоты закрывают линию."
    )
    return TimingHint(bands=bands, summary=summary)


# --- public entry point ----------------------------------------------------


def build_plan(
    my_hero_id: str,
    enemy_ids: list[str],
    catalog: Catalog,
    role: Optional[str] = None,
    mode: Optional[str] = None,
) -> DraftBuildPlan:
    """Local build plan for one hero against an enemy draft (no I/O).

    Raises ValueError on rule violations (unknown hero, duplicates, >6 enemies,
    unknown role/mode) — the API layer maps that to 404/422.
    """
    started = time.monotonic()
    resolved_mode = normalize_mode(mode)
    resolved_role = resolve_role(role)

    if len(enemy_ids) > MAX_ENEMIES:
        raise ValueError(f"At most {MAX_ENEMIES} enemy heroes are allowed.")
    if len(enemy_ids) != len(set(enemy_ids)):
        raise ValueError("Enemy list contains duplicate hero ids.")
    if not enemy_ids:
        raise ValueError("At least one enemy hero is required.")
    if my_hero_id in enemy_ids:
        raise ValueError(f"Hero {my_hero_id!r} cannot be both picked and enemy.")

    hero = catalog.by_id.get(my_hero_id)
    if hero is None:
        raise ValueError(f"Unknown hero id: {my_hero_id!r}.")
    enemies: list[Hero] = []
    for hid in enemy_ids:
        enemy = catalog.by_id.get(hid)
        if enemy is None:
            raise ValueError(f"Unknown hero id: {hid!r}.")
        enemies.append(enemy)

    counters, threat_summary, threats = _counters(hero, enemies, catalog)
    drafter = drafter_mod
    analysis = drafter.analyze_draft([my_hero_id], enemy_ids, catalog)
    cover = _covered_tags(set(threats), catalog)
    scale = _scale_for(catalog)

    variants = [
        _build_variant(
            key, hero, catalog, resolved_role, threats, cover, counters, scale
        )
        for key in ("farm", "teamfight", "counter")
    ]

    base_weights = _weights_for(hero, "farm", resolved_role)
    starter = _starter(hero, catalog, base_weights, scale)

    notes = [
        "Локальный движок (advisor/drafter/calculator): без обращения к LLM и сети.",
        "Red Flags считаются только по вашему герою — состав вашей команды в матче неизвестен.",
        f"Предметы T5 (9100+ душ) в быстрые сборки не включаются: это лейт-люкс.",
    ]

    return DraftBuildPlan(
        my_hero={
            "id": hero.id,
            "name": hero.name,
            "name_en": hero.name_en,
            "roles": [r.value for r in hero.roles],
            "threat_tags": [t.value for t in hero.threat_tags],
        },
        enemies=[
            {
                "id": e.id,
                "name": e.name,
                "threat_tags": [t.value for t in e.threat_tags],
            }
            for e in enemies
        ],
        role_hint=resolved_role,
        starter=starter,
        starter_cost=sum(s.cost for s in starter),
        variants=variants,
        counters=counters,
        red_flags=analysis.red_flags,
        threat_summary=threat_summary,
        timing_hint=_timing_hint(variants),
        notes=notes,
        engine=EngineMeta(
            mode=resolved_mode,
            local_ms=round((time.monotonic() - started) * 1000, 1),
        ),
    )


# --- optional LLM enrichment (never on the local path) ---------------------

LLM_NOTES_TIMEOUT = 8.0   # hard cap: the endpoint must stay far below 40 s
LLM_NOTES_MAX_CHARS = 400


def _extract_json(raw: str) -> dict[str, Any] | None:
    """Best-effort JSON object extraction from a model answer."""
    if not raw:
        return None
    import json

    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        parsed = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def llm_notes(plan: DraftBuildPlan, client, timeout: float = LLM_NOTES_TIMEOUT):
    """3 short RU rationales for the variants, or None on any failure.

    Strictly best-effort: the client timeout is cut to `timeout`, every
    exception/timeout returns None and the caller keeps the local result.
    """
    if client is None:
        return None
    payload = {
        "hero": plan.my_hero.get("name"),
        "role": plan.role_hint,
        "threats": [f"{t.label} ({t.score})" for t in plan.threat_summary[:4]],
        "variants": [
            {
                "key": v.key,
                "items": [i.name for i in v.items],
                "cost": v.total_cost,
                "stats": {
                    k: v.stats.get(k)
                    for k in ("weapon_dps", "spirit_power", "effective_hp_bullet")
                },
            }
            for v in plan.variants
        ],
        "counters": [c.name for c in plan.counters[:5]],
    }
    prompt = (
        "Ты тренер по Deadlock. Ниже готовый локальный план сборки. "
        "Верни СТРОГО JSON вида "
        '{"farm": "...", "teamfight": "...", "counter": "..."} — по одному '
        "предложению (до 200 символов) на вариант: почему именно эта сборка "
        "работает в данном матче. Только русский язык, без выдуманных предметов."
    )
    try:
        if hasattr(client, "request_timeout"):
            client.request_timeout = float(timeout)
        raw = client.generate_coach_report(prompt, payload)
    except Exception:
        return None
    parsed = _extract_json(raw)
    if not parsed:
        return None
    notes: dict[str, str] = {}
    for key in ("farm", "teamfight", "counter"):
        value = parsed.get(key)
        if isinstance(value, str) and value.strip():
            notes[key] = value.strip()[:LLM_NOTES_MAX_CHARS]
    return notes or None