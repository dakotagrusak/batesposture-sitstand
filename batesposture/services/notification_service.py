from __future__ import annotations

import logging
import sys
from time import monotonic

from .platform_notification import send_notification
from .settings_service import SettingsService

logger = logging.getLogger(__name__)


class NotificationService:
    """Sends desktop notifications for posture alerts and tracking status changes.

    Respects ``notifications_enabled`` and ``focus_mode_enabled`` runtime settings.

    A slump spell starts the first time the score drops below the threshold.
    The first toast fires immediately. While the score stays down, a louder
    repeat fires every ``SLUMP_REPEAT_S`` seconds — the stored cooldown only
    gates *starting a new spell* after posture has recovered. That is what
    makes sustained slumping feel like an alarm instead of a one-off toast.
    """

    TREND_DROP_POINTS: float = 8.0
    SLUMP_REPEAT_S: float = 45.0
    RECOVERY_S: float = 20.0

    def __init__(self, settings: SettingsService, icon_path: str) -> None:
        self._settings = settings
        self._icon_path = icon_path
        self._last_notification_time: float = 0.0
        self._last_trend_notification_time: float = 0.0
        self._slump_active = False
        self._slump_alerts = 0
        self._recovered_at: float = 0.0

    def notify_interval_change(self, message: str) -> None:
        runtime = self._settings.runtime
        if message and runtime.notifications_enabled and not runtime.focus_mode_enabled:
            send_notification(message, "Tracking Interval Changed", self._icon_path)

    def maybe_notify_posture(self, posture_score: float) -> None:
        runtime = self._settings.runtime
        if not runtime.notifications_enabled or runtime.focus_mode_enabled:
            return

        now = monotonic()
        slumping = posture_score < runtime.poor_posture_threshold

        if not slumping:
            if self._slump_active:
                self._recovered_at = now
            self._slump_active = False
            self._slump_alerts = 0
            return

        if not self._slump_active:
            # New spell. Cooldown after a recent recovery still applies.
            if (
                self._recovered_at
                and now - self._recovered_at < self.RECOVERY_S
                and now - self._last_notification_time < runtime.notification_cooldown
            ):
                return
            self._slump_active = True
            self._slump_alerts = 0

        if self._slump_alerts == 0:
            self._fire_slump(posture_score, now, first=True)
            return

        if now - self._last_notification_time >= self.SLUMP_REPEAT_S:
            self._fire_slump(posture_score, now, first=False)

    def _fire_slump(self, score: float, now: float, first: bool) -> None:
        runtime = self._settings.runtime
        self._slump_alerts += 1
        self._last_notification_time = now
        if first:
            title = "Sit up — slumping"
            body = runtime.default_posture_message
        else:
            title = f"Still slumping ({self._slump_alerts}x)"
            body = f"Score {score:.0f}. Sit up straight."
        send_notification(body, title, self._icon_path)
        _beep()

    def maybe_notify_trend(self, score_service) -> None:
        """Nudge the user when rolling posture has declined meaningfully.

        Catches gradual slumps that never cross the absolute threshold. Uses an
        independent cooldown from the threshold alert so the two don't interfere.
        """
        runtime = self._settings.runtime
        if not runtime.notifications_enabled or runtime.focus_mode_enabled:
            return
        decline = score_service.recent_decline()
        if decline is None or decline < self.TREND_DROP_POINTS:
            return
        current_time = monotonic()
        if (
            current_time - self._last_trend_notification_time
            <= min(runtime.notification_cooldown, 90)
        ):
            return
        send_notification(
            f"Your posture has slipped {decline:.0f} points — sit up now.",
            "Posture dropping",
            self._icon_path,
        )
        self._last_trend_notification_time = current_time
        _beep()


def _beep() -> None:
    """Windows system exclamation; no-op elsewhere. Must never break tracking."""
    if sys.platform != "win32":
        return
    try:
        import winsound

        winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
    except Exception:  # noqa: BLE001
        logger.debug("Slump beep skipped", exc_info=True)
