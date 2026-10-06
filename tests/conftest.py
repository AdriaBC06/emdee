# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared fixtures and platform capability markers.

Emdee's filesystem behaviour genuinely differs between POSIX and Windows — not
in what it guarantees, but in the mechanisms available to test it.  POSIX
permission bits and Windows file locking are different phenomena, so the suite
tests each where it exists rather than reducing both to whatever they have in
common.  The markers here name the *capability* a test needs, so a skip always
says which platform feature was missing.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

IS_WINDOWS = sys.platform == "win32"

#: POSIX permission bits that actually restrict the owner.  On Windows
#: ``chmod`` only ever toggles the read-only attribute, so a test that asserts
#: on ``0o600`` is asserting about a thing the platform does not have.
requires_posix_permissions = pytest.mark.skipif(
    IS_WINDOWS, reason="POSIX permission bits; Windows only models the read-only attribute"
)

#: ``os.geteuid`` does not exist on Windows, so this cannot be evaluated
#: eagerly in a decorator the way the original test did.
running_as_root = hasattr(os, "geteuid") and os.geteuid() == 0

skip_if_root = pytest.mark.skipif(
    running_as_root, reason="root ignores file permissions"
)

requires_windows = pytest.mark.skipif(
    not IS_WINDOWS, reason="exercises Windows file-locking behaviour"
)


def _symlinks_available() -> bool:
    """Windows needs Developer Mode or admin rights to create symlinks."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "target"
        target.write_text("x")
        try:
            (Path(tmp) / "link").symlink_to(target)
        except (OSError, NotImplementedError):
            return False
        return True


requires_symlinks = pytest.mark.skipif(
    not _symlinks_available(),
    reason="symlink creation is unavailable (Windows needs Developer Mode or admin)",
)


class FileHolder:
    """Holds a file open in another process, with a chosen Windows share mode.

    Emdee's save path has to cope with a document that something else has open,
    which on Windows blocks renaming.  Reproducing that needs a *separate*
    process — a handle in this one would not exercise the same code path in the
    kernel — and it needs control over the share mode, because which fallback
    succeeds depends on it.
    """

    GENERIC_READ = 0x80000000
    SHARE_READ = 0x1
    SHARE_WRITE = 0x2
    SHARE_DELETE = 0x4

    def __init__(self, path: Path, share: int) -> None:
        self._path = path
        self._share = share
        self._proc: subprocess.Popen | None = None

    def __enter__(self) -> FileHolder:
        code = (
            "import ctypes, ctypes.wintypes as w, sys, time\n"
            "k = ctypes.WinDLL('kernel32', use_last_error=True)\n"
            "k.CreateFileW.restype = w.HANDLE\n"
            "k.CreateFileW.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD, w.LPVOID,"
            " w.DWORD, w.DWORD, w.HANDLE]\n"
            f"h = k.CreateFileW(r'{self._path}', {self.GENERIC_READ}, {self._share},"
            " None, 3, 0x80, None)\n"
            "print('ready' if h != -1 else 'failed', flush=True)\n"
            "time.sleep(120)\n"
        )
        self._proc = subprocess.Popen(
            [sys.executable, "-c", code], stdout=subprocess.PIPE, text=True
        )
        assert self._proc.stdout is not None
        if self._proc.stdout.readline().strip() != "ready":
            self.__exit__(None, None, None)
            pytest.skip("could not open the file with the requested share mode")
        return self

    def release(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            self._proc.kill()
            self._proc.wait(timeout=10)
            # The handle is dropped asynchronously; give the kernel a moment or
            # a following rename can still see the file as in use.
            time.sleep(0.2)

    def __exit__(self, *exc: object) -> None:
        self.release()
