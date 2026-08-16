# SPDX-License-Identifier: GPL-3.0-or-later
"""Pure text transformations used by the editor.

Keeping these out of the widget means every formatting shortcut, the list
continuation logic and the document statistics can be unit-tested without a
running Qt application.

All functions operate on ``(text, start, end)`` where ``start``/``end`` are
absolute character offsets, and return an :class:`EditResult` describing the
new text and the new selection.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = [
    "EditResult",
    "DocumentStats",
    "SearchOptions",
    "find_matches",
    "build_search_pattern",
    "toggle_wrap",
    "apply_heading",
    "make_link",
    "toggle_code_block",
    "toggle_block_prefix",
    "continuation_for",
    "compute_stats",
    "expand_selection_to_lines",
    "WORDS_PER_MINUTE",
]

WORDS_PER_MINUTE: int = 220

_ORDERED_ITEM = re.compile(r"^(\s*)(\d+)([.)])(\s+)(\[[ xX]\]\s+)?(.*)$")
_BULLET_ITEM = re.compile(r"^(\s*)([-*+])(\s+)(\[[ xX]\]\s+)?(.*)$")
_QUOTE_LINE = re.compile(r"^(\s*(?:>\s?)+)(.*)$")
_WORD = re.compile(r"[^\W_]+(?:['’-][^\W_]+)*", re.UNICODE)
_CODE_FENCE = re.compile(r"^\s*(```|~~~)")


@dataclass(frozen=True)
class EditResult:
    """New document text together with the selection to restore."""

    text: str
    start: int
    end: int

    @property
    def cursor(self) -> int:
        return self.end


@dataclass(frozen=True)
class DocumentStats:
    """Counters shown in the status bar."""

    words: int
    characters: int
    characters_no_spaces: int
    lines: int
    reading_seconds: int

    @property
    def reading_time(self) -> str:
        """Human readable estimate, e.g. ``"3 min read"``."""
        if self.words == 0:
            return "0 min read"
        minutes = max(1, round(self.reading_seconds / 60))
        return f"{minutes} min read"


def compute_stats(text: str) -> DocumentStats:
    """Word / character / reading-time counters for ``text``."""
    words = len(_WORD.findall(text))
    characters = len(text)
    no_spaces = len(re.sub(r"\s", "", text))
    lines = text.count("\n") + 1 if text else 1
    seconds = int(round(words / WORDS_PER_MINUTE * 60)) if words else 0
    return DocumentStats(words, characters, no_spaces, lines, seconds)


# --------------------------------------------------------------- selection
def expand_selection_to_lines(text: str, start: int, end: int) -> tuple[int, int]:
    """Grow a selection so it covers whole lines."""
    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", end)
    if line_end == -1:
        line_end = len(text)
    return line_start, line_end


def _trim_selection(text: str, start: int, end: int) -> tuple[int, int]:
    """Drop leading/trailing whitespace from a selection range."""
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return start, end


# ------------------------------------------------------------ inline marks
def toggle_wrap(
    text: str,
    start: int,
    end: int,
    marker: str,
    closing: str | None = None,
    placeholder: str = "",
) -> EditResult:
    """Wrap or unwrap the selection with ``marker`` (e.g. ``**`` or ``` ` ```).

    With no selection, the markers are inserted and the cursor is placed in the
    middle so the user can just keep typing.
    """
    close = closing if closing is not None else marker

    if start == end:
        if placeholder:
            new = text[:start] + marker + placeholder + close + text[end:]
            return EditResult(new, start + len(marker), start + len(marker) + len(placeholder))
        new = text[:start] + marker + close + text[end:]
        pos = start + len(marker)
        return EditResult(new, pos, pos)

    start, end = _trim_selection(text, start, end)
    selected = text[start:end]

    # Already wrapped inside the selection -> unwrap.
    if selected.startswith(marker) and selected.endswith(close) and len(selected) >= len(marker) + len(close):
        inner = selected[len(marker) : len(selected) - len(close)]
        new = text[:start] + inner + text[end:]
        return EditResult(new, start, start + len(inner))

    # Wrapped just outside the selection -> unwrap too.
    outer_start = start - len(marker)
    outer_end = end + len(close)
    if (
        outer_start >= 0
        and text[outer_start:start] == marker
        and text[end:outer_end] == close
    ):
        new = text[:outer_start] + selected + text[outer_end:]
        return EditResult(new, outer_start, outer_start + len(selected))

    new = text[:start] + marker + selected + close + text[end:]
    return EditResult(new, start + len(marker), end + len(marker))


def make_link(text: str, start: int, end: int, url: str = "") -> EditResult:
    """Turn the selection into ``[selection](url)``.

    If the selection already looks like a URL it becomes the target instead of
    the label.
    """
    if start != end:
        start, end = _trim_selection(text, start, end)
    selected = text[start:end]

    if selected and re.match(r"^(https?://|www\.|mailto:|/|\./|\.\./)", selected):
        snippet = f"[]({selected})"
        new = text[:start] + snippet + text[end:]
        pos = start + 1
        return EditResult(new, pos, pos)

    label = selected or "text"
    snippet = f"[{label}]({url})"
    new = text[:start] + snippet + text[end:]
    if selected:
        pos = start + len(label) + 3
        return EditResult(new, pos, pos + len(url))
    return EditResult(new, start + 1, start + 1 + len(label))


def toggle_code_block(text: str, start: int, end: int, language: str = "") -> EditResult:
    """Wrap whole lines in a fenced code block, or remove an existing fence."""
    line_start, line_end = expand_selection_to_lines(text, start, end)
    block = text[line_start:line_end]
    lines = block.split("\n")

    if len(lines) >= 2 and _CODE_FENCE.match(lines[0]) and _CODE_FENCE.match(lines[-1]):
        inner = "\n".join(lines[1:-1])
        new = text[:line_start] + inner + text[line_end:]
        return EditResult(new, line_start, line_start + len(inner))

    opening = f"```{language}"
    fenced = f"{opening}\n{block}\n```"
    prefix = "" if line_start == 0 or text[line_start - 1 : line_start] == "\n" else "\n"
    suffix = "" if line_end >= len(text) else ""
    new = text[:line_start] + prefix + fenced + suffix + text[line_end:]
    body_start = line_start + len(prefix) + len(opening) + 1
    return EditResult(new, body_start, body_start + len(block))


# ------------------------------------------------------------ line prefixes
def toggle_block_prefix(text: str, start: int, end: int, prefix: str) -> EditResult:
    """Add ``prefix`` to every selected line, or strip it when all lines have it."""
    line_start, line_end = expand_selection_to_lines(text, start, end)
    lines = text[line_start:line_end].split("\n")
    has_all = all(line.lstrip().startswith(prefix.strip()) or not line.strip() for line in lines)

    out: list[str] = []
    for line in lines:
        if has_all:
            stripped = line.lstrip()
            indent = line[: len(line) - len(stripped)]
            if stripped.startswith(prefix):
                out.append(indent + stripped[len(prefix) :])
            elif stripped.startswith(prefix.strip()):
                out.append(indent + stripped[len(prefix.strip()) :].lstrip())
            else:
                out.append(line)
        else:
            out.append(prefix + line if line.strip() or len(lines) == 1 else line)

    block = "\n".join(out)
    new = text[:line_start] + block + text[line_end:]
    return EditResult(new, line_start, line_start + len(block))


def apply_heading(text: str, start: int, end: int, level: int) -> EditResult:
    """Set (or clear, when the level already matches) the heading level."""
    if not 0 <= level <= 6:
        raise ValueError("heading level must be between 0 and 6")

    line_start, line_end = expand_selection_to_lines(text, start, end)
    lines = text[line_start:line_end].split("\n")
    target = "#" * level + " " if level else ""

    out: list[str] = []
    toggled_off = False
    for line in lines:
        match = re.match(r"^(\s*)(#{1,6})\s+(.*)$", line)
        if match:
            indent, hashes, body = match.groups()
            if len(hashes) == level:
                out.append(indent + body)
                toggled_off = True
            else:
                out.append(indent + target + body)
        else:
            stripped = line.lstrip()
            indent = line[: len(line) - len(stripped)]
            out.append(indent + target + stripped)

    block = "\n".join(out)
    new = text[:line_start] + block + text[line_end:]
    end_pos = line_start + len(block)
    if toggled_off or start == end:
        return EditResult(new, end_pos, end_pos)
    return EditResult(new, line_start, end_pos)


# -------------------------------------------------------- list continuation
def continuation_for(line: str) -> str | None:
    """Return the prefix a new line should start with after pressing Enter.

    ``None`` means "no continuation".  An empty string means "the list item was
    empty, so the marker should be removed" — the caller signals that case by
    also getting :func:`is_empty_item` to be true.
    """
    match = _BULLET_ITEM.match(line)
    if match:
        indent, bullet, space, task, body = match.groups()
        if not body.strip() and not task:
            return ""
        marker = f"{indent}{bullet}{space}"
        if task:
            marker += "[ ] "
        return marker

    match = _ORDERED_ITEM.match(line)
    if match:
        indent, number, sep, space, task, body = match.groups()
        if not body.strip() and not task:
            return ""
        marker = f"{indent}{int(number) + 1}{sep}{space}"
        if task:
            marker += "[ ] "
        return marker

    match = _QUOTE_LINE.match(line)
    if match:
        marker, body = match.groups()
        if not body.strip():
            return ""
        return marker

    return None


def is_empty_list_item(line: str) -> bool:
    """True when the line is a list/quote marker with no content after it."""
    for pattern in (_BULLET_ITEM, _ORDERED_ITEM):
        match = pattern.match(line)
        if match:
            groups = match.groups()
            task, body = groups[-2], groups[-1]
            return not body.strip() and (task is None or not body.strip())
    match = _QUOTE_LINE.match(line)
    if match:
        return not match.group(2).strip()
    return False


# ------------------------------------------------------------------ search
@dataclass(frozen=True)
class SearchOptions:
    """How a find/replace query should be interpreted."""

    case_sensitive: bool = False
    whole_word: bool = False
    regex: bool = False


def build_search_pattern(query: str, options: SearchOptions) -> re.Pattern[str]:
    """Compile a query into a regex honouring the search options.

    Raises :class:`re.error` for an invalid user-supplied pattern; the caller is
    expected to surface that as "invalid regex" rather than crashing.
    """
    body = query if options.regex else re.escape(query)
    if options.whole_word:
        body = rf"(?<!\w){body}(?!\w)"
    flags = 0 if options.case_sensitive else re.IGNORECASE
    return re.compile(body, flags | re.MULTILINE)


def find_matches(
    text: str, query: str, options: SearchOptions | None = None
) -> list[tuple[int, int]]:
    """Return ``(start, end)`` offsets of every match, skipping empty ones."""
    if not query:
        return []
    pattern = build_search_pattern(query, options or SearchOptions())
    matches: list[tuple[int, int]] = []
    for match in pattern.finditer(text):
        if match.end() > match.start():
            matches.append((match.start(), match.end()))
    return matches


def replace_all(
    text: str, query: str, replacement: str, options: SearchOptions | None = None
) -> tuple[str, int]:
    """Replace every match and report how many substitutions were made."""
    if not query:
        return text, 0
    opts = options or SearchOptions()
    pattern = build_search_pattern(query, opts)
    if opts.regex:
        new_text, count = pattern.subn(replacement, text)
    else:
        new_text, count = pattern.subn(lambda _: replacement, text)
    return new_text, count
