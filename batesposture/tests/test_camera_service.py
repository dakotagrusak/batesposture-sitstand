from __future__ import annotations

import threading
from unittest.mock import MagicMock

from ..services import camera_service as camera_module
from ..services.camera_service import CameraService
from ..services.settings_service import SettingsService


def _service(tmp_path):
    settings = SettingsService.for_testing(tmp_path / "camera_settings.ini")
    return CameraService(settings)


def test_start_releases_camera_when_device_cannot_open(tmp_path, monkeypatch):
    monkeypatch.setattr(camera_module, "open_camera", lambda camera_id: None)
    service = _service(tmp_path)

    assert not service.start()
    assert service._cap is None
    assert not service._is_running.is_set()


def _run_loop_for_iterations(monkeypatch, service, iterations):
    """Let _capture_loop run for a fixed number of time.sleep() calls, then stop it."""
    remaining = [iterations]

    def fake_sleep(_seconds):
        remaining[0] -= 1
        if remaining[0] <= 0:
            service._is_running.clear()

    monkeypatch.setattr(camera_module.time, "sleep", fake_sleep)
    service._is_running.set()
    service._thread = threading.current_thread()
    service._capture_loop()


def test_failed_frame_read_releases_and_retries_without_stopping(tmp_path, monkeypatch):
    capture = MagicMock()
    capture.read.return_value = (False, None)
    monkeypatch.setattr(camera_module, "session_locked", lambda: False)
    service = _service(tmp_path)
    service._cap = capture

    _run_loop_for_iterations(monkeypatch, service, iterations=1)

    capture.release.assert_called_once_with()
    assert service._cap is None
    assert service._is_running.is_set() is False  # stopped by the test harness, not by stop()


def test_session_locked_releases_camera_without_stopping(tmp_path, monkeypatch):
    capture = MagicMock()
    monkeypatch.setattr(camera_module, "session_locked", lambda: True)
    service = _service(tmp_path)
    service._cap = capture

    _run_loop_for_iterations(monkeypatch, service, iterations=1)

    capture.release.assert_called_once_with()
    assert service._cap is None
    assert service._released_for_hello is True


def test_camera_reopens_after_hello_unlock(tmp_path, monkeypatch):
    locked_calls = {"count": 0}

    def fake_session_locked():
        locked_calls["count"] += 1
        return locked_calls["count"] == 1

    reopened = MagicMock()
    reopened.read.return_value = (True, "frame")
    open_camera = MagicMock(return_value=reopened)
    monkeypatch.setattr(camera_module, "session_locked", fake_session_locked)
    monkeypatch.setattr(camera_module, "open_camera", open_camera)
    service = _service(tmp_path)
    service._cap = MagicMock()

    _run_loop_for_iterations(monkeypatch, service, iterations=2)

    open_camera.assert_called_once_with(service._camera_id)
    assert service._cap is reopened
    assert service._released_for_hello is False


def test_stop_releases_camera_when_thread_does_not_exit(tmp_path, caplog):
    capture = MagicMock()
    thread = MagicMock()
    thread.is_alive.return_value = True
    service = _service(tmp_path)
    service._cap = capture
    service._thread = thread
    service._is_running.set()

    service.stop()

    thread.join.assert_called_once_with(timeout=2.0)
    capture.release.assert_called_once_with()
    assert "did not stop within 2 seconds" in caplog.text
