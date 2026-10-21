"""Build the seamless loader loop from Assets/animation_ai_loading.mp4.

WHY PING-PONG
The source clip (1280x720, 24 fps, 10 s) plays:
    frames   0-163  calm eye/reticle emblem
    frames 164-205  flare: the iris opens
    frames 206-239  settled aperture hold
It does not loop: frame 239 (bright aperture) vs frame 0 (calm) differs by ~23
grey levels, ~65x a normal frame step, so a straight loop snaps hard.

A ping-pong (forward, then reversed) is the same idiom the project already uses
for its background video, and both wrap points become ordinary frame steps.

FRAME MAPPING (exact, by construction)
    forward  : source frames 0 .. 239                (240 frames)
    reversed : source frames 238 .. 1                (238 frames)
    asset    : 0..477, so
               asset[239] = src 239, asset[240] = src 238   (turn, a normal step)
               asset[477] = src 1,   wraps to asset[0] = src 0  (wrap, a normal step)

The reversed half is assembled in Python rather than with ffmpeg's reverse+trim,
so the mapping above is exact and directly verifiable.

Output:
    web/assets/video/ai_loading_eye.webm   (VP9, primary)
    web/assets/video/ai_loading_eye.mp4    (H.264, fallback)
"""
from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = ROOT / "Assets" / "animation_ai_loading.mp4"
OUT_DIR = ROOT / "web" / "assets" / "video"
BUILD = ROOT / ".scratch" / "anim" / "build"

# Centred square crop around the emblem.
# Bright emblem bbox in the source is x 334..940, y 60..659 -> centre (637, 360).
# A 640x640 crop at (317, 40) is centred on that point, with dark margin all round.
CROP = "crop=640:640:317:40"
SIZE = 320      # rendered at ~80-96 CSS px, so 320 px keeps retina headroom
FPS = 24        # the source frame rate: keeps the flare at its natural speed


def source_frame_count() -> int:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "format=duration", "-of", "json", str(SRC)],
        check=True, capture_output=True, text=True,
    ).stdout
    return round(float(json.loads(out)["format"]["duration"]) * FPS)


def decode_source(seq_dir: pathlib.Path) -> int:
    shutil.rmtree(seq_dir, ignore_errors=True)
    seq_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run([
        "ffmpeg", "-y", "-v", "error", "-i", str(SRC),
        "-vf", f"{CROP},scale={SIZE}:{SIZE}:flags=lanczos,fps={FPS}",
        "-fps_mode", "passthrough",
        str(seq_dir / "s_%04d.png"),
    ], check=True)
    names = sorted(p.name for p in seq_dir.glob("s_*.png"))
    return len(names)


def assemble_pingpong(seq_dir: pathlib.Path, out_dir: pathlib.Path, n: int) -> list[int]:
    """Write the ping-pong sequence as p_0001.png .. and return the source index list."""
    shutil.rmtree(out_dir, ignore_errors=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    order = list(range(n)) + list(range(n - 2, 0, -1))
    for i, src_idx in enumerate(order, start=1):
        src_png = seq_dir / ("s_%04d.png" % (src_idx + 1))
        shutil.copyfile(src_png, out_dir / ("p_%04d.png" % i))
    return order


def encode(out_dir: pathlib.Path, dst: pathlib.Path, codec: list[str]) -> None:
    subprocess.run([
        "ffmpeg", "-y", "-v", "error",
        "-framerate", str(FPS), "-i", str(out_dir / "p_%04d.png"),
        "-an", *codec, "-pix_fmt", "yuv420p", str(dst),
    ], check=True)


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    seq = BUILD / "src"
    pp = BUILD / "pp"

    n = decode_source(seq)
    order = assemble_pingpong(seq, pp, n)
    total = len(order)

    print(f"source frames @{FPS}fps : {n}")
    print(f"ping-pong frames      : {total}  ({total / FPS:.2f} s)")
    print(f"  forward  src {order[0]}..{order[n - 1]}")
    print(f"  reversed src {order[n]}..{order[-1]}")
    print(f"  turn     asset[{n - 1}]=src{order[n - 1]} -> asset[{n}]=src{order[n]}")
    print(f"  wrap     asset[{total - 1}]=src{order[-1]} -> asset[0]=src{order[0]}")

    webm = OUT_DIR / "ai_loading_eye.webm"
    mp4 = OUT_DIR / "ai_loading_eye.mp4"
    encode(pp, webm, ["-c:v", "libvpx-vp9", "-crf", "36", "-b:v", "0",
                      "-row-mt", "1", "-cpu-used", "2"])
    encode(pp, mp4, ["-c:v", "libx264", "-crf", "22", "-preset", "slow",
                     "-movflags", "+faststart"])

    (BUILD / "order.json").write_text(json.dumps(order), encoding="utf-8")
    for f in (webm, mp4):
        print(f"{f.name}: {f.stat().st_size / 1024:.1f} KB")
    return 0


if __name__ == "__main__":
    sys.exit(main())