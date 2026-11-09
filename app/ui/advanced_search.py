# SPDX-License-Identifier: GPL-3.0-or-later
"""Advanced search: a big search box over the whole window that finds *sections*.

Made for open-book exams and study sessions: press the shortcut, type a few words, a question,
or paste code, a traceback or the whole statement, and the list shows the
sections that explain it as ``Note › Heading › Subheading`` with the lines
that matched.  The keyboard does everything: ↑/↓ move through the results
(the preview on the right follows), Enter opens the note at that section,
Shift+Enter adds a line to the query and Esc closes.  Reopening keeps the
last query and results.

The ranking lives in :mod:`app.core.section_search`; this module only shows it.
"""

from __future__ import annotations

import html
import re
from collections.abc import Callable

from PyQt6.QtCore import QMimeData, QRect, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QKeyEvent, QPainter, QPaintEvent, QResizeEvent
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QSizePolicy,
    QSplitter,
    QTextBrowser,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..core.search import LineHit, SearchIndex, normalize
from ..core.section_search import SectionResult
from ..themes.palettes import Palette
from .search_panel import _HTML_ROLE, _HtmlDelegate

__all__ = ["AdvancedSearch"]

#: Pause after typing before searching; pasted text searches at once.
DEBOUNCE_MS = 90
MAX_RESULTS = 100
#: Most lines the query box grows to before it scrolls.
MAX_INPUT_LINES = 8

_HINT = "↑↓ results · Enter open · Shift+Enter new line · Esc close"
_EMPTY_HELP = (
    "<p><b>Find where something is explained.</b> Type a few words or a question "
    "(<i>crear un dataframe</i>), or paste code, an error message or a whole "
    "statement: the results are the sections of your notes that explain it.</p>"
    "<p>Filters: <code>tag:pandas</code> <code>in:tema-3</code> <code>title:merge</code> "
    "<code>-excluir</code> <code>\"frase exacta\"</code>. Sections with a "
    "<code>&lt;!-- busca … --&gt;</code> comment rank by its keywords, purpose, API and "
    "errors (see <i>docs/guia-metadatos-notas.md</i>).</p>"
)
_WORD_RE = re.compile(r"[^\W_]+", re.UNICODE)
_TAG_SPLIT_RE = re.compile(r"(<[^>]*>)")
_FIELD_NAMES = {"title": "title", "aka": "aka", "keys": "keywords", "api": "API",
                "errors": "errors", "intent": "purpose", "path": "headings", "body": "text",
                "code": "code"}


class _QueryEdit(QPlainTextEdit):
    """Multi-line query box: Enter submits, the arrows walk the results."""

    submitted = pyqtSignal()
    navigate = pyqtSignal(int)
    escape = pyqtSignal()
    pasted = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("advancedQuery")
        self.setTabChangesFocus(True)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.textChanged.connect(self._fit_height)
        self._fit_height()

    def keyPressEvent(self, event: QKeyEvent | None) -> None:  # noqa: N802 - Qt API
        if event is None:
            return
        key, mods = event.key(), event.modifiers()
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if mods & Qt.KeyboardModifier.ShiftModifier:
                self.textCursor().insertText("\n")
            else:
                self.submitted.emit()
            return
        steps = {Qt.Key.Key_Down: 1, Qt.Key.Key_Up: -1, Qt.Key.Key_PageDown: 8,
                 Qt.Key.Key_PageUp: -8}
        if key in steps and not mods & Qt.KeyboardModifier.AltModifier:
            self.navigate.emit(steps[key])
            return
        if key == Qt.Key.Key_Escape:
            self.escape.emit()
            return
        super().keyPressEvent(event)

    def insertFromMimeData(self, source: QMimeData | None) -> None:  # noqa: N802 - Qt API
        # Plain text only: rich text from a PDF or a web page brings nothing.
        if source is not None and source.hasText():
            self.textCursor().insertText(source.text())
            self.pasted.emit()

    def _fit_height(self) -> None:
        lines = max(1, min(MAX_INPUT_LINES, self.document().size().height() or 1))
        metrics = self.fontMetrics()
        margins = self.contentsMargins()
        height = int(lines * metrics.lineSpacing() + 2 * self.document().documentMargin()
                     + margins.top() + margins.bottom() + 4)
        self.setFixedHeight(height)

    def resizeEvent(self, event: QResizeEvent | None) -> None:  # noqa: N802 - Qt API
        super().resizeEvent(event)
        self._fit_height()


class AdvancedSearch(QWidget):
    """The overlay: query box, filters, result list and section preview."""

    #: The user picked a section (Enter, double click).
    section_chosen = pyqtSignal(object)
    closed = pyqtSignal()

    def __init__(self, render: Callable[[str], str], parent: QWidget) -> None:
        """``render`` turns Markdown into the HTML shown in the preview."""
        super().__init__(parent)
        self.setObjectName("advancedOverlay")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self._render = render
        self._index: SearchIndex | None = None
        self._results: list[SectionResult] = []
        self._status = ""
        self._shade = QColor(0, 0, 0, 110)
        self._preview_css = ""
        self._code_css = ""

        self.card = QFrame(self)
        self.card.setObjectName("advancedCard")
        layout = QVBoxLayout(self.card)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(8)

        header = QHBoxLayout()
        title = QLabel("Advanced search")
        title.setObjectName("advancedTitle")
        self.hint = QLabel(_HINT)
        self.hint.setObjectName("findCounter")
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(self.hint)
        layout.addLayout(header)

        self.input = _QueryEdit(self.card)
        self.input.setPlaceholderText(
            "Words, a question, code, an error or the whole statement…"
        )
        self.input.textChanged.connect(self._schedule)
        self.input.submitted.connect(self.choose_current)
        self.input.navigate.connect(self.move)
        self.input.escape.connect(self.close_overlay)
        self.input.pasted.connect(lambda: QTimer.singleShot(0, self.run_search))
        layout.addWidget(self.input)

        filters = QHBoxLayout()
        filters.setSpacing(8)
        self.scope = QComboBox(self.card)
        self.scope.setToolTip("Search only inside this folder")
        self.scope.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.scope.currentIndexChanged.connect(lambda _i: self.run_search())
        self.exact = QCheckBox("Exact words", self.card)
        self.exact.setToolTip("No word forms, synonyms or typo tolerance")
        self.exact.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.exact.toggled.connect(lambda _on: self.run_search())
        self.summary = QLabel("")
        self.summary.setObjectName("findCounter")
        self.summary.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        filters.addWidget(self.scope)
        filters.addWidget(self.exact)
        filters.addWidget(self.summary, 1)
        layout.addLayout(filters)

        self.splitter = QSplitter(Qt.Orientation.Horizontal, self.card)
        self.splitter.setChildrenCollapsible(False)
        self.list = QTreeWidget(self.splitter)
        self.list.setObjectName("searchResults")
        self.list.setHeaderHidden(True)
        self.list.setRootIsDecorated(False)
        self.list.setIndentation(0)
        self.list.setUniformRowHeights(False)
        self.list.setWordWrap(True)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.list.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # the query box keeps the keys
        self._delegate = _HtmlDelegate(self.list)
        self.list.setItemDelegate(self._delegate)
        self.list.currentItemChanged.connect(lambda _c, _p: self._show_preview())
        self.list.itemDoubleClicked.connect(lambda _item, _col: self.choose_current())
        self.preview = QTextBrowser(self.splitter)
        self.preview.setObjectName("advancedPreview")
        self.preview.setOpenLinks(False)
        self.preview.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.splitter.addWidget(self.list)
        self.splitter.addWidget(self.preview)
        self.splitter.setStretchFactor(0, 5)
        self.splitter.setStretchFactor(1, 6)
        layout.addWidget(self.splitter, 1)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(DEBOUNCE_MS)
        self._timer.timeout.connect(self.run_search)
        self.hide()

    # ------------------------------------------------------------ appearance
    def apply_palette(self, palette: Palette, code_css: str = "") -> None:
        t = palette.tokens()
        self._delegate.colors = {
            "muted": t["muted_on_surface"],
            "hit": t["accent_on_surface"],
            "hit_bg": t["accent_soft"],
        }
        self._shade = QColor(0, 0, 0, 140 if palette.is_dark else 90)
        self.card.setStyleSheet(
            f"QFrame#advancedCard {{ background: {t['surface']}; border: 1px solid "
            f"{t['border_strong']}; border-radius: 10px; }}"
            f"QLabel#advancedTitle {{ font-weight: 600; color: {t['text_on_surface']}; }}"
            f"QPlainTextEdit#advancedQuery {{ background: {t['bg']}; color: {t['text_on_bg']}; "
            f"border: 1px solid {t['accent']}; border-radius: 6px; padding: 4px 6px; }}"
            f"QTextBrowser#advancedPreview {{ background: {t['bg']}; color: {t['text_on_bg']}; "
            f"border: 1px solid {t['border']}; border-radius: 6px; }}"
        )
        self._preview_css = (
            f"body {{ color: {t['text_on_bg']}; }}"
            f"h1, h2, h3, h4, h5, h6 {{ color: {t['text_on_bg']}; margin: 6px 0 4px 0; }}"
            f"pre {{ background: {t['code_bg']}; padding: 6px; }}"
            f"code {{ color: {t['inline_code_fg']}; background: {t['code_bg']}; }}"
            f".crumbs, .meta {{ color: {t['muted_on_bg']}; }}"
            f"span.hit {{ background: {t['accent_soft']}; color: {t['text_on_accent_soft']}; }}"
            f"a {{ color: {t['accent_on_bg']}; }}"
        )
        self._code_css = code_css
        self.preview.document().setDefaultStyleSheet(self._code_css + self._preview_css)
        self.list.viewport().update()
        self._show_preview()
        self.update()

    def paintEvent(self, event: QPaintEvent | None) -> None:  # noqa: N802 - Qt API
        painter = QPainter(self)
        painter.fillRect(self.rect(), self._shade)
        painter.end()

    def resizeEvent(self, event: QResizeEvent | None) -> None:  # noqa: N802 - Qt API
        super().resizeEvent(event)
        self._place_card()

    def _place_card(self) -> None:
        width = min(self.width() - 32, max(640, int(self.width() * 0.86)), 1400)
        height = self.height() - 2 * max(16, int(self.height() * 0.05))
        self.card.setGeometry(QRect((self.width() - width) // 2,
                                    (self.height() - height) // 2, width, height))

    # ----------------------------------------------------------------- state
    def set_index(self, index: SearchIndex | None, status: str = "") -> None:
        """Use ``index``; ``status`` explains why there is none yet."""
        self._index = index
        self._status = status
        self.refresh_scopes()
        self.run_search()

    def refresh_scopes(self) -> None:
        current = str(self.scope.currentData() or "")
        folders = self._index.folders() if self._index is not None else []
        self.scope.blockSignals(True)
        self.scope.clear()
        self.scope.addItem("All folders", "")
        for folder in folders:
            self.scope.addItem("    " * folder.count("/") + folder.rsplit("/", 1)[-1], folder)
            self.scope.setItemData(self.scope.count() - 1, folder, Qt.ItemDataRole.ToolTipRole)
        self.scope.setCurrentIndex(max(0, self.scope.findData(current)))
        self.scope.blockSignals(False)

    def set_scope(self, folder: str) -> None:
        self.refresh_scopes()
        found = self.scope.findData(folder)
        self.scope.setCurrentIndex(max(0, found))

    @property
    def query(self) -> str:
        return self.input.toPlainText()

    def open_overlay(self, text: str | None = None) -> None:
        """Show over the parent; ``text`` replaces the query if given."""
        parent = self.parentWidget()
        if parent is not None:
            self.setGeometry(parent.rect())
        self._place_card()
        self.show()
        self.raise_()
        if text:
            self.input.setPlainText(text)
            self.run_search()
        self.input.setFocus()
        self.input.selectAll()

    def close_overlay(self) -> None:
        self.hide()
        self.closed.emit()

    # --------------------------------------------------------------- search
    def _schedule(self) -> None:
        self._timer.start()

    def run_search(self) -> None:
        self._timer.stop()
        text = self.query
        self.list.clear()
        self._results = []
        if self._index is None:
            self.summary.setText(self._status or "Open a folder to search it.")
            self._show_help()
            return
        if not text.strip():
            self.summary.setText(f"{len(self._index.sections)} sections in "
                                 f"{len(self._index.docs)} notes")
            self._show_help()
            return
        results, analysis = self._index.search_sections(
            text, scope=str(self.scope.currentData() or ""), exact=self.exact.isChecked(),
            limit=MAX_RESULTS,
        )
        self._results = results
        count = f"{len(results)}{'+' if len(results) >= MAX_RESULTS else ''} section" \
                f"{'s' if len(results) != 1 else ''}"
        self.summary.setText(f"{count} · {analysis.describe()}" if results
                             else f"No results · {analysis.describe()}")
        self.summary.setToolTip(analysis.describe())
        self.list.setUpdatesEnabled(False)
        for result in results:
            item = QTreeWidgetItem(self.list)
            item.setData(0, _HTML_ROLE, _result_html(result))
            item.setToolTip(0, f"{result.rel}:{result.line}")
        self.list.setUpdatesEnabled(True)
        if results:
            self.list.setCurrentItem(self.list.topLevelItem(0))
        else:
            self.preview.setHtml(
                "<p class='meta'>Nothing matches. Try fewer or other words, untick "
                "<i>Exact words</i>, or choose <i>All folders</i>.</p>"
            )

    def _show_help(self) -> None:
        self.preview.setHtml(f"<body>{_EMPTY_HELP}</body>")

    # ------------------------------------------------------------- choosing
    def move(self, delta: int) -> None:
        count = self.list.topLevelItemCount()
        if not count:
            return
        current = self.list.indexOfTopLevelItem(self.list.currentItem()) \
            if self.list.currentItem() is not None else -1
        target = max(0, min(count - 1, current + delta))
        self.list.setCurrentItem(self.list.topLevelItem(target))

    def current_result(self) -> SectionResult | None:
        item = self.list.currentItem()
        if item is None:
            return None
        row = self.list.indexOfTopLevelItem(item)
        return self._results[row] if 0 <= row < len(self._results) else None

    def choose_current(self) -> None:
        if self._timer.isActive():
            self.run_search()
        result = self.current_result()
        if result is None:
            return
        self.hide()
        self.section_chosen.emit(result)

    def _show_preview(self) -> None:
        result = self.current_result()
        if result is None or self._index is None:
            return
        source = self._index.sections.section_text(result)
        try:
            body = self._render(source)
        except Exception:  # noqa: BLE001 - a preview must never take the search down
            body = f"<pre>{html.escape(source)}</pre>"
        crumbs = " › ".join(html.escape(c) for c in result.crumbs)
        meta = _meta_html(result)
        self.preview.setHtml(
            f"<body><p class='crumbs'>{crumbs}<br><small>{html.escape(result.rel)}"
            f":{result.line}</small></p>{meta}{_highlight_html(body, result.terms)}</body>"
        )


# ------------------------------------------------------------------ helpers
def _result_html(result: SectionResult) -> str:
    crumbs = result.crumbs
    head = " › ".join(html.escape(c) for c in crumbs[:-1])
    last = html.escape(crumbs[-1])
    why = ", ".join(_FIELD_NAMES.get(f, f) for f in result.fields[:3])
    folder = result.rel.rsplit("/", 1)[0] if "/" in result.rel else ""
    parts = [
        f"<span class='muted'>{head} › </span>" if head else "",
        f"<b style='background:transparent'>{last}</b>",
        f"<span class='muted'>  · {html.escape(why)}</span>" if why else "",
        f"<span class='muted'>  · {html.escape(folder)}</span>" if folder else "",
    ]
    lines = [_line_html(hit, result.snippet_is_code) for hit in result.snippet]
    return "".join(parts) + ("<br>" + "<br>".join(lines) if lines else "")


def _line_html(hit: LineHit, code: bool) -> str:
    parts: list[str] = []
    last = 0
    for start, end in sorted(hit.spans):
        if start < last:
            continue
        parts.append(html.escape(hit.text[last:start]))
        parts.append(f"<b>{html.escape(hit.text[start:end])}</b>")
        last = end
    parts.append(html.escape(hit.text[last:]))
    text = "".join(parts)
    return f"<tt>{text}</tt>" if code else text


def _meta_html(result: SectionResult) -> str:
    rows = []
    for key, label in (("intent", "Purpose"), ("api", "API"), ("errors", "Errors")):
        items = result.meta.get(key)
        if items:
            rows.append(f"<b>{label}:</b> {html.escape('; '.join(items))}")
    return f"<p class='meta'><small>{'<br>'.join(rows)}</small></p>" if rows else ""


def _highlight_html(body: str, terms: list[str]) -> str:
    """Wrap the words of ``body`` that match ``terms`` (outside tags) in spans."""
    plain = {t for t in terms if "." not in t and "_" not in t}
    compound = sorted((t for t in terms if "." in t or "_" in t), key=len, reverse=True)
    if not plain and not compound:
        return body
    compound_re = re.compile("|".join(re.escape(t) for t in compound), re.IGNORECASE) \
        if compound else None
    out: list[str] = []
    for piece in _TAG_SPLIT_RE.split(body):
        if not piece or piece.startswith("<"):
            out.append(piece)
            continue
        out.append(_mark_text(piece, plain, compound_re))
    return "".join(out)


def _mark_text(text: str, plain: set[str], compound_re: re.Pattern[str] | None) -> str:
    spans: list[tuple[int, int]] = []
    if compound_re is not None:
        spans += [(m.start(), m.end()) for m in compound_re.finditer(text)]
    for match in _WORD_RE.finditer(text):
        # Skip the letters of HTML entities such as ``&amp;``.
        if match.start() and text[match.start() - 1] == "&":
            continue
        if normalize(match.group(0)) in plain:
            spans.append((match.start(), match.end()))
    if not spans:
        return text
    out: list[str] = []
    last = 0
    for start, end in sorted(spans):
        if start < last:
            continue
        out.append(text[last:start])
        out.append(f"<span class='hit'>{text[start:end]}</span>")
        last = end
    out.append(text[last:])
    return "".join(out)
