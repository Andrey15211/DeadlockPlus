"""In-app update engine for Deadlock Plus via GitHub Releases.

Designed with clean architecture, zero external runtime dependencies (Python stdlib),
safe rate-limit caching, and Windows-safe executable replacement.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# Primary repository and version
DEFAULT_REPO = "Andrey15211/DeadlockPlus"
CURRENT_VERSION = "1.2.0"

# Rate limit prevention: cache successful/failed check for 15 minutes unless forced
CACHE_TTL_SECONDS = 900


def get_repo_slug() -> str:
    """GitHub repo slug (owner/repo), overridable via DEADLOCK_UPDATE_REPO."""
    return os.environ.get("DEADLOCK_UPDATE_REPO", DEFAULT_REPO).strip()


def parse_semver(version_str: str) -> Tuple[int, ...]:
    """Parse semver string like 'v1.2.3' or '1.2.3' into a numeric tuple.

    Strips leading 'v' and suffix pre-release tags for primary comparison.
    """
    clean = re.sub(r"^[vV]", "", (version_str or "").strip())
    # Separate core version from build/pre-release metadata (e.g., 1.0.1-beta -> 1.0.1)
    core = clean.split("-")[0].split("+")[0]
    parts = []
    for segment in core.split("."):
        try:
            parts.append(int(segment))
        except ValueError:
            break
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])


def is_newer_version(latest_str: str, current_str: str = CURRENT_VERSION) -> bool:
    """Return True if latest_str is strictly greater than current_str."""
    return parse_semver(latest_str) > parse_semver(current_str)


class UpdateManager:
    """Thread-safe state machine for update checking, downloading, and applying."""

    def __init__(self, repo_slug: Optional[str] = None, current_version: str = CURRENT_VERSION):
        self.repo_slug = repo_slug or get_repo_slug()
        self.current_version = current_version
        self._lock = threading.Lock()

        # Cache
        self._last_check_time: float = 0.0
        self._cached_check: Optional[Dict[str, Any]] = None

        # Download state
        self._download_thread: Optional[threading.Thread] = None
        self._download_state: Dict[str, Any] = {
            "status": "idle",  # idle, downloading, ready, error
            "progress_pct": 0,
            "downloaded_bytes": 0,
            "total_bytes": 0,
            "speed_bps": 0,
            "target_file": None,
            "error": None,
        }

    def check_for_updates(self, force: bool = False) -> Dict[str, Any]:
        """Check GitHub Releases for the latest version with rate-limit caching."""
        with self._lock:
            now = time.time()
            if not force and self._cached_check and (now - self._last_check_time < CACHE_TTL_SECONDS):
                return dict(self._cached_check)

        url = f"https://api.github.com/repos/{self.repo_slug}/releases/latest"
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": f"DeadlockPlus-Updater/{self.current_version}",
                "Accept": "application/vnd.github+json",
            },
        )

        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 404:
                # No releases published yet on this repository
                result = {
                    "update_available": False,
                    "current_version": self.current_version,
                    "latest_version": self.current_version,
                    "release_name": "Актуальная версия",
                    "release_notes": "Официальных релизов пока не опубликовано.",
                    "published_at": None,
                    "asset_url": None,
                    "asset_name": None,
                    "asset_size": 0,
                    "html_url": f"https://github.com/{self.repo_slug}",
                    "error": None,
                    "checked_at": time.time(),
                }
            elif e.code == 403:
                result = {
                    "update_available": False,
                    "current_version": self.current_version,
                    "latest_version": self.current_version,
                    "release_name": "",
                    "release_notes": "",
                    "published_at": None,
                    "asset_url": None,
                    "asset_name": None,
                    "asset_size": 0,
                    "html_url": f"https://github.com/{self.repo_slug}",
                    "error": "Превышен лимит запросов GitHub API (Rate Limit). Попробуйте позже.",
                    "checked_at": time.time(),
                }
            else:
                result = {
                    "update_available": False,
                    "current_version": self.current_version,
                    "latest_version": self.current_version,
                    "release_name": "",
                    "release_notes": "",
                    "published_at": None,
                    "asset_url": None,
                    "asset_name": None,
                    "asset_size": 0,
                    "html_url": f"https://github.com/{self.repo_slug}",
                    "error": f"Ошибка сервера GitHub: HTTP {e.code}",
                    "checked_at": time.time(),
                }
            with self._lock:
                self._cached_check = result
                self._last_check_time = time.time()
            return result
        except Exception as ex:
            logger.warning("Failed to check for updates: %s", ex)
            return {
                "update_available": False,
                "current_version": self.current_version,
                "latest_version": self.current_version,
                "release_name": "",
                "release_notes": "",
                "published_at": None,
                "asset_url": None,
                "asset_name": None,
                "asset_size": 0,
                "html_url": f"https://github.com/{self.repo_slug}",
                "error": f"Сетевая ошибка: {ex}",
                "checked_at": time.time(),
            }

        tag_name = data.get("tag_name", "").strip()
        has_update = is_newer_version(tag_name, self.current_version)

        # Select matching asset (.exe for Windows)
        assets = data.get("assets", [])
        chosen_asset = None
        for asset in assets:
            name = asset.get("name", "").lower()
            if name.endswith(".exe") or name.endswith(".zip"):
                chosen_asset = asset
                if "deadlock" in name:
                    break

        result = {
            "update_available": has_update,
            "current_version": self.current_version,
            "latest_version": tag_name.lstrip("vV") if tag_name else self.current_version,
            "release_name": data.get("name") or tag_name or "Новый релиз",
            "release_notes": data.get("body") or "Список изменений не указан.",
            "published_at": data.get("published_at"),
            "asset_url": chosen_asset.get("browser_download_url") if chosen_asset else None,
            "asset_name": chosen_asset.get("name") if chosen_asset else None,
            "asset_size": chosen_asset.get("size", 0) if chosen_asset else 0,
            "html_url": data.get("html_url") or f"https://github.com/{self.repo_slug}/releases",
            "error": None,
            "checked_at": time.time(),
        }

        with self._lock:
            self._cached_check = result
            self._last_check_time = time.time()

        return result

    def get_download_status(self) -> Dict[str, Any]:
        """Return snapshot of the current download state."""
        with self._lock:
            return dict(self._download_state)

    def start_download(self, asset_url: str, asset_name: Optional[str] = None) -> Dict[str, Any]:
        """Initiate background streaming download of the update artifact."""
        with self._lock:
            if self._download_state["status"] == "downloading":
                return {"status": "downloading", "message": "Загрузка уже выполняется"}

            if not asset_name:
                asset_name = asset_url.split("/")[-1] or "DeadlockPlus_Update.exe"

            target_dir = Path(tempfile.gettempdir()) / "DeadlockPlus_Update"
            target_dir.mkdir(parents=True, exist_ok=True)
            target_file = target_dir / asset_name

            self._download_state = {
                "status": "downloading",
                "progress_pct": 0,
                "downloaded_bytes": 0,
                "total_bytes": 0,
                "speed_bps": 0,
                "target_file": str(target_file),
                "error": None,
            }

            self._download_thread = threading.Thread(
                target=self._run_download,
                args=(asset_url, target_file),
                daemon=True,
            )
            self._download_thread.start()

            return dict(self._download_state)

    def _run_download(self, url: str, target_file: Path) -> None:
        """Worker thread for downloading artifact with speed & progress calculation."""
        req = urllib.request.Request(
            url,
            headers={"User-Agent": f"DeadlockPlus-Updater/{self.current_version}"},
        )
        temp_partial = target_file.with_suffix(target_file.suffix + ".part")

        try:
            with urllib.request.urlopen(req, timeout=30) as resp, open(temp_partial, "wb") as out_f:
                total_bytes = int(resp.headers.get("Content-Length", 0))
                with self._lock:
                    self._download_state["total_bytes"] = total_bytes

                downloaded = 0
                start_time = time.time()
                last_calc_time = start_time
                bytes_since_last_calc = 0
                chunk_size = 64 * 1024

                while True:
                    chunk = resp.read(chunk_size)
                    if not chunk:
                        break
                    out_f.write(chunk)
                    downloaded += len(chunk)
                    bytes_since_last_calc += len(chunk)

                    now = time.time()
                    elapsed_calc = now - last_calc_time
                    if elapsed_calc >= 0.5:
                        speed = bytes_since_last_calc / elapsed_calc
                        pct = int((downloaded / total_bytes * 100)) if total_bytes > 0 else 0
                        with self._lock:
                            self._download_state["downloaded_bytes"] = downloaded
                            self._download_state["progress_pct"] = min(pct, 99)
                            self._download_state["speed_bps"] = int(speed)
                        last_calc_time = now
                        bytes_since_last_calc = 0

            # Atomic rename once download is 100% complete
            if temp_partial.is_file():
                if target_file.is_file():
                    target_file.unlink()
                temp_partial.rename(target_file)

            with self._lock:
                self._download_state["status"] = "ready"
                self._download_state["progress_pct"] = 100
                self._download_state["downloaded_bytes"] = downloaded
                self._download_state["target_file"] = str(target_file)
                self._download_state["speed_bps"] = 0

        except Exception as ex:
            logger.error("Download failed: %s", ex, exc_info=True)
            if temp_partial.is_file():
                try:
                    temp_partial.unlink()
                except OSError:
                    pass
            with self._lock:
                self._download_state["status"] = "error"
                self._download_state["error"] = str(ex)

    def apply_update_and_restart(self) -> Dict[str, Any]:
        """Apply downloaded update using a detached Windows batch script and restart.

        In dev mode (running from Python interpreter), skips killing current process
        and returns informative message.
        """
        with self._lock:
            state = dict(self._download_state)

        if state.get("status") != "ready" or not state.get("target_file"):
            return {
                "success": False,
                "message": "Обновление ещё не загружено или произошла ошибка.",
            }

        new_exe_path = Path(state["target_file"])
        if not new_exe_path.is_file():
            return {
                "success": False,
                "message": f"Файл обновления не найден на диске: {new_exe_path}",
            }

        is_frozen = getattr(sys, "frozen", False)
        current_exe = Path(sys.executable).resolve()

        if not is_frozen:
            # Dev environment: do not kill Python development server
            return {
                "success": True,
                "dev_mode": True,
                "message": (
                    f"Режим разработки: автозамена EXE отключена. "
                    f"Обновленный файл сохранён: {new_exe_path}"
                ),
            }

        # Production PyInstaller EXE environment:
        # Generate updater helper script in %TEMP%
        updater_bat = Path(tempfile.gettempdir()) / "deadlock_plus_updater.bat"
        script_content = f"""@echo off
chcp 65001 > nul
title Обновление Deadlock Plus...
echo.
echo ===================================================
echo     Deadlock Plus — Применение обновления
echo ===================================================
echo.
echo Ожидание завершения текущей программы...
timeout /t 2 /nobreak > nul

:retry_copy
copy /y "{new_exe_path}" "{current_exe}" > nul 2>&1
if errorlevel 1 (
    echo Файл ещё занят, повтор через 1 секунду...
    timeout /t 1 /nobreak > nul
    goto retry_copy
)

echo Обновление успешно скопировано!
echo Запуск обновленного Deadlock Plus...
del "{new_exe_path}" > nul 2>&1
start "" "{current_exe}"

echo Готово.
del "%~f0" > nul 2>&1
exit
"""
        try:
            updater_bat.write_text(script_content, encoding="utf-8")
        except Exception as ex:
            return {"success": False, "message": f"Не удалось создать скрипт обновления: {ex}"}

        # Spawn detached updater process
        try:
            DETACHED_PROCESS = 0x00000008
            CREATE_NEW_PROCESS_GROUP = 0x00000200
            flags = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
            subprocess.Popen(["cmd.exe", "/c", str(updater_bat)], creationflags=flags, close_fds=True)
        except Exception as ex:
            return {"success": False, "message": f"Не удалось запустить процесс обновления: {ex}"}

        # Schedule quick clean exit in background thread
        threading.Thread(target=lambda: (time.sleep(0.5), os._exit(0)), daemon=True).start()

        return {"success": True, "dev_mode": False, "message": "Приложение перезапускается..."}


# Global singleton instance
updater = UpdateManager()
