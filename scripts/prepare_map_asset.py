#!/usr/bin/env python
"""prepare_map_asset.py — привести подложку карты к квадрату проекции мира.

Проблема: data/map.json и точки смертей Deep Match живут в квадратной
проекции (left = x/(2R)+0.5, top = 0.5-y/(2R)). Исходный ассет
Map_colored.png — 502x560, то есть квадратная карта с полями 29 px сверху
и снизу. Из-за полей контейнер маркеров (aspect-ratio картинки) не совпадал
с системой координат, и точки уезжали мимо объектов.

Проверка после обрезки: 51/51 нейтральных лагерей и 15/16 объективов из
/v1/assets/map попадают на игровой контент карты.

Использование:
    python scripts/prepare_map_asset.py [--src path] [--out path]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PIL import Image


def crop_to_square(img: Image.Image) -> Image.Image:
    """Обрезать вертикальные (или горизонтальные) поля до квадрата."""
    w, h = img.size
    if w == h:
        return img
    if h > w:
        pad = (h - w) // 2
        return img.crop((0, pad, w, h - pad))
    pad = (w - h) // 2
    return img.crop((pad, 0, w - pad, h))


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--src", default=str(root / "Assets" / "Map_colored.png"),
                    help="исходная карта с полями")
    ap.add_argument("--out", default=str(root / "web" / "assets" / "map" / "cursed_apple_square.png"),
                    help="куда положить квадратную подложку")
    args = ap.parse_args()

    src, out = Path(args.src), Path(args.out)
    if not src.is_file():
        print(f"[map] исходник не найден: {src}", file=sys.stderr)
        return 1

    with Image.open(src) as img:
        img = img.convert("RGBA")
        square = crop_to_square(img)
        out.parent.mkdir(parents=True, exist_ok=True)
        square.save(out)
    print(f"[map] {src.name} {img.size} -> {out.name} {square.size}")

    # Вторая копия в webp: та же геометрия, меньше вес для WebView2.
    webp_out = out.with_suffix(".webp")
    square.save(webp_out, quality=92, method=6)
    print(f"[map] записан {webp_out.name} ({webp_out.stat().st_size // 1024} КБ)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())