# SPDX-License-Identifier: GPL-3.0-or-later
"""Platform detection, in one place.

Every ``sys.platform`` test in Emdee lives here.  The rest of the code asks a
question about *capability* — "is there a native window frame to work with?" —
instead of asking which operating system it is running on, so a future third
platform means adding a branch here rather than hunting for scattered
conditionals in the user interface.

Nothing in this module imports Qt: it is used from ``main`` before the
application object exists, and from tests that never start a GUI.
"""

from __future__ import annotations

import sys

__all__ = [
    "IS_WINDOWS",
    "IS_LINUX",
    "IS_MACOS",
    "uses_native_frame_hit_testing",
    "set_app_user_model_id",
]

IS_WINDOWS: bool = sys.platform == "win32"
IS_LINUX: bool = sys.platform.startswith("linux")
IS_MACOS: bool = sys.platform == "darwin"


def uses_native_frame_hit_testing() -> bool:
    """True where custom chrome should keep the real window frame.

    On Windows the way to get a custom title bar without losing Aero Snap,
    snap layouts, the drop shadow and the resize borders is to keep an ordinary
    top-level window and suppress only the *drawing* of its non-client area
    (see :mod:`app.ui.win_chrome`).  Everywhere else Emdee sets
    ``FramelessWindowHint`` and asks the compositor to move and resize the
    window for it, which is the only thing that works under Wayland.
    """
    return IS_WINDOWS


def set_app_user_model_id(app_id: str) -> None:
    """Tell Windows which Start-menu entry this process belongs to.

    Without an explicit AppUserModelID a Python-hosted process inherits the
    interpreter's identity: the taskbar shows a generic Python icon and refuses
    to group the window with Emdee's own shortcut or jump list.  A no-op
    everywhere else.
    """
    if not IS_WINDOWS:
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
    except (AttributeError, OSError):  # pragma: no cover - cosmetic only
        pass
