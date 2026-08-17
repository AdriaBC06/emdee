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
def _run_length(text: str, index: int) -> int:
    """How many times the character at ``index`` repeats from there."""
    char = text[index]
    end = index
    while end < len(text) and text[end] == char:
        end += 1
    return end - index


def _marker_at(text: str, index: int, marker: str) -> bool:
    """True when ``marker`` starts at ``index`` and is not part of a longer run.

    Every inline marker is one character repeated (``*``, ``**``, ``~~``,
    ``` ` ```), so the ``*`` inside ``**bold**`` must not be mistaken for an
    italic marker — otherwise italicising bold text would strip the bold.
    """
    if not text.startswith(marker, index):
        return False
    if len(set(marker)) != 1:
        return True
    return _run_length(text, index) == len(marker)


def _find_marker(text: str, begin: int, stop: int, marker: str) -> int:
    """Index of the first real ``marker`` in ``text[begin:stop]``, or -1."""
    index = text.find(marker, begin, stop)
    while index != -1:
        if _marker_at(text, index, marker):
            return index
        index = text.find(marker, index + _run_length(text, index), stop)
    return -1


def _rfind_marker(text: str, begin: int, stop: int, marker: str) -> int:
    """Index of the last real ``marker`` in ``text[begin:stop]``, or -1."""
    found = -1
    index = _find_marker(text, begin, stop, marker)
    while index != -1:
        found = index
        index = _find_marker(text, index + len(marker), stop, marker)
    return found


def _enclosing_run(
    text: str, position: int, marker: str, close: str
) -> tuple[int, int] | None:
    """Find the ``marker … close`` run the caret sits inside, if any.

    Only the caret's own line is searched: emphasis does not span a blank line
    in CommonMark, and scanning the whole document would happily pair up
    markers belonging to two unrelated paragraphs.
    """
    line_start = text.rfind("\n", 0, position) + 1
    line_end = text.find("\n", position)
    if line_end == -1:
        line_end = len(text)

    opening = _rfind_marker(text, line_start, position, marker)
    if opening == -1:
        return None
    closing = _find_marker(text, max(opening + len(marker), position), line_end, close)
    if closing == -1:
        return None
    return opening, closing + len(close)


def _is_wrapped(selected: str, marker: str, close: str) -> bool:
    """True when ``selected`` is exactly one ``marker … close`` run.

    ``**bold**`` must not read as an italic run when the marker is a single
    ``*``; toggling italic on bold text is meant to add emphasis, not remove
    half of the bold markers.
    """
    if len(selected) < len(marker) + len(close):
        return False
    if not (_marker_at(selected, 0, marker) and selected.endswith(close)):
        return False
    # The run that opens at 0 has to be the one that closes at the very end,
    # otherwise this is several runs side by side ("**a** y **b**") and peeling
    # off the outermost markers would wreck the ones in the middle.
    closing = _find_marker(selected, len(marker), len(selected), close)
    return closing == len(selected) - len(close)


def toggle_wrap(
    text: str,
    start: int,
    end: int,
    marker: str,
    closing: str | None = None,
    placeholder: str = "",
) -> EditResult:
    """Wrap or unwrap the selection with ``marker`` (e.g. ``**`` or ``` ` ```).

    With no selection the caret's own run is removed when it is already inside
    one; otherwise the markers are inserted and the cursor is placed in the
    middle so the user can just keep typing.

    On a selection the outcome is always unambiguous: either the whole
    selection carries the formatting or none of it does.  A selection that
    merely *contains* formatted runs has them flattened before being wrapped,
    because leaving them in place would nest markers and produce something no
    Markdown parser reads back the way it looks.
    """
    close = closing if closing is not None else marker

    if start == end:
        run = _enclosing_run(text, start, marker, close)
        if run is not None:
            run_start, run_end = run
            inner = text[run_start + len(marker) : run_end - len(close)]
            new = text[:run_start] + inner + text[run_end:]
            return EditResult(new, run_start, run_start + len(inner))
        if placeholder:
            new = text[:start] + marker + placeholder + close + text[end:]
            return EditResult(new, start + len(marker), start + len(marker) + len(placeholder))
        new = text[:start] + marker + close + text[end:]
        pos = start + len(marker)
        return EditResult(new, pos, pos)

    start, end = _trim_selection(text, start, end)
    selected = text[start:end]

    # Already wrapped inside the selection -> unwrap.
    if _is_wrapped(selected, marker, close):
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

    body = _strip_runs(selected, marker, close)
    new = text[:start] + marker + body + close + text[end:]
    return EditResult(new, start + len(marker), start + len(marker) + len(body))


def _strip_runs(selected: str, marker: str, close: str) -> str:
    """Remove complete ``marker … close`` pairs from within a selection.

    Markers belonging to a *different* emphasis level are left alone, so
    flattening bold inside a selection never disturbs the italics around it.
    """
    if marker not in selected:
        return selected

    out: list[str] = []
    index = 0
    length = len(selected)
    while index < length:
        opening = _find_marker(selected, index, length, marker)
        if opening == -1:
            break
        closing = _find_marker(selected, opening + len(marker), length, close)
        if closing == -1:
            break
        out.append(selected[index:opening])
        out.append(selected[opening + len(marker) : closing])
        index = closing + len(close)
    out.append(selected[index:])
    return "".join(out)


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


def _enclosing_fence(text: str, start: int, end: int) -> tuple[int, int] | None:
    """Character range of the fenced block containing ``start``..``end``.

    Without this a caret sitting on a line *inside* a code block sees only that
    one line, decides it is not fenced, and opens a second fence inside the
    first.
    """
    start, end = _trim_selection(text, start, end)
    offset = 0
    opening: tuple[int, str] | None = None
    for line in text.split("\n"):
        line_end = offset + len(line)
        match = _CODE_FENCE.match(line)
        if match and opening is None:
            opening = (offset, match.group(1))
        elif match and opening is not None and match.group(1) == opening[1]:
            if opening[0] <= start and end <= line_end:
                return opening[0], line_end
            opening = None
        offset = line_end + 1
    return None


def toggle_code_block(text: str, start: int, end: int, language: str = "") -> EditResult:
    """Wrap whole lines in a fenced code block, or remove an existing fence."""
    fence = _enclosing_fence(text, start, end)
    if fence is not None:
        fence_start, fence_end = fence
        inner = "\n".join(text[fence_start:fence_end].split("\n")[1:-1])
        new = text[:fence_start] + inner + text[fence_end:]
        return EditResult(new, fence_start, fence_start + len(inner))

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
    """Add ``prefix`` to every selected line, or strip it when all lines have it.

    A selection that mixes prefixed and unprefixed lines gets the prefix added
    only where it is missing.  Adding it everywhere would push the lines that
    already had it a level deeper — a selection of a quote plus a plain line
    came back as ``> > a`` / ``> b`` — and then the toggle no longer returns the
    block to where it started.
    """
    line_start, line_end = expand_selection_to_lines(text, start, end)
    lines = text[line_start:line_end].split("\n")
    marker = prefix.strip()
    has_all = all(line.lstrip().startswith(marker) or not line.strip() for line in lines)

    out: list[str] = []
    for line in lines:
        stripped = line.lstrip()
        indent = line[: len(line) - len(stripped)]
        if has_all:
            if stripped.startswith(prefix):
                out.append(indent + stripped[len(prefix) :])
            elif stripped.startswith(marker):
                out.append(indent + stripped[len(marker) :].lstrip())
            else:
                out.append(line)
        elif stripped.startswith(marker):
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
