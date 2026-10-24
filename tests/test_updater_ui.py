import socket
import threading
import time
import pytest
import uvicorn
from playwright.sync_api import sync_playwright
from deadlockforge.api.app import create_app


def find_free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def live_server():
    port = find_free_port()
    config = uvicorn.Config(create_app(), host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    time.sleep(1.2)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True


def test_updater_ui_flow(live_server):
    errors = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(live_server)
        page.wait_for_selector(".hud-header")

        # Open Profile tab
        page.click("button[data-tab='profile']")
        page.wait_for_selector(".update-panel")

        # Check version badge
        badge = page.locator("#profile-version-badge")
        assert "v1.0.0" in badge.inner_text()

        # Check update button
        check_btn = page.locator("#btn-check-updates")
        assert check_btn.is_visible()

        # Check modal structure exists and initially hidden
        modal = page.locator("#update-modal")
        assert "hidden" in (modal.get_attribute("class") or "")

        browser.close()

    assert errors == []
