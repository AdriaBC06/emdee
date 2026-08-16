# SPDX-License-Identifier: GPL-3.0-or-later
"""Allow-list sanitiser for the HTML produced from Markdown.

Markdown is a *document* format, but CommonMark lets raw HTML through, and
Emdee renders that HTML inside a real browser engine.  Without this module a
``.md`` file downloaded from anywhere could run arbitrary JavaScript in the
preview — and, worse, the exported HTML would carry that script to whoever the
document is shared with.

The actual sanitising is delegated to `nh3 <https://nh3.readthedocs.io>`_, the
Python binding for Mozilla's `ammonia` (Rust), which parses with the same
html5ever engine browsers use.  Hand-rolled sanitisers built on ``HTMLParser``
are a well-known source of bypasses: the parser's idea of where a tag ends
rarely matches the browser's, and that gap *is* the vulnerability.  This module
therefore contributes only the *policy*, never the parsing.
"""

from __future__ import annotations

import re

import nh3

__all__ = ["sanitize_html", "ALLOWED_TAGS", "ALLOWED_URL_SCHEMES"]

#: Tags that may appear in the output at all.
ALLOWED_TAGS: frozenset[str] = frozenset(
    {
        "a", "abbr", "article", "b", "blockquote", "br", "caption", "cite",
        "code", "col", "colgroup", "dd", "del", "details", "div", "dl", "dt",
        "em", "figcaption", "figure", "h1", "h2", "h3", "h4", "h5", "h6", "hr",
        "i", "img", "input", "ins", "kbd", "li", "mark", "ol", "p", "pre",
        "q", "s", "samp", "section", "small", "span", "strong", "sub",
        "summary", "sup", "table", "tbody", "td", "tfoot", "th", "thead",
        "tr", "u", "ul", "var", "wbr",
    }
)

#: Tags dropped together with everything inside them.  ``svg`` and ``math``
#: matter because they switch the parser into a foreign content mode where the
#: usual escaping rules stop applying.
CLEAN_CONTENT_TAGS: frozenset[str] = frozenset(
    {"script", "style", "template", "svg", "math", "iframe", "object", "embed"}
)

#: Attributes accepted on any allowed tag.  ``data-source-line`` is what the
#: editor↔preview scroll synchronisation is anchored to; the ARIA attributes
#: are inert markup that keeps the generated heading anchors accessible.
_GLOBAL_ATTRS: frozenset[str] = frozenset(
    {
        "class", "id", "title", "dir", "lang",
        "aria-hidden", "aria-label", "role",
        "data-source-line",
    }
)

#: Extra attributes accepted per tag.
_TAG_ATTRS: dict[str, frozenset[str]] = {
    # ``rel`` is deliberately absent: nh3 manages it itself via ``link_rel``
    # and refuses to run if the policy also claims it.
    "a": frozenset({"href", "name", "target"}),
    "img": frozenset({"src", "alt", "width", "height", "loading", "decoding"}),
    "input": frozenset({"type", "checked", "disabled"}),
    "ol": frozenset({"start", "type"}),
    "li": frozenset({"value"}),
    "td": frozenset({"colspan", "rowspan", "style"}),
    "th": frozenset({"colspan", "rowspan", "scope", "style"}),
    "col": frozenset({"span", "style"}),
    "details": frozenset({"open"}),
    "abbr": frozenset({"title"}),
    "del": frozenset({"datetime"}),
    "ins": frozenset({"datetime"}),
}

#: URL schemes permitted in ``href`` / ``src``.
ALLOWED_URL_SCHEMES: frozenset[str] = frozenset(
    {"http", "https", "mailto", "file", "data"}
)

#: The only CSS properties an inline ``style`` may carry.  Markdown can produce
#: exactly one of these — table column alignment — and nothing else.
ALLOWED_STYLE_PROPERTIES: frozenset[str] = frozenset({"text-align"})

#: ``data:`` is in the scheme list only so that embedded images survive the
#: HTML export.  Everything else it can carry (``text/html``, ``image/svg+xml``
#: with a script inside) has to be refused.
_SAFE_DATA_URL = re.compile(r"^data:image/(png|jpe?g|gif|webp|avif);base64,[A-Za-z0-9+/=\s]*$", re.I)

_ATTRIBUTES: dict[str, set[str]] = {
    "*": set(_GLOBAL_ATTRS),
    **{tag: set(attrs) for tag, attrs in _TAG_ATTRS.items()},
}


def _attribute_filter(tag: str, attribute: str, value: str) -> str | None:
    """Narrow the two things nh3's declarative policy cannot express.

    Returning ``None`` drops the attribute; returning the value keeps it.
    """
    is_data_url = attribute in ("href", "src") and value.lstrip().lower().startswith("data:")
    # Only <img> may carry a data: URL, and only a real raster image — an
    # image/svg+xml payload can contain a <script>.
    if is_data_url and (tag != "img" or not _SAFE_DATA_URL.match(value.strip())):
        return None
    return value


def sanitize_html(html: str) -> str:
    """Return ``html`` with every scripting vector removed.

    Dropped: ``<script>``/``<style>``/``<svg>``/``<math>``/frames/objects and
    their contents, every ``on*`` handler, ``javascript:`` and other unlisted
    URL schemes, ``data:`` URLs that are not raster images, forms, ``<base>``,
    ``<meta>``, ``<link>``, comments, and any inline style other than table
    alignment.  Text content is preserved even when its wrapping tag is not on
    the allow-list, and external links gain ``rel="noopener noreferrer"``.
    """
    return nh3.clean(
        html,
        tags=set(ALLOWED_TAGS),
        clean_content_tags=set(CLEAN_CONTENT_TAGS),
        attributes=_ATTRIBUTES,
        attribute_filter=_attribute_filter,
        url_schemes=set(ALLOWED_URL_SCHEMES),
        filter_style_properties=set(ALLOWED_STYLE_PROPERTIES),
        strip_comments=True,
        link_rel="noopener noreferrer",
    )
