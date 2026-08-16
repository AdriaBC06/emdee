#!/usr/bin/env python3
# Emdee — a themeable Markdown editor for Linux.
# Copyright (C) 2026 Adrià Bonnin Catalán
# SPDX-License-Identifier: GPL-3.0-or-later
"""Regenerate every raster icon from the hand-written SVG sources.

    python tools/build_icons.py            # write the hicolor tree and the .ico
    python tools/build_icons.py --check    # also build a legibility contact sheet

Two outputs, from one set of sources:

* the freedesktop *hicolor* layout, which ``install.sh`` copies into
  ``~/.local/share/icons``;
* ``packaging/windows/emdee.ico``, the multi-resolution icon that the Windows
  executable, its Start-menu shortcut and the ``.md`` file association all use.

Rasterisation uses whichever backend is available, preferring Qt — PyQt6 is
already a hard dependency of the application, so it is the one backend that is
guaranteed present on every machine that can run Emdee, and it needs no native
libraries of its own.  ``cairosvg`` needs Cairo's DLLs, and ``rsvg-convert`` and
``inkscape`` essentially do not exist on Windows.
"""

from __future__ import annotations

import argparse
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCE_DIR = REPO_ROOT / "app" / "resources" / "icons" / "app"
HICOLOR = REPO_ROOT / "packaging" / "icons" / "hicolor"
WINDOWS_DIR = REPO_ROOT / "packaging" / "windows"
APP_ID = "emdee"

#: Sizes required by the freedesktop icon theme spec plus the launcher sizes.
SIZES: tuple[int, ...] = (16, 24, 32, 48, 64, 128, 256, 512)

#: Sizes Windows actually looks for.  512 is deliberately absent: nothing on
#: Windows requests it, and every entry is carried inside the executable.
ICO_SIZES: tuple[int, ...] = (16, 24, 32, 48, 64, 128, 256)

#: At and below this size the full logo turns to mush, so a simplified
#: source (the M alone, drawn much heavier) is rasterised instead.  This is
#: the same trick hand-tuned icon themes use for their small buckets.
SMALL_SIZE_CUTOFF = 24


class RasteriserError(RuntimeError):
    """No usable SVG rasteriser was found on this system."""


def _qt(source: Path, target: Path, size: int) -> None:
    """Rasterise with Qt's own SVG renderer.

    Runs on the offscreen platform plugin so it needs no display, which matters
    for CI and for a headless Linux build box.
    """
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from PyQt6.QtCore import QRectF
    from PyQt6.QtGui import QColor, QGuiApplication, QImage, QPainter
    from PyQt6.QtSvg import QSvgRenderer

    if QGuiApplication.instance() is None:
        _qt.app = QGuiApplication([])  # type: ignore[attr-defined]

    image = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor(0, 0, 0, 0))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    QSvgRenderer(str(source)).render(painter, QRectF(0, 0, size, size))
    painter.end()
    if not image.save(str(target), "PNG"):
        raise RasteriserError(f"Qt could not write {target}")


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
        import PyQt6.QtSvg  # noqa: F401
    except ImportError:
        pass
    else:
        return "Qt", _qt

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
        "  pip install PyQt6            (already required to run Emdee)\n"
        "  pip install cairosvg\n"
        "  sudo pacman -S librsvg      (Arch)\n"
        "  sudo apt install librsvg2-bin  (Debian/Ubuntu)\n"
        "  sudo dnf install librsvg2-tools  (Fedora)"
    )


def build_ico(render, destination: Path) -> Path:  # type: ignore[no-untyped-def]
    """Pack the logo at every Windows size into one ``.ico``.

    The container is assembled here rather than through an imaging library so
    the script keeps working with only PyQt6 installed.  The format is not
    complicated: a six-byte header, one sixteen-byte directory entry per image,
    then the images themselves.

    Each entry holds a complete PNG rather than a raw DIB.  Windows has accepted
    PNG-compressed icon entries since Vista, it keeps the file a fraction of the
    size, and it sidesteps the DIB convention where the height field is doubled
    to account for an AND mask that modern icons do not use.
    """
    logo = SOURCE_DIR / "logo.svg"
    small = SOURCE_DIR / "logo-small.svg"
    if not logo.is_file():
        raise SystemExit(f"missing source icon: {logo}")

    destination.parent.mkdir(parents=True, exist_ok=True)

    images: list[tuple[int, bytes]] = []
    with tempfile.TemporaryDirectory(prefix="emdee-ico-") as tmp:
        for size in ICO_SIZES:
            # Same small/large split as the hicolor tree: below 24 px the full
            # logo turns to mush, and Windows shows exactly that size in the
            # taskbar and in Explorer's list view.
            source = small if size <= SMALL_SIZE_CUTOFF and small.is_file() else logo
            png = Path(tmp) / f"{size}.png"
            render(source, png, size)
            images.append((size, png.read_bytes()))

    header = struct.pack("<HHH", 0, 1, len(images))
    offset = len(header) + 16 * len(images)
    entries = bytearray()
    payload = bytearray()
    for size, data in images:
        entries += struct.pack(
            "<BBBBHHII",
            0 if size >= 256 else size,  # 0 means 256 in a single byte
            0 if size >= 256 else size,
            0,      # palette entries: 0 for a truecolour image
            0,      # reserved
            1,      # colour planes
            32,     # bits per pixel
            len(data),
            offset,
        )
        payload += data
        offset += len(data)

    destination.write_bytes(header + bytes(entries) + bytes(payload))
    return destination


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
    parser.add_argument(
        "--ico",
        type=Path,
        default=WINDOWS_DIR / f"{APP_ID}.ico",
        help="where to write the Windows icon",
    )
    parser.add_argument(
        "--only",
        choices=("hicolor", "ico"),
        help=(
            "build just one platform's icons. The Windows build uses --only ico "
            "so that packaging for one platform never rewrites the other's "
            "committed assets — rasterisers differ in their antialiasing, so "
            "regenerating them produces a diff of changed bytes and no changed "
            "pixels worth having."
        ),
    )
    args = parser.parse_args(argv)

    name, render = pick_backend()
    print(f"rasterising with {name}")

    written: list[Path] = []
    if args.only != "ico":
        written += build_hicolor(render)
    if args.only != "hicolor":
        written.append(build_ico(render, args.ico))
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
