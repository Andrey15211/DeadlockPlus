"""AI coach: turn a normalized match into a validated CoachReport.

Primary path: LLM via LLMClient.generate_coach_report -> strict JSON ->
CoachReport.model_validate. Fallback path (critical: the app must work
without an API key): a local heuristic report with real numbers from the
match data.
"""

from __future__ import annotations

import json
import re
import time
from typing import Optional

from pydantic import ValidationError

from ..engine.match_loader import NormalizedMatch
from ..models import (
    Benchmarks,
    CoachReport,
    DeathSummary,
    ItemCorrection,
    KeyMistake,
    LaneComparison,
    PurchaseTiming,
    TeamfightSummary,
)
from .client import LLMClient, LLMError
from .deadlock_knowledge import (
    evaluate_souls_pace,
    get_counter_recommendations_for_enemies,
    get_tactical_knowledge_prompt,
    localize_report_dict,
    localize_text,
)

BASE_COACH_PROMPT = """\
Ты — элитный аналитик и тренер высшего ранга (High MMR / Eternus) в Deadlock.
Твой разбор предельно техничен, циничен к ошибкам, объективен и свободен от воды.
Запрещены любые шаблонные односложные отписки («1 раз умер, главная ошибка», «играл неплохо»).
Каждый тезис опирается на точные тайминги (MM:SS), механику способностей, позиционирование и макро-ситуацию.

ЛАКОНИЧНОСТЬ — ЖЁСТКОЕ ТРЕБОВАНИЕ (приоритет №1):
Отвечай МАКСИМАЛЬНО КРАТКО и по делу. Никакой воды, повторов, общих слов,
вводных фраз и лишнего контекста. Каждая фраза несёт факт или цифру.
- Весь ответ — один компактный JSON (см. схему). Никаких вступлений,
  благодарностей, пояснений и текста вне JSON.
- coach_conclusion: каждый пункт — 1 короткое предложение (до ~15 слов).
- item_corrections / purchase_timings / teamfights / death_breakdown:
  reason, comment, lesson — до ~10 слов на поле.
- key_mistake: event / why_fatal / correct_play — до ~15 слов на поле.
- Целевой объём всего ответа: 300–700 слов. Если фактов мало — пиши меньше,
  но точно. Запрещено раздувать отчёт ради объёма.

Ты всегда отвечаешь СТРОГО одним валидным JSON-объектом по схеме:

{
  "performance_grade": "S+|S|A|B|C",
  "key_mistake": {
    "time": "MM:SS",
    "location": "линия/место на карте (например: яма Мидбосса, вражеский Шагоход T2, проход между линиями)",
    "event": "что произошло (инициация, позиционная ошибка, драка без ультов)",
    "why_fatal": "почему это стоило игры/файта (механическая причина: соло выход без обзора, смерть в лейтгейме с 60+с таймером возрождения при отсутствии Омолодителя, отдача Мидбосса)",
    "correct_play": "верный сценарий действий"
  },
  "coach_conclusion": [
    "Оценка темпа фарма на 10-й минуте в сравнении с нормативом роли (керри 5000+, саппорт 3500+ душ).",
    "Аудит билда и контр-предметов: что было куплено не вовремя или какой предмет отсутствовал против сетапа врага.",
    "Упражнение 1 на следующие матчи...",
    "Упражнение 2 на следующие матчи...",
    "Упражнение 3 на следующие матчи..."
  ],
  "benchmarks": {
    "souls_at_10": 0.0,
    "lane_efficiency": 0.0,
    "fight_participation": 0.0
  },
  "item_corrections": [
    {"slot": "weapon-2", "sold_or_skipped": "...", "recommended": "...",
     "reason": "..."}
  ]
}

СТРОГИЕ ПРАВИЛА РАЗБОРА (TZ v1.6):
0. ПЕРСПЕКТИВА ИГРОКА (ТОЧКА ЗРЕНИЯ):
   Ты проводишь персональный разбор СТРОГО для героя пользователя, указанного в поле `my_hero_ru` (и `my_hero`)!
   Категорически запрещено путать героя пользователя с его союзниками или врагами!
   Все выводы, бенчмарки, тайминги закупок и разборы смертей относятся ИСКЛЮЧИТЕЛЬНО к `my_hero_ru`.
1. Тактический анализ ключевого момента (key_mistake):
   - Назови точный тайминг ("MM:SS"), точку карты и механическую причину слива файта
     (например: «Выход на линию без обзора при живом Мидбоссе у врага», «Смерть соло в чужом лесу после 15:00»).
   - КАТЕГОРИЧЕСКИЙ ЗАПРЕТ ВЫКУПА/БАЙБЕКА:
     В Deadlock НЕТ механики выкупа/байбека (Buyback), нет вардов и нет смоков.
     Категорически запрещено упоминать выкуп, байбек, "резерв на Buyback" или "копить на выкуп"!
     Единственный способ быстрого возрождения в игре — бафф Омолодителя (Rejuvenator) с Мидбосса.
     Смерть после 20:00 означает таймер возрождения 50-70+ секунд без возможности выкупиться.
2. СТРОЖАЙШЕЕ ТРЕБОВАНИЕ — 100% РУССКИЕ НАЗВАНИЯ В ТЕКСТЕ ОТЧЁТА:
   В итоговом JSON-отчёте (во всех полях: key_mistake, coach_conclusion, item_corrections) ВСЕ названия персонажей, предметов магазина, способностей героев и объектов карты должны быть ИСКЛЮЧИТЕЛЬНО НА РУССКОМ ЯЗЫКЕ!
   Категорически запрещено использовать английские слова для имён героев, предметов, способностей и объектов карты!
   - Герои: только русские имена (Виндикта [не Vindicta], Хлыст [не Lash], Хейз [не Haze], Тень [не Wraith], Инфернус [не Infernus], Семь [не Seven], Абрамс [не Abrams], Кельвин [не Kelvin], Леди Гайст [не Lady Geist], Серый Коготь [не Grey Talon], Мо и Мелкий [не Mo & Krill], Шив [не Shiv], Айви [не Ivy], Дозорный [не Warden], Ямато [не Yamato], Вязкус [не Viscous], Карман [не Pocket], Мираж [не Mirage], Бебоп [не Bebop], Парадокс [не Paradox], Динамо [не Dynamo], Макгиннис [не McGinnis] и др.).
   - Предметы: только русские названия (Нокдаун [не Knockdown], Гроза целителей [не Healbane], Токсичные пули [не Toxic Bullets], Металлическая кожа [не Metal Skin], Обратный огонь [не Return Fire], Проклятие [не Curse], Колосс [не Colossus], Кровопийца [не Leech], Измельчитель пулестойкости [не Bullet Resist Shredder], Стеклянная пушка [не Glass Cannon] и др.).
   - Способности: только русские названия (Полёт [не Flight], Ликвидация [не Assassinate], Удар оземь [не Ground Strike], Смертельный бросок [не Death Slam], Ручной ворон [не Crow Familiar], Вытягивание жизни [не Life Drain], Грозовое облако [не Storm Cloud], Танец пуль [не Bullet Dance] и др.).
   - Объекты карты: только русские названия (Мидбосс [не Midboss], Омолодитель [не Rejuvenator], Шагоход / Шагоход T2 [не Walker / T2 Walker], Страж [не Guardian], Покровитель [не Patron], Урна душ [не Soul Urn], Секретный магазин [не Secret Shop], Базовый магазин [не Base Shop], Тросы / Зиплайн [не Zipline]).
3. Оценка темпа фарма (в benchmarks и coach_conclusion):
   - Сравни количество душ игрока на 10-й минуте с эталоном роли:
     * Керри (Хейз, Тень, Инфернус, Семь, Виндикта): норма 5000+ душ.
     * Дуэлянт/Инициатор (Абрамс, Шив, Хлыст, Ямато): норма 4200+ душ.
     * Саппорт/Контроллер (Кельвин, Айви, Динамо, Макгиннис): норма 3500+ душ.
   - В benchmarks.souls_at_10 укажи отношение душ на 10:00 к эталону роли в виде коэффициента
     (например, 1.15 или 0.90, где 1.0 = 100% нормы роли). Категорически запрещено писать абсолютное количество душ (5000+)!
4. Аудит билда (item_corrections):
   - Укажи, какой предмет был куплен не вовремя или какой критический контр-предмет отсутствовал против сетапа врага (все предметы строго на русском):
     * Против хила (Кельвин, Абрамс, Инфернус, Айви) -> Гроза целителей / Токсичные пули.
     * Против пулевого DPS (Хейз, Тень) -> Металлическая кожа / Обратный огонь.
     * Против кастеров и ультимейтов (Семь, Макгиннис, Виндикта) -> Нокдаун / Проклятие.
     * Против плотного фронтлайна -> Колосс / Измельчитель пулестойкости.
5. Заключение тренера (coach_conclusion):
   - Ровно 3-5 содержательных пунктов: [оценка темпа фарма на 10:00] + [аудит билда и позиционки] +
     [3 практических упражнения на следующие матчи с измеримым результатом]. Никаких лозунгов. Все термины строго на русском.
6. benchmarks:
   - souls_at_10, lane_efficiency, fight_participation — строго коэффициенты от 0.0 до 1.5+ (где 1.0 = 100% нормы роли). Не пиши тысячи душ!
7. Никакого текста вне JSON. Никаких markdown-обёрток.
"""

COACH_SYSTEM_PROMPT = BASE_COACH_PROMPT

# --- V2.0: три глубины разбора (ТЗ §3.1) -----------------------------------

VALID_DEPTHS = ("short", "medium", "deep")
DEFAULT_DEPTH = "medium"

COACH_PROMPT_SUFFIXES: dict[str, str] = {
    "short": """\
РЕЖИМ РАЗБОРА: «КОРОТКИЙ» (быстрый, ≤30 с).
Отчёт ДОЛЖЕН содержать ТОЛЬКО: performance_grade, key_mistake,
coach_conclusion (РОВНО 3 пункта), benchmarks.
- item_corrections — пустой массив []; teamfights, purchase_timings,
  death_breakdown, lane_comparison — НЕ заполняй.
- НЕ разбирай покупки, тайминги, тимфайты и смерти по отдельности.
- coach_conclusion: [1] оценка темпа фарма на 10:00 против нормы роли,
  [2] главная причина исхода матча, [3] одно конкретное упражнение на
  следующий матч с измеримым результатом.
""",
    "medium": """\
РЕЖИМ РАЗБОРА: «СРЕДНИЙ» (по умолчанию).
Отчёт: performance_grade, key_mistake, coach_conclusion (3-5 пунктов),
benchmarks, item_corrections, purchase_timings.
- purchase_timings: 2-4 пункта — аудит таймингов покупок: что куплено
  поздно/рано, какой критический предмет пропущен против сетапа врага
  (time "MM:SS", item на русском, verdict вовремя/поздно/рано, comment).
- teamfights — пустой массив []; death_breakdown, lane_comparison — НЕ заполняй.
""",
    "deep": """\
РЕЖИМ РАЗБОРА: «МАКСИМАЛЬНЫЙ» (полный).
ВАЖНО: Формулируй анализ четко и предельно лаконично (1-2 предложения на пункт). Не трать токены на избыточные рассуждения, сразу формируй итоговый JSON.
Отчёт: performance_grade, key_mistake, coach_conclusion (5 пунктов),
benchmarks, item_corrections, purchase_timings, teamfights,
death_breakdown, lane_comparison.
- teamfights: массив объектов {time, outcome, lesson}. Разбери тимфайты
  (time "MM:SS", outcome - кто выиграл размен и почему, lesson).
- death_breakdown: массив объектов {time, location, cause, lesson}. Разбери
  смерти игрока из поля deaths (где, когда, причина гибели/killer, что делать иначе). До 10 смертей.
- lane_comparison: объект {opponent_hero, opponent_kda, opponent_souls_at_10, opponent_net_worth, verdict}.
  Сравни с соперником по линии (поле lane_opponent). Кто выиграл линию и за счёт чего — verdict.
- В coach_conclusion добавь пункт про итог линии против lane_opponent.
""",
}


def get_coach_prompt(depth: str | None = None) -> str:
    """Промпт тренера для выбранной глубины: BASE + суффикс глубины.

    Валидные значения: short | medium | deep (регистронезависимо);
    дефолт — medium. Неизвестная глубина -> ValueError.
    """
    d = (depth or DEFAULT_DEPTH).strip().lower()
    if d not in VALID_DEPTHS:
        raise ValueError(
            f"unknown depth '{depth}', expected one of {sorted(VALID_DEPTHS)}"
        )
    return BASE_COACH_PROMPT + "\n\n" + COACH_PROMPT_SUFFIXES[d]


def build_coach_prompt(depth: str | None = None,
                       player_context: dict | None = None,
                       system_prompt_override: str | None = None) -> str:
    """Собрать финальный промпт: глубина + история игрока + правила юзера.

    player_context — результат ai.progress.build_player_context (тренд +
    слабости игрока из match_reviews); system_prompt_override — личные
    правила пользователя из AIConfig.
    """
    prompt = get_coach_prompt(depth)
    if player_context:
        from .progress import context_to_prompt

        block = context_to_prompt(player_context)
        if block:
            prompt += "\n\n" + block
    if system_prompt_override and system_prompt_override.strip():
        prompt += (
            "\n\nПРАВИЛА ПОЛЬЗОВАТЕЛЯ (личные инструкции, приоритет над общими):\n"
            + system_prompt_override.strip()
        )
    return prompt

_JSON_FENCE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


def analyze_match(match: NormalizedMatch | dict, my_account_id: int | None,
                  client: LLMClient,
                  image_bytes: bytes | None = None,
                  fallback: bool = True,
                  hero_slug: str | None = None,
                  depth: str | None = DEFAULT_DEPTH,
                  player_context: dict | None = None,
                  system_prompt_override: str | None = None) -> CoachReport:
    """Build the compact prompt, call the LLM, validate the schema.

    depth: short | medium | deep (V2.0, дефолт medium) — выбирает вариант
    промпта; player_context — тренд/слабости игрока из ai.progress;
    system_prompt_override — личные правила пользователя.

    Raises LLMError on invalid schema or network failure (when fallback=False);
    falls back to a heuristic local report only when fallback=True.
    """
    dataset = _compact_dataset(match, my_account_id, hero_slug=hero_slug)
    prompt = build_coach_prompt(depth, player_context, system_prompt_override)
    try:
        raw = client.generate_coach_report(
            prompt, dataset, image_bytes=image_bytes
        )
    except LLMError as e:
        if fallback:
            print(f"[coach] LLM unavailable, using heuristic fallback: {e}")
            return heuristic_report(match, my_account_id, hero_slug=hero_slug)
        raise

    return _validate_report(raw)


# Модели-дублёры: если выбранная «остывает» (429) или отдаёт пустой ответ,
# разбор автоматически пробует следующую рабочую — фича не должна падать
# из-за одного залипшего провайдера.
# deepseek-v4-flash — быстрый пул каналов владельца (лог: «куда бодрее идет»).
FALLBACK_MODELS: tuple[str, ...] = (
    "glm-5.3-flash", "deepseek-v4-flash", "glm-5.3", "kimi-k3",
    "GLM-5.3", "GLM-5.3-flash", "Kimi-K3", "MiniMax-M3", "mimo-3",
)


def analyze_match_with_fallback(match: NormalizedMatch | dict,
                                my_account_id: int | None,
                                config,
                                image_bytes: bytes | None = None,
                                hero_slug: str | None = None,
                                depth: str | None = DEFAULT_DEPTH,
                                player_context: dict | None = None,
                                on_attempt=None,
                                on_chunk=None,
                                total_timeout: float | None = None,
                                attempt_timeout: float | None = None,
                                ) -> tuple[CoachReport, str]:
    """Разбор с автосменой модели при «остывании»/сбое.

    Пробует config.model_name, затем модели из FALLBACK_MODELS (кроме
    залипшей). Возвращает (отчёт, имя модели, которой это удалось).
    Исключение — только если не сработала ни одна модель.

    total_timeout — дедлайн на всю цепочку моделей (сек, общий);
    attempt_timeout — потолок одной попытки. Остаток total_timeout
    пробрасывается в сокет-таймаут клиента, поэтому после истечения окна
    запрос к провайдеру реально обрывается, а не продолжает жечь квоту.
    """
    from .client import create_client

    deadline = None if total_timeout is None else time.monotonic() + float(total_timeout)
    tried: list[str] = []
    chain: list[str] = []
    for candidate in (config.model_name, *FALLBACK_MODELS):
        if candidate and candidate not in chain:
            chain.append(candidate)

    last_exc: Exception | None = None
    for model in chain:
        if deadline is not None and time.monotonic() >= deadline:
            last_exc = last_exc or LLMError(
                f"Время разбора истекло ({int(total_timeout)}с) до ответа модели")
            break
        tried.append(model)
        if on_attempt:
            try:
                on_attempt(model, tried)
            except Exception:  # noqa: BLE001 — прогресс не критичен
                pass
        try:
            cfg = config.model_copy(update={"model_name": model})
            client = create_client(cfg)
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if attempt_timeout:
                    remaining = min(remaining, float(attempt_timeout))
                # Сокет не должен переживать дедлайн: max(5.0, ...) давал до
                # 5с лишних после истечения окна — квота жглась в фоне.
                client.request_timeout = max(1.0, remaining)
            if on_chunk:
                report = analyze_match_stream(
                    match, my_account_id, client,
                    image_bytes=image_bytes,
                    on_chunk=on_chunk,
                    hero_slug=hero_slug, depth=depth,
                    player_context=player_context,
                    system_prompt_override=cfg.system_prompt_override,
                    deadline=deadline,
                )
            else:
                report = analyze_match(
                    match, my_account_id, client,
                    image_bytes=image_bytes, fallback=False,
                    hero_slug=hero_slug, depth=depth,
                    player_context=player_context,
                    system_prompt_override=cfg.system_prompt_override,
                )
            return report, model
        except LLMError as e:
            last_exc = e
            continue
        except Exception as e:  # noqa: BLE001 — пробуем следующую модель
            last_exc = e
            continue

    raise LLMError(
        f"Ни одна модель не ответила (пробовали: {', '.join(tried)}). "
        f"Последняя ошибка: {last_exc}"
    )


def analyze_match_stream(match: NormalizedMatch | dict, my_account_id: int | None,
                         client: LLMClient,
                         image_bytes: bytes | None = None,
                         on_chunk=None,
                         hero_slug: str | None = None,
                         depth: str | None = DEFAULT_DEPTH,
                         player_context: dict | None = None,
                         system_prompt_override: str | None = None,
                         deadline: float | None = None) -> CoachReport:
    """Streaming-вариант: on_chunk(received_chars) на каждый chunk.

    depth / player_context / system_prompt_override — как в analyze_match.
    Исключения LLMError пробрасываются в API, чтобы UI выводил реальную
    ошибку нейросети вместо тихого шаблона.

    deadline — монотонное время окончания окна разбора: повторный (не-стрим)
    вызов после истечения дедлайна уже не может уложиться в окно, поэтому он
    не делается — иначе квота тратится дважды.
    """
    dataset = _compact_dataset(match, my_account_id, hero_slug=hero_slug)
    prompt = build_coach_prompt(depth, player_context, system_prompt_override)

    def _clamp_socket() -> None:
        """Остаток окна → сокет-таймаут перед КАЖДЫМ вызовом.

        Без этого фолбэк после долгого стрима получал полный attempt_budget
        заново (двойная оплата времени) и жил дольше дедлайна."""
        if deadline is not None:
            client.request_timeout = min(
                client.request_timeout,
                max(1.0, deadline - time.monotonic()))

    try:
        _clamp_socket()
        raw = client.generate_coach_report_stream(
            prompt, dataset, image_bytes=image_bytes,
            on_chunk=on_chunk,
        )
        return _validate_report(raw)
    except Exception as stream_err:
        if deadline is not None and time.monotonic() >= deadline:
            raise
        try:
            _clamp_socket()
            raw_full = client.generate_coach_report(
                prompt, dataset, image_bytes=image_bytes
            )
            if on_chunk and raw_full:
                on_chunk(len(raw_full))
            return _validate_report(raw_full)
        except Exception:
            raise stream_err


def _clamp_performance_grade(grade: str) -> str:
    """Приводит оценку LLM к валидному алфавиту S+|S|A|B|C.

    LLM иногда присылает 'D', 'F', 'A-' и т.п. Вместо ошибки валидации
    клампим значение: всё хуже C становится C, A-/A+ нормализуются к A/S.
    Значения, не являющиеся осмысленными оценками ('ZZZ', пустая строка,
    'oops'), не нормализуются — валидация потом честно упадёт с ошибкой.
    """
    g = grade.strip().upper()
    if g in {"S+", "S", "A", "B", "C"}:
        return g
    if g in {"S++", "SS", "A+", "A-"}:
        return "A" if g.startswith("A") else "S"
    if g in {"D", "F", "E"}:
        return "C"
    return grade  # не трогаем мусор — пусть валидация даст осмысленную ошибку


def _validate_report(raw: str) -> CoachReport:
    """Общая пост-обработка: strip fence -> JSON -> CoachReport."""
    parsed = _strip_json_fence(raw)
    try:
        data = json.loads(parsed)
    except json.JSONDecodeError as e:
        raise LLMError(f"LLM returned invalid JSON: {e}; body: {parsed[:300]}") from e

    if not isinstance(data, dict):
        raise LLMError(f"LLM returned non-dict JSON ({type(data).__name__})")

    # Автоматическая локализация терминов, персонажей и предметов в тексте
    data = localize_report_dict(data)

    # Авто-нормализация бенчмарков: если LLM прислал 5535 вместо коэффициента 1.11
    if isinstance(data.get("benchmarks"), dict):
        bm = data["benchmarks"]
        s10 = bm.get("souls_at_10")
        if isinstance(s10, (int, float)) and s10 > 10:
            bm["souls_at_10"] = round(float(s10) / 5000.0, 2)
        for k in ("lane_efficiency", "fight_participation"):
            v = bm.get(k)
            if isinstance(v, (int, float)) and v > 10:
                bm[k] = round(float(v) / 100.0, 2)
    elif data.get("benchmarks") is None:
        data["benchmarks"] = {}

    # Авто-нормализация performance_grade и списков:
    grade = data.get("performance_grade")
    if isinstance(grade, str):
        data["performance_grade"] = _clamp_performance_grade(grade)

    concl = data.get("coach_conclusion")
    if isinstance(concl, str):
        data["coach_conclusion"] = [concl]
    elif not isinstance(concl, list):
        data["coach_conclusion"] = []

    concl_list = data["coach_conclusion"]
    if len(concl_list) > 5:
        data["coach_conclusion"] = concl_list[:5]
    elif len(concl_list) < 3:
        fallbacks = [
            "Оптимизируйте темп фарма на линии и добивание душ.",
            "Следите за миникартой и избегайте изолированных смертей.",
            "Адаптируйте закупку предметов под сетап соперников.",
        ]
        for fb in fallbacks:
            if len(data["coach_conclusion"]) >= 3:
                break
            data["coach_conclusion"].append(fb)

    # Нормализация key_mistake: если строка вместо объекта
    if isinstance(data.get("key_mistake"), str):
        data["key_mistake"] = {
            "time": "10:00",
            "location": "Карта",
            "event": data["key_mistake"],
            "why_fatal": "Потеря преимущества по карте",
            "correct_play": "Держаться рядом с союзниками и не рисковать в одиночку",
        }
    elif not isinstance(data.get("key_mistake"), dict):
        data["key_mistake"] = {}

    # Нормализация lane_comparison: если строка, пустой dict или False
    if isinstance(data.get("lane_comparison"), str):
        data["lane_comparison"] = {"verdict": data["lane_comparison"]}
    elif data.get("lane_comparison") is False or data.get("lane_comparison") == {}:
        data["lane_comparison"] = None

    # Нормализация списков (death_breakdown, purchase_timings, teamfights, item_corrections):
    for list_key, text_field in (
        ("death_breakdown", "cause"),
        ("purchase_timings", "item"),
        ("teamfights", "outcome"),
        ("item_corrections", "recommended"),
    ):
        items = data.get(list_key)
        if isinstance(items, list):
            cleaned = []
            for item in items:
                if isinstance(item, str):
                    cleaned.append({text_field: item})
                elif isinstance(item, dict):
                    cleaned.append(item)
            data[list_key] = cleaned
        elif items is not None:
            data[list_key] = []

    try:
        return CoachReport.model_validate(data)
    except ValidationError as e:
        raise LLMError(f"LLM returned invalid schema: {e}") from e


_THINK_TAG = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


def _strip_json_fence(text: str) -> str:
    """Remove ```json ... ``` markdown fences and reasoning blocks an LLM may add."""
    cleaned = _THINK_TAG.sub("", text).strip()
    m = _JSON_FENCE.search(cleaned)
    target = m.group(1).strip() if m else cleaned
    # Если в ответе остался вводный/заключительный текст, находим внешний JSON-объект
    start = target.find("{")
    end = target.rfind("}")
    if start != -1 and end > start:
        return target[start : end + 1]
    return target


def _fmt_mmss(t: int | float) -> str:
    mins, secs = divmod(int(t), 60)
    return f"{mins:02d}:{secs:02d}"


def _hero_ru(slug: str | None) -> str:
    if not slug:
        return "Неизвестный герой"
    try:
        from ..engine.dataset import get_catalog
        h = get_catalog().by_id.get(str(slug).lower())
        if h and h.name:
            return h.name
    except Exception:
        pass
    return localize_text(str(slug))


def _item_ru(item_id: str | None) -> str:
    if not item_id:
        return ""
    try:
        from ..engine.dataset import get_catalog
        it = get_catalog().item_by_id.get(str(item_id).lower())
        if it and it.name:
            return it.name
    except Exception:
        pass
    return localize_text(str(item_id))


# --- compact dataset -------------------------------------------------------


def _resolve_me(match: NormalizedMatch,
                my_account_id: int | None,
                hero_slug: str | None = None) -> NormalizedPlayer | None:
    if my_account_id:
        p = match.player_by_account(my_account_id)
        if p is not None:
            return p
    if hero_slug:
        slug_norm = str(hero_slug).strip().lower()
        for p in match.players:
            if p.hero_id_slug.lower() == slug_norm:
                return p
    if match.players:
        return match.players[0]
    return None


def _compact_dataset(match: NormalizedMatch | dict,
                     my_account_id: int | None,
                     hero_slug: str | None = None) -> dict:
    if isinstance(match, dict):
        return match
    me = _resolve_me(match, my_account_id, hero_slug)

    my_curve: list[dict] = []
    if me is not None:
        curve = [p.t for p in me.networth_curve]
        step = max(1, len(curve) // 20)
        my_curve = [
            {"t": p.t, "nw": p.networth}
            for p in me.networth_curve[::step]
        ]
        if me.networth_curve and my_curve[-1]["t"] != me.networth_curve[-1].t:
            my_curve.append(
                {"t": me.networth_curve[-1].t,
                 "nw": me.networth_curve[-1].networth}
            )

    deaths = []
    items = []
    items_all = []
    souls_10 = 0.0
    if me is not None:
        for pt in me.networth_curve:
            if pt.t <= 600:
                souls_10 = float(pt.networth)

        deaths = [
            {
                "time": _fmt_mmss(d.t),
                "t": d.t,
                "x": round(d.pos_x, 3),
                "y": round(d.pos_y, 3),
                "killer": _hero_ru(d.killer_hero),
            }
            for d in me.deaths_timeline
        ][:10]
        sorted_items = sorted(me.items_timeline, key=lambda i: i.t)
        items = [
            {"time": _fmt_mmss(it.t), "t": it.t, "item": _item_ru(it.item_id), "sold": it.sold}
            for it in sorted_items[-5:]
        ]
        items_all = [
            {"time": _fmt_mmss(it.t), "t": it.t, "item": _item_ru(it.item_id), "sold": it.sold}
            for it in sorted_items[-15:]
        ]

    enemies_raw = [p.hero_id_slug for p in match.players if me is not None and p.team != me.team]
    enemies = [_hero_ru(slug) for slug in enemies_raw]
    allies_raw = [p.hero_id_slug for p in match.players if me is not None and p.team == me.team and p.account_id != me.account_id]
    allies = [_hero_ru(p.hero_id_slug) for p in match.players if me is not None and p.team == me.team and p.account_id != me.account_id]
    pace_info = evaluate_souls_pace(souls_10, me.hero_id_slug if me else None)
    suggested_counters = get_counter_recommendations_for_enemies(enemies_raw)
    lane_opponent = _lane_opponent(match, me)

    return {
        "match_id": match.match_id,
        "duration_s": match.duration_s,
        "duration": _fmt_mmss(match.duration_s),
        "my_hero": me.hero_id_slug if me else None,
        "my_hero_ru": _hero_ru(me.hero_id_slug) if me else None,
        "my_account_id": my_account_id,
        "kda": match.kda(account_id=me.account_id) if me else None,
        "result": match.result_per_player.get(me.account_id) if me else None,
        "level": me.level if me else None,
        "net_worth_final": me.net_worth if me else None,
        "souls_at_10": souls_10,
        "farm_pace_at_10": pace_info,
        "enemy_heroes": enemies_raw,
        "enemy_heroes_ru": enemies,
        "allied_heroes": allies_raw,
        "allied_heroes_ru": allies,
        "suggested_counters": suggested_counters,
        "lane_opponent": lane_opponent,
        "networth_curve_20pts": my_curve[:15],
        "items_top5": items,
        "items_timeline": items_all,
        "deaths": deaths,
        "team_curve_delta": [
            {"t": t, "delta": delta}
            for t, delta in match.team_curve
        ][:15],
        "events": _events(match)[:10],
    }


def _events(match: NormalizedMatch) -> list[dict]:
    events: list[dict] = []
    for mb in match.mid_boss_kills or []:
        t = next((mb.get(k) for k in ("game_time_s", "time_s", "game_time", "time")
                  if isinstance(mb.get(k), (int, float))), None)
        if t is not None:
            events.append({"t": int(t), "event": "midboss"})
    for obj in match.objectives or []:
        raw = json.dumps(obj)
        if "walker" in raw.lower():
            t = next((obj.get(k) for k in ("game_time_s", "time_s", "game_time", "time")
                      if isinstance(obj.get(k), (int, float))), None)
            if t is not None:
                events.append({"t": int(t), "event": "walker"})
    return events


def _lane_opponent(match: NormalizedMatch, me) -> Optional[dict]:
    """Эвристика соперника по линии: враг, чаще всего убивавший игрока.

    В метаданных нет явного назначения линий, поэтому «соперник по линии»
    выводится из тимлайна смертей: убийца с максимальным числом киллов по
    игроку — самый вероятный оппонент линии. Fallback — первый враг в списке.
    """
    if me is None:
        return None
    killer_counts: dict[str, int] = {}
    for d in me.deaths_timeline:
        if d.killer_hero:
            killer_counts[d.killer_hero] = killer_counts.get(d.killer_hero, 0) + 1
    foes = [p for p in match.players if p.team != me.team]
    if not foes:
        return None
    top = max(killer_counts, key=killer_counts.get) if killer_counts else None
    foe = next((p for p in foes if p.hero_id_slug == top), None) if top else None
    if foe is None:
        foe = foes[0]
    souls_10 = 0.0
    for pt in foe.networth_curve:
        if pt.t <= 600:
            souls_10 = float(pt.networth)
    return {
        "hero": _hero_ru(foe.hero_id_slug),
        "kda": f"{foe.kills}/{foe.deaths}/{foe.assists}",
        "souls_at_10": souls_10,
        "net_worth": foe.net_worth,
    }


def compute_match_benchmarks(match: NormalizedMatch, player) -> dict:
    """Вычисляет точные математические бенчмарки матча из телеметрии.

    - souls_at_10: отношение набранных душ к 10:00 к эталону роли (например, 1.32 = 132%).
    - lane_efficiency: эффективность фарма линии в фазе лайнинга (0..10 мин) относительно эталона 4200 душ.
    - fight_participation: реальная доля участия в командных убийствах (K+A)/TeamKills.
    """
    if player is None:
        return {
            "souls_at_10": 1.0,
            "lane_efficiency": 0.8,
            "fight_participation": 0.5,
            "actual_souls": 4000,
            "target_souls": 4000,
            "team_kills": 0,
            "player_ka": 0,
            "early_deaths": 0,
        }

    souls_10 = 0.0
    for pt in player.networth_curve:
        if pt.t <= 600:
            souls_10 = float(pt.networth)

    pace = evaluate_souls_pace(souls_10, player.hero_id_slug)
    target = pace.get("target_souls", 4200)
    souls_ratio = round(souls_10 / target, 2) if target > 0 else 1.0

    lane_eff = round(min(1.2, max(0.2, souls_10 / 4200.0)), 2)

    team_kills = sum(p.kills for p in match.players if p.team == player.team)
    player_ka = player.kills + player.assists
    participation = round(player_ka / max(1, team_kills), 2)

    # V2.0 §4: смерти до 10-й минуты — база сравнения «с собой на герое».
    early_deaths = sum(1 for d in player.deaths_timeline if d.t <= 600)

    return {
        "souls_at_10": souls_ratio,
        "lane_efficiency": lane_eff,
        "fight_participation": participation,
        "actual_souls": int(souls_10),
        "target_souls": int(target),
        "team_kills": int(team_kills),
        "player_ka": int(player_ka),
        "early_deaths": int(early_deaths),
    }


# --- heuristic fallback ----------------------------------------------------


def heuristic_report(match: NormalizedMatch | dict,
                     my_account_id: int | None,
                     hero_slug: str | None = None) -> CoachReport:
    """Local report without LLM: grade from kda+networth, mistake from the
    longest death streak / biggest advantage swing, conclusions with real
    numbers, benchmarks from stats."""
    if isinstance(match, dict):
        return _heuristic_from_dict(match)

    me = _resolve_me(match, my_account_id, hero_slug)
    if me is None:
        return _empty_report()

    peers = [p for p in match.players if p is not me]
    ranks = sorted([p.net_worth for p in match.players], reverse=True)
    nw_rank = ranks.index(me.net_worth) + 1 if me.net_worth in ranks else len(ranks)
    avg_peer_nw = (sum(p.net_worth for p in peers) / len(peers)) if peers else 0.0
    kda_ratio = (me.kills + me.assists) / me.deaths if me.deaths else me.kills + me.assists

    won = match.result_per_player.get(me.account_id, False)

    grade = "C"
    score = 0.0
    score += min(kda_ratio, 6.0) / 6.0 * 40        # kda weight 40
    score += (me.net_worth / avg_peer_nw if avg_peer_nw else 1.0) * 30 / 1.5
    score += 20.0 if won else 5.0
    score += 10.0 * (1 - (nw_rank - 1) / max(1, len(match.players) - 1))
    if score >= 85:
        grade = "S+"
    elif score >= 70:
        grade = "S"
    elif score >= 55:
        grade = "A"
    elif score >= 40:
        grade = "B"

    # Key mistake: longest death streak; fallback to biggest delta swing.
    mistake = _mistake_from_deaths(match, me)
    if mistake is None:
        mistake = _mistake_from_swing(match, me, won)

    team_kills = sum(p.kills for p in match.players if p.team == me.team)
    participation = (
        (me.kills + me.assists) / team_kills if team_kills else 0.0
    )

    bm = compute_match_benchmarks(match, me)

    rep = CoachReport(
        performance_grade=grade,
        key_mistake=mistake,
        coach_conclusion=[
            f"KDA {me.kills}/{me.deaths}/{me.assists}, networth {me.net_worth} "
            f"({nw_rank}-е место из {len(match.players)}).",
            f"Участие в файтах: {participation:.0%} командных киллов "
            f"({me.kills + me.assists} из {team_kills}).",
            f"Смертей за матч: {me.deaths}. Каждая смерть после 20-й минуты "
            f"стоит темпа и душ — режь серии из 2+ смертей подряд.",
        ],
        benchmarks=Benchmarks(
            souls_at_10=bm["souls_at_10"],
            lane_efficiency=bm["lane_efficiency"],
            fight_participation=bm["fight_participation"],
        ),
        item_corrections=[],
        teamfights=_heuristic_teamfights(match, me),
        purchase_timings=_heuristic_purchase_timings(me),
    )
    return CoachReport.model_validate(localize_report_dict(rep.model_dump()))



def _heuristic_teamfights(match: NormalizedMatch, me) -> list[TeamfightSummary]:
    """Top teamfights from raw death data: 3+ deaths of any players in a 60s
    window; verdict by which team lost more people."""
    my_team = me.team
    all_deaths: list[tuple[int, int]] = [
        (d.t, p.team) for p in match.players for d in p.deaths_timeline
    ]
    all_deaths.sort()
    fights: list[tuple[int, int, int]] = []  # (t_mid, my_team_deaths, foe_deaths)
    i = 0
    while i < len(all_deaths):
        j = i
        while j + 1 < len(all_deaths) and all_deaths[j + 1][0] - all_deaths[i][0] <= 60:
            j += 1
        window = all_deaths[i:j + 1]
        if len(window) >= 3:
            my_deaths = sum(1 for _, team in window if team == my_team)
            foe_deaths = len(window) - my_deaths
            fights.append((window[len(window) // 2][0], my_deaths, foe_deaths))
        i = j + 1
    # top-3 biggest fights by total participants
    fights.sort(key=lambda f: f[1] + f[2], reverse=True)
    out: list[TeamfightSummary] = []
    for t_mid, my_d, foe_d in fights[:3]:
        if my_d < foe_d:
            outcome = (f"Размен выигран: потеряли {my_d}, забрали {foe_d}. "
                       f"Численный перевес конвертирован.")
            lesson = "Повторяй: входи в файт только с численным преимуществом."
        elif my_d > foe_d:
            outcome = (f"Размен проигран: потеряли {my_d}, забрали {foe_d}. "
                       f"Дали врагу золото и темп.")
            lesson = ("Проверяй состав перед входом: живы ли союзники, готовы ли ключевые ультимейты, "
                      "не заходи неполной командой.")
        else:
            outcome = f"Размен равный ({my_d} на {foe_d}) — темп не получил никто."
            lesson = "Равные размены оправданы только при защите объекта."
        out.append(TeamfightSummary(time=_fmt_mmss(t_mid), outcome=outcome, lesson=lesson))
    return out


def _heuristic_purchase_timings(me) -> list[PurchaseTiming]:
    """Last-4 purchases with a crude timing verdict: an item bought while the
    player's networth barely grows afterwards is flagged late."""
    items = sorted(me.items_timeline, key=lambda i: i.t)[-4:]
    out: list[PurchaseTiming] = []
    for it in items:
        if it.sold:
            verdict, comment = "рано", "Предмет продан вскоре после покупки — план покупки был неверным."
        else:
            verdict, comment = "вовремя", "Покупка без последующей продажи."
        out.append(PurchaseTiming(
            time=_fmt_mmss(it.t), item=_item_ru(str(it.item_id)), verdict=verdict, comment=comment,
        ))
    return out


def _mistake_from_deaths(match: NormalizedMatch, me) -> Optional[KeyMistake]:
    if not me.deaths_timeline:
        return None
    streak: list = []
    best: list = []
    for d in me.deaths_timeline:
        if streak and d.t - streak[-1].t <= 90:
            streak.append(d)
        else:
            streak = [d]
        if len(streak) > len(best):
            best = list(streak)
    if not best:
        return None
    d = best[0]
    mins, secs = divmod(d.t, 60)
    return KeyMistake(
        time=f"{mins:02d}:{secs:02d}",        location=f"карта ({d.pos_x:.2f}, {d.pos_y:.2f})",
        event=(
            f"Серия из {len(best)} смертей подряд (окно "
            f"{(best[-1].t - best[0].t) // 60} мин"
            + (f", убийца {_hero_ru(best[0].killer_hero)}" if best[0].killer_hero else "")
            + ")"
        ),
        why_fatal=(
            f"{len(best)} смертей без отыгрыша = потерянные души и темп: "
            f"противник конвертирует преимущество в карту."
        ),
        correct_play=(
            "После первой смерти в серии — отступай, фармь безопасную волну, "
            "жди возрождения союзников и отката способностей, а не входи в тот же размен."
        ),
    )


def _mistake_from_swing(match: NormalizedMatch, me, won: bool) -> KeyMistake:
    peak_t, peak = 0, 0
    trough_t, trough = 0, 0
    sign = 1 if me.team == 0 else -1
    for t, delta in match.team_curve:
        adj = delta * sign
        if adj > peak:
            peak_t, peak = t, adj
        if adj < trough:
            trough_t, trough = t, adj
    mins, secs = divmod(peak_t, 60)
    return KeyMistake(
        time=f"{mins:02d}:{secs:02d}",
        location="карта",
        event=(
            f"Максимальное преимущество команды {peak} душ на {mins} мин "
            f"конвертировано {'в поражение' if not won else 'с трудом в победу'}."
        ),
        why_fatal="Пойманное преимущество не было закрыто объектами и пушем.",
        correct_play="После перевеса 10к+ душ — группируйся, бери Шагоход/Мидбосса и запирай карту.",
    )


def _heuristic_from_dict(match: dict) -> CoachReport:
    kda = str(match.get("kda") or "0/0/0")
    k, d, a = (kda.split("/") + ["0", "0", "0"])[:3]
    return CoachReport(
        performance_grade="C",
        key_mistake=KeyMistake(
            time="00:00", location="нет данных", event="нет данных",
            why_fatal="нет данных", correct_play="нет данных",
        ),
        coach_conclusion=[
            f"KDA {kda}: убивай {int(k or 0) + 1}, умирай {max(0, int(d or 0) - 1)}.",
            "Данных для полного разбора мало — подключи API-ключ.",
            "Проверь тайминги предметов против вражеских кор-предметов.",
        ],
        benchmarks=Benchmarks(),
        item_corrections=[],
    )


def _empty_report() -> CoachReport:
    return CoachReport(
        performance_grade="C",
        key_mistake=KeyMistake(
            time="00:00", location="нет данных", event="нет данных",
            why_fatal="нет данных", correct_play="нет данных",
        ),
        coach_conclusion=[
            "В матче нет игроков — данные неполные.",
            "Проверь корректность match_id.",
            "Повтори разбор после обновления данных.",
        ],
        benchmarks=Benchmarks(),
        item_corrections=[],
    )


DRAFT_SCREENSHOT_PROMPT = """\
Ты — элитный аналитик Deadlock (High MMR / Eternus).
Перед тобой скриншот экрана Deadlock (экран загрузки матча 6v6, выбор героев или внутриигровая таблица счета/TAB).

Твоя задача:
1. Определить состав команд:
   - "my_team": список ID героев вашей/левой/союзной команды (до 6 героев).
   - "enemy_team": список ID героев команды противника/правой команды (до 6 героев).
   - "my_hero": ID героя, за которого играет пользователь (если выделен/виден ник/рамка, иначе null или первый герой).
2. Дать тактические рекомендации по закупке и ранней игре:
   - "core_counters": список предметов, критически необходимых против сетапа врага (строго на русском языке, с объяснением кого они контрят).
   - "lane_tips": 2-3 совета по лейнингу и начальной стадии.
   - "threats": главные угрозы вражеского пика.

Используй стандартные идентификаторы героев Deadlock (id):
abrams, bebop, dynamo, grey_talon, haze, infernus, ivy, kelvin, lady_geist, lash, mcginnis, mo_and_krill, paradox, pocket, seven, shiv, vindicta, viscous, warden, wraith, yamato, mirage, sinclair, victor, mina, wanderer, venator, paige, calico, billie, graves, apollo, dream, silver, celesta, rat_king, busya.

Ответь СТРОГО валидным JSON-объектом следующего формата:
{
  "my_team": ["haze", "abrams", ...],
  "enemy_team": ["seven", "kelvin", ...],
  "my_hero": "haze",
  "tactics": {
    "summary": "Краткая характеристика матчапа 6v6 (1-2 предложения)",
    "core_counters": [
      {"item": "Гроза целителей", "targets": "Кельвин, Абрамс", "reason": "Срезает лечение на 60%"}
    ],
    "lane_tips": [
      "Совет 1 по игре на линии...",
      "Совет 2..."
    ],
    "threats": [
      "Угроза 1..."
    ]
  }
}
"""


def analyze_draft_screenshot(image_bytes: bytes, client: LLMClient) -> dict:
    """Send draft screenshot to multimodal LLM to parse 6v6 rosters and tactical tips."""
    raw = client.generate_coach_report(DRAFT_SCREENSHOT_PROMPT, {}, image_bytes=image_bytes)
    # Parse JSON from response
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        # Try to locate json object in text
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if match:
            data = json.loads(match.group(0))
        else:
            raise ValueError(f"AI returned invalid JSON: {raw[:300]}")

    return data
