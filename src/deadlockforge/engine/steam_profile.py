"""Steam profile autodetection (TZ A1).

Public surface:
    detect() -> dict | None
        Steam autodetected candidates: steamid64, account_id, steam_id32,
        persona name, source description. None when Steam is not installed
        or no user info is available.

Detection order:
    1. loginusers.vdf parsed for accounts with MostRecent=1 / RememberMe;
    2. registry HKCU\\Software\\Valve\\Steam\\AutoLoginUser (account name) +
       Steam\\SteamPath for loginusers.vdf fallback;
    3. conventional Steam install roots for loginusers.vdf.

Only stdlib (winreg guarded by platform check, pathlib, urllib is NOT used —
no network here). Never raises: every failure narrows to None.
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

STEAMID64_OFFSET = 76561197960265728  # 0x0110000100000000

RECENT_MATCHES_API = (
    "https://api.deadlock-api.com/v1/players/{account_id}/match-history"
)
RECENT_MATCHES_TTL = 3600  # 60 минут (TZ v1.4 D3)

STEAM_INFO_API = "https://api.deadlock-api.com/v1/players/steam"
RANK_API = "https://api.deadlock-api.com/v1/players/{account_id}/rank"
AVATAR_TTL = 86400  # 24 часа (TZ v1.4 D2)


def _api_headers() -> dict[str, str]:
    headers = {
        "User-Agent": "Mozilla/5.0 DeadlockForge/1.2",
        "Accept": "application/json",
    }
    key = os.environ.get("DEADLOCK_API_KEY", "").strip()
    if key:
        headers["Authorization"] = f"Bearer {key}"
    return headers


class RateLimitError(RuntimeError):
    """429 от deadlock-api: лимит запросов (3/час). detail — человекочитаемо."""

    def __init__(self, wait_seconds: float | None) -> None:
        self.wait_seconds = wait_seconds
        if wait_seconds and wait_seconds > 0:
            mins = max(1, round(wait_seconds / 60))
            super().__init__(
                f"Лимит API (3 запроса/час). Повторите через {mins} мин.")
        else:
            super().__init__("Лимит API (3 запроса/час). Повторите позже.")


def _parse_429_wait(body: str) -> float | None:
    """Выделить next_request_in из JSON-тела 429. None если поля нет."""
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, TypeError):
        return None
    for key in ("next_request_in", "retry_in", "retry_after"):
        v = data.get(key) if isinstance(data, dict) else None
        if isinstance(v, (int, float)):
            return float(v)
    return None


def _read_http_error(exc: urllib.error.HTTPError) -> str:
    try:
        return exc.read().decode("utf-8", "replace")[:500]
    except Exception:  # noqa: BLE001 — best-effort body
        return ""


def _urlopen_json(url: str, timeout: float) -> dict | list:
    """GET с UA; 429 → RateLimitError, прочие HTTP — RuntimeError."""
    req = urllib.request.Request(url, headers=_api_headers())
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        body = _read_http_error(exc)
        if exc.code == 429:
            raise RateLimitError(_parse_429_wait(body)) from exc
        raise RuntimeError(
            f"deadlock-api HTTP {exc.code}: {body[:200]}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise RuntimeError(f"запрос к deadlock-api не удался: {exc}") from exc
    return json.loads(raw)

_VDF_ACCOUNT_RE = re.compile(r'"(\d{17})"\s*\{')
_VDF_STR_RE = re.compile(r'"(\w+)"\s*"([^"]*)"')

_LOGINUSERS_CANDIDATES = [
    Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"))
    / "Steam" / "config" / "loginusers.vdf",
    Path("C:/Program Files/Steam/config/loginusers.vdf"),
    Path("C:/SteamLibrary/Steam/config/loginusers.vdf"),
]


def _steam_roots() -> list[Path]:
    roots: list[Path] = []
    if sys.platform == "win32":
        try:
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam"
            ) as key:
                path, _ = winreg.QueryValueEx(key, "SteamPath")
                roots.append(Path(path) / "config" / "loginusers.vdf")
        except OSError:
            pass
    roots.extend(_LOGINUSERS_CANDIDATES)
    return roots


def _parse_loginusers(text: str) -> list[dict]:
    """Parse loginusers.vdf into a list of per-account field dicts."""
    accounts: list[dict] = []
    for block_match in re.finditer(r'"(\d{17})"\s*\{([^{}]*)\}', text):
        steamid64, body = block_match.group(1), block_match.group(2)
        fields = {"steamid64": steamid64}
        for key, value in _VDF_STR_RE.findall(body):
            if key in ("AccountName", "PersonaName", "MostRecent",
                       "Remembered", "MostRecent2"):
                fields[key] = value
        accounts.append(fields)
    return accounts


def _read_loginusers() -> list[dict]:
    for path in _steam_roots():
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        accounts = _parse_loginusers(text)
        if accounts:
            return accounts
    return []


def _autologin_account_name() -> str | None:
    if sys.platform != "win32":
        return None
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam"
        ) as key:
            value, _ = winreg.QueryValueEx(key, "AutoLoginUser")
            return str(value) or None
    except OSError:
        return None


def account_id_from_steamid64(steamid64: int) -> int:
    return steamid64 - STEAMID64_OFFSET


def steam_id32(account_id: int) -> str:
    return f"STEAM_1:{account_id % 2}:{account_id // 2}"


def detect() -> dict | None:
    """Best-effort active Steam user. None when nothing found.

    Priority: MostRecent=1 (then MostRecent2=1), then Remembered with a
    matching AutoLoginUser registry name, then first remembered account.
    """
    accounts = _read_loginusers()
    if not accounts:
        return None

    def _pick(predicate) -> dict | None:
        for acc in accounts:
            if predicate(acc):
                return acc
        return None

    chosen = (
        _pick(lambda a: a.get("MostRecent") == "1" and a.get("Remembered") == "1")
        or _pick(lambda a: a.get("MostRecent") == "1")
        or _pick(lambda a: a.get("MostRecent2") == "1" and a.get("Remembered") == "1")
    )
    if chosen is None:
        autologin = _autologin_account_name()
        if autologin:
            chosen = _pick(lambda a: a.get("AccountName", "").lower() == autologin.lower())
    if chosen is None:
        chosen = _pick(lambda a: a.get("Remembered") == "1")
    if chosen is None:
        chosen = accounts[0]

    try:
        steamid64 = int(chosen["steamid64"])
    except (KeyError, ValueError):
        return None
    account_id = account_id_from_steamid64(steamid64)
    return {
        "steamid64": str(steamid64),
        "account_id": str(account_id),
        "steam_id32": steam_id32(account_id),
        "persona_name": chosen.get("PersonaName") or chosen.get("AccountName") or "",
        "account_name": chosen.get("AccountName") or "",
        "source": "loginusers.vdf" if len(accounts) > 0 else "registry",
        "candidates": [
            {
                "steamid64": acc.get("steamid64", ""),
                "account_id": str(account_id_from_steamid64(int(acc["steamid64"])))
                if acc.get("steamid64", "").isdigit() else "",
                "persona_name": acc.get("PersonaName") or acc.get("AccountName") or "",
                "account_name": acc.get("AccountName") or "",
                "most_recent": acc.get("MostRecent") == "1",
            }
            for acc in accounts
        ],
    }


def fetch_steam_info(account_id: int | str, timeout: float = 15.0) -> dict:
    """GET /v1/players/steam?account_ids={id} — ник + аватар (без ключа).

    Возвращает dict {account_id, persona_name, avatar} либо {found: False}.
    Аватар — прямой URL (каширование делает вызывающая сторона, D2).
    refresh=true заставляет API обновить профиль из Steam Web API.
    """
    url = (f"{STEAM_INFO_API}?account_ids={int(account_id)}"
           "&refresh=true")
    data = _urlopen_json(url, timeout)
    if isinstance(data, list) and data:
        entry = data[0]
        return {
            "found": True,
            "account_id": entry.get("account_id"),
            "persona_name": entry.get("personaname") or "",
            "avatar": entry.get("avatarfull") or entry.get("avatarmedium")
            or entry.get("avatar") or "",
        }
    return {"found": False, "account_id": int(account_id)}


def fetch_rank(account_id: int | str, timeout: float = 15.0) -> dict:
    """GET /v1/players/{account_id}/rank → rank, subrank, badge, last_match.

    При rank:0 и last_match:null фронтенд получает rank_hidden: true —
    пользователь скрыл матч-данные (см. плашку в UI, TZ v1.4 D2).
    """
    url = RANK_API.format(account_id=account_id)
    data = _urlopen_json(url, timeout)
    if not isinstance(data, dict):
        raise RuntimeError("rank: unexpected payload shape")
    rank = data.get("rank") or 0
    subrank = data.get("subrank") or 0
    last_match = data.get("last_match")
    return {
        "rank": rank,
        "subrank": subrank,
        "badge": data.get("badge") or "",
        "last_match": last_match,
        "rank_hidden": rank == 0 and last_match is None,
    }


class RecentMatchesError(RuntimeError):
    """Raised when the recent-matches API call cannot be completed."""


def download_avatar(url: str, timeout: float = 15.0) -> bytes | None:
    """Скачать аватар (jpg). None при сбое — UI покажет буквенную заглушку."""
    if not url:
        return None
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 DeadlockForge/1.2"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except (urllib.error.HTTPError, urllib.error.URLError,
            TimeoutError, OSError):
        return None


def fetch_recent_matches(account_id: int | str,
                         timeout: float = 30.0) -> list[dict]:
    """GET /v1/players/{account_id}/match-history.

    Возвращает список последних матчей (поля: hero_id, match_id, start_time,
    player_kills/deaths/assists, player_match_outcome: 1=win, иначе loss).
    Raises RecentMatchesError.
    """
    url = RECENT_MATCHES_API.format(account_id=account_id)
    req = urllib.request.Request(url, headers=_api_headers())
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read().decode("utf-8", "replace")[:300]
        except Exception:  # noqa: BLE001 — best-effort body
            body = ""
        if exc.code == 429:
            raise RateLimitError(_parse_429_wait(body)) from exc
        raise RecentMatchesError(
            f"deadlock-api HTTP {exc.code}: {body}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise RecentMatchesError(
            f"recent matches request failed: {exc}") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RecentMatchesError("recent matches: non-JSON body") from exc
    if not isinstance(data, list):
        raise RecentMatchesError("recent matches: unexpected payload shape")
    return data
