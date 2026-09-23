from __future__ import annotations

from unittest.mock import MagicMock

from ..services import global_hotkey as gh
from ..services.global_hotkey import GlobalHotkeyManager


def test_unsupported_platform_register_is_a_harmless_no_op(monkeypatch):
    monkeypatch.setattr(gh.sys, "platform", "linux")
    manager = GlobalHotkeyManager()

    assert manager.supported is False
    assert manager.register(gh.MOD_CONTROL, ord("T"), MagicMock()) is False
    manager.unregister_all()  # must not raise with nothing registered


def test_register_calls_win32_and_wires_up_dispatch(qapp, monkeypatch):
    user32 = MagicMock()
    user32.RegisterHotKey.return_value = 1
    monkeypatch.setattr(gh.ctypes, "windll", MagicMock(user32=user32))
    manager = GlobalHotkeyManager()
    callback = MagicMock()

    ok = manager.register(gh.MOD_CONTROL | gh.MOD_ALT, ord("T"), callback)

    assert ok is True
    user32.RegisterHotKey.assert_called_once_with(0, 1, gh.MOD_CONTROL | gh.MOD_ALT, ord("T"))
    manager.dispatch(1)
    callback.assert_called_once_with()

    manager.unregister_all()
    user32.UnregisterHotKey.assert_called_once_with(0, 1)


def test_register_returns_false_when_win32_call_fails(qapp, monkeypatch):
    user32 = MagicMock()
    user32.RegisterHotKey.return_value = 0
    monkeypatch.setattr(gh.ctypes, "windll", MagicMock(user32=user32))
    manager = GlobalHotkeyManager()

    assert manager.register(gh.MOD_CONTROL, ord("T"), MagicMock()) is False


def test_dispatch_ignores_unknown_hotkey_id():
    manager = GlobalHotkeyManager()
    manager.dispatch(999)  # no callback registered; must not raise
