"""Глобальные горячие клавиши Windows через ctypes.

Парсинг строк вида 'ctrl+f1' и регистрация системных хоткеев
в собственном потоке с циклом сообщений Win32.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import sys
import threading
from collections.abc import Callable
from typing import Optional, Tuple

# Модификаторы Windows (RegisterHotKey)
MOD_ALT: int = 0x0001
MOD_CONTROL: int = 0x0002
MOD_SHIFT: int = 0x0004
MOD_WIN: int = 0x0008
# Запрет авто-повтора при удержании
MOD_NOREPEAT: int = 0x4000

# Константы сообщений Win32
WM_HOTKEY: int = 0x0312
WM_QUIT: int = 0x0012

# Идентификатор хоткея, передаваемый в RegisterHotKey
_HOTKEY_ID: int = 1

# Порядок модификаторов для format_hotkey
_MOD_NAMES: Tuple[Tuple[int, str], ...] = (
    (MOD_CONTROL, "Ctrl"),
    (MOD_ALT, "Alt"),
    (MOD_SHIFT, "Shift"),
    (MOD_WIN, "Win"),
)

# Синонимы модификаторов -> бит
_MOD_ALIASES: dict[str, int] = {
    "ctrl": MOD_CONTROL,
    "control": MOD_CONTROL,
    "alt": MOD_ALT,
    "shift": MOD_SHIFT,
    "win": MOD_WIN,
    "meta": MOD_WIN,
    "super": MOD_WIN,
}

# Именованные клавиши -> виртуальный код
_NAMED_KEYS: dict[str, int] = {
    "space": 0x20,
    "tab": 0x09,
    "enter": 0x0D,
    "return": 0x0D,
    "escape": 0x1B,
    "esc": 0x1B,
    "backspace": 0x08,
    "insert": 0x2D,
    "ins": 0x2D,
    "delete": 0x2E,
    "del": 0x2E,
    "home": 0x24,
    "end": 0x23,
    "pageup": 0x21,
    "pagedown": 0x22,
    "left": 0x25,
    "up": 0x26,
    "right": 0x27,
    "down": 0x28,
}

# Подписи именованных клавиш для format_hotkey (первый алиас из _NAMED_KEYS
# даёт 'pageup'/'esc' — в UI нужен человеческий вид).
_KEY_LABELS: dict[str, str] = {
    "space": "Space",
    "tab": "Tab",
    "enter": "Enter",
    "return": "Enter",
    "escape": "Escape",
    "esc": "Escape",
    "backspace": "Backspace",
    "insert": "Insert",
    "ins": "Insert",
    "delete": "Delete",
    "del": "Delete",
    "home": "Home",
    "end": "End",
    "pageup": "PageUp",
    "pagedown": "PageDown",
    "left": "Left",
    "up": "Up",
    "right": "Right",
    "down": "Down",
}

# Символьные клавиши -> виртуальный код
_CHAR_KEYS: dict[str, int] = {
    "`": 0xC0,
    "-": 0xBD,
    "=": 0xBB,
    "[": 0xDB,
    "]": 0xDD,
    ";": 0xBA,
    "'": 0xDE,
    ",": 0xBC,
    ".": 0xBE,
    "/": 0xBF,
    "\\": 0xDC,
}


class HotkeyError(ValueError):
    """Ошибка разбора спецификации или регистрации горячей клавиши."""


def _parse_modifiers(parts: list[str]) -> int:
    """Собирает биты модификаторов, бросает HotkeyError при повторе/неизвестном."""
    mods: int = 0
    for part in parts:
        mod = _MOD_ALIASES.get(part.lower())
        if mod is None:
            raise HotkeyError(f"Неизвестный модификатор: {part}")
        if mods & mod:
            raise HotkeyError(f"Повтор модификатора: {part}")
        mods |= mod
    return mods


def _parse_key(part: str) -> int:
    """Возвращает виртуальный код одиночной клавиши или бросает HotkeyError."""
    upper = part.upper()
    named = _NAMED_KEYS.get(part.lower())
    if named is not None:
        return named
    char = _CHAR_KEYS.get(part)
    if char is not None:
        return char
    # F1..F24
    if upper.startswith("F") and upper[1:].isdigit():
        n = int(upper[1:])
        if 1 <= n <= 24:
            return 0x70 + (n - 1)
    # A..Z
    if len(upper) == 1 and "A" <= upper <= "Z":
        return ord(upper)
    # 0..9
    if len(upper) == 1 and "0" <= upper <= "9":
        return ord(upper)
    raise HotkeyError(f"Неизвестная клавиша: {part}")


def parse_hotkey(spec: str) -> Tuple[int, int]:
    """Разбирает 'ctrl+f1' в (модификаторы без MOD_NOREPEAT, виртуальный код)."""
    raw = spec.strip()
    if not raw:
        raise HotkeyError("Пустая спецификация горячей клавиши")

    parts = [p.strip() for p in raw.split("+")]
    if any(p == "" for p in parts):
        raise HotkeyError("Пустая часть в спецификации горячей клавиши")

    if len(parts) < 2:
        raise HotkeyError("Отсутствует модификатор или клавиша: нужен формат 'ctrl+f1'")

    mods = _parse_modifiers(parts[:-1])
    key = _parse_key(parts[-1])
    return mods, key


def format_hotkey(spec: str) -> str:
    """Канонический вид спецификации для UI: 'shift+alt+a' -> 'Alt+Shift+A'."""
    mods, key = parse_hotkey(spec)
    names = [label for bit, label in _MOD_NAMES if mods & bit]

    # Клавиша: A-Z и 0-9 печатаются как есть
    if 0x41 <= key <= 0x5A or 0x30 <= key <= 0x39:
        key_label = chr(key)
    elif 0x70 <= key <= 0x87:
        key_label = f"F{key - 0x70 + 1}"
    else:
        # Именованные и символьные клавиши
        name = next((k for k, v in _NAMED_KEYS.items() if v == key), None)
        if name is not None:
            key_label = _KEY_LABELS.get(name, name)
        else:
            key_label = next((k for k, v in _CHAR_KEYS.items() if v == key),
                             f"{key:02X}")
    return "+".join([*names, key_label])


class HotkeyListener:
    """Регистрирует глобальную горячую клавишу и ждёт срабатывания в своём потоке."""

    def __init__(self, spec: str, callback: Callable[[], None]) -> None:
        self._spec = spec
        self._mods, self._vk = parse_hotkey(spec)
        self._error: Optional[str] = None
        self._callback = callback
        self._thread: Optional[threading.Thread] = None
        self._ready = threading.Event()
        self._registered = False
        self._thread_id = 0

    @property
    def registered(self) -> bool:
        """True, если горячая клавиша активна."""
        return self._registered

    @property
    def error(self) -> Optional[str]:
        """Текст ошибки (не-Windows, занятая комбинация) или None."""
        if self._error is not None:
            return self._error
        if sys.platform != "win32":
            return "Глобальные горячие клавиши доступны только в Windows"
        return None

    def start(self) -> bool:
        """Запускает слушатель; при живом потоке повторный вызов ничего не меняет."""
        if self._thread is not None and self._thread.is_alive():
            return self._registered
        if self._error is not None or sys.platform != "win32":
            self._registered = False
            return False
        self._ready.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self._ready.wait(timeout=2.0)
        return self._registered

    def stop(self) -> None:
        """Останавливает слушатель; повторный вызов безопасен."""
        thread = self._thread
        if thread is not None and thread.is_alive():
            user32 = ctypes.windll.user32
            if self._thread_id:
                user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
            thread.join(timeout=2.0)
        if self._thread_id:
            ctypes.windll.user32.UnregisterHotKey(None, _HOTKEY_ID)
        self._registered = False
        self._thread = None

    def _run(self) -> None:
        """Потоковая функция: регистрирует хоткей и крутит цикл сообщений."""
        kernel32 = ctypes.windll.kernel32
        user32 = ctypes.windll.user32
        self._thread_id = kernel32.GetCurrentThreadId()

        # MOD_NOREPEAT добавляется только при реальной регистрации в Win32
        result = user32.RegisterHotKey(None, _HOTKEY_ID, self._mods | MOD_NOREPEAT, self._vk)
        if result == 0:
            self._error = "Комбинация занята другой программой"
            self._registered = False
            self._ready.set()
            return

        self._registered = True
        self._ready.set()

        msg = ctypes.wintypes.MSG()
        try:
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == WM_HOTKEY:
                    try:
                        self._callback()
                    except Exception:
                        # Исключение не должно убивать цикл сообщений
                        pass
        finally:
            self._registered = False
            user32.UnregisterHotKey(None, _HOTKEY_ID)
            self._thread_id = 0