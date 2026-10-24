"""Thin HTTP adapter over the DeadlockForge engine.

Every endpoint maps 1:1 to an engine public function; no business logic
lives here. Served static SPA lives in web/ at the project root.

Optional modules (storage, ai, drafter) are imported lazily so the app
boots even while those verticals are being built; their endpoints return
503 until the module is present.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel

from ..engine import builds as builds_mod
from ..engine import build_codec as build_codec_mod
from ..engine import calculator as calculator_mod
from ..engine import dataset as dataset_mod
from ..engine import advisor as advisor_mod
from ..engine import steam_profile as steam_mod
from ..models import (
    Advice,
    AIConfig,
    BuildError,
    BuildSlots,
    Counterpicks,
    Hero,
    Item,
    MapGuide,
    MatchReview,
    RedFlag,
    StatsSnapshot,
    Synergies,
    effective_ai_config,
)


def _project_root() -> Path:
    """Project root: honors frozen builds and env overrides first."""
    env = os.environ.get("DEADLOCK_ROOT")
    if env:
        return Path(env)
    if hasattr(sys_module := __import__("sys"), "_MEIPASS"):  # PyInstaller
        return Path(sys_module._MEIPASS)
    return Path(__file__).resolve().parents[3]


def _web_dir() -> Path:
    env = os.environ.get("DEADLOCK_WEB_DIR")
    if env:
        return Path(env)
    return _project_root() / "web"


def _data_dir() -> Path:
    env = os.environ.get("DEADLOCK_DATA_DIR")
    if env:
        return Path(env)
    return _project_root() / "data"


WEB_DIR = _web_dir()


def _get_storage():
    """Lazy import of the SQLite layer (Subagent 1 vertical)."""
    from ..storage import db as db_mod

    return db_mod


def _get_drafter():
    """Lazy import of the 6v6 draft engine (Subagent 2 vertical)."""
    from ..engine import drafter as drafter_mod

    return drafter_mod


def _get_match_pipeline():
    """Lazy import of match loader + coach (Subagent 3 vertical)."""
    from ..engine import match_loader as ml_mod
    from ..ai import coach as coach_mod
    from ..ai import client as client_mod

    return ml_mod, coach_mod, client_mod


def _db():
    storage = _get_storage()
    return storage.get_database(_data_dir() / "deadlockforge.db")


def _optional(fn):
    """Wrap an endpoint so a missing vertical returns 503, not a crash."""

    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except ImportError as exc:
            raise HTTPException(
                status_code=503, detail=f"Module not available yet: {exc.name}"
            ) from exc

    wrapper.__name__ = fn.__name__
    wrapper.__doc__ = fn.__doc__
    return wrapper


class CalcRequest(BaseModel):
    """POST /api/calc body."""

    hero_id: str
    item_ids: list[str] = []


class BuildCodeRequest(BaseModel):
    """POST /api/builds/import body."""

    code: str


class BuildSaveRequest(BaseModel):
    """POST /api/builds body: persist a build to SQLite."""

    id: str | None = None
    hero_id: str
    title: str
    description: str = ""
    weapon_slots: list[str | None] = []
    vitality_slots: list[str | None] = []
    spirit_slots: list[str | None] = []
    flex_slots: list[str | None] = []
    base64_code: str | None = None


class DraftRequest(BaseModel):
    """POST /api/draft/analyze body."""

    my_team: list[str] = []
    enemy_team: list[str] = []
    allow_mirror: bool = False


class AIConfigUpdate(BaseModel):
    """POST /api/ai/config body: partial update of provider, model, api_key."""

    provider: str | None = None
    model_name: str | None = None
    api_key: str | None = None
    base_url: str | None = None
    reasoning_effort: str | None = None
    enable_thinking: bool | None = None


class DraftScreenshotRequest(BaseModel):
    """POST /api/draft/parse-screenshot body: изображение экрана загрузки/матча."""

    image_b64: str


class DraftBuildsRequest(BaseModel):
    """POST /api/draft/builds body (S5: быстрые сборки по драфту).

    mode — совместимость с UI/прошлыми вызовами: "local"/"offline" = только
    локальный движок, "ai" = локальный движок + короткая LLM-надстройка
    (никогда не блокирует ответ и не влияет на обязательные поля).
    """

    my_hero: str
    enemies: list[str] = []
    mode: str = "local"
    role: str | None = None


class MatchAnalyzeRequest(BaseModel):
    """POST /api/match/analyze body: match id or screenshot (base64)."""

    match_id: str | None = None
    image_b64: str | None = None
    account_id: int | None = None
    hero_slug: str | None = None
    mode: str = "ai"  # "ai" (requires api_key) or "offline" (heuristic)
    depth: str = "medium"  # V2.0: short | medium | deep
    model_name: str | None = None  # V2.0 М4: разовая смена модели разбора
    reasoning_effort: str | None = None  # auto | off | low | medium | high


class ReanalyzeRequest(BaseModel):
    """POST /api/match/reviews/{match_id}/reanalyze body (V2.0 §3.5)."""

    account_id: int | None = None
    hero_slug: str | None = None
    mode: str = "ai"
    depth: str = "medium"


class LiveStateRequest(BaseModel):
    """POST /api/live/state + /api/live/advice body (M4 §6: ручной ввод оверлея).

    Поля опциональны (оверлей позволяет не заполнять всё); невалидные
    идентификаторы, которых нет в каталоге, отклоняются 404, >6 врагов и
    дубликаты — 422.
    """

    my_hero: str = ""
    enemies: list[str] = []
    items: list[str] = []


class LiveClockSync(BaseModel):
    """POST /api/live/clock body — точка отсчёта игровых часов.

    Панель live.html присылает своё время на старте/паузе/правке, сервер
    запоминает точку и дальше считает сам. Нужно, чтобы оверлей-плашки
    знали время даже когда панель закрыта.
    """

    seconds: float = 0.0
    running: bool = False


class LiveOverlayRequest(BaseModel):
    """POST /api/live/overlay body — тумблер плашек поверх игры."""

    enabled: bool


class HotkeyRequest(BaseModel):
    """POST /api/desktop/hotkey body — комбинация вида 'ctrl+f1'."""

    hotkey: str


class PanelRequest(BaseModel):
    """POST /api/desktop/panel body — показать или скрыть панель поверх игры."""

    visible: bool


class TopmostRequest(BaseModel):
    """POST /api/desktop/topmost body — тумблер «Поверх других окон»."""

    enabled: bool


def _resolve_heroes(ids: list[str]) -> list[Hero]:
    catalog = dataset_mod.get_catalog()
    resolved: list[Hero] = []
    for hero_id in ids:
        hero = catalog.by_id.get(hero_id)
        if hero is None:
            raise KeyError(hero_id)
        resolved.append(hero)
    return resolved


def _badge_to_tier(badge: str | None) -> str | None:
    """badge вида 'Emissary III' → имя тира для селектора профиля."""
    if not badge:
        return None
    name = badge.split()[0] if badge.split() else ""
    known = {"Ascendant", "Emissary", "Archon", "Phantom", "Ritualist",
             "Acolyte", "Seeker", "Alchemist", "Magician"}
    return name if name in known else None


def _load_match_for_analysis(body, match_loader):
    """Общая загрузка матча для /analyze и /analyze/stream.

    Возвращает (normalized, image_bytes); кидает MatchLoadError /
    RateLimitError (D3: сырье кэшируется в match_raw навсегда).
    """
    image_bytes: bytes | None = None
    if body.match_id:
        raw = match_loader.fetch_match(body.match_id, db=_db())
        hero_map = match_loader.load_hero_map()
        normalized = match_loader.normalize_match(raw, hero_map)
    else:
        import base64 as b64mod

        image_bytes = b64mod.b64decode(body.image_b64)
        normalized = match_loader.parse_screenshot(image_bytes)
    return normalized, image_bytes


def _llm_size_estimate() -> int:
    """Скользящая оценка объёма ответа LLM (символы) из api_cache."""
    try:
        cached = _db().get_cached("llm-size-estimate", 30 * 86400)
        if isinstance(cached, dict):
            return int(cached.get("chars") or 0)
    except Exception:  # noqa: BLE001 — оценка опциональна
        pass
    return 0


def _update_llm_size_estimate(review) -> None:
    """Скользящее обновление: 70% прошлого + 30% текущего размера."""
    try:
        size = len(json.dumps({
            "c": review.coach_conclusion,
            "km": review.key_mistake,
            "tf": getattr(review, "item_analysis", None),
        }, ensure_ascii=False, default=str))
        prev = _llm_size_estimate() or size
        blended = int(prev * 0.7 + size * 0.3)
        _db().set_cached("llm-size-estimate", {"chars": blended})
    except Exception:  # noqa: BLE001 — оценка опциональна
        pass


def _player_context() -> dict:
    """V2.0: агрегированный контекст игрока из match_reviews (тренд + слабости)."""
    from ..ai import progress as progress_mod

    try:
        reviews = _db().get_reviews(limit=200)
        return progress_mod.build_player_context(reviews)
    except Exception:  # noqa: BLE001 — контекст опционален
        return {"trend": {}, "weaknesses": {}}


def _validate_depth(depth: str | None) -> str:
    """V2.0: валидация глубины разбора (short|medium|deep) -> 422 иначе."""
    from ..ai.coach import VALID_DEPTHS, DEFAULT_DEPTH

    d = (depth or DEFAULT_DEPTH).strip().lower()
    if d not in VALID_DEPTHS:
        raise HTTPException(
            status_code=422,
            detail=f"Неизвестная глубина разбора '{depth}'. "
                   f"Допустимые значения: {', '.join(sorted(VALID_DEPTHS))}.",
        )
    return d


def _resolve_view_player(nm, account_id: int | None, hero_slug: str | None):
    """Точка зрения игрока: account_id -> hero_slug -> первый игрок."""
    me = None
    if account_id:
        me = nm.player_by_account(int(account_id))
    if me is None and hero_slug:
        slug = str(hero_slug).strip().lower()
        me = next((p for p in nm.players if p.hero_id_slug.lower() == slug), None)
    if me is None and nm.players:
        me = nm.players[0]
    return me


def _deaths_geo(nm, me) -> list[dict]:
    """V2.0 §5.1: смерти игрока с нормализованными координатами 0..1.

    Координаты считает match_loader._norm_coord (та же проекция, что у
    маркеров карты), фронтенд просто раскладывает их в проценты.
    Пустой список — разбор по скриншоту или отсутствие телеметрии.
    """
    if me is None:
        return []
    from ..ai.coach import _hero_ru

    out: list[dict] = []
    for d in sorted(me.deaths_timeline, key=lambda x: x.t):
        out.append({
            "t": int(d.t),
            "time": f"{d.t // 60}:{d.t % 60:02d}",
            "pos_x": round(float(d.pos_x), 4),
            "pos_y": round(float(d.pos_y), 4),
            "killer": _hero_ru(d.killer_hero) if d.killer_hero else "",
        })
    return out


def _personal_benchmark(review, account_id: int | None) -> dict | None:
    """V2.0 §4.1: сравнение матча со средней нормой игрока на этом герое."""
    from ..ai import progress as progress_mod

    if not account_id:
        return None
    try:
        trend = _db().get_hero_player_trends(
            int(account_id), review.hero_id,
            exclude_match_id=review.match_id,
        )
    except Exception:  # noqa: BLE001 — сравнение опционально
        return None
    return progress_mod.personal_benchmark(review.model_dump(), trend)


def _attach_match_extras(review, normalized, account_id: int | None,
                         hero_slug: str | None = None):
    """V2.0: добить ответ MatchReview точкой зрения, смертями и личным трендом."""
    review.account_id = int(account_id) if account_id else None
    review.progress = _player_context()
    if normalized is not None and not isinstance(normalized, dict):
        me = _resolve_view_player(normalized, account_id, hero_slug)
        review.deaths_geo = _deaths_geo(normalized, me)
    review.personal_benchmark = _personal_benchmark(review, account_id)
    return review

# Ключ SQLite-кэша для ручного ввода оверлея; живёт до следующего POST.
LIVE_STATE_KEY = "live_state"
LIVE_STATE_TTL = 365 * 86400

# Справочные респавны объектов — захардкоженные константы из ТЗ §6
# (честная рамка: без чтения памяти/экрана игры, только тайминги).
LIVE_RESPAWNS = [
    {
        "name": "Soul Urn",
        "first_open_minutes": 10.0,
        "interval_minutes": 5.0,
        "note": "Урна душ открывается в 10:00 и респавнится каждые 5 минут после сдачи.",
    },
    {
        "name": "Midboss",
        "respawn_minutes": [7.0, 6.0, 5.0],
        "note": "Мидбосс после убийства респавнится по циклу 7/6/5 минут.",
    },
]


def _mmss_to_minutes(value: str) -> float | None:
    """'MM:SS' -> минуты float (10:00 -> 10.0, 3:30 -> 3.5). None — мусор."""
    try:
        m, s = value.strip().split(":", 1)
        return int(m) + int(s) / 60.0
    except (ValueError, AttributeError):
        return None


def _live_timeline() -> list[dict]:
    """Тайминги объектов матча из data/map.json (timeline) + минуты float."""
    events: list[dict] = []
    for ev in dataset_mod.get_catalog().map_guide.timeline:
        minutes = _mmss_to_minutes(ev.time)
        if minutes is None:
            continue
        events.append({
            "time": ev.time,
            "title": ev.title,
            "description": ev.description,
            "minutes": round(minutes, 2),
        })
    return events


# За сколько секунд до события оверлей пишет «через N секунд».
LIVE_ANNOUNCE_LEAD = 20
# Горизонт раскрытия повторяющихся событий, секунды матча.
LIVE_SCHEDULE_HORIZON = 45 * 60
# Обрезка текста события в фиде комментатора.
LIVE_TEXT_LIMIT = 150

# Повторяющиеся циклы, которых нет в timeline (первое появление там уже описано,
# поэтому раскрываются со второго). Источник: deadlock.wiki — Soul Urn, Powerup,
# Unstable Rift; статичные спавны берутся из data/map.json.
LIVE_CYCLES = [
    {
        "id": "urn",
        "title": "Урна душ",
        "first_seconds": 600,
        "every_seconds": 300,
        "pre": "Урна душ через {lead} секунд — линия чередуется, приземление 12.5 с.",
        "now": "Урна душ заспавнилась: забрать ударом в ближнем бою, сдавать на противоположной линии.",
    },
    {
        "id": "bridge-buffs",
        "title": "Баффы мостов",
        "first_seconds": 300,
        "every_seconds": 300,
        "pre": "Баффы на мостах через {lead} секунд — не копятся, забирать сразу.",
        "now": "Баффы мостов заспавнились: держатся 160 с после разбития.",
    },
    {
        "id": "rift",
        "title": "Unstable Rift",
        "first_seconds": 660,
        "every_seconds": 420,
        "pre": "Unstable Rift примерно через {lead} секунд (±1 мин) — готовь состав на боковой мост.",
        "now": "Unstable Rift: захват на мосту боковой линии, окно 60 с, при равенстве — ничья.",
    },
]


def _short(text: str, title: str = "") -> str:
    """Первое предложение описания без дубля заголовка, до LIVE_TEXT_LIMIT."""
    head = (text or "").split(". ")[0].strip().rstrip(".")
    if title and head.lower().startswith(title.lower()):
        head = head[len(title):].lstrip(" :—–-") or head
    return head if len(head) <= LIVE_TEXT_LIMIT else head[:LIVE_TEXT_LIMIT - 1] + "…"


def _live_schedule(lead: int = LIVE_ANNOUNCE_LEAD,
                   horizon: int = LIVE_SCHEDULE_HORIZON) -> list[dict]:
    """Расписание комментатора: [{id, kind, at, lead, title, pre, now}].

    Раскрыто по секундам матча до horizon, отсортировано по времени. Считается
    локально из data/map.json + LIVE_CYCLES, без сети и без LLM.
    """
    events: list[dict] = []
    for ev in _live_timeline():
        at = int(round(ev["minutes"] * 60))
        events.append({
            "id": f"map:{at}",
            "kind": "map",
            "at": at,
            "lead": lead,
            "title": ev["title"],
            "pre": f"Через {lead} секунд — {ev['title']}.",
            "now": f"{ev['title']}: {_short(ev['description'], ev['title'])}",
        })
    for cyc in LIVE_CYCLES:
        at = int(cyc["first_seconds"]) + int(cyc["every_seconds"])
        while at <= horizon:
            events.append({
                "id": f"{cyc['id']}:{at}",
                "kind": "cycle",
                "at": at,
                "lead": lead,
                "title": cyc["title"],
                "pre": cyc["pre"].format(lead=lead),
                "now": cyc["now"],
            })
            at += int(cyc["every_seconds"])
    events.sort(key=lambda e: (e["at"], e["id"]))
    return events


def _resolve_live_payload(body: LiveStateRequest, catalog):
    """Валидация ручного ввода -> (my_hero, foes: list[Hero], owned: list[Item]).

    HTTPException: 404 на неизвестные id, 422 на >6 врагов и дубликаты.
    """
    my_hero = (body.my_hero or "").strip().lower()
    if my_hero and my_hero not in catalog.by_id:
        raise HTTPException(status_code=404, detail=f"Unknown hero id: {my_hero!r}.")
    if len(body.enemies) > 6:
        raise HTTPException(status_code=422, detail="At most 6 enemy heroes are allowed.")
    if len(body.enemies) != len(set(body.enemies)):
        raise HTTPException(
            status_code=422, detail="Enemy list contains duplicate hero ids.")
    foes: list[Hero] = []
    for enemy_id in body.enemies:
        hero = catalog.by_id.get(enemy_id)
        if hero is None:
            raise HTTPException(status_code=404, detail=f"Unknown hero id: {enemy_id!r}.")
        foes.append(hero)
    owned: list[Item] = []
    for item_id in body.items:
        item = catalog.item_by_id.get(item_id)
        if item is None:
            raise HTTPException(status_code=404, detail=f"Unknown item id: {item_id!r}.")
        owned.append(item)
    return my_hero, foes, owned


def _to_review(normalized, report, account_id: int | None, hero_slug: str | None = None) -> MatchReview:
    """Merge a NormalizedMatch + CoachReport into the persisted MatchReview."""
    from ..engine import match_loader as ml_mod
    from ..models import CoachReport

    from ..ai.deadlock_knowledge import localize_report_dict

    if isinstance(normalized, dict) and normalized.get("mode") == "screenshot":
        # screenshot path: no telemetry, LLM/vision report only
        r = report if isinstance(report, CoachReport) else None
        tf_list = [c.model_dump() for c in r.teamfights] if (r and r.teamfights) else []
        pt_list = [c.model_dump() for c in r.purchase_timings] if (r and r.purchase_timings) else []
        db_list = [c.model_dump() for c in r.death_breakdown] if (r and r.death_breakdown) else []
        lc_dict = r.lane_comparison.model_dump() if (r and r.lane_comparison) else None
        return MatchReview(
            match_id=f"screenshot-{int(time.time())}",
            hero_id=hero_slug or "unknown",
            duration_seconds=0,
            result="loss",
            kda="0/0/0",
            souls_curve=[],
            performance_grade=r.performance_grade if r else "C",
            key_mistake=(localize_report_dict(r.key_mistake.model_dump()) if r else {}),
            coach_conclusion=(localize_report_dict(r.coach_conclusion) if r else []),
            stat_benchmarks=(r.benchmarks.model_dump() if r else {}),
            item_analysis=(localize_report_dict({"corrections": [
                c.model_dump() for c in r.item_corrections
            ]}) if r else {}),
            teamfights=localize_report_dict(tf_list),
            purchase_timings=localize_report_dict(pt_list),
            death_breakdown=localize_report_dict(db_list),
            lane_comparison=(localize_report_dict(lc_dict) if lc_dict else None),
        )

    nm = normalized  # NormalizedMatch
    me = None
    if account_id:
        me = nm.player_by_account(account_id)
    if me is None and hero_slug:
        slug_clean = str(hero_slug).strip().lower()
        for p in nm.players:
            if p.hero_id_slug.lower() == slug_clean:
                me = p
                break
    if me is None and nm.players:
        me = nm.players[0]
    won = nm.result_per_player.get(me.account_id, False) if me else False
    curve = ml_mod.build_souls_curve(nm)
    r = report if isinstance(report, CoachReport) else None

    bm_dict = (r.benchmarks.model_dump() if r else {})
    if me is not None:
        from ..ai import coach as coach_mod
        computed_bm = coach_mod.compute_match_benchmarks(nm, me)
        bm_dict["actual_souls"] = computed_bm["actual_souls"]
        bm_dict["target_souls"] = computed_bm["target_souls"]
        bm_dict["team_kills"] = computed_bm["team_kills"]
        bm_dict["player_ka"] = computed_bm["player_ka"]
        if bm_dict.get("lane_efficiency") == bm_dict.get("souls_at_10"):
            bm_dict["lane_efficiency"] = computed_bm["lane_efficiency"]
        if not bm_dict.get("fight_participation") or abs(bm_dict.get("fight_participation", 0) - computed_bm["fight_participation"]) > 0.08:
            bm_dict["fight_participation"] = computed_bm["fight_participation"]
        if not bm_dict.get("souls_at_10"):
            bm_dict["souls_at_10"] = computed_bm["souls_at_10"]

    tf_list = [c.model_dump() for c in r.teamfights] if (r and r.teamfights) else []
    pt_list = [c.model_dump() for c in r.purchase_timings] if (r and r.purchase_timings) else []
    db_list = [c.model_dump() for c in r.death_breakdown] if (r and r.death_breakdown) else []
    lc_dict = r.lane_comparison.model_dump() if (r and r.lane_comparison) else None

    return MatchReview(
        match_id=str(nm.match_id),
        hero_id=me.hero_id_slug if me else "unknown",
        duration_seconds=nm.duration_s,
        result="win" if won else "loss",
        kda=nm.kda(account_id=me.account_id) if me else "0/0/0",
        souls_curve=[p.model_dump() for p in curve],
        performance_grade=r.performance_grade if r else "C",
        key_mistake=(localize_report_dict(r.key_mistake.model_dump()) if r else {}),
        coach_conclusion=(localize_report_dict(r.coach_conclusion) if r else []),
        stat_benchmarks=bm_dict,
        item_analysis=(localize_report_dict({"corrections": [
            c.model_dump() for c in r.item_corrections
        ]}) if r else {}),
        teamfights=localize_report_dict(tf_list),
        purchase_timings=localize_report_dict(pt_list),
        death_breakdown=localize_report_dict(db_list),
        lane_comparison=(localize_report_dict(lc_dict) if lc_dict else None),
    )


def create_app() -> FastAPI:
    """Build the FastAPI application with all /api/* routes and static SPA."""
    app = FastAPI(title="DeadlockForge", version="1.1.0")

    # --- Catalog & engine -------------------------------------------------

    # Pre-serialized catalog cache to eliminate repeated Pydantic model_dump overhead
    import hashlib
    catalog = dataset_mod.get_catalog()
    _cached_heroes_json = json.dumps(
        [h.model_dump(mode="json") for h in catalog.heroes],
        ensure_ascii=False,
    )
    _cached_items_json = json.dumps(
        [i.model_dump(mode="json") for i in catalog.items],
        ensure_ascii=False,
    )
    _cached_map_json = json.dumps(
        catalog.map_guide.model_dump(mode="json"),
        ensure_ascii=False,
    )
    _heroes_etag = f'"{hashlib.md5(_cached_heroes_json.encode("utf-8")).hexdigest()}"'
    _items_etag = f'"{hashlib.md5(_cached_items_json.encode("utf-8")).hexdigest()}"'
    _map_etag = f'"{hashlib.md5(_cached_map_json.encode("utf-8")).hexdigest()}"'

    def _catalog_response(req: Request, content: str, etag: str) -> Response:
        # no-cache не запрещает кэш, а требует ревалидацию по ETag: иначе
        # WebView2 применяет эвристическое кэширование и может отдать
        # устаревший каталог после обновления датасетов.
        cache_headers = {"ETag": etag, "Cache-Control": "no-cache, must-revalidate"}
        if_none_match = req.headers.get("if-none-match")
        if if_none_match:
            client_etags = [t.strip() for t in if_none_match.split(",")]
            if etag in client_etags or f"W/{etag}" in client_etags or "*" in client_etags:
                return Response(status_code=304, headers=cache_headers)
        return Response(content=content, media_type="application/json", headers=cache_headers)

    @app.get("/api/heroes", response_model=list[Hero])
    def heroes(request: Request) -> Response:
        return _catalog_response(request, _cached_heroes_json, _heroes_etag)

    @app.get("/api/items", response_model=list[Item])
    def items(request: Request) -> Response:
        return _catalog_response(request, _cached_items_json, _items_etag)

    @app.get("/api/map", response_model=MapGuide)
    def map_guide(request: Request) -> Response:
        return _catalog_response(request, _cached_map_json, _map_etag)

    # --- Контрпики (экран 7) ---------------------------------------------
    # Датасет грузится лениво: отсутствие counterpicks.json не должно мешать
    # остальным экранам, а 503 честно сообщает, что данные не собраны.
    _counterpicks_cache: dict[str, str | None] = {"json": None, "etag": None}

    def _counterpicks_payload() -> tuple[str, str]:
        import hashlib

        if _counterpicks_cache["json"] is None:
            data = dataset_mod.get_counterpicks()
            content = json.dumps(data.model_dump(mode="json"), ensure_ascii=False)
            _counterpicks_cache["json"] = content
            _counterpicks_cache["etag"] = (
                f'"{hashlib.md5(content.encode("utf-8")).hexdigest()}"'
            )
        return _counterpicks_cache["json"], _counterpicks_cache["etag"]  # type: ignore[return-value]

    @app.get("/api/counterpicks", response_model=Counterpicks)
    def counterpicks(request: Request) -> Response:
        try:
            content, etag = _counterpicks_payload()
        except FileNotFoundError as exc:
            raise HTTPException(
                status_code=503,
                detail=(
                    "Датасет контрпиков не собран. Выполните "
                    "python scripts/build_counterpicks.py --extract, затем переведите "
                    "чанки и соберите их через --assemble."
                ),
            ) from exc
        except ValueError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return _catalog_response(request, content, etag)

    @app.get("/api/counterpicks/hero/{hero_id}")
    def counterpicks_hero(hero_id: str):
        data = dataset_mod.get_counterpicks()
        for hero in data.heroes:
            if hero.id == hero_id:
                return hero.model_dump(mode="json")
        raise HTTPException(status_code=404, detail=f"Unknown hero id: {hero_id!r}.")

    @app.get("/api/counterpicks/item/{item_id}")
    def counterpicks_item(item_id: str):
        data = dataset_mod.get_counterpicks()
        for item in data.items:
            if item.id == item_id:
                return item.model_dump(mode="json")
        raise HTTPException(status_code=404, detail=f"Unknown item id: {item_id!r}.")

    # --- Синергии (экран «Синергии») -------------------------------------
    # Как и контрпики, датасет грузится лениво и не мешает остальным экранам.
    _synergies_cache: dict[str, str | None] = {"json": None, "etag": None}

    def _synergies_payload() -> tuple[str, str]:
        import hashlib

        if _synergies_cache["json"] is None:
            data = dataset_mod.get_synergies()
            content = json.dumps(data.model_dump(mode="json"), ensure_ascii=False)
            _synergies_cache["json"] = content
            _synergies_cache["etag"] = (
                f'"{hashlib.md5(content.encode("utf-8")).hexdigest()}"'
            )
        return _synergies_cache["json"], _synergies_cache["etag"]  # type: ignore[return-value]

    @app.get("/api/synergies", response_model=Synergies)
    def synergies(request: Request) -> Response:
        try:
            content, etag = _synergies_payload()
        except FileNotFoundError as exc:
            raise HTTPException(
                status_code=503,
                detail=(
                    "Датасет синергий не собран. Выполните "
                    "python scripts/build_synergies.py."
                ),
            ) from exc
        except ValueError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return _catalog_response(request, content, etag)

    @app.get("/api/advice", response_model=Advice)
    def advice(
        my_hero: str = Query(...),
        enemies: str = Query(...),
        allow_mirror: bool = Query(
            False,
            description=(
                "True permits the same hero on both sides (mirror duel); "
                "False keeps the competitive rule: my_hero in enemy_ids → 422."
            ),
        ),
    ) -> Advice:
        catalog = dataset_mod.get_catalog()
        enemy_ids = [e.strip() for e in enemies.split(",") if e.strip()]
        if my_hero in enemy_ids and not allow_mirror:
            raise HTTPException(
                status_code=422,
                detail=f"Hero {my_hero!r} cannot be both picked and enemy.",
            )
        if len(enemy_ids) != len(set(enemy_ids)):
            raise HTTPException(
                status_code=422,
                detail="Enemy list contains duplicate hero ids.",
            )
        if not enemy_ids:
            raise HTTPException(
                status_code=422, detail="At least one enemy hero is required."
            )
        if len(enemy_ids) > 6:
            raise HTTPException(
                status_code=422, detail="At most 6 enemy heroes are allowed."
            )
        try:
            me = _resolve_heroes([my_hero])[0]
            foes = _resolve_heroes(enemy_ids)
        except KeyError as missing:
            raise HTTPException(
                status_code=404, detail=f"Unknown hero id: {missing.args[0]!r}."
            ) from missing
        return advisor_mod.recommend(
            me, foes, catalog.counter_rules, catalog.item_by_id
        )

    @app.post("/api/calc", response_model=StatsSnapshot)
    def calc(body: CalcRequest) -> StatsSnapshot:
        catalog = dataset_mod.get_catalog()
        hero = catalog.by_id.get(body.hero_id)
        if hero is None:
            raise HTTPException(
                status_code=404, detail=f"Unknown hero id: {body.hero_id!r}."
            )
        try:
            picked = [catalog.item_by_id[i] for i in body.item_ids]
        except KeyError as missing:
            raise HTTPException(
                status_code=404, detail=f"Unknown item id: {missing.args[0]!r}."
            ) from missing
        return calculator_mod.compute(hero, picked)

    @app.post("/api/build/validate")
    def validate_build(slots: BuildSlots):
        catalog = dataset_mod.get_catalog()
        try:
            builds_mod.validate(slots, catalog.item_by_id)
        except BuildError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"valid": True}

    @app.post("/api/builds/export")
    def export_build(slots: BuildSlots) -> dict[str, str]:
        catalog = dataset_mod.get_catalog()
        try:
            builds_mod.validate(slots, catalog.item_by_id)
        except BuildError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"code": build_codec_mod.encode(slots)}

    @app.post("/api/builds/import", response_model=BuildSlots)
    def import_build(body: BuildCodeRequest) -> BuildSlots:
        catalog = dataset_mod.get_catalog()
        try:
            slots = build_codec_mod.decode(body.code)
        except BuildError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        try:
            builds_mod.validate(slots, catalog.item_by_id)
        except BuildError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return slots

    # --- Builds CRUD (SQLite) ----------------------------------------------

    @app.get("/api/builds")
    def list_builds(hero_id: str | None = None) -> list[dict]:
        return _db().get_builds(hero_id=hero_id)

    @app.post("/api/builds")
    def save_build(body: BuildSaveRequest) -> dict:
        import uuid

        build = body.model_dump()
        build["id"] = body.id or str(uuid.uuid4())[:8]
        _db().save_build(build)
        return {"ok": True, "id": build["id"]}

    @app.delete("/api/builds/{build_id}")
    def delete_build(build_id: str) -> dict:
        deleted = _db().delete_build(build_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Build not found.")
        return {"ok": True}

    # --- Profile -------------------------------------------------------------

    @app.get("/api/profile")
    def get_profile() -> dict:
        profile = _db().get_profile()
        # TZ v1.4 D2: аватар из кэша (если подтянут через steam-info).
        if profile.get("account_id"):
            avatar_file = (
                _web_dir() / "assets" / "avatars"
                / f"{profile['account_id']}.jpg")
            if avatar_file.is_file():
                profile = dict(profile)
                profile["avatar_url"] = (
                    f"/assets/avatars/{profile['account_id']}.jpg")
        # TZ A1: при пустом профиле прикладываем автодетект-кандидатов Steam.
        if not (profile.get("steam_id") or profile.get("account_id")):
            detected = steam_mod.detect()
            if detected is not None:
                profile = dict(profile)
                profile["steam_autodetect"] = {
                    "steamid64": detected["steamid64"],
                    "account_id": detected["account_id"],
                    "steam_id32": detected["steam_id32"],
                    "persona_name": detected["persona_name"],
                    "account_name": detected["account_name"],
                }
        return profile

    @app.post("/api/profile")
    def update_profile(body: dict) -> dict:
        allowed = {
            "steam_id", "account_id", "player_name", "rank_tier",
            "rank_subrank", "mmr",
        }
        fields = {k: v for k, v in body.items() if k in allowed}
        _db().update_profile(**fields)
        return _db().get_profile()

    @app.get("/api/profile/detect")
    def detect_profile() -> dict:
        """TZ A1: явный автодетект Steam (кнопка «Найти мой профиль»)."""
        detected = steam_mod.detect()
        if detected is None:
            raise HTTPException(
                status_code=404,
                detail="Steam не найден: установите Steam или заполните "
                       "Account ID вручную в расширенных настройках.",
            )
        return detected

    @app.get("/api/profile/steam-info")
    def profile_steam_info() -> dict:
        """TZ v1.4 D2: автоподтягивание ника, аватара и ранга.

        Ник+аватар: /v1/players/steam (без ключа), аватар кэшируется в
        web/assets/avatars/{account_id}.jpg (TTL 24ч через api_cache).
        Ранг: /v1/players/{id}/rank → rank/subrank/badge; при rank:0 и
        last_match:null отдаётся rank_hidden: true (плашка в UI). MMR —
        только ручное поле (эндпоинты deprecated).
        """
        profile = _db().get_profile()
        account_id = str(profile.get("account_id") or "").strip()
        if not account_id or not account_id.isdigit():
            detected = steam_mod.detect()
            if detected is not None:
                account_id = str(detected["account_id"])
        if not account_id or not account_id.isdigit():
            raise HTTPException(
                status_code=422,
                detail="Account ID не определён: заполните профиль или "
                       "запустите Steam.",
            )
        result: dict = {"account_id": int(account_id)}

        # Ник + аватар (без ключа), аватар — файл с 24ч кэшем.
        avatar_key = f"steam-avatar:{account_id}"
        cached_avatar_ts = _db().get_cached(avatar_key, steam_mod.AVATAR_TTL)
        avatar_dir = _web_dir() / "assets" / "avatars"
        avatar_file = avatar_dir / f"{account_id}.jpg"
        try:
            info = steam_mod.fetch_steam_info(account_id)
            if info.get("found"):
                result["persona_name"] = info.get("persona_name") or ""
                if cached_avatar_ts is None or not avatar_file.is_file():
                    data = steam_mod.download_avatar(info.get("avatar", ""))
                    if data:
                        avatar_dir.mkdir(parents=True, exist_ok=True)
                        avatar_file.write_bytes(data)
                        _db().set_cached(avatar_key, {"saved": True})
                result["avatar_url"] = (
                    f"/assets/avatars/{account_id}.jpg"
                    if avatar_file.is_file() else "")
        except steam_mod.RateLimitError as exc:
            raise HTTPException(status_code=429, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001 — инфо опционально
            result["steam_info_error"] = str(exc)[:200]

        # Ранг (rank_hidden при закрытых данных).
        try:
            rank = steam_mod.fetch_rank(account_id)
            result["rank"] = rank
            if not rank.get("rank_hidden") and rank.get("rank"):
                # Автозаполнение rank_tier/rank_subrank профиля.
                tier = _badge_to_tier(rank.get("badge"))
                _db().update_profile(
                    rank_tier=tier or profile.get("rank_tier") or "Ascendant",
                    rank_subrank=int(rank.get("subrank") or 1) or 1,
                )
        except steam_mod.RateLimitError as exc:
            raise HTTPException(status_code=429, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001 — ранг опционален
            result["rank_error"] = str(exc)[:200]
        return result

    @app.get("/api/profile/recent-matches")
    def profile_recent_matches(force: bool = False) -> dict:
        """TZ A1 + v1.4 D3: последние матчи по профилю. Кэш 60 минут в
        SQLite; force=True принудительно запрашивает свежие матчи из сети;
        офлайн — 502 с читаемым текстом, 429 — человеческая строка."""
        profile = _db().get_profile()
        account_id = profile.get("account_id") or ""
        if not str(account_id).strip():
            detected = steam_mod.detect()
            if detected is not None:
                account_id = detected["account_id"]
        account_id = str(account_id).strip()
        if not account_id or not account_id.isdigit():
            raise HTTPException(
                status_code=422,
                detail="Account ID не определён: заполните профиль или "
                       "запустите Steam.",
            )
        cache_key = f"recent-matches:{account_id}"
        if not force:
            cached = _db().get_cached(cache_key, steam_mod.RECENT_MATCHES_TTL)
            if cached is not None:
                return {"account_id": int(account_id), "matches": cached, "cached": True}
        try:
            matches = steam_mod.fetch_recent_matches(account_id)
        except steam_mod.RateLimitError as exc:
            raise HTTPException(status_code=429, detail=str(exc)) from exc
        except steam_mod.RecentMatchesError as exc:
            raise HTTPException(
                status_code=502,
                detail=f"Не удалось получить последние матчи: {exc}",
            ) from exc
        _db().set_cached(cache_key, matches)
        return {"account_id": int(account_id), "matches": matches, "cached": False}

    @app.post("/api/profile/recent-matches/refresh")
    def profile_recent_matches_refresh() -> dict:
        """Принудительное обновление списка последних матчей профиля."""
        return profile_recent_matches(force=True)

    # --- AI config -----------------------------------------------------------

    @app.get("/api/ai/config")
    def get_ai_config() -> dict:
        env_key = os.environ.get("DEADLOCKFORGE_AI_KEY", "").strip()
        db_cfg = _db().get_ai_config()
        db_key = db_cfg.get("api_key") or ""
        key = env_key or db_key
        eff = effective_ai_config(db_cfg)
        masked = (key[:4] + "…" + key[-4:]) if len(key) > 8 else ("sk-***" if key else "")
        return {
            "provider": eff.provider,
            "model_name": eff.model_name,
            "base_url": eff.base_url,
            "reasoning_effort": getattr(eff, "reasoning_effort", "low") or "low",
            "enable_thinking": getattr(eff, "enable_thinking", True),
            "api_key_masked": masked,
            "has_key": bool(key),
            "key_source": "env" if env_key else ("db" if db_key else "none"),
        }

    @app.get("/api/ai/models")
    def get_ai_models() -> dict:
        from ..ai import client as client_mod

        cfg = effective_ai_config(_db().get_ai_config())
        models = client_mod.list_available_models(cfg)
        return {"models": models, "current": cfg.model_name}

    @app.post("/api/ai/config")
    def update_ai_config(body: AIConfigUpdate) -> dict:
        fields = {k: v for k, v in body.model_dump().items() if v is not None}
        if "provider" in fields and "model_name" not in fields:
            from ..models import PROVIDER_DEFAULT_MODELS
            p = fields["provider"].lower()
            if p in PROVIDER_DEFAULT_MODELS:
                fields["model_name"] = PROVIDER_DEFAULT_MODELS[p]
        _db().update_ai_config(**fields)
        return {"ok": True}


    @app.post("/api/ai/test")
    def test_ai_connection() -> dict:
        from ..ai import client as client_mod

        cfg = effective_ai_config(_db().get_ai_config())
        if not cfg.api_key:
            raise HTTPException(
                status_code=422,
                detail="AI-ключ не задан. Укажите DEADLOCKFORGE_AI_KEY "
                       "или сохраните ключ в настройках.",
            )
        start = time.monotonic()
        try:
            result = client_mod.test_connection(cfg)
        except Exception as exc:  # noqa: BLE001 — surface any client error
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        result["latency_ms"] = round((time.monotonic() - start) * 1000, 1)
        return result

    # --- Draft 6v6 ------------------------------------------------------------

    @app.post("/api/draft/analyze")
    def draft_analyze(body: DraftRequest) -> dict:
        drafter = _get_drafter()
        catalog = dataset_mod.get_catalog()
        if len(body.my_team) > 6 or len(body.enemy_team) > 6:
            raise HTTPException(status_code=422, detail="Teams are 6v6 maximum.")
        if len(body.my_team) != len(set(body.my_team)) or len(
            body.enemy_team
        ) != len(set(body.enemy_team)):
            raise HTTPException(
                status_code=422, detail="Duplicate heroes inside one team."
            )
        try:
            _resolve_heroes(body.my_team)
            _resolve_heroes(body.enemy_team)
        except KeyError as missing:
            raise HTTPException(
                status_code=404, detail=f"Unknown hero id: {missing.args[0]!r}."
            ) from missing
        try:
            analysis = drafter.analyze_draft(
                body.my_team, body.enemy_team, catalog, body.allow_mirror
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return analysis.model_dump()

    @app.post("/api/draft/builds")
    def draft_builds(body: DraftBuildsRequest) -> dict:
        """S5: быстрые сборки по драфту (локальный движок, без LLM на пути).

        Скорость — главное требование сценария «в матче» (30–40 с): весь план
        считается локально (advisor/drafter/calculator). `mode="ai"` лишь
        добавляет короткие текстовые обоснования, если ключ есть и модель
        успела за 8 с; при любой ошибке/таймауте ответ остаётся локальным.
        """
        from ..engine import draft_builds as draft_builds_mod

        catalog = dataset_mod.get_catalog()
        drafter = _get_drafter()
        if len(body.enemies) > 6:
            raise HTTPException(status_code=422, detail="Teams are 6v6 maximum.")
        if len(body.enemies) != len(set(body.enemies)):
            raise HTTPException(
                status_code=422, detail="Enemy list contains duplicate hero ids."
            )
        try:
            plan = draft_builds_mod.build_plan(
                body.my_hero, body.enemies, catalog,
                role=body.role, mode=body.mode,
            )
        except ValueError as exc:
            detail = str(exc)
            status = 404 if detail.startswith("Unknown hero id") else 422
            raise HTTPException(status_code=status, detail=detail) from exc

        wants_llm = plan.engine.mode == "ai"
        notes: dict[str, str] | None = None
        llm_ms: float | None = None
        if wants_llm and body.my_hero:
            try:
                cfg = effective_ai_config(_db().get_ai_config())
            except Exception:  # noqa: BLE001 — хранилище недоступно: остаёмся локальными
                cfg = None
            if cfg is not None and cfg.api_key:
                from ..ai import client as client_mod

                start = time.monotonic()
                try:
                    client = client_mod.create_client(cfg)
                    notes = draft_builds_mod.llm_notes(plan, client)
                except Exception:  # noqa: BLE001 — LLM необязателен
                    notes = None
                llm_ms = round((time.monotonic() - start) * 1000, 1)

        if notes:
            for variant in plan.variants:
                variant.llm_note = notes.get(variant.key)
        plan.engine.llm_used = bool(notes)
        plan.engine.llm_ms = llm_ms
        return plan.model_dump()

    @app.post("/api/draft/parse-screenshot")
    def draft_parse_screenshot(body: DraftScreenshotRequest) -> dict:
        import base64 as b64mod
        from ..ai import client as client_mod
        from ..ai import coach as coach_mod

        cfg = effective_ai_config(_db().get_ai_config())
        if not cfg.api_key:
            raise HTTPException(
                status_code=422,
                detail="Для распознавания скриншота требуется API-ключ в настройках AI.",
            )
        try:
            image_bytes = b64mod.b64decode(body.image_b64)
        except Exception as exc:
            raise HTTPException(status_code=422, detail="Некорректный base64 скриншота.") from exc

        client = client_mod.create_client(cfg)
        try:
            parsed = coach_mod.analyze_draft_screenshot(image_bytes, client)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Ошибка разбора скриншота AI: {exc}") from exc

        # Валидация идентификаторов против каталога
        catalog = dataset_mod.get_catalog()
        known_ids = set(catalog.by_id.keys())

        my_clean = [h for h in parsed.get("my_team", []) if h in known_ids][:6]
        enemy_clean = [h for h in parsed.get("enemy_team", []) if h in known_ids][:6]

        # Также генерируем стандартный эвристический анализ драфта для этих команд
        drafter = _get_drafter()
        heuristic = {}
        if enemy_clean:
            try:
                heuristic = drafter.analyze_draft(my_clean, enemy_clean, catalog, True).model_dump()
            except Exception:
                pass

        return {
            "my_team": my_clean,
            "enemy_team": enemy_clean,
            "my_hero": parsed.get("my_hero"),
            "tactics": parsed.get("tactics", {}),
            "heuristic": heuristic,
        }

    # --- Live In-Game Assistant (M4 §6) -----------------------------------

    # Часы матча: панель присылает точку отсчёта, дальше сервер считает сам.
    # Нужно, чтобы плашки поверх игры шли и при закрытой панели. Список
    # заменяется срезом целиком — это атомарно в CPython, блокировка не нужна.
    _clock: list = [0.0, time.monotonic(), False]

    def _clock_now() -> dict:
        base, at, running = _clock
        secs = base + (time.monotonic() - at) if running else base
        return {"seconds": round(secs, 1), "running": running}

    @app.get("/api/live/clock")
    def live_clock_get() -> dict:
        """Текущее игровое время по часам сервера (см. POST /api/live/clock)."""
        return _clock_now()

    @app.post("/api/live/clock")
    def live_clock_post(body: LiveClockSync) -> dict:
        """Точка отсчёта от панели: старт, пауза, правка ±10 с, сброс."""
        _clock[:] = [max(0.0, float(body.seconds)), time.monotonic(), bool(body.running)]
        return _clock_now()

    @app.get("/api/live/overlay")
    def live_overlay_get() -> dict:
        """Состояние тумблера плашек. В браузере (без pywebview) — unsupported."""
        ctl = getattr(app.state, "overlay_ctl", None)
        return {"supported": ctl is not None, "enabled": bool(ctl and ctl(None))}

    @app.post("/api/live/overlay")
    def live_overlay_post(body: LiveOverlayRequest) -> dict:
        """Тумблер плашек поверх игры; окно живёт в run_desktop.py."""
        ctl = getattr(app.state, "overlay_ctl", None)
        if ctl is None:
            return {"supported": False, "enabled": False}
        return {"supported": True, "enabled": bool(ctl(body.enabled))}

    # --- Глобальная горячая клавиша панели (десктопная оболочка) -----------
    # Комбинация живёт в app_settings и регистрируется в run_desktop.py.
    # В браузере hotkey_ctl отсутствует, поэтому supported=False.
    HOTKEY_KEY = "hotkey"
    DEFAULT_HOTKEY = "ctrl+f1"
    TOPMOST_KEY = "always_on_top"

    def _setting_bool(key: str, default: bool) -> bool:
        raw = _db().get_setting(key, "1" if default else "0")
        return str(raw).strip().lower() not in {"0", "false", "off", "no", ""}

    def _topmost_state() -> dict:
        ctl = getattr(app.state, "topmost_ctl", None)
        state = ctl(None) if ctl is not None else {
            "enabled": _setting_bool(TOPMOST_KEY, True), "error": None,
        }
        return {
            "supported": ctl is not None,
            "enabled": bool(state.get("enabled")),
            "error": state.get("error"),
        }

    def _hotkey_state() -> dict:
        ctl = getattr(app.state, "hotkey_ctl", None)
        spec = _db().get_setting(HOTKEY_KEY, DEFAULT_HOTKEY) or DEFAULT_HOTKEY
        state = ctl(None) if ctl is not None else {"active": False, "error": None}
        return {
            "supported": ctl is not None,
            "hotkey": spec,
            "active": bool(state.get("active")),
            "error": state.get("error"),
            "always_on_top": _topmost_state()["enabled"],
        }

    @app.get("/api/desktop/hotkey")
    def hotkey_get() -> dict:
        """Комбинация панели и состояние регистрации; в браузере supported: False."""
        return _hotkey_state()

    @app.post("/api/desktop/hotkey")
    def hotkey_post(body: HotkeyRequest) -> dict:
        """Сохранить и применить комбинацию: 422 — невалидная, 409 — занята."""
        from ..desktop.hotkey import HotkeyError, format_hotkey

        try:
            canonical = format_hotkey(body.hotkey)
        except HotkeyError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        ctl = getattr(app.state, "hotkey_ctl", None)
        if ctl is not None:
            state = ctl(canonical)
            if state.get("error"):
                raise HTTPException(status_code=409, detail=str(state["error"]))
        _db().set_setting(HOTKEY_KEY, canonical)
        return _hotkey_state()

    # Панель поверх игры: Esc в UI прячет окно и возвращает фокус игре.
    def _panel_state() -> dict:
        ctl = getattr(app.state, "panel_ctl", None)
        state = ctl(None) if ctl is not None else {"active": False, "by_hotkey": False}
        return {
            "supported": ctl is not None,
            "visible": bool(state.get("active")),
            "by_hotkey": bool(state.get("by_hotkey")),
        }

    @app.get("/api/desktop/panel")
    def panel_get() -> dict:
        """Видима ли панель и открыта ли она хоткеем (тогда Esc её прячет)."""
        return _panel_state()

    @app.post("/api/desktop/panel")
    def panel_post(body: PanelRequest) -> dict:
        """Показать/скрыть панель по запросу UI; окно живёт в run_desktop.py."""
        ctl = getattr(app.state, "panel_ctl", None)
        if ctl is None:
            return {"supported": False, "visible": False, "by_hotkey": False}
        ctl(body.visible)
        return _panel_state()

    # Окно-оверлей (live.html) на CTRL+F2: кликабельно, но фокус у игры не
    # забирает (WS_EX_NOACTIVATE), поэтому управление персонажем не страдает.
    # Окно живёт в run_desktop.py; здесь — только состояние и показ/скрытие.
    def _overlay_panel_state() -> dict:
        ctl = getattr(app.state, "overlay_panel_ctl", None)
        state = ctl(None) if ctl is not None else {"visible": False}
        return {
            "supported": ctl is not None,
            "visible": bool(state.get("visible")),
        }

    @app.get("/api/desktop/overlay")
    def overlay_panel_get() -> dict:
        """Виден ли оверлей; в браузере без pywebview — supported: False."""
        return _overlay_panel_state()

    @app.post("/api/desktop/overlay")
    def overlay_panel_post(body: PanelRequest) -> dict:
        """Показать/скрыть оверлей (кнопка ✕ в live.html); в браузере no-op."""
        ctl = getattr(app.state, "overlay_panel_ctl", None)
        if ctl is None:
            return {"supported": False, "visible": False}
        ctl(body.visible)
        return _overlay_panel_state()

    # Тумблер «Поверх других окон»: включает перехват хоткея и закреп окна над
    # игрой. Выключен — приложение ведёт себя как обычное окно.
    @app.get("/api/desktop/topmost")
    def topmost_get() -> dict:
        """Состояние тумблера; в браузере supported: False."""
        return _topmost_state()

    @app.post("/api/desktop/topmost")
    def topmost_post(body: TopmostRequest) -> dict:
        """Включить/выключить закреп; выключение снимает перехват хоткея."""
        _db().set_setting(TOPMOST_KEY, "1" if body.enabled else "0")
        ctl = getattr(app.state, "topmost_ctl", None)
        if ctl is None:
            return {"supported": False, "enabled": body.enabled, "error": None}
        state = ctl(body.enabled)
        return {
            "supported": True,
            "enabled": bool(state.get("enabled")),
            "error": state.get("error"),
        }

    # Драйвер плашек в run_desktop.py берёт часы и расписание отсюда, чтобы
    # не дублировать логику: окно-плашка не опрашивает HTTP из JS (WebView2
    # троттлит таймеры скрытого окна и плашка опаздывала бы).
    app.state.live_clock = _clock_now
    app.state.live_schedule = _live_schedule
    app.state.live_lead = LIVE_ANNOUNCE_LEAD
    app.state.db = _db

    @app.get("/api/live/timers")
    def live_timers() -> dict:
        """Тайминги объектов матча из data/map.json timeline + справочные
        респавны (Урна душ, Мидбосс). Всё локально, без внешних запросов."""
        return {"timeline": _live_timeline(), "respawns": LIVE_RESPAWNS}

    @app.get("/api/live/schedule")
    def live_schedule() -> dict:
        """Расписание комментатора: что и за сколько секунд до события писать.

        Раскрыто до 45 минут матча (статические спавны из data/map.json +
        повторяющиеся циклы LIVE_CYCLES). Локально, без LLM и без сети.
        """
        return {
            "lead": LIVE_ANNOUNCE_LEAD,
            "horizon": LIVE_SCHEDULE_HORIZON,
            "events": _live_schedule(),
        }

    @app.get("/api/live/state")
    def live_state_get() -> dict:
        """Сохранённый ручной ввод оверлея (мой герой, враги, предметы)."""
        state = _db().get_cached(LIVE_STATE_KEY, LIVE_STATE_TTL)
        if not isinstance(state, dict):
            state = {}
        return {
            "my_hero": str(state.get("my_hero") or ""),
            "enemies": list(state.get("enemies") or []),
            "items": list(state.get("items") or []),
            "set": bool(state),
        }

    @app.post("/api/live/state")
    def live_state_post(body: LiveStateRequest) -> dict:
        """Сохранить ручной ввод оверлея в SQLite (kv-кэш api_cache)."""
        catalog = dataset_mod.get_catalog()
        my_hero, foes, owned = _resolve_live_payload(body, catalog)
        state = {
            "my_hero": my_hero,
            "enemies": [f.id for f in foes],
            "items": [i.id for i in owned],
        }
        _db().set_cached(LIVE_STATE_KEY, state)
        return {**state, "set": True}

    @app.post("/api/live/advice")
    def live_advice(body: LiveStateRequest | None = None) -> dict:
        """Мгновенные локальные советы «купи X против Y» (без LLM, без сети).

        Тело {my_hero, enemies, items} — или сохранённое состояние, если тело
        пустое. Движки: advisor.recommend (контр-предметы по тегам) +
        drafter.analyze_draft (Red Flags). Цель — < 100 мс.
        """
        start = time.monotonic()
        catalog = dataset_mod.get_catalog()
        drafter = _get_drafter()
        if body is None or not (body.my_hero or body.enemies or body.items):
            saved = _db().get_cached(LIVE_STATE_KEY, LIVE_STATE_TTL)
            body = LiveStateRequest(**(saved or {}))
        my_hero, foes, _owned = _resolve_live_payload(body, catalog)
        me = catalog.by_id.get(my_hero) if my_hero else None

        # Red Flags: вражеский состав против моей команды (один герой).
        red_flags: list[dict] = []
        if foes:
            try:
                analysis = drafter.analyze_draft(
                    [my_hero] if my_hero else [],
                    [f.id for f in foes],
                    catalog,
                    allow_mirror=True,
                )
                red_flags = [f.model_dump() for f in analysis.red_flags]
            except ValueError:
                pass  # пустой/невалидный ввод — флагов нет

        # Контр-предметы по тегам врагов (advisor.recommend, локально).
        advice = advisor_mod.recommend(
            me, foes, catalog.counter_rules, catalog.item_by_id)
        counters: list[dict] = []
        for rec in advice.recommendations:
            counters.append({
                "item_id": rec.item.id,
                "name": rec.item.name,
                "reason": rec.explanation,
                "cost": rec.cost,
                "tier": rec.tier,
                "timing": rec.timing,
            })

        tips: list[str] = []
        for flag in red_flags[:2]:
            if flag.get("fix"):
                tips.append(flag["fix"])
        for rec in advice.recommendations[:3]:
            covered = ", ".join(t.value for t in rec.item.counters)
            tips.append(
                f"Купи {rec.item.name} против {covered or 'угроз врага'}: "
                f"{rec.explanation}")
        if advice.threats and not tips:
            top = advice.threats[0]
            tips.append(
                f"Главная угроза: {top.tag.value} от {', '.join(top.contributors)}.")

        return {
            "red_flags": red_flags,
            "counters": counters,
            "tips": tips,
            "computed_ms": round((time.monotonic() - start) * 1000, 1),
        }

    # --- Deep Match -------------------------------------------------------------

    @app.get("/api/match/{match_id}/roster")
    def get_match_roster(match_id: str) -> dict:
        match_loader, _, _ = _get_match_pipeline()
        try:
            raw = match_loader.fetch_match(match_id, db=_db())
            hero_map = match_loader.load_hero_map()
            normalized = match_loader.normalize_match(raw, hero_map)
        except match_loader.RateLimitError as exc:
            raise HTTPException(status_code=429, detail=str(exc)) from exc
        except match_loader.MatchLoadError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Не удалось загрузить данные матча: {exc}") from exc

        catalog = dataset_mod.get_catalog()
        players_data = []
        for p in normalized.players:
            h_info = catalog.by_id.get(p.hero_id_slug)
            name_ru = h_info.name if h_info else p.hero_id_slug
            players_data.append({
                "account_id": p.account_id,
                "hero_slug": p.hero_id_slug,
                "hero_name": name_ru,
                "team": p.team,
                "kills": p.kills,
                "deaths": p.deaths,
                "assists": p.assists,
                "net_worth": p.net_worth,
                "level": p.level,
            })

        return {
            "match_id": str(match_id),
            "duration_s": normalized.duration_s,
            "players": players_data,
        }

    @app.post("/api/match/analyze", response_model=MatchReview)
    def match_analyze(body: MatchAnalyzeRequest) -> MatchReview:
        match_loader, coach_mod, client_mod = _get_match_pipeline()
        if not body.match_id and not body.image_b64:
            raise HTTPException(
                status_code=422, detail="match_id or image_b64 is required."
            )
        try:
            normalized, image_bytes = _load_match_for_analysis(body, match_loader)
        except match_loader.RateLimitError as exc:
            raise HTTPException(status_code=429, detail=str(exc)) from exc
        except match_loader.MatchLoadError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

        depth = _validate_depth(body.depth)  # V2.0: short|medium|deep

        from ..models import AIConfig  # noqa: F401 — re-exported seam

        cfg = effective_ai_config(_db().get_ai_config())
        # V2.0 М4: разовая смена модели из UI (селект «Модель») — применяем к
        # текущему разбору и сохраняем в БД, чтобы выбор запомнился.
        if body.model_name:
            cfg = cfg.model_copy(update={"model_name": body.model_name})
            try:
                _db().update_ai_config(model_name=body.model_name)
            except Exception:  # noqa: BLE001 — необязательно, только запомнить
                pass
        # V2.0: Авто-настройка уровня размышлений (reasoning_effort)
        if body.reasoning_effort and body.reasoning_effort != "auto":
            cfg = cfg.model_copy(update={"reasoning_effort": body.reasoning_effort, "enable_thinking": True})
        elif depth == "short":
            cfg = cfg.model_copy(update={"reasoning_effort": "off", "enable_thinking": False})
        elif not getattr(cfg, "reasoning_effort", None) or cfg.reasoning_effort == "auto":
            cfg = cfg.model_copy(update={"reasoning_effort": "low", "enable_thinking": True})

        is_offline = (body.mode or "").lower() == "offline"
        if not is_offline and not cfg.api_key:
            raise HTTPException(
                status_code=422,
                detail="Для анализа матча нейросетью укажите API-ключ в профиле",
            )

        if is_offline:
            report = coach_mod.heuristic_report(normalized, body.account_id, hero_slug=body.hero_slug)
        else:
            # V2.0 М4: автосмена модели при «остывании» (429) — разбор не
            # падает из-за одного залипшего провайдера, а пробует следующие.
            try:
                report, used_model = coach_mod.analyze_match_with_fallback(
                    normalized, body.account_id, cfg, image_bytes=image_bytes,
                    hero_slug=body.hero_slug, depth=depth,
                    player_context=_player_context(),
                )
                cfg = cfg.model_copy(update={"model_name": used_model})
            except Exception as exc:
                raise HTTPException(
                    status_code=502,
                    detail=f"Ошибка анализа нейросетью: {exc}",
                ) from exc

        review = _to_review(normalized, report, body.account_id, hero_slug=body.hero_slug)
        review.progress = _player_context()
        _db().save_review(review.model_dump())
        return review

    @app.post("/api/match/analyze/stream")
    def match_analyze_stream(body: MatchAnalyzeRequest):
        """TZ v1.5: SSE-разбор с реальным прогрессом и запретом фальшивки без ключа.

        События (text/event-stream, data: {...}):
          {"type": "stage", "stage": "fetch"}      — загрузка метаданных;
          {"type": "progress", "seconds": N}       — секунды с начала LLM;
          {"type": "chunk", "received": K, "estimated": M} — доля ответа;
          {"type": "result", ...}                  — MatchReview (финал);
          {"type": "error", "detail": "..."}       — финальная ошибка.
        """
        import asyncio

        match_loader, coach_mod, client_mod = _get_match_pipeline()

        def sse(payload: dict) -> str:
            return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

        async def gen():
            if not body.match_id and not body.image_b64:
                yield sse({"type": "error",
                           "detail": "Укажи Match ID или загрузи скриншот."})
                return
            try:
                depth = _validate_depth(body.depth)  # V2.0: short|medium|deep
            except HTTPException as exc:
                yield sse({"type": "error", "detail": str(exc.detail)})
                return
            try:
                yield sse({"type": "stage", "stage": "fetch"})
                normalized, image_bytes = await asyncio.to_thread(
                    _load_match_for_analysis, body, match_loader)
            except match_loader.RateLimitError as exc:
                yield sse({"type": "error", "detail": str(exc)})
                return
            except match_loader.MatchLoadError as exc:
                yield sse({"type": "error", "detail": str(exc)})
                return

            cfg = effective_ai_config(_db().get_ai_config())
            # V2.0 М4: разовая смена модели из UI + запоминание в БД.
            if body.model_name:
                cfg = cfg.model_copy(update={"model_name": body.model_name})
                try:
                    _db().update_ai_config(model_name=body.model_name)
                except Exception:  # noqa: BLE001 — необязательно
                    pass
            # V2.0: Авто-настройка уровня размышлений (reasoning_effort)
            if body.reasoning_effort and body.reasoning_effort != "auto":
                cfg = cfg.model_copy(update={"reasoning_effort": body.reasoning_effort, "enable_thinking": True})
            elif depth == "short":
                cfg = cfg.model_copy(update={"reasoning_effort": "off", "enable_thinking": False})
            elif not getattr(cfg, "reasoning_effort", None) or cfg.reasoning_effort == "auto":
                cfg = cfg.model_copy(update={"reasoning_effort": "low", "enable_thinking": True})

            # V2.0 М5: короткий режим — это экспресс. Тяжёлые reasoning-модели
            # в него физически не влезают (замер владельца: даже быстрый
            # glm-5.3-flash ~63.5с при окне 60с), поэтому «Короткий» без явного
            # выбора модели пинится на быструю модель пула.
            if depth == "short" and not body.model_name:
                cfg = cfg.model_copy(update={"model_name": "deepseek-v4-flash"})

            is_offline = (body.mode or "").lower() == "offline"
            if not is_offline and not cfg.api_key:
                yield sse({
                    "type": "error",
                    "detail": "Для анализа матча нейросетью укажите API-ключ в профиле",
                })
                return

            if is_offline:
                report = coach_mod.heuristic_report(normalized, body.account_id, hero_slug=body.hero_slug)
                review = _to_review(normalized, report, body.account_id, hero_slug=body.hero_slug)
                _db().save_review(review.model_dump())
                yield sse({"type": "result", **review.model_dump()})
                return

            estimated = _llm_size_estimate()
            start = time.monotonic()
            last_sent = [0.0]

            def on_chunk(received: int) -> None:
                now = time.monotonic()
                if now - last_sent[0] < 0.25:
                    return
                last_sent[0] = now
                pending.append({"type": "chunk", "received": received,
                                "estimated": estimated})

            pending: list[dict] = []
            report = None
            used_model = [cfg.model_name]
            attempt_notes: list[dict] = []
            # Адаптивный лимит под глубину разбора: short: 60с, medium: 120с,
            # deep: 180с. Дедлайн считается от старта ВСЕЙ цепочки моделей, а не
            # от попытки: раньше on_attempt сбрасывал счётчик, и разбор мог идти
            # кратно дольше заявленного окна, а ошибка приходила с ложным числом.
            timeout_limit = 180.0 if depth == "deep" else (120.0 if depth == "medium" else 60.0)
            # Потолок одной модели: залипший провайдер не должен съесть всё окно,
            # остаток идёт на failover.
            attempt_budget = 90.0 if depth == "deep" else (60.0 if depth == "medium" else 30.0)
            deadline = time.monotonic() + timeout_limit

            def _remaining() -> float:
                return deadline - time.monotonic()

            def on_attempt(model: str, tried: list[str]) -> None:
                if len(tried) > 1:
                    attempt_notes.append({
                        "type": "progress",
                        "model": model,
                        "detail": f"Модель «{tried[-2]}» остывает — пробую «{model}»…",
                    })
                else:
                    attempt_notes.append({"type": "progress", "model": model,
                                          "detail": f"Разбираю моделью «{model}»…"})

            def run_llm():
                # V2.0 М4: автосмена модели при «остывании» (429) — не падаем
                # из-за одного залипшего провайдера.
                rep, model = coach_mod.analyze_match_with_fallback(
                    normalized, body.account_id, cfg,
                    image_bytes=image_bytes,
                    hero_slug=body.hero_slug, depth=depth,
                    player_context=_player_context(),
                    on_attempt=on_attempt,
                    on_chunk=on_chunk,
                    total_timeout=timeout_limit,
                    attempt_timeout=attempt_budget,
                )
                used_model[0] = model
                return rep

            yield sse({"type": "stage", "stage": "llm"})
            yield sse({"type": "progress", "seconds": 0})
            task = asyncio.get_running_loop().run_in_executor(None, run_llm)
            while not task.done():
                elapsed = time.monotonic() - start
                if _remaining() <= 0:
                    yield sse({
                        "type": "error",
                        "detail": f"Превышено время ожидания ответа модели ({int(timeout_limit)}с). Проверьте ключ и выбранную модель.",
                    })
                    return
                await asyncio.sleep(0.5)
                yield sse({"type": "progress",
                           "seconds": int(elapsed)})
                while attempt_notes:
                    yield sse(attempt_notes.pop(0))
                while pending:
                    yield sse(pending.pop(0))
            try:
                report = task.result()
            except Exception as exc:
                yield sse({
                    "type": "error",
                    "detail": f"Ошибка анализа нейросетью: {exc}",
                })
                return
            while pending:
                yield sse(pending.pop(0))

            if report is None:
                yield sse({
                    "type": "error",
                    "detail": "Не удалось сформировать отчёт нейросети",
                })
                return

            yield sse({"type": "stage", "stage": "report"})
            review = _to_review(normalized, report, body.account_id, hero_slug=body.hero_slug)
            _db().save_review(review.model_dump())
            _update_llm_size_estimate(review)
            yield sse({"type": "stage", "stage": "done"})
            yield sse({"type": "result", **review.model_dump()})


        from fastapi.responses import StreamingResponse

        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache",
                                          "X-Accel-Buffering": "no"})

    @app.get("/api/match/reviews")
    def match_reviews(limit: int = 20) -> list[dict]:
        return _db().get_reviews(limit=limit)

    @app.post("/api/match/reviews/{match_id}/reanalyze", response_model=MatchReview)
    def match_reanalyze(match_id: str, body: ReanalyzeRequest) -> MatchReview:
        """V2.0 (ТЗ §3.5): переразобрать сохранённый матч с новой глубиной/
        обновлёнными инструкциями/моделью. Сырьё берётся из кэша match_raw
        (fetch_match кэширует навсегда), повторного запроса к API нет.
        """
        match_loader, coach_mod, client_mod = _get_match_pipeline()
        saved = _db().get_review(match_id)
        if saved is None:
            raise HTTPException(status_code=404, detail=f"Нет сохранённого разбора матча {match_id}")

        # Скриншот-разборы не хранят сырой телеметрии — переразобрать их нельзя.
        if str(match_id).startswith("screenshot-"):
            raise HTTPException(
                status_code=404,
                detail="Разбор по скриншоту без данных матча — переразбор невозможен. "
                       "Укажите Match ID и разберите матч заново.",
            )

        try:
            raw = match_loader.fetch_match(match_id, db=_db())
            hero_map = match_loader.load_hero_map()
            normalized = match_loader.normalize_match(raw, hero_map)
        except match_loader.RateLimitError as exc:
            raise HTTPException(status_code=429, detail=str(exc)) from exc
        except match_loader.MatchLoadError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

        from ..models import AIConfig  # noqa: F401 — re-exported seam

        depth = _validate_depth(body.depth)  # V2.0: short|medium|deep
        cfg = effective_ai_config(_db().get_ai_config())
        is_offline = (body.mode or "").lower() == "offline"
        if not is_offline and not cfg.api_key:
            raise HTTPException(
                status_code=422,
                detail="Для анализа матча нейросетью укажите API-ключ в профиле",
            )

        # Точка зрения: явный account_id; иначе hero_id из сохранённого разбора
        # (MatchReview не хранит account_id — резолвим его в нормализованном матче).
        account_id = body.account_id
        if account_id is None and isinstance(saved, dict):
            hero_id = str(saved.get("hero_id") or "").strip().lower()
            if hero_id:
                for p in normalized.players:
                    if p.hero_id_slug.lower() == hero_id:
                        account_id = p.account_id
                        break

        if is_offline:
            report = coach_mod.heuristic_report(normalized, account_id, hero_slug=body.hero_slug)
        else:
            client = client_mod.create_client(cfg)
            try:
                report = coach_mod.analyze_match(
                    normalized, account_id, client, image_bytes=None,
                    hero_slug=body.hero_slug, depth=depth,
                    player_context=_player_context(),
                    system_prompt_override=cfg.system_prompt_override,
                )
            except Exception as exc:
                raise HTTPException(
                    status_code=502,
                    detail=f"Ошибка анализа нейросетью: {exc}",
                ) from exc

        review = _to_review(normalized, report, account_id, hero_slug=body.hero_slug)
        review.progress = _player_context()
        _db().save_review(review.model_dump())
        return review

    # --- Assets -------------------------------------------------------------------

    @app.get("/api/assets/status")
    def assets_status() -> dict:
        assets_root = _web_dir() / "assets"
        heroes = len(list((assets_root / "heroes").glob("*"))) if (
            assets_root / "heroes").is_dir() else 0
        items = len(list((assets_root / "items").glob("*"))) if (
            assets_root / "items").is_dir() else 0
        has_map = (assets_root / "map" / "cursed_apple_minimap.png").is_file()
        return {
            "synced": heroes > 0,
            "heroes": heroes,
            "items": items,
            "map": has_map,
        }

    @app.post("/api/assets/sync")
    def assets_sync() -> dict:
        """TZ A4: внутренний/CLI-инструмент (python -m deadlockforge.assets.ingest).
        UI больше не дергает эту точку; оставлена для отладки и скриптов."""
        from ..assets.ingest import AssetIngest

        catalog = dataset_mod.get_catalog()
        ingest = AssetIngest(_web_dir() / "assets")
        report = ingest.sync([h.id for h in catalog.heroes])
        return report.as_dict()

    # --- Static SPA ---------------------------------------------------------

    _no_cache = {"Cache-Control": "no-cache, no-store, must-revalidate"}
    _assets_cache = {"Cache-Control": "public, max-age=86400, stale-while-revalidate=3600"}

    def _serve_with_etag(file_path: Path, request: Request, media_type: str | None = None) -> Response:
        if not file_path.is_file():
            raise HTTPException(status_code=404, detail="File not found.")
        st = file_path.stat()
        etag = f'"{st.st_mtime_ns:x}-{st.st_size:x}"'
        # no-cache + ETag: браузер обязан ревалидировать. Без этого заголовка
        # WebView2 кэширует эвристически и может отдать старый js после
        # обновления сборки.
        headers = {"ETag": etag, "Cache-Control": "no-cache, must-revalidate"}
        if_none_match = request.headers.get("if-none-match")
        if if_none_match:
            # Handle possible weak etag or multiple etags
            client_etags = [t.strip() for t in if_none_match.split(",")]
            if etag in client_etags or f"W/{etag}" in client_etags or "*" in client_etags:
                return Response(status_code=304, headers=headers)
        return FileResponse(file_path, media_type=media_type, headers=headers)

    @app.get("/")
    def index():
        return FileResponse(WEB_DIR / "index.html", headers=_no_cache)

    @app.get("/live.html")
    def live_page():
        """M4 §6: оверлей In-Game Assistant (отдельное окно PyWebView on_top)."""
        return FileResponse(WEB_DIR / "live.html", headers=_no_cache)

    @app.get("/overlay.html")
    def overlay_page():
        """Прозрачное окно плашек поверх игры (frameless + click-through)."""
        return FileResponse(WEB_DIR / "overlay.html", headers=_no_cache)

    @app.get("/favicon.ico")
    def favicon():
        # TZ A5/C1: убирает 404 из консоли; иконка живёт в web/assets/icons.
        target = WEB_DIR / "assets" / "icons" / "favicon.ico"
        if target.is_file():
            return FileResponse(target, headers=_assets_cache)
        return FileResponse(WEB_DIR / "favicon.ico")

    @app.get("/js/{file_name}")
    def js(file_name: str, request: Request):
        return _serve_with_etag(WEB_DIR / "js" / file_name, request, media_type="application/javascript")

    @app.get("/css/{file_name}")
    def css(file_name: str, request: Request):
        return _serve_with_etag(WEB_DIR / "css" / file_name, request, media_type="text/css")

    @app.get("/assets/{file_path:path}")
    def assets(file_path: str):
        root = (WEB_DIR / "assets").resolve()
        target = (root / file_path).resolve()
        # Проверка вхождения по Path-иерархии, а не по префиксу строки:
        # иначе соседний каталог с тем же началом имени ("assets_old") проходил бы.
        if not target.is_relative_to(root):
            raise HTTPException(status_code=403, detail="Forbidden path.")
        if not target.is_file():
            raise HTTPException(status_code=404, detail="Asset not found.")
        return FileResponse(target, headers=_assets_cache)

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8377)
