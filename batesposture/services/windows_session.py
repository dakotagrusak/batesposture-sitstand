"""Detect the Windows lock screen so the webcam can be given back to Hello."""

from __future__ import annotations

import sys


def session_locked() -> bool:
    """True when the secure desktop (lock screen / Windows Hello) is up.

    OpenInputDesktop fails from a normal app while the lock screen owns input.
    Other platforms always return False.
    """
    if sys.platform != "win32":
        return False
    import ctypes

    user32 = ctypes.windll.user32
    # DESKTOP_SWITCHDESKTOP (0x0100). NULL means we are not on the input desktop.
    desktop = user32.OpenInputDesktop(0, False, 0x0100)
    if not desktop:
        return True
    user32.CloseDesktop(desktop)
    return False
