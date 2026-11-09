# SPDX-License-Identifier: GPL-3.0-or-later
"""Note search by title, docked in the side panel next to the explorer.

Finds notes by their title and their headings (H1–H6) in the whole folder or
in one subfolder: results update as you type, each note lists the headings
that matched, and the arrow keys walk through them, jumping the editor *and*
the preview to that heading.  Searching what the notes *say* is the job of the
advanced search (Ctrl+E).  The matching lives in :mod:`app.core.search`.
"""

from __future__ import annotations

import html
from pathlib import Path

from PyQt6.QtCore import QModelIndex, QPoint, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QAbstractTextDocumentLayout, QKeyEvent, QPainter, QPalette, QTextDocument
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QSizePolicy,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..core.search import HeadingHit, LineHit, SearchIndex, TitleResult, parse_query
from ..themes.palettes import Palette

__all__ = ["SearchPanel"]

#: Pause after the last keystroke before searching.
SEARCH_DEBOUNCE_MS = 120
#: Notes shown at most; the summary says when there were more.
MAX_RESULTS = 200

_HTML_ROLE = Qt.ItemDataRole.UserRole + 1
_PATH_ROLE = Qt.ItemDataRole.UserRole + 2
_LINE_ROLE = Qt.ItemDataRole.UserRole + 3
_NTH_ROLE = Qt.ItemDataRole.UserRole + 4

_HELP = (
    "Type to find notes by their title and headings (H1, H2…). Pick a folder "
    "below to search only there, or leave the box empty to list its notes.\n\n"
    "Word forms count: “forma” also finds formas, formación; the start of a "
    "word is enough while typing.\n\n"
    "“exact phrase”   -exclude   tag:exam   in:folder   field:value (front matter)\n\n"
    "↓ / ↑ walk the results, Enter opens, F3 / Shift+F3 jump between the "
    "marked headings.\n\n"
    "To search what the notes say, use the advanced search (Ctrl+E): it finds "
    "the section that explains something, even from pasted code, an error or "
    "a whole question."
)


class _HtmlDelegate(QStyledItemDelegate):
    """Draws the rich-text snippets (matched words emphasised)."""

    PAD_X, PAD_Y = 4, 3

    def __init__(self, view: QTreeWidget) -> None:
        super().__init__(view)
        self._view = view
        self.colors = {"muted": "#6272a4", "hit": "#bd93f9", "hit_bg": "#44475a"}

    def _document(self, index: QModelIndex, width: int, selected: bool) -> QTextDocument:
        doc = QTextDocument()
        doc.setDocumentMargin(0)
        doc.setDefaultFont(self._view.font())
        c = self.colors
        # The view's own text colour (set by the stylesheet) always contrasts
        # with its background, whatever the theme.
        role = QPalette.ColorRole.HighlightedText if selected else QPalette.ColorRole.Text
        text = self._view.palette().color(role).name()
        doc.setDefaultStyleSheet(
            f"body {{ color: {text}; }}"
            f".muted {{ color: {text if selected else c['muted']}; }}"
            f"b {{ color: {text if selected else c['hit']}; "
            f"background-color: {c['hit_bg']}; }}"
        )
        doc.setHtml(f"<body>{index.data(_HTML_ROLE) or ''}</body>")
        doc.setTextWidth(max(40, width - 2 * self.PAD_X))
        return doc

    def _width(self, option: QStyleOptionViewItem, index: QModelIndex) -> int:
        if option.rect.width() > 0:
            return option.rect.width()
        depth = 0
        parent = index.parent()
        while parent.isValid():
            depth += 1
            parent = parent.parent()
        return self._view.viewport().width() - self._view.indentation() * (depth + 1)

    def paint(self, painter: QPainter | None, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        if painter is None:
            return
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        opt.text = ""
        widget = opt.widget
        style = widget.style() if widget is not None else None
        if style is not None:
            style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, widget)
        selected = bool(opt.state & QStyle.StateFlag.State_Selected)
        doc = self._document(index, opt.rect.width(), selected)
        painter.save()
        painter.translate(opt.rect.topLeft() + QPoint(self.PAD_X, self.PAD_Y))
        context = QAbstractTextDocumentLayout.PaintContext()
        context.palette.setColor(QPalette.ColorRole.Text, opt.palette.color(QPalette.ColorRole.Text))
        layout = doc.documentLayout()
        if layout is not None:
            layout.draw(painter, context)
        painter.restore()

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:  # noqa: N802
        doc = self._document(index, self._width(option, index), False)
        return QSize(int(doc.idealWidth()) + 2 * self.PAD_X,
                     int(doc.size().height()) + 2 * self.PAD_Y)


class _ResultTree(QTreeWidget):
    """Result list that hands Up-at-the-top back to the query box."""

    escape_to_input = pyqtSignal()

    def keyPressEvent(self, event: QKeyEvent | None) -> None:  # noqa: N802 - Qt API
        if event is None:
            return
        if event.key() == Qt.Key.Key_Up and self.currentItem() is self._first_hit():
            self.escape_to_input.emit()
            return
        if event.key() == Qt.Key.Key_Escape:
            self.escape_to_input.emit()
            return
        super().keyPressEvent(event)

    def _first_hit(self) -> QTreeWidgetItem | None:
        top = self.topLevelItem(0)
        if top is None:
            return None
        return top.child(0) or top

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().resizeEvent(event)
        # Snippets wrap to the width, so their heights change with it.
        self.scheduleDelayedItemsLayout()


class SearchPanel(QWidget):
    """Query box, folder scope and the grouped list of hits."""

    #: A hit was chosen deliberately (click, Enter): ``(note, line, nth)`` where
    #: ``nth`` is the hit's position among the hits on that line.
    hit_activated = pyqtSignal(Path, int, int)
    #: The keyboard moved onto a hit; follow it unless that would interrupt.
    hit_selected = pyqtSignal(Path, int, int)
    #: The query changed (empty string when cleared).
    query_changed = pyqtSignal(str)
    #: Esc on an empty query.
    close_requested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("searchPanel")
        self._index: SearchIndex | None = None
        self._results: list[TitleResult] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 0)
        layout.setSpacing(6)

        row = QHBoxLayout()
        row.setSpacing(4)
        self.input = QLineEdit()
        self.input.setPlaceholderText("Find notes by title or heading…")
        self.input.setClearButtonEnabled(True)
        self.input.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.input.textChanged.connect(self._schedule)
        self.input.returnPressed.connect(self._activate_first)
        self.input.installEventFilter(self)
        self.exact_button = QToolButton()
        self.exact_button.setText("ab")
        self.exact_button.setCheckable(True)
        self.exact_button.setAutoRaise(True)
        self.exact_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.exact_button.setToolTip("Exact words only (no plurals, stems, word starts or typos)")
        self.exact_button.toggled.connect(lambda _checked: self.run_search())
        row.addWidget(self.input)
        row.addWidget(self.exact_button)
        layout.addLayout(row)

        self.scope = QComboBox()
        self.scope.setToolTip("Search only inside this folder")
        self.scope.currentIndexChanged.connect(lambda _i: self.run_search())
        layout.addWidget(self.scope)

        self.summary = QLabel("")
        self.summary.setObjectName("findCounter")
        layout.addWidget(self.summary)

        self.help = QLabel(_HELP)
        self.help.setObjectName("emptyBody")
        self.help.setWordWrap(True)
        self.help.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.help.setContentsMargins(4, 8, 4, 8)
        layout.addWidget(self.help)

        self.tree = _ResultTree(self)
        self.tree.setObjectName("searchResults")
        self.tree.setHeaderHidden(True)
        self.tree.setIndentation(10)
        self.tree.setUniformRowHeights(False)
        self.tree.setWordWrap(True)
        self.tree.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tree.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tree.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._delegate = _HtmlDelegate(self.tree)
        self.tree.setItemDelegate(self._delegate)
        self.tree.itemClicked.connect(lambda item, _col: self._emit(item, self.hit_activated))
        self.tree.itemActivated.connect(lambda item, _col: self._emit(item, self.hit_activated))
        self.tree.currentItemChanged.connect(self._on_current_changed)
        self.tree.escape_to_input.connect(self.focus_input)
        self.tree.hide()
        layout.addWidget(self.tree, 1)
        layout.addStretch(0)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(SEARCH_DEBOUNCE_MS)
        self._timer.timeout.connect(self.run_search)

    # ------------------------------------------------------------ appearance
    def apply_palette(self, palette: Palette) -> None:
        t = palette.tokens()
        self._delegate.colors = {
            "muted": t["muted_on_bg_alt"],
            "hit": t["accent_on_bg_alt"],
            "hit_bg": t["accent_soft"],
        }
        self.tree.viewport().update()

    # ----------------------------------------------------------------- state
    def set_index(self, index: SearchIndex | None) -> None:
        self._index = index
        self.refresh_scopes()
        self.run_search()

    def refresh_scopes(self, select: str | None = None) -> None:
        """Rebuild the folder picker, keeping (or switching to) a folder."""
        current = select if select is not None else self.scope_folder()
        folders = self._index.folders() if self._index is not None else []
        if current and current not in folders:
            folders = sorted([*folders, current], key=str.casefold)
        self.scope.blockSignals(True)
        self.scope.clear()
        root_name = self._index.vault.root.name if self._index is not None else ""
        self.scope.addItem(f"All of {root_name}" if root_name else "Whole folder", "")
        for folder in folders:
            depth = folder.count("/")
            self.scope.addItem("    " * depth + folder.rsplit("/", 1)[-1], folder)
            self.scope.setItemData(self.scope.count() - 1, folder, Qt.ItemDataRole.ToolTipRole)
        found = self.scope.findData(current or "")
        self.scope.setCurrentIndex(max(0, found))
        self.scope.blockSignals(False)

    def scope_folder(self) -> str:
        return str(self.scope.currentData() or "")

    def set_scope(self, folder: str) -> None:
        self.refresh_scopes(select=folder)
        self.run_search()

    @property
    def query(self) -> str:
        return self.input.text().strip()

    @property
    def exact(self) -> bool:
        return self.exact_button.isChecked()

    def focus_input(self) -> None:
        self.input.setFocus()
        self.input.selectAll()

    # --------------------------------------------------------------- search
    def _schedule(self) -> None:
        self._timer.start()

    def run_search(self) -> None:
        """Search now and repopulate the results (keeps the selected hit)."""
        self._timer.stop()
        query = self.query
        previous = self._current_key()
        self.tree.clear()
        self._results = []
        if not (query or self.scope_folder()) or self._index is None:
            self.summary.setText("" if self._index is not None else "Open a folder to search it.")
            self.help.show()
            self.tree.hide()
            self.query_changed.emit("")
            return
        parsed = parse_query(query)
        self._results = self._index.search_titles(parsed, scope=self.scope_folder(), exact=self.exact)
        shown = self._results[:MAX_RESULTS]
        headings = sum(len(r.headings) for r in self._results)
        notes = len(self._results)
        if not notes:
            self.summary.setText("No notes")
        else:
            more = f" (showing {MAX_RESULTS})" if notes > MAX_RESULTS else ""
            found = f" · {headings} heading{'s' if headings != 1 else ''}" if headings else ""
            self.summary.setText(f"{notes} note{'s' if notes != 1 else ''}{found}{more}")
        self.help.setVisible(not notes)
        self.tree.setVisible(bool(notes))
        self._fill(shown)
        self._restore_current(previous)
        self.query_changed.emit(query)

    def _fill(self, results: list[TitleResult]) -> None:
        self.tree.setUpdatesEnabled(False)
        for result in results:
            top = QTreeWidgetItem(self.tree)
            folder = result.rel.rsplit("/", 1)[0] if "/" in result.rel else ""
            title = _marked_html(LineHit(0, result.title, result.title_spans))
            top.setData(0, _HTML_ROLE,
                        f"<span style='font-weight:600'>{title}</span>"
                        f"<span class='muted'>  {html.escape(folder)}</span>")
            top.setData(0, _PATH_ROLE, str(result.path))
            first = result.headings[0].line if result.headings else 1
            top.setData(0, _LINE_ROLE, first)
            top.setData(0, _NTH_ROLE, 0)
            top.setToolTip(0, result.rel)
            for hit in result.headings:
                child = QTreeWidgetItem(top)
                child.setData(0, _HTML_ROLE, _heading_html(hit))
                child.setData(0, _PATH_ROLE, str(result.path))
                child.setData(0, _LINE_ROLE, hit.line)
                child.setData(0, _NTH_ROLE, 0)
            top.setExpanded(True)
        self.tree.setUpdatesEnabled(True)

    def _current_key(self) -> tuple[str, int, bool] | None:
        item = self.tree.currentItem()
        if item is None:
            return None
        return (str(item.data(0, _PATH_ROLE)), int(item.data(0, _LINE_ROLE)), item.parent() is None)

    def _restore_current(self, key: tuple[str, int, bool] | None) -> None:
        if key is None:
            return
        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            if top is None or top.data(0, _PATH_ROLE) != key[0]:
                continue
            target = top
            if not key[2]:
                for j in range(top.childCount()):
                    child = top.child(j)
                    if child is not None and child.data(0, _LINE_ROLE) == key[1]:
                        target = child
                        break
            self.tree.blockSignals(True)
            self.tree.setCurrentItem(target)
            self.tree.blockSignals(False)
            return

    # -------------------------------------------------------------- choosing
    def _emit(self, item: QTreeWidgetItem | None, signal) -> None:
        if item is None:
            return
        signal.emit(Path(item.data(0, _PATH_ROLE)), int(item.data(0, _LINE_ROLE)),
                    int(item.data(0, _NTH_ROLE)))

    def _on_current_changed(self, current: QTreeWidgetItem | None, _previous) -> None:
        # A mouse click also fires itemClicked; only keyboard moves "select".
        if current is not None and self.tree.hasFocus():
            self._emit(current, self.hit_selected)

    def _activate_first(self) -> None:
        if self._timer.isActive():
            self.run_search()
        top = self.tree.topLevelItem(0)
        if top is None:
            return
        target = top.child(0) or top
        self.tree.setCurrentItem(target)
        self._emit(target, self.hit_activated)

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 - Qt API
        if obj is self.input and isinstance(event, QKeyEvent) and event.type() == event.Type.KeyPress:
            if event.key() == Qt.Key.Key_Down and self.tree.isVisible():
                self.tree.setFocus()
                first = self.tree.topLevelItem(0)
                if first is not None:
                    target = first.child(0) or first
                    if self.tree.currentItem() is target:
                        self._emit(target, self.hit_selected)
                    self.tree.setCurrentItem(target)
                return True
            if event.key() == Qt.Key.Key_Escape:
                if self.input.text():
                    self.input.clear()
                else:
                    self.close_requested.emit()
                return True
        return super().eventFilter(obj, event)


def _heading_html(hit: HeadingHit) -> str:
    indent = "&nbsp;&nbsp;" * max(0, hit.level - 1)
    return f"{indent}<span class='muted'>H{hit.level}</span>&nbsp;&nbsp;{_marked_html(hit)}"


def _marked_html(hit: LineHit) -> str:
    parts: list[str] = []
    last = 0
    for start, end in sorted(hit.spans):
        if start < last:
            continue
        parts.append(html.escape(hit.text[last:start]))
        parts.append(f"<b>{html.escape(hit.text[start:end])}</b>")
        last = end
    parts.append(html.escape(hit.text[last:]))
    return "".join(parts)
