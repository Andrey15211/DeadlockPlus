"""Desktop-обёртки DeadlockForge.

Публичная поверхность:
    - hotkey: parse_hotkey(), format_hotkey(), HotkeyListener, HotkeyError
"""

from __future__ import annotations

from .hotkey import HotkeyError, HotkeyListener, format_hotkey, parse_hotkey

__all__ = [
    "HotkeyError",
    "HotkeyListener",
    "format_hotkey",
    "parse_hotkey",
]