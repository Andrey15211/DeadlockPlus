"""Спек-уровневые QA-ассертации (тикет 04).

Проверяют SPEC.md как контракт, а не реализацию:
- ростер ровно 22 героя, >= 48 предметов, 3 категории;
- все 9 тегов угроз закрыты контр-правилами, приоритеты в диапазоне;
- сигнатурные маппинги топ-приоритетов (SPEC stories 9-12);
- CDR-кап 0.55 и полосы цен тирам (SPEC Implementation Decisions);
- эндпоинты REST-шва и русскоязычный SPA;
- каждая угроза словаря встречается хотя бы у одного героя или предмета.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from deadlockforge.engine.calculator import compute
from deadlockforge.engine.dataset import load_catalog
from deadlockforge.models import (
    CDR_CAP,
    Hero,
    HeroRole,
    HeroStats,
    Item,
    ItemCategory,
    ItemStats,
    StatsSnapshot,
    ThreatTag,
    TIER_COST_BANDS,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def catalog():
    return load_catalog()


def test_spec_hero_roster_at_least_40(catalog):
    """Ростер расширен до полного (40+ release + Hero Labs pre-release)."""
    assert len(catalog.heroes) >= 40
    assert len(catalog.by_id) == len(catalog.heroes)  # нет дубликатов id
    # минимально 4 героя Hero Labs / pre-release для экрана тир-листа
    assert sum(1 for h in catalog.heroes if h.in_development) >= 4


def test_spec_item_catalog_at_least_48(catalog):
    assert len(catalog.items) >= 48
    cats = {item.category for item in catalog.items}
    assert cats == {ItemCategory.WEAPON, ItemCategory.VITALITY, ItemCategory.SPIRIT}
    # каждый тир каждой категории непуст (SPEC: 3 категории x 4 тира)
    for cat in cats:
        for tier in (1, 2, 3, 4):
            assert any(
                i.category == cat and i.tier == tier for i in catalog.items
            ), f"нет предметов {cat.value} T{tier}"


def test_spec_all_9_threat_tags_have_rules(catalog):
    rules_by_tag = {rule.tag: rule for rule in catalog.counter_rules}
    assert set(rules_by_tag) == set(ThreatTag)
    for rule in catalog.counter_rules:
        for entry in rule.entries:
            assert 1 <= entry.priority <= 10
            assert entry.explanation.strip()
            assert entry.timing.strip()
            assert entry.item_id in catalog.item_by_id


def test_spec_signature_top_counter_mappings(catalog):
    """SPEC stories 9-12: топ-приоритеты четырёх ключевых сигнатур."""
    top = {rule.tag: rule.entries[0].item_id for rule in catalog.counter_rules}
    assert top[ThreatTag.HEAL] == "healbane"
    assert top[ThreatTag.UNINTERRUPTIBLE_ULT] == "knockdown"
    assert top[ThreatTag.BULLET_DPS] == "metal-skin"
    assert top[ThreatTag.SPIRIT_CASTER] == "silence-glyph"


def test_spec_cdr_cap_is_55_percent():
    assert CDR_CAP == 0.55


def test_spec_cdr_cap_enforced():
    hero = Hero(
        id="qa", name="QA", roles=[HeroRole.DUELIST],
        stats=HeroStats(
            hp=600, bullet_shield=4, spirit_shield=4, weapon_damage=30,
            fire_rate=2.0, spirit_power=5, stamina=3, move_speed=6.5,
        ),
        signature="qa", difficulty=1,
    )
    spam = [
        Item(
            id=f"cdr{i}", name=f"cdr{i}", name_en=f"cdr{i}",
            category=ItemCategory.SPIRIT, tier=1, cost=500,
            stats=ItemStats(cooldown_reduction_pct=0.2), active=None,
            passive=None, counters=[],
        )
        for i in range(4)
    ]
    snap = compute(hero, spam)  # 0.8 суммарно
    assert snap.cooldown_reduction_pct == pytest.approx(CDR_CAP)


def test_spec_tier_cost_bands(catalog):
    for item in catalog.items:
        low, high = TIER_COST_BANDS[item.tier]
        assert low <= item.cost <= high, (
            f"{item.id}: cost {item.cost} вне полосы тира {item.tier}"
        )


def test_spec_every_tag_reachable(catalog):
    """Каждый тег словаря встречается у героя или адресуется предметом."""
    hero_tags = {tag for h in catalog.heroes for tag in h.threat_tags}
    item_tags = {tag for i in catalog.items for tag in i.counters}
    used = hero_tags | item_tags
    missing = set(ThreatTag) - used
    assert not missing, f"мёртвые теги словаря: {missing}"


def test_spec_rest_seam_endpoints():
    from fastapi.testclient import TestClient

    from deadlockforge.api.app import create_app

    client = TestClient(create_app())
    for path in ("/api/heroes", "/api/items", "/api/map"):
        resp = client.get(path)
        assert resp.status_code == 200, path

    resp = client.get(
        "/api/advice",
        params={"my_hero": "abrams", "enemies": "seven,haze,pocket"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["threats"] and body["recommendations"]
    assert len(body["recommendations"]) <= 6

    resp = client.post(
        "/api/calc",
        json={
            "hero_id": "abrams",
            "item_ids": ["healbane", "metal-skin", "silence-glyph"],
        },
    )
    assert resp.status_code == 200
    assert resp.json()["souls_spent"] == 3100 + 3800 + 6200

    resp = client.post(
        "/api/build/validate",
        json={
            "weapon": [None] * 4, "vitality": [None] * 4,
            "spirit": [None] * 4, "flex": ["healbane", None, None, None],
        },
    )
    assert resp.status_code == 200
    assert resp.json() == {"valid": True}


def test_spec_spa_is_russian_and_present():
    index = (PROJECT_ROOT / "web" / "index.html").read_text(encoding="utf-8")
    assert "DeadlockForge" in index
    assert 'lang="ru"' in index
    # русские строки UI вместо английских
    for ru in ("Билд", "Матчап", "Карта", "Сбросить", "Каталог предметов"):
        assert ru in index, f"нет русской строки UI: {ru}"
    # все табы/разделы спецификации
    assert "tab-lab" in index and "tab-match" in index and "tab-map" in index
    # 16-слотовая доска: 4 группы по 4
    for group in ("slots-weapon", "slots-vitality", "slots-spirit", "slots-flex"):
        assert group in index


def test_spec_stats_snapshot_has_ehp_fields():
    """Фаза 3: StatsSnapshot объявляет effective_hp_bullet и effective_hp_spirit."""
    fields = set(StatsSnapshot.model_fields)
    assert "effective_hp_bullet" in fields, "StatsSnapshot missing effective_hp_bullet"
    assert "effective_hp_spirit" in fields, "StatsSnapshot missing effective_hp_spirit"


def test_spec_advice_works_without_allow_mirror_param():
    """Фаза 3: /api/advice отвечает и без параметра allow_mirror
    (обратная совместимость с клиентами Фазы 2)."""
    from fastapi.testclient import TestClient

    from deadlockforge.api.app import create_app

    client = TestClient(create_app())
    resp = client.get(
        "/api/advice",
        params={"my_hero": "abrams", "enemies": "seven,haze,pocket"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["threats"] and body["recommendations"]


def test_spec_spa_phase3_russian_ui_strings():
    """Фаза 3: SPA содержит новые элементы — все строки русские.

    'Зеркальная дуэль' — тумблер зеркала, 'Сохранить билд' и
    'Скопировать код билда' — обмен билдами, 'Синергии врага' — блок
    синергий (заголовок блока рендерится из web/js/ui.js, контейнер
    #synergy-block объявлен в index.html).
    """
    index = (PROJECT_ROOT / "web" / "index.html").read_text(encoding="utf-8")
    ui_js = (PROJECT_ROOT / "web" / "js" / "ui.js").read_text(encoding="utf-8")
    spa = index + ui_js
    for ru in ("Зеркальная дуэль", "Сохранить билд", "Скопировать код", "Синергии врага"):
        assert ru in spa, f"нет русской строки UI Фазы 3: {ru}"
    assert 'id="synergy-block"' in index, "нет контейнера блока синергий"
    assert 'id="mirror-toggle"' in index, "нет тумблера зеркальной дуэли"


def test_spec_no_cjk_anywhere():
    """Запрет CJK-символов во всех текстовых файлах проекта."""
    import re

    cjk = re.compile(r"[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]")
    suffixes = {".py", ".js", ".html", ".css", ".json", ".md"}
    for path in PROJECT_ROOT.rglob("*"):
        if (
            path.is_file()
            and path.suffix in suffixes
            and "__pycache__" not in path.parts
            and ".pytest_cache" not in path.parts
            and ".scratch" not in path.parts
            and ".venv" not in path.parts
            and "venv" not in path.parts
            and ".git" not in path.parts
            and "dist" not in path.parts
            and "build" not in path.parts
        ):
            text = path.read_text(encoding="utf-8", errors="ignore")
            assert not cjk.search(text), f"CJK-символы в {path}"


def test_item_catalog_visual_style_consistency(catalog):
    """Каталог предметов: 100% цветных официальных 3D-арт иконок магазина Valve.
    
    1. Никаких SVG-заглушек с буквами.
    2. Все 214 предметов имеют валидный .webp файл в web/assets/items/.
    3. Отсутствие монохромных силуэтов HUD: каждый файл имеет валидную вариацию каналов RGB.
    """
    import json
    from PIL import Image
    import numpy as np

    items_dir = PROJECT_ROOT / "web" / "assets" / "items"
    assert not list(items_dir.glob("*.svg")), "Найдены SVG-заглушки в web/assets/items/"

    items_json = json.loads((PROJECT_ROOT / "data" / "items.json").read_text(encoding="utf-8"))
    assert len(items_json) >= 180

    for it in items_json:
        slug = it["id"]
        webp_path = items_dir / f"{slug}.webp"
        assert webp_path.is_file(), f"Отсутствует WebP-иконка для {slug}"
        assert webp_path.stat().st_size > 500, f"Файл иконки {slug} поврежден или пуст"

        im = Image.open(webp_path).convert("RGB")
        arr = np.array(im, dtype=float)
        channel_std = np.std(arr, axis=2)
        mean_diff = float(np.mean(channel_std))
        color_ratio = float(np.mean(channel_std > 5))

        # Защита от монохромных силуэтов Valve HUD:
        assert color_ratio >= 0.05 or mean_diff >= 3.0, (
            f"Иконка {slug} является монохромным ч/б силуэтом (mean_diff={mean_diff:.2f}, ratio={color_ratio:.3f})"
        )

