# SPDX-License-Identifier: GPL-3.0-or-later
"""Unit tests for the pure text transformations behind the editor."""

from __future__ import annotations

import pytest

from app.core.textops import (
    SearchOptions,
    apply_heading,
    compute_stats,
    continuation_for,
    expand_selection_to_lines,
    find_matches,
    is_empty_list_item,
    make_link,
    replace_all,
    toggle_block_prefix,
    toggle_code_block,
    toggle_wrap,
)


# ----------------------------------------------------------------- wrapping
def test_wrap_selection_with_bold_markers() -> None:
    result = toggle_wrap("hello world", 0, 5, "**")
    assert result.text == "**hello** world"
    assert (result.start, result.end) == (2, 7)


def test_wrap_unwraps_when_markers_are_inside_the_selection() -> None:
    result = toggle_wrap("**hello** world", 0, 9, "**")
    assert result.text == "hello world"
    assert (result.start, result.end) == (0, 5)


def test_wrap_unwraps_when_markers_surround_the_selection() -> None:
    result = toggle_wrap("**hello** world", 2, 7, "**")
    assert result.text == "hello world"


def test_wrap_without_selection_inserts_a_placeholder() -> None:
    result = toggle_wrap("ab", 1, 1, "`", placeholder="code")
    assert result.text == "a`code`b"
    assert result.text[result.start : result.end] == "code"


def test_wrap_ignores_whitespace_at_the_selection_edges() -> None:
    result = toggle_wrap("say  hi  now", 3, 9, "*")
    assert result.text == "say  *hi*  now"


# ------------------------------------------------------------------- links
def test_make_link_wraps_the_selected_label() -> None:
    result = make_link("click me", 0, 8)
    assert result.text == "[click me]()"
    assert result.start == result.end == 11


def test_make_link_uses_a_selected_url_as_the_target() -> None:
    result = make_link("https://example.com", 0, 19)
    assert result.text == "[](https://example.com)"
    assert result.start == 1


def test_make_link_without_selection_selects_the_placeholder_label() -> None:
    result = make_link("", 0, 0)
    assert result.text == "[text]()"
    assert result.text[result.start : result.end] == "text"


# ---------------------------------------------------------------- headings
def test_apply_heading_sets_the_level() -> None:
    assert apply_heading("title", 0, 0, 2).text == "## title"


def test_apply_heading_replaces_an_existing_level() -> None:
    assert apply_heading("# title", 0, 0, 3).text == "### title"


def test_apply_heading_toggles_off_when_the_level_matches() -> None:
    assert apply_heading("### title", 0, 0, 3).text == "title"


def test_apply_heading_covers_every_selected_line() -> None:
    result = apply_heading("one\ntwo", 0, 7, 1)
    assert result.text == "# one\n# two"


def test_apply_heading_rejects_out_of_range_levels() -> None:
    with pytest.raises(ValueError):
        apply_heading("x", 0, 0, 9)


# ------------------------------------------------------------ line prefixes
def test_toggle_block_prefix_adds_then_removes() -> None:
    added = toggle_block_prefix("a\nb", 0, 3, "> ")
    assert added.text == "> a\n> b"
    removed = toggle_block_prefix(added.text, 0, len(added.text), "> ")
    assert removed.text == "a\nb"


def test_toggle_block_prefix_makes_a_task_list() -> None:
    assert toggle_block_prefix("milk", 0, 4, "- [ ] ").text == "- [ ] milk"


# --------------------------------------------------------------- code block
def test_toggle_code_block_fences_and_unfences() -> None:
    fenced = toggle_code_block("print(1)", 0, 8, "python")
    assert fenced.text == "```python\nprint(1)\n```"
    assert fenced.text[fenced.start : fenced.end] == "print(1)"
    plain = toggle_code_block(fenced.text, 0, len(fenced.text))
    assert plain.text == "print(1)"


# --------------------------------------------------------- list continuation
@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("- item", "- "),
        ("* item", "* "),
        ("  + nested", "  + "),
        ("1. first", "2. "),
        ("9) ninth", "10) "),
        ("- [ ] todo", "- [ ] "),
        ("- [x] done", "- [ ] "),
        ("> quoted", "> "),
        (">> deep", ">> "),
        ("plain text", None),
        ("", None),
    ],
)
def test_continuation_for(line: str, expected: str | None) -> None:
    assert continuation_for(line) == expected


@pytest.mark.parametrize("line", ["- ", "1. ", "> "])
def test_empty_items_end_the_list(line: str) -> None:
    assert continuation_for(line) == ""
    assert is_empty_list_item(line)


# ------------------------------------------------------------------- stats
def test_compute_stats_counts_words_characters_and_reading_time() -> None:
    stats = compute_stats("one two three")
    assert stats.words == 3
    assert stats.characters == 13
    assert stats.characters_no_spaces == 11
    assert stats.lines == 1
    assert stats.reading_time == "1 min read"


def test_compute_stats_on_empty_text() -> None:
    stats = compute_stats("")
    assert (stats.words, stats.characters) == (0, 0)
    assert stats.reading_time == "0 min read"


def test_reading_time_scales_with_length() -> None:
    stats = compute_stats(" ".join(["word"] * 1100))
    assert stats.words == 1100
    assert stats.reading_time == "5 min read"


def test_stats_handle_apostrophes_and_hyphens_as_one_word() -> None:
    assert compute_stats("well-known don't").words == 2


# ------------------------------------------------------------------ search
def test_find_matches_is_case_insensitive_by_default() -> None:
    assert len(find_matches("Cat cat CAT", "cat")) == 3


def test_find_matches_honours_case_sensitivity() -> None:
    options = SearchOptions(case_sensitive=True)
    assert len(find_matches("Cat cat CAT", "cat", options)) == 1


def test_find_matches_whole_word_only() -> None:
    options = SearchOptions(whole_word=True)
    assert len(find_matches("cat catalogue cat.", "cat", options)) == 2


def test_find_matches_supports_regex() -> None:
    options = SearchOptions(regex=True)
    spans = find_matches("a1 b22 c333", r"\d+", options)
    assert [end - start for start, end in spans] == [1, 2, 3]


def test_find_matches_ignores_zero_width_regex_hits() -> None:
    assert find_matches("abc", "x*", SearchOptions(regex=True)) == []


def test_replace_all_counts_substitutions() -> None:
    text, count = replace_all("a a a", "a", "b")
    assert (text, count) == ("b b b", 3)


def test_replace_all_treats_the_replacement_literally_outside_regex_mode() -> None:
    text, _ = replace_all("path", "path", r"C:\1\new")
    assert text == r"C:\1\new"


def test_replace_all_supports_regex_backreferences() -> None:
    text, count = replace_all("John Smith", r"(\w+) (\w+)", r"\2, \1", SearchOptions(regex=True))
    assert (text, count) == ("Smith, John", 1)


# --------------------------------------------------------------- selections
def test_expand_selection_to_lines() -> None:
    text = "alpha\nbeta\ngamma"
    assert expand_selection_to_lines(text, 7, 8) == (6, 10)
    assert expand_selection_to_lines(text, 0, 0) == (0, 5)
