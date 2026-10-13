# -*- coding: utf-8 -*-
"""Генерация фирменной иконки DeadlockForge (TZ C1).

Рисует Pillow-ом эмблему: тёмный ромб-щит с оранжевой (#F97316) гранью
и «молотом» из двух полос — узнаваемый символ кузни. Выходы:
- web/assets/icons/deadlockforge.ico (256/64/48/32/16)
- web/assets/icons/deadlockforge_256.png / _64.png
- web/assets/icons/favicon.ico (копия)
"""

from pathlib import Path

from PIL import Image, ImageDraw

ORANGE = (249, 115, 22, 255)          # #F97316
ORANGE_HOT = (253, 186, 116, 255)     # светлый акцент
BG = (7, 10, 15, 255)                 # #070A0F
EDGE = (229, 169, 60, 255)            # #E5A93C


def draw_emblem(size: int) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    s = size
    cx = cy = s / 2.0

    # внешний ромб-щит; паддинг 6% под обводку, чтобы вершины не резались
    r = s * 0.42
    shield = [(cx, cy - r), (cx + r * 0.8, cy), (cx, cy + r), (cx - r * 0.8, cy)]
    d.polygon(shield, fill=BG)
    d.line(shield + [shield[0]], fill=EDGE, width=max(1, int(s * 0.025)),
           joint="curve")

    # «кузнечный молот» — вертикальная рукоять + горизонтальный боёк
    # (T-силуэт: читается мгновенно на любом размере)
    handle_w = max(2, int(s * 0.055))
    handle_top = cy - r * 0.05
    handle_bot = cy + r * 0.62
    d.rectangle([cx - handle_w / 2, handle_top, cx + handle_w / 2, handle_bot],
                fill=ORANGE)

    head_h = r * 0.16
    head_w = r * 0.85
    head_top = handle_top - head_h * 1.4
    # боёк со скруглёнными углами — плоский залив, без бликов (мелкие
    # размеры не читают псевдо-3D)
    d.rounded_rectangle(
        [cx - head_w / 2, head_top, cx + head_w / 2, head_top + head_h],
        radius=head_h / 2, fill=ORANGE)

    # «наковальня» — широкий клин снизу
    anvil_top = handle_bot
    anvil = [(cx - r * 0.34, anvil_top + r * 0.14),
             (cx + r * 0.34, anvil_top + r * 0.14),
             (cx + r * 0.22, anvil_top),
             (cx - r * 0.22, anvil_top)]
    d.polygon(anvil, fill=ORANGE)

    return img


def _rotated_rect(center, w, h, deg):
    import math

    a = math.radians(deg)
    cos, sin = math.cos(a), math.sin(a)
    pts = []
    for dx, dy in ((-w, -h), (w, -h), (w, h), (-w, h)):
        pts.append((center[0] + dx * cos - dy * sin,
                    center[1] + dx * sin + dy * cos))
    return pts


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    out_dir = root / "web" / "assets" / "icons"
    out_dir.mkdir(parents=True, exist_ok=True)

    master = draw_emblem(1024)

    ico_sizes = [(256, 256), (64, 64), (48, 48), (32, 32), (16, 16)]
    master.resize((256, 256), Image.LANCZOS).save(
        out_dir / "deadlockforge.ico", format="ICO", sizes=ico_sizes)
    master.resize((64, 64), Image.LANCZOS).save(
        out_dir / "favicon.ico", format="ICO", sizes=ico_sizes)

    for png_size in (256, 64):
        master.resize((png_size, png_size), Image.LANCZOS).save(
            out_dir / f"deadlockforge_{png_size}.png", format="PNG")

    print(f"Иконки записаны в {out_dir}")


if __name__ == "__main__":
    main()
