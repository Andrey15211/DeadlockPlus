"""Deep match loader: fetch + normalize Deadlock match metadata.

Public surface:
    - fetch_match(match_id)          -> raw dict from deadlock-api
    - normalize_match(raw, hero_map) -> NormalizedMatch
    - build_souls_curve(normalized)  -> list[SoulsPoint]
    - parse_screenshot(image_bytes)  -> vision-input hook for the LLM

Only stdlib (urllib.request, json). No SDK, no requests.
"""

from __future__ import annotations

import base64
import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from ..models import SoulsPoint

API_BASE = "https://api.deadlock-api.com"
UA = "Mozilla/5.0"                       # deadlock-api answers 403 without it
FETCH_TIMEOUT = 30
FETCH_RETRIES = 2


class RateLimitError(RuntimeError):
    """429 от deadlock-api: лимит запросов. Человекочитаемый message."""

    def __init__(self, wait_seconds: float | None) -> None:
        self.wait_seconds = wait_seconds
        if wait_seconds and wait_seconds > 0:
            mins = max(1, round(wait_seconds / 60))
            super().__init__(
                f"Лимит API (3 запроса/час). Повторите через {mins} мин.")
        else:
            super().__init__("Лимит API (3 запроса/час). Повторите позже.")


def _parse_429_wait(body: str) -> float | None:
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, TypeError):
        return None
    for key in ("next_request_in", "retry_in", "retry_after"):
        v = data.get(key) if isinstance(data, dict) else None
        if isinstance(v, (int, float)):
            return float(v)
    return None

# World coordinates span roughly +/-10752 units on each axis.
_WORLD_RADIUS = 10752.0


class MatchLoadError(RuntimeError):
    """Raised when match metadata cannot be fetched or parsed."""


# --- dataclasses -----------------------------------------------------------


@dataclass
class ItemTimelinePoint:
    t: int                       # game_time_s of purchase
    item_id: int
    sold: bool = False           # sold_time_s present and > 0


@dataclass
class DeathTimelinePoint:
    t: int
    pos_x: float                 # normalized 0..1, clipped
    pos_y: float
    killer_hero: str             # slug or numeric-id string


@dataclass
class NetworthPoint:
    t: int
    networth: int


@dataclass
class NormalizedPlayer:
    account_id: int
    hero_id_slug: str            # slug if hero_map known, else str(numeric id)
    team: int
    kills: int
    deaths: int
    assists: int
    level: int
    net_worth: int
    items_timeline: list[ItemTimelinePoint] = field(default_factory=list)
    deaths_timeline: list[DeathTimelinePoint] = field(default_factory=list)
    networth_curve: list[NetworthPoint] = field(default_factory=list)


@dataclass
class NormalizedMatch:
    match_id: str
    duration_s: int
    result_per_player: dict[int, bool]        # account_id -> win?
    players: list[NormalizedPlayer]
    team_curve: list[tuple[int, int]]         # [(t, delta team0 - team1)]
    mid_boss_kills: list[dict]                # raw mid_boss entries
    objectives: list[dict]                    # raw objectives (walkers etc.)
    team_score: dict = field(default_factory=dict)

    def player_by_account(self, account_id: int) -> NormalizedPlayer | None:
        for p in self.players:
            if p.account_id == account_id:
                return p
        return None

    def kda(self, *, account_id: int | None = None,
            hero_id: str | int | None = None) -> str:
        """KDA string "14/3/9" for a player found by account_id or hero."""
        player = None
        if account_id is not None:
            player = self.player_by_account(account_id)
        if player is None and hero_id is not None:
            key = str(hero_id)
            for p in self.players:
                if p.hero_id_slug == key:
                    player = p
                    break
        if player is None:
            raise MatchLoadError(f"player not found: account={account_id} hero={hero_id}")
        return f"{player.kills}/{player.deaths}/{player.assists}"


# --- fetching --------------------------------------------------------------


def _request_headers() -> dict[str, str]:
    import os

    headers = {"User-Agent": UA, "Accept": "application/json"}
    key = os.environ.get("DEADLOCK_API_KEY", "").strip()
    if key:
        headers["Authorization"] = f"Bearer {key}"
    return headers


def fetch_match(match_id: str | int, db=None) -> dict:
    """GET /v1/matches/{id}/metadata с UA, таймаутом и 2 ретраями.

    TZ v1.4 D3: при переданном db (Database) сырье кэшируется в таблице
    match_raw навсегда (метаданные матча неизменны) — повторные разборы
    не тратят квоту API. 429 поднимает RateLimitError с человеческим
    текстом.
    """
    mid = str(match_id)
    if db is not None:
        cached = db.get_match_raw(mid)
        if cached is not None:
            return cached
    raw = _fetch_match_http(match_id)
    if db is not None:
        db.set_match_raw(mid, raw)
    return raw


def _fetch_match_http(match_id: str | int) -> dict:
    url = f"{API_BASE}/v1/matches/{match_id}/metadata"
    last_err: Exception | None = None
    for attempt in range(FETCH_RETRIES + 1):
        if attempt:
            time.sleep(0.5 * attempt)
        req = urllib.request.Request(url, headers=_request_headers())
        try:
            with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", "replace")[:300]
            except Exception:  # noqa: BLE001 - body is best-effort context
                pass
            if e.code == 429:
                # Ретраи бессмысленны: квота на час.
                raise RateLimitError(_parse_429_wait(body)) from e
            raise MatchLoadError(
                f"deadlock-api HTTP {e.code} for match {match_id}: {body}"
            ) from e
        except (urllib.error.URLError, TimeoutError) as e:
            last_err = e
            continue
    raise MatchLoadError(
        f"cannot fetch match {match_id} after {FETCH_RETRIES + 1} attempts: {last_err}"
    )


# --- normalization ---------------------------------------------------------


def load_hero_map() -> dict[int, str]:
    """api_id -> slug map from local data/heroes.json. Empty if api_id absent."""
    p3 = Path(__file__).resolve().parents[3] / "data" / "heroes.json"
    p2 = Path(__file__).resolve().parents[2] / "data" / "heroes.json"
    heroes_file = p3 if p3.exists() else p2
    result: dict[int, str] = {}
    try:
        raw = json.loads(heroes_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return result
    for hero in raw:
        api_id = hero.get("api_id")
        if api_id:
            result[int(api_id)] = hero["id"]
    return result


def _norm_coord(x: float, y: float) -> tuple[float, float]:
    """Мировые координаты -> (left, top) в 0..1.

    Ось Y инвертируется: в мире +Y направлен «вверх», а на миникарте top
    растёт вниз. Проверено на 51 нейтральном лагере и 16 позициях башен
    из /v1/assets/map: left = x/(2R)+0.5 и top = 0.5 - y/(2R) совпадают
    для 51/51 и 16/16 объектов, тогда как прежняя формула без инверсии
    давала 2/51 — точки уезжали за пределы игровой зоны.
    """
    left = min(1.0, max(0.0, x / (2.0 * _WORLD_RADIUS) + 0.5))
    top = min(1.0, max(0.0, 0.5 - y / (2.0 * _WORLD_RADIUS)))
    return left, top


def _slug_hero(hero_id, hero_map: dict[int, str] | None) -> str:
    if hero_map and int(hero_id) in hero_map:
        return hero_map[int(hero_id)]
    return str(hero_id)


def normalize_match(raw: dict, hero_map: dict[int, str] | None = None) -> NormalizedMatch:
    """Convert raw API metadata to NormalizedMatch."""
    try:
        info = raw["match_info"]
        raw_players = info["players"]
        winning_team = info.get("winning_team")
    except (KeyError, TypeError) as e:
        raise MatchLoadError(f"unexpected metadata shape: missing {e}") from e

    players: list[NormalizedPlayer] = []
    team_networth: dict[int, dict[int, int]] = {}  # team -> {t: sum}

    for rp in raw_players:
        hero_slug = _slug_hero(rp.get("hero_id", 0), hero_map)
        items: list[ItemTimelinePoint] = []
        for it in rp.get("items") or []:
            items.append(ItemTimelinePoint(
                t=int(it.get("game_time_s") or 0),
                item_id=int(it.get("item_id") or 0),
                sold=bool(it.get("sold_time_s")),
            ))
        deaths: list[DeathTimelinePoint] = []
        for d in rp.get("death_details") or []:
            pos = d.get("death_pos") or {}
            pos_x, pos_y = _norm_coord(
                float(pos.get("x") or 0), float(pos.get("y") or 0)
            )
            deaths.append(DeathTimelinePoint(
                t=int(d.get("game_time_s") or 0),
                pos_x=pos_x,
                pos_y=pos_y,
                killer_hero=_slug_hero(_killer_hero_id(raw_players, d), hero_map),
            ))
        curve: list[NetworthPoint] = []
        for s in rp.get("stats") or []:
            t = int(s.get("time_stamp_s") or 0)
            curve.append(NetworthPoint(t=t, networth=int(s.get("net_worth") or 0)))
            bucket = team_networth.setdefault(int(rp.get("team") or 0), {})
            bucket[t] = bucket.get(t, 0) + int(s.get("net_worth") or 0)

        players.append(NormalizedPlayer(
            account_id=int(rp.get("account_id") or 0),
            hero_id_slug=hero_slug,
            team=int(rp.get("team") or 0),
            kills=int(rp.get("kills") or 0),
            deaths=int(rp.get("deaths") or 0),
            assists=int(rp.get("assists") or 0),
            level=int(rp.get("level") or 0),
            net_worth=int(rp.get("net_worth") or 0),
            items_timeline=items,
            deaths_timeline=deaths,
            networth_curve=curve,
        ))

    # team_curve: delta team0 - team1 per shared 180s sample stamp.
    stamps = sorted(set(team_networth.get(0, {})) | set(team_networth.get(1, {})))
    team_curve: list[tuple[int, int]] = []
    for t in stamps:
        last0 = _last_value(team_networth.get(0, {}), t)
        last1 = _last_value(team_networth.get(1, {}), t)
        team_curve.append((t, last0 - last1))

    match_id = raw.get("match_id")
    if match_id is None:
        match_id = info.get("match_id")
    return NormalizedMatch(
        match_id=str(match_id) if match_id is not None else "",
        duration_s=int(info.get("duration_s") or 0),
        result_per_player={
            p.account_id: (winning_team is not None and p.team == winning_team)
            for p in players
        },
        players=players,
        team_curve=team_curve,
        mid_boss_kills=list(info.get("mid_boss") or []),
        objectives=list(info.get("objectives") or []),
        team_score=info.get("team_score") or {},
    )


def _last_value(series: dict[int, int], at_t: int) -> int:
    """Networth of the team at time at_t (carry last known value forward)."""
    best = 0
    last_t = -1
    for t, v in series.items():
        if t <= at_t and t > last_t:
            last_t = t
            best = v
    return best


def _killer_hero_id(raw_players: list[dict], death: dict) -> int:
    """Resolve killer_player_slot to that player's hero_id."""
    slot = death.get("killer_player_slot")
    for rp in raw_players:
        if rp.get("player_slot") == slot:
            return int(rp.get("hero_id") or 0)
    return 0


# --- souls curve -----------------------------------------------------------


def build_souls_curve(normalized: NormalizedMatch) -> list[SoulsPoint]:
    """Team souls advantage timeline with event annotations."""
    if not normalized.team_curve:
        return []
    stamps = [t for t, _ in normalized.team_curve]
    events: dict[int, str] = {}

    def _attach(t: int, label: str) -> None:
        # snap the event to the nearest curve sample point
        nearest = min(stamps, key=lambda s: abs(s - t))
        events.setdefault(nearest, label)

    for mb in normalized.mid_boss_kills or []:
        t = _obj_time(mb)
        if t is not None:
            _attach(t, "Убийство Midboss")

    for obj in normalized.objectives or []:
        if "walker" not in json.dumps(obj).lower():
            continue
        t = _obj_time(obj)
        if t is not None:
            _attach(t, "Падение Walker")

    # Командный файт: 3+ смертей в окне 60s (по всем игрокам).
    all_deaths = sorted(
        d.t for p in normalized.players for d in p.deaths_timeline
    )
    i = 0
    while i < len(all_deaths):
        j = i
        while j + 1 < len(all_deaths) and all_deaths[j + 1] - all_deaths[i] <= 60:
            j += 1
        if j - i + 1 >= 3:
            t = all_deaths[(i + j) // 2]
            _attach(t, "Командный файт")
        i = j + 1

    return [
        SoulsPoint(time_sec=t, networth_delta=float(delta), event=events.get(t, ""))
        for t, delta in normalized.team_curve
    ]


def _obj_time(entry: dict) -> int | None:
    for key in ("game_time_s", "time_s", "game_time", "time"):
        v = entry.get(key)
        if isinstance(v, (int, float)):
            return int(v)
    return None


# --- screenshot hook -------------------------------------------------------


def parse_screenshot(image_bytes: bytes) -> dict:
    """No OCR here (no tesseract): wrap bytes as a vision-input payload
    for the LLM client. The client turns image_b64 into provider format."""
    if not image_bytes:
        raise MatchLoadError("empty screenshot bytes")
    return {
        "mode": "screenshot",
        "image_b64": base64.b64encode(image_bytes).decode("ascii"),
    }
