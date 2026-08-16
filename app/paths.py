# SPDX-License-Identifier: GPL-3.0-or-later
"""Filesystem helpers.

The project deliberately does not use Qt's ``.qrc`` resource system: ``pyrcc6``
does not exist for PyQt6, so compiled resource modules are not portable.  Every
asset is loaded from disk through :func:`resource_path`, which also understands
the temporary extraction directory used by PyInstaller one-file builds.
"""

from __future__ import annotations

import sys
from pathlib import Path

__all__ = ["APP_DIR", "PROJECT_ROOT", "resource_path", "icon_path", "app_logo"]


def _base_dir() -> Path:
    """Return the directory that contains the ``app`` package at runtime."""
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass)
    return Path(__file__).resolve().parent.parent


PROJECT_ROOT: Path = _base_dir()
APP_DIR: Path = PROJECT_ROOT / "app"


def resource_path(*parts: str) -> Path:
    """Resolve a path inside ``app/resources`` regardless of how we are run."""
    return APP_DIR.joinpath("resources", *parts)


def icon_path(name: str) -> Path:
    """Return the path of a UI icon (``name`` without the ``.svg`` suffix)."""
    return resource_path("icons", "ui", f"{name}.svg")


def app_logo(mono: bool = False, small: bool = False) -> Path:
    """Return the path of an application logo variant.

    ``small`` is the simplified glyph used at 16–24 px, where the full logo
    stops being readable; ``mono`` is the single-colour version used for the
    exported favicon and for panel/tray contexts.
    """
    if mono:
        name = "logo-mono.svg"
    elif small:
        name = "logo-small.svg"
    else:
        name = "logo.svg"
    return resource_path("icons", "app", name)
