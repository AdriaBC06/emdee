# SPDX-License-Identifier: GPL-3.0-or-later
"""Frozen-build entry point.

PyInstaller runs its entry script as ``__main__`` with no package around it, so
pointing it at ``app/main.py`` makes every ``from . import …`` in that module
fail with "attempted relative import with no known parent package".  This file
exists to import the package properly and hand straight over, which is the same
thing ``python -m app.main`` does when running from a checkout.
"""

from __future__ import annotations

import sys

from app.main import main

if __name__ == "__main__":
    sys.exit(main())
