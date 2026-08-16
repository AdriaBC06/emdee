# SPDX-License-Identifier: GPL-3.0-or-later
"""The Markdown preview stylesheet, generated from the same palette tokens.

Exactly like :mod:`app.themes.qss_template`, this is a ``{token}`` template.
The rendered CSS is used in three places: the live preview, the standalone HTML
export and the PDF export (which adds the ``@page`` rules at the bottom).
"""

from __future__ import annotations

import re

from .palettes import Palette, print_variant
from .pygments_style import style_for

__all__ = ["build_preview_css", "PREVIEW_CSS_TEMPLATE"]

_PLACEHOLDER = re.compile(r"\{([a-z_][a-z0-9_]*)\}")

PREVIEW_CSS_TEMPLATE = """
:root {
    color-scheme: {color_scheme};
    --bg: {bg};
    --bg-alt: {bg_alt};
    --surface: {surface};
    --border: {border};
    --text: {text};
    --muted: {muted_on_bg};
    --accent: {accent_on_bg};
    --accent2: {accent2_on_bg};
    --green: {green_on_bg};
    --yellow: {yellow_on_bg};
    --red: {red_on_bg};
    --cyan: {cyan_on_bg};
}

* {
    box-sizing: border-box;
}

html {
    scroll-behavior: auto;
}

body {
    margin: 0;
    padding: 40px 48px 60vh 48px;
    background: {bg};
    color: {text};
    font-family: "Inter", "Noto Sans", "DejaVu Sans", -apple-system, sans-serif;
    font-size: {body_font_size}px;
    line-height: 1.7;
    -webkit-font-smoothing: antialiased;
    overflow-wrap: break-word;
    tab-size: 4;
}

.markdown-body {
    max-width: {content_width}px;
    margin: 0 auto;
}

/* ------------------------------------------------------------- headings */
h1, h2, h3, h4, h5, h6 {
    color: {text};
    font-weight: 700;
    line-height: 1.3;
    margin: 1.8em 0 0.6em;
    scroll-margin-top: 20px;
}

h1:first-child, h2:first-child, h3:first-child {
    margin-top: 0;
}

h1 {
    font-size: 2.05em;
    padding-bottom: 0.3em;
    border-bottom: 2px solid {accent};
}

h2 {
    font-size: 1.55em;
    padding-bottom: 0.25em;
    border-bottom: 1px solid {border};
}

h3 { font-size: 1.3em; color: {accent_on_bg}; }
h4 { font-size: 1.12em; }
h5 { font-size: 1em; }
h6 { font-size: 0.92em; color: {muted_on_bg}; }

.heading-anchor {
    color: {muted_on_bg};
    text-decoration: none;
    opacity: 0;
    margin-left: 0.35em;
    font-weight: 400;
    transition: opacity 120ms ease-in-out;
}

h1:hover .heading-anchor,
h2:hover .heading-anchor,
h3:hover .heading-anchor,
h4:hover .heading-anchor,
h5:hover .heading-anchor,
h6:hover .heading-anchor {
    opacity: 1;
}

/* ---------------------------------------------------------------- inline */
p {
    margin: 0 0 1.05em;
}

a {
    color: {accent_on_bg};
    text-decoration: none;
    border-bottom: 1px solid {accent_soft};
}

a:hover {
    color: {accent2_on_bg};
    border-bottom-color: {accent2_on_bg};
}

strong {
    color: {text};
    font-weight: 700;
}

em {
    color: {text};
}

del,
s {
    color: {muted_on_bg};
    text-decoration-color: {red_on_bg};
}

mark {
    background: {warning_soft};
    color: {text};
    border-radius: 3px;
    padding: 0 3px;
}

abbr {
    border-bottom: 1px dotted {muted_on_bg};
    cursor: help;
}

kbd {
    background: {surface};
    border: 1px solid {border};
    border-bottom-width: 2px;
    border-radius: 5px;
    padding: 1px 6px;
    font-family: {mono_stack};
    font-size: 0.85em;
}

hr {
    border: none;
    height: 1px;
    background: {border};
    margin: 2.2em 0;
}

/* ------------------------------------------------------------------ code */
code {
    font-family: {mono_stack};
    font-size: 0.88em;
}

:not(pre) > code {
    background: {code_bg};
    color: {inline_code_fg};
    border: 1px solid {code_border};
    border-radius: 5px;
    padding: 0.12em 0.38em;
    white-space: break-spaces;
}

pre {
    background: {code_bg};
    border: 1px solid {code_border};
    border-radius: 10px;
    padding: 14px 16px;
    overflow-x: auto;
    margin: 0 0 1.3em;
    line-height: 1.55;
}

pre code {
    background: none;
    border: none;
    padding: 0;
    font-size: 0.86em;
    color: {text};
}

.code-wrap {
    position: relative;
    margin: 0 0 1.3em;
}

.code-wrap pre {
    margin: 0;
}

.code-lang {
    position: absolute;
    top: 0;
    right: 0;
    font-family: {mono_stack};
    font-size: 0.68em;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: {muted_on_bg};
    background: {code_bg};
    border-left: 1px solid {code_border};
    border-bottom: 1px solid {code_border};
    border-radius: 0 10px 0 8px;
    padding: 3px 10px;
    pointer-events: none;
}

/* ----------------------------------------------------------- blockquote */
blockquote {
    margin: 0 0 1.3em;
    padding: 0.2em 1.1em;
    border-left: 4px solid {accent};
    background: {bg_alt};
    border-radius: 0 8px 8px 0;
    color: {muted_on_bg_alt};
}

blockquote > :last-child {
    margin-bottom: 0;
}

blockquote blockquote {
    border-left-color: {accent2};
}

/* ----------------------------------------------------------------- lists */
ul, ol {
    margin: 0 0 1.05em;
    padding-left: 1.6em;
}

li {
    margin: 0.28em 0;
}

li::marker {
    color: {accent_on_bg};
}

li > ul, li > ol {
    margin: 0.28em 0 0.28em;
}

.task-list-item {
    list-style: none;
    margin-left: -1.35em;
}

.task-list-item input[type="checkbox"] {
    margin: 0 0.55em 0 0;
    accent-color: {accent};
    width: 0.95em;
    height: 0.95em;
    vertical-align: -0.08em;
}

.task-list-item input[type="checkbox"]:checked + * {
    color: {muted_on_bg};
}

dl dt {
    font-weight: 700;
    color: {accent_on_bg};
    margin-top: 0.8em;
}

dl dd {
    margin: 0.2em 0 0.2em 1.4em;
}

/* ---------------------------------------------------------------- tables */
.table-wrap {
    overflow-x: auto;
    margin: 0 0 1.4em;
}

table {
    border-collapse: collapse;
    border-spacing: 0;
    width: 100%;
    font-size: 0.95em;
}

th, td {
    border: 1px solid {border};
    padding: 8px 12px;
    text-align: left;
}

thead th {
    background: {surface};
    color: {text_on_surface};
    font-weight: 700;
    white-space: nowrap;
}

tbody tr:nth-child(even) {
    background: {table_stripe};
}

/* ---------------------------------------------------------------- images */
img {
    max-width: 100%;
    height: auto;
    border-radius: 8px;
    background: {bg_alt};
}

img.emoji {
    display: inline;
    width: 1.1em;
    height: 1.1em;
    border-radius: 0;
    vertical-align: -0.15em;
    background: none;
}

figure {
    margin: 0 0 1.4em;
    text-align: center;
}

figcaption {
    color: {muted_on_bg};
    font-size: 0.88em;
    margin-top: 0.5em;
}

/* ------------------------------------------------------------- footnotes */
.footnotes-sep {
    display: none;
}

.footnotes {
    margin-top: 3em;
    padding-top: 1.2em;
    border-top: 1px solid {border};
    color: {muted_on_bg};
    font-size: 0.92em;
}

.footnotes-list {
    padding-left: 1.4em;
}

.footnote-item p {
    margin-bottom: 0.4em;
}

.footnote-ref a,
.footnote-backref {
    border-bottom: none;
    font-weight: 600;
}

.footnotes hr {
    display: none;
}

:target {
    background: {overlay};
    border-radius: 6px;
}

/* ------------------------------------------------------------ empty note */
.emdee-empty {
    color: {muted_on_bg};
    text-align: center;
    margin-top: 22vh;
    font-size: 1.05em;
}

.emdee-empty .glyph {
    display: block;
    font-size: 2.6em;
    margin-bottom: 0.35em;
    color: {accent_on_bg};
}

/* ------------------------------------------------------------ scrollbars */
::-webkit-scrollbar {
    width: 10px;
    height: 10px;
}

::-webkit-scrollbar-track {
    background: transparent;
}

::-webkit-scrollbar-thumb {
    background: {scrollbar};
    border-radius: 5px;
}

::-webkit-scrollbar-thumb:hover {
    background: {scrollbar_hover};
}

::selection {
    background: {selection_bg};
    color: {selection_fg};
}
"""

PRINT_CSS_TEMPLATE = """
/* Page margins come from the QPageLayout handed to printToPdf, so the CSS box
   must not add its own or the two would stack. */
@page {
    margin: 0;
}

body {
    padding: 0;
    font-size: 10.5pt;
}

.markdown-body {
    max-width: none;
}

a {
    border-bottom: none;
}

h1, h2, h3, h4, h5, h6 {
    break-after: avoid-page;
    page-break-after: avoid;
}

pre, blockquote, table, figure, img {
    break-inside: avoid-page;
    page-break-inside: avoid;
}

tr, li {
    break-inside: avoid-page;
}

.code-lang {
    display: none;
}

.heading-anchor {
    display: none;
}
"""


def _render(template: str, tokens: dict[str, str]) -> str:
    def _sub(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in tokens:
            raise KeyError(f"unknown theme token in CSS template: {key!r}")
        return tokens[key]

    return _PLACEHOLDER.sub(_sub, template)


def build_preview_css(
    palette: Palette,
    *,
    body_font_size: int = 16,
    content_width: int = 820,
    for_print: bool = False,
) -> str:
    """Render the preview stylesheet, including the Pygments code theme.

    For ``for_print`` the whole stylesheet is regenerated from a paper variant
    of the palette rather than patched with overrides — that way *every* rule,
    including the Pygments one, lands on white consistently.
    """
    from pygments.formatters import HtmlFormatter

    if for_print:
        palette = print_variant(palette)

    tokens = palette.tokens()
    tokens["body_font_size"] = str(body_font_size)
    tokens["content_width"] = str(content_width)
    tokens["mono_stack"] = (
        '"JetBrains Mono", "Fira Code", "Cascadia Code", '
        '"DejaVu Sans Mono", "Liberation Mono", monospace'
    )

    css = _render(PREVIEW_CSS_TEMPLATE, tokens)
    code_css = HtmlFormatter(style=style_for(palette)).get_style_defs(".highlight")
    parts = [css, "\n/* ---------------------------------- pygments */\n", code_css]
    if for_print:
        parts.append("\n/* -------------------------------------- print */\n")
        parts.append(_render(PRINT_CSS_TEMPLATE, tokens))
    return "".join(parts)
