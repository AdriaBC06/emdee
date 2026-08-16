# SPDX-License-Identifier: GPL-3.0-or-later
"""Reusable icon tool strips.

Actions are created once in the main window (so a single ``QAction`` drives the
menu entry, the keyboard shortcut and the button) and merely *rendered* here.
The strip remembers which icon belongs to which button so it can retint itself
when the theme changes.
"""

from __future__ import annotations

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QSizePolicy,
    QToolButton,
    QWidget,
)

from .icons import themed_icon

__all__ = ["ToolStrip"]


class ToolStrip(QWidget):
    """A horizontal row of icon buttons backed by :class:`QAction` objects."""

    def __init__(
        self,
        object_name: str = "toolBar",
        icon_size: int = 18,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName(object_name)
        self._icon_size = icon_size
        self._buttons: list[tuple[QToolButton, str]] = []

        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(8, 5, 8, 5)
        self._layout.setSpacing(2)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    # ------------------------------------------------------------- building
    def add_action(self, action: QAction, icon_name: str) -> QToolButton:
        """Append a button driven by ``action``."""
        button = QToolButton(self)
        button.setDefaultAction(action)
        button.setAutoRaise(True)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setIconSize(QSize(self._icon_size, self._icon_size))
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._layout.addWidget(button)
        self._buttons.append((button, icon_name))
        return button

    def add_separator(self) -> None:
        line = QFrame(self)
        line.setObjectName("toolSeparator")
        line.setFrameShape(QFrame.Shape.VLine)
        line.setFixedWidth(1)
        self._layout.addSpacing(4)
        self._layout.addWidget(line)
        self._layout.addSpacing(4)

    def add_stretch(self) -> None:
        self._layout.addStretch(1)

    def add_widget(self, widget: QWidget) -> None:
        self._layout.addWidget(widget)

    # ----------------------------------------------------------- appearance
    def retint(self, color: str, disabled_color: str) -> None:
        """Regenerate every icon for the active theme."""
        for button, icon_name in self._buttons:
            action = button.defaultAction()
            icon = themed_icon(icon_name, color, self._icon_size, disabled_color)
            if action is not None:
                action.setIcon(icon)
            else:  # pragma: no cover - all buttons currently have actions
                button.setIcon(icon)
