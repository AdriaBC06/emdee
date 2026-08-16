# SPDX-License-Identifier: GPL-3.0-or-later
"""The six Emdee colour palettes, defined once as colour tokens.

This module is the single source of truth for colour in the whole application.
The application stylesheet (QSS), the HTML preview stylesheet, the editor syntax
highlighter and the Pygments code theme are *all* generated from these tokens —
no colour is ever written twice.

Base tokens are the literal palette values published by each upstream project.
Derived tokens (hover states, selection tints, readable-on-surface variants…)
are computed, so a new theme only ever needs the base tokens.
"""

from __future__ import annotations

from dataclasses import dataclass

from .contrast import AA_NORMAL, contrast_ratio, ensure_readable, mix, with_alpha

__all__ = [
    "Palette",
    "PALETTES",
    "DEFAULT_THEME",
    "get_palette",
    "print_variant",
]

DEFAULT_THEME: str = "dracula"

#: Tokens that are painted as text and therefore must pass WCAG AA.
TEXT_ROLE_TOKENS: tuple[str, ...] = (
    "text",
    "muted",
    "accent",
    "accent2",
    "green",
    "yellow",
    "red",
    "cyan",
)

#: Surfaces those text roles can land on.
SURFACE_ROLE_TOKENS: tuple[str, ...] = ("bg", "bg_alt", "surface")


@dataclass(frozen=True)
class Palette:
    """A theme expressed purely as colour tokens."""

    key: str
    name: str
    is_dark: bool
    bg: str
    bg_alt: str
    surface: str
    border: str
    text: str
    muted: str
    accent: str
    accent2: str
    green: str
    yellow: str
    red: str
    cyan: str

    # ---------------------------------------------------------------- helpers
    def on(self, token: str, surface: str = "bg", target: float = AA_NORMAL) -> str:
        """Return ``token`` adjusted so it is readable on ``surface``.

        ``token`` and ``surface`` may be token names or literal hex colours.
        """
        fg = getattr(self, token, token)
        bg = getattr(self, surface, surface)
        return ensure_readable(fg, bg, target)

    @property
    def contrast_color(self) -> str:
        """Text colour to place *on top of* the accent colour.

        Whichever of near-white / near-black contrasts better wins, so accent
        buttons stay legible in both the pastel and the vivid palettes.
        """
        light, dark = "#ffffff", "#101014"
        return light if contrast_ratio(light, self.accent) >= contrast_ratio(dark, self.accent) else dark

    def base_tokens(self) -> dict[str, str]:
        """The literal, unmodified palette tokens."""
        return {
            "bg": self.bg,
            "bg_alt": self.bg_alt,
            "surface": self.surface,
            "border": self.border,
            "text": self.text,
            "muted": self.muted,
            "accent": self.accent,
            "accent2": self.accent2,
            "green": self.green,
            "yellow": self.yellow,
            "red": self.red,
            "cyan": self.cyan,
        }

    def tokens(self) -> dict[str, str]:
        """Every token available to the QSS / CSS templates.

        Includes the base palette, readable-on-surface variants of each text
        role and a handful of interaction states derived by blending.
        """
        toward_text = 0.10 if self.is_dark else 0.08
        tokens: dict[str, str] = dict(self.base_tokens())

        # Blended surfaces have to exist before the foregrounds that sit on
        # them, because those foregrounds are corrected against them.
        code_bg = self.bg_alt if self.is_dark else mix(self.bg_alt, self.text, 0.03)
        selection_bg = mix(self.bg, self.accent, 0.35)
        current_line = mix(self.bg, self.text, 0.06)
        hover = mix(self.surface, self.text, toward_text)
        accent_soft = mix(self.bg, self.accent, 0.18)

        # Readable text roles, per surface.  ``muted`` in particular is often
        # too dim in the upstream palettes; this is where it gets fixed.
        for role in TEXT_ROLE_TOKENS:
            for surface in SURFACE_ROLE_TOKENS:
                tokens[f"{role}_on_{surface}"] = self.on(role, surface)

        tokens.update(
            {
                # interaction states
                "hover": hover,
                "active": mix(self.surface, self.accent, 0.28),
                "pressed": mix(self.surface, self.text, toward_text * 2),
                "border_strong": mix(self.border, self.text, 0.25),
                "selection_bg": selection_bg,
                "selection_fg": ensure_readable(self.text, selection_bg),
                "current_line": current_line,
                "text_on_hover": ensure_readable(self.text, hover),
                "text_on_accent_soft": ensure_readable(self.text, accent_soft),
                "line_number": ensure_readable(self.muted, self.bg_alt),
                "line_number_active": ensure_readable(self.accent, self.bg_alt),
                "code_bg": code_bg,
                "code_border": self.border,
                "inline_code_fg": ensure_readable(self.accent2, code_bg),
                "table_stripe": mix(self.bg, self.text, 0.04),
                "scrollbar": mix(self.bg_alt, self.text, 0.18),
                "scrollbar_hover": mix(self.bg_alt, self.accent, 0.55),
                "shadow": with_alpha("#000000", 0.45 if self.is_dark else 0.18),
                "overlay": with_alpha(self.accent, 0.16),
                "accent_soft": accent_soft,
                "accent_contrast": self.contrast_color,
                "danger_soft": mix(self.bg, self.red, 0.22),
                "warning_soft": mix(self.bg, self.yellow, 0.22),
                "success_soft": mix(self.bg, self.green, 0.22),
                "is_dark": "true" if self.is_dark else "false",
                "color_scheme": "dark" if self.is_dark else "light",
            }
        )
        return tokens


PALETTES: dict[str, Palette] = {
    "dracula": Palette(
        key="dracula",
        name="Dracula",
        is_dark=True,
        bg="#282a36",
        bg_alt="#21222c",
        surface="#343746",
        border="#44475a",
        text="#f8f8f2",
        muted="#6272a4",
        accent="#bd93f9",
        accent2="#ff79c6",
        green="#50fa7b",
        yellow="#f1fa8c",
        red="#ff5555",
        cyan="#8be9fd",
    ),
    "clean-light": Palette(
        key="clean-light",
        name="Clean Light",
        is_dark=False,
        bg="#ffffff",
        bg_alt="#f6f8fa",
        surface="#eaeef2",
        border="#d0d7de",
        text="#1f2328",
        muted="#656d76",
        accent="#8250df",
        accent2="#bf3989",
        green="#1a7f37",
        yellow="#9a6700",
        red="#cf222e",
        cyan="#0969da",
    ),
    "catppuccin-latte": Palette(
        key="catppuccin-latte",
        name="Catppuccin Latte",
        is_dark=False,
        bg="#eff1f5",
        bg_alt="#e6e9ef",
        surface="#ccd0da",
        border="#bcc0cc",
        text="#4c4f69",
        muted="#8c8fa1",
        accent="#8839ef",
        accent2="#ea76cb",
        green="#40a02b",
        yellow="#df8e1d",
        red="#d20f39",
        cyan="#179299",
    ),
    "catppuccin-frappe": Palette(
        key="catppuccin-frappe",
        name="Catppuccin Frappé",
        is_dark=True,
        bg="#303446",
        bg_alt="#292c3c",
        surface="#414559",
        border="#51576d",
        text="#c6d0f5",
        muted="#838ba7",
        accent="#ca9ee6",
        accent2="#f4b8e4",
        green="#a6d189",
        yellow="#e5c890",
        red="#e78284",
        cyan="#81c8be",
    ),
    "rose-pine-dawn": Palette(
        key="rose-pine-dawn",
        name="Rosé Pine Dawn",
        is_dark=False,
        bg="#faf4ed",
        bg_alt="#fffaf3",
        surface="#f2e9e1",
        border="#dfdad9",
        text="#575279",
        muted="#9893a5",
        accent="#907aa9",
        accent2="#d7827e",
        green="#56949f",
        yellow="#ea9d34",
        red="#b4637a",
        cyan="#286983",
    ),
    "nord": Palette(
        key="nord",
        name="Nord",
        is_dark=True,
        bg="#2e3440",
        bg_alt="#3b4252",
        surface="#434c5e",
        border="#4c566a",
        text="#eceff4",
        muted="#81a1c1",
        accent="#88c0d0",
        accent2="#b48ead",
        green="#a3be8c",
        yellow="#ebcb8b",
        red="#bf616a",
        cyan="#8fbcbb",
    ),
}


PAPER = "#ffffff"
INK = "#16161a"


def print_variant(palette: Palette) -> Palette:
    """Return a paper version of ``palette`` for PDF export.

    Printing a dark theme as-is either wastes toner or produces invisible text,
    so exports keep the theme's *hues* but move them onto white.  Every accent
    is pre-corrected for contrast against paper, which means even the CSS rules
    that use a raw token stay readable.
    """
    def ink(color: str) -> str:
        return ensure_readable(color, PAPER)

    return Palette(
        key=f"{palette.key}@print",
        name=f"{palette.name} (print)",
        is_dark=False,
        bg=PAPER,
        bg_alt=mix(PAPER, INK, 0.035),
        surface=mix(PAPER, INK, 0.08),
        border=mix(PAPER, INK, 0.22),
        text=INK,
        muted=mix(INK, PAPER, 0.38),
        accent=ink(palette.accent),
        accent2=ink(palette.accent2),
        green=ink(palette.green),
        yellow=ink(palette.yellow),
        red=ink(palette.red),
        cyan=ink(palette.cyan),
    )


def get_palette(key: str | None) -> Palette:
    """Look up a palette, falling back to the default theme."""
    return PALETTES.get(key or "", PALETTES[DEFAULT_THEME])
