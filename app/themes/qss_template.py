# SPDX-License-Identifier: GPL-3.0-or-later
"""The application stylesheet, generated from palette tokens.

The template uses ``{token}`` placeholders which are substituted by
:func:`build_qss`.  A plain ``str.format`` cannot be used because QSS itself is
full of curly braces, so substitution is done with a regular expression that
only matches ``{identifier}``.
"""

from __future__ import annotations

import re

from .palettes import Palette

__all__ = ["build_qss", "QSS_TEMPLATE"]

_PLACEHOLDER = re.compile(r"\{([a-z_][a-z0-9_]*)\}")

QSS_TEMPLATE = """
/* ------------------------------------------------------------------ base */
* {
    outline: none;
}

/* Only the colour is set globally.  Putting `font-size` on a universal
   selector would beat any font set with `QWidget::setFont`, which would in
   turn silently break the editor's own configurable font size. */
QWidget {
    color: {text};
}

QWidget#rootFrame {
    background-color: {bg};
    border: 1px solid {border};
}

QWidget#contentArea,
QWidget#centerStack {
    background-color: {bg};
}

QToolTip {
    background-color: {bg_alt};
    color: {text};
    border: 1px solid {accent};
    border-radius: 4px;
    padding: 4px 8px;
}

/* ------------------------------------------------------------- title bar */
QWidget#titleBar {
    background-color: {bg_alt};
    border-bottom: 1px solid {border};
}

QLabel#titleLabel {
    color: {text};
    font-size: {title_bar_font_size}px;
    font-weight: 600;
}

QLabel#subtitleLabel {
    color: {muted_on_bg_alt};
    font-size: {small_font_size}px;
}

QPushButton#windowButton {
    background-color: transparent;
    border: none;
    border-radius: 4px;
    padding: 0px;
}

QPushButton#windowButton:hover {
    background-color: {hover};
}

QPushButton#windowButton:pressed {
    background-color: {pressed};
}

QPushButton#windowButtonClose:hover {
    background-color: {red};
}

/* --------------------------------------------------------------- sidebar */
QWidget#sideMenu {
    background-color: {bg_alt};
    border-right: 1px solid {border};
}

QWidget#sideMenuHeader {
    background-color: {bg_alt};
}

QWidget#filePanel {
    background-color: {bg_alt};
    border-right: 1px solid {border};
}

QPushButton#menuButton {
    background-color: transparent;
    border: none;
    border-left: 3px solid transparent;
    text-align: left;
    padding-left: 15px;
    color: {muted_on_bg_alt};
    font-size: {ui_font_size}px;
}

QPushButton#menuButton::menu-indicator {
    image: none;
    width: 0px;
}

QPushButton#menuButton:hover {
    background-color: {hover};
    color: {text_on_hover};
}

QPushButton#menuButton:checked {
    background-color: {bg};
    border-left: 3px solid {accent};
    color: {text};
    font-weight: 600;
}

/* --------------------------------------------------------------- toolbar */
QWidget#toolBar,
QWidget#formatBar {
    background-color: {bg_alt};
    border-bottom: 1px solid {border};
}

QToolButton {
    background-color: transparent;
    border: 1px solid transparent;
    border-radius: 5px;
    padding: 4px;
}

QToolButton:hover {
    background-color: {hover};
    border-color: {border};
}

QToolButton:pressed {
    background-color: {pressed};
}

QToolButton:checked {
    background-color: {accent_soft};
    border-color: {accent};
}

QToolButton:disabled {
    background-color: transparent;
}

QFrame#toolSeparator {
    background-color: {border};
    max-width: 1px;
    border: none;
}

/* --------------------------------------------------------------- buttons */
QPushButton {
    background-color: {surface};
    color: {text_on_surface};
    border: 1px solid {border};
    border-radius: 6px;
    padding: 6px 14px;
}

QPushButton:hover {
    background-color: {hover};
    border-color: {border_strong};
}

QPushButton:pressed {
    background-color: {pressed};
}

QPushButton:disabled {
    color: {muted_on_surface};
    background-color: {bg_alt};
}

QPushButton#primaryButton {
    background-color: {accent};
    color: {accent_contrast};
    border: 1px solid {accent};
    font-weight: 600;
}

QPushButton#primaryButton:hover {
    background-color: {accent2};
    border-color: {accent2};
}

QPushButton#dangerButton {
    background-color: {danger_soft};
    border-color: {red};
    color: {red_on_bg};
}

/* ------------------------------------------------------------ text entry */
QLineEdit,
QSpinBox,
QComboBox {
    background-color: {bg};
    color: {text};
    border: 1px solid {border};
    border-radius: 6px;
    padding: 5px 8px;
    selection-background-color: {selection_bg};
    selection-color: {selection_fg};
}

QLineEdit:focus,
QSpinBox:focus,
QComboBox:focus,
QComboBox:on {
    border-color: {accent};
}

QLineEdit[state="nomatch"] {
    border-color: {red};
    background-color: {danger_soft};
}

QSpinBox::up-button,
QSpinBox::down-button {
    width: 0px;
    border: none;
}

QComboBox::drop-down {
    border: none;
    width: 22px;
}

QComboBox QAbstractItemView {
    background-color: {bg_alt};
    color: {text};
    border: 1px solid {border};
    border-radius: 6px;
    padding: 4px;
    selection-background-color: {accent_soft};
    selection-color: {text_on_accent_soft};
    outline: none;
}

/* -------------------------------------------------------------- checkbox */
QCheckBox {
    spacing: 8px;
    color: {text};
}

QCheckBox::indicator {
    width: 16px;
    height: 16px;
    border: 1px solid {border_strong};
    border-radius: 4px;
    background-color: {bg};
}

QCheckBox::indicator:hover {
    border-color: {accent};
}

QCheckBox::indicator:checked {
    background-color: {accent};
    border-color: {accent};
    image: url({check_icon});
}

/* ---------------------------------------------------------------- slider */
QSlider::groove:horizontal {
    height: 4px;
    background: {border};
    border-radius: 2px;
}

QSlider::handle:horizontal {
    background: {accent};
    width: 14px;
    height: 14px;
    margin: -6px 0;
    border-radius: 7px;
}

QSlider::sub-page:horizontal {
    background: {accent};
    border-radius: 2px;
}

/* ---------------------------------------------------------------- editor */
QPlainTextEdit#editor {
    background-color: {bg};
    color: {text};
    border: none;
    selection-background-color: {selection_bg};
    selection-color: {selection_fg};
}

QWidget#lineNumberArea {
    background-color: {bg_alt};
}

QWidget#editorContainer {
    background-color: {bg};
    border: none;
}

/* ------------------------------------------------------------- file tree */
QTreeView {
    background-color: {bg_alt};
    border: none;
    color: {text};
    show-decoration-selected: 1;
}

QTreeView::item {
    padding: 4px 2px;
    border-radius: 4px;
}

QTreeView::item:hover {
    background-color: {hover};
    color: {text_on_hover};
}

QTreeView::item:selected {
    background-color: {accent_soft};
    color: {text_on_accent_soft};
}

QTreeView::branch {
    background: transparent;
}

QTreeView::branch:has-children:!has-siblings:closed,
QTreeView::branch:closed:has-children:has-siblings {
    image: url({branch_closed_icon});
}

QTreeView::branch:open:has-children:!has-siblings,
QTreeView::branch:open:has-children:has-siblings {
    image: url({branch_open_icon});
}

QHeaderView::section {
    background-color: {bg_alt};
    color: {muted_on_bg_alt};
    border: none;
    padding: 4px;
}

/* -------------------------------------------------------------- splitter */
QSplitter::handle {
    background-color: {border};
}

QSplitter::handle:horizontal {
    width: 1px;
}

QSplitter::handle:vertical {
    height: 1px;
}

QSplitter::handle:hover {
    background-color: {accent};
}

/* ------------------------------------------------------------ scrollbars */
QScrollBar:vertical {
    background: transparent;
    width: 10px;
    margin: 0px;
}

QScrollBar::handle:vertical {
    background: {scrollbar};
    min-height: 28px;
    border-radius: 5px;
}

QScrollBar::handle:vertical:hover {
    background: {scrollbar_hover};
}

QScrollBar:horizontal {
    background: transparent;
    height: 10px;
    margin: 0px;
}

QScrollBar::handle:horizontal {
    background: {scrollbar};
    min-width: 28px;
    border-radius: 5px;
}

QScrollBar::handle:horizontal:hover {
    background: {scrollbar_hover};
}

QScrollBar::add-line,
QScrollBar::sub-line,
QScrollBar::add-page,
QScrollBar::sub-page {
    background: none;
    border: none;
    height: 0px;
    width: 0px;
}

/* ----------------------------------------------------------- status area */
QWidget#statusBar {
    background-color: {bg_alt};
    border-top: 1px solid {border};
}

QLabel#statusLabel {
    color: {muted_on_bg_alt};
    font-size: {small_font_size}px;
}

QLabel#statusAccent {
    color: {accent_on_bg_alt};
    font-size: {small_font_size}px;
    font-weight: 600;
}

/* -------------------------------------------------------- settings panel */
QWidget#settingsPanel {
    background-color: {bg_alt};
    border-left: 1px solid {border};
}

QLabel#panelTitle {
    color: {text};
    font-size: {title_font_size}px;
    font-weight: 700;
}

QLabel#sectionLabel {
    color: {accent_on_bg_alt};
    font-size: {small_font_size}px;
    font-weight: 700;
    letter-spacing: 1px;
}

QLabel#hintLabel {
    color: {muted_on_bg_alt};
    font-size: {small_font_size}px;
}

QFrame#panelSeparator {
    background-color: {border};
    max-height: 1px;
    border: none;
}

QWidget#themeSwatch {
    border: 2px solid {border};
    border-radius: 8px;
}

QWidget#themeSwatch[selected="true"] {
    border: 2px solid {accent};
}

/* ----------------------------------------------------------------- menus */
QMenu {
    background-color: {bg_alt};
    color: {text};
    border: 1px solid {border};
    border-radius: 8px;
    padding: 6px;
}

QMenu::item {
    padding: 6px 24px 6px 12px;
    border-radius: 5px;
}

QMenu::item:selected {
    background-color: {accent_soft};
    color: {text_on_accent_soft};
}

QMenu::item:disabled {
    color: {muted_on_bg_alt};
}

QMenu::separator {
    height: 1px;
    background: {border};
    margin: 5px 8px;
}

/* --------------------------------------------------------- find/replace */
QWidget#findPanel {
    background-color: {bg_alt};
    border-top: 1px solid {border};
}

QLabel#findCounter {
    color: {muted_on_bg_alt};
    font-size: {small_font_size}px;
    min-width: 76px;
}

/* --------------------------------------------------------------- dialogs */
QDialog,
QMessageBox {
    background-color: {bg};
    color: {text};
}

QMessageBox QLabel {
    color: {text};
}

/* ------------------------------------------------------------ empty state */
QLabel#emptyTitle {
    color: {text};
    font-size: {title_font_size}px;
    font-weight: 700;
}

QLabel#emptyBody {
    color: {muted_on_bg};
    font-size: {ui_font_size}px;
}
"""


def build_qss(
    palette: Palette,
    *,
    ui_font_size: int = 13,
    check_icon: str = "",
    branch_closed_icon: str = "",
    branch_open_icon: str = "",
) -> str:
    """Render the QSS template for ``palette``.

    The icon arguments are filesystem paths to recoloured SVGs written by
    :class:`~app.themes.manager.ThemeManager`; QSS has no way to tint an image,
    so sub-controls that are drawn by the stylesheet need a per-theme file.
    """
    tokens = palette.tokens()
    tokens["ui_font_size"] = str(ui_font_size)
    tokens["small_font_size"] = str(max(9, ui_font_size - 2))
    tokens["title_font_size"] = str(ui_font_size + 4)
    tokens["title_bar_font_size"] = str(ui_font_size + 1)
    tokens["check_icon"] = check_icon.replace("\\", "/")
    tokens["branch_closed_icon"] = branch_closed_icon.replace("\\", "/")
    tokens["branch_open_icon"] = branch_open_icon.replace("\\", "/")

    def _sub(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in tokens:
            raise KeyError(f"unknown theme token in QSS template: {key!r}")
        return tokens[key]

    return _PLACEHOLDER.sub(_sub, QSS_TEMPLATE)
