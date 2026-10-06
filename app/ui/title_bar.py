# SPDX-License-Identifier: GPL-3.0-or-later
"""Frameless-window title bar in the PyDracula style.

Dragging is handed to the platform rather than done with manual geometry
arithmetic, but *which* platform mechanism differs:

* On Linux, ``QWindow.startSystemMove`` asks the compositor to take over.  It
  is the only approach that works on Wayland, where a client may not position
  its own surface, and it gives correct edge snapping on X11 for free.
* On Windows the window keeps its real frame and reports this widget as
  ``HTCAPTION`` from the hit test (see :mod:`app.ui.win_chrome`), so the
  desktop drags, snaps and shows the system menu with no help from us.  Calling
  ``startSystemMove`` there as well would start a *second*, competing drag.
"""

from __future__ import annotations

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtGui import QMouseEvent, QPixmap
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .. import APP_NAME
from ..platform_support import uses_native_frame_hit_testing
from .icons import logo_pixmap, themed_icon

__all__ = ["TitleBar"]


class TitleBar(QWidget):
    """Custom window chrome: logo, document title, menu and window buttons."""

    minimize_requested = pyqtSignal()
    maximize_requested = pyqtSignal()
    close_requested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("titleBar")
        self.setFixedHeight(56)
        self._maximized = False

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 0, 8, 0)
        layout.setSpacing(12)

        self._logo = QLabel(self)
        self._logo.setFixedSize(QSize(26, 26))
        self._logo.setPixmap(self._scaled_logo())
        self._logo.setScaledContents(True)

        # The name and its folder are stacked, so they need room to breathe:
        # a little leading between them and a little air above and below.
        titles = QVBoxLayout()
        titles.setContentsMargins(0, 6, 0, 6)
        titles.setSpacing(3)
        self._title = QLabel(APP_NAME, self)
        self._title.setObjectName("titleLabel")
        self._subtitle = QLabel("", self)
        self._subtitle.setObjectName("subtitleLabel")
        for label in (self._title, self._subtitle):
            label.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        titles.addWidget(self._title)
        titles.addWidget(self._subtitle)

        layout.addWidget(self._logo)
        layout.addLayout(titles)
        layout.addStretch(1)

        self._buttons: dict[str, QPushButton] = {}
        for key, tooltip in (
            ("minus", "Minimise"),
            ("maximize", "Maximise / restore"),
            ("x", "Close"),
        ):
            button = QPushButton(self)
            button.setObjectName("windowButtonClose" if key == "x" else "windowButton")
            button.setFixedSize(QSize(30, 30))
            button.setIconSize(QSize(16, 16))
            button.setToolTip(tooltip)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            layout.addWidget(button)
            self._buttons[key] = button

        self._buttons["minus"].clicked.connect(self.minimize_requested.emit)
        self._buttons["maximize"].clicked.connect(self.maximize_requested.emit)
        self._buttons["x"].clicked.connect(self.close_requested.emit)

        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    # ------------------------------------------------------------- contents
    @staticmethod
    def _scaled_logo() -> QPixmap:
        return logo_pixmap(44)

    def set_document_title(self, title: str, subtitle: str = "") -> None:
        """Show the open document's name and its folder underneath."""
        self._title.setText(title)
        self._subtitle.setText(subtitle)
        self._subtitle.setVisible(bool(subtitle))

    def set_maximized(self, maximized: bool) -> None:
        self._buttons["maximize"].setToolTip("Restore" if maximized else "Maximise")
        self._maximized = maximized

    def retint(self, color: str, close_color: str) -> None:
        """Recolour the window buttons for the active theme."""
        self._buttons["minus"].setIcon(themed_icon("minus", color, 16))
        self._buttons["maximize"].setIcon(
            themed_icon("restore" if self._maximized else "maximize", color, 16)
        )
        self._buttons["x"].setIcon(themed_icon("x", close_color, 16))

    @property
    def window_buttons(self) -> list[QPushButton]:
        """The minimise/maximise/close buttons, in layout order.

        Platform chrome needs to know about these: on Windows they have to be
        carved out of the draggable caption region, or the hit test claims them
        and they stop responding to clicks.
        """
        return [self._buttons[key] for key in ("minus", "maximize", "x")]

    @property
    def maximize_button(self) -> QPushButton:
        """The maximise button, which Snap Layouts needs to locate by rectangle."""
        return self._buttons["maximize"]

    # --------------------------------------------------------------- events
    def mousePressEvent(self, event: QMouseEvent | None) -> None:  # noqa: N802 - Qt API
        if uses_native_frame_hit_testing():
            # The hit test already reported this widget as the caption; Windows
            # is running the drag itself and must not be interrupted.
            super().mousePressEvent(event)
            return
        if event is not None and event.button() == Qt.MouseButton.LeftButton:
            window = self.window().windowHandle()
            if window is not None:
                window.startSystemMove()
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent | None) -> None:  # noqa: N802 - Qt API
        if uses_native_frame_hit_testing():
            # Double-click-to-maximise on a caption is a system behaviour; it
            # arrives as WM_NCLBUTTONDBLCLK and never reaches this widget.
            super().mouseDoubleClickEvent(event)
            return
        if event is not None and event.button() == Qt.MouseButton.LeftButton:
            self.maximize_requested.emit()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)
