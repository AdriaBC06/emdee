# SPDX-License-Identifier: GPL-3.0-or-later
"""The application shell: PyDracula layout wired to the Markdown workflow.

The original template kept every behaviour in a single ``app_functions.py``;
here the window only *coordinates*.  Text transformations live in
:mod:`app.core.textops`, disk access in :mod:`app.core.file_service`, rendering
in :mod:`app.core.renderer` and colour in :mod:`app.themes` — this file wires
them to widgets and to the user's intent.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from PyQt6.QtCore import (
    QBuffer,
    QByteArray,
    QEasingCurve,
    QEvent,
    QFileSystemWatcher,
    QMarginsF,
    QPoint,
    QPropertyAnimation,
    QSize,
    Qt,
    QTimer,
    QUrl,
    pyqtSlot,
)
from PyQt6.QtGui import (
    QAction,
    QCloseEvent,
    QDragEnterEvent,
    QDropEvent,
    QGuiApplication,
    QImage,
    QKeySequence,
    QMouseEvent,
    QPageLayout,
    QPageSize,
    QResizeEvent,
)
from PyQt6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .. import APP_NAME, APP_VERSION
from ..core import file_service
from ..core import page as page_builder
from ..core.document import Document
from ..core.file_service import MARKDOWN_SUFFIXES, FileError
from ..core.renderer import MarkdownRenderer, inline_local_images
from ..core.settings import Settings, ViewMode
from ..paths import app_logo
from ..platform_support import uses_native_frame_hit_testing
from ..themes.manager import ThemeManager
from .about import AboutDialog
from .editor import MarkdownEditor
from .file_tree import FileTree
from .find_replace import FindReplacePanel
from .highlighter import MarkdownHighlighter
from .icons import app_icon, themed_icon
from .preview import MarkdownPreview
from .settings_panel import SettingsPanel
from .title_bar import TitleBar
from .toolbar import ToolStrip

if TYPE_CHECKING:
    from .win_chrome import WindowsChrome

log = logging.getLogger(__name__)

__all__ = ["MainWindow"]

RAIL_COLLAPSED = 58
RAIL_EXPANDED = 196
FILE_PANEL_WIDTH = 258
#: Floor for the preferences drawer when the window is too small to spare a
#: third of its width. Below this the drawer stops being usable at all, so it
#: is allowed to encroach on the editor rather than shrink further.
MIN_PANEL_WIDTH = 200
ANIMATION_MS = 180
SYNC_GUARD_MS = 140

_MARKDOWN_FILTER = (
    "Markdown documents (*.md *.markdown *.mdown *.mkd *.mdx);;"
    "Text files (*.txt);;All files (*)"
)


class _Grip(QWidget):
    """Invisible resize handle along one window edge.

    ``startSystemResize`` hands the interaction to the compositor, which is the
    only thing that works under Wayland and gives proper snapping on X11.

    Not used on Windows, where the window keeps its real frame and the system
    provides resize borders of its own — see :mod:`app.ui.win_chrome`.
    """

    def __init__(self, window: QWidget, edge: Qt.Edge, cursor: Qt.CursorShape) -> None:
        super().__init__(window)
        self._edge = edge
        self.setCursor(cursor)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

    def mousePressEvent(self, event: QMouseEvent | None) -> None:  # noqa: N802 - Qt API
        if event is not None and event.button() == Qt.MouseButton.LeftButton:
            handle = self.window().windowHandle()
            if handle is not None:
                handle.startSystemResize(self._edge)
                event.accept()
                return
        super().mousePressEvent(event)


class MainWindow(QMainWindow):
    """Everything the user sees."""

    def __init__(self, theme_manager: ThemeManager, settings: Settings) -> None:
        super().__init__()
        self._themes = theme_manager
        self._settings = settings
        self._document = Document()
        self._renderer = MarkdownRenderer(theme_manager.key)
        self._watcher = QFileSystemWatcher(self)
        self._sync_guard = False
        self._reload_pending = False
        self._view_mode = settings.view_mode

        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(app_icon())
        self.setMinimumSize(QSize(760, 520))
        self.setAcceptDrops(True)

        self._use_native_chrome = settings.native_decorations
        self._win_chrome: WindowsChrome | None = None
        if not self._use_native_chrome:
            if uses_native_frame_hit_testing():
                # Keep the real window frame and suppress only its painting; the
                # flag below would replace it with a bare popup and take Aero
                # Snap, the shadow and the resize borders with it.
                from .win_chrome import WindowsChrome as _WindowsChrome

                self._win_chrome = _WindowsChrome(self)
            else:
                self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)

        self._build_actions()
        self._build_ui()
        self._connect_signals()
        self._restore_state()

        self._themes.theme_changed.connect(self._on_theme_changed)
        self._on_theme_changed(self._themes.key)

    # ================================================================ actions
    def _action(
        self,
        text: str,
        slot: Callable[[], None],
        shortcut: str | None = None,
        *,
        tip: str = "",
        checkable: bool = False,
    ) -> QAction:
        action = QAction(text, self)
        action.triggered.connect(lambda _checked=False: slot())
        if shortcut:
            action.setShortcut(QKeySequence(shortcut))
            action.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
        action.setToolTip(f"{text}  ({shortcut})" if shortcut else (tip or text))
        action.setStatusTip(tip or text)
        action.setCheckable(checkable)
        return action

    def _toggle_action(
        self, text: str, slot: Callable[[bool], None], shortcut: str
    ) -> QAction:
        """A checkable action whose slot receives the new checked state."""
        action = QAction(text, self)
        action.setCheckable(True)
        action.setShortcut(QKeySequence(shortcut))
        action.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
        action.setToolTip(f"{text}  ({shortcut})")
        action.toggled.connect(slot)
        return action

    def _build_actions(self) -> None:
        self.act_new = self._action("New", self.new_document, "Ctrl+N")
        self.act_open = self._action("Open File", self.open_file_dialog, "Ctrl+O")
        self.act_open_folder = self._action(
            "Open Folder", self.open_folder_dialog, "Ctrl+Shift+O"
        )
        self.act_save = self._action("Save", self.save, "Ctrl+S")
        self.act_save_as = self._action("Save As", self.save_as, "Ctrl+Shift+S")
        self.act_reload = self._action("Reload from disk", self.reload_from_disk, "F5")
        self.act_export_html = self._action(
            "Export HTML", self.export_html, "Ctrl+Shift+E"
        )
        self.act_export_pdf = self._action("Export PDF", self.export_pdf, "Ctrl+P")
        self.act_quit = self._action("Quit", self.close, "Ctrl+Q")

        self.act_find = self._action("Find", self.show_find, "Ctrl+F")
        self.act_replace = self._action("Replace", self.show_replace, "Ctrl+H")
        self.act_paste = self._action(
            "Paste from clipboard", self.paste_clipboard, "Ctrl+Shift+V"
        )

        self.act_bold = self._action(
            "Bold", lambda: self._editor.wrap_selection("**", placeholder="bold"), "Ctrl+B"
        )
        self.act_italic = self._action(
            "Italic", lambda: self._editor.wrap_selection("*", placeholder="italic"), "Ctrl+I"
        )
        self.act_strike = self._action(
            "Strikethrough",
            lambda: self._editor.wrap_selection("~~", placeholder="struck"),
            "Ctrl+Shift+X",
        )
        self.act_code = self._action(
            "Inline code", lambda: self._editor.wrap_selection("`", placeholder="code"), "Ctrl+`"
        )
        self.act_code_block = self._action(
            "Code block", lambda: self._editor.toggle_code_block(), "Ctrl+Shift+K"
        )
        self.act_link = self._action("Link", self.insert_link, "Ctrl+K")
        self.act_image = self._action("Image", self.insert_image, "Ctrl+Shift+I")
        self.act_bullets = self._action(
            "Bullet list", lambda: self._editor.toggle_line_prefix("- "), "Ctrl+Shift+L"
        )
        self.act_numbers = self._action(
            "Numbered list", lambda: self._editor.toggle_line_prefix("1. "), "Ctrl+Shift+N"
        )
        self.act_tasks = self._action(
            "Task list", lambda: self._editor.toggle_line_prefix("- [ ] "), "Ctrl+Shift+T"
        )
        self.act_quote = self._action(
            "Blockquote", lambda: self._editor.toggle_line_prefix("> "), "Ctrl+Shift+Q"
        )
        self.act_table = self._action("Insert table", self.insert_table, "Ctrl+Shift+B")

        self.act_headings: list[QAction] = []
        for level in range(1, 7):
            action = self._action(
                f"Heading {level}",
                lambda lvl=level: self._editor.toggle_heading(lvl),
                f"Ctrl+{level}",
            )
            self.act_headings.append(action)
        self.act_paragraph = self._action(
            "Paragraph", lambda: self._editor.toggle_heading(0), "Ctrl+0"
        )

        self.act_view_editor = self._action(
            "Editor only", lambda: self.set_view_mode(ViewMode.EDITOR),
            "Ctrl+Shift+1", checkable=True,
        )
        self.act_view_split = self._action(
            "Split view", lambda: self.set_view_mode(ViewMode.SPLIT),
            "Ctrl+Shift+2", checkable=True,
        )
        self.act_view_preview = self._action(
            "Preview only", lambda: self.set_view_mode(ViewMode.PREVIEW),
            "Ctrl+Shift+3", checkable=True,
        )

        self.act_toggle_files = self._toggle_action(
            "Explorer", self.toggle_file_panel, "F9"
        )
        self.act_toggle_settings = self._toggle_action(
            "Preferences", self.toggle_settings_panel, "F10"
        )
        self.act_about = self._action("About", self.show_about, "F1")
        self.act_zoom_in = self._action("Increase font size", lambda: self._nudge_font(1), "Ctrl+=")
        self.act_zoom_out = self._action("Decrease font size", lambda: self._nudge_font(-1), "Ctrl+-")

        for action in self.findChildren(QAction):
            self.addAction(action)

    # ===================================================================== ui
    def _build_ui(self) -> None:
        root = QWidget(self)
        root.setObjectName("rootFrame")
        self.setCentralWidget(root)

        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self._title_bar = TitleBar(root)
        if self._use_native_chrome:
            self._title_bar.hide()
        outer.addWidget(self._title_bar)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        outer.addLayout(body, 1)

        body.addWidget(self._build_rail())
        body.addWidget(self._build_file_panel())
        body.addWidget(self._build_center(), 1)

        self._settings_panel = SettingsPanel(self._settings, root)
        body.addWidget(self._settings_panel)

        self._grips: list[_Grip] = []
        if self._win_chrome is not None:
            # Windows resizes from the frame it still owns; drawn-on grips would
            # only shadow it and swallow clicks meant for the edge.
            self._win_chrome.set_caption_widget(self._title_bar)
            self._win_chrome.set_exclusions(self._title_bar.window_buttons)
            self._win_chrome.set_maximize_button(
                self._title_bar.maximize_button, self.toggle_maximized
            )
        elif not self._use_native_chrome:
            self._build_grips(root)

    # ------------------------------------------------------------------ rail
    def _rail_button(self, action: QAction, icon: str) -> QPushButton:
        button = QPushButton(action.text(), self._rail)
        button.setObjectName("menuButton")
        button.setFixedHeight(42)
        button.setIconSize(QSize(20, 20))
        button.setToolTip(action.toolTip())
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        button.setProperty("fullText", action.text())
        button.setCheckable(action.isCheckable())
        if action.isCheckable():
            button.setChecked(action.isChecked())
            action.toggled.connect(button.setChecked)
            button.clicked.connect(action.setChecked)
        else:
            button.clicked.connect(action.trigger)
        self._rail_buttons.append((button, icon))
        return button

    def _build_rail(self) -> QWidget:
        self._rail = QWidget()
        self._rail.setObjectName("sideMenu")
        self._rail.setFixedWidth(RAIL_COLLAPSED)
        self._rail_buttons: list[tuple[QPushButton, str]] = []

        layout = QVBoxLayout(self._rail)
        layout.setContentsMargins(0, 8, 0, 8)
        layout.setSpacing(2)

        self._rail_toggle = QPushButton("Menu", self._rail)
        self._rail_toggle.setObjectName("menuButton")
        self._rail_toggle.setFixedHeight(42)
        self._rail_toggle.setIconSize(QSize(20, 20))
        self._rail_toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self._rail_toggle.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._rail_toggle.setToolTip("Expand or collapse this menu")
        self._rail_toggle.setProperty("fullText", "Menu")
        self._rail_toggle.clicked.connect(self.toggle_rail)
        self._rail_buttons.append((self._rail_toggle, "menu"))
        layout.addWidget(self._rail_toggle)
        layout.addSpacing(6)

        for action, icon in (
            (self.act_toggle_files, "folder"),
            (self.act_new, "file-plus"),
            (self.act_open, "file-text"),
            (self.act_open_folder, "folder-open"),
            (self.act_save, "save"),
        ):
            layout.addWidget(self._rail_button(action, icon))

        self._recent_button = QPushButton("Recent", self._rail)
        self._recent_button.setObjectName("menuButton")
        self._recent_button.setFixedHeight(42)
        self._recent_button.setIconSize(QSize(20, 20))
        self._recent_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._recent_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._recent_button.setToolTip("Recently opened documents")
        self._recent_button.setProperty("fullText", "Recent")
        self._recent_menu = QMenu(self)
        # Deliberately not QPushButton.setMenu(): a button that owns a menu is
        # laid out by the style with room reserved for a drop-down indicator,
        # which shifts its icon and label out of line with the rail's other
        # entries. Popping the menu up by hand keeps every row identical.
        self._recent_button.clicked.connect(self._show_recent_menu)
        self._rail_buttons.append((self._recent_button, "refresh"))
        layout.addWidget(self._recent_button)

        layout.addStretch(1)

        for action, icon in (
            (self.act_toggle_settings, "sliders"),
            (self.act_about, "info"),
        ):
            layout.addWidget(self._rail_button(action, icon))

        return self._rail

    # ------------------------------------------------------------ file panel
    def _build_file_panel(self) -> QWidget:
        self._file_panel = QWidget()
        self._file_panel.setObjectName("filePanel")
        self._file_panel.setMinimumWidth(0)
        self._file_panel.setMaximumWidth(0)
        self._file_panel.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)

        layout = QVBoxLayout(self._file_panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = QWidget()
        header.setObjectName("sideMenuHeader")
        header.setFixedHeight(38)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(14, 0, 8, 0)
        self._folder_label = QLabel("Explorer")
        self._folder_label.setObjectName("sectionLabel")
        header_layout.addWidget(self._folder_label)
        header_layout.addStretch(1)

        self._file_tree = FileTree(self._file_panel)
        layout.addWidget(header)
        layout.addWidget(self._file_tree, 1)
        return self._file_panel

    # ---------------------------------------------------------------- center
    def _build_center(self) -> QWidget:
        center = QWidget()
        center.setObjectName("centerStack")
        layout = QVBoxLayout(center)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        layout.addWidget(self._build_main_strip())
        layout.addWidget(self._build_format_strip())

        self._splitter = QSplitter(Qt.Orientation.Horizontal, center)
        self._splitter.setChildrenCollapsible(False)
        self._splitter.setHandleWidth(1)

        editor_container = QWidget()
        editor_container.setObjectName("editorContainer")
        editor_layout = QVBoxLayout(editor_container)
        editor_layout.setContentsMargins(0, 0, 0, 0)
        editor_layout.setSpacing(0)
        self._editor = MarkdownEditor(editor_container)
        self._highlighter = MarkdownHighlighter(self._editor.document(), self._themes.palette)
        editor_layout.addWidget(self._editor)
        self._editor_container = editor_container

        self._preview = MarkdownPreview(self._splitter)
        self._splitter.addWidget(editor_container)
        self._splitter.addWidget(self._preview)
        self._splitter.setStretchFactor(0, 1)
        self._splitter.setStretchFactor(1, 1)
        layout.addWidget(self._splitter, 1)

        self._find_panel = FindReplacePanel(self._editor, center)
        layout.addWidget(self._find_panel)

        layout.addWidget(self._build_status_bar())
        return center

    def _build_main_strip(self) -> QWidget:
        strip = ToolStrip("toolBar", 18, self)
        strip.add_action(self.act_new, "file-plus")
        strip.add_action(self.act_open, "file-text")
        strip.add_action(self.act_save, "save")
        strip.add_separator()
        strip.add_action(self.act_view_editor, "edit")
        strip.add_action(self.act_view_split, "columns")
        strip.add_action(self.act_view_preview, "eye")
        strip.add_separator()
        strip.add_action(self.act_find, "search")
        strip.add_action(self.act_replace, "replace")
        strip.add_action(self.act_paste, "clipboard")
        strip.add_stretch()
        strip.add_action(self.act_reload, "refresh")
        strip.add_separator()
        strip.add_action(self.act_export_html, "download")
        strip.add_action(self.act_export_pdf, "printer")
        self._main_strip = strip
        return strip

    def _build_format_strip(self) -> QWidget:
        strip = ToolStrip("formatBar", 18, self)

        self._heading_button = QToolButton(strip)
        self._heading_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._heading_button.setAutoRaise(True)
        self._heading_button.setIconSize(QSize(18, 18))
        self._heading_button.setToolTip("Heading level (Ctrl+1 … Ctrl+6)")
        self._heading_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._heading_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        heading_menu = QMenu(self._heading_button)
        for action in self.act_headings:
            heading_menu.addAction(action)
        heading_menu.addSeparator()
        heading_menu.addAction(self.act_paragraph)
        self._heading_button.setMenu(heading_menu)
        strip.add_widget(self._heading_button)

        strip.add_action(self.act_bold, "bold")
        strip.add_action(self.act_italic, "italic")
        strip.add_action(self.act_strike, "strikethrough")
        strip.add_separator()
        strip.add_action(self.act_code, "code")
        strip.add_action(self.act_code_block, "code-block")
        strip.add_action(self.act_quote, "quote")
        strip.add_separator()
        strip.add_action(self.act_bullets, "list")
        strip.add_action(self.act_numbers, "list-ordered")
        strip.add_action(self.act_tasks, "check-square")
        strip.add_separator()
        strip.add_action(self.act_link, "link")
        strip.add_action(self.act_image, "image")
        strip.add_action(self.act_table, "table")
        strip.add_stretch()
        self._format_strip = strip
        return strip

    def _build_status_bar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("statusBar")
        bar.setFixedHeight(28)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(14, 0, 14, 0)
        layout.setSpacing(16)

        self._status_message = QLabel("Ready")
        self._status_message.setObjectName("statusLabel")
        self._status_cursor = QLabel("Ln 1, Col 1")
        self._status_cursor.setObjectName("statusLabel")
        self._status_counts = QLabel("0 words")
        self._status_counts.setObjectName("statusLabel")
        self._status_reading = QLabel("0 min read")
        self._status_reading.setObjectName("statusAccent")

        # Fixed minimums so the counters never get elided by a long file path.
        self._status_cursor.setMinimumWidth(110)
        self._status_counts.setMinimumWidth(190)
        self._status_reading.setMinimumWidth(90)
        for label in (self._status_cursor, self._status_counts, self._status_reading):
            label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._status_message.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )

        layout.addWidget(self._status_message, 1)
        layout.addWidget(self._status_cursor)
        layout.addWidget(self._status_counts)
        layout.addWidget(self._status_reading)
        return bar

    def _build_grips(self, root: QWidget) -> None:
        edges = (
            (Qt.Edge.LeftEdge, Qt.CursorShape.SizeHorCursor),
            (Qt.Edge.RightEdge, Qt.CursorShape.SizeHorCursor),
            (Qt.Edge.TopEdge, Qt.CursorShape.SizeVerCursor),
            (Qt.Edge.BottomEdge, Qt.CursorShape.SizeVerCursor),
            (Qt.Edge.LeftEdge | Qt.Edge.TopEdge, Qt.CursorShape.SizeFDiagCursor),
            (Qt.Edge.RightEdge | Qt.Edge.BottomEdge, Qt.CursorShape.SizeFDiagCursor),
            (Qt.Edge.RightEdge | Qt.Edge.TopEdge, Qt.CursorShape.SizeBDiagCursor),
            (Qt.Edge.LeftEdge | Qt.Edge.BottomEdge, Qt.CursorShape.SizeBDiagCursor),
        )
        for edge, cursor in edges:
            grip = _Grip(root, edge, cursor)
            grip.raise_()
            self._grips.append(grip)

    def nativeEvent(  # noqa: N802 - Qt API
        self, event_type: QByteArray, message: object
    ) -> tuple[bool, int]:
        """Route the Windows chrome messages; ignore everything else.

        Deliberately does not chain to ``super()``.  ``QWidget::nativeEvent`` is
        an empty hook that only ever returns false, and invoking it through
        PyQt6 crashes the process — sip mishandles the ``qintptr *result``
        out-parameter on the way back. ``(False, 0)`` is the same answer, safely.
        """
        if self._win_chrome is not None:
            handled = self._win_chrome.native_event(event_type, int(message))  # type: ignore[arg-type]
            if handled is not None:
                return handled
        return False, 0

    def resizeEvent(self, event: QResizeEvent | None) -> None:  # noqa: N802 - Qt API
        super().resizeEvent(event)
        self._reclaim_panel_width()
        if not self._grips:
            return
        thickness = 6
        width, height = self.width(), self.height()
        geometries = [
            (0, thickness, thickness, height - 2 * thickness),
            (width - thickness, thickness, thickness, height - 2 * thickness),
            (thickness, 0, width - 2 * thickness, thickness),
            (thickness, height - thickness, width - 2 * thickness, thickness),
            (0, 0, thickness, thickness),
            (width - thickness, height - thickness, thickness, thickness),
            (width - thickness, 0, thickness, thickness),
            (0, height - thickness, thickness, thickness),
        ]
        for grip, geometry in zip(self._grips, geometries, strict=True):
            grip.setGeometry(*geometry)
            grip.raise_()

    # ================================================================ signals
    def _connect_signals(self) -> None:
        self._title_bar.minimize_requested.connect(self.showMinimized)
        self._title_bar.maximize_requested.connect(self.toggle_maximized)
        self._title_bar.close_requested.connect(self.close)

        self._editor.textChanged.connect(self._on_text_changed)
        self._editor.cursorPositionChanged.connect(self._update_cursor_status)
        self._editor.scrolled.connect(self._on_editor_scrolled)
        self._editor.mime_handler = self._handle_mime

        self._preview.debounce.timeout.connect(self._render_preview)
        self._preview.scrolled_to_line.connect(self._on_preview_scrolled)
        self._preview.document_link_clicked.connect(self.open_path)

        self._file_tree.file_activated.connect(self.open_path)
        self._find_panel.closed.connect(lambda: self._editor.setFocus())

        self._settings_panel.theme_selected.connect(self.set_theme)
        self._settings_panel.editor_font_changed.connect(self._set_editor_font)
        self._settings_panel.preview_font_changed.connect(self._set_preview_font)
        self._settings_panel.tab_width_changed.connect(self._set_tab_width)
        self._settings_panel.word_wrap_changed.connect(self._set_word_wrap)
        self._settings_panel.line_numbers_changed.connect(self._set_line_numbers)
        self._settings_panel.sync_scroll_changed.connect(self._set_sync_scroll)
        self._settings_panel.native_decorations_changed.connect(self._set_native_chrome)

        self._watcher.fileChanged.connect(self._on_file_changed_on_disk)
        self._document.subscribe(lambda _doc: self._update_titles())

    # ================================================================= state
    def _restore_state(self) -> None:
        geometry = self._settings.geometry
        if geometry is not None:
            self.restoreGeometry(geometry)
        else:
            self.resize(1240, 800)

        self._editor.set_font_size(self._settings.editor_font_size)
        self._editor.set_tab_width(self._settings.tab_width)
        self._editor.set_word_wrap(self._settings.word_wrap)
        self._editor.set_show_line_numbers(self._settings.show_line_numbers)

        folder = self._settings.last_folder
        if folder is not None:
            self._file_tree.set_root(folder)
            self._folder_label.setText(folder.name or str(folder))
            # Reopening with a folder but no explorer would look broken.
            self._file_panel.setMaximumWidth(FILE_PANEL_WIDTH)
            self.act_toggle_files.setChecked(True)

        self._rail_expanded = self._settings.sidebar_expanded
        self._rail.setFixedWidth(RAIL_EXPANDED if self._rail_expanded else RAIL_COLLAPSED)
        self._sync_rail_labels()

        self.set_view_mode(self._settings.view_mode, persist=False)
        sizes = self._settings.split_sizes
        if len(sizes) == 2 and all(sizes):
            self._splitter.setSizes(sizes)

        self._preview.show_empty_state()
        self._update_titles()
        self._update_counts()
        self._editor.setFocus()

    # ================================================================= theme
    def set_theme(self, key: str) -> None:
        """Switch theme at runtime and persist the choice."""
        self._settings.theme = key
        self._themes.apply(key)

    @pyqtSlot(str)
    def _on_theme_changed(self, key: str) -> None:
        palette = self._themes.palette
        tokens = palette.tokens()

        self._renderer.set_theme(key)
        self._highlighter.set_palette(palette)
        self._editor.apply_palette(palette)
        self._file_tree.apply_palette(palette)
        self._settings_panel.set_theme(key)
        self._settings_panel.refresh_swatches()
        # A theme carries a UI font size, so the drawer's measurements may have
        # just changed underneath it.
        self._settings_panel.refresh_metrics()
        if self.act_toggle_settings.isChecked():
            self._settings_panel.setFixedWidth(self._settings_panel_width(True))

        icon = self._themes.icon_color
        disabled = tokens["muted_on_bg_alt"]
        self._main_strip.retint(icon, disabled)
        self._format_strip.retint(icon, disabled)
        self._find_panel.apply_icons(icon)
        self._title_bar.retint(icon, tokens["muted_on_bg_alt"])
        self._heading_button.setIcon(themed_icon("heading", icon, 18))
        self._recent_button.setIcon(themed_icon("refresh", icon, 20))
        for button, name in self._rail_buttons:
            button.setIcon(themed_icon(name, icon, 20))

        self._preview.set_css(self._themes.preview_css(self._settings.preview_font_size))
        self._render_preview()

    # ============================================================= documents
    def _confirm_discard(self) -> bool:
        """Ask about unsaved changes.  Returns True when it is safe to go on."""
        if not self._document.is_dirty:
            return True
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Unsaved changes")
        box.setText(f"“{self._document.name}” has unsaved changes.")
        box.setInformativeText("Do you want to save them before continuing?")
        box.setStandardButtons(
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel
        )
        box.setDefaultButton(QMessageBox.StandardButton.Save)
        choice = box.exec()
        if choice == QMessageBox.StandardButton.Save:
            return self.save()
        return choice == QMessageBox.StandardButton.Discard

    def new_document(self) -> None:
        """Start an empty, untitled document."""
        if not self._confirm_discard():
            return
        self._set_watch_path(None)
        self._document.reset()
        self._set_editor_text("")
        self._status("New document")

    def open_file_dialog(self) -> None:
        start = self._settings.last_folder or self._document.directory or Path.home()
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Markdown file", str(start), _MARKDOWN_FILTER
        )
        if path:
            self.open_path(Path(path))

    def open_folder_dialog(self) -> None:
        start = self._settings.last_folder or Path.home()
        directory = QFileDialog.getExistingDirectory(
            self,
            "Open folder",
            str(start),
            QFileDialog.Option.ShowDirsOnly | QFileDialog.Option.DontUseNativeDialog,
        )
        if directory:
            self.open_folder(Path(directory))

    def open_folder(self, directory: Path) -> None:
        """Point the explorer at ``directory`` and remember it."""
        if not directory.is_dir():
            self._error("Folder unavailable", f"“{directory}” is not a folder.")
            return
        self._settings.last_folder = directory
        self._file_tree.set_root(directory)
        self._folder_label.setText(directory.name or str(directory))
        if not self.act_toggle_files.isChecked():
            self.act_toggle_files.setChecked(True)
        self._status(f"Opened folder {directory}")

    def open_path(self, path: Path) -> None:
        """Open a file (or a folder) coming from any source."""
        path = Path(path)
        if path.is_dir():
            self.open_folder(path)
            return
        if path.resolve() == (self._document.path.resolve() if self._document.path else None):
            return
        if not self._confirm_discard():
            return
        try:
            self._document.load(path)
        except FileError as exc:
            self._error("Could not open the file", exc.message, exc.detail)
            self._settings.prune_recent_files()
            return

        self._set_editor_text(self._document.text)
        self._set_watch_path(path)
        self._settings.push_recent_file(path)
        if self._settings.last_folder is None:
            self._settings.last_folder = path.parent
        self._preview.set_base_dir(path.parent)
        self._file_tree.select_path(path)
        self._render_preview()

        if self._document.had_decode_errors:
            self._warn(
                "Encoding problem",
                f"“{path.name}” is not valid UTF-8. Invalid bytes were replaced "
                "with “�”; saving would make that permanent.",
            )
        self._status(f"Opened {path}")

    def save(self) -> bool:
        """Save the current document, prompting for a name when needed."""
        if self._document.is_untitled:
            return self.save_as()
        return self._write(self._document.path)  # type: ignore[arg-type]

    def save_as(self) -> bool:
        start = self._document.path or (
            (self._settings.last_folder or Path.home()) / self._document.name
        )
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Markdown file", str(start), _MARKDOWN_FILTER
        )
        if not path:
            return False
        target = Path(path)
        if not target.suffix:
            target = target.with_suffix(".md")
        return self._write(target)

    def _write(self, path: Path) -> bool:
        self._set_watch_path(None)
        try:
            self._document.save(path)
        except FileError as exc:
            self._error("Could not save the file", exc.message, exc.detail)
            self._set_watch_path(self._document.path)
            return False
        self._set_watch_path(path)
        self._settings.push_recent_file(path)
        self._preview.set_base_dir(path.parent)
        self._file_tree.mark_dirty(path, False)
        self._file_tree.select_path(path)
        self._status(f"Saved {path}")
        return True

    def reload_from_disk(self) -> None:
        """Discard the buffer and read the file again."""
        if self._document.path is None:
            return
        if self._document.is_dirty and not self._confirm_discard():
            return
        try:
            self._document.load(self._document.path)
        except FileError as exc:
            self._error("Could not reload the file", exc.message, exc.detail)
            return
        self._set_editor_text(self._document.text)
        self._render_preview()
        self._status("Reloaded from disk")

    # ------------------------------------------------------------- watching
    def _set_watch_path(self, path: Path | None) -> None:
        for watched in self._watcher.files():
            self._watcher.removePath(watched)
        if path is not None and path.exists():
            self._watcher.addPath(str(path))

    def _on_file_changed_on_disk(self, _path: str) -> None:
        if self._reload_pending:
            return
        self._reload_pending = True
        QTimer.singleShot(250, self._ask_about_external_change)

    def _ask_about_external_change(self) -> None:
        self._reload_pending = False
        path = self._document.path
        if path is None:
            return
        # An atomic replace swaps the inode, so the watch has to be renewed.
        self._set_watch_path(path)
        if not path.exists():
            self._warn(
                "File removed",
                f"“{path.name}” no longer exists on disk. The text is still here; "
                "use “Save As” to write it somewhere else.",
            )
            return
        if not self._document.changed_on_disk():
            return

        if not self._document.is_dirty:
            try:
                self._document.load(path)
            except FileError:
                return
            self._set_editor_text(self._document.text)
            self._render_preview()
            self._status(f"{path.name} changed on disk — reloaded")
            return

        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle("File changed on disk")
        box.setText(f"“{path.name}” was modified outside {APP_NAME}.")
        box.setInformativeText(
            "You have unsaved changes here. Reload and lose them, or keep editing?"
        )
        reload_button = box.addButton("Reload", QMessageBox.ButtonRole.DestructiveRole)
        box.addButton("Keep editing", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() is reload_button:
            self._document.mark_saved(path, 0)
            self.reload_from_disk()

    # ================================================================ editing
    def _set_editor_text(self, text: str) -> None:
        self._editor.blockSignals(True)
        self._editor.setPlainText(text)
        self._editor.blockSignals(False)
        self._editor.document().clearUndoRedoStacks()
        self._editor.moveCursor(self._editor.textCursor().MoveOperation.Start)
        self._on_text_changed()
        self._update_titles()

    def _on_text_changed(self) -> None:
        self._document.text = self._editor.toPlainText()
        self._preview.schedule_render()
        self._update_counts()
        self._find_panel.refresh_after_edit()
        self._file_tree.mark_dirty(self._document.path, self._document.is_dirty)

    def _render_preview(self) -> None:
        result = self._renderer.render(self._document.text)
        if result.html.strip():
            self._preview.set_body(result.html)
        else:
            self._preview.show_empty_state()

    def _update_counts(self) -> None:
        stats = self._document.stats()
        self._status_counts.setText(
            f"{stats.words:,} words · {stats.characters:,} chars"
        )
        self._status_reading.setText(stats.reading_time)

    def _update_cursor_status(self) -> None:
        cursor = self._editor.textCursor()
        self._status_cursor.setText(
            f"Ln {cursor.blockNumber() + 1}, Col {cursor.positionInBlock() + 1}"
        )

    def _update_titles(self) -> None:
        name = self._document.display_name
        folder = self._document.directory
        self._title_bar.set_document_title(name, str(folder) if folder else "Not saved yet")
        self.setWindowTitle(f"{name} — {APP_NAME}")
        self.act_save.setEnabled(self._document.is_dirty or self._document.is_untitled)

    # ------------------------------------------------------------ formatting
    def insert_link(self) -> None:
        self._editor.insert_link()

    def insert_image(self) -> None:
        """Pick an image and insert a relative Markdown reference to it."""
        start = self._document.directory or self._settings.last_folder or Path.home()
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Insert image",
            str(start),
            "Images (*.png *.jpg *.jpeg *.gif *.webp *.svg *.avif);;All files (*)",
        )
        if not path:
            return
        target = Path(path)
        reference = self._relative_reference(target)
        self._editor.insert_snippet(f"![{target.stem}]({reference})")

    def insert_table(self) -> None:
        snippet = (
            "\n| Column A | Column B |\n"
            "| -------- | -------- |\n"
            "| Cell     | Cell     |\n"
        )
        self._editor.insert_snippet(snippet)

    def _relative_reference(self, target: Path) -> str:
        base = self._document.directory
        if base is None:
            return target.as_posix()
        try:
            return target.resolve().relative_to(base.resolve()).as_posix()
        except ValueError:
            return target.as_posix()

    def _nudge_font(self, delta: int) -> None:
        self._set_editor_font(self._settings.editor_font_size + delta)

    # ------------------------------------------------------------- clipboard
    def paste_clipboard(self) -> None:
        """Toolbar action: paste text, or save a clipboard image next to the file."""
        clipboard = QGuiApplication.clipboard()
        if clipboard is None:
            return
        mime = clipboard.mimeData()
        if mime is not None and self._handle_mime(mime):
            return
        text = clipboard.text()
        if text:
            self._editor.insert_snippet(text)
            self._status("Pasted from clipboard")
        else:
            self._status("Clipboard is empty")

    def _handle_mime(self, source: object) -> bool:
        """Intercept image pastes; return True when the paste was handled."""
        has_image = getattr(source, "hasImage", None)
        if has_image is None or not has_image():
            return False
        image = source.imageData()  # type: ignore[attr-defined]
        if not isinstance(image, QImage) or image.isNull():
            return False

        if self._document.is_untitled:
            self._warn(
                "Save the document first",
                "Pasted images are written next to the document, so it needs a "
                "location on disk before you can drop pictures into it.",
            )
            if not self.save_as():
                return True

        directory = self._document.directory
        if directory is None:
            return True

        assets = directory / "assets"
        target = file_service.unique_path(assets, "pasted-image", ".png")
        buffer = QBuffer()
        buffer.open(QBuffer.OpenModeFlag.WriteOnly)
        if not image.save(buffer, "PNG"):
            self._error("Could not save the image", "Qt refused to encode the image.")
            return True
        try:
            file_service.write_bytes_atomic(target, bytes(buffer.data()))
        except FileError as exc:
            self._error("Could not save the image", exc.message, exc.detail)
            return True

        self._editor.insert_snippet(f"![{target.stem}](assets/{target.name})")
        self._status(f"Saved pasted image to {target}")
        return True

    # =================================================================== view
    def set_view_mode(self, mode: ViewMode | str, *, persist: bool = True) -> None:
        """Switch between editor-only, split and preview-only."""
        mode = ViewMode.coerce(mode)
        self._view_mode = mode

        self._editor_container.setVisible(mode is not ViewMode.PREVIEW)
        self._preview.setVisible(mode is not ViewMode.EDITOR)
        self._format_strip.setVisible(mode is not ViewMode.PREVIEW)

        self.act_view_editor.setChecked(mode is ViewMode.EDITOR)
        self.act_view_split.setChecked(mode is ViewMode.SPLIT)
        self.act_view_preview.setChecked(mode is ViewMode.PREVIEW)

        if mode is not ViewMode.EDITOR:
            self._preview.render_now()
        if persist:
            self._settings.view_mode = mode

    def toggle_rail(self) -> None:
        self._rail_expanded = not self._rail_expanded
        self._settings.sidebar_expanded = self._rail_expanded
        self._animate_width(
            self._rail, RAIL_EXPANDED if self._rail_expanded else RAIL_COLLAPSED, fixed=True
        )
        self._sync_rail_labels()

    def _sync_rail_labels(self) -> None:
        """Show captions only while the rail is expanded."""
        for button, _icon in self._rail_buttons:
            caption = str(button.property("fullText") or "")
            button.setText(caption if self._rail_expanded else "")

    def toggle_file_panel(self, opening: bool) -> None:
        self._animate_width(self._file_panel, FILE_PANEL_WIDTH if opening else 0)

    def toggle_settings_panel(self, opening: bool) -> None:
        self._animate_width(self._settings_panel, self._settings_panel_width(opening), fixed=True)

    def _settings_panel_width(self, opening: bool) -> int:
        """How wide to open the preferences drawer, given the window we have.

        ``fixed=True`` matters as much as the number: with a Fixed size policy a
        widget takes its *sizeHint* clamped to the maximum, not the maximum
        itself, so animating only the maximum left the panel at whatever its
        content happened to hint — which is font-dependent, and on Windows fell
        short of the room its own contents had been forced to occupy.

        The cap keeps the drawer from crowding out the editor on a small window;
        the panel reflows its swatch grid and wraps its text to cope.
        """
        if not opening:
            return 0
        preferred = self._settings_panel.preferred_width()
        return min(preferred, max(MIN_PANEL_WIDTH, self.width() // 3))

    def _reclaim_panel_width(self) -> None:
        """Re-fit the open drawer when the window is resized.

        Pinning the width with ``setFixedWidth`` is what makes the drawer honour
        its computed size, but it also means it cannot give ground on its own as
        the window shrinks around it.
        """
        if not hasattr(self, "_settings_panel"):  # still building the UI
            return
        if not self.act_toggle_settings.isChecked():
            return
        target = self._settings_panel_width(True)
        if target != self._settings_panel.width():
            self._settings_panel.setFixedWidth(target)

    def _animate_width(self, widget: QWidget, target: int, *, fixed: bool = False) -> None:
        animation = QPropertyAnimation(widget, b"maximumWidth", self)
        animation.setDuration(ANIMATION_MS)
        animation.setStartValue(widget.width())
        animation.setEndValue(target)
        animation.setEasingCurve(QEasingCurve.Type.InOutQuad)
        if fixed:
            animation.finished.connect(lambda: widget.setFixedWidth(target))
            widget.setMinimumWidth(0)
        animation.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)

    def toggle_maximized(self) -> None:
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()
        self._sync_maximize_icon()

    def _sync_maximize_icon(self) -> None:
        self._title_bar.set_maximized(self.isMaximized())
        self._title_bar.retint(self._themes.icon_color, self._themes.tokens["muted_on_bg_alt"])

    def changeEvent(self, event: QEvent | None) -> None:  # noqa: N802 - Qt API
        """Keep the maximise/restore glyph honest however the state changed.

        The window is not always maximised by our own button: Aero Snap, Snap
        Layouts, double-clicking the caption and ``Win`` + arrow all go straight
        to the window manager. Reacting to the resulting state change is the only
        way the icon stays in step — on Linux too, where a tiling or keyboard
        shortcut does the same thing behind our back.
        """
        super().changeEvent(event)
        if event is not None and event.type() == QEvent.Type.WindowStateChange:
            self._sync_maximize_icon()

    # ------------------------------------------------------------ scroll sync
    def _guard(self) -> None:
        self._sync_guard = True
        QTimer.singleShot(SYNC_GUARD_MS, lambda: setattr(self, "_sync_guard", False))

    def _on_editor_scrolled(self) -> None:
        if self._sync_guard or not self._settings.sync_scroll:
            return
        if self._view_mode is not ViewMode.SPLIT:
            return
        self._guard()
        self._preview.scroll_to_line(self._editor.first_visible_line())

    def _on_preview_scrolled(self, line: float) -> None:
        if self._sync_guard or not self._settings.sync_scroll:
            return
        if self._view_mode is not ViewMode.SPLIT:
            return
        self._guard()
        self._editor.scroll_to_line(int(round(line)))

    # =============================================================== exports
    def export_html(self) -> None:
        """Write a single self-contained HTML file."""
        default = (self._document.path or Path.home() / self._document.name).with_suffix(".html")
        path, _ = QFileDialog.getSaveFileName(
            self, "Export HTML", str(default), "HTML document (*.html);;All files (*)"
        )
        if not path:
            return
        target = Path(path)
        result = self._renderer.render(self._document.text)
        body = inline_local_images(result.html, self._document.directory)
        html = page_builder.build_page(
            body,
            self._themes.preview_css(self._settings.preview_font_size),
            title=self._document.path.stem if self._document.path else APP_NAME,
            favicon=page_builder.svg_data_uri(app_logo(mono=True)),
            csp=page_builder.EXPORT_CSP,
            generator=f"{APP_NAME} {APP_VERSION}",
        )
        try:
            file_service.write_text_atomic(target, html)
        except FileError as exc:
            self._error("Could not export", exc.message, exc.detail)
            return
        self._status(f"Exported HTML to {target}")

    def export_pdf(self) -> None:
        """Print the preview to PDF using the active theme's print stylesheet."""
        default = (self._document.path or Path.home() / self._document.name).with_suffix(".pdf")
        path, _ = QFileDialog.getSaveFileName(
            self, "Export PDF", str(default), "PDF document (*.pdf);;All files (*)"
        )
        if not path:
            return

        target = Path(path)
        was_visible = self._preview.isVisible()
        self._preview.setVisible(True)
        self._render_preview()
        self._preview.set_css(
            self._themes.preview_css(self._settings.preview_font_size, for_print=True)
        )
        self._status("Rendering PDF…")

        layout = QPageLayout(
            QPageSize(QPageSize.PageSizeId.A4),
            QPageLayout.Orientation.Portrait,
            QMarginsF(15, 16, 15, 16),
            QPageLayout.Unit.Millimeter,
        )

        def finished(file_path: str, success: bool) -> None:
            page = self._preview.page()
            if page is not None:
                page.pdfPrintingFinished.disconnect(finished)
            self._preview.set_css(
                self._themes.preview_css(self._settings.preview_font_size)
            )
            self._preview.setVisible(was_visible)
            if success:
                self._status(f"Exported PDF to {file_path}")
            else:
                self._error("Could not export", "Rendering the PDF failed.")

        page = self._preview.page()
        if page is None:  # pragma: no cover - defensive
            return
        page.pdfPrintingFinished.connect(finished)
        QTimer.singleShot(250, lambda: page.printToPdf(str(target), layout))

    # ============================================================ preferences
    def _set_editor_font(self, size: int) -> None:
        self._settings.editor_font_size = size
        self._editor.set_font_size(self._settings.editor_font_size)
        self._highlighter.rehighlight()

    def _set_preview_font(self, size: int) -> None:
        self._settings.preview_font_size = size
        self._preview.set_css(self._themes.preview_css(size))

    def _set_tab_width(self, width: int) -> None:
        self._settings.tab_width = width
        self._editor.set_tab_width(width)

    def _set_word_wrap(self, enabled: bool) -> None:
        self._settings.word_wrap = enabled
        self._editor.set_word_wrap(enabled)

    def _set_line_numbers(self, enabled: bool) -> None:
        self._settings.show_line_numbers = enabled
        self._editor.set_show_line_numbers(enabled)

    def _set_sync_scroll(self, enabled: bool) -> None:
        self._settings.sync_scroll = enabled

    def _set_native_chrome(self, enabled: bool) -> None:
        self._settings.native_decorations = enabled
        self._status("Window decoration change takes effect after a restart")

    # ================================================================ dialogs
    def show_find(self) -> None:
        self._find_panel.open_panel(with_replace=False)

    def show_replace(self) -> None:
        self._find_panel.open_panel(with_replace=True)

    def show_about(self) -> None:
        AboutDialog(self._settings.filename, self).exec()

    def _show_recent_menu(self) -> None:
        """Drop the recent-files menu just under the rail button."""
        self._rebuild_recent_menu()
        corner = self._recent_button.mapToGlobal(
            QPoint(0, self._recent_button.height())
        )
        self._recent_menu.exec(corner)

    def _rebuild_recent_menu(self) -> None:
        self._recent_menu.clear()
        recents = self._settings.prune_recent_files()
        if not recents:
            empty = self._recent_menu.addAction("No recent documents")
            empty.setEnabled(False)
            return
        for path in recents:
            action = self._recent_menu.addAction(path.name)
            action.setToolTip(str(path))
            action.triggered.connect(lambda _checked=False, p=path: self.open_path(p))
        self._recent_menu.addSeparator()
        self._recent_menu.addAction("Clear list", self._settings.clear_recent_files)

    # ------------------------------------------------------------- messaging
    def _status(self, message: str) -> None:
        self._status_message.setText(message)
        log.info("%s", message)

    def _error(self, title: str, message: str, detail: str = "") -> None:
        log.error("%s: %s (%s)", title, message, detail)
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Critical)
        box.setWindowTitle(title)
        box.setText(message)
        if detail:
            box.setDetailedText(detail)
        box.exec()
        self._status(message)

    def _warn(self, title: str, message: str) -> None:
        log.warning("%s: %s", title, message)
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(title)
        box.setText(message)
        box.exec()

    # ============================================================ drag & drop
    def dragEnterEvent(self, event: QDragEnterEvent | None) -> None:  # noqa: N802 - Qt API
        if event is None:
            return
        mime = event.mimeData()
        if mime is not None and mime.hasUrls() and self._acceptable_urls(mime.urls()):
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent | None) -> None:  # noqa: N802 - Qt API
        if event is None:
            return
        mime = event.mimeData()
        if mime is None or not mime.hasUrls():
            return
        for url in mime.urls():
            path = Path(url.toLocalFile())
            if path.is_dir():
                self.open_folder(path)
                event.acceptProposedAction()
                return
            if path.suffix.lower() in MARKDOWN_SUFFIXES:
                self.open_path(path)
                event.acceptProposedAction()
                return

    @staticmethod
    def _acceptable_urls(urls: list[QUrl]) -> bool:
        for url in urls:
            if not url.isLocalFile():
                continue
            path = Path(url.toLocalFile())
            if path.is_dir() or path.suffix.lower() in MARKDOWN_SUFFIXES:
                return True
        return False

    # ================================================================ closing
    def closeEvent(self, event: QCloseEvent | None) -> None:  # noqa: N802 - Qt API
        if event is None:
            return
        if not self._confirm_discard():
            event.ignore()
            return
        self._settings.geometry = self.saveGeometry()
        if self._view_mode is ViewMode.SPLIT:
            self._settings.split_sizes = self._splitter.sizes()
        self._settings.first_run_done = True
        self._settings.sync()
        event.accept()
