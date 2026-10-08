# SPDX-License-Identifier: GPL-3.0-or-later
"""Errors raised by vault sharing, each phrased so it can be shown as-is."""

from __future__ import annotations

__all__ = [
    "AuthError",
    "ConnectionClosedError",
    "ProtocolError",
    "RemoteError",
    "SecurityError",
    "SyncError",
    "UnsafePathError",
]


class SyncError(Exception):
    """Base class: something stopped the operation; the message says what."""


class ProtocolError(SyncError):
    """The other side sent something malformed or out of order."""


class AuthError(SyncError):
    """The handshake failed: wrong code, or someone in the middle."""


class SecurityError(SyncError):
    """Something that looks like an attack, not a mistake: abort everything."""


class UnsafePathError(SecurityError):
    """A peer named a file outside the vault or one that may not be written."""


class RemoteError(SyncError):
    """The other device refused or failed, and said why."""


class ConnectionClosedError(SyncError):
    """The connection ended before the operation finished."""
