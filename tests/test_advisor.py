"""Advisor tests: signature scenarios over hand-built fixtures first,
then (when the real dataset has landed) the same scenarios against
data/heroes.json, asserting SPEC's expected counter items.

Covers: heal stacking, uninterruptible ults, bullet DPS, spirit casters,
stacking amplification, empty enemy roster, and ranking rules.
"""

from __future__ import annotations

import os

import pytest

from deadlockforge.engine.advisor import recommend
from deadlockforge.models import (
    CounterEntry,
    CounterRule,
    Hero,
    HeroRole,
    HeroStats,
    Item,
    ItemCategory,
    ItemStats,
    ThreatTag,
)

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DATA_DIR = os.path.join(PROJECT_ROOT, "data")
REQUIRED_DATA_FILES = ("heroes.json", "items.json", "counter_rules.json", "map.json")
HAS_REAL_DATA = all(
    os.path.isfile(os.path.join(DATA_DIR, name)) for name in REQUIRED_DATA_FILES
)


# --- Fixture factory helpers -------------------------------------------------


def make_hero(id: str, tags: list[ThreatTag]) -> Hero:
    return Hero(
        id=id,
        name=id,
        roles=[HeroRole.DUELIST],
        stats=HeroStats(
            hp=600,
            bullet_shield=4,
            spirit_shield=4,
            weapon_damage=28,
            fire_rate=2.0,
            spirit_power=0,
            stamina=3,
            move_speed=6.5,
        ),
        threat_tags=tags,
        signature="fixture hero",
        difficulty=2,
    )


def make_item(id: str, category: ItemCategory = ItemCategory.VITALITY) -> Item:
    return Item(
        id=id,
        name=id,
        name_en=id,
        category=category,
        tier=2,
        cost=1250,
        stats=ItemStats(),
        active=None,
        passive=None,
        counters=[],
    )


def make_rule(tag: ThreatTag, entries: list[tuple[str, int, str]]) -> CounterRule:
    return CounterRule(
        tag=tag,
        entries=[
            CounterEntry(item_id=item_id, priority=priority, explanation=f"{item_id} counters {tag.value}", timing="tier-based timing")
            for item_id, priority, *_ in entries
        ],
    )


def make_rules() -> list[CounterRule]:
    return [
        make_rule(ThreatTag.HEAL, [("healbane", 1), ("toxic_bullets", 2)]),
        make_rule(ThreatTag.UNINTERRUPTIBLE_ULT, [("knockdown", 1), ("curse", 2)]),
        make_rule(ThreatTag.BULLET_DPS, [("metal_skin", 1), ("return_fire", 2)]),
        make_rule(ThreatTag.SPIRIT_CASTER, [("silence_glyph", 1), ("spellbreaker", 2), ("spirit_armor", 3)]),
        make_rule(ThreatTag.BURST, [("burst_item", 1)]),
        make_rule(ThreatTag.MOBILITY, [("mobility_item", 1)]),
        make_rule(ThreatTag.STEALTH, [("stealth_item", 1)]),
        make_rule(ThreatTag.TANK, [("tank_item", 1)]),
        make_rule(ThreatTag.CC_CHAIN, [("cc_item", 1)]),
    ]


def make_items() -> dict[str, Item]:
    ids = [
        "healbane", "toxic_bullets",
        "knockdown", "curse",
        "metal_skin", "return_fire",
        "silence_glyph", "spellbreaker", "spirit_armor",
        "burst_item", "mobility_item", "stealth_item", "tank_item", "cc_item",
    ]
    return {item_id: make_item(item_id) for item_id in ids}


MY_HERO = make_hero("my_hero", [])
FIXTURE_RULES = make_rules()
FIXTURE_ITEMS = make_items()


# --- Hand-built fixture scenarios ---------------------------------------------


def test_heal_stacking_yields_anti_heal_top():
    enemies = [
        make_hero("abrams", [ThreatTag.HEAL]),
        make_hero("kelvin", [ThreatTag.HEAL]),
        make_hero("infernus", [ThreatTag.HEAL, ThreatTag.BURST]),
    ]
    advice = recommend(MY_HERO, enemies, FIXTURE_RULES, FIXTURE_ITEMS)
    # heal score: 1.0 + 1.5 + 2.0 = 4.5 → dominant threat
    assert advice.threats[0].tag == ThreatTag.HEAL
    assert advice.recommendations[0].item.id == "healbane"
    assert advice.recommendations[0].tier == 2
    assert advice.recommendations[0].cost == 1250
    assert advice.recommendations[0].timing


def test_uninterruptible_ult_top_recommendation():
    enemies = [
        make_hero("seven", [ThreatTag.UNINTERRUPTIBLE_ULT]),
        make_hero("mcginnis", [ThreatTag.UNINTERRUPTIBLE_ULT]),
    ]
    advice = recommend(MY_HERO, enemies, FIXTURE_RULES, FIXTURE_ITEMS)
    assert advice.threats[0].tag == ThreatTag.UNINTERRUPTIBLE_ULT
    assert advice.recommendations[0].item.id == "knockdown"
    assert "knockdown" in {r.item.id for r in advice.recommendations}


def test_bullet_dps_top_recommendation():
    enemies = [
        make_hero("haze", [ThreatTag.BULLET_DPS]),
        make_hero("wraith", [ThreatTag.BULLET_DPS]),
    ]
    advice = recommend(MY_HERO, enemies, FIXTURE_RULES, FIXTURE_ITEMS)
    assert advice.threats[0].tag == ThreatTag.BULLET_DPS
    top_ids = [r.item.id for r in advice.recommendations[:2]]
    assert "metal_skin" in top_ids


def test_spirit_caster_top_recommendations():
    enemies = [
        make_hero("pocket", [ThreatTag.SPIRIT_CASTER]),
        make_hero("yamato", [ThreatTag.SPIRIT_CASTER]),
        make_hero("grey_talon", [ThreatTag.SPIRIT_CASTER]),
    ]
    advice = recommend(MY_HERO, enemies, FIXTURE_RULES, FIXTURE_ITEMS)
    assert advice.threats[0].tag == ThreatTag.SPIRIT_CASTER
    rec_ids = {r.item.id for r in advice.recommendations}
    assert {"silence_glyph", "spellbreaker"} <= rec_ids


def test_stacking_same_tag_beats_distinct_tags():
    stacked = [
        make_hero("a", [ThreatTag.BURST]),
        make_hero("b", [ThreatTag.BURST]),
    ]
    distinct = [
        make_hero("c", [ThreatTag.BURST]),
        make_hero("d", [ThreatTag.MOBILITY]),
    ]
    stacked_advice = recommend(MY_HERO, stacked, FIXTURE_RULES, FIXTURE_ITEMS)
    distinct_advice = recommend(MY_HERO, distinct, FIXTURE_RULES, FIXTURE_ITEMS)

    stacked_scores = {t.tag: t.score for t in stacked_advice.threats}
    distinct_scores = {t.tag: t.score for t in distinct_advice.threats}
    assert stacked_scores[ThreatTag.BURST] > distinct_scores[ThreatTag.BURST]
    # 2 stacked instances: 1.0 + 1.5 = 2.5
    assert stacked_scores[ThreatTag.BURST] == pytest.approx(2.5)
    # single instances: 1.0 each
    assert distinct_scores[ThreatTag.BURST] == pytest.approx(1.0)
    assert distinct_scores[ThreatTag.MOBILITY] == pytest.approx(1.0)


def test_empty_enemies_give_empty_advice():
    advice = recommend(MY_HERO, [], FIXTURE_RULES, FIXTURE_ITEMS)
    assert advice.threats == []
    assert advice.recommendations == []


def test_only_top3_threats_drive_recommendations():
    # Four distinct tags with decreasing coverage: 3, 2, 1, 1 heroes.
    enemies = [
        make_hero("a", [ThreatTag.BURST]),
        make_hero("b", [ThreatTag.BURST]),
        make_hero("c", [ThreatTag.BURST]),
        make_hero("d", [ThreatTag.MOBILITY]),
        make_hero("e", [ThreatTag.MOBILITY]),
        make_hero("f", [ThreatTag.STEALTH]),
        make_hero("g", [ThreatTag.TANK]),
    ]
    advice = recommend(MY_HERO, enemies, FIXTURE_RULES, FIXTURE_ITEMS)
    # All four tags are reported as threats, but only the top 3 matter.
    assert len(advice.threats) == 4
    rec_ids = {r.item.id for r in advice.recommendations}
    # tank_item's tag is 4th → must NOT appear via a recommendation.
    assert "tank_item" not in rec_ids
    assert {"burst_item", "mobility_item"} <= rec_ids


def test_aggregation_deduplicates_and_sums():
    # Item countering two top threats must accumulate scores.
    rules = [
        make_rule(ThreatTag.BURST, [("universal", 1), ("burst_only", 2)]),
        make_rule(ThreatTag.MOBILITY, [("universal", 2)]),
    ] + [r for r in make_rules() if r.tag not in (ThreatTag.BURST, ThreatTag.MOBILITY)]
    items = dict(FIXTURE_ITEMS)
    items["universal"] = make_item("universal")

    enemies = [
        make_hero("a", [ThreatTag.BURST, ThreatTag.MOBILITY]),
        make_hero("b", [ThreatTag.BURST, ThreatTag.MOBILITY]),
    ]
    advice = recommend(MY_HERO, enemies, rules, items)
    # burst: 2.5, mobility: 2.5 → universal scores 1*2.5 + 0.5*2.5 = 3.75;
    # burst_only scores 0.5 * 2.5 = 1.25 → universal must rank first.
    assert advice.recommendations[0].item.id == "universal"
    assert "multiple threats" in advice.recommendations[0].explanation.lower()


def test_recommendations_capped_at_six():
    enemies = [
        make_hero("a", [ThreatTag.SPIRIT_CASTER, ThreatTag.HEAL]),
        make_hero("b", [ThreatTag.SPIRIT_CASTER, ThreatTag.BULLET_DPS]),
        make_hero("c", [ThreatTag.SPIRIT_CASTER, ThreatTag.BURST]),
    ]
    advice = recommend(MY_HERO, enemies, FIXTURE_RULES, FIXTURE_ITEMS)
    assert len(advice.recommendations) <= 6


def test_tie_break_by_tier_then_cost():
    low_tier = make_item("low_tier")
    low_tier = low_tier.model_copy(update={"tier": 1, "cost": 500})
    high_tier = make_item("high_tier")
    high_tier = high_tier.model_copy(update={"tier": 3, "cost": 3000})
    items = dict(FIXTURE_ITEMS)
    items["low_tier"] = low_tier
    items["high_tier"] = high_tier
    rules = [
        CounterRule(
            tag=ThreatTag.BURST,
            entries=[
                CounterEntry(item_id="low_tier", priority=1, explanation="e1", timing="t"),
                CounterEntry(item_id="high_tier", priority=1, explanation="e2", timing="t"),
            ],
        ),
    ] + [r for r in make_rules() if r.tag != ThreatTag.BURST]
    enemies = [make_hero("a", [ThreatTag.BURST])]
    advice = recommend(MY_HERO, enemies, rules, items)
    assert advice.recommendations[0].item.id == "low_tier"


# --- Real-data scenarios (only when the dataset has landed) -------------------


@pytest.mark.skipif(not HAS_REAL_DATA, reason="data/heroes.json not present yet")
class TestRealDataScenarios:
    """Re-run the four signature scenarios against the real catalog."""

    @pytest.fixture()
    def catalog(self):
        from deadlockforge.engine.dataset import load_catalog

        return load_catalog()

    def _advice(self, catalog, enemy_ids: list[str]):
        enemies = [catalog.by_id[e] for e in enemy_ids]
        return recommend(
            catalog.heroes[0], enemies, catalog.counter_rules, catalog.item_by_id
        )

    def test_real_heal_scenario(self, catalog):
        advice = self._advice(catalog, ["abrams", "kelvin", "infernus"])
        assert advice.threats[0].tag == ThreatTag.HEAL
        rec_names = {r.item.name_en for r in advice.recommendations[:3]}
        assert "Healbane" in rec_names

    def test_real_uninterruptible_scenario(self, catalog):
        advice = self._advice(catalog, ["seven", "mcginnis"])
        assert advice.threats[0].tag == ThreatTag.UNINTERRUPTIBLE_ULT
        rec_names = {r.item.name_en for r in advice.recommendations[:3]}
        assert "Knockdown" in rec_names

    def test_real_bullet_dps_scenario(self, catalog):
        advice = self._advice(catalog, ["haze", "wraith"])
        assert advice.threats[0].tag == ThreatTag.BULLET_DPS
        rec_names = {r.item.name_en for r in advice.recommendations[:3]}
        assert "Metal Skin" in rec_names

    def test_real_spirit_caster_scenario(self, catalog):
        advice = self._advice(catalog, ["pocket", "yamato", "grey-talon"])
        # pocket/yamato/grey-talon each carry both spirit_caster and burst:
        # the two tags tie at 4.5, so either may head the ranking — the
        # requirement is that the spirit rule's top items are recommended.
        spirit_score = {t.tag: t.score for t in advice.threats}[ThreatTag.SPIRIT_CASTER]
        assert spirit_score == pytest.approx(4.5)
        assert advice.threats[0].tag in (ThreatTag.SPIRIT_CASTER, ThreatTag.BURST)
        rec_names = {r.item.name_en for r in advice.recommendations[:4]}
        assert rec_names & {"Silence Glyph", "Spellbreaker"}
