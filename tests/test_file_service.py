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


def test_write_text_atomic_preserves_permissions(tmp_path: Path) -> None:
    target = tmp_path / "out.md"
    target.write_text("old", encoding="utf-8")
    target.chmod(0o600)
    write_text_atomic(target, "new")
    assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_write_text_atomic_can_skip_the_trailing_newline(tmp_path: Path) -> None:
    target = tmp_path / "out.md"
    write_text_atomic(target, "no newline", ensure_newline=False)
    assert target.read_text(encoding="utf-8") == "no newline"


def test_write_text_atomic_creates_missing_parents(tmp_path: Path) -> None:
    target = tmp_path / "deep" / "nested" / "out.md"
    write_text_atomic(target, "x")
    assert target.is_file()


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores file permissions")
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

