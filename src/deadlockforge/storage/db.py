"""SQLite persistence for DeadlockForge: builds, reviews, profile, AI config.

One instance — one connection (check_same_thread=False + Lock), WAL mode.
Meant to be used from FastAPI endpoints that run in a worker thread while
the desktop shell (pywebview) owns the main thread.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from pathlib import Path
from typing import Any

from .models import (
    AI_CONFIG_DEFAULTS,
    PROFILE_DEFAULTS,
    REVIEW_JSON_COLUMNS,
    BUILD_JSON_COLUMNS,
)

_shared_db: "Database | None" = None
_shared_lock = threading.Lock()


def get_database(path: str | Path | None = None) -> "Database":
    """Process-wide shared Database (the FastAPI app uses one instance).

    Path resolution: explicit argument first, then the DEADLOCK_DATA_DIR
    env var (set by run_desktop.py before the app import), then the
    project-root data/ directory resolved from this file's location.
    """
    global _shared_db
    if _shared_db is not None and path is None:
        return _shared_db
    with _shared_lock:
        if _shared_db is None:
            if path is None:
                env = os.environ.get("DEADLOCK_DATA_DIR")
                if env:
                    path = Path(env) / "deadlockforge.db"
                else:
                    path = (
                        Path(__file__).resolve().parents[3]
                        / "data" / "deadlockforge.db"
                    )
            _shared_db = Database(path)
        return _shared_db

_SCHEMA = """
CREATE TABLE IF NOT EXISTS user_profile (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    steam_id TEXT,
    account_id TEXT,
    player_name TEXT DEFAULT 'Deadlock Operator',
    rank_tier TEXT DEFAULT 'Ascendant',
    rank_subrank INTEGER DEFAULT 1,
    mmr INTEGER DEFAULT 1600,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS ai_config (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    provider TEXT NOT NULL DEFAULT 'openai',
    model_name TEXT NOT NULL DEFAULT 'glm-5.3-flash',
    api_key TEXT NOT NULL DEFAULT '',
    base_url TEXT,
    enable_thinking BOOLEAN DEFAULT 1,
    reasoning_effort TEXT DEFAULT 'low',
    thinking_budget INTEGER DEFAULT 2048,
    temperature REAL DEFAULT 0.2,
    system_prompt_override TEXT
);
CREATE TABLE IF NOT EXISTS builds (
    id TEXT PRIMARY KEY,
    hero_id TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT,
    weapon_slots TEXT NOT NULL,
    vitality_slots TEXT NOT NULL,
    spirit_slots TEXT NOT NULL,
    flex_slots TEXT NOT NULL,
    base64_code TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS match_reviews (
    match_id TEXT PRIMARY KEY,
    hero_id TEXT NOT NULL,
    duration_seconds INTEGER NOT NULL,
    result TEXT NOT NULL,
    kda TEXT NOT NULL,
    souls_curve TEXT NOT NULL,
    performance_grade TEXT NOT NULL,
    key_mistake TEXT NOT NULL,
    coach_conclusion TEXT NOT NULL,
    stat_benchmarks TEXT NOT NULL,
    item_analysis TEXT NOT NULL,
    teamfights TEXT,
    purchase_timings TEXT,
    death_breakdown TEXT,
    lane_comparison TEXT,
    account_id INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS api_cache (
    cache_key TEXT PRIMARY KEY,
    payload TEXT NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS match_raw (
    match_id TEXT PRIMARY KEY,
    payload TEXT NOT NULL,
    fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_builds_hero ON builds(hero_id);
CREATE INDEX IF NOT EXISTS idx_reviews_created ON match_reviews(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_api_cache_key ON api_cache(cache_key);
"""

_REQUIRED_BUILD_FIELDS = ("id", "hero_id", "title", "weapon_slots",
                          "vitality_slots", "spirit_slots", "flex_slots")
_BUILD_COLUMNS = ("id", "hero_id", "title", "description", "weapon_slots",
                  "vitality_slots", "spirit_slots", "flex_slots",
                  "base64_code", "created_at", "updated_at")
_REVIEW_COLUMNS = ("match_id", "hero_id", "duration_seconds", "result", "kda",
                   "souls_curve", "performance_grade", "key_mistake",
                   "coach_conclusion", "stat_benchmarks", "item_analysis",
                   "teamfights", "purchase_timings", "death_breakdown", "lane_comparison",
                   "account_id", "created_at")
_PROFILE_COLUMNS = ("id", "steam_id", "account_id", "player_name",
                    "rank_tier", "rank_subrank", "mmr",
                    "created_at", "updated_at")
_AI_CONFIG_COLUMNS = ("id", "provider", "model_name", "api_key", "base_url",
                      "enable_thinking", "reasoning_effort", "thinking_budget",
                      "temperature", "system_prompt_override")


def _mean(values: list[float]) -> float | None:
    """Среднее или None для пустой выборки (без деления на ноль)."""
    if not values:
        return None
    return round(sum(values) / len(values), 2)


def _parse_kda(value: Any) -> tuple[int, int, int]:
    """'14/3/9' или {'kills':..,'deaths':..,'assists':..} -> (k, d, a)."""
    if isinstance(value, dict):
        try:
            return (int(value.get("kills") or 0), int(value.get("deaths") or 0),
                    int(value.get("assists") or 0))
        except (TypeError, ValueError):
            return 0, 0, 0
    parts = str(value or "").replace(" ", "").split("/")
    parts = (parts + ["0", "0", "0"])[:3]
    try:
        return int(parts[0] or 0), int(parts[1] or 0), int(parts[2] or 0)
    except (TypeError, ValueError):
        return 0, 0, 0


class Database:
    """SQLite storage manager. Thread-safe, one connection per instance."""

    def __init__(self, path: str | Path = Path("data") / "deadlockforge.db") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(
            str(self.path),
            check_same_thread=False,
            timeout=10.0,
        )
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous = NORMAL")
            self._conn.execute("PRAGMA temp_store = MEMORY")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.executescript(_SCHEMA)
            try:
                self._conn.execute("ALTER TABLE ai_config ADD COLUMN reasoning_effort TEXT DEFAULT 'low'")
            except sqlite3.OperationalError:
                pass
            # V2.0: точка зрения игрока (account_id) нужна для сравнения «с собой».
            try:
                self._conn.execute("ALTER TABLE match_reviews ADD COLUMN account_id INTEGER")
            except sqlite3.OperationalError:
                pass
            # V2.0: расширенные аналитические блоки (teamfights, purchase_timings, death_breakdown, lane_comparison)
            for col in ("teamfights", "purchase_timings", "death_breakdown", "lane_comparison"):
                try:
                    self._conn.execute(f"ALTER TABLE match_reviews ADD COLUMN {col} TEXT")
                except sqlite3.OperationalError:
                    pass
            self._conn.commit()

    # -- context manager ---------------------------------------------------

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        """Close the single connection; further use raises ProgrammingError."""
        with self._lock:
            self._conn.close()

    # -- internal helpers ---------------------------------------------------

    def _execute(self, sql: str, params: tuple | list = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur

    def _query_all(self, sql: str, params: tuple | list = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def _query_one(self, sql: str, params: tuple | list = ()) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(sql, params).fetchone()

    @staticmethod
    def _dump(value: Any) -> str:
        return value if isinstance(value, str) else json.dumps(
            value, ensure_ascii=False, separators=(",", ":"))

    @staticmethod
    def _load(value: Any) -> Any:
        if value is None or value == "":
            return None
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return value

    # -- builds ---------------------------------------------------------------

    def save_build(self, build: dict) -> dict:
        """Insert or update a build by id. JSON slots round-trip as lists."""
        missing = [f for f in _REQUIRED_BUILD_FIELDS if f not in build]
        if missing:
            raise ValueError(f"build is missing required fields: {missing}")
        row = {
            "id": str(build["id"]),
            "hero_id": str(build["hero_id"]),
            "title": str(build["title"]),
            "description": build.get("description"),
            "weapon_slots": self._dump(build["weapon_slots"]),
            "vitality_slots": self._dump(build["vitality_slots"]),
            "spirit_slots": self._dump(build["spirit_slots"]),
            "flex_slots": self._dump(build["flex_slots"]),
            "base64_code": build.get("base64_code"),
        }
        self._execute(
            """INSERT INTO builds (id, hero_id, title, description, weapon_slots,
                   vitality_slots, spirit_slots, flex_slots, base64_code,
                   created_at, updated_at)
               VALUES (:id, :hero_id, :title, :description, :weapon_slots,
                       :vitality_slots, :spirit_slots, :flex_slots, :base64_code,
                       CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
               ON CONFLICT(id) DO UPDATE SET
                   hero_id=excluded.hero_id, title=excluded.title,
                   description=excluded.description,
                   weapon_slots=excluded.weapon_slots,
                   vitality_slots=excluded.vitality_slots,
                   spirit_slots=excluded.spirit_slots,
                   flex_slots=excluded.flex_slots,
                   base64_code=excluded.base64_code,
                   updated_at=CURRENT_TIMESTAMP""",
            row,
        )
        stored = self._query_one("SELECT * FROM builds WHERE id = ?", (row["id"],))
        return self._build_row_to_dict(stored)  # type: ignore[arg-type]

    def get_builds(self, hero_id: str | None = None) -> list[dict]:
        """All builds (optionally filtered by hero_id), newest first."""
        if hero_id is None:
            rows = self._query_all(
                "SELECT * FROM builds ORDER BY datetime(updated_at) DESC, id")
        else:
            rows = self._query_all(
                "SELECT * FROM builds WHERE hero_id = ? "
                "ORDER BY datetime(updated_at) DESC, id", (hero_id,))
        return [self._build_row_to_dict(r) for r in rows]

    def delete_build(self, id: str) -> bool:
        """Delete a build; True if a row was actually removed."""
        cur = self._execute("DELETE FROM builds WHERE id = ?", (id,))
        return cur.rowcount > 0

    def _build_row_to_dict(self, row: sqlite3.Row) -> dict:
        d = {k: row[k] for k in _BUILD_COLUMNS}
        for col in BUILD_JSON_COLUMNS:
            d[col] = self._load(d[col])
        return d

    # -- match reviews ----------------------------------------------------------

    def save_review(self, review: dict) -> dict:
        """Insert or update a match review by match_id."""
        row = {
            "match_id": str(review["match_id"]),
            "hero_id": str(review["hero_id"]),
            "duration_seconds": int(review["duration_seconds"]),
            "result": str(review["result"]),
            "kda": self._dump(review["kda"]),
            "souls_curve": self._dump(review["souls_curve"]),
            "performance_grade": str(review["performance_grade"]),
            "key_mistake": self._dump(review["key_mistake"]),
            "coach_conclusion": self._dump(review["coach_conclusion"]),
            "stat_benchmarks": self._dump(review["stat_benchmarks"]),
            "item_analysis": self._dump(review["item_analysis"]),
            "teamfights": self._dump(review.get("teamfights") or []),
            "purchase_timings": self._dump(review.get("purchase_timings") or []),
            "death_breakdown": self._dump(review.get("death_breakdown") or []),
            "lane_comparison": self._dump(review.get("lane_comparison")),
            "account_id": (int(review["account_id"])
                           if review.get("account_id") is not None else None),
        }
        self._execute(
            """INSERT INTO match_reviews (match_id, hero_id, duration_seconds,
                   result, kda, souls_curve, performance_grade, key_mistake,
                   coach_conclusion, stat_benchmarks, item_analysis,
                   teamfights, purchase_timings, death_breakdown, lane_comparison,
                   account_id, created_at)
               VALUES (:match_id, :hero_id, :duration_seconds, :result, :kda,
                       :souls_curve, :performance_grade, :key_mistake,
                       :coach_conclusion, :stat_benchmarks, :item_analysis,
                       :teamfights, :purchase_timings, :death_breakdown, :lane_comparison,
                       :account_id, CURRENT_TIMESTAMP)
               ON CONFLICT(match_id) DO UPDATE SET
                   hero_id=excluded.hero_id,
                   duration_seconds=excluded.duration_seconds,
                   result=excluded.result, kda=excluded.kda,
                   souls_curve=excluded.souls_curve,
                   performance_grade=excluded.performance_grade,
                   key_mistake=excluded.key_mistake,
                   coach_conclusion=excluded.coach_conclusion,
                   stat_benchmarks=excluded.stat_benchmarks,
                   item_analysis=excluded.item_analysis,
                   teamfights=excluded.teamfights,
                   purchase_timings=excluded.purchase_timings,
                   death_breakdown=excluded.death_breakdown,
                   lane_comparison=excluded.lane_comparison,
                   account_id=COALESCE(excluded.account_id,
                                       match_reviews.account_id)""",
            row,
        )
        return self.get_review(row["match_id"])  # type: ignore[return-value]

    def get_review(self, match_id: str) -> dict | None:
        row = self._query_one(
            "SELECT * FROM match_reviews WHERE match_id = ?", (match_id,))
        if row is None:
            return None
        return self._review_row_to_dict(row)

    def get_reviews(self, limit: int = 20) -> list[dict]:
        rows = self._query_all(
            "SELECT * FROM match_reviews "
            "ORDER BY datetime(created_at) DESC, match_id LIMIT ?",
            (int(limit),))
        return [self._review_row_to_dict(r) for r in rows]

    def _review_row_to_dict(self, row: sqlite3.Row) -> dict:
        d = {k: row[k] for k in _REVIEW_COLUMNS}
        for col in REVIEW_JSON_COLUMNS:
            d[col] = self._load(d[col])
        if d.get("teamfights") is None:
            d["teamfights"] = []
        if d.get("purchase_timings") is None:
            d["purchase_timings"] = []
        if d.get("death_breakdown") is None:
            d["death_breakdown"] = []
        return d

    def get_hero_player_trends(self, account_id: int | None, hero_slug: str,
                               limit: int = 5,
                               exclude_match_id: str | None = None) -> dict:
        """V2.0 (ТЗ §4.1): средние показатели прошлых игр игрока на герое.

        Источник — сохранённые разборы match_reviews (без сети и LLM):
          - avg_souls_at_10: абсолютные души к 10:00 (stat_benchmarks.actual_souls);
          - avg_early_deaths: смерти до 10-й минуты (stat_benchmarks.early_deaths);
          - avg_kda: отношение (K+A)/D по строке kda.

        Строки, разобранные до V2.0, не имеют ни account_id, ни early_deaths:
        такие записи (account_id IS NULL) считаются своими и попадают в выборку,
        но метрика, которой в них нет, просто не участвует в среднем.
        """
        sql = ("SELECT * FROM match_reviews WHERE lower(hero_id) = lower(?)")
        params: list[Any] = [str(hero_slug)]
        if account_id is not None:
            sql += " AND (account_id = ? OR account_id IS NULL)"
            params.append(int(account_id))
        if exclude_match_id is not None:
            sql += " AND match_id != ?"
            params.append(str(exclude_match_id))
        sql += " ORDER BY datetime(created_at) DESC, match_id DESC LIMIT ?"
        params.append(int(limit))

        rows = [self._review_row_to_dict(r) for r in self._query_all(sql, params)]

        souls: list[float] = []
        early: list[float] = []
        kda_ratios: list[float] = []
        for review in rows:
            bm = review.get("stat_benchmarks")
            bm = bm if isinstance(bm, dict) else {}
            actual = bm.get("actual_souls")
            if isinstance(actual, (int, float)):
                souls.append(float(actual))
            deaths = bm.get("early_deaths")
            if isinstance(deaths, (int, float)):
                early.append(float(deaths))
            k, d, a = _parse_kda(review.get("kda"))
            kda_ratios.append((k + a) / max(1, d))

        return {
            "games": len(rows),
            "hero_id": str(hero_slug),
            "avg_souls_at_10": _mean(souls),
            "avg_early_deaths": _mean(early),
            "avg_kda": _mean(kda_ratios),
            "match_ids": [str(r.get("match_id")) for r in rows],
        }

    # -- user profile -----------------------------------------------------------

    def get_profile(self) -> dict:
        """Row id=1, created with defaults on first call."""
        row = self._query_one("SELECT * FROM user_profile WHERE id = 1")
        if row is None:
            self._execute(
                "INSERT INTO user_profile (id) VALUES (1)")
            row = self._query_one("SELECT * FROM user_profile WHERE id = 1")
        return {k: row[k] for k in _PROFILE_COLUMNS}

    def update_profile(self, **fields: Any) -> dict:
        """Patch profile columns (whitelist; unknown keys raise ValueError)."""
        if "id" in fields:
            raise ValueError("profile id is fixed at 1")
        allowed = [c for c in _PROFILE_COLUMNS if c not in ("id", "created_at")]
        unknown = [k for k in fields if k not in allowed]
        if unknown:
            raise ValueError(f"unknown profile fields: {unknown}")
        if fields:
            sets = ", ".join(f"{k} = ?" for k in fields)
            self._execute(
                f"UPDATE user_profile SET {sets}, "
                "updated_at = CURRENT_TIMESTAMP WHERE id = 1",
                tuple(fields.values()),
            )
        return self.get_profile()

    # -- AI config -----------------------------------------------------------------

    def get_ai_config(self) -> dict:
        """Row id=1; a default row is created on first call."""
        row = self._query_one("SELECT * FROM ai_config WHERE id = 1")
        if row is None:
            self._execute("INSERT INTO ai_config (id) VALUES (1)")
            row = self._query_one("SELECT * FROM ai_config WHERE id = 1")
        return {k: row[k] for k in _AI_CONFIG_COLUMNS}

    def update_ai_config(self, **fields: Any) -> dict:
        """Patch AI config columns (whitelist; unknown keys raise ValueError)."""
        if "id" in fields:
            raise ValueError("ai_config id is fixed at 1")
        allowed = [c for c in _AI_CONFIG_COLUMNS if c != "id"]
        unknown = [k for k in fields if k not in allowed]
        if unknown:
            raise ValueError(f"unknown ai_config fields: {unknown}")
        if fields:
            sets = ", ".join(f"{k} = ?" for k in fields)
            self._execute(
                f"UPDATE ai_config SET {sets} WHERE id = 1",
                tuple(fields.values()),
            )
        return self.get_ai_config()

    # -- raw match cache (TZ v1.4 D3: метаданные матча неизменны) -----------

    def get_match_raw(self, match_id: str) -> dict | None:
        """Сырые метаданные матча из кэша (TTL — навсегда). None если нет."""
        row = self._query_one(
            "SELECT payload FROM match_raw WHERE match_id = ?",
            (str(match_id),))
        if row is None:
            return None
        try:
            return json.loads(row["payload"])
        except (TypeError, ValueError):
            return None

    def set_match_raw(self, match_id: str, payload: dict) -> None:
        """Сохранить сырье матча; повторные разборы квоту API не тратят."""
        self._execute(
            """INSERT INTO match_raw (match_id, payload, fetched_at)
               VALUES (?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(match_id) DO NOTHING""",
            (str(match_id),
             json.dumps(payload, ensure_ascii=False, separators=(",", ":"))),
        )

    # -- generic API cache (TZ A1: recent-matches, 10 min TTL) ----------------

    def get_cached(self, cache_key: str, ttl_seconds: float) -> dict | list | None:
        """Return cached JSON payload if younger than ttl_seconds, else None."""
        row = self._query_one(
            "SELECT payload, updated_at FROM api_cache WHERE cache_key = ?",
            (cache_key,))
        if row is None:
            return None
        from datetime import datetime, timezone

        try:
            raw_ts = str(row["updated_at"])
            if not raw_ts.endswith("Z") and "+" not in raw_ts and "-" not in raw_ts[10:]:
                raw_ts += "+00:00"
            updated = datetime.fromisoformat(raw_ts)
        except ValueError:
            updated = None
        if updated is not None:
            now_utc = datetime.now(timezone.utc)
            age = (now_utc - updated).total_seconds()
            if age > ttl_seconds:
                return None
        try:
            return json.loads(row["payload"])
        except (TypeError, ValueError):
            return None

    def set_cached(self, cache_key: str, payload: dict | list) -> None:
        """Store a JSON payload with a fresh updated_at timestamp."""
        self._execute(
            """INSERT INTO api_cache (cache_key, payload, updated_at)
               VALUES (?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(cache_key) DO UPDATE SET
                   payload=excluded.payload,
                   updated_at=CURRENT_TIMESTAMP""",
            (cache_key, json.dumps(payload, ensure_ascii=False)),
        )


__all__ = ["Database"]
