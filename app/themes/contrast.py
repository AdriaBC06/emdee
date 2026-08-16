# SPDX-License-Identifier: GPL-3.0-or-later
"""Colour maths: WCAG contrast, blending and automatic readability fixes.

Pure Python, no Qt.  This module is what guarantees the accessibility promise of
the theme system: every colour that ends up being used as *text* is pushed
through :func:`ensure_readable` so it reaches at least WCAG AA (4.5:1) against
the surface it is painted on.
"""

from __future__ import annotations

import colorsys

__all__ = [
    "hex_to_rgb",
    "rgb_to_hex",
    "relative_luminance",
    "contrast_ratio",
    "mix",
    "with_alpha",
    "ensure_readable",
    "is_dark",
    "AA_NORMAL",
    "AA_LARGE",
]

AA_NORMAL: float = 4.5
AA_LARGE: float = 3.0


def hex_to_rgb(color: str) -> tuple[int, int, int]:
    """Convert ``#rrggbb`` (or ``#rgb``) to an ``(r, g, b)`` 0-255 tuple."""
    value = color.strip().lstrip("#")
    if len(value) == 3:
        value = "".join(ch * 2 for ch in value)
    if len(value) != 6:
        raise ValueError(f"not a hex colour: {color!r}")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def rgb_to_hex(rgb: tuple[int, int, int]) -> str:
    """Convert an ``(r, g, b)`` tuple back to ``#rrggbb``."""
    r, g, b = (max(0, min(255, int(round(c)))) for c in rgb)
    return f"#{r:02x}{g:02x}{b:02x}"


def _channel_luminance(channel: int) -> float:
    c = channel / 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def relative_luminance(color: str) -> float:
    """WCAG relative luminance of a hex colour (0.0 = black, 1.0 = white)."""
    r, g, b = hex_to_rgb(color)
    return (
        0.2126 * _channel_luminance(r)
        + 0.7152 * _channel_luminance(g)
        + 0.0722 * _channel_luminance(b)
    )


def contrast_ratio(fg: str, bg: str) -> float:
    """WCAG contrast ratio between two hex colours (1.0 … 21.0)."""
    l1, l2 = relative_luminance(fg), relative_luminance(bg)
    if l1 < l2:
        l1, l2 = l2, l1
    return (l1 + 0.05) / (l2 + 0.05)


def is_dark(color: str) -> bool:
    """True when a colour is dark enough that light text reads better on it."""
    return relative_luminance(color) < 0.18


def mix(color_a: str, color_b: str, weight: float = 0.5) -> str:
    """Blend two hex colours; ``weight`` is how much of ``color_b`` to use."""
    weight = max(0.0, min(1.0, weight))
    a = hex_to_rgb(color_a)
    b = hex_to_rgb(color_b)
    return rgb_to_hex(tuple(a[i] + (b[i] - a[i]) * weight for i in range(3)))  # type: ignore[arg-type]


def with_alpha(color: str, alpha: float) -> str:
    """Return an ``rgba(...)`` CSS/QSS string for a hex colour."""
    r, g, b = hex_to_rgb(color)
    return f"rgba({r}, {g}, {b}, {max(0.0, min(1.0, alpha)):.3f})"


def ensure_readable(fg: str, bg: str, target: float = AA_NORMAL) -> str:
    """Nudge ``fg`` until it reaches ``target`` contrast against ``bg``.

    Hue and saturation are preserved; only HLS lightness moves, away from the
    background.  If the colour cannot reach the target without becoming pure
    black or white, the best achievable variant is returned.
    """
    if contrast_ratio(fg, bg) >= target:
        return fg

    r, g, b = (c / 255.0 for c in hex_to_rgb(fg))
    hue, lightness, saturation = colorsys.rgb_to_hls(r, g, b)
    # Move away from the background: lighten on dark backgrounds, darken on
    # light ones.
    direction = 1.0 if relative_luminance(bg) < 0.5 else -1.0

    best = fg
    best_ratio = contrast_ratio(fg, bg)
    for step in range(1, 101):
        candidate_lightness = min(1.0, max(0.0, lightness + direction * step * 0.01))
        cr, cg, cb = colorsys.hls_to_rgb(hue, candidate_lightness, saturation)
        color = rgb_to_hex((cr * 255, cg * 255, cb * 255))
        ratio = contrast_ratio(color, bg)
        if ratio > best_ratio:
            best, best_ratio = color, ratio
        if ratio >= target:
            return color
        if candidate_lightness in (0.0, 1.0):
            break
    return best
