# SPDX-License-Identifier: GPL-3.0-or-later
"""Custom window chrome on Windows, without giving up the real window frame.

The obvious way to draw your own title bar — ``Qt.FramelessWindowHint`` — turns
the window into a ``WS_POPUP`` with no non-client area at all.  That is exactly
right on Wayland, where the compositor owns window placement and
``startSystemMove`` is the only sanctioned way to drag.  On Windows it throws
away things the desktop provides for free and users notice immediately:

* Aero Snap — dragging to an edge, ``Win`` + arrow keys
* Snap Layouts — the Windows 11 flyout when hovering the maximise button
* the drop shadow and the rounded corners
* resize borders that extend a few pixels *outside* the visible window
* the ``Alt+Space`` system menu, and double-click-to-maximise

So Emdee keeps an ordinary top-level window here and suppresses only the
*painting* of its non-client area, via ``WM_NCCALCSIZE``.  The frame is still
there as far as the desktop window manager is concerned; it is simply not drawn
over.  ``WM_NCHITTEST`` then tells Windows which parts of our own widgets
behave like a caption and which like a resize border, and every behaviour above
comes back on its own.

Nothing in this module is imported on other platforms.
"""

from __future__ import annotations

import ctypes
import logging
from collections.abc import Callable
from ctypes import wintypes
from typing import TYPE_CHECKING

from PyQt6.QtCore import QByteArray, QPoint, Qt

if TYPE_CHECKING:
    from PyQt6.QtWidgets import QWidget

log = logging.getLogger(__name__)

__all__ = ["WindowsChrome"]

# ------------------------------------------------------------------ Win32 API
WM_NCCALCSIZE = 0x0083
WM_NCHITTEST = 0x0084
WM_NCMOUSEMOVE = 0x00A0
WM_NCLBUTTONDOWN = 0x00A1
WM_NCLBUTTONUP = 0x00A2
WM_NCMOUSELEAVE = 0x02A2

HTCLIENT = 1
HTCAPTION = 2
HTMAXBUTTON = 9
HTLEFT = 10
HTRIGHT = 11
HTTOP = 12
HTTOPLEFT = 13
HTTOPRIGHT = 14
HTBOTTOM = 15
HTBOTTOMLEFT = 16
HTBOTTOMRIGHT = 17

SM_CXSIZEFRAME = 32
SM_CYSIZEFRAME = 33
SM_CXPADDEDBORDER = 92


#: ``LPARAM`` is signed, but when it carries a pointer we need the unsigned
#: value; masking keeps ``from_address`` correct on either architecture.
_POINTER_MASK = (1 << (ctypes.sizeof(ctypes.c_void_p) * 8)) - 1


class _MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("message", wintypes.UINT),
        ("wParam", wintypes.WPARAM),
        ("lParam", wintypes.LPARAM),
        ("time", wintypes.DWORD),
        ("pt", wintypes.POINT),
    ]


# Deliberately spelled as in the Windows SDK: these mirror C structs field for
# field, and renaming them would make it harder to check against the docs.
class _NCCALCSIZE_PARAMS(ctypes.Structure):  # noqa: N801 - Win32 struct name
    _fields_ = [("rgrc", wintypes.RECT * 3), ("lppos", ctypes.c_void_p)]


_user32 = ctypes.WinDLL("user32", use_last_error=True)
_user32.GetSystemMetrics.argtypes = [ctypes.c_int]
_user32.GetSystemMetrics.restype = ctypes.c_int
_user32.IsZoomed.argtypes = [wintypes.HWND]
_user32.IsZoomed.restype = wintypes.BOOL


class WindowsChrome:
    """Native hit-testing for a window that paints its own title bar.

    The owning window forwards ``nativeEvent`` here and asks
    :meth:`apply_window_flags` what to do at construction time.  Everything
    else — dragging, snapping, the system menu, resize cursors — is handled by
    Windows once the hit test answers correctly.
    """

    #: How far inside the window edge counts as "grab to resize", in logical
    #: pixels. Windows' own metric is used when it is larger; this is a floor,
    #: because the system value is only 4 px on a default configuration and a
    #: 4 px target is genuinely hard to hit with a mouse.
    RESIZE_MARGIN = 6

    def __init__(self, window: QWidget) -> None:
        self._window = window
        self._caption: QWidget | None = None
        self._exclusions: list[QWidget] = []
        self._maximize_button: QWidget | None = None
        self._on_maximize: Callable[[], None] | None = None

    # ------------------------------------------------------------------ setup
    def set_caption_widget(self, widget: QWidget) -> None:
        """Nominate the widget that behaves like a title bar."""
        self._caption = widget

    def set_exclusions(self, widgets: list[QWidget]) -> None:
        """Widgets inside the caption that must stay clickable, not draggable."""
        self._exclusions = widgets

    def set_maximize_button(self, widget: QWidget, on_click: Callable[[], None]) -> None:
        """Nominate the maximise button, so Snap Layouts can find it.

        Reporting ``HTMAXBUTTON`` for this rectangle is what makes the Windows 11
        layout flyout appear when the pointer rests on it.  The price is that the
        button is now part of the *non-client* area as far as Windows is
        concerned: clicks arrive as ``WM_NCLBUTTONUP`` and never reach the
        ``QPushButton``, so ``on_click`` is what actually toggles the window.
        Hover highlighting has to be driven by hand for the same reason.
        """
        self._maximize_button = widget
        self._on_maximize = on_click

    # -------------------------------------------------------------- geometry
    def _scale(self) -> float:
        """Physical pixels per logical pixel for this window."""
        handle = self._window.windowHandle()
        return handle.devicePixelRatio() if handle is not None else 1.0

    @staticmethod
    def _is_maximized(hwnd: int) -> bool:
        """Ask Windows directly, using the handle carried by the message.

        Deliberately *not* ``self._window.winId()``: the first ``WM_NCCALCSIZE``
        arrives while the window is still being created, and ``winId()`` would
        re-enter that creation to materialise a handle — which crashes the
        process with an access violation rather than returning anything.
        """
        return bool(_user32.IsZoomed(wintypes.HWND(hwnd)))

    def _frame_thickness(self) -> tuple[int, int]:
        """The border Windows reserves around a maximised sizable window."""
        padded = _user32.GetSystemMetrics(SM_CXPADDEDBORDER)
        return (
            _user32.GetSystemMetrics(SM_CXSIZEFRAME) + padded,
            _user32.GetSystemMetrics(SM_CYSIZEFRAME) + padded,
        )

    # ---------------------------------------------------------------- events
    def native_event(self, event_type: QByteArray, message: int) -> tuple[bool, int] | None:
        """Handle the two messages that matter; return ``None`` to pass on."""
        if event_type != b"windows_generic_MSG":
            return None
        msg = _MSG.from_address(int(message))

        if msg.message == WM_NCCALCSIZE:
            return self._on_nccalcsize(msg)
        if msg.message == WM_NCHITTEST:
            return self._on_nchittest(msg)
        if msg.message in (WM_NCLBUTTONDOWN, WM_NCLBUTTONUP):
            return self._on_nc_click(msg)
        if msg.message in (WM_NCMOUSEMOVE, WM_NCMOUSELEAVE):
            self._sync_maximize_hover(
                msg.wParam == HTMAXBUTTON and msg.message == WM_NCMOUSEMOVE
            )
            return None
        return None

    def _on_nc_click(self, msg: _MSG) -> tuple[bool, int] | None:
        """Turn a non-client click on the maximise button into a real action."""
        if msg.wParam != HTMAXBUTTON or self._maximize_button is None:
            return None
        if msg.message == WM_NCLBUTTONDOWN:
            self._maximize_button.setDown(True)
            return True, 0
        self._maximize_button.setDown(False)
        if self._on_maximize is not None:
            self._on_maximize()
        return True, 0

    def _sync_maximize_hover(self, hovered: bool) -> None:
        """Fake the hover state Windows took away from the maximise button."""
        if self._maximize_button is None:
            return
        if self._maximize_button.property("winHover") == hovered:
            return
        self._maximize_button.setProperty("winHover", hovered)
        self._maximize_button.setAttribute(
            Qt.WidgetAttribute.WA_UnderMouse, hovered
        )
        style = self._maximize_button.style()
        if style is not None:
            style.unpolish(self._maximize_button)
            style.polish(self._maximize_button)
        self._maximize_button.update()

    def _on_nccalcsize(self, msg: _MSG) -> tuple[bool, int]:
        """Grow the client area over the whole window, caption included.

        Returning zero with the proposed rectangle untouched is what removes
        the drawn title bar while leaving the frame — and therefore snapping and
        the shadow — intact.

        A maximised sizable window is deliberately positioned so its frame hangs
        off every edge of the monitor.  With the caption suppressed that would
        push our own content off-screen, so in that one state the rectangle is
        pulled back in by the frame thickness.
        """
        if not msg.wParam:
            return True, 0

        if self._is_maximized(msg.hwnd):
            params = _NCCALCSIZE_PARAMS.from_address(msg.lParam & _POINTER_MASK)
            rect = params.rgrc[0]
            frame_x, frame_y = self._frame_thickness()
            rect.left += frame_x
            rect.top += frame_y
            rect.right -= frame_x
            rect.bottom -= frame_y

        return True, 0

    def _on_nchittest(self, msg: _MSG) -> tuple[bool, int] | None:
        """Classify a point as resize border, caption, or ordinary content."""
        # lParam packs two *signed* 16-bit screen coordinates; a window dragged
        # onto a monitor left of the primary one produces negative values, and
        # reading them as unsigned puts the cursor 65000 px away.
        x = ctypes.c_short(msg.lParam & 0xFFFF).value
        y = ctypes.c_short((msg.lParam >> 16) & 0xFFFF).value

        local = self._window.mapFromGlobal(QPoint(int(x / self._scale()), int(y / self._scale())))
        width, height = self._window.width(), self._window.height()
        margin = self.RESIZE_MARGIN

        # A maximised window has no resize borders — offering them would resize
        # it out of its maximised state on a stray click at the screen edge.
        if not self._is_maximized(msg.hwnd):
            left = local.x() < margin
            right = local.x() >= width - margin
            top = local.y() < margin
            bottom = local.y() >= height - margin

            if top and left:
                return True, HTTOPLEFT
            if top and right:
                return True, HTTOPRIGHT
            if bottom and left:
                return True, HTBOTTOMLEFT
            if bottom and right:
                return True, HTBOTTOMRIGHT
            if left:
                return True, HTLEFT
            if right:
                return True, HTRIGHT
            if top:
                return True, HTTOP
            if bottom:
                return True, HTBOTTOM

        if self._maximize_button is not None and self._contains(self._maximize_button, local):
            return True, HTMAXBUTTON

        if self._caption is not None and self._contains(self._caption, local):
            for widget in self._exclusions:
                if self._contains(widget, local):
                    return True, HTCLIENT
            return True, HTCAPTION

        return None

    def _contains(self, widget: QWidget, point_in_window: QPoint) -> bool:
        """Is ``point_in_window`` inside ``widget``, and is it actually visible?"""
        if not widget.isVisible():
            return False
        top_left = widget.mapTo(self._window, QPoint(0, 0))
        return widget.rect().translated(top_left).contains(point_in_window)

    # ------------------------------------------------------------------ flags
    @staticmethod
    def window_flags() -> Qt.WindowType:
        """Flags for a window whose chrome we draw but whose frame we keep.

        Notably *not* ``FramelessWindowHint``: the frame is what Windows snaps,
        shadows and resizes, and the whole approach depends on it existing.
        """
        return Qt.WindowType.Window
