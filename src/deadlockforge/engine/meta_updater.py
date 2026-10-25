"""Automatic daily background tierlist / hero stats updater.

Fetches global analytics from deadlock-api (https://api.deadlock-api.com/v1/analytics/hero-stats),
computes winrate and pickrate for all heroes, compares against existing values, and only updates
data/heroes.json and the in-memory Catalog cache if actual stats have changed.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("deadlockforge.meta_updater")

HERO_STATS_API = "https://api.deadlock-api.com/v1/analytics/hero-stats"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) DeadlockForge/1.1"
HTTP_TIMEOUT = 25
REFRESH_INTERVAL_SECONDS = 24 * 60 * 60  # Once every 24 hours (1 day)


class MetaUpdater:
    def __init__(self, data_dir: Optional[Path] = None):
        self._data_dir = data_dir
        self._lock = threading.Lock()
        self._is_updating = False
        self._thread: Optional[threading.Thread] = None
        self._status: Dict[str, Any] = {
            "is_updating": False,
            "last_checked_at": None,
            "last_updated_at": None,
            "updated": False,
            "changes_count": 0,
            "error": None,
        }

    def set_data_dir(self, data_dir: Path) -> None:
        self._data_dir = data_dir

    def get_data_dir(self) -> Path:
        if self._data_dir:
            return self._data_dir
        env = os.environ.get("DEADLOCK_DATA_DIR")
        if env:
            return Path(env)
        root = Path(__file__).resolve().parents[2]
        return root / "data"

    def get_status(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._status)

    def trigger_background_check(self, force: bool = False) -> None:
        """Trigger background check in a daemon thread if interval elapsed or forced."""
        with self._lock:
            if self._is_updating:
                return

        def _worker():
            self.check_and_update(force=force)

        t = threading.Thread(target=_worker, name="meta-updater", daemon=True)
        t.start()

    def check_and_update(self, force: bool = False) -> Dict[str, Any]:
        """Perform check and update. Thread-safe."""
        with self._lock:
            if self._is_updating:
                return dict(self._status)
            self._is_updating = True
            self._status["is_updating"] = True
            self._status["error"] = None

        now_utc = datetime.now(timezone.utc)
        now_iso = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")

        try:
            data_dir = self.get_data_dir()
            heroes_file = data_dir / "heroes.json"

            if not heroes_file.is_file():
                raise FileNotFoundError(f"heroes.json not found in {data_dir}")

            # Check interval if not forced
            last_checked = self._status.get("last_checked_at")
            if not force and last_checked:
                try:
                    last_dt = datetime.fromisoformat(last_checked.replace("Z", "+00:00"))
                    elapsed = (now_utc - last_dt).total_seconds()
                    if elapsed < REFRESH_INTERVAL_SECONDS:
                        logger.info("Meta updater: 24h interval not elapsed (%.1f s remaining)", REFRESH_INTERVAL_SECONDS - elapsed)
                        with self._lock:
                            self._is_updating = False
                            self._status["is_updating"] = False
                            self._status["updated"] = False
                            return dict(self._status)
                except Exception:
                    pass

            # Fetch live analytics
            req = urllib.request.Request(
                HERO_STATS_API,
                headers={"User-Agent": UA, "Accept": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
                raw = json.loads(resp.read().decode("utf-8", "replace"))

            if not isinstance(raw, list):
                raise ValueError(f"Unexpected analytics payload type: {type(raw).__name__}")

            b0 = [entry for entry in raw if entry.get("bucket") == 0]
            if not b0:
                raise ValueError("No bucket=0 entries in hero-stats payload")

            total_matches = sum(
                e.get("matches") or 0 for e in b0
                if isinstance(e.get("matches"), (int, float))
            )

            stats_by_api_id: Dict[int, dict] = {}
            for entry in b0:
                eid = entry.get("hero_id") or entry.get("id")
                if isinstance(eid, int) or (isinstance(eid, str) and eid.isdigit()):
                    stats_by_api_id[int(eid)] = entry

            # Read existing heroes
            heroes = json.loads(heroes_file.read_text(encoding="utf-8"))
            if not isinstance(heroes, list):
                raise ValueError("heroes.json content is not a list")

            today_date = now_utc.strftime("%Y-%m-%d")
            changes_count = 0

            for hero in heroes:
                api_id = hero.get("api_id")
                if not api_id or int(api_id) not in stats_by_api_id:
                    continue

                entry = stats_by_api_id[int(api_id)]
                wins = entry.get("wins")
                matches = entry.get("matches")
                if not isinstance(wins, (int, float)) or not isinstance(matches, (int, float)) or matches <= 0:
                    continue

                new_wr = round(float(wins) / float(matches), 4)
                new_pr = round(float(matches) / float(total_matches), 4) if total_matches > 0 else None

                old_wr = hero.get("winrate")
                old_pr = hero.get("pickrate")

                has_diff = False
                if old_wr is None or abs(old_wr - new_wr) > 0.0001:
                    has_diff = True
                if new_pr is not None and (old_pr is None or abs(old_pr - new_pr) > 0.0001):
                    has_diff = True

                if has_diff:
                    changes_count += 1
                    hero["winrate"] = new_wr
                    if new_pr is not None:
                        hero["pickrate"] = new_pr
                    hero["meta_updated_at"] = today_date

            # Only write to disk and refresh cache if changes actually occurred
            if changes_count > 0:
                heroes_file.write_text(
                    json.dumps(heroes, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8"
                )
                logger.info("Meta updater: updated %d heroes with fresh stats to %s", changes_count, heroes_file)

                # Invalidate in-memory Catalog caches
                try:
                    from .dataset import get_catalog
                    get_catalog.cache_clear()
                except Exception as e:
                    logger.warning("Could not clear catalog cache: %s", e)

                with self._lock:
                    self._status["updated"] = True
                    self._status["changes_count"] = changes_count
                    self._status["last_updated_at"] = today_date
                    self._status["last_checked_at"] = now_iso
            else:
                logger.info("Meta updater: checked successfully, stats are already up-to-date")
                with self._lock:
                    self._status["updated"] = False
                    self._status["changes_count"] = 0
                    self._status["last_checked_at"] = now_iso

        except Exception as exc:
            logger.error("Meta updater error: %s", exc)
            with self._lock:
                self._status["error"] = str(exc)
                self._status["updated"] = False
        finally:
            with self._lock:
                self._is_updating = False
                self._status["is_updating"] = False

        return self.get_status()


# Global instance
meta_updater = MetaUpdater()
