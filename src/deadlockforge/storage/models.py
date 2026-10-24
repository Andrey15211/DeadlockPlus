"""Shared constants for the storage layer.

Kept separate from the SQL DDL in db.py so tests and future modules can
import the defaults and column whitelists without pulling in sqlite3.
"""

from __future__ import annotations

from typing import Any

# Column defaults, mirroring the DDL in db.py.
PROFILE_DEFAULTS: dict[str, Any] = {
    "player_name": "Deadlock Operator",
    "rank_tier": "Ascendant",
    "rank_subrank": 1,
    "mmr": 1600,
}

AI_CONFIG_DEFAULTS: dict[str, Any] = {
    "provider": "openai",
    "model_name": "deepseek-v4.1-flash",
    "api_key": "",
    "base_url": None,
    "enable_thinking": True,
    "reasoning_effort": "low",
    "thinking_budget": 2048,
    "temperature": 0.2,
    "system_prompt_override": None,
}

# TEXT columns that hold JSON and must round-trip through json.dumps/loads.
BUILD_JSON_COLUMNS = ("weapon_slots", "vitality_slots", "spirit_slots",
                      "flex_slots")
REVIEW_JSON_COLUMNS = ("kda", "souls_curve", "coach_conclusion",
                       "stat_benchmarks", "item_analysis",
                       "teamfights", "purchase_timings", "death_breakdown", "lane_comparison")
