# SPDX-License-Identifier: GPL-3.0-or-later
"""The Markdown source editor: a ``QPlainTextEdit`` with editing affordances.

Everything that can be expressed as a pure string transformation lives in
:mod:`app.core.textops`; this widget is only responsible for turning cursors
into offsets, applying the smallest possible replacement (so undo stays
granular) and painting the line-number gutter.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PyQt6.QtCore import QRect, QSize, Qt, pyqtSignal
from PyQt6.QtGui import (
    QColor,
    QFont,
    QFontDatabase,
    QFontMetricsF,
    QKeyEvent,
    QPainter,
    QPaintEvent,
    QResizeEvent,
    QTextBlock,
    QTextCursor,
    QTextFormat,
    QTextOption,
)
from PyQt6.QtWidgets import QPlainTextEdit, QTextEdit, QWidget

from ..core import textops
from ..core.textops import EditResult
from ..themes.palettes import Palette

__all__ = ["MarkdownEditor", "LineNumberArea"]

MONO_FAMILIES = (
    "JetBrains Mono",
    "Fira Code",
    "Cascadia Code",
    "Source Code Pro",
    "DejaVu Sans Mono",
    "Liberation Mono",
)


def _mono_font(point_size: int) -> QFont:
    """Pick the nicest monospace family that is actually installed."""
    available = set(QFontDatabase.families())
    for family in MONO_FAMILIES:
        if family in available:
            font = QFont(family)
            break
    else:
        font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
    font.setPointSize(point_size)
    font.setStyleHint(QFont.StyleHint.Monospace)
    font.setFixedPitch(True)
    return font


class LineNumberArea(QWidget):
    """Gutter painted by the editor it belongs to."""

    def __init__(self, editor: MarkdownEditor) -> None:
        super().__init__(editor)
        self._editor = editor
        self.setObjectName("lineNumberArea")

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt API
        return QSize(self._editor.line_number_area_width(), 0)

    def paintEvent(self, event: QPaintEvent | None) -> None:  # noqa: N802 - Qt API
        if event is not None:
            self._editor.paint_line_numbers(event)


class MarkdownEditor(QPlainTextEdit):
    """Plain-text Markdown editor with gutter, smart Enter and format actions."""

    #: Emitted when the vertical scroll position changes for a user reason.
    scrolled = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("editor")
        self.setFrameShape(QPlainTextEdit.Shape.NoFrame)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.setCursorWidth(2)
        self.setTabChangesFocus(False)
        self.setAcceptDrops(False)  # the main window owns drag & drop

        self._gutter = LineNumberArea(self)
        self._tab_width = 4
        self._show_line_numbers = True
        self._gutter_bg = QColor("#21222c")
        self._gutter_fg = QColor("#6272a4")
        self._gutter_fg_active = QColor("#bd93f9")
        self._current_line_bg = QColor("#2f3140")
        self._search_bg = QColor("#44475a")
        self._search_fg = QColor("#f8f8f2")
        self._current_match_bg = QColor("#bd93f9")
        self._current_match_fg = QColor("#282a36")
        self._search_ranges: list[tuple[int, int]] = []
        self._current_match: tuple[int, int] | None = None

        #: Injected by the main window so pasting an image can write a file.
        self.mime_handler: Callable[[Any], bool] | None = None

        self.blockCountChanged.connect(self._update_gutter_width)
        self.updateRequest.connect(self._update_gutter)
        self.cursorPositionChanged.connect(self._refresh_extra_selections)
        self.verticalScrollBar().valueChanged.connect(self.scrolled.emit)

        self.set_font_size(14)
        self._update_gutter_width()

    # ------------------------------------------------------------ appearance
    def apply_palette(self, palette: Palette) -> None:
        """Adopt the colours used for the gutter and the current-line stripe."""
        t = palette.tokens()
        self._gutter_bg = QColor(t["bg_alt"])
        self._gutter_fg = QColor(t["line_number"])
        self._gutter_fg_active = QColor(t["line_number_active"])
        self._current_line_bg = QColor(t["current_line"])
        self._search_bg = QColor(t["surface"])
        self._search_fg = QColor(t["text_on_surface"])
        self._current_match_bg = QColor(t["accent"])
        self._current_match_fg = QColor(t["accent_contrast"])
        self._refresh_extra_selections()
        self._gutter.update()

    def set_font_size(self, size: int) -> None:
        """Change the monospace font size and re-derive the tab stop."""
        font = _mono_font(size)
        self.setFont(font)
        self.document().setDefaultFont(font)
        self._apply_tab_stop()
        self._update_gutter_width()

    def set_tab_width(self, width: int) -> None:
        self._tab_width = max(2, min(8, width))
        self._apply_tab_stop()

    def _apply_tab_stop(self) -> None:
        metrics = QFontMetricsF(self.font())
        self.setTabStopDistance(metrics.horizontalAdvance(" ") * self._tab_width)

    def set_word_wrap(self, enabled: bool) -> None:
        self.setLineWrapMode(
            QPlainTextEdit.LineWrapMode.WidgetWidth
            if enabled
            else QPlainTextEdit.LineWrapMode.NoWrap
        )
        option = self.document().defaultTextOption()
        option.setWrapMode(
            QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere
            if enabled
            else QTextOption.WrapMode.NoWrap
        )
        self.document().setDefaultTextOption(option)

    def set_show_line_numbers(self, enabled: bool) -> None:
        self._show_line_numbers = enabled
        self._gutter.setVisible(enabled)
        self._update_gutter_width()

    # ---------------------------------------------------------------- gutter
    def line_number_area_width(self) -> int:
        if not self._show_line_numbers:
            return 0
        digits = max(2, len(str(max(1, self.blockCount()))))
        return 18 + self.fontMetrics().horizontalAdvance("9") * digits

    def _update_gutter_width(self) -> None:
        self.setViewportMargins(self.line_number_area_width(), 0, 0, 0)

    def _update_gutter(self, rect: QRect, dy: int) -> None:
        if dy:
            self._gutter.scroll(0, dy)
        else:
            self._gutter.update(0, rect.y(), self._gutter.width(), rect.height())
        if rect.contains(self.viewport().rect()):  # type: ignore[union-attr]
            self._update_gutter_width()

    def resizeEvent(self, event: QResizeEvent | None) -> None:  # noqa: N802 - Qt API
        super().resizeEvent(event)
        rect = self.contentsRect()
        self._gutter.setGeometry(
            QRect(rect.left(), rect.top(), self.line_number_area_width(), rect.height())
        )

    def paint_line_numbers(self, event: QPaintEvent) -> None:
        """Paint the gutter; called back from :class:`LineNumberArea`."""
        painter = QPainter(self._gutter)
        painter.fillRect(event.rect(), self._gutter_bg)

        block = self.firstVisibleBlock()
        number = block.blockNumber()
        top = round(self.blockBoundingGeometry(block).translated(self.contentOffset()).top())
        bottom = top + round(self.blockBoundingRect(block).height())
        current = self.textCursor().blockNumber()
        width = self._gutter.width() - 10
        height = self.fontMetrics().height()

        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                is_current = number == current
                painter.setPen(self._gutter_fg_active if is_current else self._gutter_fg)
                font = painter.font()
                font.setBold(is_current)
                painter.setFont(font)
                painter.drawText(
                    0,
                    top,
                    width,
                    height,
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                    str(number + 1),
                )
            block = block.next()
            top = bottom
            bottom = top + round(self.blockBoundingRect(block).height())
            number += 1
        painter.end()

    # ------------------------------------------------------- extra selections
    def set_search_highlights(
        self, ranges: list[tuple[int, int]], current: tuple[int, int] | None
    ) -> None:
        """Highlight every search match, emphasising the current one."""
        self._search_ranges = ranges
        self._current_match = current
        self._refresh_extra_selections()

    def clear_search_highlights(self) -> None:
        self.set_search_highlights([], None)

    def _refresh_extra_selections(self) -> None:
        selections: list[QTextEdit.ExtraSelection] = []

        if not self.isReadOnly():
            line = QTextEdit.ExtraSelection()
            line.format.setBackground(self._current_line_bg)
            line.format.setProperty(QTextFormat.Property.FullWidthSelection, True)
            cursor = self.textCursor()
            cursor.clearSelection()
            line.cursor = cursor
            selections.append(line)

        for start, end in self._search_ranges:
            selection = QTextEdit.ExtraSelection()
            is_current = self._current_match == (start, end)
            selection.format.setBackground(
                self._current_match_bg if is_current else self._search_bg
            )
            selection.format.setForeground(
                self._current_match_fg if is_current else self._search_fg
            )
            cursor = self.textCursor()
            cursor.setPosition(start)
            cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
            selection.cursor = cursor
            selections.append(selection)

        self.setExtraSelections(selections)

    # ------------------------------------------------------------ scrolling
    def first_visible_line(self) -> int:
        """1-based source line currently at the top of the viewport."""
        return self.firstVisibleBlock().blockNumber() + 1

    def scroll_to_line(self, line: int) -> None:
        """Put the given 1-based source line at the top of the viewport.

        The scrollbar is moved directly rather than by parking the text cursor
        on the target block: ``setTextCursor`` keeps the caret visible, so
        putting the caret back afterwards scrolls the viewport straight back to
        wherever the user left it and the scroll never happens.  Nothing here
        touches the cursor, which also means the selection survives a sync.
        """
        block = self.document().findBlockByNumber(max(0, line - 1))
        if not block.isValid():
            return
        bar = self.verticalScrollBar()
        bar.setValue(min(self._scroll_value_for(block), bar.maximum()))

    @staticmethod
    def _scroll_value_for(block: QTextBlock) -> int:
        """Scrollbar value that puts ``block`` at the top of the viewport.

        ``QPlainTextEdit`` counts *visual* lines, not blocks, so with word wrap
        on a paragraph that occupies three rows advances the bar by three.
        Blocks the lazy layout has not reached yet report no lines and are
        counted as one, which is what they will be once laid out unless they
        wrap.
        """
        total = 0
        current = block.document().begin()
        while current.isValid() and current.blockNumber() < block.blockNumber():
            if current.isVisible():
                total += max(1, current.layout().lineCount())
            current = current.next()
        return total

    # ------------------------------------------------------- text operations
    def _replace_document(self, result: EditResult) -> None:
        """Apply an :class:`EditResult` using the smallest possible edit."""
        old = self.toPlainText()
        new = result.text
        if old == new:
            cursor = self.textCursor()
            cursor.setPosition(result.start)
            cursor.setPosition(result.end, QTextCursor.MoveMode.KeepAnchor)
            self.setTextCursor(cursor)
            return

        prefix = 0
        limit = min(len(old), len(new))
        while prefix < limit and old[prefix] == new[prefix]:
            prefix += 1
        suffix = 0
        while (
            suffix < limit - prefix
            and old[len(old) - 1 - suffix] == new[len(new) - 1 - suffix]
        ):
            suffix += 1

        cursor = self.textCursor()
        cursor.beginEditBlock()
        cursor.setPosition(prefix)
        cursor.setPosition(len(old) - suffix, QTextCursor.MoveMode.KeepAnchor)
        cursor.insertText(new[prefix : len(new) - suffix])
        cursor.endEditBlock()

        cursor = self.textCursor()
        cursor.setPosition(min(result.start, len(new)))
        cursor.setPosition(min(result.end, len(new)), QTextCursor.MoveMode.KeepAnchor)
        self.setTextCursor(cursor)

    def _selection(self) -> tuple[str, int, int]:
        cursor = self.textCursor()
        return self.toPlainText(), cursor.selectionStart(), cursor.selectionEnd()

    def wrap_selection(self, marker: str, closing: str | None = None, placeholder: str = "") -> None:
        """Toggle an inline marker such as ``**`` around the selection."""
        text, start, end = self._selection()
        self._replace_document(
            textops.toggle_wrap(text, start, end, marker, closing, placeholder)
        )

    def toggle_heading(self, level: int) -> None:
        text, start, end = self._selection()
        self._replace_document(textops.apply_heading(text, start, end, level))

    def insert_link(self, url: str = "") -> None:
        text, start, end = self._selection()
        self._replace_document(textops.make_link(text, start, end, url))

    def toggle_code_block(self, language: str = "") -> None:
        text, start, end = self._selection()
        self._replace_document(textops.toggle_code_block(text, start, end, language))

    def toggle_line_prefix(self, prefix: str) -> None:
        text, start, end = self._selection()
        self._replace_document(textops.toggle_block_prefix(text, start, end, prefix))

    def insert_snippet(self, snippet: str, cursor_offset: int | None = None) -> None:
        """Insert text at the cursor, optionally repositioning inside it."""
        cursor = self.textCursor()
        position = cursor.selectionStart()
        cursor.insertText(snippet)
        if cursor_offset is not None:
            cursor.setPosition(position + cursor_offset)
            self.setTextCursor(cursor)

    # ------------------------------------------------------------- keyboard
    def keyPressEvent(self, event: QKeyEvent | None) -> None:  # noqa: N802 - Qt API
        if event is None:
            return
        key = event.key()
        modifiers = event.modifiers()
        plain = modifiers == Qt.KeyboardModifier.NoModifier

        if key == Qt.Key.Key_Tab and modifiers == Qt.KeyboardModifier.NoModifier:
            if self.textCursor().hasSelection():
                self._indent_selection(1)
            else:
                self.insertPlainText(" " * self._tab_width)
            return
        if key == Qt.Key.Key_Backtab or (
            key == Qt.Key.Key_Tab and modifiers == Qt.KeyboardModifier.ShiftModifier
        ):
            self._indent_selection(-1)
            return
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and plain and self._continue_list():
            return
        if key == Qt.Key.Key_Backspace and plain and self._unindent_at_cursor():
            return

        super().keyPressEvent(event)

    def _indent_selection(self, direction: int) -> None:
        cursor = self.textCursor()
        text = self.toPlainText()
        start, end = cursor.selectionStart(), cursor.selectionEnd()
        line_start, line_end = textops.expand_selection_to_lines(text, start, end)
        pad = " " * self._tab_width
        lines = text[line_start:line_end].split("\n")

        out: list[str] = []
        for line in lines:
            if direction > 0:
                out.append(pad + line)
            else:
                stripped = line[: self._tab_width]
                removed = len(stripped) - len(stripped.lstrip(" "))
                out.append(line[removed:])
        block = "\n".join(out)
        self._replace_document(EditResult(
            text[:line_start] + block + text[line_end:],
            line_start,
            line_start + len(block),
        ))

    def _continue_list(self) -> bool:
        """Handle Enter inside a list item or a blockquote."""
        cursor = self.textCursor()
        if cursor.hasSelection():
            return False
        line = cursor.block().text()
        column = cursor.positionInBlock()
        # Splitting a line in the middle: let Qt do its default thing.
        if column < len(line.rstrip()) and column != len(line) and line[column:].strip():
            return False

        prefix = textops.continuation_for(line)
        if prefix is None:
            return False

        cursor.beginEditBlock()
        if prefix == "":
            # Empty item: clear the marker instead of adding another one.
            cursor.movePosition(QTextCursor.MoveOperation.StartOfBlock)
            cursor.movePosition(
                QTextCursor.MoveOperation.EndOfBlock, QTextCursor.MoveMode.KeepAnchor
            )
            cursor.insertText("")
            cursor.insertText("\n")
        else:
            cursor.insertText("\n" + prefix)
        cursor.endEditBlock()
        self.setTextCursor(cursor)
        return True

    def _unindent_at_cursor(self) -> bool:
        """Backspace over a full indent step when standing in leading spaces."""
        cursor = self.textCursor()
        if cursor.hasSelection():
            return False
        column = cursor.positionInBlock()
        if column == 0 or column % self._tab_width != 0:
            return False
        line = cursor.block().text()
        if line[:column].strip():
            return False
        for _ in range(self._tab_width):
            cursor.deletePreviousChar()
        return True

    # ------------------------------------------------------------ clipboard
    def insertFromMimeData(self, source: Any) -> None:  # noqa: N802 - Qt API
        """Let the main window intercept image pastes before falling back."""
        if self.mime_handler is not None and source is not None and self.mime_handler(source):
            return
        super().insertFromMimeData(source)

    def canInsertFromMimeData(self, source: Any) -> bool:  # noqa: N802 - Qt API
        if source is not None and source.hasImage():
            return True
        return super().canInsertFromMimeData(source)
