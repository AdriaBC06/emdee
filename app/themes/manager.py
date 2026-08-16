# SPDX-License-Identifier: GPL-3.0-or-later
"""Hot-swappable theme application.

One call to :meth:`ThemeManager.apply` repaints the entire application: the QSS
of every widget, the Qt palette used by native dialogs, the icon tint, the
editor highlighter colours and the preview stylesheet — all derived from the
same tokens in :mod:`app.themes.palettes`.
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path

from PyQt6.QtCore import QObject, QStandardPaths, pyqtSignal
from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import QApplication

from .palettes import DEFAULT_THEME, PALETTES, Palette, get_palette
from .preview_css import build_preview_css
from .qss_template import build_qss

log = logging.getLogger(__name__)

__all__ = ["ThemeManager"]


class ThemeManager(QObject):
    """Owns the active palette and pushes it into every consumer."""

    #: Emitted after the new stylesheet has been installed.
    theme_changed = pyqtSignal(str)

    def __init__(self, app: QApplication, theme: str = DEFAULT_THEME) -> None:
        super().__init__(app)
        self._app = app
        self._key = theme if theme in PALETTES else DEFAULT_THEME
        self._ui_font_size = 13
        self._fallback_cache: Path | None = None

    # ---------------------------------------------------------------- state
    @property
    def key(self) -> str:
        return self._key

    @property
    def palette(self) -> Palette:
        return get_palette(self._key)

    @property
    def tokens(self) -> dict[str, str]:
        return self.palette.tokens()

    # ---------------------------------------------------------- icon colours
    @property
    def icon_color(self) -> str:
        """Default tint for toolbar and sidebar icons."""
        return self.tokens["muted_on_bg_alt"]

    @property
    def icon_active_color(self) -> str:
        """Tint for hovered / checked icons."""
        return self.tokens["text"]

    # --------------------------------------------------------------- applying
    def _cache_dir(self) -> Path:
        """Private directory for the recoloured stylesheet icons.

        Never falls back to a shared, world-writable location: a predictable
        filename in ``/tmp`` is a symlink-attack invitation.  If Qt cannot give
        us a cache location we make a private temporary directory instead.
        """
        base = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.CacheLocation)
        if base:
            path = Path(base) / "themed-icons"
            path.mkdir(parents=True, exist_ok=True)
            return path
        if self._fallback_cache is None:
            self._fallback_cache = Path(tempfile.mkdtemp(prefix="emdee-icons-"))
        return self._fallback_cache

    def _stylesheet_icon(self, name: str, color: str) -> str:
        """Write a recoloured icon to the cache and return its path.

        QSS cannot tint an SVG, so widgets whose sub-controls are styled purely
        through the stylesheet — the checkbox tick, the tree branch arrows —
        need one real file per theme.
        """
        from ..ui.icons import write_recolored_svg

        try:
            target = self._cache_dir() / f"{name}-{self._key}.svg"
            write_recolored_svg(name, color, target)
            return str(target).replace("\\", "/")
        except OSError:  # pragma: no cover - the control just loses its glyph
            log.warning("could not write the themed %s icon", name)
            return ""

    def _apply_qt_palette(self) -> None:
        """Keep native dialogs (file pickers, message boxes) on-theme."""
        t = self.tokens
        qp = QPalette()
        qp.setColor(QPalette.ColorRole.Window, QColor(t["bg"]))
        qp.setColor(QPalette.ColorRole.WindowText, QColor(t["text"]))
        qp.setColor(QPalette.ColorRole.Base, QColor(t["bg_alt"]))
        qp.setColor(QPalette.ColorRole.AlternateBase, QColor(t["surface"]))
        qp.setColor(QPalette.ColorRole.Text, QColor(t["text"]))
        qp.setColor(QPalette.ColorRole.Button, QColor(t["surface"]))
        qp.setColor(QPalette.ColorRole.ButtonText, QColor(t["text_on_surface"]))
        qp.setColor(QPalette.ColorRole.BrightText, QColor(t["red"]))
        qp.setColor(QPalette.ColorRole.Highlight, QColor(t["accent"]))
        qp.setColor(QPalette.ColorRole.HighlightedText, QColor(t["accent_contrast"]))
        qp.setColor(QPalette.ColorRole.ToolTipBase, QColor(t["bg_alt"]))
        qp.setColor(QPalette.ColorRole.ToolTipText, QColor(t["text"]))
        qp.setColor(QPalette.ColorRole.Link, QColor(t["accent_on_bg"]))
        qp.setColor(QPalette.ColorRole.PlaceholderText, QColor(t["muted_on_bg"]))
        disabled = QPalette.ColorGroup.Disabled
        qp.setColor(disabled, QPalette.ColorRole.Text, QColor(t["muted_on_bg"]))
        qp.setColor(disabled, QPalette.ColorRole.ButtonText, QColor(t["muted_on_surface"]))
        qp.setColor(disabled, QPalette.ColorRole.WindowText, QColor(t["muted_on_bg"]))
        self._app.setPalette(qp)

    def apply(self, theme: str | None = None, *, ui_font_size: int | None = None) -> None:
        """Switch to ``theme`` and restyle everything, without restarting."""
        from ..ui.icons import clear_cache

        if theme is not None:
            self._key = theme if theme in PALETTES else DEFAULT_THEME
        if ui_font_size is not None:
            self._ui_font_size = max(10, min(20, int(ui_font_size)))

        clear_cache()
        self._apply_qt_palette()
        muted = self.tokens["muted_on_bg_alt"]
        qss = build_qss(
            self.palette,
            ui_font_size=self._ui_font_size,
            check_icon=self._stylesheet_icon("check", self.palette.contrast_color),
            branch_closed_icon=self._stylesheet_icon("chevron-right", muted),
            branch_open_icon=self._stylesheet_icon("chevron-down", muted),
        )
        self._app.setStyleSheet(qss)
        log.debug("applied theme %s", self._key)
        self.theme_changed.emit(self._key)

    # ---------------------------------------------------------------- preview
    def preview_css(self, font_size: int = 16, for_print: bool = False) -> str:
        """Stylesheet for the HTML preview / export in the current theme."""
        return build_preview_css(
            self.palette, body_font_size=font_size, for_print=for_print
        )
