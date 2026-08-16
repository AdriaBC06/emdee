# SPDX-License-Identifier: GPL-3.0-or-later
"""The sliding preferences panel.

Every control writes straight through to :class:`~app.core.settings.Settings`
and emits a signal, so preferences are persistent by construction — there is no
"apply" button and nothing to forget to save.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QMouseEvent, QPainter, QPaintEvent
from PyQt6.QtWidgets import (
    QCheckBox,
    QFrame,
    QGridLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from ..core.settings import Settings
from ..themes.palettes import PALETTES, Palette

__all__ = ["SettingsPanel", "PANEL_WIDTH"]

PANEL_WIDTH = 292


class _Swatch(QWidget):
    """Clickable preview of one palette."""

    clicked = pyqtSignal(str)

    def __init__(self, palette: Palette, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("themeSwatch")
        self.setFixedHeight(58)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(palette.name)
        self._palette = palette
        self._selected = False
        self.setProperty("selected", "false")

    @property
    def key(self) -> str:
        return self._palette.key

    def set_selected(self, selected: bool) -> None:
        if selected == self._selected:
            return
        self._selected = selected
        self.setProperty("selected", "true" if selected else "false")
        style = self.style()
        if style is not None:
            style.unpolish(self)
            style.polish(self)
        self.update()

    def paintEvent(self, event: QPaintEvent | None) -> None:  # noqa: N802 - Qt API
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        rect = self.rect().adjusted(4, 4, -4, -4)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(self._palette.bg))
        painter.drawRoundedRect(rect, 6, 6)

        dots = [
            self._palette.accent,
            self._palette.accent2,
            self._palette.green,
            self._palette.cyan,
        ]
        size = 11
        gap = 5
        total = len(dots) * size + (len(dots) - 1) * gap
        x = rect.center().x() - total // 2
        y = rect.top() + 9
        for color in dots:
            painter.setBrush(QColor(color))
            painter.drawEllipse(x, y, size, size)
            x += size + gap

        painter.setPen(QColor(self._palette.on("text", "bg")))
        font = painter.font()
        font.setPointSizeF(max(7.5, font.pointSizeF() - 1.5))
        painter.setFont(font)
        painter.drawText(
            rect.adjusted(4, 26, -4, -4),
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
            self._palette.name,
        )
        painter.end()

    def mouseReleaseEvent(self, event: QMouseEvent | None) -> None:  # noqa: N802 - Qt API
        if event is not None and event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.key)
        super().mouseReleaseEvent(event)


def _section(text: str) -> QLabel:
    label = QLabel(text.upper())
    label.setObjectName("sectionLabel")
    return label


def _separator() -> QFrame:
    line = QFrame()
    line.setObjectName("panelSeparator")
    line.setFrameShape(QFrame.Shape.HLine)
    line.setFixedHeight(1)
    return line


class _LabelledSlider(QWidget):
    """A slider with a caption that shows the live value."""

    value_changed = pyqtSignal(int)

    def __init__(
        self, caption: str, minimum: int, maximum: int, value: int, suffix: str = ""
    ) -> None:
        super().__init__()
        self._caption = caption
        self._suffix = suffix

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self._label = QLabel()
        self._label.setObjectName("hintLabel")
        self._slider = QSlider(Qt.Orientation.Horizontal)
        self._slider.setRange(minimum, maximum)
        self._slider.setValue(value)
        self._slider.setPageStep(1)
        self._slider.valueChanged.connect(self._on_change)

        layout.addWidget(self._label)
        layout.addWidget(self._slider)
        self._update_label(value)

    def _update_label(self, value: int) -> None:
        self._label.setText(f"{self._caption}: {value}{self._suffix}")

    def _on_change(self, value: int) -> None:
        self._update_label(value)
        self.value_changed.emit(value)


class SettingsPanel(QWidget):
    """Right-hand preferences drawer."""

    theme_selected = pyqtSignal(str)
    editor_font_changed = pyqtSignal(int)
    preview_font_changed = pyqtSignal(int)
    tab_width_changed = pyqtSignal(int)
    word_wrap_changed = pyqtSignal(bool)
    line_numbers_changed = pyqtSignal(bool)
    sync_scroll_changed = pyqtSignal(bool)
    native_decorations_changed = pyqtSignal(bool)

    def __init__(self, settings: Settings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("settingsPanel")
        self.setMinimumWidth(0)
        self.setMaximumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        content = QWidget()
        content.setMinimumWidth(PANEL_WIDTH - 16)
        body = QVBoxLayout(content)
        body.setContentsMargins(18, 18, 18, 24)
        body.setSpacing(14)

        title = QLabel("Preferences")
        title.setObjectName("panelTitle")
        body.addWidget(title)
        body.addWidget(_separator())

        # ------------------------------------------------------------ theme
        body.addWidget(_section("Theme"))
        grid = QGridLayout()
        grid.setSpacing(8)
        self._swatches: list[_Swatch] = []
        for index, palette in enumerate(PALETTES.values()):
            swatch = _Swatch(palette)
            swatch.clicked.connect(self.theme_selected.emit)
            grid.addWidget(swatch, index // 2, index % 2)
            self._swatches.append(swatch)
        body.addLayout(grid)
        body.addWidget(_separator())

        # ----------------------------------------------------------- typography
        body.addWidget(_section("Typography"))
        self._editor_font = _LabelledSlider(
            "Editor font size", 9, 28, settings.editor_font_size, " pt"
        )
        self._editor_font.value_changed.connect(self.editor_font_changed.emit)
        body.addWidget(self._editor_font)

        self._preview_font = _LabelledSlider(
            "Preview font size", 12, 26, settings.preview_font_size, " px"
        )
        self._preview_font.value_changed.connect(self.preview_font_changed.emit)
        body.addWidget(self._preview_font)

        self._tab_width = _LabelledSlider(
            "Tab width", 2, 8, settings.tab_width, " spaces"
        )
        self._tab_width.value_changed.connect(self.tab_width_changed.emit)
        body.addWidget(self._tab_width)
        body.addWidget(_separator())

        # ----------------------------------------------------------- toggles
        body.addWidget(_section("Editor"))
        self._wrap = self._checkbox("Wrap long lines", settings.word_wrap)
        self._wrap.toggled.connect(self.word_wrap_changed.emit)
        body.addWidget(self._wrap)

        self._numbers = self._checkbox("Show line numbers", settings.show_line_numbers)
        self._numbers.toggled.connect(self.line_numbers_changed.emit)
        body.addWidget(self._numbers)

        self._sync = self._checkbox("Synchronise scrolling", settings.sync_scroll)
        self._sync.toggled.connect(self.sync_scroll_changed.emit)
        body.addWidget(self._sync)
        body.addWidget(_separator())

        # ------------------------------------------------------------ window
        body.addWidget(_section("Window"))
        self._native = self._checkbox(
            "Use system title bar", settings.native_decorations
        )
        self._native.toggled.connect(self.native_decorations_changed.emit)
        body.addWidget(self._native)

        hint = QLabel(
            "Turn this on if the custom title bar misbehaves with your "
            "window manager. Takes effect after a restart."
        )
        hint.setObjectName("hintLabel")
        hint.setWordWrap(True)
        body.addWidget(hint)

        body.addStretch(1)
        scroll.setWidget(content)
        outer.addWidget(scroll)

        self.set_theme(settings.theme)

    @staticmethod
    def _checkbox(text: str, checked: bool) -> QCheckBox:
        box = QCheckBox(text)
        box.setChecked(checked)
        box.setCursor(Qt.CursorShape.PointingHandCursor)
        return box

    # ---------------------------------------------------------------- state
    def set_theme(self, key: str) -> None:
        """Reflect the active theme in the swatch grid."""
        for swatch in self._swatches:
            swatch.set_selected(swatch.key == key)

    def refresh_swatches(self) -> None:
        for swatch in self._swatches:
            swatch.update()
