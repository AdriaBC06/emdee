# SPDX-License-Identifier: GPL-3.0-or-later
"""Filesystem access with friendly errors and crash-safe writes.

Two rules are enforced here:

* Reads and writes are UTF-8, and every failure mode (missing file, permission
  denied, undecodable bytes, disk full) is turned into a :class:`FileError`
  carrying a message that can be shown to a human as-is.
* Writes are atomic: the content goes to a temporary file in the same directory
  and is then ``os.replace``-d over the target, so an interrupted save can never
  leave a truncated document behind.

On Windows that second rule needs help.  ``os.replace`` is refused outright if
*anything* holds a handle to the destination — an indexer, a sync client, a
virus scanner, another editor — so :func:`_replace_atomic` walks a ladder of
increasingly compromised strategies instead of giving up.  See its docstring
for the measurements behind the ordering.
"""

from __future__ import annotations

import errno
import logging
import os
import stat
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from ..platform_support import IS_WINDOWS

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


#: Win32 status codes that mean "someone else is holding this file".
#: Windows collapses every one of these onto ``EACCES``, so the errno alone
#: cannot tell a locked file apart from a genuine permissions problem — and
#: telling the user "permission denied" when the real fix is to close Word is
#: the kind of message that wastes an afternoon.
_WIN_SHARING_ERRORS: frozenset[int] = frozenset({5, 32, 33})

#: ``REPLACEFILE_IGNORE_MERGE_ERRORS`` — do not fail the whole rename just
#: because an ACL or an alternate stream could not be carried across.
_REPLACEFILE_IGNORE_MERGE_ERRORS = 0x1


def _describe_oserror(exc: OSError, path: Path, action: str) -> FileError:
    name = path.name or str(path)
    winerror = getattr(exc, "winerror", None)
    if exc.errno == errno.ENOENT:
        message = f"“{name}” no longer exists on disk."
    elif winerror in _WIN_SHARING_ERRORS:
        message = f"Another program is using “{name}”. Close it and try again."
    elif exc.errno == errno.EACCES and path.is_dir():
        # Windows reports EACCES, not EISDIR, when a directory is opened as a
        # file; check the path itself rather than trusting the errno.
        message = f"“{name}” is a directory, not a file."
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


#: Back-off schedule for a refused replace, in seconds (~1.6 s in total).
#: Sized from the common Windows cause: an indexer or sync client that opened
#: the file a moment ago and is about to let go.  Long enough to ride that out,
#: short enough that Ctrl+S never feels like it hung.
_REPLACE_RETRY_DELAYS: tuple[float, ...] = (0.01, 0.02, 0.05, 0.1, 0.2, 0.4, 0.8)


def _clear_readonly(target: Path) -> int | None:
    """Drop the read-only attribute, returning the previous mode to restore.

    Windows refuses to rename over a read-only file, and unlike POSIX the bit
    lives on the file rather than on the directory, so the user's own documents
    routinely carry it (restored from a backup, copied off a CD, synced from a
    share).  Returns ``None`` when nothing needed changing.
    """
    try:
        mode = target.stat().st_mode
    except OSError:
        return None
    if mode & stat.S_IWRITE:
        return None
    try:
        os.chmod(target, mode | stat.S_IWRITE)
    except OSError:
        return None
    return mode


def _replace_via_win32(source: Path, target: Path) -> None:
    """Rename using ``ReplaceFileW``, which tolerates ``FILE_SHARE_DELETE``.

    A reader that opened the document politely — sharing delete as well as read
    — blocks ``MoveFileEx`` (what ``os.replace`` uses) but not this call, which
    is the one Windows itself provides for save-over-an-open-file.  It also
    carries the original's ACLs and creation time across, which a plain rename
    does not.
    """
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.ReplaceFileW.argtypes = [
        wintypes.LPCWSTR,  # lpReplacedFileName
        wintypes.LPCWSTR,  # lpReplacementFileName
        wintypes.LPCWSTR,  # lpBackupFileName
        wintypes.DWORD,    # dwReplaceFlags
        wintypes.LPVOID,
        wintypes.LPVOID,
    ]
    kernel32.ReplaceFileW.restype = wintypes.BOOL

    ok = kernel32.ReplaceFileW(
        str(target), str(source), None, _REPLACEFILE_IGNORE_MERGE_ERRORS, None, None
    )
    if not ok:
        code = ctypes.get_last_error()
        raise OSError(errno.EACCES, ctypes.FormatError(code), str(target), code)


def _rewrite_in_place(source: Path, target: Path) -> None:
    """Last resort: keep the destination's identity and overwrite its bytes.

    This is the only thing that still works when the other process allowed
    shared *writes* but not deletes.  It gives up atomicity — a crash midway
    leaves a half-written file — so it is tried only after both renames have
    failed, and ``source`` is deleted only once the new content is on disk, so
    the complete text always exists somewhere.
    """
    data = source.read_bytes()
    with open(target, "r+b") as handle:
        handle.write(data)
        handle.truncate()
        handle.flush()
        os.fsync(handle.fileno())
    source.unlink(missing_ok=True)


def _replace_atomic(source: Path, target: Path) -> None:
    """Move ``source`` onto ``target``, working around Windows file locking.

    On POSIX this is one ``os.replace`` and nothing else runs.  On Windows a
    handle held anywhere in the system refuses the rename with ``EACCES``, so
    the fallbacks below are attempted in descending order of safety.  Measured
    against a process holding the destination open with each share mode:

    ==========================  ==========  =============  ================
    destination held with       os.replace  ReplaceFileW   rewrite in place
    ==========================  ==========  =============  ================
    SHARE_READ                  refused     refused        refused
    SHARE_READ | WRITE          refused     refused        works
    SHARE_READ | WRITE | DELETE refused     works          (not reached)
    read-only attribute set     refused     n/a            n/a
    ==========================  ==========  =============  ================

    Nothing rescues a destination opened with ``SHARE_READ`` alone; that one
    surfaces to the user, who can close the other program and save again.  The
    document itself is never at risk — it is still in the editor's buffer.
    """
    if not IS_WINDOWS:
        os.replace(source, target)
        return

    restore_mode = _clear_readonly(target)
    try:
        last: OSError | None = None
        for delay in (0.0, *_REPLACE_RETRY_DELAYS):
            if delay:
                time.sleep(delay)
            try:
                os.replace(source, target)
                return
            except PermissionError as exc:
                last = exc

        log.debug("os.replace refused for %s, trying ReplaceFileW", target)
        try:
            _replace_via_win32(source, target)
            return
        except OSError as exc:
            log.debug("ReplaceFileW refused for %s: %s", target, exc)

        log.warning(
            "%s is locked by another process; falling back to a non-atomic "
            "in-place rewrite",
            target,
        )
        try:
            _rewrite_in_place(source, target)
            return
        except OSError:
            pass

        assert last is not None
        raise last
    finally:
        if restore_mode is not None and target.exists():
            try:
                os.chmod(target, restore_mode)
            except OSError:  # pragma: no cover - the save itself succeeded
                log.debug("could not restore the read-only bit on %s", target)


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
        _replace_atomic(tmp_path, target)
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
        _replace_atomic(Path(tmp_name), target)
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
