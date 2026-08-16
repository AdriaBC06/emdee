# SPDX-License-Identifier: GPL-3.0-or-later
"""Frameless-window title bar in the PyDracula style.

Window dragging and resizing use ``QWindow.startSystemMove`` /
``startSystemResize`` rather than manual geometry arithmetic.  That is the only
approach that works on Wayland, where a client is not allowed to position its
own surface — and it also gives correct edge snapping on X11 for free.
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

    # --------------------------------------------------------------- events
    def mousePressEvent(self, event: QMouseEvent | None) -> None:  # noqa: N802 - Qt API
        if event is not None and event.button() == Qt.MouseButton.LeftButton:
            window = self.window().windowHandle()
            if window is not None:
                window.startSystemMove()
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent | None) -> None:  # noqa: N802 - Qt API
        if event is not None and event.button() == Qt.MouseButton.LeftButton:
            self.maximize_requested.emit()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)
