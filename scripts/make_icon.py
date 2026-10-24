# -*- coding: utf-8 -*-
"""Генерация иконок DeadlockForge из мастер-арта.

Источник (по приоритету):
1. самый свежий `Assets/*.ico` — готовый ярлык от заказчика (имя не важно);
2. `Assets/Готический портал с оком-мишенью.png` — исходный арт 1254×1254.

Готовый .ico от заказчика одноразмерный (256), поэтому Windows сама мылит его
в панели задач и меню «Пуск». Скрипт берёт его кадр как мастер и нарезает
полный набор размеров. Выходы:
- web/assets/icons/deadlockforge.ico (256/64/48/32/16) — иконка exe и ярлыка;
- web/assets/icons/favicon.ico (та же нарезка);
- web/assets/icons/deadlockforge_256.png / _64.png — логотип в шапке UI.
"""

import sys
from pathlib import Path

from PIL import Image

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "Assets"
PNG_SOURCE = ASSETS / "Готический портал с оком-мишенью.png"
OUT_DIR = ROOT / "web" / "assets" / "icons"

ICO_SIZES = [(256, 256), (64, 64), (48, 48), (32, 32), (16, 16)]
PNG_SIZES = (256, 64)


def find_ico() -> Path | None:
    """Самый свежий .ico в Assets/ — заказчик кладёт готовый значок туда."""
    icons = list(ASSETS.glob("*.ico"))
    return max(icons, key=lambda p: p.stat().st_mtime) if icons else None


def load_master() -> Image.Image:
    """Мастер-квадрат RGBA: готовый .ico, иначе обрезанный по альфе PNG."""
    ico = find_ico()
    if ico is not None:
        return Image.open(ico).convert("RGBA")

    img = Image.open(PNG_SOURCE).convert("RGBA")
    bbox = img.getchannel("A").point(lambda a: 255 if a > 16 else 0).getbbox()
    if bbox:
        img = img.crop(bbox)

    side = max(img.size)
    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    canvas.paste(img, ((side - img.width) // 2, (side - img.height) // 2))
    return canvas


def main() -> None:
    if find_ico() is None and not PNG_SOURCE.exists():
        raise SystemExit(f"Нет мастер-арта: Assets/*.ico или {PNG_SOURCE}")
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    master = load_master()
    if master.width < 256:
        master = master.resize((256, 256), Image.LANCZOS)

    master.save(OUT_DIR / "deadlockforge.ico", format="ICO", sizes=ICO_SIZES)
    master.save(OUT_DIR / "favicon.ico", format="ICO",
                sizes=[s for s in ICO_SIZES if s[0] <= 64])

    for png_size in PNG_SIZES:
        master.resize((png_size, png_size), Image.LANCZOS).save(
            OUT_DIR / f"deadlockforge_{png_size}.png", format="PNG")

    print(f"Иконки записаны в {OUT_DIR}")


if __name__ == "__main__":
    main()
