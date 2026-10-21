"""Проверка собранного датасета: посторонняя латиница в текстовых полях и целостность ссылок.

Идентификаторы (id, item_id, hero_id, image, portrait, category, key, groups, group_tags,
stages первых уровней) намеренно остаются латиницей — они сверяются с data/heroes.json,
data/items.json и не показываются игроку. Проверяются только текстовые листья.
"""
import json
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Латиница, допустимая в русском тексте.
ALLOW = {"M1", "M2", "Deadlock", "NPC", "HP", "DPS", "FPS", "webp", "API"}
# Поля-идентификаторы: их значения не текст.
SKIP_KEYS = {
    "id", "name_en", "portrait", "image", "shop_image", "source", "extracted_at",
    "category", "tier", "cost", "key", "groups", "group_tags", "hero_id", "item_id",
    "stage_of", "top", "related", "related_guides", "alts", "all", "best", "items",
    "avg_minutes", "version", "item", "hero", "problem_index", "stages",
}

payload = json.load(open("data/counterpicks.json", encoding="utf-8"))


def walk(node, path=()):
    if isinstance(node, str):
        yield path, node
    elif isinstance(node, list):
        for i, x in enumerate(node):
            yield from walk(x, path + (i,))
    elif isinstance(node, dict):
        for k, v in node.items():
            yield from walk(v, path + (k,))


bad = []
for path, text in walk(payload):
    if any(isinstance(p, str) and p in SKIP_KEYS for p in path):
        continue
    tokens = [t for t in re.findall(r"[A-Za-z][A-Za-z0-9'’\-]*", text) if t not in ALLOW]
    if tokens:
        bad.append((".".join(map(str, path)), text, tokens))

print(f"героев {len(payload['heroes'])}, предметов {len(payload['items'])}, групп {len(payload['groups'])}")
print(f"текстовых листьев с посторонней латиницей: {len(bad)}")
for path, text, tokens in bad[:25]:
    print(f"  {path}: {tokens} | {text[:120]}")
