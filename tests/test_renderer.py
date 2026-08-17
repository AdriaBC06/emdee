# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for Markdown → HTML rendering (no Qt involved)."""

from __future__ import annotations

import base64
from pathlib import Path

import pytest

from app.core.page import build_page, svg_data_uri
from app.core.renderer import MarkdownRenderer, inline_local_images, slugify
from app.themes.palettes import PALETTES


@pytest.fixture(scope="module")
def renderer() -> MarkdownRenderer:
    return MarkdownRenderer("dracula")


# ----------------------------------------------------------------- commonmark
def test_headings_get_ids_and_anchors(renderer: MarkdownRenderer) -> None:
    html = renderer.render_html("# Hello World")
    assert '<h1 id="hello-world"' in html
    assert 'class="heading-anchor" href="#hello-world"' in html


def test_duplicate_headings_get_unique_slugs(renderer: MarkdownRenderer) -> None:
    html = renderer.render_html("# Notes\n\n# Notes\n\n# Notes")
    assert 'id="notes"' in html
    assert 'id="notes-1"' in html
    assert 'id="notes-2"' in html


def test_outline_reports_levels_and_lines(renderer: MarkdownRenderer) -> None:
    headings = renderer.outline("# One\n\ntext\n\n## Two")
    assert [(h.level, h.text, h.line) for h in headings] == [(1, "One", 1), (2, "Two", 5)]


def test_every_block_carries_its_source_line(renderer: MarkdownRenderer) -> None:
    html = renderer.render_html("first\n\nsecond\n\nthird")
    assert 'data-source-line="1"' in html
    assert 'data-source-line="3"' in html
    assert 'data-source-line="5"' in html


# ------------------------------------------------------------------- gfm
def test_tables_render_and_are_wrapped_for_overflow(renderer: MarkdownRenderer) -> None:
    html = renderer.render_html("| a | b |\n| - | - |\n| 1 | 2 |")
    assert '<div class="table-wrap"' in html
    assert "<thead" in html and "<th>a</th>" in html and "<td>1</td>" in html


def test_task_lists_produce_checkboxes(renderer: MarkdownRenderer) -> None:
    html = renderer.render_html("- [x] done\n- [ ] todo")
    assert html.count('type="checkbox"') == 2
    assert 'checked="checked"' in html
    assert "task-list-item" in html


def test_strikethrough(renderer: MarkdownRenderer) -> None:
    assert "<s>gone</s>" in renderer.render_html("~~gone~~")


def test_footnotes_render_a_section_with_a_backref(renderer: MarkdownRenderer) -> None:
    html = renderer.render_html("text[^a]\n\n[^a]: the note")
    assert 'class="footnotes"' in html
    assert 'class="footnote-backref"' in html
    assert "the note" in html


def test_definition_lists(renderer: MarkdownRenderer) -> None:
    html = renderer.render_html("Term\n: Definition")
    assert "<dt" in html and "<dd" in html


def test_front_matter_is_extracted_and_not_rendered(renderer: MarkdownRenderer) -> None:
    result = renderer.render("---\ntitle: Hi\n---\n\n# Body")
    assert result.front_matter is not None
    assert "title: Hi" in result.front_matter
    assert "title: Hi" not in result.html


def test_bare_urls_are_linkified(renderer: MarkdownRenderer) -> None:
    assert 'href="https://example.com"' in renderer.render_html("see https://example.com")


# ------------------------------------------------------------------ code
def test_fenced_code_is_highlighted_with_pygments(renderer: MarkdownRenderer) -> None:
    html = renderer.render_html("```python\ndef f():\n    return 1\n```")
    assert 'class="highlight"' in html
    assert 'class="code-lang">python<' in html
    assert "<span" in html  # Pygments emitted token spans


def test_unknown_language_still_produces_a_code_block(renderer: MarkdownRenderer) -> None:
    html = renderer.render_html("```notalanguage\nplain\n```")
    assert "<pre" in html and "plain" in html


def test_code_content_is_escaped(renderer: MarkdownRenderer) -> None:
    html = renderer.render_html("```\n<script>alert(1)</script>\n```")
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


# ---------------------------------------------------------------- theming
def test_switching_theme_changes_the_generated_pygments_css() -> None:
    from app.themes.preview_css import build_preview_css

    dark = build_preview_css(PALETTES["dracula"])
    light = build_preview_css(PALETTES["clean-light"])
    assert dark != light
    assert "#282a36" in dark
    assert "#ffffff" in light


def test_renderer_theme_switch_is_idempotent(renderer: MarkdownRenderer) -> None:
    before = renderer.theme
    renderer.set_theme("nord")
    assert renderer.theme == "nord"
    renderer.set_theme(before)
    assert renderer.theme == before


# ------------------------------------------------------------------ slugs
@pytest.mark.parametrize(
    ("text", "slug"),
    [
        ("Hello World", "hello-world"),
        ("A/B testing!", "ab-testing"),
        ("  spaced  out  ", "spaced-out"),
        ("", "section"),
        ("!!!", "section"),
    ],
)
def test_slugify(text: str, slug: str) -> None:
    assert slugify(text) == slug


# ----------------------------------------------------------------- export
def test_inline_local_images_embeds_a_data_uri(tmp_path: Path) -> None:
    image = tmp_path / "pic.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\nfake")
    html = '<p><img src="pic.png" alt="x"></p>'
    out = inline_local_images(html, tmp_path)
    assert "data:image/png;base64," in out
    payload = out.split("base64,")[1].split('"')[0]
    assert base64.b64decode(payload) == b"\x89PNG\r\n\x1a\nfake"


def test_inline_local_images_leaves_remote_urls_alone(tmp_path: Path) -> None:
    html = '<img src="https://example.com/a.png">'
    assert inline_local_images(html, tmp_path) == html


def test_inline_local_images_survives_a_missing_file(tmp_path: Path) -> None:
    html = '<img src="nope.png">'
    assert inline_local_images(html, tmp_path) == html


def test_build_page_is_self_contained(renderer: MarkdownRenderer) -> None:
    from app.themes.preview_css import build_preview_css

    body = renderer.render_html("# Title")
    page = build_page(
        body,
        build_preview_css(PALETTES["dracula"]),
        title="Doc",
        favicon="data:image/svg+xml;base64,AAA",
    )
    assert page.startswith("<!DOCTYPE html>")
    assert "<style>" in page and "</style>" in page
    assert "<title>Doc</title>" in page
    assert 'rel="icon" href="data:image/svg+xml' in page
    # No external requests of any kind.
    assert "http://" not in page and "https://" not in page
    assert "<script src=" not in page


def test_svg_data_uri_round_trips(tmp_path: Path) -> None:
    svg = tmp_path / "a.svg"
    svg.write_text("<svg/>", encoding="utf-8")
    uri = svg_data_uri(svg)
    assert uri.startswith("data:image/svg+xml;base64,")
    assert base64.b64decode(uri.split(",", 1)[1]) == b"<svg/>"
    assert svg_data_uri(tmp_path / "missing.svg") == ""


def test_malformed_markdown_does_not_raise(renderer: MarkdownRenderer) -> None:
    nasty = "| broken |\n|---\n\n```\nunclosed\n\n> [link](" + "(" * 200
    assert isinstance(renderer.render_html(nasty), str)


# --------------------------------------------------- export path containment
def test_inline_local_images_refuses_files_outside_the_document_folder(
    tmp_path: Path,
) -> None:
    """A crafted document must not be able to bake unrelated files into an export."""
    secret = tmp_path / "secret.png"
    secret.write_bytes(b"\x89PNG\r\n\x1a\ntop-secret")
    docs = tmp_path / "docs"
    docs.mkdir()

    html = '<img src="../secret.png">'
    out = inline_local_images(html, docs)
    assert out == html
    assert "top-secret" not in out
    assert "base64" not in out


def test_inline_local_images_refuses_symlinks_escaping_the_folder(tmp_path: Path) -> None:
    secret = tmp_path / "secret.png"
    secret.write_bytes(b"\x89PNG\r\n\x1a\ntop-secret")
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "innocent.png").symlink_to(secret)

    out = inline_local_images('<img src="innocent.png">', docs)
    assert "base64" not in out


def test_build_page_emits_the_requested_csp() -> None:
    from app.core.page import EXPORT_CSP, build_page

    page = build_page("<p>x</p>", "body{}", csp=EXPORT_CSP)
    assert 'http-equiv="Content-Security-Policy"' in page
    assert "script-src 'none'" in page
    assert "object-src 'none'" in page
    assert "base-uri 'none'" in page


# ---------------------------------------------------------------- link schemes
def test_file_urls_render_as_images(renderer: MarkdownRenderer) -> None:
    """``file:`` is what a local document links its own pictures with."""
    html = renderer.render_html("![a](file:///tmp/pic.png)")
    assert '<img src="file:///tmp/pic.png" alt="a">' in html


def test_file_urls_render_as_links(renderer: MarkdownRenderer) -> None:
    html = renderer.render_html("[doc](file:///tmp/other.md)")
    assert 'href="file:///tmp/other.md"' in html


@pytest.mark.parametrize(
    "source",
    [
        "[x](javascript:alert(1))",
        "[x](JaVaScRiPt:alert(1))",
        "[x](  javascript:alert(1))",
        "![x](vbscript:msgbox(1))",
        "![x](data:text/html;base64,PHNjcmlwdD4=)",
    ],
)
def test_dangerous_schemes_are_still_refused(
    renderer: MarkdownRenderer, source: str
) -> None:
    html = renderer.render_html(source).lower()
    # The destination is left as literal text, so look for it in an attribute
    # rather than anywhere in the output.
    for scheme in ("javascript:", "vbscript:", "data:text/html"):
        assert f'href="{scheme}' not in html
        assert f'src="{scheme}' not in html


def test_data_image_urls_survive(renderer: MarkdownRenderer) -> None:
    html = renderer.render_html("![a](data:image/png;base64,iVBORw0KGgo=)")
    assert 'src="data:image/png;base64,iVBORw0KGgo="' in html


def test_relative_image_paths_are_left_alone(renderer: MarkdownRenderer) -> None:
    """They are resolved by the preview's base URL, not rewritten here."""
    for source, expected in (
        ("![a](pic.png)", 'src="pic.png"'),
        ("![a](./pic.png)", 'src="./pic.png"'),
        ("![a](sub/pic.png)", 'src="sub/pic.png"'),
        ("![a](/abs/pic.png)", 'src="/abs/pic.png"'),
    ):
        assert expected in renderer.render_html(source)
