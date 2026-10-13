"""Pydantic contracts for DeadlockForge. The single shared seam of the project.

Every module (dataset, advisor, calculator, builds, api) speaks through these
types. Field names are frozen: data JSON files and engine code both target them.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

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
    """Объект на карте гайда: кемпы, урна, боссы, волкеры (TZ B2)."""

    layer: str                              # camps | urn | midboss | walkers
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

    time: str                                 # "MM:SS"
    location: str                             # линия / место на карте
    event: str                                # что произошло
    why_fatal: str                            # почему это стоило игры
    correct_play: str                         # верный сценарий


class ItemCorrection(BaseModel):
    slot: str                                 # категория-слот ("weapon-2")
    sold_or_skipped: str                      # что продано/пропущено
    recommended: str                          # что нужно было купить
    reason: str


class TeamfightSummary(BaseModel):
    """One teamfight: who died, when, what it cost."""

    time: str                                 # "MM:SS"
    outcome: str                              # кто выиграл размен и почему
    lesson: str                               # что делать иначе


class PurchaseTiming(BaseModel):
    """One item purchase timing callout."""

    time: str                                 # "MM:SS" покупки
    item: str                                 # предмет (id или имя)
    verdict: str                              # вовремя / поздно / рано
    comment: str


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


# --- AI config contract (settings screen seam) -----------------------------


class AIConfig(BaseModel):
    provider: str = "gemini"                # deepseek | openai | gemini | anthropic
    model_name: str = "gemini-3.8-flash-low"
    api_key: str = ""
    base_url: Optional[str] = None
    enable_thinking: bool = True
    thinking_budget: int = 2048
    temperature: float = 0.2
    system_prompt_override: Optional[str] = None


# TZ A3: параметры AI задаются кодом/дефолтами; UI принимает только api_key.
# Точка входа «зашитого ключа» — env DEADLOCKFORGE_AI_KEY, перекрывающий БД.
AI_CONFIG_DEFAULTS: dict[str, object] = {
    "provider": "gemini",
    "model_name": "gemini-3.8-flash-low",
    "base_url": None,
    "enable_thinking": True,
    "thinking_budget": 2048,
    "temperature": 0.2,
}


PROVIDER_DEFAULT_MODELS: dict[str, str] = {
    "openai": "gpt-4o",
    "anthropic": "claude-3-7-sonnet-latest",
    "gemini": "gemini-3.8-flash-low",
    "deepseek": "deepseek-chat",
}


def effective_ai_config(db_config: dict) -> AIConfig:
    """Merge DB row with code defaults + DEADLOCKFORGE_AI_KEY env override.

    Multi-vendor support: provider & model_name come from DB if saved,
    otherwise default; api_key comes from env DEADLOCKFORGE_AI_KEY if set, otherwise DB.
    """
    import os
    import sys
    from pathlib import Path

    env_key = os.environ.get("DEADLOCKFORGE_AI_KEY", "").strip()
    db_key = str((db_config or {}).get("api_key") or "").strip()
    file_key = ""

    # Only check api.txt if no key from env/db AND DEADLOCKFORGE_AI_KEY was not explicitly cleared
    if not env_key and not db_key and "DEADLOCKFORGE_AI_KEY" not in os.environ and "pytest" not in sys.modules:
        exe_dir = Path(sys.executable).parent if hasattr(sys, "_MEIPASS") else Path.cwd()
        for candidate in [
            Path("src/deadlockforge/ai/api.txt"),
            Path(__file__).resolve().parent / "ai" / "api.txt",
            Path("api.txt"),
            exe_dir / "api.txt",
            exe_dir / "data" / "api.txt",
        ]:
            if candidate.is_file():
                try:
                    file_key = candidate.read_text(encoding="utf-8").strip()
                    if file_key:
                        break
                except OSError:
                    pass

    api_key = env_key or db_key or file_key
    provider = (db_config or {}).get("provider") or AI_CONFIG_DEFAULTS["provider"]
    provider = str(provider).strip().lower()
    default_model = PROVIDER_DEFAULT_MODELS.get(provider, AI_CONFIG_DEFAULTS["model_name"])
    model_name = (db_config or {}).get("model_name") or default_model
    base_url = (db_config or {}).get("base_url") or AI_CONFIG_DEFAULTS["base_url"]

    # When using a proxy key (sk-...) with Gemini, route to the proxy endpoint unless overridden
    if provider == "gemini" and api_key.startswith("sk-") and not base_url:
        base_url = "https://api.zexkora.cc/v1"

    return AIConfig(
        provider=provider,
        model_name=str(model_name),
        base_url=base_url,  # type: ignore[arg-type]
        enable_thinking=bool(AI_CONFIG_DEFAULTS["enable_thinking"]),  # type: ignore[arg-type]
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
