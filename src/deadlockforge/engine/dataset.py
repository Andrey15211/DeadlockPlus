"""Load and validate the DeadlockForge dataset from data/*.json.

Interface:
    load_catalog() -> Catalog   (fresh read + validation on every call)
    get_catalog()  -> Catalog   (lru_cache-1 convenience wrapper)

Everything else — JSON parsing, Pydantic validation, cross-reference checks —
is private. Callers only ever see a ready-to-use frozen Catalog.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from pydantic import ValidationError

from ..models import (
    CounterRule,
    Counterpicks,
    Hero,
    Item,
    MapGuide,
    Synergies,
    ThreatTag,
    TIER_COST_BANDS,
)


@dataclass(frozen=True)
class Catalog:
    """Immutable, fully validated dataset snapshot."""

    heroes: list[Hero]
    items: list[Item]
    counter_rules: list[CounterRule]
    map_guide: MapGuide
    by_id: dict[str, Hero]
    item_by_id: dict[str, Item]


def _data_root() -> Path:
    """Data directory, resolved in this order:

    1. DEADLOCK_DATA_DIR env var (set by run_desktop.py before import);
    2. sys._MEIPASS/data (PyInstaller frozen build, bundled by --add-data);
    3. project-root data/ resolved from this file's location
       (engine -> deadlockforge -> src -> root).
    """
    import os
    import sys

    env = os.environ.get("DEADLOCK_DATA_DIR")
    if env:
        return Path(env)
    if hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / "data"
    return Path(__file__).resolve().parents[3] / "data"


def _read_json(path: Path) -> object:
    import sys

    if not path.is_file() and hasattr(sys, "_MEIPASS"):
        bundle_path = Path(sys._MEIPASS) / "data" / path.name
        if bundle_path.is_file():
            path = bundle_path
    if not path.is_file():
        raise FileNotFoundError(
            f"Required data file not found: {path}. Dataset incomplete — "
            "the engine expects heroes.json, items.json, counter_rules.json and "
            "map.json in the data/ directory of the project root."
        )
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in {path}: {exc}") from exc


def _validate_no_duplicate_ids(entities: list, label: str) -> None:
    seen: set[str] = set()
    for entity in entities:
        if entity.id in seen:
            raise ValueError(f"Duplicate {label} id: {entity.id!r}")
        seen.add(entity.id)


def _validate_cost_tiers(items: list[Item]) -> None:
    for item in items:
        low, high = TIER_COST_BANDS[item.tier]
        if not (low <= item.cost <= high):
            raise ValueError(
                f"Item {item.id!r}: cost {item.cost} outside tier {item.tier} "
                f"band [{low}, {high}]."
            )


def _validate_cross_references(
    rules: list[CounterRule], item_by_id: dict[str, Item]
) -> None:
    """Every rule entry resolves to an existing item; every tag has a rule."""
    rule_tags: set[ThreatTag] = set()
    for rule in rules:
        if rule.tag in rule_tags:
            raise ValueError(f"Multiple counter rules for tag {rule.tag.value!r}.")
        rule_tags.add(rule.tag)
        for entry in rule.entries:
            if entry.item_id not in item_by_id:
                raise ValueError(
                    f"Counter rule for tag {rule.tag.value!r} references "
                    f"unknown item id {entry.item_id!r}."
                )

    for tag in ThreatTag:
        if tag not in rule_tags:
            raise ValueError(
                f"No counter rule for threat tag {tag.value!r}: every tag of "
                "the closed vocabulary must have a rule."
            )


def _parse_single(model, payload: object, path: Path, ctx: str):
    try:
        return model.model_validate(payload)
    except ValidationError as exc:
        raise ValueError(
            f"Schema validation failed for {ctx} in {path}:\n{exc}"
        ) from exc


def _parse_list(model, payload: object, path: Path, label: str) -> list:
    if not isinstance(payload, list):
        raise ValueError(f"{path}: expected a JSON array of {label}, got {type(payload).__name__}.")
    parsed = []
    for i, entry in enumerate(payload):
        try:
            parsed.append(model.model_validate(entry))
        except ValidationError as exc:
            raise ValueError(
                f"Schema validation failed for {label}[{i}] in {path}:\n{exc}"
            ) from exc
    return parsed


def load_catalog() -> Catalog:
    """Read, parse and cross-validate the whole dataset.

    Raises FileNotFoundError if a data file is missing, ValueError if any
    file fails schema validation or cross-reference checks.
    """
    root = _data_root()

    heroes_payload = _read_json(root / "heroes.json")
    items_payload = _read_json(root / "items.json")
    rules_payload = _read_json(root / "counter_rules.json")
    map_payload = _read_json(root / "map.json")

    heroes = _parse_list(Hero, heroes_payload, root / "heroes.json", "heroes")
    items = _parse_list(Item, items_payload, root / "items.json", "items")

    rules = _parse_list(
        CounterRule, rules_payload, root / "counter_rules.json", "counter rules"
    )
    map_guide = _parse_single(
        MapGuide, map_payload, root / "map.json", "map guide"
    )

    _validate_no_duplicate_ids(heroes, "hero")
    _validate_no_duplicate_ids(items, "item")

    if not heroes:
        raise ValueError(f"{root / 'heroes.json'}: hero roster is empty.")
    if not items:
        raise ValueError(f"{root / 'items.json'}: item catalog is empty.")

    _validate_cost_tiers(items)

    by_id = {hero.id: hero for hero in heroes}
    item_by_id = {item.id: item for item in items}
    _validate_cross_references(rules, item_by_id)

    return Catalog(
        heroes=heroes,
        items=items,
        counter_rules=rules,
        map_guide=map_guide,
        by_id=by_id,
        item_by_id=item_by_id,
    )


@lru_cache(maxsize=1)
def get_catalog() -> Catalog:
    """Cached catalog accessor — loads (and validates) at most once."""
    return load_catalog()


# --- Контрпики (экран 7) ---------------------------------------------------


def load_counterpicks() -> Counterpicks:
    """Read and validate data/counterpicks.json.

    Отдельный от Catalog датасет: контент экрана «Контрпики» не участвует
    в расчётах билдов, поэтому его загрузка не должна ломать основной каталог.
    """
    payload = _read_json(_data_root() / "counterpicks.json")
    if not isinstance(payload, dict):
        raise ValueError("counterpicks.json: ожидался объект с полями heroes/items.")
    return _parse_single(Counterpicks, payload, _data_root() / "counterpicks.json", "counterpicks")


@lru_cache(maxsize=1)
def get_counterpicks() -> Counterpicks:
    """Cached counterpicks accessor — loads (and validates) at most once."""
    return load_counterpicks()


# --- Синергии (экран «Синергии») -------------------------------------------


def load_synergies() -> Synergies:
    """Read and validate data/synergies.json.

    Датасет самодостаточен: имена и портреты героев уже внутри, поэтому
    экран работает даже если /api/heroes недоступен.
    """
    payload = _read_json(_data_root() / "synergies.json")
    if not isinstance(payload, dict):
        raise ValueError("synergies.json: ожидался объект с полем duos.")
    return _parse_single(Synergies, payload, _data_root() / "synergies.json", "synergies")


@lru_cache(maxsize=1)
def get_synergies() -> Synergies:
    """Cached synergies accessor — loads (and validates) at most once."""
    return load_synergies()
