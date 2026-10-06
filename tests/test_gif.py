# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for animated-GIF detection (no Qt involved)."""

from __future__ import annotations

import base64
from pathlib import Path

from app.core.gif import animated_gif_sources, is_animated_gif


def _gif(frames: int, *, extension: bool = True) -> bytes:
    """A minimal 1x1 GIF89a with a global colour table and ``frames`` frames."""
    out = bytearray(b"GIF89a")
    out += b"\x01\x00\x01\x00"  # logical screen 1x1
    out += b"\x80\x00\x00"  # global colour table, 2 entries
    out += b"\x00\x00\x00\xff\xff\xff"
    for _ in range(frames):
        if extension:
            # Graphic control extension; its delay byte is 0x2C on purpose, so a
            # naive byte scan would count it as an image descriptor.
            out += b"\x21\xf9\x04\x00\x2c\x00\x00\x00"
        out += b"\x2c\x00\x00\x00\x00\x01\x00\x01\x00\x00"
        out += b"\x02\x02\x44\x01\x00"  # LZW min size, one sub-block, terminator
    out += b"\x3b"
    return bytes(out)


def test_single_frame_is_static() -> None:
    assert not is_animated_gif(_gif(1))
    assert not is_animated_gif(_gif(1, extension=False))


def test_two_frames_is_animated() -> None:
    assert is_animated_gif(_gif(2))
    assert is_animated_gif(_gif(3, extension=False))


def test_not_a_gif() -> None:
    assert not is_animated_gif(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
    assert not is_animated_gif(b"")


def test_truncated_gif_does_not_raise() -> None:
    data = _gif(2)
    for cut in range(len(data)):
        is_animated_gif(data[:cut])


def test_sources_resolve_relative_absolute_file_and_data(tmp_path: Path) -> None:
    (tmp_path / "moving pic.gif").write_bytes(_gif(2))
    (tmp_path / "still.gif").write_bytes(_gif(1))
    encoded = base64.b64encode(_gif(2)).decode("ascii")
    html = (
        '<img src="moving%20pic.gif" alt="">'
        '<img src="still.gif" alt="">'
        f'<img src="{(tmp_path / "moving pic.gif").as_uri()}">'
        f'<img src="data:image/gif;base64,{encoded}">'
        '<img src="https://example.com/remote.gif">'
        '<img src="missing.gif">'
    )
    found = animated_gif_sources(html, tmp_path)
    assert found[0] == "moving%20pic.gif"
    assert len(found) == 3
    assert "still.gif" not in found


def test_relative_sources_need_a_base_dir() -> None:
    assert animated_gif_sources('<img src="a.gif">', None) == []
