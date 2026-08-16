# SPDX-License-Identifier: GPL-3.0-or-later
"""Markdown → HTML rendering.

CommonMark plus the GitHub-flavoured extensions that matter for notes: tables,
task lists, strikethrough, footnotes, definition lists and YAML front matter.
Fenced code blocks are highlighted with Pygments using a style generated from
the active theme.

Every block-level element carries a ``data-source-line`` attribute; that is what
makes the two-way scroll synchronisation between editor and preview accurate
instead of a crude percentage mapping.
"""

from __future__ import annotations

import base64
import html
import logging
import mimetypes
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from markdown_it import MarkdownIt
from markdown_it.renderer import RendererHTML
from markdown_it.token import Token
from markdown_it.utils import OptionsDict
from mdit_py_plugins.deflist import deflist_plugin
from mdit_py_plugins.footnote import footnote_plugin
from mdit_py_plugins.front_matter import front_matter_plugin
from mdit_py_plugins.tasklists import tasklists_plugin
from pygments import highlight as pygments_highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import get_lexer_by_name, guess_lexer
from pygments.util import ClassNotFound

from ..themes.palettes import DEFAULT_THEME, Palette, get_palette
from ..themes.pygments_style import style_for
from .sanitize import sanitize_html

log = logging.getLogger(__name__)

__all__ = ["MarkdownRenderer", "Heading", "RenderResult", "slugify"]

#: Images larger than this are linked rather than inlined when exporting.
MAX_INLINE_IMAGE_BYTES = 4 * 1024 * 1024

_SLUG_STRIP = re.compile(r"[^\w\- ]+", re.UNICODE)


def slugify(text: str) -> str:
    """GitHub-ish heading slug: lowercase, spaces to dashes, punctuation gone."""
    value = unicodedata.normalize("NFKD", text).strip().lower()
    value = _SLUG_STRIP.sub("", value)
    value = re.sub(r"[\s]+", "-", value)
    return value or "section"


@dataclass(frozen=True)
class Heading:
    """One entry of the document outline."""

    level: int
    text: str
    slug: str
    line: int


@dataclass
class RenderResult:
    """Rendered body HTML plus the metadata the UI needs."""

    html: str
    headings: list[Heading] = field(default_factory=list)
    front_matter: str | None = None


class _EmdeeRenderer(RendererHTML):
    """HTML renderer that annotates blocks with their source line."""

    def renderToken(  # noqa: N802 - markdown-it API
        self,
        tokens: Sequence[Token],
        idx: int,
        options: OptionsDict,
        env: dict[str, Any],
    ) -> str:
        token = tokens[idx]
        if token.block and token.nesting >= 0 and token.map:
            token.attrSet("data-source-line", str(token.map[0] + 1))
        return super().renderToken(tokens, idx, options, env)


class MarkdownRenderer:
    """Reusable Markdown renderer bound to a theme.

    The instance is cheap to keep around; changing the theme only rebuilds the
    Pygments formatter.
    """

    def __init__(self, theme: str = DEFAULT_THEME) -> None:
        self._theme = theme
        self._formatter = self._make_formatter(theme)
        self._md = self._build_parser()

    # ------------------------------------------------------------- plumbing
    @staticmethod
    def _make_formatter(theme: str) -> HtmlFormatter:
        return HtmlFormatter(
            style=style_for(get_palette(theme)), nowrap=False, cssclass="highlight"
        )

    def set_theme(self, theme: str) -> None:
        """Switch the code-highlighting theme in place."""
        if theme != self._theme:
            self._theme = theme
            self._formatter = self._make_formatter(theme)

    @property
    def theme(self) -> str:
        return self._theme

    @property
    def palette(self) -> Palette:
        return get_palette(self._theme)

    def _build_parser(self) -> MarkdownIt:
        md = MarkdownIt(
            "commonmark",
            {
                "html": True,
                "linkify": True,
                "typographer": False,
                "breaks": False,
                "highlight": self._highlight,
            },
            renderer_cls=_EmdeeRenderer,
        )
        md.enable(["table", "strikethrough", "linkify"])
        md.use(footnote_plugin)
        md.use(tasklists_plugin, enabled=True)
        md.use(deflist_plugin)
        md.use(front_matter_plugin)

        rules = md.renderer.rules
        rules["heading_open"] = self._heading_open
        rules["heading_close"] = self._heading_close
        rules["table_open"] = self._table_open
        rules["table_close"] = self._table_close
        rules["front_matter"] = self._front_matter
        return md

    # -------------------------------------------------------- custom rules
    def _heading_open(
        self,
        tokens: Sequence[Token],
        idx: int,
        options: OptionsDict,
        env: dict[str, Any],
    ) -> str:
        token = tokens[idx]
        inline = tokens[idx + 1] if idx + 1 < len(tokens) else None
        title = inline.content if inline is not None else ""
        slug = self._unique_slug(slugify(title), env)
        token.attrSet("id", slug)
        env.setdefault("headings", []).append(
            Heading(
                level=int(token.tag[1]),
                text=title,
                slug=slug,
                line=(token.map[0] + 1) if token.map else 0,
            )
        )
        env["current_slug"] = slug
        if token.map:
            token.attrSet("data-source-line", str(token.map[0] + 1))
        attrs = self._md.renderer.renderAttrs(token)
        return f"<{token.tag}{attrs}>"

    def _heading_close(
        self,
        tokens: Sequence[Token],
        idx: int,
        options: OptionsDict,
        env: dict[str, Any],
    ) -> str:
        slug = env.get("current_slug", "")
        anchor = (
            f'<a class="heading-anchor" href="#{html.escape(slug, quote=True)}" '
            f'aria-hidden="true">#</a>'
        )
        return f"{anchor}</{tokens[idx].tag}>\n"

    @staticmethod
    def _unique_slug(slug: str, env: dict[str, Any]) -> str:
        used: dict[str, int] = env.setdefault("slugs", {})
        if slug not in used:
            used[slug] = 0
            return slug
        used[slug] += 1
        return f"{slug}-{used[slug]}"

    def _table_open(
        self,
        tokens: Sequence[Token],
        idx: int,
        options: OptionsDict,
        env: dict[str, Any],
    ) -> str:
        line = ""
        if tokens[idx].map:
            line = f' data-source-line="{tokens[idx].map[0] + 1}"'
        return f'<div class="table-wrap"{line}>\n<table>'

    def _table_close(
        self,
        tokens: Sequence[Token],
        idx: int,
        options: OptionsDict,
        env: dict[str, Any],
    ) -> str:
        return "</table>\n</div>\n"

    def _front_matter(
        self,
        tokens: Sequence[Token],
        idx: int,
        options: OptionsDict,
        env: dict[str, Any],
    ) -> str:
        env["front_matter"] = tokens[idx].content
        return ""

    # ---------------------------------------------------------- highlighting
    def _highlight(self, code: str, lang: str, attrs: str) -> str:
        label = (lang or "").strip()
        try:
            lexer = get_lexer_by_name(label) if label else guess_lexer(code)
        except (ClassNotFound, ValueError):
            lexer = None

        if lexer is None:
            body = f'<pre class="highlight"><code>{html.escape(code)}</code></pre>'
        else:
            body = pygments_highlight(code, lexer, self._formatter)

        tag = (
            f'<span class="code-lang">{html.escape(label)}</span>' if label else ""
        )
        return f'<div class="code-wrap">{tag}{body}</div>'

    # -------------------------------------------------------------- public
    def render(self, text: str) -> RenderResult:
        """Render Markdown source to sanitised body HTML plus outline metadata.

        CommonMark allows raw HTML, and the preview is a real browser engine —
        so the output always goes through :func:`~app.core.sanitize.sanitize_html`
        before anyone sees it.  Doing it here rather than in the widget means
        the HTML export is protected by the same pass.
        """
        env: dict[str, Any] = {}
        try:
            body = sanitize_html(self._md.render(text, env))
        except Exception:  # pragma: no cover - defensive, keeps the UI alive
            log.exception("markdown rendering failed")
            body = (
                '<div class="emdee-empty"><span class="glyph">!</span>'
                "Could not render this document.</div>"
            )
        return RenderResult(
            html=body,
            headings=list(env.get("headings", [])),
            front_matter=env.get("front_matter"),
        )

    def render_html(self, text: str) -> str:
        """Convenience wrapper returning only the body HTML."""
        return self.render(text).html

    def outline(self, text: str) -> list[Heading]:
        """Return just the heading outline of a document."""
        return self.render(text).headings


def inline_local_images(body_html: str, base_dir: Path | None) -> str:
    """Replace ``src`` attributes pointing at local files with data URIs.

    Used by the standalone HTML export so the resulting file has no external
    dependencies at all.  Remote URLs and oversized files are left untouched.

    Only files *inside* ``base_dir`` are embedded.  Without that check a crafted
    document containing ``<img src="../../.ssh/id_rsa">`` would quietly bake an
    unrelated private file into an HTML export the user then shares.
    """
    if base_dir is None:
        return body_html

    try:
        root = base_dir.resolve(strict=True)
    except OSError:
        return body_html

    pattern = re.compile(r'(<img\b[^>]*?\bsrc=")([^"]+)(")', re.IGNORECASE)

    def _replace(match: re.Match[str]) -> str:
        src = match.group(2)
        if re.match(r"^[a-z][a-z0-9+.-]*:", src, re.IGNORECASE) or src.startswith("//"):
            return match.group(0)
        try:
            candidate = (root / src).resolve()
        except OSError:
            return match.group(0)
        # Containment check, after resolving symlinks and "..".
        if not candidate.is_relative_to(root):
            log.warning("refusing to inline %s: outside the document folder", candidate)
            return match.group(0)
        try:
            if not candidate.is_file() or candidate.stat().st_size > MAX_INLINE_IMAGE_BYTES:
                return match.group(0)
            data = candidate.read_bytes()
        except OSError:
            return match.group(0)
        mime = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        encoded = base64.b64encode(data).decode("ascii")
        return f"{match.group(1)}data:{mime};base64,{encoded}{match.group(3)}"

    return pattern.sub(_replace, body_html)
