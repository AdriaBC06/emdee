#!/usr/bin/env python3
# Emdee — a themeable Markdown editor for Linux.
# Copyright (C) 2026 Adrià Bonnin Catalán
# SPDX-License-Identifier: GPL-3.0-or-later
"""Regenerate every raster icon from the hand-written SVG sources.

    python tools/build_icons.py            # write the hicolor tree
    python tools/build_icons.py --check    # also build a legibility contact sheet

Rasterisation uses whichever backend is available, in order of preference:
``cairosvg`` (pure Python), ``rsvg-convert`` or ``inkscape``.  The output is the
freedesktop *hicolor* layout, which is exactly what ``install.sh`` copies into
``~/.local/share/icons``.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCE_DIR = REPO_ROOT / "app" / "resources" / "icons" / "app"
HICOLOR = REPO_ROOT / "packaging" / "icons" / "hicolor"
APP_ID = "emdee"

#: Sizes required by the freedesktop icon theme spec plus the launcher sizes.
SIZES: tuple[int, ...] = (16, 24, 32, 48, 64, 128, 256, 512)

#: At and below this size the full logo turns to mush, so a simplified
#: source (the M alone, drawn much heavier) is rasterised instead.  This is
#: the same trick hand-tuned icon themes use for their small buckets.
SMALL_SIZE_CUTOFF = 24


class RasteriserError(RuntimeError):
    """No usable SVG rasteriser was found on this system."""


def _cairosvg(source: Path, target: Path, size: int) -> None:
    import cairosvg  # type: ignore[import-untyped]

    cairosvg.svg2png(
        url=str(source),
        write_to=str(target),
        output_width=size,
        output_height=size,
    )


def _run(command: list[str]) -> None:
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise RasteriserError(f"{command[0]} failed: {result.stderr.strip()}")


def pick_backend() -> tuple[str, object]:
    """Return ``(name, render_callable)`` for the best available rasteriser."""
    try:
        import cairosvg  # noqa: F401
    except ImportError:
        pass
    else:
        return "cairosvg", _cairosvg

    if shutil.which("rsvg-convert"):
        def render(source: Path, target: Path, size: int) -> None:
            _run(
                [
                    "rsvg-convert",
                    "-w", str(size),
                    "-h", str(size),
                    str(source),
                    "-o", str(target),
                ]
            )

        return "rsvg-convert", render

    if shutil.which("inkscape"):
        def render(source: Path, target: Path, size: int) -> None:
            _run(
                [
                    "inkscape",
                    str(source),
                    f"--export-filename={target}",
                    f"--export-width={size}",
                    f"--export-height={size}",
                ]
            )

        return "inkscape", render

    raise RasteriserError(
        "No SVG rasteriser found. Install one of:\n"
        "  pip install cairosvg\n"
        "  sudo pacman -S librsvg      (Arch)\n"
        "  sudo apt install librsvg2-bin  (Debian/Ubuntu)\n"
        "  sudo dnf install librsvg2-tools  (Fedora)"
    )


def build_hicolor(render) -> list[Path]:  # type: ignore[no-untyped-def]
    """Write ``packaging/icons/hicolor/<size>/apps/emdee.png`` for every size."""
    logo = SOURCE_DIR / "logo.svg"
    if not logo.is_file():
        raise SystemExit(f"missing source icon: {logo}")

    small = SOURCE_DIR / "logo-small.svg"

    written: list[Path] = []
    for size in SIZES:
        target_dir = HICOLOR / f"{size}x{size}" / "apps"
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"{APP_ID}.png"
        source = small if size <= SMALL_SIZE_CUTOFF and small.is_file() else logo
        render(source, target, size)
        written.append(target)

    scalable = HICOLOR / "scalable" / "apps"
    scalable.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(logo, scalable / f"{APP_ID}.svg")
    written.append(scalable / f"{APP_ID}.svg")

    symbolic = HICOLOR / "symbolic" / "apps"
    symbolic.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(SOURCE_DIR / "logo-mono.svg", symbolic / f"{APP_ID}-symbolic.svg")
    written.append(symbolic / f"{APP_ID}-symbolic.svg")

    return written


def build_contact_sheet(destination: Path) -> Path | None:
    """Compose the rendered sizes side by side, magnified, for eyeballing.

    An icon that survives 16×16 is a design constraint, not a nice-to-have, so
    this sheet exists to make the small end easy to judge.
    """
    if not shutil.which("magick") and not shutil.which("montage"):
        print("! ImageMagick not found; skipping the contact sheet", file=sys.stderr)
        return None

    tool = "magick" if shutil.which("magick") else "montage"
    parts: list[str] = []
    for size in (16, 24, 32, 48, 64):
        source = HICOLOR / f"{size}x{size}" / "apps" / f"{APP_ID}.png"
        scaled = destination.parent / f".sheet-{size}.png"
        _run([tool, str(source), "-filter", "point", "-resize", "256x256", str(scaled)])
        parts.append(str(scaled))

    parts.append(str(HICOLOR / "256x256" / "apps" / f"{APP_ID}.png"))
    _run([tool, *parts, "+append", str(destination)])
    for leftover in destination.parent.glob(".sheet-*.png"):
        leftover.unlink()
    return destination


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="also build a magnified contact sheet for the legibility check",
    )
    parser.add_argument(
        "--sheet",
        type=Path,
        default=REPO_ROOT / "packaging" / "icons" / "contact-sheet.png",
        help="where to write the contact sheet",
    )
    args = parser.parse_args(argv)

    name, render = pick_backend()
    print(f"rasterising with {name}")

    written = build_hicolor(render)
    for path in written:
        print(f"  {path.relative_to(REPO_ROOT)}")

    if args.check:
        sheet = build_contact_sheet(args.sheet)
        if sheet is not None:
            print(f"\ncontact sheet: {sheet.relative_to(REPO_ROOT)}")
            print("Check the 16px rendering on the left; if it reads as a blob,")
            print("simplify logo.svg and run this again.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
