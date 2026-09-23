"""System-wide hotkeys via Win32 RegisterHotKey.

A QAction shortcut only fires while one of the app's own windows has Qt
focus, which a tray-only app like BatesPosture rarely does. This registers
a hotkey with Windows itself so it fires no matter which application is
focused, as long as the user is logged in. Other platforms are a no-op:
register() returns False and nothing is wired up.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import logging
import sys
from collections.abc import Callable

from PyQt6.QtCore import QAbstractNativeEventFilter
from PyQt6.QtWidgets import QApplication

logger = logging.getLogger(__name__)

WM_HOTKEY = 0x0312
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_NOREPEAT = 0x4000  # fire once per press, not once per key-repeat tick


class GlobalHotkeyManager:
    """Registers global hotkeys on win32; harmless no-op on other platforms."""

    def __init__(self) -> None:
        self._callbacks: dict[int, Callable[[], None]] = {}
        self._next_id = 1
        self._filter: _HotkeyEventFilter | None = None

    @property
    def supported(self) -> bool:
        return sys.platform == "win32"

    def register(self, modifiers: int, virtual_key: int, callback: Callable[[], None]) -> bool:
        """Register *callback* to run when the given hotkey is pressed system-wide."""
        if not self.supported:
            return False
        hotkey_id = self._next_id
        self._next_id += 1
        if not ctypes.windll.user32.RegisterHotKey(0, hotkey_id, modifiers, virtual_key):
            logger.warning("Failed to register global hotkey (id=%s)", hotkey_id)
            return False
        self._callbacks[hotkey_id] = callback
        self._install_filter()
        return True

    def dispatch(self, hotkey_id: int) -> None:
        """Invoke the callback registered for *hotkey_id*, if any."""
        callback = self._callbacks.get(hotkey_id)
        if callback:
            callback()

    def unregister_all(self) -> None:
        if not self.supported:
            return
        for hotkey_id in list(self._callbacks):
            ctypes.windll.user32.UnregisterHotKey(0, hotkey_id)
        self._callbacks.clear()
        if self._filter is not None:
            app = QApplication.instance()
            if app is not None:
                app.removeNativeEventFilter(self._filter)
            self._filter = None

    def _install_filter(self) -> None:
        if self._filter is not None:
            return
        self._filter = _HotkeyEventFilter(self)
        app = QApplication.instance()
        if app is not None:
            app.installNativeEventFilter(self._filter)


class _HotkeyEventFilter(QAbstractNativeEventFilter):
    def __init__(self, manager: GlobalHotkeyManager) -> None:
        super().__init__()
        self._manager = manager

    def nativeEventFilter(self, event_type, message):
        if event_type == b"windows_generic_MSG":
            msg = ctypes.wintypes.MSG.from_address(int(message))
            if msg.message == WM_HOTKEY:
                self._manager.dispatch(msg.wParam)
        return False, 0
