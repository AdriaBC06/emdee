#!/usr/bin/env python3
# Emdee — a themeable Markdown editor for Linux.
# Copyright (C) 2026 Adrià Bonnin Catalán
#
# This program is free software: you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free Software
# Foundation, either version 3 of the License, or (at your option) any later
# version.
#
# This program is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE.  See the GNU General Public License for more
# details.
#
# You should have received a copy of the GNU General Public License along with
# this program.  If not, see <https://www.gnu.org/licenses/>.
"""Application entry point.

``QtWebEngineWidgets`` has to be imported before the ``QApplication`` instance
exists, otherwise Chromium's initialisation order is wrong and the process
aborts — which is why the import sits at the very top of this module and is
marked as intentionally unused.
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import signal
import sys
from pathlib import Path

from PyQt6 import QtWebEngineWidgets  # noqa: F401  (must precede QApplication)
from PyQt6.QtCore import QCoreApplication, Qt
from PyQt6.QtWidgets import QApplication

from . import APP_ID, APP_NAME, APP_ORG, APP_TAGLINE, APP_VERSION
from .core.settings import Settings
from .paths import PROJECT_ROOT
from .themes.manager import ThemeManager
from .ui.icons import app_icon
from .ui.main_window import MainWindow

log = logging.getLogger("emdee")

WELCOME_FILENAME = "WELCOME.md"


def _configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s  %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    # Chromium is chatty on stderr; keep it out of our log unless asked.
    if not verbose:
        os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--log-level=3")


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog=APP_ID,
        description=f"{APP_NAME} — {APP_TAGLINE}",
    )
    parser.add_argument(
        "path",
        nargs="?",
        help="Markdown file or folder to open on startup",
    )
    parser.add_argument(
        "--size",
        metavar="WxH",
        help="start at an exact window size, e.g. 1440x900 (for reproducible screenshots)",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="verbose logging")
    parser.add_argument(
        "--version", action="version", version=f"{APP_NAME} {APP_VERSION}"
    )
    return parser.parse_args(argv)


def _parse_size(value: str | None) -> tuple[int, int] | None:
    """Parse a ``WIDTHxHEIGHT`` argument, or return ``None`` if unusable."""
    if not value:
        return None
    match = re.fullmatch(r"\s*(\d{3,5})\s*[xX×]\s*(\d{3,5})\s*", value)
    if match is None:
        log.warning("ignoring --size %r: expected something like 1440x900", value)
        return None
    return int(match.group(1)), int(match.group(2))


def _welcome_document() -> Path | None:
    candidate = PROJECT_ROOT / WELCOME_FILENAME
    return candidate if candidate.is_file() else None


def main(argv: list[str] | None = None) -> int:
    """Create the application, restore the session and run the event loop."""
    args = _parse_args(list(argv if argv is not None else sys.argv[1:]))
    _configure_logging(args.verbose)

    QCoreApplication.setApplicationName(APP_NAME)
    QCoreApplication.setOrganizationName(APP_ORG)
    QCoreApplication.setApplicationVersion(APP_VERSION)
    QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_DontUseNativeMenuBar, True)

    app = QApplication(sys.argv[:1])
    # Matches StartupWMClass in the .desktop file so the launcher can group us.
    app.setDesktopFileName(APP_ID)
    app.setApplicationDisplayName(APP_NAME)
    app.setWindowIcon(app_icon())

    settings = Settings()
    themes = ThemeManager(app, settings.theme)
    themes.apply()

    window = MainWindow(themes, settings)
    size = _parse_size(args.size)
    if size is not None:
        window.resize(*size)
    window.show()

    target = Path(args.path).expanduser() if args.path else None
    if target is not None and target.exists():
        window.open_path(target)
    elif not settings.first_run_done:
        welcome = _welcome_document()
        if welcome is not None:
            window.open_path(welcome)
        settings.first_run_done = True

    # Let Ctrl+C in a terminal actually kill the app.
    signal.signal(signal.SIGINT, signal.SIG_DFL)

    log.info("%s %s started", APP_NAME, APP_VERSION)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
