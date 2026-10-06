# SPDX-License-Identifier: GPL-3.0-or-later
"""The sliding preferences panel.

Every control writes straight through to :class:`~app.core.settings.Settings`
and emits a signal, so preferences are persistent by construction — there is no
"apply" button and nothing to forget to save.
"""

from __future__ import annotations

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QMouseEvent, QPainter, QPaintEvent
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

__all__ = ["SettingsPanel"]

#: How wide the drawer's text column should read, in characters of the UI font.
#: The panel used to carry a pixel width instead, which silently assumed one
#: font: the same constant that left room to spare under DejaVu Sans clipped the
#: content under Segoe UI, because the panel's real width comes from its
#: ``sizeHint`` and that shrank with the narrower font.  Measuring in characters
#: keeps the proportion the design intended on any font, at any DPI.
TEXT_COLUMN_CHARS = 34

#: The swatch grid's preferred shape.  It reflows to fewer columns when the
#: panel is squeezed, but never spreads wider than this.
SWATCH_COLUMNS = 2


class _Swatch(QWidget):
    """Clickable preview of one palette.

    Reports honest size hints derived from its own label, so the grid above it
    can tell when two of them no longer fit side by side.  It previously
    reported none at all, which is why the panel had no idea it was being drawn
    narrower than its contents needed.
    """

    clicked = pyqtSignal(str)

    #: Inset of the painted card inside the widget.
    CARD_INSET = 4
    #: Gap between the card's top edge and the row of accent dots.
    DOT_TOP_GAP = 9
    DOT_SIZE = 11
    DOT_GAP = 5
    #: Breathing room either side of the palette name.
    LABEL_PADDING = 8

    def __init__(self, palette: Palette, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("themeSwatch")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(palette.name)
        self._palette = palette
        self._selected = False
        self.setProperty("selected", "false")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    @property
    def key(self) -> str:
        return self._palette.key

    # ----------------------------------------------------------- measurement
    def _label_font(self) -> QFont:
        """The font the palette name is painted in.

        Used for both painting and measuring, so the two can never disagree
        about how much room the name needs.
        """
        font = QFont(self.font())
        font.setPointSizeF(max(7.5, font.pointSizeF() - 1.5))
        return font

    def _dots_width(self) -> int:
        count = 4
        return count * self.DOT_SIZE + (count - 1) * self.DOT_GAP

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt API
        metrics = QFontMetrics(self._label_font())
        content = max(
            metrics.horizontalAdvance(self._palette.name) + 2 * self.LABEL_PADDING,
            self._dots_width() + 2 * self.LABEL_PADDING,
        )
        height = (
            2 * self.CARD_INSET
            + self.DOT_TOP_GAP
            + self.DOT_SIZE
            + self.DOT_GAP
            + metrics.height()
            + self.DOT_TOP_GAP
        )
        return QSize(content + 2 * self.CARD_INSET, height)

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt API
        return self.minimumSizeHint()

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

        inset = self.CARD_INSET
        rect = self.rect().adjusted(inset, inset, -inset, -inset)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(self._palette.bg))
        painter.drawRoundedRect(rect, 6, 6)

        dots = [
            self._palette.accent,
            self._palette.accent2,
            self._palette.green,
            self._palette.cyan,
        ]
        x = rect.center().x() - self._dots_width() // 2
        y = rect.top() + self.DOT_TOP_GAP
        for color in dots:
            painter.setBrush(QColor(color))
            painter.drawEllipse(x, y, self.DOT_SIZE, self.DOT_SIZE)
            x += self.DOT_SIZE + self.DOT_GAP

        painter.setPen(QColor(self._palette.on("text", "bg")))
        painter.setFont(self._label_font())
        label_top = self.DOT_TOP_GAP + self.DOT_SIZE + self.DOT_GAP
        painter.drawText(
            rect.adjusted(self.LABEL_PADDING, label_top, -self.LABEL_PADDING, 0),
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
            # Elide rather than spill, for the case where the panel has been
            # squeezed below even the single-column width.
            QFontMetrics(self._label_font()).elidedText(
                self._palette.name,
                Qt.TextElideMode.ElideRight,
                rect.width() - 2 * self.LABEL_PADDING,
            ),
        )
        painter.end()

    def mouseReleaseEvent(self, event: QMouseEvent | None) -> None:  # noqa: N802 - Qt API
        if event is not None and event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.key)
        super().mouseReleaseEvent(event)


class _SwatchGrid(QWidget):
    """Theme swatches laid out in as many columns as actually fit.

    A fixed two-column grid is right at the panel's natural width, but it has
    to give way rather than clip when the window is too narrow to grant that —
    on a small screen, or beside a maximised editor at the minimum window size.
    """

    def __init__(self, swatches: list[_Swatch], spacing: int, parent: QWidget | None = None):
        super().__init__(parent)
        self._swatches = swatches
        self._columns = 0
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setSpacing(spacing)
        for swatch in swatches:
            swatch.setParent(self)
        self._apply(SWATCH_COLUMNS)

    def _widest_swatch(self) -> int:
        return max(s.minimumSizeHint().width() for s in self._swatches)

    def columns_for(self, width: int) -> int:
        """How many columns of swatches fit in ``width``."""
        needed = self._widest_swatch()
        spacing = self._grid.spacing()
        fit = (width + spacing) // (needed + spacing)
        return max(1, min(SWATCH_COLUMNS, int(fit)))

    def natural_width(self) -> int:
        """Width at which the grid shows its preferred number of columns."""
        return (
            SWATCH_COLUMNS * self._widest_swatch()
            + (SWATCH_COLUMNS - 1) * self._grid.spacing()
        )

    def _apply(self, columns: int) -> None:
        if columns == self._columns:
            return
        self._columns = columns
        for swatch in self._swatches:
            self._grid.removeWidget(swatch)
        for index, swatch in enumerate(self._swatches):
            self._grid.addWidget(swatch, index // columns, index % columns)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().resizeEvent(event)
        self._apply(self.columns_for(self.width()))

    def refresh_metrics(self) -> None:
        """Re-evaluate the column count after a font or DPI change."""
        self._apply(self.columns_for(self.width()))


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
        # Vertical scrolling is the escape valve when the content is taller than
        # the window; horizontally there is nowhere to go, so everything inside
        # must be able to shrink or wrap instead.
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._scroll = scroll

        content = QWidget()
        # Deliberately no minimum width. Pinning one here is what used to force
        # the content wider than the viewport and clip it, since the panel's own
        # width is decided by sizeHint rather than by that constant.
        self._content = content
        body = QVBoxLayout(content)
        body.setContentsMargins(18, 18, 18, 24)
        body.setSpacing(14)
        self._body_margins = body.contentsMargins()

        title = QLabel("Preferences")
        title.setObjectName("panelTitle")
        body.addWidget(title)
        body.addWidget(_separator())

        # ------------------------------------------------------------ theme
        body.addWidget(_section("Theme"))
        self._swatches: list[_Swatch] = []
        for palette in PALETTES.values():
            swatch = _Swatch(palette)
            swatch.clicked.connect(self.theme_selected.emit)
            self._swatches.append(swatch)
        self._grid = _SwatchGrid(self._swatches, spacing=8)
        body.addWidget(self._grid)
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

    # ---------------------------------------------------------------- sizing
    def preferred_width(self) -> int:
        """The width at which nothing in the drawer is clipped.

        Computed from the content every time it is asked for, rather than
        stored as a constant, because all three inputs move underneath us: the
        UI font size is a user preference, the system font differs per platform,
        and the whole lot rescales when the window moves to a monitor at another
        DPI.  A number that is correct on one of those combinations is wrong on
        the next, which is exactly how the panel came to clip on Windows.

        The result is the widest of what the content genuinely needs, floored by
        the design's intended proportions, plus room for the scrollbar.
        """
        metrics = QFontMetrics(self.font())
        margins = self._body_margins.left() + self._body_margins.right()

        # What the design wants: a comfortable text column, and a swatch grid
        # wide enough to stay in two columns.
        text_column = metrics.averageCharWidth() * TEXT_COLUMN_CHARS
        design = margins + max(text_column, self._grid.natural_width())

        # What the content will not go below without clipping — the widest
        # control that cannot wrap or shrink, such as the longest checkbox.
        required = self._content.minimumSizeHint().width()

        return max(design, required) + self._scrollbar_allowance()

    def _scrollbar_allowance(self) -> int:
        """Room for the vertical scrollbar, which overlays nothing on Qt."""
        bar = self._scroll.verticalScrollBar()
        return bar.sizeHint().width() if bar is not None else 0

    def refresh_metrics(self) -> None:
        """Recompute layout decisions after a font or DPI change."""
        for swatch in self._swatches:
            swatch.updateGeometry()
        self._grid.refresh_metrics()
        self.updateGeometry()

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
