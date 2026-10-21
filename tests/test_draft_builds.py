"""Тесты S5: быстрые сборки по драфту (POST /api/draft/builds).

Проверяем и движок, и эндпоинт:
  * форма ответа (starter / variants / counters / red_flags / timing_hint);
  * варианты действительно различаются составом и целевыми статами;
  * контр-предметы отражают теги врагов (heal → анти-хил и т.д.);
  * все item_id существуют в каталоге и лежат в слотах своей категории;
  * валидация (неизвестный герой → 404, >6 врагов → 422, дубликаты → 422);
  * локальный путь не делает сетевых вызовов.
"""

from __future__ import annotations

import sys
import time
import urllib.request
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from fastapi.testclient import TestClient  # noqa: E402

from deadlockforge.api.app import create_app  # noqa: E402
from deadlockforge.engine import dataset as dataset_mod  # noqa: E402
from deadlockforge.engine import draft_builds as db_mod  # noqa: E402
from deadlockforge.models import ItemCategory, TIER_COST_BANDS  # noqa: E402

CLIENT = TestClient(create_app())

HEAL_ENEMIES = ["kelvin", "abrams", "infernus"]
MIXED_ENEMIES = ["kelvin", "infernus", "seven", "abrams", "wraith", "lash"]
MY_HERO = "haze"


@pytest.fixture(scope="module")
def catalog():
    return dataset_mod.get_catalog()


def _plan(my_hero: str = MY_HERO, enemies: list[str] | None = None, **kw):
    resp = CLIENT.post(
        "/api/draft/builds",
        json={"my_hero": my_hero, "enemies": enemies or MIXED_ENEMIES, **kw},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


# --- форма ответа -----------------------------------------------------------


def test_endpoint_shape():
    data = _plan()
    assert {"starter", "variants", "counters", "red_flags", "timing_hint"} <= set(data)
    assert data["my_hero"]["id"] == MY_HERO
    assert [e["id"] for e in data["enemies"]] == MIXED_ENEMIES


def test_starter_is_tier1_set_with_price_and_reason():
    data = _plan()
    starter = data["starter"]
    assert 3 <= len(starter) <= 4, "стартовый набор — 3–4 позиции"
    assert data["starter_cost"] == sum(s["cost"] for s in starter) > 0
    for entry in starter:
        assert entry["tier"] == 1
        low, high = TIER_COST_BANDS[1]
        assert low <= entry["cost"] <= high
        assert entry["reason"].strip(), "у каждой стартовой покупки есть причина"
        assert entry["item_id"] and entry["name"]


def test_variants_present_and_labelled():
    data = _plan()
    keys = [v["key"] for v in data["variants"]]
    assert keys == ["farm", "teamfight", "counter"]
    titles = {v["key"]: v["title"] for v in data["variants"]}
    assert titles == {
        "farm": "Под фарм",
        "teamfight": "Под драки",
        "counter": "Против их состава",
    }
    variant_list = data["variants"]
    for variant in variant_list:
        assert len(variant["item_ids"]) >= 8
        assert variant["total_cost"] > 0
        assert variant["rationale"], "у варианта есть обоснование"
        assert variant["focus"], "у варианта есть целевые статы"
        for stat in ("hp", "weapon_dps", "spirit_power", "effective_hp_bullet",
                     "effective_hp_spirit", "souls_spent"):
            assert stat in variant["stats"]
        assert variant["stats"]["souls_spent"] == variant["total_cost"]


def _offense(hero_id: str, variant: dict, catalog) -> float:
    """Урон в «своём» канале героя: пулевой у керри-ганнеров, иначе спирит.

    «Под фарм» — это про урон, но канал у героя свой: спирит-керри фармит
    спирит-уроном, а не пулями.
    """
    hero = catalog.by_id[hero_id]
    tags = {t.value for t in hero.threat_tags}
    if "bullet_dps" in tags:
        return variant["stats"]["weapon_dps"]
    return variant["stats"]["spirit_power"]


def test_variants_differ_in_composition_and_stats(catalog):
    data = _plan()
    by_key = {v["key"]: v for v in data["variants"]}
    farm, fight, counter = (by_key[k] for k in ("farm", "teamfight", "counter"))

    assert set(farm["item_ids"]) != set(fight["item_ids"]), (
        "«под фарм» и «под драки» должны отличаться составом"
    )
    assert set(counter["item_ids"]) != set(farm["item_ids"])

    # Фарм-вариант обязан быть про урон, драки — про живучесть.
    assert _offense(MY_HERO, farm, catalog) > _offense(MY_HERO, fight, catalog)
    assert fight["stats"]["effective_hp_bullet"] > farm["stats"]["effective_hp_bullet"]
    assert fight["stats"]["effective_hp_spirit"] > farm["stats"]["effective_hp_spirit"]


def test_variants_stay_distinct_for_every_hero(catalog):
    """Инвариант «фарм — урон, драки — живучесть» держится для всех героев."""
    roster = [h.id for h in catalog.heroes if not h.in_development]
    offenders: list[str] = []
    for i, hero_id in enumerate(roster):
        if hero_id == MY_HERO:
            continue
        enemies = [h for h in roster if h != hero_id][:6]
        plan = db_mod.build_plan(hero_id, enemies, catalog)
        farm, fight = plan.variants[0], plan.variants[1]
        stats_farm, stats_fight = farm.stats, fight.stats
        if not (
            _offense(hero_id, farm.model_dump(), catalog)
            > _offense(hero_id, fight.model_dump(), catalog)
            and stats_fight["effective_hp_bullet"] > stats_farm["effective_hp_bullet"]
            and set(farm.item_ids) != set(fight.item_ids)
        ):
            offenders.append(hero_id)
    assert not offenders, f"варианты не разошлись у героев: {offenders}"


def test_items_exist_and_match_their_slot_category(catalog):
    data = _plan()
    for variant in data["variants"]:
        for item_id in variant["item_ids"]:
            item = catalog.item_by_id.get(item_id)
            assert item is not None, f"неизвестный предмет {item_id}"
            assert item.tier <= 4, "T5 (9100+) в быстрые сборки не попадают"
            low, high = TIER_COST_BANDS[item.tier]
            assert low <= item.cost <= high

        # Каждый id лежит в слоте своей категории — сборку можно применить.
        placed: dict[str, str] = {}
        for group, slots in variant["slots"].items():
            for item_id in slots:
                if item_id is None:
                    continue
                item = catalog.item_by_id[item_id]
                assert item.category.value == group, (
                    f"{item_id} ({item.category.value}) стоит в слоте {group}"
                )
                assert item_id not in placed, f"дубликат {item_id} в сборке"
                placed[item_id] = group
        assert set(placed) == set(variant["item_ids"])

    for entry in data["starter"]:
        assert catalog.item_by_id[entry["item_id"]].tier == 1


# --- контр-предметы и Red Flags ---------------------------------------------


def test_counters_reflect_enemy_tags_heal():
    """Враги с heal → в counters обязан быть анти-хил предмет."""
    data = _plan(enemies=HEAL_ENEMIES)
    heal_counters = [c for c in data["counters"] if "heal" in c["covered_tags"]]
    assert heal_counters, "против heal-состава нужен анти-хил предмет"
    assert any(c["item_id"] == "healbane" for c in heal_counters)


def test_counters_carry_priority_and_timing(catalog):
    data = _plan()
    assert data["counters"], "контр-предметы должны быть подобраны"
    for counter in data["counters"]:
        assert counter["item_id"] in catalog.item_by_id
        assert 1 <= counter["priority"] <= 10
        assert counter["timing"].strip()
        assert counter["covered_tags"], "у контр-предмета указаны закрытые теги"
        for tag in counter["covered_tags"]:
            assert tag in {t.value for t in catalog.item_by_id[counter["item_id"]].counters} \
                or tag in {r.tag.value for r in catalog.counter_rules
                           if any(e.item_id == counter["item_id"] for e in r.entries)}


def test_counter_variant_contains_counter_items():
    data = _plan()
    counter_ids = {c["item_id"] for c in data["counters"]}
    by_key = {v["key"]: v for v in data["variants"]}
    counters_variant = by_key["counter"]
    overlap = counter_ids & set(counters_variant["item_ids"])
    assert len(overlap) >= 3, (
        "вариант «против их состава» обязан содержать контр-предметы"
    )
    farm_overlap = counter_ids & set(by_key["farm"]["item_ids"])
    assert len(overlap) > len(farm_overlap), (
        "контр-вариант должен опираться на контр-предметы сильнее фарм-варианта"
    )


def test_red_flags_and_threat_summary_from_drafter():
    data = _plan(enemies=HEAL_ENEMIES)
    titles = " ".join(f["title"] for f in data["red_flags"])
    assert data["red_flags"], "heal-состав должен давать Red Flag"
    assert "лечением" in titles
    for flag in data["red_flags"]:
        assert flag["severity"] in ("critical", "warning", "info")
        assert flag["title"] and flag["detail"] and flag["fix"]

    tags = [t["tag"] for t in data["threat_summary"]]
    assert "heal" in tags
    for threat in data["threat_summary"]:
        assert threat["score"] > 0
        assert threat["contributors"]


def test_timing_hint_uses_tier_cost_bands():
    data = _plan()
    bands = data["timing_hint"]["bands"]
    assert bands, "тайминг должен быть разложен по тирам"
    assert data["timing_hint"]["summary"]
    for band in bands:
        low, high = TIER_COST_BANDS[band["tier"]]
        assert str(low) in band["cost_range"]
        assert band["item_ids"]
        assert band["window"]


# --- валидация --------------------------------------------------------------


def test_unknown_hero_returns_404():
    resp = CLIENT.post(
        "/api/draft/builds", json={"my_hero": "not-a-hero", "enemies": ["kelvin"]}
    )
    assert resp.status_code == 404
    assert "Unknown hero id" in resp.json()["detail"]


def test_unknown_enemy_returns_404():
    resp = CLIENT.post(
        "/api/draft/builds", json={"my_hero": MY_HERO, "enemies": ["kelvin", "nope"]}
    )
    assert resp.status_code == 404


def test_more_than_six_enemies_returns_422():
    enemies = [h.id for h in dataset_mod.get_catalog().heroes[:7]]
    resp = CLIENT.post("/api/draft/builds", json={"my_hero": MY_HERO, "enemies": enemies})
    assert resp.status_code == 422


def test_duplicate_enemies_return_422():
    resp = CLIENT.post(
        "/api/draft/builds", json={"my_hero": MY_HERO, "enemies": ["kelvin", "kelvin"]}
    )
    assert resp.status_code == 422


def test_my_hero_in_enemies_returns_422():
    resp = CLIENT.post(
        "/api/draft/builds", json={"my_hero": "kelvin", "enemies": ["kelvin"]}
    )
    assert resp.status_code == 422


def test_empty_enemies_return_422():
    resp = CLIENT.post("/api/draft/builds", json={"my_hero": MY_HERO, "enemies": []})
    assert resp.status_code == 422


def test_unknown_role_and_mode_return_422():
    bad_role = CLIENT.post(
        "/api/draft/builds",
        json={"my_hero": MY_HERO, "enemies": ["kelvin"], "role": "wizard"},
    )
    assert bad_role.status_code == 422
    bad_mode = CLIENT.post(
        "/api/draft/builds",
        json={"my_hero": MY_HERO, "enemies": ["kelvin"], "mode": "turbo"},
    )
    assert bad_mode.status_code == 422


def test_role_hint_alias_and_passthrough():
    data = _plan(role="tank")
    assert data["role_hint"] == "initiator", "«танк» нормализуется в роль initiator"


# --- скорость и отсутствие сети ---------------------------------------------


def test_local_path_makes_no_network_calls(monkeypatch):
    """Локальный путь не должен обращаться к сети ни при каких условиях."""

    calls: list[str] = []

    def forbidden(req, *args, **kwargs):
        url = getattr(req, "full_url", req)
        calls.append(str(url))
        raise AssertionError(f"локальный путь сделал сетевой вызов: {url}")

    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    for module in ("deadlockforge.engine.advisor", "deadlockforge.engine.drafter",
                   "deadlockforge.engine.draft_builds"):
        module_obj = __import__(module, fromlist=["*"])
        if hasattr(module_obj, "urllib"):
            monkeypatch.setattr(module_obj.urllib.request, "urlopen", forbidden)

    for endpoint, body in [
        ("/api/advice", None),
        ("/api/draft/builds", {"my_hero": MY_HERO, "enemies": MIXED_ENEMIES}),
    ]:
        if endpoint == "/api/advice":
            resp = CLIENT.get(
                "/api/advice", params={"my_hero": MY_HERO, "enemies": ",".join(MIXED_ENEMIES)}
            )
        else:
            resp = CLIENT.post(endpoint, json=body)
        assert resp.status_code == 200, resp.text
    assert calls == [], f"исходящие запросы: {calls}"


def test_local_path_is_fast_enough():
    """Сценарий «в матче»: локальный ответ должен быть мгновенным (<40 с)."""
    start = time.perf_counter()
    data = _plan()
    elapsed = (time.perf_counter() - start) * 1000
    assert elapsed < 40_000, f"ответ занял {elapsed:.0f} мс"
    assert data["engine"]["local_ms"] < 5_000
    assert data["engine"]["llm_used"] is False


def test_mode_ai_degrades_to_local_without_key():
    """Без ключа (или при ошибке LLM) ответ остаётся полным и локальным."""
    data = _plan(mode="ai")
    assert data["engine"]["llm_used"] is False
    assert data["starter"] and data["variants"] and data["counters"]


def test_engine_marks_notes_about_local_engine():
    data = _plan()
    assert any("Локальный движок" in note for note in data["notes"])


def test_llm_notes_never_raise_on_a_broken_client(catalog):
    """LLM-надстройка обязана возвращать None, а не падать внутри эндпоинта."""
    plan = db_mod.build_plan(MY_HERO, MIXED_ENEMIES, catalog)

    class ExplodingClient:
        request_timeout = 60.0

        def generate_coach_report(self, prompt, payload, image_bytes=None):
            raise RuntimeError("provider down")

    class GarbageClient:
        request_timeout = 60.0

        def generate_coach_report(self, prompt, payload, image_bytes=None):
            return "не JSON вовсе"

    assert db_mod.llm_notes(plan, ExplodingClient()) is None
    assert db_mod.llm_notes(plan, GarbageClient()) is None
    assert db_mod.llm_notes(plan, None) is None

    class GoodClient:
        request_timeout = 60.0

        def generate_coach_report(self, prompt, payload, image_bytes=None):
            return '```json\n{"farm": "Фарм.", "teamfight": "Файты.", "counter": "Контр."}\n```'

    client = GoodClient()
    notes = db_mod.llm_notes(plan, client)
    assert notes == {"farm": "Фарм.", "teamfight": "Файты.", "counter": "Контр."}
    assert client.request_timeout <= db_mod.LLM_NOTES_TIMEOUT


# --- движок: прямое API -----------------------------------------------------


def test_build_plan_is_deterministic(catalog):
    first = db_mod.build_plan(MY_HERO, MIXED_ENEMIES, catalog, role="carry")
    second = db_mod.build_plan(MY_HERO, MIXED_ENEMIES, catalog, role="carry")
    assert [v.item_ids for v in first.variants] == [v.item_ids for v in second.variants]
    assert [s.item_id for s in first.starter] == [s.item_id for s in second.starter]


def test_counter_variant_not_tankier_than_teamfight_when_threats_are_damage(catalog):
    """Контр-вариант отвечает на угрозы, но не обязан быть «толще» файтового."""
    plan = db_mod.build_plan(MY_HERO, MIXED_ENEMIES, catalog)
    by_key = {v.key: v for v in plan.variants}
    counter_ids = {c.item_id for c in plan.counters}
    overlap = counter_ids & set(by_key["counter"].item_ids)
    assert len(overlap) >= 3
    assert by_key["counter"].total_cost >= by_key["farm"].total_cost


def test_starter_categories_are_spread(catalog):
    plan = db_mod.build_plan(MY_HERO, MIXED_ENEMIES, catalog)
    categories = {catalog.item_by_id[s.item_id].category for s in plan.starter}
    assert categories <= set(ItemCategory)
    assert len(categories) >= 2, "стартовый набор не должен быть однобоким"