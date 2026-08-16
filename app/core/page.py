# SPDX-License-Identifier: GPL-3.0-or-later
"""Assembly of complete HTML documents (live preview, export, print).

The same builder serves all three consumers so the exported file always looks
exactly like what the preview shows.
"""

from __future__ import annotations

import base64
import html
from pathlib import Path

__all__ = ["build_page", "svg_data_uri", "EMPTY_STATE_HTML"]

EMPTY_STATE_HTML = (
    '<div class="emdee-empty"><span class="glyph">◆</span>'
    "Nothing to preview yet — start typing on the left.</div>"
)


def svg_data_uri(svg_path: Path | str) -> str:
    """Return a ``data:`` URI for an SVG file, or an empty string on failure."""
    try:
        data = Path(svg_path).read_bytes()
    except OSError:
        return ""
    encoded = base64.b64encode(data).decode("ascii")
    return f"data:image/svg+xml;base64,{encoded}"


#: Content-Security-Policy for the exported file.  It carries no scripts of its
#: own, so everything executable is forbidden outright.
EXPORT_CSP = (
    "default-src 'none'; "
    "img-src data: https: http:; "
    "style-src 'unsafe-inline'; "
    "font-src data:; "
    "script-src 'none'; "
    "object-src 'none'; "
    "frame-src 'none'; "
    "base-uri 'none'; "
    "form-action 'none'"
)


def build_page(
    body_html: str,
    css: str,
    *,
    title: str = "Document",
    base_href: str | None = None,
    favicon: str = "",
    script: str = "",
    script_nonce: str = "",
    csp: str = "",
    generator: str = "Emdee",
) -> str:
    """Wrap rendered body HTML in a complete, self-contained document.

    ``csp`` is emitted as a ``Content-Security-Policy`` meta tag; combined with
    the HTML sanitiser it is the second layer keeping a hostile document from
    running code or phoning home.
    """
    head: list[str] = [
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
    ]
    if csp:
        # A CSP is full of single quotes ('none', 'self', 'nonce-…').  Escaping
        # them would still work — browsers decode entities before parsing the
        # policy — but it makes the output unreadable, so only the characters
        # that actually matter inside a double-quoted attribute are escaped.
        policy = html.escape(csp, quote=False).replace('"', "%22")
        head.append(f'<meta http-equiv="Content-Security-Policy" content="{policy}">')
    head.append(f'<meta name="generator" content="{html.escape(generator, quote=True)}">')
    if base_href:
        head.append(f'<base href="{html.escape(base_href, quote=True)}">')
    head.append(f"<title>{html.escape(title)}</title>")
    if favicon:
        head.append(f'<link rel="icon" href="{favicon}">')
    head.append(f"<style>\n{css}\n</style>")

    if script:
        nonce = f' nonce="{html.escape(script_nonce, quote=True)}"' if script_nonce else ""
        tail = f"<script{nonce}>\n{script}\n</script>"
    else:
        tail = ""

    return (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n<head>\n'
        + "\n".join(head)
        + "\n</head>\n<body>\n"
        + f'<article class="markdown-body">\n{body_html}\n</article>\n'
        + tail
        + "\n</body>\n</html>\n"
    )
