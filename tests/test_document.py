# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for the document model: dirty state, load/save and disk drift."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from app.core.document import UNTITLED_NAME, Document
from app.core.file_service import FileError


def test_a_new_document_is_clean_and_untitled() -> None:
    doc = Document()
    assert doc.is_untitled
    assert not doc.is_dirty
    assert doc.name == UNTITLED_NAME
    assert doc.display_name == UNTITLED_NAME
    assert doc.directory is None


def test_editing_marks_the_document_dirty() -> None:
    doc = Document("start")
    assert not doc.is_dirty
    doc.text = "changed"
    assert doc.is_dirty
    assert doc.display_name.endswith("•")


def test_setting_the_same_text_does_not_dirty_the_document() -> None:
    doc = Document("same")
    doc.text = "same"
    assert not doc.is_dirty


def test_reverting_the_text_clears_the_dirty_flag() -> None:
    doc = Document("start")
    doc.text = "changed"
    doc.text = "start"
    assert not doc.is_dirty


def test_observers_are_notified_on_change() -> None:
    doc = Document()
    seen: list[str] = []
    doc.subscribe(lambda d: seen.append(d.text))
    doc.text = "one"
    doc.text = "two"
    assert seen == ["one", "two"]


def test_load_reads_the_file_and_starts_clean(tmp_path: Path) -> None:
    target = tmp_path / "note.md"
    target.write_text("# Note\n", encoding="utf-8")
    doc = Document()
    doc.load(target)
    assert doc.text == "# Note\n"
    assert doc.path == target
    assert doc.name == "note.md"
    assert doc.directory == tmp_path
    assert not doc.is_dirty


def test_save_writes_and_clears_the_dirty_flag(tmp_path: Path) -> None:
    target = tmp_path / "note.md"
    doc = Document("body", target)
    doc.text = "new body"
    assert doc.is_dirty
    doc.save()
    assert target.read_text(encoding="utf-8") == "new body\n"
    assert not doc.is_dirty
    assert doc.mtime_ns > 0


def test_save_without_a_path_raises_a_friendly_error() -> None:
    doc = Document("orphan")
    with pytest.raises(FileError) as excinfo:
        doc.save()
    assert "Save As" in excinfo.value.message


def test_save_as_adopts_the_new_path(tmp_path: Path) -> None:
    doc = Document("body")
    doc.save(tmp_path / "elsewhere.md")
    assert doc.path == tmp_path / "elsewhere.md"
    assert doc.name == "elsewhere.md"


def test_reset_replaces_state_and_marks_clean() -> None:
    doc = Document("old", Path("/tmp/x.md"))
    doc.text = "dirty"
    doc.reset()
    assert doc.text == ""
    assert doc.is_untitled
    assert not doc.is_dirty


def test_changed_on_disk_detects_external_edits(tmp_path: Path) -> None:
    target = tmp_path / "note.md"
    target.write_text("one\n", encoding="utf-8")
    doc = Document()
    doc.load(target)
    assert not doc.changed_on_disk()

    time.sleep(0.01)
    target.write_text("two\n", encoding="utf-8")
    assert doc.changed_on_disk()


def test_changed_on_disk_is_false_for_untitled_documents() -> None:
    assert Document("x").changed_on_disk() is False


def test_stats_come_from_the_current_buffer() -> None:
    doc = Document("one two three")
    assert doc.stats().words == 3
    doc.text = "one"
    assert doc.stats().words == 1


def test_decode_errors_are_surfaced(tmp_path: Path) -> None:
    target = tmp_path / "bad.md"
    target.write_bytes(b"\xff\xfe not utf 8")
    doc = Document()
    doc.load(target)
    assert doc.had_decode_errors
