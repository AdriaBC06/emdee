# SPDX-License-Identifier: GPL-3.0-or-later
"""Inline find & replace.

Deliberately a docked panel rather than a modal dialog: the user must be able to
keep reading and editing the document while refining a query, and every match
stays highlighted in the editor as they type.
"""

from __future__ import annotations

import re

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..core.textops import SearchOptions, find_matches, replace_all
from .editor import MarkdownEditor
from .icons import themed_icon

__all__ = ["FindReplacePanel"]


def _tool_button(tooltip: str, checkable: bool = False, text: str = "") -> QToolButton:
    button = QToolButton()
    button.setToolTip(tooltip)
    button.setCheckable(checkable)
    button.setAutoRaise(True)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    if text:
        button.setText(text)
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        button.setMinimumWidth(30)
    return button


class FindReplacePanel(QWidget):
    """Search and replace strip attached under the editor."""

    closed = pyqtSignal()

    def __init__(self, editor: MarkdownEditor, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("findPanel")
        self._editor = editor
        self._matches: list[tuple[int, int]] = []
        self._index = -1
        self._icon_color = "#6272a4"

        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 8, 10, 8)
        outer.setSpacing(6)

        # ------------------------------------------------------- find row
        find_row = QHBoxLayout()
        find_row.setSpacing(6)

        self.find_input = QLineEdit()
        self.find_input.setPlaceholderText("Find")
        self.find_input.setClearButtonEnabled(True)
        self.find_input.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.find_input.textChanged.connect(self._refresh)
        self.find_input.returnPressed.connect(self.find_next)

        self.case_button = _tool_button("Match case", checkable=True, text="Aa")
        self.word_button = _tool_button("Whole word", checkable=True, text="ab")
        self.regex_button = _tool_button("Regular expression", checkable=True, text=".*")
        for button in (self.case_button, self.word_button, self.regex_button):
            button.toggled.connect(self._refresh)

        self.counter = QLabel("No results")
        self.counter.setObjectName("findCounter")
        self.counter.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        self.prev_button = _tool_button("Previous match (Shift+Enter)")
        self.next_button = _tool_button("Next match (Enter)")
        self.close_button = _tool_button("Close (Esc)")
        self.prev_button.clicked.connect(self.find_previous)
        self.next_button.clicked.connect(self.find_next)
        self.close_button.clicked.connect(self.close_panel)

        for widget in (
            self.find_input,
            self.case_button,
            self.word_button,
            self.regex_button,
            self.counter,
            self.prev_button,
            self.next_button,
            self.close_button,
        ):
            find_row.addWidget(widget)

        # ---------------------------------------------------- replace row
        replace_row = QHBoxLayout()
        replace_row.setSpacing(6)

        self.replace_input = QLineEdit()
        self.replace_input.setPlaceholderText("Replace with")
        self.replace_input.setClearButtonEnabled(True)
        self.replace_input.returnPressed.connect(self.replace_current)

        self.replace_button = _tool_button("Replace", text="Replace")
        self.replace_all_button = _tool_button("Replace all", text="All")
        self.replace_button.setMinimumWidth(70)
        self.replace_all_button.setMinimumWidth(44)
        self.replace_button.clicked.connect(self.replace_current)
        self.replace_all_button.clicked.connect(self.replace_every)

        replace_row.addWidget(self.replace_input)
        replace_row.addWidget(self.replace_button)
        replace_row.addWidget(self.replace_all_button)

        self._replace_widget = QWidget()
        self._replace_widget.setLayout(replace_row)

        outer.addLayout(find_row)
        outer.addWidget(self._replace_widget)

        self.hide()

    # ------------------------------------------------------------ appearance
    def apply_icons(self, color: str) -> None:
        """Retint the panel icons after a theme change."""
        self._icon_color = color
        self.prev_button.setIcon(themed_icon("arrow-up", color, 18))
        self.next_button.setIcon(themed_icon("arrow-down", color, 18))
        self.close_button.setIcon(themed_icon("x", color, 18))

    # --------------------------------------------------------------- opening
    def open_panel(self, *, with_replace: bool = False) -> None:
        """Reveal the panel, seeding the query from the current selection."""
        self._replace_widget.setVisible(with_replace)
        selection = self._editor.textCursor().selectedText()
        if selection and " " not in selection:
            self.find_input.setText(selection)
        self.show()
        self.find_input.setFocus()
        self.find_input.selectAll()
        self._refresh()

    def close_panel(self) -> None:
        """Hide the panel and drop every highlight."""
        self.hide()
        self._matches = []
        self._index = -1
        self._editor.clear_search_highlights()
        self._editor.setFocus()
        self.closed.emit()

    # ---------------------------------------------------------------- search
    def _options(self) -> SearchOptions:
        return SearchOptions(
            case_sensitive=self.case_button.isChecked(),
            whole_word=self.word_button.isChecked(),
            regex=self.regex_button.isChecked(),
        )

    def _set_invalid(self, invalid: bool) -> None:
        self.find_input.setProperty("state", "nomatch" if invalid else "")
        style = self.find_input.style()
        if style is not None:
            style.unpolish(self.find_input)
            style.polish(self.find_input)

    def _refresh(self) -> None:
        query = self.find_input.text()
        if not query:
            self._matches = []
            self._index = -1
            self._set_invalid(False)
            self.counter.setText("No results")
            self._editor.clear_search_highlights()
            return

        try:
            self._matches = find_matches(self._editor.toPlainText(), query, self._options())
        except re.error as exc:
            self._matches = []
            self._index = -1
            self._set_invalid(True)
            self.counter.setText("Bad pattern")
            self.counter.setToolTip(str(exc))
            self._editor.clear_search_highlights()
            return

        self.counter.setToolTip("")
        self._set_invalid(bool(query) and not self._matches)
        if not self._matches:
            self._index = -1
            self.counter.setText("No results")
            self._editor.clear_search_highlights()
            return

        cursor_pos = self._editor.textCursor().selectionStart()
        self._index = next(
            (i for i, (start, _) in enumerate(self._matches) if start >= cursor_pos), 0
        )
        self._apply_highlights(select=False)

    def _apply_highlights(self, *, select: bool) -> None:
        current = self._matches[self._index] if 0 <= self._index < len(self._matches) else None
        self._editor.set_search_highlights(self._matches, current)
        self.counter.setText(
            f"{self._index + 1} of {len(self._matches)}" if current else "No results"
        )
        if current and select:
            cursor = self._editor.textCursor()
            cursor.setPosition(current[0])
            cursor.setPosition(current[1], cursor.MoveMode.KeepAnchor)
            self._editor.setTextCursor(cursor)
            self._editor.ensureCursorVisible()

    def _step(self, delta: int) -> None:
        if not self._matches:
            self._refresh()
            if not self._matches:
                return
        self._index = (self._index + delta) % len(self._matches)
        self._apply_highlights(select=True)

    def find_next(self) -> None:
        self._step(1)

    def find_previous(self) -> None:
        self._step(-1)

    # --------------------------------------------------------------- replace
    def replace_current(self) -> None:
        """Replace the highlighted match and move to the next one."""
        if not self._matches or not 0 <= self._index < len(self._matches):
            self.find_next()
            return
        start, end = self._matches[self._index]
        replacement = self.replace_input.text()

        cursor = self._editor.textCursor()
        cursor.setPosition(start)
        cursor.setPosition(end, cursor.MoveMode.KeepAnchor)
        cursor.insertText(replacement)

        self._refresh()
        if self._matches:
            self._apply_highlights(select=True)

    def replace_every(self) -> None:
        """Replace every match in one undoable step."""
        query = self.find_input.text()
        if not query:
            return
        try:
            new_text, count = replace_all(
                self._editor.toPlainText(),
                query,
                self.replace_input.text(),
                self._options(),
            )
        except re.error as exc:
            self._set_invalid(True)
            self.counter.setText("Bad pattern")
            self.counter.setToolTip(str(exc))
            return

        if count:
            cursor = self._editor.textCursor()
            cursor.beginEditBlock()
            cursor.select(cursor.SelectionType.Document)
            cursor.insertText(new_text)
            cursor.endEditBlock()
        self._refresh()
        self.counter.setText(f"{count} replaced")

    # --------------------------------------------------------------- events
    def keyPressEvent(self, event: QKeyEvent | None) -> None:  # noqa: N802 - Qt API
        if event is None:
            return
        if event.key() == Qt.Key.Key_Escape:
            self.close_panel()
            return
        if (
            event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
            and event.modifiers() == Qt.KeyboardModifier.ShiftModifier
        ):
            self.find_previous()
            return
        super().keyPressEvent(event)

    def refresh_after_edit(self) -> None:
        """Recompute matches after the document changed elsewhere."""
        if self.isVisible():
            self._refresh()
