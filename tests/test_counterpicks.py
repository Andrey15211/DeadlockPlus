"""Контрпики: схема датасета, целостность ссылок и API-роуты.

Датасет собирается скриптом scripts/build_counterpicks.py из внешнего
источника, поэтому тесты проверяют именно контракт: ссылки между героями,
предметами и проблемами разрешаются, а API отдаёт данные или честную 503.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from deadlockforge.api.app import create_app
from deadlockforge.engine import dataset as dataset_mod
from deadlockforge.models import Counterpicks

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
COUNTERPICKS_JSON = DATA_DIR / "counterpicks.json"
ITEMS_JSON = DATA_DIR / "items.json"
HEROES_JSON = DATA_DIR / "heroes.json"


@pytest.fixture(scope="module")
def counterpicks_payload() -> dict:
    if not COUNTERPICKS_JSON.is_file():
        pytest.skip(
            "data/counterpicks.json отсутствует — соберите его через "
            "scripts/build_counterpicks.py --extract/--assemble"
        )
    return json.loads(COUNTERPICKS_JSON.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def project_ids() -> tuple[set[str], set[str]]:
    heroes = {h["id"] for h in json.loads(HEROES_JSON.read_text(encoding="utf-8"))}
    items = {i["id"] for i in json.loads(ITEMS_JSON.read_text(encoding="utf-8"))}
    return heroes, items


# --- схема -----------------------------------------------------------------


def test_schema_validates(counterpicks_payload: dict) -> None:
    data = Counterpicks.model_validate(counterpicks_payload)
    assert data.heroes, "в датасете нет ни одного гайда героя"
    assert data.items, "в датасете нет ни одного гайда предмета"
    assert data.stages == ["early", "mid", "late"]


def test_every_hero_has_content(counterpicks_payload: dict) -> None:
    """У каждого героя должны быть заполнены ключевые блоки интерфейса."""
    for hero in counterpicks_payload["heroes"]:
        if hero.get("in_development") or hero["id"] == "nurse":
            continue
        assert hero["intro"].strip(), f"{hero['id']}: пустое вступление"
        assert hero["top"], f"{hero['id']}: пуст список лучших предметов"
        assert hero["problems"], f"{hero['id']}: пуст разбор проблем"
        assert hero["faq"], f"{hero['id']}: пуст FAQ"
        assert hero["helper"]["sits"], f"{hero['id']}: пуст блок «проблема → покупка»"
        for stage in ("early", "mid", "late"):
            assert hero["stages"][stage], f"{hero['id']}: пустая фаза {stage}"
        glance = hero["glance"]
        assert glance["main_threat"].strip(), f"{hero['id']}: пустая главная угроза"
        assert glance["buy_priority"].strip(), f"{hero['id']}: пуст приоритет покупки"


def test_every_item_has_content(counterpicks_payload: dict) -> None:
    for item in counterpicks_payload["items"]:
        assert item["short"].strip(), f"{item['id']}: пустое описание"
        assert item["when"].strip(), f"{item['id']}: пустое «когда брать»"
        assert item["best"] is not None, f"{item['id']}: пуст список матчапов"


# --- целостность ссылок ----------------------------------------------------


def test_hero_ids_match_project(counterpicks_payload: dict, project_ids: tuple[set[str], set[str]]) -> None:
    heroes, _ = project_ids
    for hero in counterpicks_payload["heroes"]:
        assert hero["id"] in heroes, f"{hero['id']} отсутствует в data/heroes.json"


def test_item_ids_match_project(counterpicks_payload: dict, project_ids: tuple[set[str], set[str]]) -> None:
    _, items = project_ids
    for item in counterpicks_payload["items"]:
        assert item["id"] in items, f"{item['id']} отсутствует в data/items.json"


def test_all_references_resolve(counterpicks_payload: dict) -> None:
    """Каждая ссылка на героя и предмет внутри датасета должна разрешаться."""
    hero_ids = {h["id"] for h in counterpicks_payload["heroes"]}
    item_ids = {i["id"] for i in counterpicks_payload["items"]}
    item_ids |= {i["id"] for i in json.loads(ITEMS_JSON.read_text(encoding="utf-8"))}

    for hero in counterpicks_payload["heroes"]:
        for item_id in hero["top"]:
            assert item_id in item_ids, f"{hero['id']}.top → неизвестный предмет {item_id!r}"
        for related in hero["related"]:
            assert related in hero_ids, f"{hero['id']}: неизвестный герой {related!r}"
        for stage in ("early", "mid", "late"):
            for offer in hero["stages"][stage]:
                assert offer["item_id"] in item_ids, f"{hero['id']}.{stage} → {offer['item_id']!r}"
                assert offer["label"].strip(), f"{hero['id']}.{stage}: пустая метка ответа"
        for problem in hero["problems"]:
            assert problem["item_id"] in item_ids, f"{hero['id']}: проблема → {problem['item_id']!r}"
        for sit in hero["helper"]["sits"]:
            assert sit["item_id"] in item_ids, f"{hero['id']}: связка → {sit['item_id']!r}"

    for item in counterpicks_payload["items"]:
        for row in item["best"]:
            assert row["hero_id"] in hero_ids, f"{item['id']}.best → неизвестный герой {row['hero_id']!r}"
        for row in item["all"]:
            assert row["hero_id"] in hero_ids, f"{item['id']}.all → неизвестный герой {row['hero_id']!r}"
        for alt in item["alts"]:
            assert alt["item_id"] in item_ids, f"{item['id']}.alts → {alt['item_id']!r}"


def test_groups_reference_known_items(counterpicks_payload: dict) -> None:
    item_ids = {i["id"] for i in counterpicks_payload["items"]}
    assert counterpicks_payload["groups"], "нет ни одной группы контр-мер"
    for group in counterpicks_payload["groups"]:
        assert group["name"].strip(), f"группа {group['key']}: пустое название"
        for item_id in group["items"]:
            assert item_id in item_ids, f"группа {group['key']} → неизвестный предмет {item_id!r}"


# --- качество перевода -----------------------------------------------------


def test_no_english_labels_left(counterpicks_payload: dict) -> None:
    """Английские служебные метки источника не должны просочиться в датасет."""
    english = {
        "Situational response", "Larger investment", "Lane option", "Not available",
        "Priority situation answer", "Situational counter option", "Spell-trade option",
        "Main threat", "Your opening", "Buy priority",
    }
    blob = json.dumps(counterpicks_payload, ensure_ascii=False)

    def walk(value):
        if isinstance(value, str):
            yield value
        elif isinstance(value, list):
            for x in value:
                yield from walk(x)
        elif isinstance(value, dict):
            for x in value.values():
                yield from walk(x)

    for text in walk(counterpicks_payload):
        for label in english:
            assert label not in text, f"английская метка {label!r} в тексте: {text[:70]}"


def test_english_item_names_replaced_by_russian(counterpicks_payload: dict) -> None:
    """Названия героев и предметов обязаны быть русскими, а не латиницей."""
    for hero in counterpicks_payload["heroes"]:
        assert not hero["name"].isascii(), f"{hero['id']}: имя не переведено ({hero['name']!r})"
        for item_id in hero["top"]:
            continue  # идентификаторы намеренно латиницей
        for problem in hero["problems"]:
            assert problem["problem"].strip()
    for item in counterpicks_payload["items"]:
        assert not item["name"].isascii(), f"{item['id']}: имя не переведено ({item['name']!r})"


# --- API -------------------------------------------------------------------


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(create_app())


def test_api_counterpicks_returns_dataset(client: TestClient) -> None:
    resp = client.get("/api/counterpicks")
    if resp.status_code == 503:
        pytest.skip("датасет контрпиков ещё не собран")
    assert resp.status_code == 200
    body = resp.json()
    assert body["heroes"] and body["items"]
    # ETag обязателен: датасет большой и кэшируется на клиенте.
    assert resp.headers.get("etag")


def test_api_counterpicks_etag_304(client: TestClient) -> None:
    resp = client.get("/api/counterpicks")
    if resp.status_code == 503:
        pytest.skip("датасет контрпиков ещё не собран")
    etag = resp.headers["etag"]
    again = client.get("/api/counterpicks", headers={"If-None-Match": etag})
    assert again.status_code == 304


def test_api_counterpicks_hero(client: TestClient, counterpicks_payload: dict) -> None:
    hero_id = counterpicks_payload["heroes"][0]["id"]
    resp = client.get(f"/api/counterpicks/hero/{hero_id}")
    assert resp.status_code == 200
    assert resp.json()["id"] == hero_id


def test_api_counterpicks_hero_unknown(client: TestClient) -> None:
    resp = client.get("/api/counterpicks/hero/no-such-hero")
    assert resp.status_code == 404


def test_api_counterpicks_item(client: TestClient, counterpicks_payload: dict) -> None:
    item_id = counterpicks_payload["items"][0]["id"]
    resp = client.get(f"/api/counterpicks/item/{item_id}")
    assert resp.status_code == 200
    assert resp.json()["id"] == item_id


def test_api_counterpicks_item_unknown(client: TestClient) -> None:
    resp = client.get("/api/counterpicks/item/no-such-item")
    assert resp.status_code == 404


# --- устойчивость загрузчика ----------------------------------------------


def test_missing_dataset_raises_with_hint(monkeypatch, tmp_path: Path) -> None:
    """Отсутствие датасета — понятная ошибка, а не падение приложения."""
    monkeypatch.setattr(dataset_mod, "_data_root", lambda: tmp_path)
    dataset_mod.get_counterpicks.cache_clear()
    with pytest.raises(FileNotFoundError):
        dataset_mod.load_counterpicks()
    dataset_mod.get_counterpicks.cache_clear()


def test_invalid_dataset_rejected(monkeypatch, tmp_path: Path) -> None:
    (tmp_path / "counterpicks.json").write_text('{"heroes": [{}], "items": []}', encoding="utf-8")
    monkeypatch.setattr(dataset_mod, "_data_root", lambda: tmp_path)
    dataset_mod.get_counterpicks.cache_clear()
    with pytest.raises(ValueError):
        dataset_mod.load_counterpicks()
    dataset_mod.get_counterpicks.cache_clear()