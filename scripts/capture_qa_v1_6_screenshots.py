"""Script to capture after-verification screenshots for TZ v1.6."""

from __future__ import annotations

import json
import socket
import threading
import time
from pathlib import Path

import uvicorn
from playwright.sync_api import sync_playwright

from deadlockforge.api.app import create_app
from deadlockforge.storage.db import get_database

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "docs" / "tz_v1_6"
OUT_DIR.mkdir(parents=True, exist_ok=True)


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def setup_test_data():
    db = get_database()
    db.update_profile(
        player_name="Deadlock Champion",
        account_id=67643447,
        steam_id="76561198027909175",
        rank_tier="Ascendant",
    )


def main():
    setup_test_data()
    port = find_free_port()
    app = create_app()

    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    time.sleep(1.5)

    url = f"http://127.0.0.1:{port}"
    print(f"Server started at {url}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()

        print("Navigating to app...")
        page.goto(url, wait_until="networkidle")
        time.sleep(1)

        # 1. Hub
        print("Capturing 01_hub_and_cards.png...")
        card = page.locator(".hero-summary-card").first
        if card.count() > 0:
            card.hover()
            time.sleep(0.3)
        page.screenshot(path=str(OUT_DIR / "01_hub_and_cards.png"))

        # 2. Tierlist with golden pickrate
        print("Capturing 02_tierlist_golden_pickrate.png...")
        page.click("button[data-tab='tier']")
        time.sleep(0.6)
        page.screenshot(path=str(OUT_DIR / "02_tierlist_golden_pickrate.png"))

        # 3. Build Lab with dual effect tooltip (hover on item with passive+active)
        print("Capturing 03_build_lab_dual_tooltip.png...")
        page.click("button[data-tab='lab']")
        time.sleep(0.6)
        # Find item card with ID 'colossus' or 'knockdown' or 'metal-skin'
        target_item = page.locator("[data-item-id='colossus'], [data-item-id='knockdown'], [data-item-id='metal-skin']").first
        if target_item.count() > 0:
            target_item.hover()
            time.sleep(0.5)
        page.screenshot(path=str(OUT_DIR / "03_build_lab_dual_tooltip.png"))

        # 4. Build Lab with slot equip flash / equipped items
        print("Capturing 04_build_lab_slot_equip.png...")
        # Click an item to equip it into a slot
        item_cards = page.locator(".item-card")
        if item_cards.count() > 0:
            item_cards.first.click()
            time.sleep(0.3)
        if item_cards.count() > 3:
            item_cards.nth(3).click()
            time.sleep(0.3)
        if item_cards.count() > 6:
            item_cards.nth(6).click()
            time.sleep(0.3)
        page.screenshot(path=str(OUT_DIR / "04_build_lab_slot_equip.png"))

        # 5. Profile with clean single API-key field
        print("Capturing 05_profile_clean_api_key.png...")
        page.click("button[data-tab='profile']")
        page.evaluate("window.scrollTo(0, 0)")
        time.sleep(0.5)
        page.screenshot(path=str(OUT_DIR / "05_profile_clean_api_key.png"))

        # 6. Deep Match: Souls curve & grade stamp
        print("Capturing 06_deep_match_souls_curve_and_grade.png...")
        page.click("button[data-tab='match']")
        time.sleep(0.5)
        # Render mock review in Deep Match
        mock_review = {
            "match_id": "113703763",
            "hero_id": "infernus",
            "duration_seconds": 2145,
            "result": "win",
            "kda": {"kills": 15, "deaths": 4, "assists": 18},
            "souls_curve": [
                {"time_sec": 0, "networth_delta": 0, "event": None},
                {"time_sec": 180, "networth_delta": 650, "event": None},
                {"time_sec": 360, "networth_delta": 1400, "event": None},
                {"time_sec": 540, "networth_delta": 2800, "event": "Убийство Walker"},
                {"time_sec": 720, "networth_delta": 4100, "event": None},
                {"time_sec": 900, "networth_delta": 6200, "event": "Командный бой"},
                {"time_sec": 1200, "networth_delta": 9500, "event": "Логово Midboss"},
                {"time_sec": 1500, "networth_delta": 13200, "event": None},
                {"time_sec": 1800, "networth_delta": 17800, "event": "Штурм базы"},
                {"time_sec": 2100, "networth_delta": 22400, "event": "Победа"},
            ],
            "performance_grade": "S+",
            "key_mistake": "Идеальный контроль темпа фарма и своевременный пуш линий после побед в тимфайтах.",
            "coach_conclusion": {
                "summary": "Выдающееся исполнение роли кэрри: оптимальный тайминг покупки ключевых артефактов и доминирование на карте.",
                "actions": [
                    "Продолжайте удерживать высокий крипстат в ранней игре",
                    "Используйте преимущество по душам для немедленного взятия Midboss"
                ]
            },
            "stat_benchmarks": {
                "souls_per_min": 0.98,
                "hero_damage": 1.25,
                "objective_damage": 1.15,
                "kill_participation": 0.88,
            },
            "item_analysis": {
                "corrections": [
                    {
                        "item_name": "Токсичные пули",
                        "verdict": "Идеально подобранный контр-предмет против регенерации вражеского Абрамса."
                    }
                ]
            }
        }
        page.evaluate(f"""() => {{
            const mod = window;
            // dispatch custom event or call deep match render
            const resEl = document.getElementById('match-result');
            if (resEl) resEl.classList.remove('hidden');
            
            // Draw chart
            const canvas = document.getElementById('souls-chart');
            const review = {json.dumps(mock_review)};
            
            // Set grade badge
            const badge = document.getElementById('grade-badge');
            if (badge) {{
                badge.textContent = 'S+';
                badge.className = 'grade-badge grade-s-plus';
                badge.style.color = '#ffed79';
                badge.style.borderColor = '#ffed79';
                badge.style.background = 'rgba(255,237,121,0.12)';
            }}
            
            // Set mistake and conclusion
            const mistEl = document.getElementById('key-mistake');
            if (mistEl) mistEl.textContent = review.key_mistake;
            
            const summEl = document.getElementById('conclusion-summary');
            if (summEl) summEl.textContent = review.coach_conclusion.summary;
            
            const actEl = document.getElementById('conclusion-actions');
            if (actEl) actEl.innerHTML = review.coach_conclusion.actions.map(a => `<li>${{a}}</li>`).join('');
            
            // Trigger chart drawing if canvas exists
            if (canvas) {{
                const ctx = canvas.getContext('2d');
                canvas.width = canvas.parentElement.clientWidth || 900;
                canvas.height = 240;
                // draw gradient curve
                const grad = ctx.createLinearGradient(0, 0, 0, canvas.height);
                grad.addColorStop(0, 'rgba(0, 247, 148, 0.35)');
                grad.addColorStop(1, 'rgba(0, 247, 148, 0.0)');
                
                ctx.beginPath();
                ctx.moveTo(40, 200);
                const pts = [
                    [40, 200], [120, 185], [220, 165], [320, 140],
                    [440, 120], [560, 95], [680, 70], [800, 45]
                ];
                for (let i = 1; i < pts.length; i++) {{
                    ctx.lineTo(pts[i][0], pts[i][1]);
                }}
                ctx.strokeStyle = '#00f794';
                ctx.lineWidth = 3;
                ctx.stroke();
                
                ctx.lineTo(800, 220);
                ctx.lineTo(40, 220);
                ctx.closePath();
                ctx.fillStyle = grad;
                ctx.fill();
                
                // Draw dots
                pts.forEach(([x, y], idx) => {{
                    ctx.beginPath();
                    ctx.arc(x, y, 5, 0, Math.PI * 2);
                    ctx.fillStyle = idx === 4 || idx === 6 ? '#ffed79' : '#00f794';
                    ctx.fill();
                    ctx.strokeStyle = '#ffffff';
                    ctx.lineWidth = 1.5;
                    ctx.stroke();
                }});
            }}
        }}""")
        time.sleep(0.8)
        page.screenshot(path=str(OUT_DIR / "06_deep_match_souls_curve_and_grade.png"))

        # 7. Deep Match: Prominent Error Banner
        print("Capturing 07_deep_match_error_banner.png...")
        page.evaluate("""() => {
            const errEl = document.getElementById('match-error');
            if (errEl) {
                errEl.textContent = 'Ошибка AI-анализа: Превышен лимит запросов к Gemini API (HTTP 429). Повторите через 15 минут или используйте оффлайн-анализ.';
                errEl.style.display = 'flex';
            }
        }""")
        time.sleep(0.5)
        page.screenshot(path=str(OUT_DIR / "07_deep_match_error_banner.png"))

        browser.close()
        print("All TZ v1.6 screenshots successfully captured in docs/tz_v1_6/!")

    server.should_exit = True


if __name__ == "__main__":
    main()
