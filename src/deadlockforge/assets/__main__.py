"""CLI-обёртка ассет-синка (TZ A4).

Запуск: python -m deadlockforge.assets.ingest [--web-assets-dir path]
Печатает отчёт синхронизации и завершается кодом 0/1.
UI больше не дергает /api/assets/sync — это внутренний инструмент.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def _default_assets_dir() -> Path:
    env = os.environ.get("DEADLOCK_WEB_DIR")
    if env:
        return Path(env) / "assets"
    return Path(__file__).resolve().parents[3] / "web" / "assets"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Синхронизация игровых ассетов (VPK / CDN) в web/assets.")
    parser.add_argument("--web-assets-dir", default=None,
                        help="каталог web/assets (по умолчанию — проект)")
    args = parser.parse_args()

    assets_dir = Path(args.web_assets_dir) if args.web_assets_dir else _default_assets_dir()

    from .ingest import AssetIngest
    from ..engine import dataset as dataset_mod

    catalog = dataset_mod.get_catalog()
    ingest = AssetIngest(assets_dir)
    print(f"Синхронизация ассетов: {assets_dir}")
    print(f"Героев в ростере: {len(catalog.heroes)}")
    report = ingest.sync([h.id for h in catalog.heroes])
    print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))
    return 0 if report.total_failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
