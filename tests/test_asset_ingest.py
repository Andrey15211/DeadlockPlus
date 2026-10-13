"""AssetIngest tests: mocked urllib + mocked vpk, no real network.

CDN path: URL formation, UA header, retries, file writes, 404 -> errors.
VPK path: monkeypatched vpk module with fake portrait entries.
"""

from __future__ import annotations

import io
import json
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from deadlockforge.assets.ingest import AssetIngest, USER_AGENT

HEROES_META = [{"id": "inferno", "class_name": "hero_inferno"},
               {"id": "abrams", "class_name": "hero_abrams"}]
ITEMS_META = [
    {"id": "item_bullet_lifesteal", "image_webp":
     "https://assets-bucket.deadlock-api.com/assets-api-res/images/items/lifesteal.webp",
     "shop_image_webp": None},
    {"id": "item_extra_charge", "image_webp": None,
     "shop_image_webp":
     "https://assets-bucket.deadlock-api.com/assets-api-res/images/shop/charge.png"},
    {"id": "item_no_images", "image_webp": None, "shop_image_webp": None},
]
MAP_META = {"images": {"minimap":
            "https://assets-bucket.deadlock-api.com/assets-api-res/map/minimap.png"}}

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32 + b"IEND\xaeB`\x82"
WEBP_BYTES = b"RIFF\x24\x00\x00\x00WEBPVP8 fakepayload"


class FakeResponse:
    def __init__(self, payload: bytes):
        self._stream = io.BytesIO(payload)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self) -> bytes:
        return self._stream.read()


class URLLoader:
    """Records requests; serves canned responses per URL or raises HTTPError."""

    def __init__(self, routes: dict[str, bytes]):
        self.routes = routes
        self.requests: list[urllib.request.Request] = []

    def __call__(self, req, timeout=None):
        assert isinstance(req, urllib.request.Request)
        self.requests.append(req)
        url = req.full_url
        if url in self.routes:
            return FakeResponse(self.routes[url])
        raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)


def json_route(data) -> bytes:
    return json.dumps(data).encode("utf-8")


@pytest.fixture()
def assets_dir(tmp_path) -> Path:
    return tmp_path / "web" / "assets"


def route_defaults(extra: dict | None = None) -> dict[str, bytes]:
    routes = {
        "https://api.deadlock-api.com/v1/assets/heroes": json_route(HEROES_META),
        "https://api.deadlock-api.com/v1/assets/items": json_route(ITEMS_META),
        "https://api.deadlock-api.com/v1/assets/map": json_route(MAP_META),
        "https://assets-bucket.deadlock-api.com/assets-api-res/images/heroes/inferno_sm.webp": WEBP_BYTES,
        "https://assets-bucket.deadlock-api.com/assets-api-res/images/heroes/abrams_sm.webp": WEBP_BYTES,
        "https://assets-bucket.deadlock-api.com/assets-api-res/images/items/lifesteal.webp": WEBP_BYTES,
        "https://assets-bucket.deadlock-api.com/assets-api-res/images/shop/charge.png": PNG_BYTES,
        "https://assets-bucket.deadlock-api.com/assets-api-res/map/minimap.png": PNG_BYTES,
    }
    routes.update(extra or {})
    return routes


# ------------------------------------------------------------------ CDN


def test_cdn_hero_url_and_ua(monkeypatch, assets_dir):
    loader = URLLoader(route_defaults())
    monkeypatch.setattr(urllib.request, "urlopen", loader)
    ingest = AssetIngest(assets_dir)
    ingest.sync(["inferno"])

    hero_requests = [r for r in loader.requests
                     if "heroes/inferno_sm" in r.full_url]
    assert hero_requests, "hero CDN request missing"
    assert hero_requests[0].full_url == (
        "https://assets-bucket.deadlock-api.com/"
        "assets-api-res/images/heroes/inferno_sm.webp")
    assert hero_requests[0].headers.get("User-agent") == USER_AGENT
    assert "Mozilla/5.0" in hero_requests[0].headers.get("User-agent", "")


def test_cdn_hero_file_written(monkeypatch, assets_dir):
    monkeypatch.setattr(urllib.request, "urlopen",
                        URLLoader(route_defaults()))
    report = AssetIngest(assets_dir).sync(["inferno"])
    hero_file = assets_dir / "heroes" / "inferno.webp"
    assert hero_file.read_bytes() == WEBP_BYTES
    assert report.downloaded["heroes"] == 1
    assert report.total_failed == 0


def test_cdn_hero_png_fallback_when_webp_404(monkeypatch, assets_dir):
    routes = route_defaults({
        "https://assets-bucket.deadlock-api.com/assets-api-res/images/heroes/abrams_sm.png": PNG_BYTES,
    })
    del routes["https://assets-bucket.deadlock-api.com/assets-api-res/images/heroes/abrams_sm.webp"]
    loader = URLLoader(routes)
    monkeypatch.setattr(urllib.request, "urlopen", loader)
    report = AssetIngest(assets_dir).sync(["abrams"])
    assert (assets_dir / "heroes" / "abrams.png").read_bytes() == PNG_BYTES
    assert not (assets_dir / "heroes" / "abrams.webp").exists()
    assert report.downloaded["heroes"] == 1


def test_cdn_hero_all_404_goes_to_errors_with_retries(monkeypatch, assets_dir):
    routes = route_defaults()
    for url in list(routes):
        if "heroes/inferno_sm" in url:
            del routes[url]
    # VPK is unavailable in tests (no real file, no env var)
    monkeypatch.delenv("DEADLOCK_VPK", raising=False)
    loader = URLLoader(routes)
    monkeypatch.setattr(urllib.request, "urlopen", loader)
    monkeypatch.setattr("time.sleep", lambda s: None)
    report = AssetIngest(assets_dir).sync(["inferno"])

    inferno_requests = [r for r in loader.requests
                        if "heroes/inferno_sm" in r.full_url]
    # webp retried, then png retried -> at least 2 + 2 attempts
    assert len(inferno_requests) >= 4
    assert not (assets_dir / "heroes" / "inferno.webp").exists()
    assert report.failed["heroes"] == 1
    assert any("inferno" in e for e in report.errors)


def test_cdn_items_written(monkeypatch, assets_dir):
    monkeypatch.setattr(urllib.request, "urlopen",
                        URLLoader(route_defaults()))
    report = AssetIngest(assets_dir).sync([])
    assert (assets_dir / "items" / "item_bullet_lifesteal.webp").exists()
    # shop_image_webp fallback field is used when image_webp is None
    assert (assets_dir / "items" / "item_extra_charge.png").exists()
    assert report.downloaded["items"] == 2
    # item without any image URL is skipped, not failed
    assert report.skipped["items"] == 1
    assert report.failed["items"] == 0


def test_cdn_map_written(monkeypatch, assets_dir):
    monkeypatch.setattr(urllib.request, "urlopen",
                        URLLoader(route_defaults()))
    report = AssetIngest(assets_dir).sync([])
    assert (assets_dir / "map" / "cursed_apple_minimap.png").read_bytes() == PNG_BYTES
    assert report.downloaded["map"] == 1


def test_single_broken_asset_does_not_abort(monkeypatch, assets_dir):
    routes = route_defaults()
    del routes["https://assets-bucket.deadlock-api.com/assets-api-res/images/items/lifesteal.webp"]
    monkeypatch.setattr(urllib.request, "urlopen", URLLoader(routes))
    report = AssetIngest(assets_dir).sync(["inferno"])
    assert report.downloaded["heroes"] == 1
    assert report.failed["items"] == 1
    assert report.downloaded["map"] == 1  # map still processed


# ------------------------------------------------------------------ VPK


class FakeVpkFile:
    def __init__(self, data: bytes):
        self._data = data

    def read(self) -> bytes:
        return self._data


class FakeVpk:
    def __init__(self, files: dict[str, bytes]):
        self._files = files
        self._path = "fake"

    def get_file(self, path: str):
        return FakeVpkFile(self._path and self._files.get(path)) if path in self._files else None


def test_vpk_png_portrait_extracted(monkeypatch, assets_dir, tmp_path):
    fake_vpk = FakeVpk({
        "panorama/images/heroes/abrams_sm_psd": PNG_BYTES,
    })
    import sys
    monkeypatch.setitem(sys.modules, "vpk", _fake_vpk_module(fake_vpk))
    vpk_file = tmp_path / "pak01_dir.vpk"
    vpk_file.write_bytes(b"fake vpk")
    monkeypatch.setenv("DEADLOCK_VPK", str(vpk_file))

    routes = route_defaults()
    for url in list(routes):
        if "heroes/abrams_sm" in url:
            del routes[url]
    monkeypatch.setattr(urllib.request, "urlopen", URLLoader(routes))

    report = AssetIngest(assets_dir).sync(["abrams"])
    assert (assets_dir / "heroes" / "abrams.png").read_bytes() == PNG_BYTES
    assert report.vpk["heroes"] == 1
    assert report.downloaded["heroes"] == 0


def test_vpk_header_wrapped_png_extracted(assets_dir):
    payload = b"\x00\x01\x02HEADER" + PNG_BYTES + b"tail"
    assert AssetIngest._png_payload(payload) == PNG_BYTES


def test_vpk_real_psd_skipped_to_cdn(monkeypatch, assets_dir, tmp_path):
    psd_bytes = b"8BPS" + b"\x00" * 64
    fake_vpk = FakeVpk({"panorama/images/heroes/abrams_sm_psd": psd_bytes})
    import sys
    monkeypatch.setitem(sys.modules, "vpk", _fake_vpk_module(fake_vpk))
    vpk_file = tmp_path / "pak01_dir.vpk"
    vpk_file.write_bytes(b"fake vpk")
    monkeypatch.setenv("DEADLOCK_VPK", str(vpk_file))

    monkeypatch.setattr(urllib.request, "urlopen",
                        URLLoader(route_defaults()))
    report = AssetIngest(assets_dir).sync(["abrams"])
    # PSD not renderable -> CDN result, skipped counted
    assert (assets_dir / "heroes" / "abrams.webp").exists()
    assert report.skipped["heroes"] == 1
    assert report.downloaded["heroes"] == 1


def test_vpk_missing_entry_falls_back_to_cdn(monkeypatch, assets_dir, tmp_path):
    fake_vpk = FakeVpk({})
    import sys
    monkeypatch.setitem(sys.modules, "vpk", _fake_vpk_module(fake_vpk))
    vpk_file = tmp_path / "pak01_dir.vpk"
    vpk_file.write_bytes(b"fake vpk")
    monkeypatch.setenv("DEADLOCK_VPK", str(vpk_file))
    monkeypatch.setattr(urllib.request, "urlopen",
                        URLLoader(route_defaults()))
    report = AssetIngest(assets_dir).sync(["inferno"])
    assert (assets_dir / "heroes" / "inferno.webp").exists()
    assert report.downloaded["heroes"] == 1
    assert report.vpk["heroes"] == 0


def test_vpk_path_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("DEADLOCK_VPK", str(tmp_path / "nonexistent.vpk"))
    ingest = AssetIngest(tmp_path / "assets")
    assert ingest._resolve_vpk_path() is None
    monkeypatch.setenv("DEADLOCK_VPK", __file__)
    assert ingest._resolve_vpk_path() == Path(__file__)


def _fake_vpk_module(fake_vpk):
    import types
    module = types.ModuleType("vpk")

    def open(path):  # noqa: A001 - mirrors ValvePython API
        return fake_vpk

    module.open = open
    return module
