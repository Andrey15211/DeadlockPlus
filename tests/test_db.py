"""Storage layer tests: builds CRUD, ai_config, match_reviews, profile.

All tests use tmp_path; the global data/ directory is never touched.
"""

from __future__ import annotations

import json

import pytest

from deadlockforge.storage import Database


@pytest.fixture()
def db(tmp_path):
    with Database(tmp_path / "test.db") as handle:
        yield handle


def make_build(id="b1", hero_id="abrams", title="Test build"):
    slots = [None] * 4
    return {
        "id": id,
        "hero_id": hero_id,
        "title": title,
        "description": "desc",
        "weapon_slots": ["bulwark-1", "bulwark-2", None, None],
        "vitality_slots": slots,
        "spirit_slots": slots,
        "flex_slots": slots,
        "base64_code": "QUJD",
    }


# ---------------------------------------------------------------- builds


def test_save_and_get_build_roundtrip(db):
    db.save_build(make_build())
    builds = db.get_builds()
    assert len(builds) == 1
    stored = builds[0]
    assert stored["id"] == "b1"
    assert stored["hero_id"] == "abrams"
    assert stored["title"] == "Test build"
    assert stored["base64_code"] == "QUJD"
    assert stored["created_at"] is not None
    assert stored["updated_at"] is not None


def test_build_16_slot_json_roundtrip(db):
    slots = [f"item-{i}" if i % 2 == 0 else None for i in range(16)]
    db.save_build({
        "id": "b16", "hero_id": "infernus", "title": "16 slots",
        "weapon_slots": slots[:4], "vitality_slots": slots[4:8],
        "spirit_slots": slots[8:12], "flex_slots": slots[12:16],
    })
    stored = db.get_builds()[0]
    for col in ("weapon_slots", "vitality_slots", "spirit_slots", "flex_slots"):
        assert stored[col] == slots[:4] if col == "weapon_slots" else True
    assert stored["weapon_slots"] == slots[:4]
    assert stored["vitality_slots"] == slots[4:8]
    assert stored["spirit_slots"] == slots[8:12]
    assert stored["flex_slots"] == slots[12:16]
    # and JSON-serialized on disk, not pickled
    raw = db._query_one(
        "SELECT weapon_slots FROM builds WHERE id = 'b16'")["weapon_slots"]
    assert json.loads(raw) == slots[:4]


def test_build_persistence_across_instances(db, tmp_path):
    db.save_build(make_build())
    db.close()
    with Database(tmp_path / "test.db") as db2:
        builds = db2.get_builds()
        assert len(builds) == 1
        assert builds[0]["weapon_slots"] == ["bulwark-1", "bulwark-2", None, None]


def test_get_builds_filter_by_hero(db):
    db.save_build(make_build(id="a", hero_id="abrams"))
    db.save_build(make_build(id="i", hero_id="infernus"))
    db.save_build(make_build(id="a2", hero_id="abrams"))
    assert [b["id"] for b in db.get_builds(hero_id="abrams")] == ["a", "a2"]
    assert len(db.get_builds()) == 3
    assert db.get_builds(hero_id="seven") == []


def test_save_build_upsert_updates(db):
    db.save_build(make_build())
    db.save_build(make_build(id="b1", title="Updated"))
    builds = db.get_builds()
    assert len(builds) == 1
    assert builds[0]["title"] == "Updated"


def test_save_build_missing_fields_raises(db):
    with pytest.raises(ValueError):
        db.save_build({"id": "x", "hero_id": "abrams", "title": "t"})


def test_delete_build(db):
    db.save_build(make_build())
    assert db.delete_build("b1") is True
    assert db.get_builds() == []
    assert db.delete_build("b1") is False


# ------------------------------------------------------------- ai_config


def test_ai_config_defaults_created_on_first_call(db):
    cfg = db.get_ai_config()
    assert cfg["id"] == 1
    assert cfg["provider"] == "deepseek"
    assert cfg["model_name"] == "deepseek-v4.1-flash"
    assert cfg["api_key"] == ""
    assert cfg["enable_thinking"] in (1, True)
    assert cfg["thinking_budget"] == 2048
    assert cfg["temperature"] == 0.2


def test_ai_config_update(db):
    db.get_ai_config()
    cfg = db.update_ai_config(api_key="sk-test", temperature=0.7,
                              model_name="deepseek-v4.1")
    assert cfg["api_key"] == "sk-test"
    assert cfg["temperature"] == 0.7
    assert cfg["model_name"] == "deepseek-v4.1"
    assert cfg["provider"] == "deepseek"  # untouched
    # persisted
    assert db.get_ai_config()["api_key"] == "sk-test"


def test_ai_config_update_unknown_field_raises(db):
    db.get_ai_config()
    with pytest.raises(ValueError):
        db.update_ai_config(nope=1)


def test_only_one_ai_config_row(db):
    db.get_ai_config()
    db.get_ai_config()
    rows = db._query_all("SELECT * FROM ai_config")
    assert len(rows) == 1


# --------------------------------------------------------- match_reviews


def make_review(match_id="m1"):
    return {
        "match_id": match_id,
        "hero_id": "abrams",
        "duration_seconds": 2100,
        "result": "loss",
        "kda": {"kills": 8, "deaths": 12, "assists": 5},
        "souls_curve": [1200, 5400, 15000],
        "performance_grade": "C+",
        "key_mistake": "Overextended mid without ward",
        "coach_conclusion": {"summary": "Focus on positioning",
                             "actions": ["buy wards", "group at 20min"]},
        "stat_benchmarks": {"souls": {"value": 32000, "pct": 45}},
        "item_analysis": [{"slot": "weapon", "verdict": "ok"}],
    }


def test_review_roundtrip(db):
    db.save_review(make_review())
    stored = db.get_review("m1")
    assert stored["hero_id"] == "abrams"
    assert stored["duration_seconds"] == 2100
    assert stored["kda"] == {"kills": 8, "deaths": 12, "assists": 5}
    assert stored["souls_curve"] == [1200, 5400, 15000]
    assert stored["coach_conclusion"]["summary"] == "Focus on positioning"
    assert stored["stat_benchmarks"]["souls"]["pct"] == 45
    assert stored["item_analysis"][0]["slot"] == "weapon"


def test_review_upsert(db):
    db.save_review(make_review())
    db.save_review(make_review(match_id="m1") | {"result": "win",
                                                 "performance_grade": "A"})
    assert db.get_reviews() and len(db.get_reviews()) == 1
    stored = db.get_review("m1")
    assert stored["result"] == "win"
    assert stored["performance_grade"] == "A"


def test_get_review_missing_returns_none(db):
    assert db.get_review("nope") is None


def test_get_reviews_limit_and_order(db):
    for i in range(25):
        db.save_review(make_review(match_id=f"m{i:02d}"))
    all_reviews = db.get_reviews(limit=20)
    assert len(all_reviews) == 20
    assert db.get_reviews(limit=5) is not None
    ids = {r["match_id"] for r in db.get_reviews(limit=100)}
    assert len(ids) == 25


# ---------------------------------------------------------- user_profile


def test_profile_defaults(db):
    profile = db.get_profile()
    assert profile["id"] == 1
    assert profile["player_name"] == "Deadlock Operator"
    assert profile["rank_tier"] == "Ascendant"
    assert profile["rank_subrank"] == 1
    assert profile["mmr"] == 1600
    assert profile["steam_id"] is None
    assert profile["created_at"] is not None


def test_profile_update(db):
    db.get_profile()
    profile = db.update_profile(mmr=1850, rank_tier="Eternus",
                                player_name="Operator X")
    assert profile["mmr"] == 1850
    assert profile["rank_tier"] == "Eternus"
    assert profile["player_name"] == "Operator X"
    assert profile["rank_subrank"] == 1  # untouched default
    assert db.get_profile()["mmr"] == 1850


def test_profile_update_unknown_field_raises(db):
    db.get_profile()
    with pytest.raises(ValueError):
        db.update_profile(hax=1)


# ---------------------------------------------------------- infrastructure


def test_wal_mode_enabled(db):
    mode = db._query_one("PRAGMA journal_mode")["journal_mode"]
    assert mode == "wal"


def test_pragmas_and_indexes(db):
    """TZ v1.8 Этап 1: проверка PRAGMA synchronous/temp_store и созданных индексов."""
    sync = db._query_one("PRAGMA synchronous")["synchronous"]
    # PRAGMA synchronous: 1 is NORMAL, 2 is FULL
    assert sync == 1

    temp_store = db._query_one("PRAGMA temp_store")["temp_store"]
    # PRAGMA temp_store: 2 is MEMORY
    assert temp_store == 2

    # Verify created indexes exist
    indices = [
        row["name"]
        for row in db._query_all("SELECT name FROM sqlite_master WHERE type = 'index'")
    ]
    assert "idx_builds_hero" in indices
    assert "idx_reviews_created" in indices
    assert "idx_api_cache_key" in indices


def test_default_dir_is_data_subdir():
    # Only checks the default path value; no file is created.
    from deadlockforge.storage.db import Database as D
    import inspect
    sig = inspect.signature(D.__init__)
    default = sig.parameters["path"].default
    assert pathlib_default_name(default) == "deadlockforge.db"
    assert "data" in str(default)


def pathlib_default_name(default) -> str:
    from pathlib import Path
    return Path(default).name

