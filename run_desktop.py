"""Desktop entry point: uvicorn in a daemon thread + pywebview window.

Dynamic port (never 8377), graceful server stop after the window closes,
browser fallback when WebView2 is unavailable, and PyInstaller-aware
resource resolution via sys._MEIPASS + DEADLOCK_WEB_DIR/DEADLOCK_DATA_DIR
(those env vars must be set BEFORE the app import).
"""

from __future__ import annotations

import os
import pathlib
import shutil
import socket
import sys
import threading
import webbrowser

ROOT = pathlib.Path(__file__).resolve().parent

# In windowed mode (GUI / PyInstaller), sys.stdout and sys.stderr are None
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")

# Ensure src/ is unconditionally on sys.path regardless of CWD or launch mode
for _cand in [ROOT / "src", ROOT.parent / "src", pathlib.Path.cwd() / "src"]:
    if _cand.is_dir() and str(_cand) not in sys.path:
        sys.path.insert(0, str(_cand))


def resource_dir(relative: str) -> pathlib.Path:
    """Project dir in dev; sys._MEIPASS subfolder in a PyInstaller build."""
    if hasattr(sys, "_MEIPASS"):
        return pathlib.Path(sys._MEIPASS) / relative
    return ROOT / relative


def resolve_persistent_data_dir() -> pathlib.Path | None:
    """Frozen builds keep SQLite next to the exe; None in dev mode.

    _MEIPASS is a temporary dir wiped on every launch, so the database
    must NOT live there. JSON datasets are copied from the bundle into
    the persistent dir on first launch (idempotent overwrite).
    """
    if not hasattr(sys, "_MEIPASS"):
        return None
    bundle = pathlib.Path(sys._MEIPASS) / "data"
    target = pathlib.Path(sys.executable).parent / "data"
    target.mkdir(parents=True, exist_ok=True)
    # JSON-датасеты — read-only данные из бандла: копируем каждый запуск
    # (idempotent overwrite), чтобы обновление меты/ростера из свежей
    # сборки всегда доезжало до exe. SQLite в копировании не участвует.
    for name in (
        "heroes.json",
        "items.json",
        "counter_rules.json",
        "map.json",
        "icon_overrides.json",
        "counterpicks.json",
    ):
        src = bundle / name
        if src.is_file():
            shutil.copyfile(src, target / name)
    return target


def find_free_port() -> int:
    """Ask the OS for a free loopback port (bind then release)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def live_overlay_enabled(argv: list[str] | None = None,
                         environ: dict | None = None) -> bool:
    """M4 §6: включить оверлей In-Game Assistant.

    Флаг командной строки --live или env DEADLOCK_LIVE=1. Вынесено в
    отдельную функцию, чтобы тесты проверяли решение без запуска GUI.
    """
    import argparse

    parser = argparse.ArgumentParser(prog="DeadlockPlus", add_help=False)
    parser.add_argument("--live", action="store_true",
                        help="Открыть оверлей In-Game Assistant (Deadlock Plus Live)")
    args = parser.parse_known_args(argv)[0]
    env = environ if environ is not None else os.environ
    return bool(args.live) or str(env.get("DEADLOCK_LIVE", "")).strip() == "1"


def main() -> int:
    web_dir = resource_dir("web")

    # Frozen builds: SQLite + JSON datasets persist next to the exe
    # (_MEIPASS is wiped on every launch and cannot hold the database).
    persistent_data = resolve_persistent_data_dir()
    data_dir = persistent_data or resource_dir("data")

    # app.py is not this file's scope: it learns about the layout only via
    # these env vars, which must exist before `from deadlockforge.api.app ...`.
    os.environ["DEADLOCK_WEB_DIR"] = str(web_dir)
    os.environ["DEADLOCK_DATA_DIR"] = str(data_dir)

    src = ROOT / "src"
    if src.is_dir() and str(src) not in sys.path:
        sys.path.insert(0, str(src))

    from deadlockforge.api.app import app

    port = find_free_port()
    url = f"http://127.0.0.1:{port}"

    import uvicorn

    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        log_level="warning",
        log_config=None,
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name="uvicorn", daemon=True)
    thread.start()

    # Wait until uvicorn actually starts accepting TCP connections
    import time
    start_wait = time.time()
    while time.time() - start_wait < 5.0:
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                break
        time.sleep(0.05)

    try:
        import webview  # pywebview
    except Exception as exc:
        print(f"[DeadlockPlus] pywebview unavailable ({exc}).")
        print(f"[DeadlockPlus] Opening {url} in your default browser.")
        print("[DeadlockPlus] Press Ctrl+C to stop the server and exit.")
        webbrowser.open(url)
        if live_overlay_enabled():
            webbrowser.open(url + "/live.html")
        try:
            thread.join()  # main thread blocks; Ctrl+C raises KeyboardInterrupt
        except KeyboardInterrupt:
            pass
        finally:
            server.should_exit = True
            thread.join(timeout=5)
        return 0

    webview.create_window(
        "Deadlock Plus",
        url,
        width=1440,
        height=900,
        min_size=(1200, 768),
        background_color="#070A0F",
    )
    # M4 §6: оверлей In-Game Assistant поверх игры — второе компактное окно
    # (on_top), только по явному флагу --live / env DEADLOCK_LIVE=1.
    # Обычный запуск без флага не меняется.
    if live_overlay_enabled():
        webview.create_window(
            "Deadlock Plus Live",
            url + "/live.html",
            width=420,
            height=640,
            min_size=(360, 480),
            on_top=True,
            background_color="#070A0F",
        )
    # DevTools только по явному env-флагу разработчика (TZ A5: release-сборка
    # не должна открывать DevTools).
    webview.start(debug=bool(os.environ.get("DEADLOCK_DEBUG")), private_mode=True)

    # Window closed -> stop the server deterministically.
    server.should_exit = True
    thread.join(timeout=5)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
