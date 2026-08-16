# SPDX-License-Identifier: GPL-3.0-or-later
"""The About dialog: version, licence and credits."""

from __future__ import annotations

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from .. import APP_NAME, APP_REPO, APP_TAGLINE, APP_VERSION
from .icons import logo_pixmap

__all__ = ["AboutDialog"]

_CREDITS = """
<p><b>Built with</b><br>
Python · PyQt6 · QtWebEngine · markdown-it-py · Pygments</p>

<p><b>Interface</b><br>
Shell inspired by <a href="https://github.com/Wanderson-Magalhaes/Modern_GUI_PyDracula_PySide6_or_PyQt6">PyDracula</a>
by Wanderson M. Pimenta (MIT).<br>
Icons in the style of <a href="https://feathericons.com">Feather</a> / <a href="https://lucide.dev">Lucide</a> (MIT).</p>

<p><b>Palettes</b><br>
<a href="https://draculatheme.com">Dracula</a> ·
<a href="https://catppuccin.com">Catppuccin</a> ·
<a href="https://rosepinetheme.com">Rosé Pine</a> ·
<a href="https://www.nordtheme.com">Nord</a></p>
"""


class AboutDialog(QDialog):
    """Small modal window describing the application."""

    def __init__(self, settings_path: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"About {APP_NAME}")
        self.setMinimumWidth(460)
        self.setModal(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 18)
        layout.setSpacing(14)

        header = QHBoxLayout()
        header.setSpacing(16)

        logo = QLabel()
        logo.setPixmap(logo_pixmap(160))
        logo.setFixedSize(80, 80)
        logo.setScaledContents(True)
        header.addWidget(logo, 0, Qt.AlignmentFlag.AlignTop)

        heading = QVBoxLayout()
        heading.setSpacing(2)
        name = QLabel(APP_NAME)
        name.setObjectName("emptyTitle")
        tagline = QLabel(APP_TAGLINE)
        tagline.setObjectName("hintLabel")
        tagline.setWordWrap(True)
        version = QLabel(f"Version {APP_VERSION} · GPL-3.0-or-later")
        version.setObjectName("hintLabel")
        repo = QLabel(f'<a href="{APP_REPO}">{APP_REPO}</a>')
        repo.setOpenExternalLinks(True)
        repo.setObjectName("hintLabel")
        for widget in (name, tagline, version, repo):
            heading.addWidget(widget)
        heading.addStretch(1)
        header.addLayout(heading, 1)
        layout.addLayout(header)

        licence = QLabel(
            f"{APP_NAME} is free software: you can redistribute it and/or modify it "
            "under the terms of the GNU General Public License as published by the "
            "Free Software Foundation, either version 3 of the License, or (at your "
            "option) any later version. It comes with ABSOLUTELY NO WARRANTY."
        )
        licence.setWordWrap(True)
        licence.setObjectName("hintLabel")
        layout.addWidget(licence)

        credits = QLabel(_CREDITS.strip())
        credits.setWordWrap(True)
        credits.setOpenExternalLinks(True)
        credits.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(credits)

        if settings_path:
            location = QLabel(f"Settings file: {settings_path}")
            location.setObjectName("hintLabel")
            location.setWordWrap(True)
            location.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            layout.addWidget(location)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)

    @staticmethod
    def open_repository() -> None:
        QDesktopServices.openUrl(QUrl(APP_REPO))
