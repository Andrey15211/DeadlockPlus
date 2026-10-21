"""Сборка переводов батчей: валидация, слияние в translation_cache.json, недобор -> _retry.json.

Проверки каждой пары (та же база, что в --import, плюс разметка и строгие имена):
  1. латиница из глоссария в переводе недопустима;
  2. для каждого термина глоссария длиной >= 4 из исходной строки в переводе
     должно стоять его русское название (с допуском на падежное окончание);
     отдельно требуются Ivy=Айви и Rem=Дрём — их нет в _glossary_todo.json из-за фильтра len > 3;
  3. разметка ** сохраняется парами.
Формат результата батча — JSON-массив переводов в порядке входа.
Не прошедшие строки уходят в _retry.json для повторного прохода.
"""
import json
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

OUT = Path("data/counterpicks")
TODO = OUT / "_to_translate.json"
GLOSS = OUT / "_glossary_todo.json"
CACHE = OUT / "translation_cache.json"
RETRY = OUT / "_retry.json"
PAIRS = (("_batches", "_translated"), ("_batches_retry", "_translated_retry"))
# Короткие официальные имена, которые не проходят фильтр len > 3 в export_worklist
# и потому отсутствуют в _glossary_todo.json, но обязаны стоять в переводе.
SUPPLEMENT = {"Ivy": "Айви", "Rem": "Дрём"}


def mentions(text: str, ru_name: str) -> bool:
    """Название в тексте с допуском на падежное окончание.

    Опорное слово — самое длинное в названии: «Мо и Мелкий» в родительном
    падеже даёт «Мо и Мелкого», и проверка по первому слову («мо») не сработала бы.
    Усечение до 3 букв: беглая гласная выпадает целиком («Коготь» → «Когтя»).
    """
    words = [w for w in ru_name.split() if w]
    if not words:
        return False
    if ru_name in text:
        return True
    low = text.lower()
    target = max(words, key=len).lower()
    return any(target[:n] in low for n in range(len(target), 2, -1))


def main() -> int:
    todo = json.load(open(TODO, encoding="utf-8"))
    gloss = json.load(open(GLOSS, encoding="utf-8"))
    cache = json.load(open(CACHE, encoding="utf-8"))

    def term_re(keys):
        names = sorted(keys, key=len, reverse=True)
        return re.compile(r"(?<![A-Za-z0-9])(" + "|".join(re.escape(k) for k in names) + r")(?![A-Za-z0-9])")

    all_re = term_re({**gloss, **SUPPLEMENT})
    # Строгая проверка имён: требуем присутствия русского названия для всех терминов
    # длиной >= 4 (короткие вроде Stake/Melee дают ложные совпадения) плюс SUPPLEMENT.
    enforced = {k: v for k, v in gloss.items() if len(k) >= 4}
    enforced.update(SUPPLEMENT)
    enforced_re = term_re(enforced)

    results: dict[str, str] = {}
    file_issues: list[str] = []
    for src_dir, done_dir in PAIRS:
        src_path, done_path = OUT / src_dir, OUT / done_dir
        if not src_path.is_dir():
            continue
        sources = {p.name: json.load(open(p, encoding="utf-8")) for p in sorted(src_path.glob("*.json"))}
        for path in sorted(done_path.glob("*.json")) if done_path.is_dir() else []:
            source_batch = sources.get(path.name)
            if source_batch is None:
                file_issues.append(f"{path.name}: нет входного батча с таким именем")
                continue
            try:
                payload = json.load(open(path, encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as exc:
                file_issues.append(f"{path.name}: не читается ({exc})")
                continue
            if isinstance(payload, list):
                if len(payload) != len(source_batch):
                    file_issues.append(
                        f"{path.name}: длина {len(payload)} вместо {len(source_batch)} — батч не принят"
                    )
                    continue
                pairs = zip(source_batch, payload)
            elif isinstance(payload, dict):
                pairs = ((s, payload.get(s)) for s in source_batch)
            else:
                file_issues.append(f"{path.name}: ожидался массив или объект, а не {type(payload).__name__}")
                continue
            for source, translated in pairs:
                if isinstance(translated, str) and translated.strip():
                    results[source] = translated

    ok, retry, bad = 0, [], []
    for source in todo:
        translated = results.get(source)
        if not translated:
            retry.append(source)
            continue
        problems = []
        leftovers = sorted({m.group(1) for m in all_re.finditer(translated)})
        if leftovers:
            problems.append("латиница: " + ", ".join(leftovers))
        missing = [
            en
            for en in sorted({m.group(1) for m in enforced_re.finditer(source)})
            if not mentions(translated, enforced[en])
        ]
        if missing:
            problems.append("потеряны названия: " + ", ".join(missing))
        if source.count("**") != translated.count("**"):
            problems.append(f"разметка ** : {source.count('**')} -> {translated.count('**')}")
        if problems:
            bad.append((source, translated, problems))
            retry.append(source)
        else:
            cache[source] = translated
            ok += 1

    CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=0) + "\n", encoding="utf-8")
    RETRY.write_text(json.dumps(retry, ensure_ascii=False, indent=0) + "\n", encoding="utf-8")
    print(f"принято новых: {ok} | не прошли: {len(bad)} | без ответа: {len(retry) - len(bad)}")
    print(f"кэш всего: {len(cache)} строк | на повтор: {len(retry)} -> {RETRY.name}")
    for issue in file_issues:
        print("  ФАЙЛ:", issue)
    for source, translated, problems in bad[:12]:
        print("  EN:", source[:110])
        print("  RU:", translated[:110])
        print("     ->", "; ".join(problems))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
