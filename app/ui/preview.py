# SPDX-License-Identifier: GPL-3.0-or-later
"""The rendered Markdown preview.

Why a full browser engine instead of ``QTextBrowser``?  Qt's rich-text engine
only understands a small subset of HTML 4 and has no CSS grid/flex, no
``position: sticky``, no web fonts and famously poor table rendering.
``QWebEngineView`` gives real CSS, correct code highlighting, anchor navigation
and — the practical clincher — ``printToPdf`` for a faithful PDF export.

The page is loaded exactly once as an empty shell; subsequent updates replace
the article's inner HTML through JavaScript.  That keeps the scroll position
across keystrokes, which a ``setHtml`` round-trip would destroy.
"""

from __future__ import annotations

import json
import logging
import secrets
from pathlib import Path

from PyQt6.QtCore import (
    QObject,
    QTimer,
    QUrl,
    pyqtSignal,
    pyqtSlot,
)
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWebChannel import QWebChannel
from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineSettings
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWidgets import QWidget

from ..core import page as page_builder
from ..core.file_service import MARKDOWN_SUFFIXES

log = logging.getLogger(__name__)

__all__ = ["MarkdownPreview", "PREVIEW_DEBOUNCE_MS"]

#: How long to wait after the last keystroke before re-rendering.
PREVIEW_DEBOUNCE_MS = 250

_SHELL_SCRIPT = r"""
(function () {
    const state = { items: [], suppressUntil: 0, ready: false };

    function collect() {
        state.items = Array.prototype.slice
            .call(document.querySelectorAll('[data-source-line]'))
            .map(function (el) {
                return { el: el, line: parseInt(el.getAttribute('data-source-line'), 10) };
            })
            .filter(function (item) { return !isNaN(item.line); })
            .sort(function (a, b) { return a.line - b.line; });
    }

    function topOf(el) {
        return el.getBoundingClientRect().top + window.scrollY;
    }

    function offsetForLine(line) {
        if (!state.items.length) { return 0; }
        let prev = null, next = null;
        for (let i = 0; i < state.items.length; i++) {
            if (state.items[i].line <= line) { prev = state.items[i]; }
            else { next = state.items[i]; break; }
        }
        if (!prev) { return 0; }
        const prevTop = topOf(prev.el);
        if (!next) { return prevTop; }
        const nextTop = topOf(next.el);
        const span = next.line - prev.line;
        const ratio = span > 0 ? (line - prev.line) / span : 0;
        return prevTop + (nextTop - prevTop) * ratio;
    }

    function lineForOffset(y) {
        if (!state.items.length) { return 1; }
        let prev = null, next = null;
        for (let i = 0; i < state.items.length; i++) {
            const top = topOf(state.items[i].el);
            if (top <= y + 2) { prev = { item: state.items[i], top: top }; }
            else { next = { item: state.items[i], top: top }; break; }
        }
        if (!prev) { return 1; }
        if (!next) { return prev.item.line; }
        const span = next.top - prev.top;
        const ratio = span > 0 ? (y - prev.top) / span : 0;
        return prev.item.line + (next.item.line - prev.item.line) * ratio;
    }

    window.emdee = {
        setContent: function (html) {
            const article = document.querySelector('article');
            const y = window.scrollY;
            article.innerHTML = html;
            collect();
            window.scrollTo(0, y);
        },
        setCss: function (css) {
            document.getElementById('emdee-theme').textContent = css;
        },
        scrollToLine: function (line) {
            state.suppressUntil = Date.now() + 200;
            window.scrollTo(0, Math.max(0, offsetForLine(line) - 8));
        },
        currentLine: function () { return lineForOffset(window.scrollY); }
    };

    let pending = false;
    window.addEventListener('scroll', function () {
        if (pending || Date.now() < state.suppressUntil) { return; }
        pending = true;
        window.requestAnimationFrame(function () {
            pending = false;
            if (Date.now() < state.suppressUntil) { return; }
            if (window.emdeeBridge && window.emdeeBridge.preview_scrolled) {
                window.emdeeBridge.preview_scrolled(lineForOffset(window.scrollY));
            }
        });
    }, { passive: true });

    window.addEventListener('resize', collect, { passive: true });

    new QWebChannel(qt.webChannelTransport, function (channel) {
        window.emdeeBridge = channel.objects.emdeeBridge;
        state.ready = true;
        if (window.emdeeBridge.preview_ready) { window.emdeeBridge.preview_ready(); }
    });

    collect();
})();
"""

_SHELL_HEAD_SCRIPT = '<script src="qrc:///qtwebchannel/qwebchannel.js"></script>'


class _Bridge(QObject):
    """JavaScript → Python channel object."""

    scrolled = pyqtSignal(float)
    ready = pyqtSignal()

    @pyqtSlot(float)
    def preview_scrolled(self, line: float) -> None:
        self.scrolled.emit(line)

    @pyqtSlot()
    def preview_ready(self) -> None:
        self.ready.emit()


class _PreviewPage(QWebEnginePage):
    """Page that routes link clicks back to the application."""

    external_link = pyqtSignal(QUrl)
    document_link = pyqtSignal(Path)

    def acceptNavigationRequest(  # noqa: N802 - Qt API
        self, url: QUrl, nav_type: QWebEnginePage.NavigationType, is_main_frame: bool
    ) -> bool:
        """Nothing navigates this page except us.

        Links are intercepted and handed to the application; every other
        main-frame navigation — redirects, form posts, ``location =`` from
        script — is refused, so a document can never replace the preview with
        a page of its own choosing.
        """
        types = QWebEnginePage.NavigationType
        if not is_main_frame:
            return False

        if nav_type == types.NavigationTypeTyped:
            # This is our own setHtml() call bootstrapping the shell.
            return True

        if nav_type == types.NavigationTypeLinkClicked:
            if url.hasFragment() and url.path() in ("", self.url().path()):
                return True
            if url.isLocalFile():
                path = Path(url.toLocalFile())
                if path.suffix.lower() in MARKDOWN_SUFFIXES:
                    self.document_link.emit(path)
                    return False
            self.external_link.emit(url)
            return False

        log.warning("blocked %s navigation to %s", nav_type.name, url.toString())
        return False

    def javaScriptConsoleMessage(  # noqa: N802 - Qt API
        self,
        level: QWebEnginePage.JavaScriptConsoleMessageLevel,
        message: str,
        line: int,
        source: str,
    ) -> None:
        log.debug("preview console [%s:%s] %s", source, line, message)


class MarkdownPreview(QWebEngineView):
    """Live HTML preview with two-way scroll synchronisation."""

    #: Emitted with a 1-based source line when the user scrolls the preview.
    scrolled_to_line = pyqtSignal(float)
    #: Emitted when a link to another Markdown file is clicked.
    document_link_clicked = pyqtSignal(Path)

    def __init__(self, parent: QWidget | None = None, base_dir: Path | None = None) -> None:
        super().__init__(parent)
        self._page = _PreviewPage(self)
        self.setPage(self._page)

        self._bridge = _Bridge(self)
        self._channel = QWebChannel(self)
        self._channel.registerObject("emdeeBridge", self._bridge)
        self._page.setWebChannel(self._channel)

        self._bridge.scrolled.connect(self.scrolled_to_line.emit)
        self._bridge.ready.connect(self._on_ready)
        self._page.external_link.connect(self._open_external)
        self._page.document_link.connect(self.document_link_clicked.emit)

        self._harden(self._page.settings())

        self._css = ""
        self._nonce = ""
        self._shell_url = QUrl()
        # Set before the first shell load so startup needs a single setHtml.
        self._base_dir: Path | None = base_dir
        self._pending_html: str | None = None
        self._last_body: str | None = None
        self._loaded = False
        self._reload_queued = False

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(PREVIEW_DEBOUNCE_MS)

        self.setMinimumWidth(220)
        self._load_shell()

    # ------------------------------------------------------------ hardening
    @staticmethod
    def _harden(settings: QWebEngineSettings) -> None:
        """Switch off everything a *document viewer* has no business doing.

        The preview only ever shows local Markdown, so storage, plugins, media
        autoplay, clipboard access and remote loads from local content are all
        turned off explicitly rather than left at their defaults.
        """
        attr = QWebEngineSettings.WebAttribute
        enabled = {
            # Needed for local images referenced relatively by the document.
            attr.LocalContentCanAccessFileUrls: True,
            attr.ShowScrollBars: True,
            attr.PlaybackRequiresUserGesture: True,
        }
        disabled = {
            attr.LocalContentCanAccessRemoteUrls,
            attr.JavascriptCanOpenWindows,
            attr.JavascriptCanAccessClipboard,
            attr.JavascriptCanPaste,
            attr.AllowWindowActivationFromJavaScript,
            attr.AllowRunningInsecureContent,
            attr.FocusOnNavigationEnabled,
            attr.PdfViewerEnabled,
            attr.LocalStorageEnabled,
            attr.PluginsEnabled,
            attr.FullScreenSupportEnabled,
            attr.ScreenCaptureEnabled,
            attr.WebGLEnabled,
            attr.Accelerated2dCanvasEnabled,
            attr.AutoLoadIconsForPage,
            attr.TouchIconsEnabled,
            attr.DnsPrefetchEnabled,
            attr.ReadingFromCanvasEnabled,
        }
        for attribute, value in enabled.items():
            settings.setAttribute(attribute, value)
        for attribute in disabled:
            settings.setAttribute(attribute, False)

    # ------------------------------------------------------------ debounce
    @property
    def debounce(self) -> QTimer:
        """Timer the owner connects its re-render slot to."""
        return self._debounce

    def schedule_render(self) -> None:
        """(Re)start the debounce window."""
        self._debounce.start()

    def render_now(self) -> None:
        """Fire any pending debounced render immediately."""
        if self._debounce.isActive():
            self._debounce.stop()
            self._debounce.timeout.emit()

    # --------------------------------------------------------------- shell
    def _shell_html(self) -> str:
        # A fresh nonce per load: the shell's own script is allowed to run, and
        # nothing a document might smuggle in ever can.
        self._nonce = secrets.token_urlsafe(18)
        csp = (
            "default-src 'none'; "
            f"script-src 'nonce-{self._nonce}' qrc:; "
            "style-src 'unsafe-inline'; "
            "img-src 'self' file: data:; "
            "font-src 'self' file: data:; "
            "connect-src 'none'; "
            "object-src 'none'; "
            "frame-src 'none'; "
            "media-src 'none'; "
            "base-uri 'none'; "
            "form-action 'none'"
        )
        return page_builder.build_page(
            "",
            self._css,
            title="Preview",
            script=_SHELL_SCRIPT,
            script_nonce=self._nonce,
            csp=csp,
        ).replace("<style>", f'{_SHELL_HEAD_SCRIPT}\n<style id="emdee-theme">', 1)

    def _load_shell(self) -> None:
        """Queue a reload of the page shell, coalescing bursts into one load.

        Startup sets the theme CSS and the base directory back to back, and
        both fall through to a reload while the first one is still in flight.
        Handing Chromium a second ``setHtml`` before it has finished the first
        is enough to take the render process down, so the reloads are collapsed
        onto the next event-loop turn and only the last one is actually issued.
        """
        if self._reload_queued:
            return
        self._reload_queued = True
        QTimer.singleShot(0, self._reload_shell_now)

    def _reload_shell_now(self) -> None:
        self._reload_queued = False
        self._loaded = False
        self._pending_html = self._last_body
        base = QUrl.fromLocalFile(str(self._base_dir) + "/") if self._base_dir else QUrl()
        self._shell_url = base
        self._page.setHtml(self._shell_html(), base)

    def _on_ready(self) -> None:
        self._loaded = True
        if self._pending_html is not None:
            self._push_content(self._pending_html)
            self._pending_html = None

    def _run(self, script: str) -> None:
        self._page.runJavaScript(script)

    # -------------------------------------------------------------- content
    def set_base_dir(self, directory: Path | None) -> None:
        """Point relative image/link resolution at the document's folder."""
        if directory == self._base_dir:
            return
        self._base_dir = directory
        self._load_shell()

    def set_css(self, css: str) -> None:
        """Swap the stylesheet without re-rendering the content."""
        self._css = css
        if self._loaded:
            self._run(f"window.emdee.setCss({json.dumps(css)});")
        else:
            self._load_shell()

    def set_body(self, body_html: str) -> None:
        """Replace the rendered article, preserving the scroll position."""
        if not self._loaded:
            self._last_body = body_html
            self._pending_html = body_html
            return
        self._push_content(body_html)

    def _push_content(self, body_html: str) -> None:
        self._last_body = body_html
        self._run(f"window.emdee.setContent({json.dumps(body_html)});")

    def show_empty_state(self) -> None:
        self.set_body(page_builder.EMPTY_STATE_HTML)

    # -------------------------------------------------------------- scroll
    def scroll_to_line(self, line: float) -> None:
        if self._loaded:
            self._run(f"window.emdee.scrollToLine({float(line)});")

    # --------------------------------------------------------------- links
    @staticmethod
    def _open_external(url: QUrl) -> None:
        if url.scheme() in ("http", "https", "mailto"):
            QDesktopServices.openUrl(url)
        else:
            log.info("ignored navigation to %s", url.toString())
