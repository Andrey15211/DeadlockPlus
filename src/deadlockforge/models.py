"""Pydantic contracts for DeadlockForge. The single shared seam of the project.

Every module (dataset, advisor, calculator, builds, api) speaks through these
types. Field names are frozen: data JSON files and engine code both target them.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal, Optional

from pydantic import AliasChoices, BaseModel, Field, model_validator


class ThreatTag(str, Enum):
    """Closed vocabulary of signature threat types an enemy hero can pose."""

    HEAL = "heal"                      # heavy lifesteal / sustain (Abrams, Kelvin, Infernus)
    UNINTERRUPTIBLE_ULT = "uninterruptible_ult"  # channeled ults that CC cannot stop (Seven, McGinnis)
    BULLET_DPS = "bullet_dps"          # rapid gun damage (Haze, Wraith)
    SPIRIT_CASTER = "spirit_caster"    # ability/spirit damage (Pocket, Yamato, Grey Talon)
    BURST = "burst"                    # quick kill windows
    MOBILITY = "mobility"              # dashes, flight, hard to lock down
    STEALTH = "stealth"                # invisibility
    TANK = "tank"                      # high effective HP, frontline
    CC_CHAIN = "cc_chain"              # chain crowd control / long silences


class ItemCategory(str, Enum):
    WEAPON = "weapon"      # orange
    VITALITY = "vitality"  # green
    SPIRIT = "spirit"      # purple


class HeroRole(str, Enum):
    CARRY = "carry"
    SUPPORT = "support"
    INITIATOR = "initiator"
    DUELIST = "duelist"
    CONTROLLER = "controller"


class HeroStats(BaseModel):
    """Base stats of a hero at level 1."""

    hp: float = Field(gt=0)
    bullet_shield: float = Field(ge=0)
    spirit_shield: float = Field(ge=0)
    weapon_damage: float = Field(gt=0)
    fire_rate: float = Field(gt=0)          # shots per second
    spirit_power: float = Field(ge=0)
    stamina: int = Field(ge=0)
    move_speed: float = Field(gt=0)


class AbilityInfo(BaseModel):
    """One hero ability (1..4 + ultimate / innate)."""

    name: str
    slot: int = Field(ge=0, le=6)             # 1-4 способности, 5 ульта, 0 врождённая
    key: str = ""                             # "1".."4", "R", "F"
    cooldown_s: float = 0
    description: str = ""
    image: str = ""                           # путь в web/assets/...


class Hero(BaseModel):
    id: str
    name: str
    name_en: str = ""
    roles: list[HeroRole] = Field(min_length=1)
    stats: HeroStats
    threat_tags: list[ThreatTag] = Field(default_factory=list)
    signature: str = ""                       # короткое описание сигнатурных угроз
    difficulty: int = Field(default=1, ge=1, le=5)
    # --- расширенные поля (опциональны: старый датасет валиден) ---
    abilities: list[AbilityInfo] = Field(default_factory=list)
    portrait: str = ""                        # web/assets/heroes/{id}.webp
    minimap_icon: str = ""
    winrate: Optional[float] = None           # 0..1, из /v1/analytics/hero-stats
    pickrate: Optional[float] = None
    meta_updated_at: Optional[str] = None     # ISO-дата обновления меты (A2)
    in_development: bool = False              # Hero Labs / prerelease
    api_id: int = 0                           # числовой id deadlock-api


class ItemStats(BaseModel):
    """Stat modifiers granted by an item. All optional, default 0 (no effect).

    fire_rate_pct and cooldown_reduction_pct are fractions (0.12 = 12%).
    cooldown_reduction_pct is summed across items and capped by the calculator.
    """

    weapon_damage: float = 0
    fire_rate_pct: float = 0
    base_hp: float = 0
    bullet_shield: float = 0
    spirit_shield: float = 0
    stamina: int = 0
    cooldown_reduction_pct: float = 0
    spirit_power: float = 0
    move_speed: float = 0


class Item(BaseModel):
    id: str
    name: str
    name_en: str
    category: ItemCategory
    tier: int = Field(ge=1, le=5)
    cost: int = Field(ge=0)
    stats: ItemStats
    active: Optional[str] = None            # текст активного эффекта (или None)
    passive: Optional[str] = None           # текст пассивного эффекта (или None)
    counters: list[ThreatTag] = Field(default_factory=list)
    # --- расширенные поля (опциональны) ---
    upgrade_from: Optional[str] = None        # id предмета-предшественника
    upgrade_to: list[str] = Field(default_factory=list)
    components: list[str] = Field(default_factory=list)
    upgrades_to: list[str] = Field(default_factory=list)
    image: str = ""                           # web/assets/items/{id}.png
    active_desc: str = ""                     # текст активки (краткая форма)


class CounterEntry(BaseModel):
    """One counter-buy recommendation for a threat tag."""

    item_id: str
    priority: int = Field(ge=1, le=10)      # 1 = самый приоритетный ответ на тег
    explanation: str
    timing: str                             # например "10-15 мин, Tier 2"


class CounterRule(BaseModel):
    """For a threat tag: ordered counter items."""

    tag: ThreatTag
    entries: list[CounterEntry] = Field(min_length=1)


class Lane(BaseModel):
    id: str
    name: str
    color: str                              # hex-акцент линии
    character: str                          # промышленный / центральный / парковый
    districts: list[str] = Field(min_length=1)
    notes: str


class TimelineEvent(BaseModel):
    time: str                               # "0:00", "3:00", ...
    title: str
    description: str


class MapMarker(BaseModel):
    """Объект на карте гайда: лагеря, урна, башни, мидбосс.

    Слои закрыты: маркеры строятся из авторитетных данных deadlock-api
    (neutral_camps, objective_positions, soul_urn_spawns), а не вписываются
    вручную. Устаревший слой walkers заменён на towers.
    """

    layer: Literal["camps", "urn", "towers", "midboss"]
    lane: str                               # york | broadway | greenwich
    x: float = Field(ge=0.0, le=1.0)        # нормализованные 0..1
    y: float = Field(ge=0.0, le=1.0)
    label: str


class MapGuide(BaseModel):
    lanes: list[Lane] = Field(min_length=3)
    lane_assignment: str                    # расстановка 2-2-2 и роуминг
    timeline: list[TimelineEvent] = Field(min_length=1)
    # --- TZ B2: маркеры и координатная привязка (опционально для старых данных)
    markers: list[MapMarker] = Field(default_factory=list)
    coordinate_system: Optional[dict] = None
    markers_source: Optional[dict] = None   # происхождение маркеров (endpoint, формула)


# --- Engine result types ---------------------------------------------------


class ThreatAssessment(BaseModel):
    tag: ThreatTag
    score: float
    contributors: list[str]                 # id героев, дающих вклад


class Recommendation(BaseModel):
    item: Item
    explanation: str
    priority: int = Field(ge=1, le=10)
    tier: int
    cost: int
    timing: str


class Advice(BaseModel):
    threats: list[ThreatAssessment]
    recommendations: list[Recommendation]


class StatsSnapshot(BaseModel):
    """Result of the build stat calculator.

    effective_hp_bullet / effective_hp_spirit follow the Deadlock shield
    model: a shield is a separate HP pool that absorbs exactly its own value
    in damage of the matching type before touches of raw HP, so effective
    HP against one damage type is hp + that shield.
    """

    hp: float
    bullet_shield: float
    spirit_shield: float
    effective_hp_bullet: float
    effective_hp_spirit: float
    weapon_damage: float
    fire_rate: float
    weapon_dps: float
    spirit_power: float
    stamina: int
    move_speed: float
    cooldown_reduction_pct: float
    souls_spent: int


class BuildError(ValueError):
    """Raised when a build violates slot rules (wrong category / duplicate)."""


class BuildSlots(BaseModel):
    """36 slots: 12 per category (weapon, vitality, spirit). Flex removed."""

    weapon: list[Optional[str]] = Field(default_factory=lambda: [None] * 12)
    vitality: list[Optional[str]] = Field(default_factory=lambda: [None] * 12)
    spirit: list[Optional[str]] = Field(default_factory=lambda: [None] * 12)
    flex: list[Optional[str]] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _normalize_slots(cls, data: Any) -> Any:
        if isinstance(data, dict):
            for g in ("weapon", "vitality", "spirit"):
                val = data.get(g)
                if isinstance(val, list) and len(val) < 12:
                    data[g] = list(val) + [None] * (12 - len(val))
        return data

    def all_item_ids(self) -> list[str]:
        ids: list[str] = []
        for slots in (self.weapon, self.vitality, self.spirit, self.flex):
            ids.extend(i for i in slots if i is not None)
        return ids



TIER_COST_BANDS: dict[int, tuple[int, int]] = {
    # Actual in-game economy (verified via deadlock-api 2026-10):
    # T1=800, T2=1600, T3=3200, T4=6400, T5=9999 (special items).
    1: (500, 1249),
    2: (1250, 2999),
    3: (3000, 6199),
    4: (6200, 9100),
    5: (9100, 10**9),
}

CDR_CAP = 0.55


# --- AI Coach & Deep Match contracts (Subagent 3 seam) --------------------


class KeyMistake(BaseModel):
    """The single fatal mistake of the match, with timestamp."""

    time: str = ""                                 # "MM:SS"
    location: str = Field(
        default="—",
        validation_alias=AliasChoices("location", "place", "lane", "map_location"),
    )
    event: str = Field(
        default="",
        validation_alias=AliasChoices("event", "mistake", "action", "description"),
    )
    why_fatal: str = Field(
        default="",
        validation_alias=AliasChoices("why_fatal", "reason", "impact", "why"),
    )
    correct_play: str = Field(
        default="",
        validation_alias=AliasChoices("correct_play", "lesson", "recommendation", "solution"),
    )


class ItemCorrection(BaseModel):
    slot: str = Field(
        default="",
        validation_alias=AliasChoices("slot", "category", "item_slot"),
    )
    sold_or_skipped: str = Field(
        default="",
        validation_alias=AliasChoices("sold_or_skipped", "skipped", "sold", "item_sold", "item_skipped"),
    )
    recommended: str = Field(
        default="",
        validation_alias=AliasChoices("recommended", "item_recommended", "buy"),
    )
    reason: str = Field(
        default="",
        validation_alias=AliasChoices("reason", "comment", "explanation"),
    )


class TeamfightSummary(BaseModel):
    """One teamfight: who died, when, what it cost."""

    time: str = ""                                 # "MM:SS"
    outcome: str = Field(
        default="",
        validation_alias=AliasChoices("outcome", "result", "verdict", "event"),
    )
    lesson: str = Field(
        default="",
        validation_alias=AliasChoices("lesson", "correct_play", "tip", "recommendation"),
    )


class PurchaseTiming(BaseModel):
    """One item purchase timing callout."""

    time: str = ""                                 # "MM:SS" покупки
    item: str = ""                                 # предмет (id или имя)
    verdict: str = Field(
        default="вовремя",
        validation_alias=AliasChoices("verdict", "status", "timing_verdict", "evaluation"),
    )
    comment: str = Field(
        default="",
        validation_alias=AliasChoices("comment", "reason", "notes", "description"),
    )


class DeathSummary(BaseModel):
    """V2.0: one player death breakdown (глубина «deep»)."""

    time: str = ""                                 # "MM:SS"
    location: str = Field(
        default="—",
        validation_alias=AliasChoices("location", "place", "lane", "map_location"),
    )
    cause: str = Field(
        default="",
        validation_alias=AliasChoices("cause", "killer", "reason", "why_fatal", "event", "description"),
    )
    lesson: str = Field(
        default="",
        validation_alias=AliasChoices("lesson", "correct_play", "tip", "recommendation", "solution"),
    )


class LaneComparison(BaseModel):
    """V2.0: сравнение с соперником по линии (глубина «deep»)."""

    opponent_hero: str = Field(
        default="—",
        validation_alias=AliasChoices("opponent_hero", "hero", "enemy_hero", "enemy"),
    )
    opponent_kda: str = Field(
        default="—",
        validation_alias=AliasChoices("opponent_kda", "kda", "enemy_kda"),
    )
    opponent_souls_at_10: float = Field(
        default=0.0,
        validation_alias=AliasChoices("opponent_souls_at_10", "souls_at_10", "souls10"),
    )
    opponent_net_worth: int = Field(
        default=0,
        validation_alias=AliasChoices("opponent_net_worth", "net_worth", "souls"),
    )
    opponent_hero_id: str = Field(
        default="",
        validation_alias=AliasChoices(
            "opponent_hero_id", "opponent_slug", "enemy_hero_id", "hero_id"
        ),
    )
    verdict: str = Field(
        default="",
        validation_alias=AliasChoices("verdict", "summary", "conclusion", "result"),
    )


class Benchmarks(BaseModel):
    """Player percentiles vs rank pool."""

    souls_at_10: float = 0.0
    lane_efficiency: float = 0.0
    fight_participation: float = Field(
        default=0.0,
        validation_alias=AliasChoices("fight_participation", "teamfight_participation"),
    )


class CoachReport(BaseModel):
    """Strict structured output of the AI coach."""

    performance_grade: str = Field(pattern=r"^(S\+|S|A|B|C)$")
    key_mistake: KeyMistake
    coach_conclusion: list[str] = Field(min_length=3, max_length=5)
    benchmarks: Benchmarks
    item_corrections: list[ItemCorrection] = Field(default_factory=list)
    teamfights: list[TeamfightSummary] = Field(default_factory=list)
    purchase_timings: list[PurchaseTiming] = Field(default_factory=list)
    # V2.0: опциональные поля глубины «deep» (обязательные поля не менялись)
    death_breakdown: list[DeathSummary] = Field(default_factory=list)
    lane_comparison: Optional[LaneComparison] = None


class SoulsPoint(BaseModel):
    """One point of the souls advantage timeline."""

    time_sec: int
    networth_delta: float                     # преимущество команды игрока
    event: str = ""


class MatchReview(BaseModel):
    """Normalized deep-match record persisted to SQLite."""

    match_id: str
    hero_id: str
    duration_seconds: int
    result: str = Field(pattern=r"^(win|loss)$")
    kda: str                                  # "14/3/9"
    souls_curve: list[SoulsPoint] = Field(default_factory=list)
    performance_grade: str = "C"
    key_mistake: dict = Field(default_factory=dict)
    coach_conclusion: list[str] = Field(default_factory=list)
    stat_benchmarks: dict = Field(default_factory=dict)
    item_analysis: dict = Field(default_factory=dict)
    # V2.0: расширенные аналитические блоки глубокого разбора
    teamfights: list[dict] = Field(default_factory=list)
    purchase_timings: list[dict] = Field(default_factory=list)
    death_breakdown: list[dict] = Field(default_factory=list)
    lane_comparison: Optional[dict] = None
    # V2.0 §4: точка зрения игрока (персистится — нужна для сравнения «с собой»)
    account_id: Optional[int] = None
    # V2.0: сравнение «с собой» — тренд + слабости игрока (не персистится)
    progress: Optional[dict] = None
    # V2.0 §4.1: дельта к средней норме игрока на этом герое (не персистится)
    personal_benchmark: Optional[dict] = None
    # V2.0 §5: координаты смертей для интерактивной миникарты (не персистится)
    deaths_geo: list[dict] = Field(default_factory=list)


# --- AI config contract (settings screen seam) -----------------------------


class AIConfig(BaseModel):
    provider: str = "openai"                # deepseek | openai | anthropic | router
    model_name: str = "deepseek-v4.1-flash" # Актуальная рабочая модель роутера (Zexkora)
    api_key: str = ""
    base_url: Optional[str] = None
    enable_thinking: bool = True
    reasoning_effort: Optional[str] = "low" # off | low | medium | high
    thinking_budget: int = 2048
    temperature: float = 0.2
    system_prompt_override: Optional[str] = None


# TZ A3: параметры AI задаются кодом/дефолтами; UI принимает только api_key.
# Точка входа «зашитого ключа» — env DEADLOCKFORGE_AI_KEY, перекрывающий БД.
# V2.0: дефолтная модель — deepseek-v4.1-flash (роутер OpenAI-совместимый), каталог из 20 моделей.
AI_CONFIG_DEFAULTS: dict[str, object] = {
    "provider": "openai",
    "model_name": "deepseek-v4.1-flash",
    "base_url": None,
    "enable_thinking": True,
    "reasoning_effort": "low",
    "thinking_budget": 2048,
    "temperature": 0.2,
}


PROVIDER_DEFAULT_MODELS: dict[str, str] = {
    "openai": "deepseek-v4.1-flash",
    "anthropic": "claude-4.8",
    "deepseek": "deepseek-v4.1-flash",
}


def _clean_key(raw: str) -> str:
    """Убрать BOM/пробелы/переводы строк из ключа.

    Регрессия: ключ, сохранённый с BOM (utf-8-sig), попадал прямо в URL
    запроса и валил его с UnicodeEncodeError — «ключ не работает» без
    внятной причины.
    """
    return raw.replace("\ufeff", "").strip().strip('"').strip("'")


def effective_ai_config(db_config: dict) -> AIConfig:
    """Merge DB row with code defaults + DEADLOCKFORGE_AI_KEY env override.

    Multi-vendor support: provider & model_name come from DB if saved,
    otherwise default; api_key comes from env DEADLOCKFORGE_AI_KEY if set, otherwise DB.
    """
    import os
    import sys
    from pathlib import Path

    env_key = _clean_key(os.environ.get("DEADLOCKFORGE_AI_KEY", ""))
    db_key = _clean_key(str((db_config or {}).get("api_key") or ""))
    file_key = ""

    # Локальный файл с ключом читается ТОЛЬКО вне пакета/репозитория:
    # внутрипакетный src/deadlockforge/ai/api.txt исключён из поиска, потому что
    # упаковывался в exe (утечка ключа в дистрибутив) и попадал в git.
    if not env_key and not db_key and "DEADLOCKFORGE_AI_KEY" not in os.environ and "pytest" not in sys.modules:
        exe_dir = Path(sys.executable).parent if hasattr(sys, "_MEIPASS") else Path.cwd()
        for candidate in [
            exe_dir / "api.txt",
            exe_dir / "data" / "api.txt",
            exe_dir.parent / "api.txt",
        ]:
            if candidate.is_file():
                try:
                    file_key = _clean_key(candidate.read_text(encoding="utf-8-sig"))
                    if file_key:
                        break
                except OSError:
                    pass

    api_key = env_key or db_key or file_key
    provider = (db_config or {}).get("provider") or AI_CONFIG_DEFAULTS["provider"]
    provider = str(provider).strip().lower()
    default_model = PROVIDER_DEFAULT_MODELS.get(provider, AI_CONFIG_DEFAULTS["model_name"])
    base_url = (db_config or {}).get("base_url") or AI_CONFIG_DEFAULTS["base_url"]
    # V2.0 М4: провайдер "gemini" удалён полностью (недоступен по API).
    # Если в БД остался легаси-провайдер gemini — лечим в openai+роутер,
    # иначе create_client упадёт на неизвестном провайдере.
    if provider == "gemini":
        provider = "openai"
        if not base_url:
            base_url = "http://127.0.0.1:8089/v1"
    # V2.0 М4: ключи sk-... у владельца — роутерные (zexkora/локальный
    # failover 127.0.0.1:8089). Если base_url не задан (чистая БД у друга,
    # exe + api.txt) — не уходить на api.openai.com, а использовать локальный
    # failover-роутер, как в UI по умолчанию.
    if provider in ("openai", "router", "openrouter") and api_key.startswith("sk-") and not base_url:
        base_url = "http://127.0.0.1:8089/v1"
    model_name = (db_config or {}).get("model_name") or default_model
    model_name = str(model_name)
    # Легаси-имена gemini/glm-моделей в БД — остатки старых настроек; на роутере
    # их нет (выведены из эксплуатации), резолвим в рабочую deepseek-v4.1-flash.
    if model_name.startswith("gemini-") or "glm-5.3" in model_name.lower():
        model_name = "deepseek-v4.1-flash"

    reasoning_effort = (db_config or {}).get("reasoning_effort") or AI_CONFIG_DEFAULTS.get("reasoning_effort", "low")

    return AIConfig(
        provider=provider,
        model_name=str(model_name),
        base_url=base_url,  # type: ignore[arg-type]
        enable_thinking=bool(AI_CONFIG_DEFAULTS["enable_thinking"]),  # type: ignore[arg-type]
        reasoning_effort=str(reasoning_effort),
        thinking_budget=int(AI_CONFIG_DEFAULTS["thinking_budget"]),  # type: ignore[arg-type]
        temperature=float(AI_CONFIG_DEFAULTS["temperature"]),  # type: ignore[arg-type]
        api_key=api_key,
    )



# --- Draft / Red Flags seam (Subagent 2) -----------------------------------


class RedFlag(BaseModel):
    """Critical draft vulnerability detected by the 6v6 engine."""

    severity: str = Field(pattern=r"^(critical|warning|info)$")
    title: str
    detail: str
    fix: str                                   # конкретное действие/предмет


# --- Контрпики (экран 7) ---------------------------------------------------
# Источник: deadlockitembuilder.com, переведено на русский с официальной
# терминологией deadlock-api. Строится скриптом scripts/build_counterpicks.py.


class CounterOffer(BaseModel):
    """Предмет-ответ в наборе на фазу: основной или альтернативный."""

    item_id: str
    label: str = ""                            # «Ситуационный ответ», «Опция для линии»
    alt: bool = False                          # True — запасной вариант, а не основной


class CounterProblem(BaseModel):
    """Конкретная проблема в матчапе и покупка, которая её закрывает."""

    problem: str
    item_id: str
    label: str = ""
    when: str = ""
    how: str = ""


class CounterStage(BaseModel):
    """Набор предметов на одну фазу игры."""

    early: list[CounterOffer] = Field(default_factory=list)
    mid: list[CounterOffer] = Field(default_factory=list)
    late: list[CounterOffer] = Field(default_factory=list)


class CounterGlance(BaseModel):
    """Краткая выжимка: главная угроза, окно игрока, приоритет покупки."""

    main_threat: str = ""
    your_opening: str = ""
    buy_priority: str = ""


class CounterThreat(BaseModel):
    """Угроза героя в терминах архетипа поведения."""

    label: str
    key: str = ""


class CounterSit(BaseModel):
    """Связка «проблема → предмет» из helper-слоя сайта."""

    problem: str
    item_id: str
    how: str = ""


class CounterHelper(BaseModel):
    """Сводка и разбор проблем для быстрого блока «во что играть»."""

    summary: str = ""
    tip: str = ""
    sits: list[CounterSit] = Field(default_factory=list)


class CounterHero(BaseModel):
    """Гайд «как играть против героя» целиком."""

    id: str
    name: str
    name_en: str = ""
    portrait: str = ""
    intro: str = ""
    roles: list[str] = Field(default_factory=list)
    role_text: list[str] = Field(default_factory=list)
    threats: list[CounterThreat] = Field(default_factory=list)
    groups: list[str] = Field(default_factory=list)         # ключи групп сайта
    group_tags: list[ThreatTag] = Field(default_factory=list)  # их теги проекта
    glance: CounterGlance = Field(default_factory=CounterGlance)
    note: str = ""
    top: list[str] = Field(default_factory=list)            # лучшие ответы, по убыванию
    stages: CounterStage = Field(default_factory=CounterStage)
    problems: list[CounterProblem] = Field(default_factory=list)
    why: dict[str, str] = Field(default_factory=dict)       # item_id -> почему помогает
    lane: list[str] = Field(default_factory=list)
    fight: list[str] = Field(default_factory=list)
    faq: list[list[str]] = Field(default_factory=list)      # пары [вопрос, ответ]
    related: list[str] = Field(default_factory=list)        # id других героев
    helper: CounterHelper = Field(default_factory=CounterHelper)


class CounterItemTarget(BaseModel):
    """Матчап, в котором предмет работает: герой и проблема из его гайда."""

    hero_id: str
    problem_index: Optional[int] = None
    note: str = ""
    methods: list[str] = Field(default_factory=list)


class CounterItemEntry(BaseModel):
    """Против кого предмет и с какой пометкой (в списке «все матчапы»)."""

    hero_id: str
    label: str = ""


class CounterItemAlt(BaseModel):
    """Запасной предмет с пояснением, чем он отличается."""

    item_id: str
    text: str = ""


class CounterItemGuide(BaseModel):
    """Гайд по предмету-контру: когда брать, чем заменить, во что играть."""

    id: str
    name: str
    name_en: str = ""
    category: ItemCategory
    tier: int = Field(ge=1, le=5)
    cost: int = Field(ge=0)
    image: str = ""
    roles: list[str] = Field(default_factory=list)
    stages: list[str] = Field(default_factory=list)
    short: str = ""
    intro: str = ""
    who: str = ""
    when: str = ""
    example: str = ""
    watch: str = ""
    buy: str = ""
    why: list[str] = Field(default_factory=list)
    timing: list[list[str]] = Field(default_factory=list)   # [фаза, средняя минута]
    avg_minutes: Optional[float] = None
    best: list[CounterItemTarget] = Field(default_factory=list)
    all: list[CounterItemEntry] = Field(default_factory=list)
    alts: list[CounterItemAlt] = Field(default_factory=list)


class CounterGroup(BaseModel):
    """Группа контр-мер: например «Анти-хил» со списком предметов."""

    key: str
    name: str
    items: list[str] = Field(default_factory=list)


class Counterpicks(BaseModel):
    """Корень датасета «Контрпики»."""

    version: int = 1
    source: str = ""
    extracted_at: str = ""
    stages: list[str] = Field(default_factory=list)
    groups: list[CounterGroup] = Field(default_factory=list)
    heroes: list[CounterHero] = Field(default_factory=list)
    items: list[CounterItemGuide] = Field(default_factory=list)


# --- Синергии (экран «Синергии») -------------------------------------------


class SynergyHero(BaseModel):
    """Герой в дуо: локальный id, русское имя и портрет из heroes.json."""

    id: str
    name: str
    name_en: str = ""
    portrait: str = ""


class SynergyDuo(BaseModel):
    """Связка двух героев на одной линии по данным Mobalytics.

    Метрики в тех же единицах, что в таблице сайта: проценты — числа
    (62.4 = 62.4%), CS/NW — за минуту, KDA — отношение.
    """

    rank: int = Field(ge=1)
    tier: Literal["S", "A", "B", "C"]
    hero1: SynergyHero
    hero2: SynergyHero
    winrate: float = Field(ge=0, le=100)
    wrf: float
    pickrate: float = Field(ge=0, le=100)
    cs_per_min: float = Field(ge=0)
    denies: float = Field(ge=0)
    nw_per_min: float = Field(ge=0)
    kda: float = Field(ge=0)


class Synergies(BaseModel):
    """Корень датасета «Синергии»."""

    version: int = 1
    source: str = ""
    collected_at: str = ""
    tier_rules: dict[str, float] = Field(default_factory=dict)
    methodology: list[str] = Field(default_factory=list)
    duos: list[SynergyDuo] = Field(default_factory=list)
