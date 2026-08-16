# SPDX-License-Identifier: GPL-3.0-or-later
"""Typed wrapper around :class:`QSettings`.

``QSettings`` returns strings for everything on the INI/native backends, which
makes booleans and integers a constant source of bugs.  This module centralises
the coercion and the defaults, and is the only place that knows the storage key
names.  It imports ``QtCore`` only — never ``QtWidgets`` — so it stays usable
from tests.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from enum import StrEnum
from pathlib import Path
from typing import Any

from PyQt6.QtCore import QByteArray, QSettings

from .. import APP_ID, APP_ORG
from ..platform_support import IS_WINDOWS

log = logging.getLogger(__name__)

__all__ = ["Settings", "ViewMode", "MAX_RECENT_FILES", "default_backend"]

MAX_RECENT_FILES: int = 10


class ViewMode(StrEnum):
    """The three switchable layouts."""

    EDITOR = "editor"
    SPLIT = "split"
    PREVIEW = "preview"

    @classmethod
    def coerce(cls, value: object, default: ViewMode = None) -> ViewMode:  # type: ignore[assignment]
        try:
            return cls(str(value))
        except ValueError:
            return default or cls.SPLIT


DEFAULTS: dict[str, Any] = {
    "theme": "dracula",
    "view_mode": ViewMode.SPLIT.value,
    "editor_font_size": 14,
    "preview_font_size": 16,
    "tab_width": 4,
    "word_wrap": True,
    "show_line_numbers": True,
    "sidebar_expanded": True,
    "native_decorations": False,
    "sync_scroll": True,
    "last_folder": "",
    "recent_files": [],
    "first_run_done": False,
    "split_sizes": [],
}


def _as_bool(value: object, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    text = str(value).strip().lower()
    if text in {"true", "1", "yes", "on"}:
        return True
    if text in {"false", "0", "no", "off"}:
        return False
    return default


def _as_int(value: object, default: int) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def default_backend() -> QSettings:
    """Open the preferences store for this platform.

    ``QSettings``'s native backend means an INI file under ``$XDG_CONFIG_HOME``
    on Linux but the *registry* on Windows, under
    ``HKCU\\Software\\Emdee\\emdee``.  Emdee asks for a file on both: it keeps
    the About dialog's "stored in" line meaningful, it lets a user copy their
    preferences between machines, and it is what makes a portable build
    possible at all — a registry key would leak out of the folder the user
    unpacked and follow them around after they deleted it.
    """
    if IS_WINDOWS:
        # %APPDATA%\Emdee\emdee.ini
        return QSettings(
            QSettings.Format.IniFormat, QSettings.Scope.UserScope, APP_ORG, APP_ID
        )
    return QSettings(APP_ORG, APP_ID)


class Settings:
    """Persistent application preferences."""

    def __init__(self, backend: QSettings | None = None) -> None:
        self._qs = backend or default_backend()

    # ----------------------------------------------------------- primitives
    def _get(self, key: str) -> Any:
        return self._qs.value(key, DEFAULTS.get(key))

    def _set(self, key: str, value: Any) -> None:
        self._qs.setValue(key, value)

    def sync(self) -> None:
        """Flush pending writes to disk."""
        self._qs.sync()

    @property
    def filename(self) -> str:
        """Where the settings are actually stored (shown in the About dialog)."""
        return self._qs.fileName()

    # -------------------------------------------------------------- theming
    @property
    def theme(self) -> str:
        return str(self._get("theme") or DEFAULTS["theme"])

    @theme.setter
    def theme(self, value: str) -> None:
        self._set("theme", value)

    # ---------------------------------------------------------------- view
    @property
    def view_mode(self) -> ViewMode:
        return ViewMode.coerce(self._get("view_mode"), ViewMode.SPLIT)

    @view_mode.setter
    def view_mode(self, value: ViewMode | str) -> None:
        self._set("view_mode", ViewMode.coerce(value).value)

    @property
    def split_sizes(self) -> list[int]:
        raw = self._get("split_sizes") or []
        if isinstance(raw, str):
            raw = [part for part in raw.split(",") if part]
        return [_as_int(part, 0) for part in raw]

    @split_sizes.setter
    def split_sizes(self, value: Iterable[int]) -> None:
        self._set("split_sizes", [int(v) for v in value])

    # --------------------------------------------------------------- editor
    @property
    def editor_font_size(self) -> int:
        return max(8, min(40, _as_int(self._get("editor_font_size"), 14)))

    @editor_font_size.setter
    def editor_font_size(self, value: int) -> None:
        self._set("editor_font_size", max(8, min(40, int(value))))

    @property
    def preview_font_size(self) -> int:
        return max(10, min(32, _as_int(self._get("preview_font_size"), 16)))

    @preview_font_size.setter
    def preview_font_size(self, value: int) -> None:
        self._set("preview_font_size", max(10, min(32, int(value))))

    @property
    def tab_width(self) -> int:
        return max(2, min(8, _as_int(self._get("tab_width"), 4)))

    @tab_width.setter
    def tab_width(self, value: int) -> None:
        self._set("tab_width", max(2, min(8, int(value))))

    @property
    def word_wrap(self) -> bool:
        return _as_bool(self._get("word_wrap"), True)

    @word_wrap.setter
    def word_wrap(self, value: bool) -> None:
        self._set("word_wrap", bool(value))

    @property
    def show_line_numbers(self) -> bool:
        return _as_bool(self._get("show_line_numbers"), True)

    @show_line_numbers.setter
    def show_line_numbers(self, value: bool) -> None:
        self._set("show_line_numbers", bool(value))

    @property
    def sync_scroll(self) -> bool:
        return _as_bool(self._get("sync_scroll"), True)

    @sync_scroll.setter
    def sync_scroll(self, value: bool) -> None:
        self._set("sync_scroll", bool(value))

    # --------------------------------------------------------------- window
    @property
    def sidebar_expanded(self) -> bool:
        return _as_bool(self._get("sidebar_expanded"), True)

    @sidebar_expanded.setter
    def sidebar_expanded(self, value: bool) -> None:
        self._set("sidebar_expanded", bool(value))

    @property
    def native_decorations(self) -> bool:
        """Use the window manager's title bar instead of the custom one."""
        return _as_bool(self._get("native_decorations"), False)

    @native_decorations.setter
    def native_decorations(self, value: bool) -> None:
        self._set("native_decorations", bool(value))

    @property
    def geometry(self) -> QByteArray | None:
        value = self._qs.value("geometry")
        return value if isinstance(value, QByteArray) else None

    @geometry.setter
    def geometry(self, value: QByteArray) -> None:
        self._set("geometry", value)

    # ---------------------------------------------------------------- files
    @property
    def last_folder(self) -> Path | None:
        raw = str(self._get("last_folder") or "")
        if not raw:
            return None
        path = Path(raw)
        return path if path.is_dir() else None

    @last_folder.setter
    def last_folder(self, value: Path | str | None) -> None:
        self._set("last_folder", str(value) if value else "")

    @property
    def recent_files(self) -> list[Path]:
        raw = self._get("recent_files") or []
        if isinstance(raw, str):
            raw = [part for part in raw.split("\n") if part]
        out: list[Path] = []
        for item in raw:
            path = Path(str(item))
            if path not in out:
                out.append(path)
        return out[:MAX_RECENT_FILES]

    @recent_files.setter
    def recent_files(self, value: Iterable[Path | str]) -> None:
        self._set("recent_files", [str(path) for path in value][:MAX_RECENT_FILES])

    def push_recent_file(self, path: Path | str) -> list[Path]:
        """Move ``path`` to the front of the recent list and persist it."""
        target = Path(path)
        items = [item for item in self.recent_files if item != target]
        items.insert(0, target)
        self.recent_files = items
        return items[:MAX_RECENT_FILES]

    def prune_recent_files(self) -> list[Path]:
        """Drop entries that no longer exist and persist the cleaned list."""
        items = [path for path in self.recent_files if path.is_file()]
        self.recent_files = items
        return items

    def clear_recent_files(self) -> None:
        self.recent_files = []

    # ------------------------------------------------------------ first run
    @property
    def first_run_done(self) -> bool:
        return _as_bool(self._get("first_run_done"), False)

    @first_run_done.setter
    def first_run_done(self, value: bool) -> None:
        self._set("first_run_done", bool(value))
