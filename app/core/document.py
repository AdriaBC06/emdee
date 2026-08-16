# SPDX-License-Identifier: GPL-3.0-or-later
"""The in-memory document model.

A :class:`Document` knows its text, where it came from and whether it differs
from what is on disk.  It is deliberately observer-based rather than Qt-signal
based so it can be exercised in tests without any Qt event loop.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from . import file_service
from .file_service import FileError
from .textops import DocumentStats, compute_stats

__all__ = ["Document", "UNTITLED_NAME"]

UNTITLED_NAME = "Untitled.md"

Observer = Callable[["Document"], None]


class Document:
    """Text buffer plus dirty-state tracking for a single file."""

    def __init__(self, text: str = "", path: Path | None = None) -> None:
        self._text = text
        self._path = path
        self._saved_text = text
        self._mtime_ns = 0
        self._had_decode_errors = False
        self._observers: list[Observer] = []

    # ------------------------------------------------------------ observers
    def subscribe(self, observer: Observer) -> None:
        """Register a callback fired whenever text, path or dirty state change."""
        self._observers.append(observer)

    def _notify(self) -> None:
        for observer in tuple(self._observers):
            observer(self)

    # --------------------------------------------------------------- state
    @property
    def text(self) -> str:
        return self._text

    @text.setter
    def text(self, value: str) -> None:
        if value != self._text:
            self._text = value
            self._notify()

    @property
    def path(self) -> Path | None:
        return self._path

    @property
    def mtime_ns(self) -> int:
        """Modification time recorded at the last successful read/write."""
        return self._mtime_ns

    @property
    def had_decode_errors(self) -> bool:
        """True when the file contained bytes that were not valid UTF-8."""
        return self._had_decode_errors

    @property
    def is_dirty(self) -> bool:
        """True when the buffer differs from the last saved content."""
        return self._text != self._saved_text

    @property
    def is_untitled(self) -> bool:
        return self._path is None

    @property
    def name(self) -> str:
        """File name, or a placeholder for a document that was never saved."""
        return self._path.name if self._path else UNTITLED_NAME

    @property
    def display_name(self) -> str:
        """File name with a bullet appended when there are unsaved changes."""
        return f"{self.name} •" if self.is_dirty else self.name

    @property
    def directory(self) -> Path | None:
        """Directory the document lives in — the base for relative images."""
        return self._path.parent if self._path else None

    def stats(self) -> DocumentStats:
        """Word / character / reading-time counters for the current text."""
        return compute_stats(self._text)

    # ------------------------------------------------------------ mutation
    def reset(self, text: str = "", path: Path | None = None, mtime_ns: int = 0) -> None:
        """Replace the whole document state and mark it as clean."""
        self._text = text
        self._saved_text = text
        self._path = path
        self._mtime_ns = mtime_ns
        self._had_decode_errors = False
        self._notify()

    def mark_saved(self, path: Path | None = None, mtime_ns: int = 0) -> None:
        """Record the current text as the on-disk content."""
        if path is not None:
            self._path = path
        self._saved_text = self._text
        self._mtime_ns = mtime_ns
        self._notify()

    # ------------------------------------------------------------------ io
    def load(self, path: Path) -> None:
        """Read ``path`` into the document.  Raises :class:`FileError`."""
        loaded = file_service.read_text(path)
        self._text = loaded.text
        self._saved_text = loaded.text
        self._path = loaded.path
        self._mtime_ns = loaded.mtime_ns
        self._had_decode_errors = loaded.had_decode_errors
        self._notify()

    def save(self, path: Path | None = None) -> Path:
        """Write the document to disk atomically and return the target path."""
        target = path or self._path
        if target is None:
            raise FileError("This document has no location yet — use “Save As”.")
        mtime = file_service.write_text_atomic(target, self._text)
        self.mark_saved(target, mtime)
        return target

    def changed_on_disk(self) -> bool:
        """True when the file's mtime moved since we last read or wrote it."""
        if self._path is None or self._mtime_ns == 0:
            return False
        try:
            return self._path.stat().st_mtime_ns != self._mtime_ns
        except OSError:
            return False
