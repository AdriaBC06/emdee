# SPDX-License-Identifier: GPL-3.0-or-later
"""Telling animated GIFs apart from static ones.

PDF has no animation, so an animated GIF exports as its first frame.  The PDF
export warns about that once — but only when the document really contains an
animated one: static GIFs are common and warning on every ``.gif`` would be
noise.  A GIF is animated when it holds more than one image descriptor, which
this module finds by walking the block structure rather than trusting the
extension or scanning for a byte that could just as well appear in pixel data.
"""

from __future__ import annotations

import base64
import binascii
import html
import re
from pathlib import Path
from urllib.parse import unquote, urlsplit
from urllib.request import url2pathname

__all__ = ["is_animated_gif", "animated_gif_sources"]

#: GIFs above this are not read; a header walk does not need the whole file,
#: but the format gives no way to find frame two without reading frame one.
MAX_GIF_BYTES = 64 * 1024 * 1024

_IMG_SRC = re.compile(r'<img\b[^>]*?\bsrc="([^"]+)"', re.IGNORECASE)
_SCHEME = re.compile(r"^[a-z][a-z0-9+.-]*:", re.IGNORECASE)
_DATA_GIF = re.compile(r"^data:image/gif;base64,(.*)$", re.IGNORECASE | re.DOTALL)


def _skip_sub_blocks(data: bytes, index: int) -> int:
    """Index just past a chain of length-prefixed sub-blocks, or -1."""
    while index < len(data):
        size = data[index]
        index += 1
        if size == 0:
            return index
        index += size
    return -1


def is_animated_gif(data: bytes) -> bool:
    """True when ``data`` is a GIF with more than one frame."""
    if len(data) < 13 or data[:6] not in (b"GIF87a", b"GIF89a"):
        return False

    index = 13
    flags = data[10]
    if flags & 0x80:
        index += 3 * (2 ** ((flags & 0x07) + 1))

    frames = 0
    while index < len(data):
        block = data[index]
        if block == 0x3B:  # trailer
            break
        if block == 0x21:  # extension: introducer, label, sub-blocks
            index = _skip_sub_blocks(data, index + 2)
        elif block == 0x2C:  # image descriptor
            frames += 1
            if frames > 1:
                return True
            if index + 10 > len(data):
                break
            local = data[index + 9]
            index += 10
            if local & 0x80:
                index += 3 * (2 ** ((local & 0x07) + 1))
            # LZW minimum code size, then the image data sub-blocks.
            index = _skip_sub_blocks(data, index + 1)
        else:
            break  # corrupt or truncated; whatever was counted stands
        if index == -1:
            break
    return False


def _local_path(src: str, base_dir: Path | None) -> Path | None:
    if src.lower().startswith("file:"):
        return Path(url2pathname(urlsplit(src).path))
    if _SCHEME.match(src) or src.startswith("//"):
        return None
    path = Path(unquote(src.split("#", 1)[0].split("?", 1)[0]))
    if path.is_absolute():
        return path
    return base_dir / path if base_dir is not None else None


def _read_gif(src: str, base_dir: Path | None) -> bytes | None:
    data_uri = _DATA_GIF.match(src)
    if data_uri:
        try:
            return base64.b64decode(data_uri.group(1), validate=False)
        except (binascii.Error, ValueError):
            return None

    path = _local_path(src, base_dir)
    if path is None or path.suffix.lower() != ".gif":
        return None
    try:
        if not path.is_file() or path.stat().st_size > MAX_GIF_BYTES:
            return None
        return path.read_bytes()
    except OSError:
        return None


def animated_gif_sources(body_html: str, base_dir: Path | None) -> list[str]:
    """``src`` of every ``<img>`` in ``body_html`` that is an animated GIF.

    Only local files and ``data:`` URIs are inspected; remote images are never
    fetched just to find out.
    """
    found: list[str] = []
    for raw in _IMG_SRC.findall(body_html):
        src = html.unescape(raw)
        if src in found:
            continue
        data = _read_gif(src, base_dir)
        if data is not None and is_animated_gif(data):
            found.append(src)
    return found
