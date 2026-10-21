"""Нарезать _retry.json на мелкие батчи для повторного прохода (rbatch_NNN.json, по 20 строк)."""
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

OUT = Path("data/counterpicks")
RETRY = OUT / "_retry.json"
SRC = OUT / "_batches_retry"
DONE = OUT / "_translated_retry"
SIZE = 20

retry = json.load(open(RETRY, encoding="utf-8"))
SRC.mkdir(parents=True, exist_ok=True)
DONE.mkdir(parents=True, exist_ok=True)
for old in list(SRC.glob("*.json")) + list(DONE.glob("*.json")):
    old.unlink()

names = []
for i in range(0, len(retry), SIZE):
    name = f"rbatch_{i // SIZE:03d}.json"
    (SRC / name).write_text(
        json.dumps(retry[i : i + SIZE], ensure_ascii=False, indent=0) + "\n", encoding="utf-8"
    )
    names.append(name)
print(f"на повтор: {len(retry)} строк -> {len(names)} батчей по {SIZE}")
print(",".join(names))
