"""Desktop entry point: uvicorn in a daemon thread + pywebview window.

Dynamic port (never 8377), graceful server stop after the window closes,
browser fallback when WebView2 is unavailable, and PyInstaller-aware
resource resolution via sys._MEIPASS + DEADLOCK_WEB_DIR/DEADLOCK_DATA_DIR
(those env vars must be set BEFORE the app import).
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import socket
import sys
import threading
import time
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
        "synergies.json",
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


def overlay_enabled(argv: list[str] | None = None,
                    environ: dict | None = None) -> bool:
    """Плашки поверх игры — под явным флагом, по умолчанию выключены.

    Начало матча определяется только вручную (в Deadlock нет ни API, ни GSI
    с игровым временем), поэтому подсказки не включаются сами: --overlay или
    DEADLOCK_OVERLAY=1. Без флага окно не создаётся вообще, и
    GET /api/live/overlay отвечает supported: false.
    """
    import argparse

    parser = argparse.ArgumentParser(prog="DeadlockPlus", add_help=False)
    parser.add_argument("--overlay", action="store_true",
                        help="Показывать подсказки плашками поверх игры")
    args = parser.parse_known_args(argv)[0]
    env = environ if environ is not None else os.environ
    return bool(args.overlay) or str(env.get("DEADLOCK_OVERLAY", "")).strip() == "1"


def screen_size() -> tuple[int, int]:
    """Первичный монитор в физических пикселях (позиция окна плашек)."""
    try:
        import ctypes

        user32 = ctypes.windll.user32
        return user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
    except Exception:
        return 1920, 1080


def make_click_through(window) -> bool:
    """Клики проходят сквозь окно плашек в игру.

    WS_EX_TRANSPARENT исключает окно из hit-testing: клик по плашке попадает
    в игру под ней, а не в WebView2. Без этого окно съедало бы прицел.
    """
    try:
        import ctypes

        native = getattr(window, "native", None)
        if native is None:
            return False
        hwnd = native.Handle.ToInt32()
        user32 = ctypes.windll.user32
        gwl_exstyle, ws_ex_transparent = -20, 0x00000020
        style = user32.GetWindowLongW(hwnd, gwl_exstyle)
        user32.SetWindowLongW(hwnd, gwl_exstyle, style | ws_ex_transparent)
        return True
    except Exception:
        return False


# Ex-стили окна-оверлея (Get/SetWindowLongW, GWL_EXSTYLE) и флаги SetWindowPos.
GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_LAYERED = 0x00080000
WS_EX_NOACTIVATE = 0x08000000
LWA_ALPHA = 0x00000002
HWND_TOPMOST = -1
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040


def make_overlay_window(window, alpha: float = 0.88) -> bool:
    """Кликабельное окно поверх игры, не отбирающее управление персонажем.

    WS_EX_NOACTIVATE: клик не активирует окно, игра остаётся foreground —
    WASD/пробел/шифт продолжают идти в игру. WS_EX_TOOLWINDOW убирает окно
    из Alt+Tab. WS_EX_LAYERED + LWA_ALPHA делают его полупрозрачным целиком:
    попиксельную прозрачность WebView2 в этой сборке не отдаёт (проверено),
    а альфа слоя работает.
    WS_EX_TRANSPARENT намеренно снимается: с ним окно было бы некликабельным.
    """
    try:
        import ctypes

        native = getattr(window, "native", None)
        if native is None:
            return False
        hwnd = int(native.Handle.ToInt32())
        user32 = ctypes.windll.user32
        style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        style = (style | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW | WS_EX_LAYERED) & ~WS_EX_TRANSPARENT
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
        level = max(0, min(255, int(round(float(alpha) * 255))))
        return bool(user32.SetLayeredWindowAttributes(hwnd, 0, level, LWA_ALPHA))
    except Exception:
        return False


def show_overlay(window) -> bool:
    """Показать окно поверх игры, не забирая фокус у активного окна.

    window.show() в pywebview активирует форму; NOACTIVATE в стиле не даёт
    системе сменить foreground, а SetWindowPos с SWP_NOACTIVATE подтверждает
    z-порядок. SetForegroundWindow в конце — страховка на случай, если
    show() всё-таки успел перехватить активность.
    """
    try:
        import ctypes
        import ctypes.wintypes as wt

        user32 = ctypes.windll.user32
        native = getattr(window, "native", None)
        if native is None:
            window.show()
            return False
        hwnd = int(native.Handle.ToInt32())
        foreground = int(user32.GetForegroundWindow())
        window.show()
        user32.SetWindowPos.argtypes = [
            wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int,
            ctypes.c_int, ctypes.c_int, ctypes.c_uint,
        ]
        user32.SetWindowPos(hwnd, wt.HWND(HWND_TOPMOST), 0, 0, 0, 0,
                            SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE | SWP_SHOWWINDOW)
        if foreground and foreground != hwnd:
            user32.SetForegroundWindow(foreground)
        return True
    except Exception:
        try:
            window.show()
        except Exception:
            pass
        return False


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

    main_win = webview.create_window(
        "Deadlock Plus",
        url,
        width=1440,
        height=900,
        min_size=(1200, 768),
        background_color="#070A0F",
    )
    # --- Окно-оверлей: программа поверх игры по горячей клавише -----------
    # In-Game Assistant (live.html) в отдельном окне: полупрозрачное,
    # кликабельное, но клавиатуру у игры не забирает (WS_EX_NOACTIVATE).
    # Окно создаётся скрытым всегда — хоткей CTRL+F2 открывает его в любой
    # момент. --live / DEADLOCK_LIVE=1 открывает его сразу при старте.
    # Нужен Borderless Window в Deadlock: эксклюзивный fullscreen не
    # перекрывается ничем.
    live_at_startup = live_overlay_enabled()
    screen_w, screen_h = screen_size()
    overlay_alpha = 0.88
    try:
        overlay_alpha = max(0.3, min(1.0, float(os.environ.get("DEADLOCK_OVERLAY_ALPHA", "0.88"))))
    except ValueError:
        pass
    overlay_panel_w, overlay_panel_h = 470, 720
    overlay_panel_win = webview.create_window(
        "Deadlock Plus In-Game",
        url + "/live.html",
        width=overlay_panel_w,
        height=overlay_panel_h,
        x=max(0, screen_w - overlay_panel_w - 48),
        y=max(24, (screen_h - overlay_panel_h) // 2),
        frameless=True,
        on_top=True,
        focus=False,
        hidden=True,
        resizable=False,
        easy_drag=False,
        shadow=False,
        background_color="#070A0F",
    )

    # Плашки поверх игры — только по флагу --overlay / DEADLOCK_OVERLAY=1.
    # Фича не оправдала себя: начало матча жмётся руками, а тайминги карты
    # игрок знает наизусть, поэтому по умолчанию окно не создаётся вовсе.
    # Если флаг включён: окно без рамки внизу по центру, скрыто, показывается
    # на 5 с на подсказку. Полноэкранная прозрачность не годится: WebView2 в
    # этой сборке не отдаёт реальную прозрачность (проверено — окно рисуется
    # сплошным), поэтому вместо прозрачного слоя — маленькое окно.
    overlay_w, overlay_h = 560, 150
    overlay_toast_sec = 5.0
    overlay_flag = overlay_enabled()
    overlay_win = None
    if overlay_flag:
        overlay_win = webview.create_window(
            "Deadlock Plus Overlay",
            url + "/overlay.html",
            width=overlay_w,
            height=overlay_h,
            x=max(0, (screen_w - overlay_w) // 2),
            y=max(0, screen_h - overlay_h - 140),
            frameless=True,
            on_top=True,
            focus=False,
            hidden=True,
            resizable=False,
            easy_drag=False,
            shadow=False,
            background_color="#070A0F",
        )

    # --- Глобальная горячая клавиша: панель поверх игры -------------------
    # RegisterHotKey шлёт WM_HOTKEY в поток, который его зарегистрировал,
    # поэтому слушатель держит собственный GetMessage-цикл. Хук клавиатуры
    # не используется: штатный Win32 API, без вмешательства в игру.
    from deadlockforge.desktop.hotkey import HotkeyError, HotkeyListener

    hotkey_spec = "ctrl+f1"
    # Тумблер «Поверх других окон»: включён — хоткей работает и окно
    # закрепляется над игрой; выключен — обычное окно без перехвата клавиш.
    topmost_on = True
    try:
        db = app.state.db()
        hotkey_spec = db.get_setting("hotkey", hotkey_spec) or hotkey_spec
        raw = db.get_setting("always_on_top", "1")
        topmost_on = str(raw).strip().lower() not in {"0", "false", "off", "no", ""}
    except Exception:
        pass

    panel_state = {"active": False, "by_hotkey": False}
    game_hwnd = 0
    listener = None
    hotkey_error: str | None = None

    def _hwnd(window) -> int:
        native = getattr(window, "native", None)
        return native.Handle.ToInt32() if native is not None else 0

    def _topmost(window, on: bool) -> None:
        """Закрепить окно над остальными (HWND_TOPMOST) или снять закреп.

        HWND_TOPMOST = -1, HWND_NOTOPMOST = -2: без объявленных argtypes ctypes
        передаёт 32-битный int и на x64 верхняя половина HWND теряется — окно
        молча остаётся в прежнем z-порядке.
        """
        import ctypes
        import ctypes.wintypes as wt

        hwnd = _hwnd(window)
        if not hwnd:
            return
        user32 = ctypes.windll.user32
        user32.SetWindowPos.argtypes = [
            wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int,
            ctypes.c_int, ctypes.c_int, ctypes.c_uint,
        ]
        user32.SetWindowPos(hwnd, wt.HWND(-1 if on else -2), 0, 0, 0, 0,
                            0x0001 | 0x0002)  # NOSIZE|NOMOVE

    def _raise(window) -> None:
        """Поднять окно наверх и забрать фокус.

        Закреп ставится только при включённом тумблере «Поверх других окон»,
        иначе окно ведёт себя как обычное. Право на SetForegroundWindow даёт
        сам факт WM_HOTKEY: процесс получил последнее событие ввода.
        AllowSetForegroundWindow здесь не нужен — из фона он возвращает 0, а при
        своём окне на переднем плане раздал бы право перехвата фокуса всем
        процессам.
        """
        import ctypes

        hwnd = _hwnd(window)
        if not hwnd:
            return
        if topmost_on:
            _topmost(window, True)
        ctypes.windll.user32.SetForegroundWindow(hwnd)

    def _focus(hwnd: int) -> None:
        """Вернуть фокус окну игры (topmost не трогаем: это окно не наше)."""
        import ctypes

        if not hwnd:
            return
        ctypes.windll.user32.SetForegroundWindow(hwnd)

    # --- Оверлей: показать/скрыть, не отбирая управление у игры -----------
    overlay_panel_state = {"visible": False}

    def overlay_panel_show() -> None:
        """Показать оверлей поверх игры; активное окно (игра) не меняется."""
        make_overlay_window(overlay_panel_win, overlay_alpha)
        show_overlay(overlay_panel_win)
        # Окно живёт скрытым между вызовами: локальный таймер в нём не
        # истина, часы подтягиваются из серверного состояния.
        try:
            overlay_panel_win.evaluate_js("window.dfSyncClock && window.dfSyncClock()")
        except Exception:
            pass
        overlay_panel_state["visible"] = True

    def overlay_panel_hide() -> None:
        try:
            overlay_panel_win.hide()
        except Exception:
            pass
        overlay_panel_state["visible"] = False

    def overlay_panel_toggle() -> None:
        """Хоткей: открыть оверлей или спрятать его обратно."""
        try:
            overlay_panel_hide() if overlay_panel_state["visible"] else overlay_panel_show()
        except Exception:
            overlay_panel_state["visible"] = False

    def overlay_panel_ctl(visible: bool | None = None) -> dict:
        """GET/POST /api/desktop/overlay: состояние или показать/скрыть."""
        if visible is not None:
            try:
                overlay_panel_show() if visible else overlay_panel_hide()
            except Exception:
                overlay_panel_state["visible"] = False
        return dict(overlay_panel_state)

    def panel_show() -> None:
        nonlocal game_hwnd
        import ctypes

        own = _hwnd(main_win)
        foreground = int(ctypes.windll.user32.GetForegroundWindow())
        # Хоткей, нажатый при своём же окне на переднем плане, не должен
        # запоминать «игру» как наш собственный HWND.
        game_hwnd = foreground if foreground and foreground != own else 0
        main_win.show()
        _raise(main_win)
        panel_state.update(active=True, by_hotkey=True)

    def panel_hide() -> None:
        # Закреп снимается до скрытия: SetWindowPos по уже скрытому окну
        # откладывает z-порядок, и окно, открытое потом обычным способом,
        # всплывало бы поверх игры и перекрывало её.
        _topmost(main_win, False)
        main_win.hide()
        panel_state.update(active=False, by_hotkey=False)
        _focus(game_hwnd)

    def panel_toggle() -> None:
        """Хоткей: показать панель поверх игры или спрятать её обратно."""
        try:
            panel_hide() if panel_state["active"] else panel_show()
        except Exception:
            panel_state.update(active=False, by_hotkey=False)

    def panel_ctl(visible: bool | None = None) -> dict:
        """Esc из UI прячет панель; None — только чтение состояния."""
        if visible is not None:
            try:
                panel_show() if visible else panel_hide()
            except Exception:
                panel_state.update(active=False, by_hotkey=False)
        return dict(panel_state)

    def _register(spec: str) -> str | None:
        """Перерегистрировать слушатель; None — успех, иначе текст ошибки."""
        nonlocal listener, hotkey_spec, hotkey_error
        try:
            candidate = HotkeyListener(spec, panel_toggle)
        except HotkeyError as exc:
            listener = None
            hotkey_error = str(exc)
            return hotkey_error
        if candidate.start():
            listener = candidate
            hotkey_spec = spec
            hotkey_error = None
            return None
        listener = None
        hotkey_error = candidate.error or "не зарегистрирована"
        return hotkey_error

    def hotkey_ctl(spec: str | None) -> dict:
        """Регистрация комбинации; None — только чтение состояния.

        Причина неудачи хранится отдельно от слушателя: после провала
        регистрации при старте listener уже None, и без этого UI показывал бы
        сухое «не зарегистрирован», не объясняя почему.
        """
        nonlocal listener, hotkey_spec
        if spec is None:
            return {
                "active": bool(listener is not None and listener.registered),
                "error": hotkey_error,
                "enabled": topmost_on,
            }
        previous = hotkey_spec
        if listener is not None:
            listener.stop()
            listener = None
        hotkey_spec = spec
        if not topmost_on:
            # Тумблер выключен: комбинация сохраняется, но не перехватывается.
            return {"active": False, "error": None, "enabled": False}
        error = _register(spec)
        if error is None:
            return {"active": True, "error": None, "enabled": True}
        # Неудачная смена не должна оставлять панель без рабочего хоткея.
        if previous and previous != spec:
            _register(previous)
        return {"active": False, "error": error, "enabled": True}

    # Второй хоткей — окно-оверлей (CTRL+F2 по умолчанию). Ключ в
    # app_settings тот же, что у панели, чтобы комбинацию можно было сменить
    # без правки кода; поля в профиле у него пока нет.
    overlay_listener = None
    overlay_hotkey_error = None
    overlay_hotkey_spec = "ctrl+f2"
    try:
        overlay_hotkey_spec = app.state.db().get_setting("overlay_hotkey", overlay_hotkey_spec) or overlay_hotkey_spec
    except Exception:
        pass

    def _register_overlay(spec: str) -> str | None:
        """Зарегистрировать хоткей оверлея; None — успех, иначе текст ошибки."""
        nonlocal overlay_listener, overlay_hotkey_spec, overlay_hotkey_error
        try:
            candidate = HotkeyListener(spec, overlay_panel_toggle)
        except HotkeyError as exc:
            overlay_listener = None
            overlay_hotkey_error = str(exc)
            return overlay_hotkey_error
        if candidate.start():
            overlay_listener = candidate
            overlay_hotkey_spec = spec
            overlay_hotkey_error = None
            return None
        overlay_listener = None
        overlay_hotkey_error = candidate.error or "не зарегистрирована"
        return overlay_hotkey_error

    def topmost_ctl(enabled: bool | None = None) -> dict:
        """Тумблер «Поверх других окон»: хоткей и закреп окна над игрой.

        Выключен — программа работает как обычное окно: перехват клавиш снят,
        закреп снят. Окно не прячем: пользователь в этот момент в нём и
        находится; снимаем только закреп и метку «открыто хоткеем», чтобы Esc
        больше не закрывал его как панель. Хоткей оверлея живёт по тем же
        правилам: выключенный тумблер глушит и его, а сам оверлей прячется.
        """
        nonlocal topmost_on, listener, hotkey_error, overlay_listener
        if enabled is None:
            return {"enabled": topmost_on, "error": None}
        topmost_on = bool(enabled)
        if topmost_on:
            error = None
            if listener is None:
                error = _register(hotkey_spec)
            if overlay_listener is None:
                _register_overlay(overlay_hotkey_spec)
            return {"enabled": True, "error": error}
        panel_state.update(active=False, by_hotkey=False)
        overlay_panel_hide()
        if listener is not None:
            listener.stop()
            listener = None
        if overlay_listener is not None:
            overlay_listener.stop()
            overlay_listener = None
        hotkey_error = None
        _topmost(main_win, False)
        return {"enabled": False, "error": None}

    overlay_on = False     # тумблер в главном окне
    overlay_shown = False  # окно сейчас на экране
    overlay_until = 0.0    # monotonic-дедлайн скрытия
    overlay_sized = False  # размер выровнен после первого показа

    def overlay_hide() -> None:
        nonlocal overlay_shown, overlay_until
        if overlay_shown and overlay_win is not None:
            try:
                overlay_win.hide()
            except Exception:
                pass
        overlay_shown = False
        overlay_until = 0.0

    def overlay_toggle(enabled: bool | None) -> bool:
        """Тумблер плашек; None — только прочитать состояние, не меняя его."""
        nonlocal overlay_on
        if overlay_win is None:  # флаг --overlay не выставлен
            return False
        if enabled is None:
            return overlay_on
        overlay_on = bool(enabled)
        if not overlay_on:
            overlay_hide()
        return overlay_on

    def overlay_push(kind: str, title: str, text: str, at: float) -> None:
        """Показать плашку: окно вперёд, JS рисует строку, таймер на 5 с."""
        nonlocal overlay_shown, overlay_until, overlay_sized
        if overlay_win is None:
            return
        try:
            # WS_EX_TRANSPARENT ставится до показа: клик по плашке должен
            # уходить в игру, а не в WebView2. Повтор идемпотентен и страхует
            # от случая, когда HWND ещё не был готов на прошлой попытке.
            make_click_through(overlay_win)
            if not overlay_shown:
                overlay_win.show()
                overlay_shown = True
                if not overlay_sized:
                    # FormBorderStyle снимается уже после Size, и окно теряет
                    # рамку из клиентской области — выравниваем размер.
                    overlay_win.resize(overlay_w, overlay_h)
                    overlay_sized = True
                overlay_win.evaluate_js("window.dfClear && window.dfClear()")
            args = ", ".join(json.dumps(v) for v in (kind, title, text))
            overlay_win.evaluate_js(f"window.dfToast({args}, {float(at):.0f})")
            overlay_until = time.monotonic() + overlay_toast_sec
        except Exception:
            pass

    def overlay_driver() -> None:
        """Держит часы и показывает подсказки: окно живёт 5 с и скрывается.

        Драйвер на Python, а не setInterval в JS: WebView2 троттлит таймеры
        скрытого окна, и плашка опаздывала бы на секунды.
        """
        fired: set[str] = set()
        last_sec = -1.0
        schedule: list[dict] = []
        lead = 20

        while True:
            time.sleep(0.25)
            try:
                clock = app.state.live_clock()
                sec = float(clock.get("seconds") or 0.0)

                # Плашка живёт фиксированные 5 с — в том числе на паузе часов.
                if overlay_shown and time.monotonic() > overlay_until:
                    overlay_hide()

                if not overlay_on:
                    continue
                if not schedule:
                    schedule = app.state.live_schedule()
                    lead = int(app.state.live_lead)
                    fired.clear()
                    last_sec = -1.0

                # Прыжок часов (старт в середине, сброс) — без пачки плашек.
                # Гасим только уже прошедшее: анонс события, до которого ещё
                # есть время, должен прозвучать, даже если включили на лету.
                if last_sec < 0 or abs(sec - last_sec) > 30:
                    for ev in schedule:
                        if ev["at"] <= sec:
                            fired.add(ev["id"] + ":pre")
                            fired.add(ev["id"] + ":now")
                    last_sec = sec
                    continue
                last_sec = sec
                if not clock.get("running"):
                    continue

                for ev in schedule:
                    pre_key = ev["id"] + ":pre"
                    now_key = ev["id"] + ":now"
                    if (ev["at"] - ev["lead"] > 0 and sec >= ev["at"] - ev["lead"]
                            and pre_key not in fired):
                        fired.add(pre_key)
                        overlay_push("pre", ev["title"], ev["pre"],
                                     ev["at"] - ev["lead"])
                    if sec >= ev["at"] and now_key not in fired:
                        fired.add(now_key)
                        overlay_push("now", ev["title"], ev["now"], ev["at"])
            except Exception:
                continue

    if overlay_flag:
        app.state.overlay_ctl = overlay_toggle
    app.state.overlay_panel_ctl = overlay_panel_ctl
    app.state.hotkey_ctl = hotkey_ctl
    app.state.panel_ctl = panel_ctl
    app.state.topmost_ctl = topmost_ctl
    hotkey_ctl(hotkey_spec)
    # Оверлей регистрируется отдельно: если его комбинацию занял Discord или
    # Steam, панель CTRL+F1 остаётся рабочей, а ошибка живёт в своём состоянии.
    if topmost_on:
        _register_overlay(overlay_hotkey_spec)

    # --- Трей: крестик прячет панель, выход только через меню -------------
    # Раньше закрытие главного окна оставляло скрытое окно плашек: pywebview
    # держит WinForms-цикл, пока жив хотя бы один BrowserView, поэтому процесс
    # не завершался и каждая следующая сборка натыкалась на залоченный exe.
    quitting = {"value": False}

    def quit_app() -> None:
        """«Выйти» из трея: погасить окна, сервер и поток хоткея, затем процесс.

        os._exit обязателен: WebView2 держит COM-потоки и не даёт обычному
        выходу из main() освободить exe, а uvicorn-поток daemon-ный.
        """
        if quitting["value"]:
            return
        quitting["value"] = True
        try:
            if listener is not None:
                listener.stop()
            if overlay_listener is not None:
                overlay_listener.stop()
        except Exception:
            pass
        try:
            tray.stop()
        except Exception:
            pass
        for window in (overlay_win, overlay_panel_win, main_win):
            if window is None:
                continue
            try:
                window.destroy()
            except Exception:
                pass
        server.should_exit = True
        try:
            thread.join(timeout=1.0)
        except Exception:
            pass
        os._exit(0)

    def on_closing() -> bool:
        """Крестик главного окна: спрятать в трей, не закрывая процесс.

        Если трей не поднялся, прятать окно нельзя — приложение осталось бы
        без единого способа себя показать. Тогда честно выходим.
        """
        if quitting["value"]:
            return True
        if tray is None:
            threading.Thread(target=quit_app, daemon=True).start()
            return False
        panel_state.update(active=False, by_hotkey=False)
        # Скрытие отложено на отдельный поток: внутри FormClosing прямой Hide()
        # конфликтует с отменой закрытия и окно остаётся на экране.
        threading.Thread(target=lambda: _safe_hide(main_win), daemon=True).start()
        return False

    def _safe_hide(window) -> None:
        try:
            window.hide()
        except Exception:
            pass

    def tray_open() -> None:
        """«Открыть» из трея: показать панель и поднять её наверх."""
        try:
            main_win.show()
            if topmost_on:
                _raise(main_win)
        except Exception:
            pass

    tray = None
    try:
        from deadlockforge.desktop.tray import TrayIcon

        tray = TrayIcon(web_dir / "assets" / "icons" / "deadlockforge.ico",
                        tray_open, quit_app)
        main_win.events.closing += on_closing
        if not tray.start():
            tray = None
    except Exception:
        tray = None

    def startup_driver() -> None:
        """Стили оверлея — после появления HWND; --live показывает его сразу.

        WS_EX_NOACTIVATE ставится до первого show: иначе окно один раз
        отобразилось бы обычным и забрало фокус. Драйвер плашек живёт в этом
        же потоке и стартует только при --overlay.
        """
        for _ in range(100):
            if _hwnd(overlay_panel_win):
                break
            time.sleep(0.05)
        make_overlay_window(overlay_panel_win, overlay_alpha)
        if live_at_startup:
            try:
                overlay_panel_show()
            except Exception:
                pass
        if overlay_flag:
            overlay_driver()

    # DevTools только по явному env-флагу разработчика (TZ A5: release-сборка
    # не должна открывать DevTools).
    webview.start(startup_driver,
                  debug=bool(os.environ.get("DEADLOCK_DEBUG")),
                  private_mode=True)

    # Окна закрыты -> остановить сервер и убрать иконку из трея.
    if tray is not None:
        try:
            tray.stop()
        except Exception:
            pass
    server.should_exit = True
    thread.join(timeout=5)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
