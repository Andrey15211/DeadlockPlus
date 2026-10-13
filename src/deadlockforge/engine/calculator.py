"""Build stat calculator: hero + equipped items → StatsSnapshot.

Interface:
    compute(hero, items) -> StatsSnapshot
    effective_hp(hp, shield) -> float

Pure function: additive aggregation for weapon_damage, base_hp, bullet_shield,
spirit_shield, stamina, spirit_power, move_speed; multiplicative fire rate;
cooldown reduction summed and capped at CDR_CAP (0.55). All floats rounded
to 2 decimals. Effective HP (hp + shield) follows the Deadlock separate-pool
shield model — see effective_hp().
"""

from __future__ import annotations

from ..models import CDR_CAP, Hero, Item, StatsSnapshot


def _round2(value: float) -> float:
    return round(value, 2)


def effective_hp(hp: float, shield: float) -> float:
    """Effective HP against a single damage type.

    Deadlock model: bullet_shield / spirit_shield are separate HP pools that
    absorb damage of the matching type point-for-point — each point of shield
    soaks exactly one point of bullet (resp. spirit) damage before the raw HP
    pool is touched. The pools do not regenerate mid-fight and do not stack
    multiplicatively with hp, so the effective HP budget against one damage
    type is simply additive:

        effective_hp = hp + shield

    Arguments:
        hp: raw health pool (>= 0).
        shield: bullet or spirit shield pool (>= 0).

    Returns:
        The additive effective HP, unrounded.
    """
    return hp + shield


def compute(hero: Hero, items: list[Item]) -> StatsSnapshot:
    """Aggregate hero base stats with item modifiers."""
    base = hero.stats

    weapon_damage = base.weapon_damage
    fire_rate_pct = 0.0
    hp_bonus = 0.0
    bullet_shield = base.bullet_shield
    spirit_shield = base.spirit_shield
    stamina = base.stamina
    cdr = 0.0
    spirit_power = base.spirit_power
    move_speed = base.move_speed
    souls_spent = 0

    for item in items:
        stats = item.stats
        weapon_damage += stats.weapon_damage
        fire_rate_pct += stats.fire_rate_pct
        hp_bonus += stats.base_hp
        bullet_shield += stats.bullet_shield
        spirit_shield += stats.spirit_shield
        stamina += stats.stamina
        cdr += stats.cooldown_reduction_pct
        spirit_power += stats.spirit_power
        move_speed += stats.move_speed
        souls_spent += item.cost

    fire_rate = base.fire_rate * (1.0 + fire_rate_pct)
    weapon_dps = weapon_damage * fire_rate
    cdr = min(cdr, CDR_CAP)

    hp_total = base.hp + hp_bonus
    return StatsSnapshot(
        hp=_round2(hp_total),
        bullet_shield=_round2(bullet_shield),
        spirit_shield=_round2(spirit_shield),
        effective_hp_bullet=_round2(effective_hp(hp_total, bullet_shield)),
        effective_hp_spirit=_round2(effective_hp(hp_total, spirit_shield)),
        weapon_damage=_round2(weapon_damage),
        fire_rate=_round2(fire_rate),
        weapon_dps=_round2(weapon_dps),
        spirit_power=_round2(spirit_power),
        stamina=stamina,
        move_speed=_round2(move_speed),
        cooldown_reduction_pct=_round2(cdr),
        souls_spent=souls_spent,
    )
