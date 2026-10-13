"""Live calibration and verification suite for Gemini 3.8 Flash AI Coach.

Tests AI coach performance, tactical accuracy, and strict Pydantic CoachReport
schema validation against real match datasets stored in SQLite (data/deadlockforge.db).

Scope (TZ v1.6 Part 2):
1. Load real matches from match_raw table (varying heroes, wins, defeats).
2. Call gemini-3.8-flash via GeminiClient.
3. Validate 100% Pydantic schema compliance with CoachReport.
4. Verify depth, tactical correctness, farm benchmark comparisons, and practical exercises.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from deadlockforge.ai import client as cl
from deadlockforge.ai import coach
from deadlockforge.engine import match_loader
from deadlockforge.models import AIConfig, CoachReport

DB_PATH = Path(__file__).resolve().parents[1] / "data" / "deadlockforge.db"
API_KEY_FILE = SRC / "deadlockforge" / "ai" / "api.txt"


def _get_live_api_key() -> str:
    """Read API key from env or api.txt file."""
    env_key = os.environ.get("DEADLOCKFORGE_AI_KEY", "").strip()
    if env_key:
        return env_key
    if API_KEY_FILE.is_file():
        try:
            key = API_KEY_FILE.read_text(encoding="utf-8").strip()
            if key:
                return key
        except OSError:
            pass
    root_key = Path(__file__).resolve().parents[1] / "api.txt"
    if root_key.is_file():
        try:
            key = root_key.read_text(encoding="utf-8").strip()
            if key:
                return key
        except OSError:
            pass
    return ""


@pytest.fixture(scope="module")
def api_key():
    key = _get_live_api_key()
    if not key:
        pytest.skip("No API key found in api.txt or DEADLOCKFORGE_AI_KEY env var.")
    return key


@pytest.fixture(scope="module")
def gemini_client(api_key):
    config = AIConfig(
        provider="gemini",
        model_name="gemini-3.8-flash",
        api_key=api_key,
        base_url="https://api.zexkora.cc/v1" if api_key.startswith("sk-") else None,
        enable_thinking=False,
        temperature=0.2,
    )
    return cl.create_client(config)


@pytest.fixture(scope="module")
def hero_map():
    return match_loader.load_hero_map()


@pytest.fixture(scope="module")
def db_matches(hero_map):
    if not DB_PATH.is_file():
        pytest.skip("Database deadlockforge.db not found.")
    con = sqlite3.connect(str(DB_PATH))
    cur = con.cursor()
    rows = cur.execute("SELECT match_id, payload FROM match_raw").fetchall()
    con.close()
    if not rows:
        pytest.skip("No matches in match_raw table.")
    
    matches = {}
    for mid, payload in rows:
        try:
            raw = json.loads(payload)
            norm = match_loader.normalize_match(raw, hero_map)
            matches[str(mid)] = norm
        except Exception:
            continue
    return matches


# --- Live Tests on 3-4 Different Matches ---


@pytest.mark.parametrize("match_id", ["777777", "113150202", "32250083"])
def test_gemini_coach_live_analysis(match_id, db_matches, gemini_client):
    """Run real gemini-3.8-flash analysis on real matches from SQLite."""
    if match_id not in db_matches:
        pytest.skip(f"Match {match_id} not found in database fixture.")

    match = db_matches[match_id]
    player = match.players[0]

    import time
    time.sleep(2)
    try:
        report = coach.analyze_match(match, player.account_id, gemini_client)
    except cl.LLMError as e:
        if "429" in str(e) or "cooling down" in str(e):
            pytest.skip(f"Live API rate-limited (cooling down): {e}")
        raise

    # 1. Pydantic validation: 100% compliant
    assert isinstance(report, CoachReport)
    re_validated = CoachReport.model_validate(report.model_dump())
    assert re_validated.performance_grade == report.performance_grade

    # 2. Performance grade in valid vocabulary
    assert report.performance_grade in {"S+", "S", "A", "B", "C"}

    # 3. Key mistake structure and depth
    assert report.key_mistake.time != ""
    assert report.key_mistake.location != ""
    assert report.key_mistake.event != ""
    assert report.key_mistake.why_fatal != ""
    assert report.key_mistake.correct_play != ""
    assert len(report.key_mistake.why_fatal) > 15, "Key mistake why_fatal should be substantive"
    assert len(report.key_mistake.correct_play) > 15, "Key mistake correct_play should be substantive"

    # 4. Coach conclusion: 3-5 substantive points with exercises
    assert 3 <= len(report.coach_conclusion) <= 5
    for item in report.coach_conclusion:
        assert len(item) > 15, f"Conclusion item too brief: '{item}'"

    # 5. Benchmarks present
    assert report.benchmarks.souls_at_10 >= 0.0
    assert report.benchmarks.lane_efficiency >= 0.0
    assert report.benchmarks.fight_participation >= 0.0

    # 6. Item corrections (if present) are structured
    for ic in report.item_corrections:
        assert ic.slot != ""
        assert ic.recommended != ""
        assert ic.reason != ""


def test_gemini_coach_streaming_live(db_matches, gemini_client):
    """Verify live streaming SSE on Gemini 3.8 Flash."""
    match_id = "113150202"
    if match_id not in db_matches:
        # Fallback to first available match
        match_id = next(iter(db_matches.keys()))

    match = db_matches[match_id]
    player = match.players[0]

    received_chars: list[int] = []

    def on_chunk(chars: int):
        received_chars.append(chars)

    import time
    time.sleep(2)
    try:
        report = coach.analyze_match_stream(
            match, player.account_id, gemini_client, on_chunk=on_chunk
        )
    except cl.LLMError as e:
        if "429" in str(e) or "cooling down" in str(e):
            pytest.skip(f"Live API rate-limited (cooling down): {e}")
        raise

    assert isinstance(report, CoachReport)
    assert len(received_chars) > 0, "Expected at least 1 streaming chunk update"
    assert received_chars[-1] > 200, "Expected substantive character stream"
    assert report.performance_grade in {"S+", "S", "A", "B", "C"}
    assert 3 <= len(report.coach_conclusion) <= 5


def test_tactical_knowledge_prompt_pack_injection():
    """Verify prompt pack contains all tactical components from TZ v1.6."""
    from deadlockforge.ai import deadlock_knowledge

    prompt = deadlock_knowledge.get_tactical_knowledge_prompt()
    assert "The Cursed Apple" in prompt
    assert "0:00" in prompt and "3:00" in prompt and "10:00" in prompt
    assert "12:00" in prompt and "15:00" in prompt and "18:00" in prompt
    assert "Soul Urn" in prompt
    assert "Midboss" in prompt
    assert "Healbane" in prompt and "Toxic Bullets" in prompt
    assert "Metal Skin" in prompt and "Return Fire" in prompt
    assert "Knockdown" in prompt and "Curse" in prompt
    assert "Colossus" in prompt and "Bullet Resist Shredder" in prompt
    assert "5000+" in prompt and "3500+" in prompt
