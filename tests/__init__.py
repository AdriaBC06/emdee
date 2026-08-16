# SPDX-License-Identifier: GPL-3.0-or-later
"""Test package.

Present so the suite can share helpers through ``from .conftest import …``.
Without it pytest imports each test file as a top-level module and the relative
import fails.
"""
