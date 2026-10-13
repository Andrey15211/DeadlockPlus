"""Build slot management: equip / unequip / validate on a 36-slot board (3x12).

Interface:
    equip(slots, item, slot_group, slot_index)   -> BuildSlots (new)
    unequip(slots, slot_group, slot_index)       -> BuildSlots (new)
    validate(slots, items_by_id)                 -> None (raises BuildError)
    find_component_slot(slots, item, items_by_id) -> tuple[str, int, str] | None
    equip_with_upgrade(slots, item, items_by_id) -> tuple[BuildSlots, int, int]

36 slots total: 12 weapon, 12 vitality, 12 spirit.
Flex column removed. Upgrade swapping replaces components in-place.
"""

from __future__ import annotations

from typing import Optional

from ..models import BuildError, BuildSlots, Item, ItemCategory

SLOT_GROUPS: tuple[str, ...] = ("weapon", "vitality", "spirit")
SLOTS_PER_GROUP = 12


def _checked_group(slots: BuildSlots, slot_group: str) -> list[Optional[str]]:
    """Return the group's slot list, raising BuildError on a bad group name."""
    if slot_group not in SLOT_GROUPS:
        raise BuildError(
            f"Invalid slot group {slot_group!r}: expected one of {', '.join(SLOT_GROUPS)}."
        )
    return getattr(slots, slot_group)


def _checked_index(group_slots: list[Optional[str]], slot_group: str, slot_index: int) -> None:
    if not 0 <= slot_index < len(group_slots):
        raise BuildError(
            f"Slot index {slot_index} out of range for group {slot_group!r} "
            f"(0..{len(group_slots) - 1})."
        )


def validate(slots: BuildSlots, items_by_id: dict[str, Item]) -> None:
    """Check the whole 36-slot board; raise BuildError on any violation."""
    seen: dict[str, str] = {}
    for group in SLOT_GROUPS:
        group_slots = getattr(slots, group)
        if len(group_slots) != SLOTS_PER_GROUP:
            raise BuildError(
                f"Slot group {group!r} must have exactly {SLOTS_PER_GROUP} slots, "
                f"got {len(group_slots)}."
            )
        for index, item_id in enumerate(group_slots):
            if item_id is None:
                continue
            if item_id not in items_by_id:
                raise BuildError(f"Unknown item id in slot {group}[{index}]: {item_id!r}.")
            item = items_by_id[item_id]
            if item.category != ItemCategory(group):
                raise BuildError(
                    f"Slot {group}[{index}] holds {item_id!r} "
                    f"(category {item.category.value}) — only "
                    f"{group} items fit there."
                )
            if item_id in seen:
                raise BuildError(
                    f"Duplicate item {item_id!r}: occupies both {seen[item_id]} "
                    f"and {group}[{index}]."
                )
            seen[item_id] = f"{group}[{index}]"

    # Support legacy flex slots if present in payload
    flex_slots = getattr(slots, "flex", []) or []
    for index, item_id in enumerate(flex_slots):
        if item_id is None:
            continue
        if item_id not in items_by_id:
            raise BuildError(f"Unknown item id in slot flex[{index}]: {item_id!r}.")
        if item_id in seen:
            raise BuildError(
                f"Duplicate item {item_id!r}: occupies both {seen[item_id]} "
                f"and flex[{index}]."
            )
        seen[item_id] = f"flex[{index}]"


def find_component_slot(
    slots: BuildSlots, item: Item, items_by_id: dict[str, Item] | None = None
) -> tuple[str, int, str] | None:
    """Check if an item that is a component of `item` is already equipped in its category.

    Returns (slot_group, slot_index, component_id) if found, else None.
    """
    comp_ids: set[str] = set()
    if getattr(item, "components", None):
        comp_ids.update(item.components)
    if getattr(item, "upgrade_from", None):
        comp_ids.add(item.upgrade_from)

    if not comp_ids:
        return None

    group = item.category.value
    group_slots = getattr(slots, group, [])
    for idx, equipped_id in enumerate(group_slots):
        if equipped_id and equipped_id in comp_ids:
            return group, idx, equipped_id
    return None


def equip(
    slots: BuildSlots,
    item: Item,
    slot_group: str,
    slot_index: int,
) -> BuildSlots:
    """Return a new BuildSlots with `item` placed at the given slot."""
    group_slots = _checked_group(slots, slot_group)
    _checked_index(group_slots, slot_group, slot_index)

    if item.category != ItemCategory(slot_group):
        raise BuildError(
            f"Cannot equip {item.id!r} (category {item.category.value}) into "
            f"{slot_group}[{slot_index}]: category mismatch."
        )

    new_group = list(group_slots)
    new_group[slot_index] = item.id
    new_slots = slots.model_copy(
        update={slot_group: new_group}, deep=True
    )

    # Duplicate check on the board.
    ids = new_slots.all_item_ids()
    if len(ids) != len(set(ids)):
        raise BuildError(
            f"Item {item.id!r} is already equipped in another slot."
        )
    return new_slots


def equip_with_upgrade(
    slots: BuildSlots, item: Item, items_by_id: dict[str, Item]
) -> tuple[BuildSlots, int, int]:
    """Intelligent upgrade equipping (Upgrade Swapping).

    If a component is equipped in the same category:
        Replaces component in that slot;
        Returns (new_slots, slot_index, soul_cost_diff = item.cost - comp.cost).
    Else:
        Places into first available slot;
        Returns (new_slots, slot_index, soul_cost_diff = item.cost).
    """
    comp_match = find_component_slot(slots, item, items_by_id)
    if comp_match is not None:
        group, idx, comp_id = comp_match
        comp_item = items_by_id.get(comp_id)
        comp_cost = comp_item.cost if comp_item else 0
        cost_diff = max(0, item.cost - comp_cost)

        group_slots = list(getattr(slots, group))
        group_slots[idx] = item.id
        new_slots = slots.model_copy(update={group: group_slots}, deep=True)
        return new_slots, idx, cost_diff

    # No component equipped: find first free slot in category
    group = item.category.value
    group_slots = list(getattr(slots, group))
    try:
        idx = group_slots.index(None)
    except ValueError:
        raise BuildError(f"No free slots in category {group!r} (12/12 filled).")

    group_slots[idx] = item.id
    new_slots = slots.model_copy(update={group: group_slots}, deep=True)
    return new_slots, idx, item.cost


def unequip(slots: BuildSlots, slot_group: str, slot_index: int) -> BuildSlots:
    """Return a new BuildSlots with the given slot cleared."""
    group_slots = _checked_group(slots, slot_group)
    _checked_index(group_slots, slot_group, slot_index)

    new_group = list(group_slots)
    new_group[slot_index] = None
    return slots.model_copy(update={slot_group: new_group}, deep=True)
