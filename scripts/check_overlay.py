"""Живая проверка плашек поверх игры (окно overlay.html).

Поднимает run_desktop.py, находит его порт по новому LISTENING-сокету, включает
тумблер через API и проверяет то, что глазами не увидишь: окно реально
появляется на 5 секунд, у него стоит WS_EX_TRANSPARENT (клики уходят в игру),
и через 5 секунд оно скрыто. Скриншот кладётся в docs/overlay_toast.png.

Запуск: .venv\\Scripts\\python.exe scripts\\check_overlay.py
"""

from __future__ import annotations

import ctypes
import json
import pathlib
import subprocess
import sys
import time
import urllib.request
from ctypes import wintypes

sys.stdout.reconfigure(encoding="utf-8")

ROOT = pathlib.Path(__file__).resolve().parents[1]
SHOT = ROOT / "docs" / "overlay_toast.png"
TITLE = "Deadlock Plus Overlay"
TOAST_SEC = 5.0

user32 = ctypes.windll.user32
user32.SetProcessDPIAware()

EnumProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
user32.EnumWindows.argtypes = [EnumProc, wintypes.LPARAM]
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.WindowFromPoint.restype = wintypes.HWND
user32.WindowFromPoint.argtypes = [wintypes.POINT]
user32.IsWindowVisible.argtypes = [wintypes.HWND]

WS_EX_TRANSPARENT = 0x20
GWL_EXSTYLE = -20


def listening_ports() -> set[int]:
    """Порты 127.0.0.1 в состоянии LISTENING (netstat без psutil)."""
    out = subprocess.run(["netstat", "-ano", "-p", "TCP"],
                         capture_output=True, text=True,
                         encoding="utf-8", errors="replace").stdout
    ports: set[int] = set()
    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 4 or parts[3] != "LISTENING":
            continue
        host, _, port = parts[1].rpartition(":")
        if host.startswith("127.0.0.1") and port.isdigit():
            ports.add(int(port))
    return ports


def windows_of(pid: int | None = None) -> list[dict]:
    """Окна процесса (или все, если pid=None): заголовок, видимость, ex-стиль."""
    found: list[dict] = []

    def cb(hwnd, _lparam):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if pid is not None and owner.value != pid:
            return True
        buf = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(hwnd, buf, 256)
        rect = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        found.append({
            "hwnd": hwnd,
            "pid": owner.value,
            "title": buf.value,
            "visible": bool(user32.IsWindowVisible(hwnd)),
            "ex": user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & 0xFFFFFFFF,
            "rect": (rect.left, rect.top,
                     rect.right - rect.left, rect.bottom - rect.top),
        })
        return True

    user32.EnumWindows(EnumProc(cb), 0)
    return found


def by_title(title: str) -> list[dict]:
    """Окна по заголовку: launcher .venv\\python.exe — редиректор, окна у потомка."""
    return [w for w in windows_of() if w["title"] == title]


def post(url: str, payload: dict) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=5) as resp:
        return json.loads(resp.read().decode())


def get(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=5) as resp:
        return json.loads(resp.read().decode())


def shot(rect: tuple[int, int, int, int], path: pathlib.Path) -> bool:
    try:
        from PIL import ImageGrab
    except Exception:
        return False
    x, y, w, h = rect
    ImageGrab.grab(bbox=(x, y, x + w, y + h), all_screens=True).save(path)
    return True


def launch(extra: list[str]) -> tuple[subprocess.Popen, str] | None:
    """Поднять приложение и вернуть процесс + базовый URL его сервера."""
    before = listening_ports()
    if "--exe" in sys.argv:
        cmd = [str(ROOT / "dist" / "DeadlockForge.exe"), *extra]
        cwd = ROOT / "dist"
    else:
        cmd = [str(ROOT / ".venv" / "Scripts" / "python.exe"),
               "run_desktop.py", *extra]
        cwd = ROOT
    print("запуск:", " ".join(cmd))
    proc = subprocess.Popen(
        cmd, cwd=cwd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    port = 0
    deadline = time.time() + 40
    while time.time() < deadline and not port:
        for cand in listening_ports() - before:
            try:
                if "supported" in get(f"http://127.0.0.1:{cand}/api/live/overlay"):
                    port = cand
                    break
            except Exception:
                continue
        if not port:
            time.sleep(0.4)
    if not port:
        proc.kill()
        print("FAIL: порт приложения не найден")
        return None
    return proc, f"http://127.0.0.1:{port}"


def check_off_by_default() -> list[str]:
    """Без флага --overlay окна плашек нет, а API говорит supported: false."""
    launched = launch([])
    if not launched:
        return ["приложение не запустилось без --overlay"]
    proc, base = launched
    problems: list[str] = []
    try:
        time.sleep(3.5)
        windows = by_title(TITLE)
        print(f"без флага окон плашек: {len(windows)}")
        if windows:
            problems.append("окно плашек создано без флага --overlay")
        state = get(f"{base}/api/live/overlay")
        print(f"без флага API: {state}")
        if state != {"supported": False, "enabled": False}:
            problems.append(f"без флага API вернул {state}")
    finally:
        proc.kill()
        proc.wait(timeout=10)
    return problems


def main() -> int:
    problems = check_off_by_default()

    launched = launch(["--overlay"])
    if not launched:
        return 1
    proc, base = launched
    try:
        time.sleep(3.5)  # окна создаются после старта GUI-цикла
        start = by_title(TITLE)
        print(f"окно плашек при старте: "
              f"{[(w['title'], w['visible']) for w in start]}")
        if not start:
            problems.append("окно плашек не создано с --overlay")
        elif start[0]["visible"]:
            problems.append("окно плашек видно до включения тумблера")

        state = get(f"{base}/api/live/overlay")
        print(f"тумблер до: {state}")
        if not state["supported"]:
            problems.append("тумблер сообщает supported=false при --overlay")
        if state["enabled"]:
            problems.append("тумблер включён по умолчанию")

        print("включаю:", post(f"{base}/api/live/overlay", {"enabled": True}))
        # 99 с: до лёгких лагерей (2:00) остаётся 21 с — попадаем в окно анонса.
        print("часы:", post(f"{base}/api/live/clock",
                            {"seconds": 99, "running": True}))
        time.sleep(2.5)

        live = by_title(TITLE)
        toast = next((w for w in live if w["visible"]), None)
        if not toast:
            problems.append("плашка не появилась за 2.5 с после анонса")
        else:
            x, y, w, h = toast["rect"]
            print(f"плашка видна: {w}x{h} в {x},{y}, ex=0x{toast['ex']:08X}, "
                  f"pid={toast['pid']}")
            if not toast["ex"] & WS_EX_TRANSPARENT:
                problems.append("нет WS_EX_TRANSPARENT — клики не пройдут в игру")
            if (w, h) != (560, 150):
                problems.append(f"размер окна {w}x{h}, ожидался 560x150")
            hit = user32.WindowFromPoint(wintypes.POINT(x + w // 2, y + h // 2))
            print(f"WindowFromPoint(центр)=0x{hit:08X} "
                  f"(плашка 0x{toast['hwnd']:08X})")
            if hit == toast["hwnd"]:
                problems.append("центр плашки перехватывает клик")
            if shot(toast["rect"], SHOT):
                print(f"скриншот: {SHOT.relative_to(ROOT)}")

        time.sleep(TOAST_SEC + 1.0)
        after = by_title(TITLE)
        print(f"через {TOAST_SEC:g} с: {[(w['title'], w['visible']) for w in after]}")
        if any(w["visible"] for w in after):
            problems.append("плашка не скрылась через 5 с")

        print("выключаю:", post(f"{base}/api/live/overlay", {"enabled": False}))
        if get(f"{base}/api/live/overlay")["enabled"]:
            problems.append("тумблер не выключился")
    finally:
        proc.kill()
        proc.wait(timeout=10)

    if problems:
        for p in problems:
            print("FAIL:", p)
        return 1
    print("OK: плашка появляется на 5 с, клики проходят сквозь, окно скрывается")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
