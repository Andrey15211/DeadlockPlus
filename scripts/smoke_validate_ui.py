import asyncio
import socket
import uvicorn
import threading
import time
from playwright.async_api import async_playwright
from deadlockforge.api.app import create_app

def find_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]

def main():
    port = find_port()
    server = uvicorn.Server(uvicorn.Config(create_app(), host='127.0.0.1', port=port, log_level='error'))
    th = threading.Thread(target=server.run, daemon=True)
    th.start()
    time.sleep(1.5)

    async def run():
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            page = await browser.new_page(viewport={'width': 1440, 'height': 900})
            await page.goto(f'http://127.0.0.1:{port}')
            await page.wait_for_selector('.hud-header')

            # 1. Build Lab & 36 slots
            await page.click('[data-tab="lab"]')
            await page.wait_for_selector('#slots-weapon')
            
            badge = await page.text_content('#slot-count')
            print(f'Initial slot count: {badge.strip()}')
            assert '0/36' in badge

            weapon_slots = await page.query_selector_all('#slots-weapon .slot')
            vitality_slots = await page.query_selector_all('#slots-vitality .slot')
            spirit_slots = await page.query_selector_all('#slots-spirit .slot')
            print(f'Slots counts: weapon={len(weapon_slots)}, vitality={len(vitality_slots)}, spirit={len(spirit_slots)}')
            assert len(weapon_slots) == 12
            assert len(vitality_slots) == 12
            assert len(spirit_slots) == 12

            # Hover over first item for authentic Statlocker tooltip
            first_item = await page.wait_for_selector('.item-card')
            await first_item.hover()
            await page.wait_for_timeout(600)
            await page.screenshot(path='docs/tz_v1_5/final/01_build_lab_and_tooltip.png')
            print('Captured: docs/tz_v1_5/final/01_build_lab_and_tooltip.png')

            # Equip item and check counter
            await first_item.click()
            await page.wait_for_timeout(300)
            badge_after_equip = await page.text_content('#slot-count')
            print(f'Slot count after equip: {badge_after_equip.strip()}')
            assert '1/36' in badge_after_equip
            await page.screenshot(path='docs/tz_v1_5/final/02_upgrade_swapping.png')
            print('Captured: docs/tz_v1_5/final/02_upgrade_swapping.png')

            # 2. Deep Match strict key validation
            await page.click('[data-tab="match"]')
            await page.wait_for_selector('#btn-match-analyze')
            await page.fill('#match-id-input', '123456')
            await page.click('#btn-match-analyze')
            await page.wait_for_timeout(500)
            err_text = await page.text_content('#match-error')
            print(f'Match error without key: {err_text.strip()}')
            assert 'API-ключ' in err_text
            await page.screenshot(path='docs/tz_v1_5/final/03_deep_match_key_required.png')
            print('Captured: docs/tz_v1_5/final/03_deep_match_key_required.png')

            # 3. Profile & Multi-vendor AI
            await page.click('[data-tab="profile"]')
            await page.wait_for_selector('#ai-provider')
            providers = await page.eval_on_selector('#ai-provider', 'sel => Array.from(sel.options).map(o => o.value)')
            print(f'AI Providers available: {providers}')
            # 4. Item catalog visual audit screenshots (Vitality, Weapon, Spirit)
            await page.click('[data-tab="lab"]')
            await page.wait_for_selector('#item-grid')
            await page.locator('#item-grid').scroll_into_view_if_needed()

            # Vitality
            await page.select_option('#filter-category', 'vitality')
            await page.wait_for_timeout(400)
            await page.screenshot(path='docs/tz_v1_5/final/05_all_color_item_catalog.png')
            print('Captured: docs/tz_v1_5/final/05_all_color_item_catalog.png')

            # Weapon
            await page.select_option('#filter-category', 'weapon')
            await page.wait_for_timeout(400)
            await page.screenshot(path='docs/tz_v1_5/final/06_all_color_weapon_catalog.png')
            print('Captured: docs/tz_v1_5/final/06_all_color_weapon_catalog.png')

            # Spirit
            await page.select_option('#filter-category', 'spirit')
            await page.wait_for_timeout(400)
            await page.screenshot(path='docs/tz_v1_5/final/07_all_color_spirit_catalog.png')
            print('Captured: docs/tz_v1_5/final/07_all_color_spirit_catalog.png')

            await browser.close()

    asyncio.run(run())
    server.should_exit = True

if __name__ == '__main__':
    main()
