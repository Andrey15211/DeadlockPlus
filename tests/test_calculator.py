"""Calculator tests: hand-computed math, CDR cap, multiplicative fire rate,
base stats with no items, 2-decimal rounding.
"""

from __future__ import annotations

import pytest

from deadlockforge.engine.calculator import compute
from deadlockforge.models import Hero, HeroRole, HeroStats, Item, ItemCategory, ItemStats

CDR_CAP = 0.55


def make_hero() -> Hero:
    return Hero(
        id="test-hero",
        name="Test Hero",
        roles=[HeroRole.DUELIST],
        stats=HeroStats(
            hp=640.0,
            bullet_shield=6.0,
            spirit_shield=6.0,
            weapon_damage=32.0,
            fire_rate=2.5,
            spirit_power=12.0,
            stamina=3,
            move_speed=6.75,
        ),
        threat_tags=[],
        signature="fixture",
        difficulty=1,
    )


def make_item(id: str, stats: ItemStats, tier: int = 2, cost: int = 1250,
              category: ItemCategory = ItemCategory.WEAPON) -> Item:
    return Item(
        id=id,
        name=id,
        name_en=id,
        category=category,
        tier=tier,
        cost=cost,
        stats=stats,
        active=None,
        passive=None,
        counters=[],
    )


def test_known_items_exact_snapshot():
    """3 known items, every field hand-computed."""
    hero = make_hero()
    items = [
        make_item("dmg1", ItemStats(weapon_damage=12.0), cost=500, tier=1),
        make_item("rate1", ItemStats(fire_rate_pct=0.12, weapon_damage=6.0), cost=1250, tier=2),
        make_item("tank1", ItemStats(base_hp=220.0, bullet_shield=8.0, spirit_shield=5.0,
                                     stamina=1, cooldown_reduction_pct=0.10,
                                     spirit_power=15.0, move_speed=0.5),
                  cost=1250, tier=2, category=ItemCategory.VITALITY),
    ]
    snap = compute(hero, items)

    # weapon_damage: 32 + 12 + 6 = 50
    assert snap.weapon_damage == pytest.approx(50.0)
    # fire_rate: 2.5 * (1 + 0.12) = 2.8
    assert snap.fire_rate == pytest.approx(2.8)
    # weapon_dps: 50 * 2.8 = 140
    assert snap.weapon_dps == pytest.approx(140.0)
    # hp: 640 + 220 = 860
    assert snap.hp == pytest.approx(860.0)
    # bullet_shield: 6 + 8 = 14
    assert snap.bullet_shield == pytest.approx(14.0)
    # spirit_shield: 6 + 5 = 11
    assert snap.spirit_shield == pytest.approx(11.0)
    # spirit_power: 12 + 15 = 27
    assert snap.spirit_power == pytest.approx(27.0)
    # stamina: 3 + 1 = 4 (int)
    assert snap.stamina == 4
    assert isinstance(snap.stamina, int)
    # move_speed: 6.75 + 0.5 = 7.25
    assert snap.move_speed == pytest.approx(7.25)
    # CDR: 0.10 (under cap)
    assert snap.cooldown_reduction_pct == pytest.approx(0.10)
    # souls: 500 + 1250 + 1250 = 3000
    assert snap.souls_spent == 3000


def test_cdr_capped_at_55_percent():
    hero = make_hero()
    items = [
        make_item("cdr1", ItemStats(cooldown_reduction_pct=0.30), category=ItemCategory.SPIRIT),
        make_item("cdr2", ItemStats(cooldown_reduction_pct=0.40), category=ItemCategory.SPIRIT),
    ]
    snap = compute(hero, items)
    # 0.30 + 0.40 = 0.70 → capped at 0.55
    assert snap.cooldown_reduction_pct == pytest.approx(CDR_CAP)


def test_cdr_exactly_at_cap_is_kept():
    hero = make_hero()
    items = [
        make_item("cdr1", ItemStats(cooldown_reduction_pct=0.25), category=ItemCategory.SPIRIT),
        make_item("cdr2", ItemStats(cooldown_reduction_pct=0.30), category=ItemCategory.SPIRIT),
    ]
    snap = compute(hero, items)
    assert snap.cooldown_reduction_pct == pytest.approx(0.55)


def test_fire_rate_multiplicative():
    hero = make_hero()  # base fire_rate 2.5
    items = [
        make_item("fr1", ItemStats(fire_rate_pct=0.10), category=ItemCategory.WEAPON),
        make_item("fr2", ItemStats(fire_rate_pct=0.15), category=ItemCategory.WEAPON),
    ]
    snap = compute(hero, items)
    # 2.5 * (1 + 0.10 + 0.15) = 2.5 * 1.25 = 3.125 → 3.12 (banker's rounding)
    assert snap.fire_rate == pytest.approx(3.12)
    assert snap.weapon_dps == pytest.approx(round(32.0 * 3.125, 2))


def test_empty_items_return_base_stats():
    hero = make_hero()
    snap = compute(hero, [])
    assert snap.hp == pytest.approx(640.0)
    assert snap.bullet_shield == pytest.approx(6.0)
    assert snap.spirit_shield == pytest.approx(6.0)
    assert snap.weapon_damage == pytest.approx(32.0)
    assert snap.fire_rate == pytest.approx(2.5)
    assert snap.weapon_dps == pytest.approx(80.0)  # 32 * 2.5
    assert snap.spirit_power == pytest.approx(12.0)
    assert snap.stamina == 3
    assert snap.move_speed == pytest.approx(6.75)
    assert snap.cooldown_reduction_pct == pytest.approx(0.0)
    assert snap.souls_spent == 0


def test_ehp_with_items_hp_plus_shield():
    """Фаза 3: щит — отдельный пул поглощения, EHP = hp + щит.

    Фикстура: hp 640 + 220 (base_hp) = 860;
    bullet_shield 6 + 8 = 14 → EHP_bullet = 860 + 14 = 874;
    spirit_shield 6 + 5 = 11 → EHP_spirit = 860 + 11 = 871.
    """
    hero = make_hero()
    items = [
        make_item("tank1", ItemStats(base_hp=220.0, bullet_shield=8.0, spirit_shield=5.0),
                  cost=1250, tier=2, category=ItemCategory.VITALITY),
    ]
    snap = compute(hero, items)
    assert snap.effective_hp_bullet == pytest.approx(874.0)
    assert snap.effective_hp_spirit == pytest.approx(871.0)
    # EHP строго больше hp: щит добавляет бюджет против каждого типа урона
    assert snap.effective_hp_bullet == pytest.approx(snap.hp + snap.bullet_shield)
    assert snap.effective_hp_spirit == pytest.approx(snap.hp + snap.spirit_shield)


def test_ehp_multiple_shield_items_sum():
    """bullet_shield и spirit_shield суммируются по всем предметам.

    hp 640 + 0 = 640; bullet 6 + 8 + 12 = 26 → EHP_bullet = 666;
    spirit 6 + 5 + 7 = 18 → EHP_spirit = 658.
    """
    hero = make_hero()
    items = [
        make_item("v1", ItemStats(bullet_shield=8.0, spirit_shield=5.0),
                  cost=1250, tier=2, category=ItemCategory.VITALITY),
        make_item("v2", ItemStats(bullet_shield=12.0, spirit_shield=7.0),
                  cost=1250, tier=2, category=ItemCategory.VITALITY),
    ]
    snap = compute(hero, items)
    assert snap.effective_hp_bullet == pytest.approx(666.0)
    assert snap.effective_hp_spirit == pytest.approx(658.0)


def test_ehp_empty_items_base_values():
    """Фаза 3: без предметов EHP = базовые hp + базовый щит (646 / 646)."""
    hero = make_hero()
    snap = compute(hero, [])
    assert snap.effective_hp_bullet == pytest.approx(640.0 + 6.0)
    assert snap.effective_hp_spirit == pytest.approx(640.0 + 6.0)


def test_ehp_rounded_to_two_decimals():
    """Фаза 3: EHP округляется до 2 знаков.

    hp 640 + 0.371 = 640.371; bullet 6 + 0.123 = 6.123;
    EHP_bullet = 646.494 → 646.49; spirit 6 + 0.037 = 6.037;
    EHP_spirit = 646.408 → 646.41.
    """
    hero = make_hero()
    items = [
        make_item("frac", ItemStats(base_hp=0.371, bullet_shield=0.123, spirit_shield=0.037),
                  cost=0, tier=1, category=ItemCategory.VITALITY),
    ]
    snap = compute(hero, items)
    assert snap.effective_hp_bullet == pytest.approx(646.49)
    assert snap.effective_hp_spirit == pytest.approx(646.41)
    for field in ("effective_hp_bullet", "effective_hp_spirit"):
        value = getattr(snap, field)
        assert value == round(value, 2), f"{field} not rounded to 2dp: {value}"


def test_ehp_fields_present_and_non_negative():
    """Поля EHP есть в снимке и неотрицательны при любых валидных входах."""
    hero = make_hero()
    snap = compute(hero, [])
    for field in ("effective_hp_bullet", "effective_hp_spirit"):
        assert hasattr(snap, field), f"StatsSnapshot missing {field}"
        assert getattr(snap, field) >= 0, f"{field} must be non-negative"

    # с предметами тоже неотрицательны
    items = [
        make_item("tank1", ItemStats(base_hp=220.0, bullet_shield=8.0, spirit_shield=5.0),
                  cost=1250, tier=2, category=ItemCategory.VITALITY),
    ]
    snap = compute(hero, items)
    assert snap.effective_hp_bullet >= 0
    assert snap.effective_hp_spirit >= 0


def test_rounding_to_two_decimals():
    hero = make_hero()
    items = [
        make_item("odd", ItemStats(weapon_damage=0.111, fire_rate_pct=0.037), cost=0, tier=1),
    ]
    snap = compute(hero, items)
    # weapon_damage: 32 + 0.111 = 32.111 → 32.11
    assert snap.weapon_damage == 32.11
    # fire_rate: 2.5 * 1.037 = 2.5925 → 2.59
    assert snap.fire_rate == 2.59
    # every float field has at most 2 decimals
    for field in ("hp", "bullet_shield", "spirit_shield", "weapon_damage",
                  "fire_rate", "weapon_dps", "spirit_power", "move_speed",
                  "cooldown_reduction_pct"):
        value = getattr(snap, field)
        assert value == round(value, 2), f"{field} not rounded to 2dp: {value}"
