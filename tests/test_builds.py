"""Build slot tests: 36 slots (3x12), category enforcement, duplicates,
index bounds (0..11), unequip, upgrade swapping, functional purity.
"""

from __future__ import annotations

import pytest

from deadlockforge.engine.builds import (
    equip,
    equip_with_upgrade,
    find_component_slot,
    unequip,
    validate,
)
from deadlockforge.models import BuildError, BuildSlots, Item, ItemCategory, ItemStats


def make_item(id: str, category: ItemCategory, tier: int = 2, cost: int = 1250, components: list[str] | None = None) -> Item:
    return Item(
        id=id,
        name=id,
        name_en=id,
        category=category,
        tier=tier,
        cost=cost,
        stats=ItemStats(),
        active=None,
        passive=None,
        counters=[],
        components=components or [],
        upgrade_from=components[0] if components else None,
    )


WEAPON_ITEM = make_item("weapon_item", ItemCategory.WEAPON)
VITALITY_ITEM = make_item("vitality_item", ItemCategory.VITALITY)
SPIRIT_ITEM = make_item("spirit_item", ItemCategory.SPIRIT)
ITEMS_BY_ID = {item.id: item for item in (WEAPON_ITEM, VITALITY_ITEM, SPIRIT_ITEM)}


def test_equip_weapon_into_weapon_slot():
    slots = equip(BuildSlots(), WEAPON_ITEM, "weapon", 0)
    assert slots.weapon[0] == "weapon_item"
    validate(slots, ITEMS_BY_ID)  # must not raise


def test_category_mismatch_raises_build_error():
    with pytest.raises(BuildError):
        equip(BuildSlots(), VITALITY_ITEM, "weapon", 0)


def test_validate_category_mismatch_raises():
    slots = BuildSlots(weapon=[VITALITY_ITEM.id] + [None] * 11)
    with pytest.raises(BuildError):
        validate(slots, ITEMS_BY_ID)


def test_duplicate_across_groups_raises():
    slots = BuildSlots(weapon=[WEAPON_ITEM.id] + [None] * 11)
    with pytest.raises(BuildError):
        equip(slots, WEAPON_ITEM, "weapon", 1)


def test_index_out_of_range_raises():
    with pytest.raises(BuildError):
        equip(BuildSlots(), WEAPON_ITEM, "weapon", 12)
    with pytest.raises(BuildError):
        equip(BuildSlots(), WEAPON_ITEM, "weapon", -1)
    with pytest.raises(BuildError):
        unequip(BuildSlots(), "spirit", 15)


def test_invalid_slot_group_raises():
    with pytest.raises(BuildError):
        equip(BuildSlots(), WEAPON_ITEM, "banana", 0)
    with pytest.raises(BuildError):
        equip(BuildSlots(), WEAPON_ITEM, "flex", 0)
    with pytest.raises(BuildError):
        unequip(BuildSlots(), "orange", 0)


def test_unequip_clears_slot():
    slots = BuildSlots(weapon=[WEAPON_ITEM.id] + [None] * 11)
    cleared = unequip(slots, "weapon", 0)
    assert cleared.weapon[0] is None
    validate(cleared, ITEMS_BY_ID)  # must not raise


def test_unequip_empty_slot_is_noop():
    slots = BuildSlots()
    cleared = unequip(slots, "spirit", 3)
    assert cleared.spirit[3] is None


def test_validate_unknown_item_id_raises():
    slots = BuildSlots(weapon=["ghost_item"] + [None] * 11)
    with pytest.raises(BuildError):
        validate(slots, ITEMS_BY_ID)


def test_equip_is_functional_original_unchanged():
    original = BuildSlots(weapon=[WEAPON_ITEM.id] + [None] * 11)
    snapshot = original.model_dump()
    result = equip(original, SPIRIT_ITEM, "spirit", 0)
    assert result.spirit[0] == SPIRIT_ITEM.id
    assert original == BuildSlots(**snapshot)
    assert original.spirit[0] is None
    assert original.weapon[0] == WEAPON_ITEM.id


def test_unequip_is_functional_original_unchanged():
    original = BuildSlots(weapon=[WEAPON_ITEM.id] + [None] * 11)
    snapshot = original.model_dump()
    result = unequip(original, "weapon", 0)
    assert result.weapon[0] is None
    assert original == BuildSlots(**snapshot)
    assert original.weapon[0] == WEAPON_ITEM.id


def test_equip_overwrites_own_slot_no_duplicate_error():
    slots = BuildSlots(weapon=[WEAPON_ITEM.id] + [None] * 11)
    other_weapon = make_item("other_weapon", ItemCategory.WEAPON)
    items = dict(ITEMS_BY_ID)
    items[other_weapon.id] = other_weapon
    result = equip(slots, other_weapon, "weapon", 0)
    assert result.weapon[0] == "other_weapon"
    validate(result, items)  # must not raise


def test_upgrade_swapping_replaces_component_in_place():
    """Интеллектуальная замена улучшений (Upgrade Swapping).

    При клике на Т2 («weapon_t2»), если экипирован его компонент Т1 («weapon_t1»),
    Т1 автоматически заменяется Т2 в том же слоте, а души увеличиваются на разницу.
    """
    t1_item = make_item("weapon_t1", ItemCategory.WEAPON, tier=1, cost=500)
    t2_item = make_item("weapon_t2", ItemCategory.WEAPON, tier=2, cost=1250, components=["weapon_t1"])

    items = {t1_item.id: t1_item, t2_item.id: t2_item}

    # Экипируем Т1 в слот 3
    slots = BuildSlots()
    slots = equip(slots, t1_item, "weapon", 3)
    assert slots.weapon[3] == "weapon_t1"

    # Экипируем Т2 через equip_with_upgrade
    new_slots, slot_idx, cost_diff = equip_with_upgrade(slots, t2_item, items)

    # Проверяем, что Т2 встал ровно в слот 3, заменив Т1
    assert slot_idx == 3
    assert new_slots.weapon[3] == "weapon_t2"
    # Т1 больше нигде нет
    assert "weapon_t1" not in new_slots.all_item_ids()
    # Разница стоимости: 1250 - 500 = 750
    assert cost_diff == 750
    validate(new_slots, items)
