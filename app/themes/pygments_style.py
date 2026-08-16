# SPDX-License-Identifier: GPL-3.0-or-later
"""Pygments styles generated from the Emdee palette tokens.

Building the code-highlighting style from the same tokens as the rest of the UI
is what keeps fenced code blocks visually consistent with the editor and the
chrome around them.
"""

from __future__ import annotations

from functools import lru_cache

from pygments.style import Style
from pygments.token import (
    Comment,
    Error,
    Generic,
    Keyword,
    Literal,
    Name,
    Number,
    Operator,
    Punctuation,
    String,
    Text,
    Token,
    Whitespace,
)

from .palettes import Palette

__all__ = ["style_for"]


@lru_cache(maxsize=32)
def _build(palette: Palette) -> type[Style]:
    key = palette.key
    t = palette.tokens()
    code_bg = t["code_bg"]

    def on_code(token: str) -> str:
        return palette.on(token, code_bg)

    text = on_code("text")
    muted = on_code("muted")
    accent = on_code("accent")
    accent2 = on_code("accent2")
    green = on_code("green")
    yellow = on_code("yellow")
    red = on_code("red")
    cyan = on_code("cyan")

    class EmdeeStyle(Style):
        name = f"emdee-{key}"
        background_color = code_bg
        highlight_color = t["selection_bg"]
        line_number_color = muted
        line_number_background_color = code_bg

        styles = {
            Token: text,
            Whitespace: muted,
            Text: text,
            Error: f"bold {red}",
            Comment: f"italic {muted}",
            Comment.Preproc: cyan,
            Comment.Special: f"bold italic {muted}",
            Keyword: f"bold {accent2}",
            Keyword.Constant: accent,
            Keyword.Type: cyan,
            Operator: accent2,
            Operator.Word: f"bold {accent2}",
            Punctuation: text,
            Name: text,
            Name.Attribute: green,
            Name.Builtin: cyan,
            Name.Builtin.Pseudo: f"italic {accent}",
            Name.Class: f"bold {green}",
            Name.Constant: accent,
            Name.Decorator: yellow,
            Name.Entity: cyan,
            Name.Exception: f"bold {red}",
            Name.Function: green,
            Name.Function.Magic: green,
            Name.Label: cyan,
            Name.Namespace: cyan,
            Name.Tag: accent2,
            Name.Variable: accent,
            Name.Variable.Magic: accent,
            Literal: yellow,
            String: yellow,
            String.Doc: f"italic {yellow}",
            String.Escape: accent2,
            String.Interpol: accent2,
            String.Regex: red,
            String.Symbol: accent,
            Number: accent,
            Generic.Deleted: red,
            Generic.Emph: "italic",
            Generic.Error: red,
            Generic.Heading: f"bold {accent}",
            Generic.Inserted: green,
            Generic.Output: muted,
            Generic.Prompt: f"bold {muted}",
            Generic.Strong: "bold",
            Generic.Subheading: f"bold {cyan}",
            Generic.Traceback: red,
        }

    return EmdeeStyle


def style_for(palette: Palette) -> type[Style]:
    """Return the Pygments style class generated from ``palette``."""
    return _build(palette)

