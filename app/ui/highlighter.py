# SPDX-License-Identifier: GPL-3.0-or-later
"""Markdown syntax highlighting inside the plain-text editor.

The colour of every rule comes from the active palette, so the editor changes
appearance together with the rest of the application.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from PyQt6.QtGui import (
    QColor,
    QFont,
    QSyntaxHighlighter,
    QTextCharFormat,
    QTextDocument,
)

from ..themes.palettes import Palette

__all__ = ["MarkdownHighlighter"]

# Block states
_STATE_NORMAL = 0
_STATE_CODE = 1
_STATE_FRONT_MATTER = 2

_FENCE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")
_FRONT_MATTER_DELIM = re.compile(r"^---\s*$")


@dataclass(frozen=True)
class _Rule:
    pattern: re.Pattern[str]
    format_key: str
    group: int = 0
    #: Optional secondary group painted with ``extra_key`` (e.g. link targets).
    extra_group: int | None = None
    extra_key: str = ""


class MarkdownHighlighter(QSyntaxHighlighter):
    """Highlights Markdown markup in a :class:`QPlainTextEdit`."""

    def __init__(self, document: QTextDocument, palette: Palette) -> None:
        super().__init__(document)
        self._formats: dict[str, QTextCharFormat] = {}
        self._rules: list[_Rule] = self._build_rules()
        self.set_palette(palette)

    # ---------------------------------------------------------------- setup
    @staticmethod
    def _build_rules() -> list[_Rule]:
        return [
            # Horizontal rules and setext underlines.
            _Rule(re.compile(r"^\s{0,3}([*_-])(?:\s*\1){2,}\s*$"), "rule"),
            # Blockquotes.
            _Rule(re.compile(r"^\s*(>+)\s?(.*)$"), "quote_marker", group=1,
                  extra_group=2, extra_key="quote"),
            # List markers, including task lists.
            _Rule(re.compile(r"^(\s*)([*+-]|\d{1,9}[.)])\s+(\[[ xX]\])?"), "list_marker", group=2,
                  extra_group=3, extra_key="task"),
            # Tables.
            _Rule(re.compile(r"^\s*\|.*\|\s*$"), "table"),
            # Images then links (images first so the leading "!" wins).
            _Rule(re.compile(r"!\[[^\]]*\]\([^)]*\)"), "image"),
            _Rule(re.compile(r"(\[[^\]^]*\])(\([^)]*\)|\[[^\]]*\])"), "link_text", group=1,
                  extra_group=2, extra_key="link_url"),
            _Rule(re.compile(r"<(?:https?|mailto):[^>\s]+>"), "link_url"),
            _Rule(re.compile(r"^\s*\[\^[^\]]+\]:"), "footnote"),
            _Rule(re.compile(r"\[\^[^\]]+\]"), "footnote"),
            # Emphasis.
            _Rule(re.compile(r"(?<!\w)(\*\*\*|___)(?!\s)(.+?)(?<!\s)\1(?!\w)"), "bold_italic"),
            _Rule(re.compile(r"(?<!\w)(\*\*|__)(?!\s)(.+?)(?<!\s)\1(?!\w)"), "bold"),
            _Rule(re.compile(r"(?<![\w*])(\*|_)(?!\s)([^*_]+?)(?<!\s)\1(?![\w*])"), "italic"),
            _Rule(re.compile(r"~~(?!\s)(.+?)(?<!\s)~~"), "strike"),
            # Inline code last so it overrides emphasis inside it.
            _Rule(re.compile(r"(`+)(?!`)(.+?)(?<!`)\1(?!`)"), "code"),
            # Raw HTML.
            _Rule(re.compile(r"</?[a-zA-Z][a-zA-Z0-9-]*(?:\s[^<>]*)?/?>"), "html"),
        ]

    def _make(
        self,
        color: str,
        *,
        bold: bool = False,
        italic: bool = False,
        strike: bool = False,
        mono: bool = False,
        background: str | None = None,
    ) -> QTextCharFormat:
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(color))
        if bold:
            fmt.setFontWeight(QFont.Weight.Bold)
        if italic:
            fmt.setFontItalic(True)
        if strike:
            fmt.setFontStrikeOut(True)
        if mono:
            fmt.setFontFixedPitch(True)
        if background:
            fmt.setBackground(QColor(background))
        return fmt

    def set_palette(self, palette: Palette) -> None:
        """Rebuild every text format for a new theme and repaint."""
        t = palette.tokens()
        accent = t["accent_on_bg"]
        accent2 = t["accent2_on_bg"]
        muted = t["muted_on_bg"]
        code_bg = t["code_bg"]

        self._formats = {
            "heading": self._make(accent, bold=True),
            "heading_marker": self._make(accent2, bold=True),
            "bold": self._make(t["text"], bold=True),
            "italic": self._make(t["text"], italic=True),
            "bold_italic": self._make(t["text"], bold=True, italic=True),
            "strike": self._make(muted, strike=True),
            "code": self._make(t["green_on_bg"], mono=True, background=code_bg),
            "code_block": self._make(t["green_on_bg"], mono=True, background=code_bg),
            "fence": self._make(muted, mono=True, background=code_bg),
            "link_text": self._make(t["cyan_on_bg"]),
            "link_url": self._make(muted),
            "image": self._make(t["yellow_on_bg"]),
            "footnote": self._make(t["cyan_on_bg"]),
            "list_marker": self._make(accent2, bold=True),
            "task": self._make(t["green_on_bg"], bold=True),
            "quote_marker": self._make(accent, bold=True),
            "quote": self._make(muted, italic=True),
            "rule": self._make(t["border_strong"], bold=True),
            "table": self._make(t["cyan_on_bg"]),
            "html": self._make(muted),
            "front_matter": self._make(muted, italic=True, background=t["bg_alt"]),
        }
        self.rehighlight()

    # ----------------------------------------------------------- highlighting
    def highlightBlock(self, text: str | None) -> None:  # noqa: N802 - Qt API
        line = text or ""
        previous = self.previousBlockState()

        if self._handle_front_matter(line, previous):
            return
        if self._handle_code_fence(line, previous):
            return

        self.setCurrentBlockState(_STATE_NORMAL)
        self._highlight_heading(line)
        for rule in self._rules:
            self._apply_rule(rule, line)

    def _handle_front_matter(self, line: str, previous: int) -> bool:
        at_start = self.currentBlock().blockNumber() == 0
        if at_start and _FRONT_MATTER_DELIM.match(line):
            self.setCurrentBlockState(_STATE_FRONT_MATTER)
            self.setFormat(0, len(line), self._formats["front_matter"])
            return True
        if previous == _STATE_FRONT_MATTER:
            self.setFormat(0, len(line), self._formats["front_matter"])
            self.setCurrentBlockState(
                _STATE_NORMAL if _FRONT_MATTER_DELIM.match(line) else _STATE_FRONT_MATTER
            )
            return True
        return False

    def _handle_code_fence(self, line: str, previous: int) -> bool:
        fence = _FENCE.match(line)
        if previous == _STATE_CODE:
            if fence:
                self.setCurrentBlockState(_STATE_NORMAL)
                self.setFormat(0, len(line), self._formats["fence"])
            else:
                self.setCurrentBlockState(_STATE_CODE)
                self.setFormat(0, len(line), self._formats["code_block"])
            return True
        if fence:
            self.setCurrentBlockState(_STATE_CODE)
            self.setFormat(0, len(line), self._formats["fence"])
            return True
        return False

    def _highlight_heading(self, line: str) -> None:
        match = re.match(r"^(\s{0,3})(#{1,6})(\s+)(.*)$", line)
        if not match:
            return
        marker = match.group(2)
        marker_start = match.start(2)
        self.setFormat(marker_start, len(marker), self._formats["heading_marker"])
        body_start = match.start(4)
        if body_start < len(line):
            fmt = QTextCharFormat(self._formats["heading"])
            # Larger headings get visually larger text — but only when the base
            # font is expressed in points; pixel-sized fonts report -1.
            base = self.document().defaultFont().pointSizeF()
            if base > 0:
                fmt.setFontPointSize(base * self._scale(len(marker)))
            self.setFormat(body_start, len(line) - body_start, fmt)

    @staticmethod
    def _scale(level: int) -> float:
        return {1: 1.55, 2: 1.35, 3: 1.2, 4: 1.1, 5: 1.05, 6: 1.0}.get(level, 1.0)

    def _apply_rule(self, rule: _Rule, line: str) -> None:
        fmt = self._formats.get(rule.format_key)
        if fmt is None:
            return
        for match in rule.pattern.finditer(line):
            if rule.group and rule.group <= (match.re.groups or 0):
                span = match.span(rule.group)
                if span[0] >= 0:
                    self.setFormat(span[0], span[1] - span[0], fmt)
            else:
                self.setFormat(match.start(), match.end() - match.start(), fmt)

            if rule.extra_group is not None and rule.extra_key:
                extra_fmt = self._formats.get(rule.extra_key)
                span = match.span(rule.extra_group)
                if extra_fmt is not None and span[0] >= 0:
                    self.setFormat(span[0], span[1] - span[0], extra_fmt)
