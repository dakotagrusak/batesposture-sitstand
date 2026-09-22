from __future__ import annotations

from ..services import windows_session


def test_session_locked_is_always_false_off_windows(monkeypatch):
    monkeypatch.setattr(windows_session.sys, "platform", "linux")
    assert windows_session.session_locked() is False

    monkeypatch.setattr(windows_session.sys, "platform", "darwin")
    assert windows_session.session_locked() is False
