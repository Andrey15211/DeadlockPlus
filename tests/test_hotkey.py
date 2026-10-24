"""Глобальный хоткей — парсер и API без GUI (без сети и без регистрации).

Часть 1 — парсер deadlockforge.desktop.hotkey (parse_hotkey/format_hotkey/
HotkeyListener/HotkeyError). Часть 2 — REST-слой /api/desktop/hotkey через
fastapi.testclient, с подменой _db на временную SQLite в tmp_path.

Настоящий хоткей (HotkeyListener.start) в тестах не регистрируется: в CI
это требует реального окна и системного хука.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

# --- Часть 1: парсер (TDD: модуль может ещё не существовать) --------------

try:
    from deadlockforge.desktop.hotkey import (
        MOD_ALT,
        MOD_CONTROL,
        MOD_SHIFT,
        MOD_WIN,
        HotkeyError,
        HotkeyListener,
        format_hotkey,
        parse_hotkey,
    )

    _HAS_HOTKEY = True
except Exception:  # noqa: BLE001 — модуль ещё не собран
    _HAS_HOTKEY = False

pytestmark = [pytest.mark.skipif(
    not _HAS_HOTKEY, reason="desktop.hotkey not importable")]

REJECT_CASES = [
    "",          # пустая строка
    "f1",        # нет модификатора
    "ctrl",      # нет клавиши
    "ctrl+",     # пустая клавиша
    "+f1",       # пустой модификатор
    "ctrl+ctrl+f1",  # дубль модификатора
    "ctrl+zzz",  # неизвестная клавиша
    "ctrl+f1+f2",    # две клавиши
]


def test_parse_simple():
    assert parse_hotkey("ctrl+f1") == (MOD_CONTROL, 0x70)


def test_parse_spaces_and_case():
    assert parse_hotkey(" Ctrl + Shift + F2 ") == (
        MOD_CONTROL | MOD_SHIFT, 0x71)


def test_parse_letters_and_digits():
    assert parse_hotkey("alt+a") == (MOD_ALT, 0x41)
    assert parse_hotkey("win+1") == (MOD_WIN, 0x31)


def test_parse_named_key():
    assert parse_hotkey("ctrl+space") == (MOD_CONTROL, 0x20)


def test_parse_f24():
    assert parse_hotkey("ctrl+f24") == (MOD_CONTROL, 0x87)


@pytest.mark.parametrize("spec", REJECT_CASES)
def test_parse_rejects(spec):
    with pytest.raises(HotkeyError):
        parse_hotkey(spec)


def test_format_canonical():
    assert format_hotkey("ctrl+f1") == "Ctrl+F1"
    assert format_hotkey("shift+alt+a") == "Alt+Shift+A"


def test_listener_bad_spec_raises():
    with pytest.raises(HotkeyError):
        HotkeyListener("f1", lambda: None)


def test_listener_stop_is_safe():
    """stop() на нестартовавшем слушателе не бросает и registered остаётся False.

    start() не вызываем — регистрация реального хоткея в CI не нужна.
    """
    listener = HotkeyListener("ctrl+shift+f2", lambda: None)
    listener.stop()
    assert listener.registered is False


# --- Часть 2: API (модуль app импортируем в try/except, как в test_live) ---

try:
    from fastapi.testclient import TestClient

    from deadlockforge.api.app import create_app
    from deadlockforge.storage.db import Database

    _client = TestClient(create_app())
    _HAS_APP = True
except Exception:  # noqa: BLE001 — зависимости недоступны
    _HAS_APP = False
    _client = None

pytest_app = pytest.mark.skipif(not _HAS_APP, reason="app not importable")


@pytest_app
def test_hotkey_get_without_gui():
    r = _client.get("/api/desktop/hotkey")
    assert r.status_code == 200
    d = r.json()
    assert d["supported"] is False
    assert d["active"] is False
    assert isinstance(d["hotkey"], str) and d["hotkey"]
    assert "always_on_top" in d


@pytest_app
def test_hotkey_post_invalid():
    r = _client.post("/api/desktop/hotkey", json={"hotkey": "f1"})
    assert r.status_code == 422


@pytest_app
def test_hotkey_post_persists(tmp_path, monkeypatch):
    import importlib

    # import ... as app_mod дал бы FastAPI-инстанс (пакет api реэкспортит
    # app из app.py) — берём настоящий модуль через importlib.
    app_mod = importlib.import_module("deadlockforge.api.app")

    db = Database(tmp_path / "hotkey.db")
    monkeypatch.setattr(app_mod, "_db", lambda: db)
    client = TestClient(create_app())

    r = client.post("/api/desktop/hotkey", json={"hotkey": "ctrl+shift+f2"})
    assert r.status_code == 200
    assert r.json()["hotkey"] == "Ctrl+Shift+F2"

    got = client.get("/api/desktop/hotkey").json()
    assert got["hotkey"] == "Ctrl+Shift+F2"

    assert db.get_setting("hotkey") == "Ctrl+Shift+F2"


@pytest_app
def test_panel_endpoints_without_gui():
    """В браузере панелью управляет только оболочка — supported: False."""
    r = _client.get("/api/desktop/panel")
    assert r.status_code == 200
    assert r.json() == {"supported": False, "visible": False, "by_hotkey": False}

    r = _client.post("/api/desktop/panel", json={"visible": True})
    assert r.status_code == 200
    assert r.json()["visible"] is False


@pytest_app
def test_topmost_without_gui():
    """В браузере закреп окна невозможен, но состояние тумблера читается."""
    r = _client.get("/api/desktop/topmost")
    assert r.status_code == 200
    d = r.json()
    assert d["supported"] is False
    assert isinstance(d["enabled"], bool)


@pytest_app
def test_topmost_post_persists(tmp_path, monkeypatch):
    """Тумблер «Поверх других окон» хранится в app_settings."""
    import importlib

    app_mod = importlib.import_module("deadlockforge.api.app")
    db = Database(tmp_path / "topmost.db")
    monkeypatch.setattr(app_mod, "_db", lambda: db)
    client = TestClient(create_app())

    r = client.post("/api/desktop/topmost", json={"enabled": False})
    assert r.status_code == 200
    assert r.json()["enabled"] is False
    assert db.get_setting("always_on_top") == "0"
    assert client.get("/api/desktop/hotkey").json()["always_on_top"] is False

    r = client.post("/api/desktop/topmost", json={"enabled": True})
    assert r.status_code == 200
    assert db.get_setting("always_on_top") == "1"
    assert client.get("/api/desktop/hotkey").json()["always_on_top"] is True