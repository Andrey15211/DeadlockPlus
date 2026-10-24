"""Иконка в системном трее Windows: правый клик — «Открыть» и «Выйти».

Без внешних зависимостей (pystray в сборку не тянем): Shell_NotifyIconW плюс
собственное окно-приёмник и цикл сообщений в отдельном потоке. pywebview уже
держит WinForms-цикл в главном потоке, поэтому трей получает свой: иначе
TrackPopupMenu заблокировал бы окно панели на время показа меню.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import sys
import threading
from collections.abc import Callable

WM_NULL = 0x0000
WM_CLOSE = 0x0010
WM_DESTROY = 0x0002
WM_COMMAND = 0x0111
WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONUP = 0x0205
WM_CONTEXTMENU = 0x007B
WM_APP = 0x8000
WM_TRAY = WM_APP + 1

NIM_ADD = 0
NIM_DELETE = 2
NIF_MESSAGE = 0x00000001
NIF_ICON = 0x00000002
NIF_TIP = 0x00000004

MF_STRING = 0x00000000
MF_SEPARATOR = 0x00000800
TPM_RIGHTBUTTON = 0x0002
TPM_NONOTIFY = 0x0080
TPM_RETURNCMD = 0x0100

IMAGE_ICON = 1
LR_LOADFROMFILE = 0x0010

ID_OPEN = 1
ID_QUIT = 2

_CLASS_NAME = "DeadlockForgeTrayWindow"
_LRESULT = ctypes.c_ssize_t
_WNDPROC = ctypes.WINFUNCTYPE(_LRESULT, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)


class _WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wt.UINT),
        ("lpfnWndProc", _WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wt.HINSTANCE),
        ("hIcon", wt.HICON),
        ("hCursor", wt.HANDLE),
        ("hbrBackground", wt.HBRUSH),
        ("lpszMenuName", wt.LPCWSTR),
        ("lpszClassName", wt.LPCWSTR),
    ]


class _NOTIFYICONDATAW(ctypes.Structure):
    """NOTIFYICONDATAW v3: поля после szTip нужны только для balloon-подсказок,
    но структура обязана быть полной — иначе Shell_NotifyIconW читает мусор."""

    _fields_ = [
        ("cbSize", wt.DWORD),
        ("hWnd", wt.HWND),
        ("uID", wt.UINT),
        ("uFlags", wt.UINT),
        ("uCallbackMessage", wt.UINT),
        ("hIcon", wt.HICON),
        ("szTip", wt.WCHAR * 128),
        ("dwState", wt.DWORD),
        ("dwStateMask", wt.DWORD),
        ("szInfo", wt.WCHAR * 256),
        ("uVersion", wt.UINT),
        ("szInfoTitle", wt.WCHAR * 64),
        ("dwInfoFlags", wt.DWORD),
        ("guidItem", ctypes.c_byte * 16),
        ("hBalloonIcon", wt.HICON),
    ]


class TrayIcon:
    """Иконка в трее с меню «Открыть» / «Выйти».

    Колбэки вызываются в потоке трея: они обязаны быть потокобезопасными
    (pywebview внутри сам маршалит show/hide/destroy в GUI-поток).
    """

    def __init__(
        self,
        icon_path: str,
        on_open: Callable[[], None],
        on_quit: Callable[[], None],
        tooltip: str = "Deadlock Plus",
    ) -> None:
        self._icon_path = str(icon_path)
        self._on_open = on_open
        self._on_quit = on_quit
        self._tooltip = tooltip[:127]
        self._thread: threading.Thread | None = None
        self._hwnd = 0
        self._nid: _NOTIFYICONDATAW | None = None
        self._ready = threading.Event()
        self._error: str | None = None
        self._wndproc = None  # держим ссылку: иначе GC снимает колбэк

    @property
    def error(self) -> str | None:
        """Текст ошибки запуска (не-Windows, нет иконки, отказ Shell_NotifyIcon)."""
        return self._error

    def start(self) -> bool:
        """Поднять поток трея и дождаться регистрации иконки."""
        if sys.platform != "win32":
            self._error = "Трей доступен только в Windows"
            return False
        if self._thread is not None and self._thread.is_alive():
            return self._hwnd != 0
        self._ready.clear()
        self._thread = threading.Thread(target=self._run, name="tray", daemon=True)
        self._thread.start()
        self._ready.wait(timeout=3.0)
        return self._hwnd != 0 and self._error is None

    def stop(self) -> None:
        """Убрать иконку и погасить поток трея; повторный вызов безопасен."""
        hwnd, thread = self._hwnd, self._thread
        if hwnd:
            ctypes.windll.user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        self._thread = None

    def _run(self) -> None:
        try:
            self._run_loop()
        except Exception as exc:  # поток не должен падать молча
            self._error = f"Трей не запустился: {exc}"
        finally:
            self._ready.set()

    def _run_loop(self) -> None:
        user32 = ctypes.windll.user32
        shell32 = ctypes.windll.shell32
        kernel32 = ctypes.windll.kernel32
        user32.DefWindowProcW.restype = _LRESULT
        user32.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
        user32.CreateWindowExW.restype = wt.HWND
        user32.LoadImageW.restype = wt.HANDLE
        user32.LoadImageW.argtypes = [
            wt.HINSTANCE, wt.LPCWSTR, wt.UINT, ctypes.c_int, ctypes.c_int, wt.UINT,
        ]
        user32.CreatePopupMenu.restype = wt.HMENU
        user32.TrackPopupMenu.restype = ctypes.c_int
        user32.TrackPopupMenu.argtypes = [
            wt.HMENU, wt.UINT, ctypes.c_int, ctypes.c_int,
            ctypes.c_int, wt.HWND, ctypes.c_void_p,
        ]
        user32.AppendMenuW.argtypes = [wt.HMENU, wt.UINT, ctypes.c_size_t, wt.LPCWSTR]
        user32.DestroyMenu.argtypes = [wt.HMENU]
        user32.SetForegroundWindow.argtypes = [wt.HWND]
        user32.GetCursorPos.argtypes = [ctypes.POINTER(wt.POINT)]
        shell32.Shell_NotifyIconW.argtypes = [wt.DWORD, ctypes.POINTER(_NOTIFYICONDATAW)]
        user32.DestroyWindow.argtypes = [wt.HWND]
        user32.PostQuitMessage.argtypes = [ctypes.c_int]
        user32.GetMessageW.argtypes = [ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT]

        hinst = kernel32.GetModuleHandleW(None)
        self._wndproc = _WNDPROC(self._handle)
        wc = _WNDCLASSW()
        wc.lpfnWndProc = self._wndproc
        wc.hInstance = hinst
        wc.lpszClassName = _CLASS_NAME
        icon = user32.LoadImageW(None, self._icon_path, IMAGE_ICON, 0, 0, LR_LOADFROMFILE)
        if icon:
            wc.hIcon = icon
        if not user32.RegisterClassW(ctypes.byref(wc)):
            self._error = "Не удалось зарегистрировать класс окна трея"
            self._ready.set()
            return

        hwnd = user32.CreateWindowExW(
            0, _CLASS_NAME, _CLASS_NAME, 0, 0, 0, 0, 0, None, None, hinst, None,
        )
        if not hwnd:
            self._error = "Не удалось создать окно трея"
            self._ready.set()
            return

        nid = _NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(_NOTIFYICONDATAW)
        nid.hWnd = hwnd
        nid.uID = 1
        nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        nid.uCallbackMessage = WM_TRAY
        nid.hIcon = icon or 0
        nid.szTip = self._tooltip
        if not shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid)):
            self._error = "Не удалось добавить иконку в трей"
            user32.DestroyWindow(hwnd)
            self._ready.set()
            return

        self._hwnd, self._nid = hwnd, nid
        self._ready.set()

        msg = wt.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

        self._hwnd = 0

    def _show_menu(self, hwnd: int) -> None:
        """Контекстное меню по правому клику; действие выполняем после его закрытия."""
        user32 = ctypes.windll.user32
        menu = user32.CreatePopupMenu()
        if not menu:
            return
        user32.AppendMenuW(menu, MF_STRING, ID_OPEN, "Открыть")
        user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
        user32.AppendMenuW(menu, MF_STRING, ID_QUIT, "Выйти")
        point = wt.POINT()
        user32.GetCursorPos(ctypes.byref(point))
        # Без SetForegroundWindow меню не закроется по клику мимо него.
        user32.SetForegroundWindow(hwnd)
        command = user32.TrackPopupMenu(
            menu, TPM_RETURNCMD | TPM_RIGHTBUTTON | TPM_NONOTIFY,
            point.x, point.y, 0, hwnd, None,
        )
        user32.PostMessageW(hwnd, WM_NULL, 0, 0)
        user32.DestroyMenu(menu)
        if command == ID_OPEN:
            self._on_open()
        elif command == ID_QUIT:
            self._on_quit()

    def _handle(self, hwnd, message, wparam, lparam) -> int:
        user32 = ctypes.windll.user32
        if message == WM_TRAY:
            event = int(lparam) & 0xFFFF
            if event in (WM_RBUTTONUP, WM_CONTEXTMENU):
                self._show_menu(hwnd)
            elif event in (WM_LBUTTONDBLCLK, WM_LBUTTONUP):
                self._on_open()
            return 0
        if message == WM_COMMAND:
            # Обычный путь — TrackPopupMenu с TPM_RETURNCMD; WM_COMMAND
            # поддержан для шелл-уведомлений и внешних вызовов.
            command = int(wparam) & 0xFFFF
            if command == ID_OPEN:
                self._on_open()
            elif command == ID_QUIT:
                self._on_quit()
            return 0
        if message == WM_CLOSE:
            user32.DestroyWindow(hwnd)
            return 0
        if message == WM_DESTROY:
            if self._nid is not None:
                ctypes.windll.shell32.Shell_NotifyIconW(NIM_DELETE,
                                                        ctypes.byref(self._nid))
                self._nid = None
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(hwnd, message, wparam, lparam)
