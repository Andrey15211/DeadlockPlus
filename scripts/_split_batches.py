"""Разбить _to_translate.json на батчи и сверить глоссарий с официальными RU-именами."""
import json
import re
from pathlib import Path

OUT = Path("data/counterpicks")
BATCH_DIR = OUT / "_batches"
DONE_DIR = OUT / "_translated"

todo = json.load(open(OUT / "_to_translate.json", encoding="utf-8"))
gloss = json.load(open(OUT / "_glossary_todo.json", encoding="utf-8"))

# --- сверка: имена героев/предметов уже есть в data/*.json на русском ---
official: dict[str, str] = {}
for path, key in (("data/heroes.json", "heroes"), ("data/items.json", "items")):
    for row in json.load(open(path, encoding="utf-8")):
        if row.get("name_en") and row.get("name"):
            official[row["name_en"]] = row["name"]

mismatch, matched = [], 0
for en, ru in gloss.items():
    if en in official:
        matched += 1
        if official[en] != ru:
            mismatch.append((en, ru, official[en]))
print(f"глоссарий: {len(gloss)} терминов, из них героев/предметов {matched}, расхождений {len(mismatch)}")
for row in mismatch:
    print("  РАСХОЖДЕНИЕ:", row)

bad_labels = [s for s in todo if re.fullmatch(r"\d+(?:[.,]\d+)?\s*min", s.strip())]
print("строк-таймингов в списке (не должно быть):", len(bad_labels))
print("латиницы в глоссарии нет" if not any(re.search(r"[А-Яа-я]", ru) is None for ru in gloss.values()) else "!! термин без кириллицы")

# --- батчи ---
BATCH = 60
BATCH_DIR.mkdir(parents=True, exist_ok=True)
DONE_DIR.mkdir(parents=True, exist_ok=True)
for old in BATCH_DIR.glob("batch_*.json"):
    old.unlink()
names = []
for i in range(0, len(todo), BATCH):
    chunk = todo[i : i + BATCH]
    name = f"batch_{i // BATCH:03d}.json"
    (BATCH_DIR / name).write_text(
        json.dumps(chunk, ensure_ascii=False, indent=0) + "\n", encoding="utf-8"
    )
    names.append(name)
print(f"батчей: {len(names)} по {BATCH}, строк всего {len(todo)}")
print(" ,".join(names))
