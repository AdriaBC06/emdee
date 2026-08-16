# SPDX-License-Identifier: GPL-3.0-or-later
"""Accessibility guarantees for the six themes.

Every colour pair that ends up as text-on-a-surface anywhere in the QSS or the
preview CSS is checked against WCAG AA (4.5:1).  If a palette ever regresses,
this is the test that fails.
"""

from __future__ import annotations

import pytest

from app.themes.contrast import (
    AA_NORMAL,
    contrast_ratio,
    ensure_readable,
    hex_to_rgb,
    mix,
    relative_luminance,
    rgb_to_hex,
)
from app.themes.palettes import (
    PALETTES,
    SURFACE_ROLE_TOKENS,
    TEXT_ROLE_TOKENS,
    Palette,
    print_variant,
)

ALL_PALETTES = list(PALETTES.values())
PALETTE_IDS = [palette.key for palette in ALL_PALETTES]

#: Additional pairs the stylesheets rely on beyond the generated `*_on_*` ones.
EXTRA_PAIRS: tuple[tuple[str, str], ...] = (
    ("selection_fg", "selection_bg"),
    ("text", "current_line"),
    ("text_on_hover", "hover"),
    ("text_on_accent_soft", "accent_soft"),
    ("line_number", "bg_alt"),
    ("line_number_active", "bg_alt"),
    ("inline_code_fg", "code_bg"),
    ("accent_contrast", "accent"),
)


# --------------------------------------------------------------- primitives
def test_contrast_ratio_extremes() -> None:
    assert contrast_ratio("#000000", "#ffffff") == pytest.approx(21.0, abs=0.01)
    assert contrast_ratio("#123456", "#123456") == pytest.approx(1.0, abs=0.001)


def test_hex_roundtrip_and_shorthand() -> None:
    assert rgb_to_hex(hex_to_rgb("#bd93f9")) == "#bd93f9"
    assert hex_to_rgb("#fff") == (255, 255, 255)
    with pytest.raises(ValueError):
        hex_to_rgb("nope")


def test_relative_luminance_is_ordered() -> None:
    assert relative_luminance("#000000") < relative_luminance("#808080")
    assert relative_luminance("#808080") < relative_luminance("#ffffff")


def test_mix_endpoints_and_midpoint() -> None:
    assert mix("#000000", "#ffffff", 0.0) == "#000000"
    assert mix("#000000", "#ffffff", 1.0) == "#ffffff"
    assert mix("#000000", "#ffffff", 0.5) == "#808080"


def test_ensure_readable_lifts_a_failing_colour() -> None:
    # Dracula's published `muted` fails against its own background…
    assert contrast_ratio("#6272a4", "#282a36") < AA_NORMAL
    # …and the fixer brings it over the line without changing it needlessly.
    fixed = ensure_readable("#6272a4", "#282a36")
    assert contrast_ratio(fixed, "#282a36") >= AA_NORMAL
    assert ensure_readable("#ffffff", "#000000") == "#ffffff"


# ------------------------------------------------------------------ palettes
@pytest.mark.parametrize("palette", ALL_PALETTES, ids=PALETTE_IDS)
def test_every_text_role_passes_aa_on_every_surface(palette: Palette) -> None:
    tokens = palette.tokens()
    failures: list[str] = []
    for role in TEXT_ROLE_TOKENS:
        for surface in SURFACE_ROLE_TOKENS:
            fg = tokens[f"{role}_on_{surface}"]
            bg = tokens[surface]
            ratio = contrast_ratio(fg, bg)
            if ratio < AA_NORMAL:
                failures.append(f"{role} on {surface}: {ratio:.2f}")
    assert not failures, f"{palette.key} fails AA for {failures}"


@pytest.mark.parametrize("palette", ALL_PALETTES, ids=PALETTE_IDS)
def test_derived_ui_pairs_pass_aa(palette: Palette) -> None:
    tokens = palette.tokens()
    failures: list[str] = []
    for fg, bg in EXTRA_PAIRS:
        ratio = contrast_ratio(tokens[fg], tokens[bg])
        if ratio < AA_NORMAL:
            failures.append(f"{fg} on {bg}: {ratio:.2f}")
    assert not failures, f"{palette.key} fails AA for {failures}"


@pytest.mark.parametrize("palette", ALL_PALETTES, ids=PALETTE_IDS)
def test_muted_is_corrected_when_the_upstream_value_is_too_dim(palette: Palette) -> None:
    tokens = palette.tokens()
    assert contrast_ratio(tokens["muted_on_bg"], palette.bg) >= AA_NORMAL


@pytest.mark.parametrize("palette", ALL_PALETTES, ids=PALETTE_IDS)
def test_print_variant_is_light_and_readable(palette: Palette) -> None:
    paper = print_variant(palette)
    assert not paper.is_dark
    assert paper.bg == "#ffffff"
    tokens = paper.tokens()
    for role in TEXT_ROLE_TOKENS:
        assert contrast_ratio(tokens[f"{role}_on_bg"], paper.bg) >= AA_NORMAL


@pytest.mark.parametrize("palette", ALL_PALETTES, ids=PALETTE_IDS)
def test_palette_tokens_are_all_valid_colours(palette: Palette) -> None:
    for name, value in palette.tokens().items():
        if name in {"is_dark", "color_scheme"} or value.startswith("rgba"):
            continue
        assert value.startswith("#") and len(value) == 7, f"{name} = {value!r}"
        hex_to_rgb(value)


def test_all_six_themes_are_present() -> None:
    assert list(PALETTES) == [
        "dracula",
        "clean-light",
        "catppuccin-latte",
        "catppuccin-frappe",
        "rose-pine-dawn",
        "nord",
    ]
    assert sum(1 for p in ALL_PALETTES if p.is_dark) == 3
