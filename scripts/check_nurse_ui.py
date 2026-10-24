"""Проверка, что Медсестра Хэрроу везде отображается портретом, а не буквой.

Поднимает реальный uvicorn-сервер и обходит экраны, где герой рисуется
картинкой: тир-лист, пик-ассистент, контрпики, билд-лаб. Для каждой точки
проверяется, что <img> реально загрузился (naturalWidth > 0), а не упал в
fallback-букву. Скриншоты — в docs/nurse/.

Usage:
    python scripts/check_nurse_ui.py
"""

from __future__ import annotations

import json
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

OUT_DIR = ROOT / "docs" / "nurse"
OUT_DIR.mkdir(parents=True, exist_ok=True)
NAME = "Медсестра Хэрроу"
SLUG = "nurse"


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


def image_state(locator) -> dict | None:
    """Состояние <img> внутри локатора: src и реальные размеры загруженного файла."""
    if locator.count() == 0:
        return None
    return locator.first.evaluate(
        """(img) => ({
            src: img.getAttribute('src'),
            complete: img.complete,
            width: img.naturalWidth,
            height: img.naturalHeight,
        })"""
    )


def main() -> int:
    port = find_free_port()
    server = uvicorn.Server(
        uvicorn.Config(create_app(), host="127.0.0.1", port=port, log_level="error")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    wait_for_port(port)

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1500, "height": 1000})
            page.goto(f"http://127.0.0.1:{port}", wait_until="domcontentloaded")
            page.wait_for_selector(".hud-header")

            # 1. Тир-лист
            page.click('[data-tab="tier"]')
            page.wait_for_selector(".tier-row")
            page.wait_for_timeout(600)
            row = page.locator(".tier-row", has_text=NAME).first
            check(row.count() == 1, "Медсестра Хэрроу есть в тир-листе")
            state = image_state(row.locator("img.tier-portrait"))
            check(
                bool(state) and state["width"] > 0 and state["complete"],
                f"портрет в тир-листе загружен ({state})",
            )
            row.scroll_into_view_if_needed()
            page.screenshot(path=str(OUT_DIR / "tierlist.png"))

            # 2. Пик-ассистент: карточка героя в сетке
            page.click('[data-tab="draft"]')
            page.wait_for_selector(".hero-cell")
            page.wait_for_timeout(600)
            cell = page.locator(f'.hero-cell[data-hero-id="{SLUG}"]')
            check(cell.count() == 1, "карточка героя есть в пик-ассистенте")
            state = image_state(cell.locator("img"))
            check(
                bool(state) and state["width"] > 0,
                f"портрет в пик-ассистенте загружен ({state})",
            )
            check(
                page.locator(f'.hero-cell[data-hero-id="{SLUG}"] .hc-fallback').count() == 0,
                "в пик-ассистенте нет fallback-заглушки",
            )
            cell.first.scroll_into_view_if_needed()
            page.screenshot(path=str(OUT_DIR / "draft.png"))

            # 3. Билд-лаб: селект героев
            page.click('[data-tab="lab"]')
            page.wait_for_timeout(700)
            options = page.evaluate(
                "() => [...document.querySelector('#lab-hero').options].map(o => o.textContent)"
            )
            check(
                any(NAME in opt for opt in options),
                "герой доступен в селекте билд-лаба",
            )

            # 4. Контрпики: карточка + честное сообщение об отсутствии гайда
            page.click('[data-tab="counterpicks"]')
            page.wait_for_selector(".cp-hero-card")
            page.wait_for_timeout(600)
            card = page.locator(".cp-hero-card", has_text=NAME).first
            check(card.count() == 1, "карточка героя есть в контрпиках")
            card.locator(".cp-hero-open").click()
            page.wait_for_selector(".cp-guide")
            page.wait_for_timeout(300)
            guide = page.locator(".cp-guide").first.inner_text()
            check("не собран" in guide, "гайд честно сообщает, что разбора нет")
            page.screenshot(path=str(OUT_DIR / "counterpicks.png"))

            # 5. Выбор противником: чип с портретом
            page.locator(".cp-guide-close").click()
            page.wait_for_timeout(200)
            card.locator(".cp-pick-mark").click()
            page.wait_for_timeout(300)
            chip = page.locator(".cp-selected-chip", has_text=NAME).first
            check(chip.count() == 1, "героя можно выбрать противником")
            state = image_state(chip.locator("img"))
            check(
                bool(state) and state["width"] > 0,
                f"портрет в чипе противника загружен ({state})",
            )

            # 6. Deep Match: пикер героев по составу матча. Ростер подменяем —
            # сеть и API-ключ для проверки отрисовки не нужны. Идём оффлайн-
            # путём: кнопка LLM-разбора без ключа отказывает ещё до ростера.
            roster = {
                "match_id": "999999",
                "duration_s": 2100,
                "players": [
                    {
                        "account_id": 1000 + i,
                        "hero_slug": slug,
                        "hero_name": hero_name,
                        "team": i // 6,
                        "kills": i,
                        "deaths": i,
                        "assists": i,
                        "net_worth": 20000 + i,
                        "level": 20,
                    }
                    for i, (slug, hero_name) in enumerate(
                        [("nurse", NAME), ("kelvin", "Кельвин")] * 6
                    )
                ],
            }
            page.route(
                "**/api/match/*/roster",
                lambda route: route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps(roster, ensure_ascii=False),
                ),
            )
            page.click('[data-tab="match"]')
            page.wait_for_selector("#match-id-input")
            page.fill("#match-id-input", "999999")
            page.click("#btn-match-offline")
            page.wait_for_selector(".mhp-card", timeout=15000)
            page.wait_for_timeout(400)
            card = page.locator('.mhp-card[data-hero-slug="nurse"]').first
            check(card.count() == 1, "герой есть в пикере состава Deep Match")
            state = image_state(card.locator("img.mhp-portrait"))
            check(
                bool(state) and state["width"] > 0,
                f"портрет в пикере состава загружен ({state})",
            )
            check(
                card.locator(".mhp-portrait-fallback.hidden").count() == 1,
                "fallback-буква в пикере скрыта",
            )
            page.screenshot(path=str(OUT_DIR / "deep_match_roster.png"))

            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=10)

    print(f"[done] скриншоты: {OUT_DIR}")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
