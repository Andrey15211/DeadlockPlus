"""TZ v1.4 D1: закрыть 27 дыр в иконках предметов.

Пайплайн на каждый предмет data/items.json без файла-иконки (web/assets/items/):
  1. /v1/assets/items ищется по class_name (upgrade_<slug>, ability_<slug>,
     синонимы по нормализованному имени) — совпадение скачивается
     как {slug}.webp, маппинг пишется в data/icon_overrides.json.
  2. Нет совпадения в API (стартовые T1-апгрейды/DLC-предметы без
     shop-иконок) — генерируется заглушка-миниатюра в едином фирменном
     стиле (буква name_en на тёмном ромбе с оранжевой гранью),
     {slug}.svg, и фиксируется в icon_overrides.json как generated.

Запуск: python scripts/fix_item_icons.py [--dry-run]
"""

from __future__ import annotations

import json
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ITEMS_JSON = ROOT / "data" / "items.json"
OVERRIDES_JSON = ROOT / "data" / "icon_overrides.json"
ICONS_DIR = ROOT / "web" / "assets" / "items"
ASSETS_API = "https://api.deadlock-api.com/v1/assets/items"
UA = "Mozilla/5.0 (compatible) DeadlockForge/1.2"

EDGE = "#E5A93C"
BG = "#070A0F"
TEXT = "#F59E6B"


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _http_json(url: str) -> list:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _http_bytes(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read()


def existing_icon(slug: str) -> Path | None:
    for ext in (".webp", ".png", ".svg"):
        p = ICONS_DIR / f"{slug}{ext}"
        if p.is_file():
            return p
    return None


def find_asset(slug: str, name_en: str, assets: list[dict]) -> dict | None:
    """Найти запись /v1/assets/items для предмета. None = в API нет."""
    by_class = {a.get("class_name", ""): a for a in assets}
    for cand in (f"upgrade_{slug}", f"ability_{slug}", slug):
        if cand in by_class:
            return by_class[cand]
    by_nclass = {_norm(a.get("class_name", "")): a for a in assets}
    for cand in (slug, f"upgrade{slug}", f"ability{slug}"):
        if cand in by_nclass:
            return by_nclass[cand]
    by_name = {a.get("name"): a for a in assets if a.get("name")}
    return by_name.get(name_en)


def placeholder_svg(letter: str) -> str:
    """Единая фирменная заглушка: буква на тёмном ромбе, оранжевая грань."""
    letter = (letter or "?").strip()[:1].upper()
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" width="64" height="64">
  <polygon points="32,4 58,32 32,60 6,32" fill="{BG}" stroke="{EDGE}" stroke-width="3"/>
  <text x="32" y="41" font-family="Oswald, Inter, sans-serif" font-size="26"
        font-weight="700" fill="{TEXT}" text-anchor="middle">{letter}</text>
</svg>
"""


def main() -> int:
    dry = "--dry-run" in sys.argv
    ICONS_DIR.mkdir(parents=True, exist_ok=True)
    items = json.loads(ITEMS_JSON.read_text(encoding="utf-8"))
    overrides: dict = {}
    if OVERRIDES_JSON.is_file():
        try:
            overrides = json.loads(OVERRIDES_JSON.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            overrides = {}

    missing = [it for it in items if existing_icon(it["id"]) is None]
    print(f"предметов без иконки: {len(missing)} / {len(items)}")

    assets: list = []
    if missing:
        print("загружаю каталог /v1/assets/items …")
        assets = _http_json(ASSETS_API)

    downloaded = generated = 0
    for it in missing:
        slug, name_en = it["id"], it.get("name_en") or it["id"]
        asset = find_asset(slug, name_en, assets)
        url = asset.get("image_webp") if asset else None
        if url:
            if dry:
                print(f"  [dry] {slug}: скачать {url}")
                continue
            data = _http_bytes(url)
            suffix = Path(url).suffix or ".png"
            target = ICONS_DIR / f"{slug}{suffix}"
            target.write_bytes(data)
            overrides[slug] = {"source": "assets_api",
                               "class_name": asset.get("class_name"),
                               "url": url}
            downloaded += 1
            print(f"  {slug}: скачан {target.name}")
        else:
            if dry:
                print(f"  [dry] {slug}: сгенерировать заглушку")
                continue
            target = ICONS_DIR / f"{slug}.svg"
            target.write_text(placeholder_svg(
                name_en.replace(" ", "")), encoding="utf-8")
            overrides[slug] = {"source": "generated"}
            generated += 1
            print(f"  {slug}: заглушка {target.name}")

    if not dry:
        OVERRIDES_JSON.write_text(
            json.dumps(overrides, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8")

    # финальная проверка
    still = [it["id"] for it in items if existing_icon(it["id"]) is None]
    print(f"скачано: {downloaded}, заглушек: {generated}, "
          f"осталось без иконки: {len(still)}")
    if still:
        for s in still:
            print("  !", s)
        return 1
    print("OK: 214/214 предметов имеют иконку")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
