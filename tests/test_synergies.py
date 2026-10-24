"""Синергии: схема датасета, целостность пар и API-роут.

Датасет собирается scripts/build_synergies.py с mobalytics.gg/deadlock/tier-list/synergies,
поэтому проверяем контракт: пары уникальны, ранги непрерывны, сорт совпадает
с порогами сайта, имена героев русские, портреты существуют, API отдаёт данные
и корректно ревалидируется по ETag.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from deadlockforge.api.app import create_app
from deadlockforge.engine import dataset as dataset_mod
from deadlockforge.models import Synergies

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
SYNERGIES_JSON = DATA_DIR / "synergies.json"
HEROES_JSON = DATA_DIR / "heroes.json"
WEB_DIR = ROOT / "web"

# Пороги сайта (чанк 29910): S >= 55, A >= 50, B >= 45, иначе C.
TIER_RULES = {"S": 55.0, "A": 50.0, "B": 45.0, "C": 0.0}


def expected_tier(winrate: float) -> str:
    for tier in ("S", "A", "B", "C"):
        if winrate >= TIER_RULES[tier]:
            return tier
    return "C"


@pytest.fixture(scope="module")
def payload() -> dict:
    if not SYNERGIES_JSON.is_file():
        pytest.skip("data/synergies.json отсутствует — запустите scripts/build_synergies.py")
    return json.loads(SYNERGIES_JSON.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def synergies() -> Synergies:
    return dataset_mod.load_synergies()


@pytest.fixture(scope="module")
def heroes() -> dict[str, dict]:
    return {h["id"]: h for h in json.loads(HEROES_JSON.read_text(encoding="utf-8"))}


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(create_app())


# --- схема и полнота -------------------------------------------------------


def test_dataset_metadata(payload):
    assert payload["version"] == 1
    assert payload["source"] == "https://mobalytics.gg/deadlock/tier-list/synergies"
    assert len(payload["collected_at"]) == 10
    assert payload["tier_rules"] == TIER_RULES
    assert len(payload["methodology"]) >= 3
    assert all(isinstance(line, str) and line.strip() for line in payload["methodology"])


def test_full_pair_coverage(synergies):
    """44 героя дают ровно 44*43/2 = 946 уникальных дуо — вся таблица сайта."""
    duos = synergies.duos
    assert len(duos) == 946
    pairs = {frozenset((d.hero1.id, d.hero2.id)) for d in duos}
    assert len(pairs) == 946
    assert all(len(pair) == 2 for pair in pairs), "в дуо не должно быть героя с самим собой"


def test_ranks_are_contiguous(synergies):
    ranks = [d.rank for d in synergies.duos]
    assert ranks == list(range(1, len(synergies.duos) + 1))


def test_ranks_sorted_by_winrate(synergies):
    winrates = [d.winrate for d in synergies.duos]
    assert winrates == sorted(winrates, reverse=True)


def test_tiers_match_site_thresholds(synergies):
    for duo in synergies.duos:
        assert duo.tier == expected_tier(duo.winrate), duo


def test_metrics_in_sane_ranges(synergies):
    for duo in synergies.duos:
        assert 0 < duo.winrate <= 100
        assert 0 <= duo.pickrate <= 100
        assert duo.cs_per_min > 0
        assert duo.denies >= 0
        assert duo.nw_per_min > 0
        assert duo.kda > 0
        assert -100 < duo.wrf < 100


def test_top_duo_is_graves_kelvin(synergies):
    top = synergies.duos[0]
    assert top.rank == 1
    assert (top.hero1.id, top.hero2.id) == ("necro", "kelvin")
    assert top.tier == "S"
    assert top.winrate == pytest.approx(62.4)


# --- локализация и ассеты --------------------------------------------------


def test_heroes_are_localized_and_known(synergies, heroes):
    for duo in synergies.duos:
        for hero in (duo.hero1, duo.hero2):
            assert hero.id in heroes, f"неизвестный герой {hero.id!r}"
            assert hero.name == heroes[hero.id]["name"], f"{hero.id}: имя не из heroes.json"
            assert hero.name != hero.name_en, f"{hero.id}: имя осталось английским"
            assert hero.name_en == heroes[hero.id].get("name_en", "")


def test_portraits_exist_on_disk(synergies):
    missing = {
        hero.portrait
        for duo in synergies.duos
        for hero in (duo.hero1, duo.hero2)
        if hero.portrait and not (WEB_DIR / hero.portrait).is_file()
    }
    assert not missing, f"нет файлов портретов: {sorted(missing)}"


def test_no_english_labels_in_dataset(payload):
    text = json.dumps(
        {k: v for k, v in payload.items() if k not in ("source", "duos")},
        ensure_ascii=False,
    )
    for word in ("Synergy", "Tier List", "WinRate", "PickRate", "data is collected"):
        assert word not in text


# --- API -------------------------------------------------------------------


def test_api_returns_dataset(client, payload):
    resp = client.get("/api/synergies")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/json")
    body = resp.json()
    assert len(body["duos"]) == len(payload["duos"])
    assert body["duos"][0]["hero1"]["name"] == "Грейвс"
    assert body["duos"][0]["winrate"] == pytest.approx(62.4)


def test_api_etag_revalidation(client):
    first = client.get("/api/synergies")
    etag = first.headers["etag"]
    assert etag.startswith('"')
    second = client.get("/api/synergies", headers={"If-None-Match": etag})
    assert second.status_code == 304
    assert second.headers["etag"] == etag


def test_api_tier_filter_has_all_grades(client):
    tiers = {d["tier"] for d in client.get("/api/synergies").json()["duos"]}
    assert tiers == {"S", "A", "B", "C"}
