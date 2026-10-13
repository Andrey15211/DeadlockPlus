"""Asset ingest pipeline: hero portraits, item icons, map minimap.

Fallback chain per asset:
  1. local VPK (ValvePython `vpk`), env DEADLOCK_VPK or default Steam path —
     only usable when the bytes carry a PNG payload;
  2. CDN (assets-bucket.deadlock-api.com / api.deadlock-api.com) with a
     browser User-Agent (bare clients get 403).

Never raises for a single broken asset: failures land in IngestReport.errors.
sync() is manual (button / CLI) — nothing here runs at import time.

HTTP client is stdlib urllib.request only.
"""

from __future__ import annotations

import json
import os
import struct
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) DeadlockForge/1.0"
HTTP_TIMEOUT = 30
HTTP_RETRIES = 2

HEROES_API = "https://api.deadlock-api.com/v1/assets/heroes"
ITEMS_API = "https://api.deadlock-api.com/v1/assets/items"
MAP_API = "https://api.deadlock-api.com/v1/assets/map"
CDN_HERO_IMAGE = (
    "https://assets-bucket.deadlock-api.com/assets-api-res/images/heroes")

DEFAULT_VPK = Path(
    r"C:\SteamLibrary\steamapps\common\Deadlock\game\citadel\pak01_dir.vpk")
VPK_HERO_PREFIX = "panorama/images/heroes"
MAP_OUTPUT_NAME = "cursed_apple_minimap.png"

# webp/png/psd/gif magic sniffing; the VPK _sm_psd entries are a PSD wrapper
# that may embed a plain PNG payload — PNG magic wins over PSD signature.
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_PSD_MAGIC = b"8BPS"
_WEBP_MAGIC_RIFF = b"RIFF"


@dataclass
class IngestReport:
    """Outcome of one sync() run; per-category counters + error strings."""

    downloaded: dict[str, int] = field(
        default_factory=lambda: {"heroes": 0, "items": 0, "map": 0})
    vpk: dict[str, int] = field(
        default_factory=lambda: {"heroes": 0, "items": 0, "map": 0})
    skipped: dict[str, int] = field(
        default_factory=lambda: {"heroes": 0, "items": 0, "map": 0})
    failed: dict[str, int] = field(
        default_factory=lambda: {"heroes": 0, "items": 0, "map": 0})
    errors: list[str] = field(default_factory=list)

    def ok(self, category: str, source: str) -> None:
        getattr(self, "downloaded" if source == "cdn" else source)[category] += 1

    def skip(self, category: str) -> None:
        self.skipped[category] += 1

    def fail(self, category: str, message: str) -> None:
        self.failed[category] += 1
        self.errors.append(f"[{category}] {message}")

    @property
    def total_downloaded(self) -> int:
        return sum(self.downloaded.values())

    @property
    def total_vpk(self) -> int:
        return sum(self.vpk.values())

    @property
    def total_skipped(self) -> int:
        return sum(self.skipped.values())

    @property
    def total_failed(self) -> int:
        return sum(self.failed.values())

    def as_dict(self) -> dict[str, Any]:
        return {
            "downloaded": dict(self.downloaded),
            "vpk": dict(self.vpk),
            "skipped": dict(self.skipped),
            "failed": dict(self.failed),
            "errors": list(self.errors),
        }


class AssetIngest:
    """Syncs game images into web/assets/ (heroes/items/map)."""

    def __init__(self, web_assets_dir: Path) -> None:
        self.web_assets_dir = Path(web_assets_dir)
        (self.web_assets_dir / "heroes").mkdir(parents=True, exist_ok=True)
        (self.web_assets_dir / "items").mkdir(parents=True, exist_ok=True)
        (self.web_assets_dir / "map").mkdir(parents=True, exist_ok=True)
        self._vpk_cache: Any = None

    # ------------------------------------------------------------------ sync

    def sync(self, hero_ids: list[str]) -> IngestReport:
        """Run the full pipeline. One broken asset -> report entry, not a raise."""
        report = IngestReport()
        try:
            hero_meta = self._hero_meta(hero_ids, report)
        except Exception as exc:  # pragma: no cover - network metadata
            report.fail("heroes", f"hero metadata fetch failed: {exc}")
            hero_meta = {}

        for hero_id in hero_ids:
            codename, direct_url = hero_meta.get(hero_id, (hero_id, None))
            try:
                self._sync_hero(hero_id, codename, direct_url, report)
            except Exception as exc:
                report.fail("heroes", f"{hero_id}: {exc}")

        try:
            self._sync_items(report)
        except Exception as exc:
            report.fail("items", f"item metadata fetch failed: {exc}")

        try:
            self._sync_map(report)
        except Exception as exc:
            report.fail("map", f"map metadata fetch failed: {exc}")

        return report

    # ---------------------------------------------------------------- heroes

    def _sync_hero(self, hero_id: str, codename: str,
                   direct_url: str | None, report: IngestReport) -> None:
        out = self.web_assets_dir / "heroes" / f"{hero_id}.webp"
        vpk_path = self._resolve_vpk_path()
        if vpk_path is not None:
            data = self._read_vpk_portrait(vpk_path, codename)
            if data is not None and self._png_payload(data) is not None:
                # VPK gave a usable PNG; store it (browser eats PNG fine).
                png = self._png_payload(data)
                out_png = out.with_suffix(".png")
                out_png.write_bytes(png)
                # Remove a stale webp twin so only one file remains.
                if out.exists():
                    out.unlink()
                report.vpk["heroes"] += 1
                return
            if data is not None:
                # Real PSD: Pillow cannot render PSD layers -> skip to CDN.
                report.skip("heroes")

        # CDN: the direct URL from the API (reliable — the CDN filename does
        # not always match the codename, e.g. abrams=atlas but bull_sm.png).
        candidates: list[str] = []
        if direct_url:
            candidates.append(direct_url)
            png_twin = direct_url.replace(".webp", ".png")
            if png_twin != direct_url:
                candidates.append(png_twin)
        # legacy constructed URLs as a last resort
        candidates.append(f"{CDN_HERO_IMAGE}/{codename}_sm.webp")
        candidates.append(f"{CDN_HERO_IMAGE}/{codename}_sm.png")

        for url in candidates:
            data = self._http_get(url)
            if data is not None:
                suffix = Path(url).suffix or ".webp"
                target = out.with_suffix(suffix)
                target.write_bytes(data)
                stale = out.with_suffix(".webp" if suffix == ".png" else ".png")
                if stale.exists():
                    stale.unlink()
                report.downloaded["heroes"] += 1
                return
        report.fail("heroes", f"{hero_id}: no source produced an image "
                              f"(vpk={'yes' if vpk_path else 'no'}, "
                              f"cdn {len(candidates)} urls failed)")

    def _hero_meta(self, hero_ids: list[str],
                   report: IngestReport) -> dict[str, tuple[str, str | None]]:
        """hero_id (dataset slug) -> (vpk codename, direct CDN url).

        The CDN filename does NOT always match the class_name codename
        (abrams=atlas but the file is bull_sm.png), so the reliable source
        is images.icon_image_small_webp of /v1/assets/heroes joined by
        api_id from data/heroes.json. The codename stays for VPK paths.
        """
        raw = self._http_get_json(HEROES_API)
        api_id_to_url: dict[str, str] = {}
        api_id_to_codename: dict[str, str] = {}
        for entry in raw:
            images = entry.get("images") or {}
            api_id_to_url[str(entry.get("id") or "")] = (
                images.get("icon_image_small_webp")
                or images.get("icon_image_small")
                or ""
            )
            class_name = entry.get("class_name") or ""
            api_id_to_codename[str(entry.get("id") or "")] = \
                class_name.removeprefix("hero_")

        result: dict[str, tuple[str, str | None]] = {}
        try:
            heroes_json = self._data_dir() / "heroes.json"
            dataset = json.loads(heroes_json.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            dataset = []
        for hero in dataset:
            hid = hero.get("id")
            api_id = hero.get("api_id")
            key = str(api_id) if api_id else ""
            url = api_id_to_url.get(key) or None
            codename = api_id_to_codename.get(key, hid)
            if hid:
                result[hid] = (codename, url)

        # fallback for heroes missing in the dataset
        for hid in hero_ids:
            if hid not in result:
                result[hid] = (hid.replace("-", "_"), None)
        return result

    def _data_dir(self) -> Path:
        env = os.environ.get("DEADLOCK_DATA_DIR")
        if env:
            return Path(env)
        return Path(__file__).resolve().parents[3] / "data"

    # ------------------------------------------------------------------ VPK

    def _resolve_vpk_path(self) -> Path | None:
        env = os.environ.get("DEADLOCK_VPK")
        path = Path(env) if env else DEFAULT_VPK
        return path if path.is_file() else None

    def _read_vpk_portrait(self, vpk_path: Path, codename: str) -> bytes | None:
        """Read the _sm portrait bytes from the VPK; None if not found."""
        try:
            import vpk  # ValvePython vpk
        except ImportError:
            return None
        try:
            if self._vpk_cache is None or getattr(self._vpk_cache, "_path", None) != str(vpk_path):
                self._vpk_cache = vpk.open(str(vpk_path))
                self._vpk_cache._path = str(vpk_path)  # type: ignore[attr-defined]
            handle = self._vpk_cache.get_file(
                f"{VPK_HERO_PREFIX}/{codename}_sm_psd")
            if handle is None:
                return None
            return handle.read()
        except Exception:
            return None

    @staticmethod
    def _png_payload(data: bytes) -> bytes | None:
        """Extract a PNG image from possibly-wrapped VPK bytes.

        Accepts: bare PNG, PNG after a custom header, or the first PNG
        signature found inside the blob. Returns None for real PSD/webp.
        """
        if data.startswith(_PNG_MAGIC):
            return data
        # Embedded PNG (psd wrapper carrying raw png payload).
        idx = data.find(_PNG_MAGIC)
        if idx != -1:
            end = data.rfind(b"IEND")
            if end != -1:
                return data[idx:end + 8]
            return data[idx:]
        return None

    # ------------------------------------------------------------------ items

    def _sync_items(self, report: IngestReport) -> None:
        raw = self._http_get_json(ITEMS_API)
        for entry in raw:
            item_id = entry.get("id") or entry.get("name")
            if not item_id:
                continue
            url = entry.get("image_webp") or entry.get("shop_image_webp")
            if not url:
                report.skip("items")
                continue
            data = self._http_get(url)
            if data is None:
                report.fail("items", f"{item_id}: download failed {url}")
                continue
            suffix = Path(url).suffix or ".png"
            target = self.web_assets_dir / "items" / f"{item_id}{suffix}"
            target.write_bytes(data)
            report.downloaded["items"] += 1

    # -------------------------------------------------------------------- map

    def _sync_map(self, report: IngestReport) -> None:
        raw = self._http_get_json(MAP_API)
        url = (raw.get("images") or {}).get("minimap")
        if not url:
            report.skip("map")
            return
        data = self._http_get(url)
        if data is None:
            report.fail("map", f"minimap download failed {url}")
            return
        (self.web_assets_dir / "map" / MAP_OUTPUT_NAME).write_bytes(data)
        report.downloaded["map"] += 1

    # ------------------------------------------------------------------- HTTP

    def _http_get(self, url: str, retries: int = HTTP_RETRIES) -> bytes | None:
        """GET with UA, timeout and retries. None on final failure."""
        for attempt in range(1 + retries):
            req = urllib.request.Request(url, headers={
                "User-Agent": USER_AGENT,
                "Accept": "image/webp,image/png,image/*;q=0.9,*/*;q=0.8",
            })
            try:
                with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
                    return resp.read()
            except (urllib.error.HTTPError, urllib.error.URLError,
                    TimeoutError, OSError):
                if attempt < retries:
                    time.sleep(0.5 * (attempt + 1))
        return None

    def _http_get_json(self, url: str) -> Any:
        data = self._http_get(url)
        if data is None:
            raise RuntimeError(f"GET {url} failed after retries")
        return json.loads(data.decode("utf-8"))
