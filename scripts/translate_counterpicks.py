"""Перевод source_en.json на русский с официальной терминологией Deadlock.

Метод:
  1. в системный промпт кладётся полный словарь официальных русских названий
     (герои, предметы, способности из deadlock-api) — модель обязана
     использовать ровно их, склоняя по правилам русского языка;
  2. каждая строка проверяется автоматически: латинское название не должно
     остаться в тексте, а для названий из исходной строки обязано появиться
     русское соответствие (с допуском на падежное окончание);
  3. брак отправляется на переспрос с явным перечнем требуемых названий;
     окончательно неисправленные строки попадают в отчёт, а не в датасет;
  4. повторяющиеся строки кэшируются, поэтому одинаковые формулировки
     в 44 гайдах переводятся один раз и звучат одинаково.

Использование:
    python scripts/translate_counterpicks.py               # всё
    python scripts/translate_counterpicks.py --only haze   # один герой
    python scripts/translate_counterpicks.py --items       # только предметы
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "data" / "counterpicks"
SOURCE_JSON = OUT_DIR / "source_en.json"
GLOSSARY_JSON = OUT_DIR / "glossary.json"
RU_DIR = OUT_DIR / "ru"
CACHE_JSON = OUT_DIR / "translation_cache.json"
REVIEW_JSON = OUT_DIR / "review_needed.json"

BASE_URL = "http://127.0.0.1:8089/v1"
MODEL = "glm-5.3-flash"
BATCH_SIZE = 24
MAX_ATTEMPTS = 4
MAX_REPAIRS = 2
TIMEOUT = 600
MAX_TOKENS = 32000

# Поля-идентификаторы: их значения не переводятся.
SKIP_KEYS = {
    "id", "name", "name_en", "portrait", "key", "icon", "image", "category",
    "tier", "cost", "avg_minutes", "item", "hero", "related", "related_guides",
    "groups", "group_tags", "stage_of", "top", "alt", "problem_index", "version",
}

# Повторяющиеся ярлыки интерфейса переводим фиксированно, чтобы все 44 гайда
# звучали одинаково, а не как 44 независимых перевода.
LABELS_RU = {
    # ответы на угрозу
    "Situational response": "Ситуационный ответ",
    "Larger investment": "Крупная инвестиция",
    "Lane option": "Опция для линии",
    "Priority situation answer": "Приоритетный ответ",
    "Situational counter option": "Ситуационный контр-вариант",
    "Spell-trade option": "Вариант для обмена заклинаниями",
    "Alternative": "Альтернатива",
    # фазы
    "Not available": "Недоступно",
    "Early game · 0–10 min": "Ранняя игра · 0–10 мин",
    "Mid game · 10–20 min": "Середина игры · 10–20 мин",
    "Late game · 20+ min": "Поздняя игра · 20+ мин",
    # панель быстрого ответа
    "Main threat": "Главная угроза",
    "Your opening": "Ваше окно",
    "Buy priority": "Приоритет покупки",
    # архетипы героев
    "Gun Carry (M1 Carry)": "Пулевой керри (M1)",
    "Spirit Carry": "Магический керри",
    "Frontline": "Фронтлайн",
    "Pick": "Пик-охотник",
    "Support": "Саппорт",
    "Split-Push": "Сплитпуш",
    "Split Push": "Сплитпуш",
    # угрозы
    "Burst": "Взрывной урон",
    "Heavy CC": "Тяжёлый контроль",
    "Mobility": "Мобильность",
    "Sustain": "Живучесть",
    "Poke": "Покер",
    "Lockdown": "Удержание",
    "Stealth": "Невидимость",
    "Tank": "Танк",
    "Lane": "Линия",
    # роли предметов
    "Gun Defense": "Защита от пуль",
    "Spirit Defense": "Защита от магии",
    "Anti-Heal": "Анти-хил",
    "Anti-CC": "Анти-контроль",
    "Anti-Mobility": "Анти-мобильность",
    "Debuff Cleanse": "Снятие дебаффов",
    "Fire Rate Control": "Контроль скорости стрельбы",
    "Interrupt": "Прерывание",
    "Lane Sustain": "Устойчивость на линии",
    "Damage": "Урон",
    "Utility": "Утилита",
    # частотные подписи предметных карточек
    "Anti-heal · Spirit application": "Анти-хил · магическое наложение",
    "Anti-heal · Weapon application": "Анти-хил · пулевое наложение",
    "Silence · Weapon application": "Безмолвие · пулевое наложение",
    "Early / mid-game option": "Опция ранней/средней игры",
    "Damage after a catch": "Урон после захвата",
    "Repositioning and escape": "Репозиционирование и побег",
    "Movement": "Передвижение",
    "Targeted anti-heal": "Точечный анти-хил",
    "Weapon Pressure": "Оружейное давление",
    "Control Defense": "Защита от контроля",
    "Health Pressure": "Давление по здоровью",
    "Mobility Control": "Контроль мобильности",
    "Summon Clear": "Зачистка призывов",
    "Lane Recovery": "Восстановление на линии",
    "Melee pressure · Lane option": "Давление в ближнем бою · опция линии",
    "Ally Save": "Спасение союзника",
    "Cast Denial": "Запрет применения",
    "Disarm": "Обезоруживание",
    "Reactive Defense": "Реактивная защита",
    "Anti-burst cleanse": "Снятие под взрывной урон",
    "Anti-carry disruption": "Срыв керри",
    "Anti-kiting": "Анти-кайтинг",
    "Anti-poke survival": "Выживание против покера",
    "Anti-sniper positioning": "Позиция против снайпера",
    "Anti-snowball": "Анти-снежок",
    "Area Control": "Контроль зоны",
    "Group CC": "Массовый контроль",
    "Kiting": "Кайтинг",
    "Cleanse": "Снятие дебаффов",
    "Dive": "Дайв",
    "Spirit Pressure": "Магическое давление",
    "Early game": "Ранняя игра",
    "Mid game": "Середина игры",
    "Late Game": "Поздняя игра",
    # названия групп фильтров (source_en.json -> groups[].name)
    "Anti-CC & Combo Breaking": "Анти-контроль и срыв комбо",
    "Interrupt & Cast Denial": "Прерывание и запрет применения",
}

# Строки вида "28.1 min" — данные таймингов, а не текст.
MINUTES_RE = re.compile(r"^(\d+(?:[.,]\d+)?)\s*min$")

# Названия короче этого порога не проверяем «на обязательное присутствие»:
# короткие способности вроде Dash или Combo — обычные английские слова,
# и требование их присутствия давало бы ложные срабатывания.
ENFORCE_MIN_LEN = 8

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

_print_lock = threading.Lock()
_cache_lock = threading.Lock()

# --- глоссарий -------------------------------------------------------------


def build_terms(glossary: dict) -> tuple[dict[str, str], dict[str, str]]:
    """Возвращает (все_термины, обязательные_термины) в виде EN -> RU."""
    all_terms: dict[str, str] = {}
    for bucket in ("heroes", "items", "abilities"):
        for en, ru in glossary.get(bucket, {}).items():
            if len(en) > 3 and en != ru:
                all_terms[en] = ru
    enforced = {
        en: ru
        for en, ru in all_terms.items()
        if len(en) >= ENFORCE_MIN_LEN or " " in en
    }
    return all_terms, enforced


def compile_term_re(terms: dict[str, str]) -> re.Pattern:
    keys = sorted(terms, key=len, reverse=True)
    return re.compile(r"(?<![A-Za-z0-9])(" + "|".join(re.escape(k) for k in keys) + r")(?![A-Za-z0-9])")


def mentions(text: str, ru_name: str) -> bool:
    """Встречается ли русское название в тексте хотя бы в косвенном падеже.

    Русские падежи меняют конец слова («Танец» → «Танца», «Латная» →
    «Латную»), а беглая гласная выпадает целиком («Коготь» → «Когтя»),
    поэтому опорное слово названия усекается справа до 3 букв — порог
    подобран так, чтобы родительный падеж проходил, а обычные слова нет.
    Опорное слово — самое длинное в названии: у «Мо и Мелкий» первое слово
    состоит из двух букв и совпасть не может ни в каком падеже.
    """
    words = [w for w in ru_name.split() if w]
    if not words:
        return False
    if ru_name in text:
        return True
    low = text.lower()
    target = max(words, key=len).lower()
    for length in range(len(target), 2, -1):
        if target[:length] in low:
            return True
    return False


# --- обход структуры -------------------------------------------------------


def collect_paths(payload, path: tuple = ()) -> list[tuple]:
    """Пути ко всем строковым листьям, которые нужно переводить.

    Проверяются ВСЕ ключи пути: значения вида {"auto-cleanse": "late"}
    внутри stage_of и элементы списков вроде ["metal-skin", ...] —
    это идентификаторы, а не текст.
    """
    out: list[tuple] = []
    if isinstance(payload, str):
        if payload.strip() and not any(isinstance(s, str) and s in SKIP_KEYS for s in path):
            out.append(path)
    elif isinstance(payload, list):
        for i, item in enumerate(payload):
            out.extend(collect_paths(item, path + (i,)))
    elif isinstance(payload, dict):
        for key, value in payload.items():
            out.extend(collect_paths(value, path + (key,)))
    return out


def get_at(payload, path: tuple):
    for step in path:
        payload = payload[step]
    return payload


def set_at(payload, path: tuple, value) -> None:
    *head, last = path
    cursor = payload
    for step in head:
        cursor = cursor[step]
    cursor[last] = value


# --- клиент LLM ------------------------------------------------------------


def build_system_prompt(terms: dict[str, str], subtitle: str = "") -> str:
    """Системный промпт с глоссарием.

    Глоссарий даётся на батч, а не на весь датасет: длинный словарь сам по
    себе провоцирует долгие рассуждения модели, которые съедают лимит вывода
    и оставляют ответ пустым.
    """
    pairs = "; ".join(f"{en} = {ru}" for en, ru in sorted(terms.items()))
    return (
        "Ты профессиональный переводчик гайдов по игре Deadlock на русский язык. "
        "Переводишь точно и живо, без канцелярита, в игровом регистре. "
        "Обращение к игроку — «ты», как в русской локализации. "
        "Сохраняй разметку **жирный текст** и разделитель ·. "
        "Работай быстро: не рассуждай, сразу выдавай перевод. "
        "Ниже ТОЧНЫЙ словарь официальных русских названий. Используй ровно эти "
        "названия и никогда не заменяй их другими словами или латиницей; "
        "при необходимости ставь их в нужный падеж и число, изменяя только "
        "окончания. Если оригинала нет в словаре, переводи смыслом, "
        "не оставляя английских слов."
        + (f" {subtitle}" if subtitle else "")
        + "\n\nСЛОВАРЬ: " + pairs
    )


def terms_in(texts: list[str], terms: dict[str, str], pattern: re.Pattern) -> dict[str, str]:
    """Подмножество глоссария, реально встречающееся в строках батча."""
    found: dict[str, str] = {}
    for text in texts:
        for match in pattern.finditer(text):
            name = match.group(1)
            if name in terms:
                found[name] = terms[name]
    return found


def chat(messages: list[dict], timeout: int = TIMEOUT) -> str:
    # max_tokens задан явно: без него роутер обрывает батч на середине JSON.
    body = {
        "model": MODEL,
        "messages": messages,
        "temperature": 0.2,
        "max_tokens": MAX_TOKENS,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    req = urllib.request.Request(
        BASE_URL.rstrip("/") + "/chat/completions",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    return payload["choices"][0]["message"]["content"]


def parse_json_array(raw: str) -> list[str] | None:
    text = raw.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end == -1:
        return None
    try:
        parsed = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    if isinstance(parsed, list) and all(isinstance(x, str) for x in parsed):
        return parsed
    return None


def ask(prompt: str, system: str) -> list[str]:
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            raw = chat([{"role": "system", "content": system}, {"role": "user", "content": prompt}])
            parsed = parse_json_array(raw)
            if parsed is not None:
                return parsed
        except (urllib.error.URLError, TimeoutError, OSError, KeyError, ValueError):
            pass
        time.sleep(2 * attempt)
    return []


def translate_batch(items: list[str], system: str) -> list[str]:
    prompt = (
        f"Переведи на русский каждую строку этого JSON-массива ({len(items)} шт). "
        f"Названия из словаря — ровно как в словаре. "
        f"Верни JSON-массив ровно из {len(items)} строк в том же порядке, "
        f"без пояснений.\n\n" + json.dumps(items, ensure_ascii=False)
    )
    return ask(prompt, system)


def translate_batch_sized(items: list[str], terms: dict[str, str], pattern: re.Pattern,
                          subtitle: str) -> list[str]:
    """Перевод батча, в системный промпт идут только нужные термины."""
    if not items:
        return []
    needed = terms_in(items, terms, pattern)
    system = build_system_prompt(needed or terms, subtitle)
    out = translate_batch(items, system)
    if len(out) == len(items):
        return out
    # Половина батча: длинные батчи чаще упираются в лимит вывода.
    half = len(items) // 2 or 1
    log(f"  батч {len(items)} не прошёл — делю на {half}+{len(items) - half}")
    return translate_batch_sized(items[:half], terms, pattern, subtitle) + \
        translate_batch_sized(items[half:], terms, pattern, subtitle)


def repair_batch(pairs: list[tuple[str, list[tuple[str, str]]]], system: str) -> list[str]:
    """Пакетный переспрос: список (исходник, требуемые названия) за один вызов."""
    if not pairs:
        return []
    lines = []
    for index, (item, required) in enumerate(pairs, start=1):
        listing = ", ".join(f"{en} -> {ru}" for en, ru in required) or "нет обязательных названий"
        lines.append(
            f"{index}. Исходник: {json.dumps(item, ensure_ascii=False)}\n"
            f"   Обязательно используй ровно эти названия (с нужным падежом): {listing}"
        )
    prompt = (
        f"Переведи на русский каждый из {len(pairs)} фрагментов. В каждом переводе "
        "обязательно используй указанные названия ровно в таком виде, склоняя только "
        "окончания. Никаких английских слов в ответе. "
        f"Верни JSON-массив ровно из {len(pairs)} строк в том же порядке.\n\n" + "\n".join(lines)
    )
    out = ask(prompt, system)
    return out if len(out) == len(pairs) else []


# --- контроль качества -----------------------------------------------------


def check(source: str, translated: str, enforced_re: re.Pattern, enforced: dict[str, str],
          all_re: re.Pattern) -> list[str]:
    """Список проблем перевода: остатки латиницы и потерянные названия."""
    problems: list[str] = []
    leftovers = {m.group(1) for m in all_re.finditer(translated)}
    if leftovers:
        problems.append("латиница: " + ", ".join(sorted(leftovers)))
    required = {(m.group(1), enforced[m.group(1)]) for m in enforced_re.finditer(source)}
    missing = [en for en, ru in sorted(required) if not mentions(translated, ru)]
    if missing:
        problems.append("потеряны названия: " + ", ".join(missing))
    return problems


# --- проходы ---------------------------------------------------------------


def load_cache() -> dict[str, str]:
    return json.loads(CACHE_JSON.read_text(encoding="utf-8")) if CACHE_JSON.is_file() else {}


def save_cache(cache: dict[str, str]) -> None:
    with _cache_lock:
        CACHE_JSON.write_text(
            json.dumps(cache, ensure_ascii=False, indent=0) + "\n", encoding="utf-8"
        )


def write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    tmp.replace(path)


def log(msg: str) -> None:
    with _print_lock:
        print(msg, flush=True)


class Translator:
    def __init__(self, glossary: dict, workers: int, offline: bool = False):
        self.all_terms, self.enforced = build_terms(glossary)
        self.all_re = compile_term_re(self.all_terms)
        self.enforced_re = compile_term_re(self.enforced)
        self.workers = workers
        self.cache = load_cache()
        self.review: dict[str, dict] = {}
        self.offline = offline
        self.missing: list[str] = []

    def _system_for(self, items: list[str]) -> str:
        """Промпт с глоссарием только по встреченным в батче названиям."""
        needed = terms_in(items, self.all_terms, self.all_re)
        return build_system_prompt(needed or self.all_terms)

    def _work_batch(self, batch: list[str], label: str) -> int:
        """Перевод батча с пакетным ремонтом: на батч максимум 1 ремонтный вызов."""
        system = self._system_for(batch)
        out = translate_batch_sized(batch, self.all_terms, self.all_re, "")
        if len(out) != len(batch):
            log(f"[{label}] батч {len(batch)}: ответ {len(out)} строк — уточняю поштучно")
            out = []
            for single in batch:
                one = ask(
                    "Переведи на русский одну строку. Названия — из словаря. "
                    "Верни JSON-массив из одной строки.\n\n"
                    + json.dumps([single], ensure_ascii=False),
                    system,
                )
                out.append(one[0] if one else "")

        for _ in range(MAX_REPAIRS):
            broken: list[tuple[int, str, list[tuple[str, str]]]] = []
            for index, (source, translated) in enumerate(zip(batch, out)):
                if not translated:
                    continue
                if check(source, translated, self.enforced_re, self.enforced, self.all_re):
                    required = [
                        (m.group(1), self.enforced[m.group(1)])
                        for m in self.enforced_re.finditer(source)
                        if not mentions(translated, self.enforced[m.group(1)])
                    ]
                    broken.append((index, source, required))
            if not broken:
                break
            repaired = repair_batch([(item, req) for _, item, req in broken], system)
            if not repaired:
                break
            for (index, _, _), fixed in zip(broken, repaired):
                out[index] = fixed

        ok = 0
        for source, translated in zip(batch, out):
            if not translated:
                continue
            problems = check(source, translated, self.enforced_re, self.enforced, self.all_re)
            if problems:
                self.review[source] = {"translation": translated, "problems": problems}
            with _cache_lock:
                self.cache[source] = translated
            ok += 1
        return ok

    def translate_scope(self, payload, label: str):
        paths = collect_paths(payload)
        known = [get_at(payload, p) for p in paths]
        todo = sorted({s for s in known if s not in self.cache and not self._is_fixed(s)})
        log(f"[{label}] листьев {len(paths)}, уникальных {len(set(known))}, к переводу {len(todo)}")
        if self.offline:
            if todo:
                self.missing.extend(todo)
            for path in paths:
                raw = get_at(payload, path)
                set_at(payload, path, LABELS_RU.get(raw, self.cache.get(raw, raw)))
            return payload

        batches = [todo[i : i + BATCH_SIZE] for i in range(0, len(todo), BATCH_SIZE)]
        if batches:
            started = time.time()
            with ThreadPoolExecutor(max_workers=self.workers) as pool:
                futures = [pool.submit(self._work_batch, b, label) for b in batches]
                done = 0
                for future in futures:
                    done += future.result()
                    log(f"[{label}] {done}/{len(todo)} строк, {time.time() - started:.0f} с")
            save_cache(self.cache)

        for path in paths:
            raw = get_at(payload, path)
            set_at(payload, path, LABELS_RU.get(raw, self.cache.get(raw, raw)))
        return payload

    @staticmethod
    def _is_fixed(raw: str) -> bool:
        """Строка уже имеет фиксированный перевод и в модель не идёт."""
        if raw in LABELS_RU:
            return True
        match = MINUTES_RE.match(raw.strip())
        if match:
            LABELS_RU[raw] = f"{match.group(1).replace('.', ',')} мин"
            return True
        return False


def unresolved(payload) -> list[str]:
    """Строки, которые так и не получили перевод."""
    latin = compile_term_re({"placeholder": ""})
    out: list[str] = []
    for path in collect_paths(payload):
        text = get_at(payload, path)
        if re.search(r"[A-Za-z]{4,}", text) and text.strip() not in LABELS_RU:
            out.append(f"{path}: {text[:90]}")
    return out


WORKLIST_JSON = OUT_DIR / "_to_translate.json"


def export_worklist(translator: "Translator") -> None:
    """Выгрузить плоский список непереведённых строк и нужный глоссарий.

    Переводить нужно уникальные строки, а не 44 файла: одна и та же формулировка
    («Check for active Unstoppable before depending on the delayed stun») повторяется
    в десятках героев. После внешнего перевода результаты складываются в
    translation_cache.json — дальше --assemble работает как обычно.
    """
    source = json.loads(SOURCE_JSON.read_text(encoding="utf-8"))
    unique: set[str] = set()
    for scope in (source["heroes"], source["items"]):
        for chunk in scope.values():
            for path in collect_paths(chunk):
                value = get_at(chunk, path)
                if value.strip() and not translator._is_fixed(value):
                    unique.add(value)
    todo = sorted(s for s in unique if s not in translator.cache)

    (OUT_DIR / "_to_translate.json").write_text(
        json.dumps(todo, ensure_ascii=False, indent=0) + "\n", encoding="utf-8"
    )
    used: dict[str, str] = {}
    pattern = compile_term_re(translator.all_terms)
    for text in todo:
        for match in pattern.finditer(text):
            used[match.group(1)] = translator.all_terms[match.group(1)]
    (OUT_DIR / "_glossary_todo.json").write_text(
        json.dumps(used, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    log(f"К переводу: {len(todo)} строк -> {WORKLIST_JSON.name}, "
        f"терминов в них: {len(used)} -> _glossary_todo.json")
    log("Слова в остатке: ~" + str(sum(len(s.split()) for s in todo)))


def import_translations(translator: "Translator") -> int:
    """Принять внешний перевод: плоский словарь {английская строка: русская}.

    Файл translation_cache.json дополняется принятыми парами. Строки проверяются
    тем же контролем качества, что и машинный перевод: остатки латиницы в
    названиях и потерянные термины попадают в отчёт, а не в датасет.
    """
    if not CACHE_JSON.is_file():
        raise FileNotFoundError(
            f"Нет {CACHE_JSON.name}: положите туда словарь "
            '{"English line": "Русская строка", ...}'
        )
    incoming = json.loads(CACHE_JSON.read_text(encoding="utf-8"))
    if not isinstance(incoming, dict):
        raise ValueError("translation_cache.json должен быть объектом {EN: RU}.")

    accepted = 0
    for source_text, translated in incoming.items():
        if not isinstance(translated, str) or not translated.strip():
            continue
        problems = check(
            source_text, translated, translator.enforced_re, translator.enforced, translator.all_re
        )
        if problems:
            translator.review[source_text] = {"translation": translated, "problems": problems}
            continue
        translator.cache[source_text] = translated
        accepted += 1
    save_cache(translator.cache)
    if translator.review:
        write_json(REVIEW_JSON, translator.review)
    log(f"Принято переводов: {accepted} из {len(incoming)}; на доработку: {len(translator.review)}")
    return accepted


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", action="append", help="только указанные герои (id проекта)")
    parser.add_argument("--items", action="store_true", help="только предметы")
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--export-worklist", action="store_true",
                        help="выгрузить непереведённые строки в _to_translate.json")
    parser.add_argument("--import", dest="import_file", action="store_true",
                        help="принять внешний перевод из translation_cache.json")
    parser.add_argument("--offline", action="store_true",
                        help="собирать чанки только из готового кэша, без вызовов модели")
    args = parser.parse_args()

    source = json.loads(SOURCE_JSON.read_text(encoding="utf-8"))
    glossary = json.loads(GLOSSARY_JSON.read_text(encoding="utf-8"))
    translator = Translator(glossary, args.workers, offline=args.offline)
    log(f"Терминов: {len(translator.all_terms)} (обязательных {len(translator.enforced)}) | "
        f"кэш: {len(translator.cache)} строк")

    if args.export_worklist:
        export_worklist(translator)
        return 0
    if args.import_file:
        return 0 if import_translations(translator) else 1

    RU_DIR.mkdir(parents=True, exist_ok=True)
    started = time.time()

    if not args.items:
        hero_ids = [h for h in source["heroes"] if not args.only or h in args.only]
        for index, hero_id in enumerate(hero_ids, start=1):
            log(f"=== [{index}/{len(hero_ids)}] {hero_id} ===")
            chunk = json.loads(json.dumps(source["heroes"][hero_id]))
            chunk = translator.translate_scope(chunk, hero_id)
            write_json(RU_DIR / f"{hero_id}.json", chunk)

    if args.items or not args.only:
        items = json.loads(json.dumps(source["items"]))
        items = translator.translate_scope(items, "предметы")
        write_json(RU_DIR / "_items.json", items)

    # Названия групп фильтров — такие же фиксированные метки, как фазы и ярлыки
    # ответов: иначе в датасете остаются англоязычные чипы интерфейса.
    groups = {key: value["name"] for key, value in source["groups"].items()}
    write_json(RU_DIR / "_groups.json", translator.translate_scope(groups, "группы"))

    save_cache(translator.cache)
    if translator.review:
        write_json(REVIEW_JSON, translator.review)
    log(f"Готово за {time.time() - started:.0f} с. Кэш: {len(translator.cache)} строк, "
        f"на доработку: {len(translator.review)}")
    if translator.missing:
        # В офлайн-режиме чанки собираются с английскими остатками: сообщаем,
        # сколько именно строк ещё ждут перевода, вместо молчаливой пустоты.
        unique_missing = sorted(set(translator.missing))
        log(f"ВНИМАНИЕ: {len(unique_missing)} строк остались английскими. "
            f"Выгрузите их: --export-worklist")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())