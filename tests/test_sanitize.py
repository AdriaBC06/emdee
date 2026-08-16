# SPDX-License-Identifier: GPL-3.0-or-later
"""Security tests for the HTML sanitiser.

A Markdown file is untrusted input: it can be downloaded, cloned or emailed.
Because the preview is a real browser engine and the HTML export gets shared
onwards, raw HTML inside a document must never be able to execute.
"""

from __future__ import annotations

import pytest

from app.core.renderer import MarkdownRenderer
from app.core.sanitize import sanitize_html


@pytest.fixture(scope="module")
def renderer() -> MarkdownRenderer:
    return MarkdownRenderer("dracula")


# --------------------------------------------------------------- scripting
def test_script_tags_are_removed_with_their_contents() -> None:
    out = sanitize_html("<p>before</p><script>alert(1)</script><p>after</p>")
    assert "script" not in out.lower()
    assert "alert(1)" not in out
    assert "before" in out and "after" in out


def test_style_tags_are_removed_with_their_contents() -> None:
    out = sanitize_html("<style>body{display:none}</style>text")
    assert "display:none" not in out
    assert "text" in out


@pytest.mark.parametrize(
    "payload",
    [
        '<img src=x onerror="alert(1)">',
        '<div onclick="alert(1)">x</div>',
        '<body onload="alert(1)">x</body>',
        '<p ONMOUSEOVER="alert(1)">x</p>',
        '<a href="#" onfocus=alert(1) autofocus>x</a>',
    ],
)
def test_every_event_handler_is_stripped(payload: str) -> None:
    out = sanitize_html(payload).lower()
    assert "alert(1)" not in out
    assert "onerror" not in out and "onclick" not in out
    assert "onload" not in out and "onmouseover" not in out and "onfocus" not in out


@pytest.mark.parametrize(
    "href",
    [
        "javascript:alert(1)",
        "JaVaScRiPt:alert(1)",
        "  javascript:alert(1)",
        "java\tscript:alert(1)",
        "vbscript:msgbox(1)",
        "data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==",
    ],
)
def test_dangerous_url_schemes_are_dropped(href: str) -> None:
    out = sanitize_html(f'<a href="{href}">click</a>')
    assert "javascript" not in out.lower()
    assert "vbscript" not in out.lower()
    assert "text/html" not in out.lower()
    assert ">click</a>" in out  # the text survives, the link does not


@pytest.mark.parametrize(
    "tag",
    ["iframe", "frame", "object", "embed", "form", "base", "meta", "link", "applet"],
)
def test_dangerous_tags_are_dropped(tag: str) -> None:
    out = sanitize_html(f'<{tag} src="https://evil.example">inner</{tag}>')
    assert f"<{tag}" not in out.lower()


def test_html_comments_are_dropped() -> None:
    assert sanitize_html("a<!-- [if IE]><script>x</script><![endif] -->b") == "ab"


def test_svg_is_dropped_entirely() -> None:
    out = sanitize_html('<svg><script>alert(1)</script></svg>text')
    assert "svg" not in out.lower()
    assert "alert(1)" not in out


# ------------------------------------------------------------------- URLs
def test_ordinary_links_survive_and_gain_noopener() -> None:
    out = sanitize_html('<a href="https://example.com">x</a>')
    assert 'href="https://example.com"' in out
    assert 'rel="noopener noreferrer"' in out


def test_relative_and_fragment_urls_survive() -> None:
    assert 'href="./other.md"' in sanitize_html('<a href="./other.md">x</a>')
    assert 'href="#section"' in sanitize_html('<a href="#section">x</a>')
    assert 'src="assets/pic.png"' in sanitize_html('<img src="assets/pic.png">')


def test_data_urls_are_allowed_only_for_real_images() -> None:
    good = '<img src="data:image/png;base64,iVBORw0KGgo=">'
    assert "data:image/png" in sanitize_html(good)
    bad = '<img src="data:text/html;base64,PHN2Zz4=">'
    assert "text/html" not in sanitize_html(bad)


# ----------------------------------------------------------------- styles
def _cell(style: str) -> str:
    """A table cell in valid document context.

    An orphan ``<td>`` is discarded by the html5 parsing algorithm exactly as a
    browser would discard it, so alignment has to be exercised inside a real
    table.
    """
    return f'<table><tbody><tr><td style="{style}">x</td></tr></tbody></table>'


def test_only_table_alignment_survives_as_inline_style() -> None:
    assert 'style="text-align:center"' in sanitize_html(_cell("text-align:center"))
    assert "javascript" not in sanitize_html(_cell("background:url(javascript:alert(1))")).lower()
    assert "position" not in sanitize_html(_cell("text-align:left;position:fixed"))
    assert "text-align:left" in sanitize_html(_cell("text-align:left;position:fixed"))
    assert "style=" not in sanitize_html('<p style="position:fixed;top:0">x</p>')


def test_orphan_table_cells_are_discarded_like_a_browser_would() -> None:
    """Parsing follows the html5 spec, so stray table markup cannot smuggle attributes."""
    assert sanitize_html('<td style="text-align:center">x</td>') == "x"


# --------------------------------------------------- our own markup survives
def test_generated_markup_is_preserved(renderer: MarkdownRenderer) -> None:
    html = renderer.render_html(
        "# Title\n\n"
        "- [x] done\n\n"
        "| a | b |\n| :- | -: |\n| 1 | 2 |\n\n"
        "```python\nprint(1)\n```\n"
    )
    assert 'data-source-line="1"' in html          # scroll sync
    assert 'id="title"' in html                    # heading anchors
    assert 'class="heading-anchor"' in html
    assert 'aria-hidden="true"' in html
    assert 'type="checkbox"' in html               # task lists
    assert 'class="highlight"' in html             # Pygments
    assert "<span" in html                         # Pygments token spans
    assert "text-align:right" in html.replace(" ", "")  # table alignment


def test_safe_inline_html_still_works(renderer: MarkdownRenderer) -> None:
    html = renderer.render_html(
        "<kbd>Ctrl</kbd> and <mark>marked</mark>\n\n"
        '<figure><img src="a.png" alt="a"><figcaption>cap</figcaption></figure>\n'
    )
    for fragment in ("<kbd>", "<mark>", "<figure>", "<figcaption>", "<img"):
        assert fragment in html


# ------------------------------------------------- end-to-end through render
def test_renderer_output_is_sanitised(renderer: MarkdownRenderer) -> None:
    hostile = (
        "# Innocent\n\n"
        "<script>fetch('file:///etc/passwd')</script>\n\n"
        '<img src=x onerror="fetch(\'https://evil.example\')">\n\n'
        '<a href="javascript:alert(1)">link</a>\n\n'
        '<iframe src="https://evil.example"></iframe>\n'
    )
    html = renderer.render_html(hostile)
    lowered = html.lower()
    assert "<script" not in lowered
    assert "onerror" not in lowered
    assert "javascript:" not in lowered
    assert "<iframe" not in lowered
    assert "evil.example" not in lowered
    assert "Innocent" in html


def test_text_content_of_dropped_tags_is_kept() -> None:
    assert "keep me" in sanitize_html("<marquee>keep me</marquee>")


def test_sanitising_is_idempotent(renderer: MarkdownRenderer) -> None:
    once = renderer.render_html("<b>x</b> <script>y</script>")
    assert sanitize_html(once) == once
