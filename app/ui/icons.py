# SPDX-License-Identifier: GPL-3.0-or-later
"""Theme-aware SVG icon loading.

PyQt6 has no ``pyrcc6``, so the ``.qrc`` machinery of the original PyDracula
template was removed entirely.  Icons are plain SVG files on disk whose
``currentColor`` placeholder is substituted with the active theme colour before
rasterisation, which is what lets a single icon set serve six themes.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

from PyQt6.QtCore import QByteArray, QRect, QRectF, QSize, Qt
from PyQt6.QtGui import QIcon, QIconEngine, QPainter, QPixmap
from PyQt6.QtSvg import QSvgRenderer

from ..paths import app_logo, icon_path

log = logging.getLogger(__name__)

__all__ = ["themed_icon", "themed_pixmap", "app_icon", "write_recolored_svg", "clear_cache"]

_DEFAULT_SIZE = 24


@lru_cache(maxsize=256)
def _svg_source(name: str) -> str:
    path = icon_path(name)
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        log.warning("missing icon: %s", path)
        return (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
            '<rect x="4" y="4" width="16" height="16" rx="3" fill="none" '
            'stroke="currentColor" stroke-width="2"/></svg>'
        )


def _colorize(source: str, color: str) -> bytes:
    return source.replace("currentColor", color).encode("utf-8")


@lru_cache(maxsize=1024)
def themed_pixmap(name: str, color: str, size: int = _DEFAULT_SIZE, dpr: float = 1.0) -> QPixmap:
    """Rasterise an icon in ``color`` at ``size`` logical pixels."""
    data = QByteArray(_colorize(_svg_source(name), color))
    renderer = QSvgRenderer(data)
    physical = max(1, int(round(size * dpr)))
    pixmap = QPixmap(physical, physical)
    pixmap.setDevicePixelRatio(dpr)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    renderer.render(painter, QRectF(0, 0, physical, physical))
    painter.end()
    return pixmap


class _SvgIconEngine(QIconEngine):
    """Rasterises the SVG at whatever size and ratio Qt asks for.

    Baking a couple of fixed device pixel ratios into a :class:`QIcon` and
    letting it scale the nearest one defeats the point of shipping vectors:
    Qt 6 passes fractional scale factors through unrounded, so on a desktop at
    125%, 150% or 175% every icon becomes a resampled bitmap — and an 18 px
    icon at 1.25 lands on a 23 px raster inside a 22 px box, which reads as the
    icon being out of proportion rather than merely soft.
    """

    def __init__(self, name: str, color: str, disabled_color: str | None = None) -> None:
        super().__init__()
        self._name = name
        self._color = color
        self._disabled_color = disabled_color or color

    def _color_for(self, mode: QIcon.Mode) -> str:
        return self._disabled_color if mode == QIcon.Mode.Disabled else self._color

    def scaledPixmap(  # noqa: N802 - Qt API
        self, size: QSize, mode: QIcon.Mode, state: QIcon.State, scale: float
    ) -> QPixmap:
        edge = max(1, min(size.width(), size.height()))
        return themed_pixmap(self._name, self._color_for(mode), edge, float(scale))

    def pixmap(  # noqa: N802 - Qt API
        self, size: QSize, mode: QIcon.Mode, state: QIcon.State
    ) -> QPixmap:
        return self.scaledPixmap(size, mode, state, 1.0)

    def paint(
        self, painter: QPainter, rect: QRect, mode: QIcon.Mode, state: QIcon.State
    ) -> None:
        device = painter.device()
        ratio = device.devicePixelRatioF() if device is not None else 1.0
        painter.drawPixmap(rect, self.scaledPixmap(rect.size(), mode, state, ratio))

    def availableSizes(  # noqa: N802 - Qt API
        self, mode: QIcon.Mode = QIcon.Mode.Normal, state: QIcon.State = QIcon.State.Off
    ) -> list[QSize]:
        return [QSize(edge, edge) for edge in (16, 18, 20, 24, 32, 48, 64)]

    def clone(self) -> QIconEngine:
        return _SvgIconEngine(self._name, self._color, self._disabled_color)


@lru_cache(maxsize=512)
def themed_icon(
    name: str,
    color: str,
    size: int = _DEFAULT_SIZE,
    disabled_color: str | None = None,
) -> QIcon:
    """Return a :class:`QIcon` tinted for the current theme.

    The icon renders itself from the SVG at the ratio the screen is actually
    using, so it stays crisp at fractional UI scaling as well as at 1x and 2x.
    ``size`` is kept in the signature — and in the cache key — because callers
    pass the logical size they will draw at, but the engine honours whatever
    size Qt requests at paint time.
    """
    del size  # the engine renders at the requested size
    return QIcon(_SvgIconEngine(name, color, disabled_color))


#: Below this size the full logo is replaced by the simplified glyph.
SMALL_ICON_CUTOFF = 24


@lru_cache(maxsize=8)
def app_icon(mono: bool = False) -> QIcon:
    """The application logo as a multi-resolution :class:`QIcon`.

    The 16 and 24 px entries come from the simplified source, which is the same
    split the generated hicolor theme uses.
    """
    icon = QIcon()
    try:
        full = QSvgRenderer(str(app_logo(mono=mono)))
        small = QSvgRenderer(str(app_logo(mono=mono, small=True)))
    except Exception:  # pragma: no cover - defensive
        log.exception("could not load the application logo")
        return icon
    for size in (16, 24, 32, 48, 64, 128, 256, 512):
        renderer = small if size <= SMALL_ICON_CUTOFF and not mono else full
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        renderer.render(painter, QRectF(0, 0, size, size))
        painter.end()
        icon.addPixmap(pixmap)
    return icon


def logo_pixmap(size: int, mono: bool = False) -> QPixmap:
    """Rasterise the application logo at an exact size."""
    renderer = QSvgRenderer(str(app_logo(mono=mono)))
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    renderer.render(painter, QRectF(0, 0, size, size))
    painter.end()
    return pixmap


def write_recolored_svg(name: str, color: str, destination: Path) -> Path:
    """Write a recoloured copy of an icon to disk.

    QSS cannot tint an SVG, so widgets styled purely through the stylesheet (the
    checkbox tick, for instance) need a real file per theme.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(_colorize(_svg_source(name), color))
    return destination


def clear_cache() -> None:
    """Drop cached pixmaps — used when the theme changes."""
    themed_pixmap.cache_clear()
    themed_icon.cache_clear()
