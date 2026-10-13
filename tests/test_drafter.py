"""Тесты драфт-движка 6v6: Red Flags, синергии, контр-пики, mirror-правила."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from deadlockforge.engine.dataset import load_catalog  # noqa: E402
from deadlockforge.engine.drafter import (  # noqa: E402
    DraftAnalysis,
    analyze_draft,
    suggest_build,
)
from deadlockforge.models import RedFlag, ThreatTag  # noqa: E402


@pytest.fixture(scope="module")
def catalog():
    return load_catalog()


def _heal_enemies(catalog):
    """Найти врагов с тегом heal для red-flag сценариев."""
    ids = [h.id for h in catalog.heroes if ThreatTag.HEAL in h.threat_tags]
    assert len(ids) >= 3, "dataset must have >= 3 heal-tagged heroes"
    return ids


def _ult_enemies(catalog):
    ids = [h.id for h in catalog.heroes
           if ThreatTag.UNINTERRUPTIBLE_ULT in h.threat_tags]
    assert len(ids) >= 2, "dataset must have >= 2 uninterruptible_ult heroes"
    return ids


def _mobile_enemies(catalog):
    ids = [h.id for h in catalog.heroes if ThreatTag.MOBILITY in h.threat_tags]
    assert len(ids) >= 3, "dataset must have >= 3 mobile heroes"
    return ids


def _spirit_enemies(catalog):
    ids = [h.id for h in catalog.heroes if ThreatTag.SPIRIT_CASTER in h.threat_tags]
    assert len(ids) >= 3, "dataset must have >= 3 spirit casters"
    return ids


def _my_team(catalog, exclude: set[str]) -> list[str]:
    """Валидная команда из героев не из exclude."""
    team = [h.id for h in catalog.heroes if h.id not in exclude][:4]
    assert len(team) == 4
    return team


# --- Red Flags --------------------------------------------------------------


def test_heal_overload_red_flag(catalog):
    heal = _heal_enemies(catalog)
    enemies = heal[:3]
    my = _my_team(catalog, set(enemies))
    result = analyze_draft(my, enemies, catalog)
    flags = [f for f in result.red_flags
             if "лечением" in f.title or "heal" in f.title.lower()]
    assert flags, "no heal overload flag"
    assert flags[0].severity == "critical"
    assert flags[0].fix


def test_ult_overload_red_flag(catalog):
    ult = _ult_enemies(catalog)
    enemies = ult[:2]
    my = _my_team(catalog, set(enemies))
    result = analyze_draft(my, enemies, catalog)
    flags = [f for f in result.red_flags if "ульт" in f.title.lower()]
    assert flags, "no ult overload flag"
    assert flags[0].severity == "critical"


def test_mobility_overload_red_flag(catalog):
    mobile = _mobile_enemies(catalog)
    enemies = mobile[:3]
    my = _my_team(catalog, set(enemies))
    result = analyze_draft(my, enemies, catalog)
    flags = [f for f in result.red_flags if "мобильн" in f.title.lower()]
    assert flags
    assert flags[0].severity == "warning"


def test_spirit_overload_red_flag(catalog):
    spirit = _spirit_enemies(catalog)
    enemies = spirit[:3]
    my = _my_team(catalog, set(enemies))
    result = analyze_draft(my, enemies, catalog)
    flags = [f for f in result.red_flags if "spirit" in f.title.lower()]
    assert flags


def test_bullet_share_red_flag(catalog):
    """6 моих героев с bullet_dps → предупреждение о Metal Skin."""
    bullet = [h.id for h in catalog.heroes
              if ThreatTag.BULLET_DPS in h.threat_tags][:6]
    if len(bullet) < 5:  # в датасете может не быть 6 bullet-героев
        pytest.skip("not enough bullet_dps heroes for the share test")
    enemies = _heal_enemies(catalog)[:2]
    result = analyze_draft(bullet, enemies, catalog)
    flags = [f for f in result.red_flags if "пулев" in f.detail.lower()
             or "bullet" in f.title.lower()]
    assert flags


# --- mirror rules ------------------------------------------------------------


def test_mirror_pick_forbidden(catalog):
    my = _my_team(catalog, set())
    with pytest.raises(ValueError, match="[Mm]irror"):
        analyze_draft(my, my[:1], catalog, allow_mirror=False)


def test_mirror_pick_allowed_in_sandbox(catalog):
    my = _my_team(catalog, set())
    result = analyze_draft(my, my[:1], catalog, allow_mirror=True)
    assert isinstance(result, DraftAnalysis)


def test_duplicate_inside_team_rejected(catalog):
    heroes = _my_team(catalog, set())
    with pytest.raises(ValueError, match="[Dd]uplicate"):
        analyze_draft([heroes[0], heroes[0]], [], catalog)


def test_team_size_limit(catalog):
    heroes = [h.id for h in catalog.heroes[:7]]
    with pytest.raises(ValueError, match="maximum"):
        analyze_draft(heroes, [], catalog)


def test_unknown_hero_rejected(catalog):
    with pytest.raises(ValueError, match="[Uu]nknown"):
        analyze_draft(["no-such-hero"], [], catalog)


# --- synergies & counter picks -------------------------------------------------


def test_synergies_present(catalog):
    cc = [h.id for h in catalog.heroes if ThreatTag.CC_CHAIN in h.threat_tags][:1]
    burst = [h.id for h in catalog.heroes
             if ThreatTag.BURST in h.threat_tags and h.id not in cc][:1]
    enemies = _heal_enemies(catalog)[:1]
    result = analyze_draft(cc + burst, enemies, catalog)
    titles = [n.title for n in result.synergy]
    assert any("Контроль + бурст" in t for t in titles)


def test_counter_picks_non_empty(catalog):
    enemies = _heal_enemies(catalog)[:2]
    my = [h.id for h in catalog.heroes
          if h.in_development is False and h.id not in enemies][:2]
    result = analyze_draft(my, enemies, catalog)
    assert result.counter_picks
    for pick in result.counter_picks:
        assert pick.hero_id not in my
        assert pick.reason


def test_threat_summary_scores(catalog):
    heal = _heal_enemies(catalog)
    my = _my_team(catalog, set(heal))
    result = analyze_draft(my, heal[:2], catalog)
    assert result.threat_summary
    top = result.threat_summary[0]
    assert top["score"] > 0
    assert top["contributors"]


def test_recommendations_reference_existing_items(catalog):
    heal = _heal_enemies(catalog)
    my = _my_team(catalog, set(heal))
    result = analyze_draft(my, heal[:2], catalog)
    for rec in result.recommended_items:
        assert rec["item_id"] in catalog.item_by_id


def test_suggest_build_orders_by_tag(catalog):
    items = suggest_build(
        catalog.heroes[0].id,
        [ThreatTag.HEAL, ThreatTag.MOBILITY],
        catalog,
    )
    assert items
    assert all(i in catalog.item_by_id for i in items)


def test_red_flag_model_shape(catalog):
    """RedFlag всегда: severity из enum, непустые title/detail/fix."""
    heal = _heal_enemies(catalog)
    my = _my_team(catalog, set(heal))
    result = analyze_draft(my, heal[:3], catalog)
    assert result.red_flags
    for flag in result.red_flags:
        assert isinstance(flag, RedFlag)
        assert flag.severity in ("critical", "warning", "info")
        assert flag.title and flag.detail and flag.fix
