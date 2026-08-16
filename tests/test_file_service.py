# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for disk I/O: atomic writes, encoding fallbacks and error phrasing."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from app.core.file_service import (
    FileError,
    is_markdown,
    read_text,
    unique_path,
    write_bytes_atomic,
    write_text_atomic,
)

from .conftest import (
    FileHolder,
    requires_posix_permissions,
    requires_windows,
    skip_if_root,
)


# ------------------------------------------------------------------ reading
def test_read_text_returns_content_and_mtime(tmp_path: Path) -> None:
    target = tmp_path / "a.md"
    target.write_text("hello\n", encoding="utf-8")
    loaded = read_text(target)
    assert loaded.text == "hello\n"
    assert loaded.encoding == "utf-8"
    assert loaded.mtime_ns > 0
    assert not loaded.had_decode_errors


def test_read_text_normalises_line_endings(tmp_path: Path) -> None:
    target = tmp_path / "crlf.md"
    target.write_bytes(b"a\r\nb\rc\n")
    assert read_text(target).text == "a\nb\nc\n"


def test_read_text_flags_invalid_utf8_instead_of_crashing(tmp_path: Path) -> None:
    target = tmp_path / "latin.md"
    target.write_bytes("café".encode("latin-1"))
    loaded = read_text(target)
    assert loaded.had_decode_errors
    assert "�" in loaded.text


def test_read_text_reports_a_missing_file_in_plain_words(tmp_path: Path) -> None:
    with pytest.raises(FileError) as excinfo:
        read_text(tmp_path / "ghost.md")
    assert "no longer exists" in excinfo.value.message
    assert "Traceback" not in excinfo.value.message


def test_read_text_reports_a_directory(tmp_path: Path) -> None:
    with pytest.raises(FileError) as excinfo:
        read_text(tmp_path)
    assert "directory" in excinfo.value.message


# ------------------------------------------------------------------ writing
def test_write_text_atomic_creates_the_file_and_returns_mtime(tmp_path: Path) -> None:
    target = tmp_path / "out.md"
    mtime = write_text_atomic(target, "content")
    assert target.read_text(encoding="utf-8") == "content\n"
    assert mtime == target.stat().st_mtime_ns


def test_write_text_atomic_leaves_no_temporary_files_behind(tmp_path: Path) -> None:
    target = tmp_path / "out.md"
    write_text_atomic(target, "content")
    assert [p.name for p in tmp_path.iterdir()] == ["out.md"]


@requires_posix_permissions
def test_write_text_atomic_preserves_permissions(tmp_path: Path) -> None:
    target = tmp_path / "out.md"
    target.write_text("old", encoding="utf-8")
    target.chmod(0o600)
    write_text_atomic(target, "new")
    assert stat.S_IMODE(target.stat().st_mode) == 0o600


@requires_windows
def test_write_text_atomic_preserves_the_read_only_attribute(tmp_path: Path) -> None:
    """The Windows expression of the test above.

    Windows has no owner/group/other bits to preserve — the read-only attribute
    is the whole of what a file's mode means here — so that is what saving must
    leave untouched.  Note this is a stronger claim than it looks: the save path
    has to *clear* the attribute to rename over the file at all, so getting it
    back afterwards is real work rather than an accident.
    """
    target = tmp_path / "out.md"
    target.write_text("old", encoding="utf-8")
    os.chmod(target, stat.S_IREAD)
    try:
        write_text_atomic(target, "new")
        assert target.read_text(encoding="utf-8") == "new\n"
        assert not os.access(target, os.W_OK), "the read-only attribute was lost"
    finally:
        os.chmod(target, stat.S_IWRITE)


def test_write_text_atomic_can_skip_the_trailing_newline(tmp_path: Path) -> None:
    target = tmp_path / "out.md"
    write_text_atomic(target, "no newline", ensure_newline=False)
    assert target.read_text(encoding="utf-8") == "no newline"


def test_write_text_atomic_creates_missing_parents(tmp_path: Path) -> None:
    target = tmp_path / "deep" / "nested" / "out.md"
    write_text_atomic(target, "x")
    assert target.is_file()


@requires_posix_permissions
@skip_if_root
def test_write_text_atomic_reports_permission_errors(tmp_path: Path) -> None:
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o500)
    try:
        with pytest.raises(FileError) as excinfo:
            write_text_atomic(locked / "out.md", "x")
        assert "Permission denied" in excinfo.value.message
    finally:
        locked.chmod(0o700)


# ------------------------------------------------- Windows file-locking rules
#
# On Windows a rename over an open file is refused outright, so saving a
# document that something else is holding needs a ladder of fallbacks. These
# tests pin down which rung each share mode lands on; if a future change to
# _replace_atomic silently drops one, the matching test fails rather than the
# user discovering it mid-save.


@requires_windows
def test_save_succeeds_when_a_reader_allows_shared_deletes(tmp_path: Path) -> None:
    """The polite-reader case, which ``ReplaceFileW`` can still satisfy."""
    target = tmp_path / "doc.md"
    target.write_text("old\n", encoding="utf-8")
    share = FileHolder.SHARE_READ | FileHolder.SHARE_WRITE | FileHolder.SHARE_DELETE
    with FileHolder(target, share):
        write_text_atomic(target, "new")
    assert target.read_text(encoding="utf-8") == "new\n"


@requires_windows
def test_save_succeeds_when_a_reader_allows_shared_writes(tmp_path: Path) -> None:
    """Neither rename works here; only the in-place rewrite gets the bytes down."""
    target = tmp_path / "doc.md"
    target.write_text("old\n", encoding="utf-8")
    with FileHolder(target, FileHolder.SHARE_READ | FileHolder.SHARE_WRITE):
        write_text_atomic(target, "new")
    assert target.read_text(encoding="utf-8") == "new\n"


@requires_windows
def test_save_reports_an_exclusive_lock_in_words_the_user_can_act_on(
    tmp_path: Path,
) -> None:
    """Nothing can rescue a destination opened without shared writes or deletes.

    What matters is that the failure names the real cause: "permission denied"
    would send the user hunting through file properties for an ACL problem that
    is not there.
    """
    target = tmp_path / "doc.md"
    target.write_text("old\n", encoding="utf-8")
    with FileHolder(target, FileHolder.SHARE_READ), pytest.raises(FileError) as excinfo:
        write_text_atomic(target, "new")
    assert "Another program is using" in excinfo.value.message
    assert "doc.md" in excinfo.value.message
    # The original must survive a failed save intact.
    assert target.read_text(encoding="utf-8") == "old\n"


@requires_windows
def test_save_rides_out_a_lock_that_is_released_quickly(tmp_path: Path) -> None:
    """An indexer or sync client holding the file for a moment must not fail a save.

    This is the overwhelmingly common real-world case, and the one the retry
    schedule exists for.
    """
    import threading

    target = tmp_path / "doc.md"
    target.write_text("old\n", encoding="utf-8")
    holder = FileHolder(target, FileHolder.SHARE_READ)
    holder.__enter__()
    threading.Timer(0.4, holder.release).start()
    try:
        write_text_atomic(target, "new")
    finally:
        holder.release()
    assert target.read_text(encoding="utf-8") == "new\n"


@requires_windows
def test_save_leaves_no_temporary_file_after_a_locked_failure(tmp_path: Path) -> None:
    target = tmp_path / "doc.md"
    target.write_text("old\n", encoding="utf-8")
    with FileHolder(target, FileHolder.SHARE_READ), pytest.raises(FileError):
        write_text_atomic(target, "new")
    assert [p.name for p in tmp_path.iterdir()] == ["doc.md"]


def test_write_bytes_atomic(tmp_path: Path) -> None:
    target = tmp_path / "blob.bin"
    write_bytes_atomic(target, b"\x00\x01\x02")
    assert target.read_bytes() == b"\x00\x01\x02"


# ------------------------------------------------------------------ helpers
def test_unique_path_avoids_collisions(tmp_path: Path) -> None:
    (tmp_path / "img.png").touch()
    (tmp_path / "img-1.png").touch()
    assert unique_path(tmp_path, "img", ".png").name == "img-2.png"


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("a.md", True),
        ("a.MARKDOWN", True),
        ("a.mdx", True),
        ("a.txt", True),
        ("a.py", False),
        ("a", False),
    ],
)
def test_is_markdown(name: str, expected: bool) -> None:
    assert is_markdown(name) is expected

