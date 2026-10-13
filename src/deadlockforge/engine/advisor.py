"""Counter-Item Advisor: enemy roster → prioritized counter-buy advice.

Interface:
    recommend(my_hero, enemies, rules, items_by_id) -> Advice

Scoring (pure, no globals, no I/O):
    threat score per tag = sum over enemy heroes carrying the tag of
    (1.0 + 0.5 * (instances_after_the_first)). Tags with score > 0 are
    ranked descending; the top 3 are reported and used for recommendations.
"""

from __future__ import annotations

from ..models import (
    Advice,
    CounterRule,
    Hero,
    Item,
    Recommendation,
    ThreatAssessment,
    ThreatTag,
)

TOP_N_THREATS = 3
MAX_RECOMMENDATIONS = 6
STACKING_BONUS = 0.5           # per extra hero sharing the tag after the first
TAG_WEIGHT = 1.0               # per hero instance of a tag


def _assess_threats(enemies: list[Hero]) -> list[ThreatAssessment]:
    """Score every tag present in the enemy roster, ranked descending."""
    contributors: dict[ThreatTag, list[str]] = {}
    for hero in enemies:
        for tag in hero.threat_tags:
            contributors.setdefault(tag, []).append(hero.id)

    assessments: list[ThreatAssessment] = []
    for tag, hero_ids in contributors.items():
        instances = len(hero_ids)
        score = TAG_WEIGHT * sum(1.0 + STACKING_BONUS * i for i in range(instances))
        assessments.append(
            ThreatAssessment(tag=tag, score=round(score, 2), contributors=hero_ids)
        )

    # Rank: score desc, then tag value for deterministic ties.
    assessments.sort(key=lambda a: (-a.score, a.tag.value))
    return assessments


def recommend(
    my_hero: Hero,
    enemies: list[Hero],
    rules: list[CounterRule],
    items_by_id: dict[str, Item],
) -> Advice:
    """Produce counter-buy advice for `my_hero` against `enemies`.

    All inputs are explicit — the function is fully testable without any
    data files. If `enemies` is empty, returns Advice with empty lists.
    """
    if not enemies:
        return Advice(threats=[], recommendations=[])

    threats = _assess_threats(enemies)
    top_threats = threats[:TOP_N_THREATS]
    rules_by_tag = {rule.tag: rule for rule in rules}

    # Aggregate item scores across the top threats: each contributing entry
    # adds (1 / priority) * threat_score; an item countering several threats
    # accumulates the sum.
    item_scores: dict[str, float] = {}
    item_covered: dict[str, list[ThreatTag]] = {}
    entry_by_item: dict[str, tuple[CounterRule, object]] = {}

    for rank, threat in enumerate(top_threats, start=1):
        rule = rules_by_tag.get(threat.tag)
        if rule is None:
            continue
        for entry in rule.entries:
            if entry.item_id not in items_by_id:
                continue
            weight = 1.0 / entry.priority  # priority 1 → weight 1.0
            item_scores[entry.item_id] = (
                item_scores.get(entry.item_id, 0.0) + weight * threat.score
            )
            item_covered.setdefault(entry.item_id, []).append(threat.tag)
            # Keep the highest-priority (lowest number) entry as the source.
            prev = entry_by_item.get(entry.item_id)
            if prev is None or entry.priority < prev[1].priority:
                entry_by_item[entry.item_id] = (rule, entry)

    # Rank: score desc, then tier asc, then cost asc.
    ranked_ids = sorted(
        item_scores,
        key=lambda item_id: (
            -item_scores[item_id],
            items_by_id[item_id].tier,
            items_by_id[item_id].cost,
        ),
    )

    recommendations: list[Recommendation] = []
    for item_id in ranked_ids[:MAX_RECOMMENDATIONS]:
        item = items_by_id[item_id]
        rule, entry = entry_by_item[item_id]
        covered = item_covered[item_id]
        if len(covered) > 1:
            explanation = (
                f"{entry.explanation} Covers multiple threats: "
                f"{', '.join(tag.value for tag in covered)}."
            )
        else:
            explanation = entry.explanation
        recommendations.append(
            Recommendation(
                item=item,
                explanation=explanation,
                priority=entry.priority,
                tier=item.tier,
                cost=item.cost,
                timing=entry.timing,
            )
        )

    return Advice(threats=threats, recommendations=recommendations)
