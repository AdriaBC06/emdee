# SPDX-License-Identifier: GPL-3.0-or-later
"""Filesystem access with friendly errors and crash-safe writes.

Two rules are enforced here:

* Reads and writes are UTF-8, and every failure mode (missing file, permission
  denied, undecodable bytes, disk full) is turned into a :class:`FileError`
  carrying a message that can be shown to a human as-is.
* Writes are atomic: the content goes to a temporary file in the same directory
  and is then ``os.replace``-d over the target, so an interrupted save can never
  leave a truncated document behind.
"""

from __future__ import annotations

import errno
import logging
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

__all__ = [
    "FileError",
    "LoadedFile",
    "MARKDOWN_SUFFIXES",
    "MARKDOWN_GLOBS",
    "read_text",
    "write_text_atomic",
    "is_markdown",
    "unique_path",
    "write_bytes_atomic",
]

#: Extensions Emdee is willing to open.
MARKDOWN_SUFFIXES: tuple[str, ...] = (".md", ".markdown", ".mdown", ".mkd", ".mdx", ".txt")
MARKDOWN_GLOBS: tuple[str, ...] = tuple(f"*{suffix}" for suffix in MARKDOWN_SUFFIXES)


class FileError(Exception):
    """A filesystem problem already phrased for the user interface."""

    def __init__(self, message: str, *, detail: str = "", path: Path | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail
        self.path = path


@dataclass(frozen=True)
class LoadedFile:
    """Result of reading a document from disk."""

    path: Path
    text: str
    encoding: str
    had_decode_errors: bool = False
    mtime_ns: int = 0


def is_markdown(path: Path | str) -> bool:
    """True when the path has one of the supported extensions."""
    return Path(path).suffix.lower() in MARKDOWN_SUFFIXES


def _describe_oserror(exc: OSError, path: Path, action: str) -> FileError:
    name = path.name or str(path)
    if exc.errno == errno.ENOENT:
        message = f"“{name}” no longer exists on disk."
    elif exc.errno == errno.EACCES:
        message = f"Permission denied while trying to {action} “{name}”."
    elif exc.errno == errno.EISDIR:
        message = f"“{name}” is a directory, not a file."
    elif exc.errno == errno.ENOSPC:
        message = "There is no space left on the device."
    elif exc.errno == errno.EROFS:
        message = f"“{name}” lives on a read-only filesystem."
    else:
        message = f"Could not {action} “{name}”."
    return FileError(message, detail=str(exc), path=path)


def read_text(path: Path | str) -> LoadedFile:
    """Read a document as UTF-8, degrading gracefully on invalid bytes.

    Raises :class:`FileError` for anything the user needs to know about.
    """
    target = Path(path)
    try:
        raw = target.read_bytes()
        stat = target.stat()
    except OSError as exc:
        raise _describe_oserror(exc, target, "open") from exc

    try:
        text = raw.decode("utf-8")
        had_errors = False
    except UnicodeDecodeError:
        log.warning("invalid UTF-8 in %s, falling back to replacement characters", target)
        text = raw.decode("utf-8", errors="replace")
        had_errors = True

    # Normalise line endings; the editor always works with "\n".
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return LoadedFile(
        path=target,
        text=text,
        encoding="utf-8",
        had_decode_errors=had_errors,
        mtime_ns=stat.st_mtime_ns,
    )


def write_text_atomic(path: Path | str, text: str, *, ensure_newline: bool = True) -> int:
    """Write ``text`` to ``path`` atomically and return the new ``mtime_ns``.

    The temporary file is created in the destination directory so the final
    ``os.replace`` stays on the same filesystem and is therefore atomic.

    ``os.replace`` acts on the path itself, never on a symlink's target.  That
    is the safe behaviour — a document that is a symlink cannot be used to
    write through to a file elsewhere — but it does mean saving a symlinked
    document replaces the link with a regular file.
    """
    target = Path(path)
    directory = target.parent
    payload = text
    if ensure_newline and payload and not payload.endswith("\n"):
        payload += "\n"

    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise _describe_oserror(exc, target, "save") from exc

    mode: int | None = None
    try:
        mode = target.stat().st_mode & 0o777
    except OSError:
        mode = None

    tmp_path: Path | None = None
    try:
        fd, tmp_name = tempfile.mkstemp(
            prefix=f".{target.name}.", suffix=".tmp", dir=str(directory)
        )
        tmp_path = Path(tmp_name)
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        if mode is not None:
            os.chmod(tmp_path, mode)
        os.replace(tmp_path, target)
        tmp_path = None
    except OSError as exc:
        raise _describe_oserror(exc, target, "save") from exc
    finally:
        if tmp_path is not None and tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:  # pragma: no cover - best effort cleanup
                log.debug("could not remove temporary file %s", tmp_path)

    try:
        return target.stat().st_mtime_ns
    except OSError:  # pragma: no cover - the write succeeded, stat is a bonus
        return 0


def write_bytes_atomic(path: Path | str, data: bytes) -> None:
    """Binary sibling of :func:`write_text_atomic` (used for pasted images)."""
    target = Path(path)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent)
        )
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, target)
    except OSError as exc:
        raise _describe_oserror(exc, target, "save") from exc


def unique_path(directory: Path, stem: str, suffix: str) -> Path:
    """Return ``directory/stem+suffix``, adding ``-1``, ``-2``… if taken."""
    candidate = directory / f"{stem}{suffix}"
    counter = 1
    while candidate.exists():
        candidate = directory / f"{stem}-{counter}{suffix}"
        counter += 1
    return candidate
