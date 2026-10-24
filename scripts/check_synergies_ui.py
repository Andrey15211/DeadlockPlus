"""Визуальная и поведенческая проверка экрана «Синергии».

Поднимает реальный uvicorn-сервер, открывает SPA в Chromium (Playwright),
кликает по новой вкладке и проверяет: категория видна, строки отрисованы,
имена героев русские, работают фильтр по сорту, поиск, сортировка и
«Показать ещё». Скриншоты складываются в docs/synergies/.

Usage:
    python scripts/check_synergies_ui.py
"""

from __future__ import annotations

import socket
import sys
import threading
import time
from pathlib import Path

import uvicorn
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from deadlockforge.api.app import create_app  # noqa: E402

OUT_DIR = ROOT / "docs" / "synergies"
OUT_DIR.mkdir(parents=True, exist_ok=True)


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_for_port(port: int, timeout: float = 20.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.5)
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.2)
    raise RuntimeError(f"server on port {port} did not start")


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)
    print(f"[ok] {message}")


def main() -> int:
    port = find_free_port()
    server = uvicorn.Server(
        uvicorn.Config(create_app(), host="127.0.0.1", port=port, log_level="error")
    )
    server_thread = threading.Thread(target=server.run, daemon=True)
    server_thread.start()
    wait_for_port(port)
    url = f"http://127.0.0.1:{port}"

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1500, "height": 1000})
            page.goto(url, wait_until="domcontentloaded")
            page.wait_for_selector(".hud-header")

            tab = page.locator('[data-tab="synergies"]')
            check(tab.count() == 1, "в навигации есть вкладка «Синергии»")
            tab.click()
            page.wait_for_selector("#tab-synergies:not(.hidden)")
            page.wait_for_selector(".syn-row", timeout=20000)

            check(
                "tab-active" in (tab.get_attribute("class") or ""),
                "вкладка «Синергии» становится активной",
            )
            check(
                page.locator("#tab-synergies").is_visible(),
                "панель синергий видима после клика",
            )

            rows = page.locator(".syn-row")
            check(rows.count() == 40, f"первая страница — 40 строк (факт: {rows.count()})")

            first = rows.first.inner_text()
            check("Грейвс" in first and "Кельвин" in first, "имена героев на русском")
            check("62.4%" in first, "винрейт дуо отображается (62.4%)")
            check("S" in first, "сорт дуо отображается")

            count_text = page.locator("#syn-count").inner_text()
            check(count_text == "946 дуо", f"счётчик дуо: {count_text}")
            check(
                page.locator("#syn-meta-date").inner_text().startswith("данные:"),
                "дата сбора данных выводится",
            )
            check(
                page.locator("#syn-method-list li").count() >= 3,
                "блок «Как считаются данные» переведён и заполнен",
            )

            # Скриншот вида по умолчанию — до всех фильтров и сортировок.
            page.wait_for_timeout(300)
            page.locator("#tab-synergies").screenshot(
                path=str(OUT_DIR / "synergies_section.png")
            )
            page.screenshot(path=str(OUT_DIR / "synergies_tab.png"), full_page=True)

            # Фильтр по сорту
            page.click('[data-syn-tier="S"]')
            page.wait_for_timeout(200)
            tiers = page.locator(".syn-row").evaluate_all(
                "els => els.map(e => e.dataset.tier)"
            )
            check(len(tiers) > 0 and set(tiers) == {"S"}, "фильтр S оставляет только S-дуо")
            page.screenshot(path=str(OUT_DIR / "synergies_tier_s.png"))

            # Поиск по русскому имени
            page.click('[data-syn-tier=""]')
            page.fill("#syn-search", "Кельвин")
            page.wait_for_timeout(200)
            texts = page.locator(".syn-row").all_inner_texts()
            check(
                len(texts) > 0 and all("Кельвин" in t for t in texts),
                f"поиск «Кельвин» фильтрует строки ({len(texts)} совпадений)",
            )

            # Сортировка и догрузка
            page.fill("#syn-search", "")
            page.select_option("#syn-sort", "kda")
            page.wait_for_timeout(200)
            check(page.locator(".syn-row").count() == 40, "сортировка по KDA не ломает список")
            page.click("#syn-more")
            page.wait_for_timeout(200)
            check(page.locator(".syn-row").count() == 80, "«Показать ещё» добавляет 40 строк")

            # Остальные вкладки не сломались новым разделом
            page.click('[data-tab="tier"]')
            page.wait_for_timeout(200)
            check(page.locator("#tab-tier").is_visible(), "вкладка «Тир-лист» работает")
            page.click('[data-tab="synergies"]')
            page.wait_for_timeout(200)
            check(page.locator("#tab-synergies").is_visible(), "возврат на «Синергии» работает")

            browser.close()
    finally:
        server.should_exit = True
        # Дать uvicorn завершить цикл внутри main(): иначе на Windows
        # Proactor-транспорт сыплет ConnectionResetError уже после выхода.
        server_thread.join(timeout=10)

    print(f"[done] скриншоты: {OUT_DIR}")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
