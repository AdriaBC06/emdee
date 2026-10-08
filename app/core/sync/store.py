# SPDX-License-Identifier: GPL-3.0-or-later
"""This device's identity and its address book of trusted devices.

Every Emdee installation owns an Ed25519 key pair, created on first use.  The
public half *is* the device as far as other devices are concerned: pairing two
devices means each one storing the other's public key, and every later
connection proves possession of the matching private key.  An IP address is
only a hint for where to dial — a contact that turns up at another address is
still recognised, and a stranger at a contact's old address is still refused.

Everything lives in a private directory (``0700``; files ``0600``) under the
user's configuration folder, outside any vault, so it is never synchronised
and a peer can never write to it.  Nothing here imports Qt: the ``emdee sync``
command line and the window share the same files.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import logging
import os
import re
import socket
import tempfile
import time
import unicodedata
from dataclasses import dataclass, replace
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
)

from ... import APP_ORG
from ...platform_support import IS_WINDOWS
from .errors import SyncError

log = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_PORT",
    "Contact",
    "ContactBook",
    "Identity",
    "config_dir",
    "fingerprint",
    "clean_name",
    "load_identity",
    "write_private",
]

#: TCP port a device listens on while "accepting connections".
DEFAULT_PORT = 47231

#: Longest device name kept; names arrive from peers, so they are bounded.
MAX_NAME = 48

_NAME_FILE = "device.json"
_KEY_FILE = "identity.key"
_CONTACTS_FILE = "contacts.json"

#: Bidirectional-text overrides and isolates: they let a name *display* as
#: something other than what it is, which is exactly what a hostile peer would
#: use to impersonate a trusted contact in a confirmation dialog.
_BIDI_CONTROLS = frozenset("‪‫‬‭‮⁦⁧⁨⁩‎‏")


def config_dir() -> Path:
    """The private folder that holds the key, the contacts and sync state.

    ``EMDEE_SYNC_HOME`` overrides it, which is how the tests run two
    "devices" side by side in one process.
    """
    override = os.environ.get("EMDEE_SYNC_HOME")
    if override:
        return Path(override)
    if IS_WINDOWS:
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / APP_ORG / "sync"


def _ensure_private_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not IS_WINDOWS:
        try:
            if path.stat().st_mode & 0o077:
                os.chmod(path, 0o700)
        except OSError:  # pragma: no cover - best effort
            log.warning("could not restrict permissions of %s", path)
    return path


def write_private(path: Path, data: bytes) -> None:
    """Atomically write ``data`` to ``path``, readable by the owner only."""
    _ensure_private_dir(path.parent)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            if not IS_WINDOWS:
                os.fchmod(handle.fileno(), 0o600)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _read_private(path: Path) -> bytes:
    data = path.read_bytes()
    if not IS_WINDOWS and path.stat().st_mode & 0o077:
        # Someone loosened the permissions; tighten them rather than refuse,
        # but say so — this is the device's private key.
        log.warning("%s was readable by other users; restricting it to the owner", path)
        os.chmod(path, 0o600)
    return data


def clean_name(value: object, fallback: str = "device") -> str:
    """A display name that is safe to show in a dialog and to put in a file name.

    Control characters, bidi overrides and characters that are invalid in
    Windows file names are dropped, whitespace is collapsed and the length is
    bounded.  It is applied to every name that arrives from the network.
    """
    if not isinstance(value, str):
        return fallback
    text = re.sub(r"\s", " ", unicodedata.normalize("NFC", value))
    kept = []
    for char in text:
        if char in _BIDI_CONTROLS or char in '<>:"/\\|?*':
            continue
        category = unicodedata.category(char)
        if category in ("Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp"):
            continue
        kept.append(char)
    name = re.sub(r"\s+", " ", "".join(kept)).strip(" .")
    return name[:MAX_NAME].strip(" .") or fallback


def fingerprint(public_key: bytes) -> str:
    """Human-comparable fingerprint of a public key: ``ab12 cd34 …`` (80 bits)."""
    digest = hashlib.sha256(b"emdee-sync fingerprint\0" + public_key).hexdigest()[:20]
    return " ".join(digest[i : i + 4] for i in range(0, 20, 4))


def device_id(public_key: bytes) -> str:
    """Stable identifier of a device: SHA-256 of its public key, hex."""
    return hashlib.sha256(public_key).hexdigest()


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _unb64(text: object, length: int) -> bytes:
    if not isinstance(text, str):
        raise ValueError("expected base64 text")
    try:
        raw = base64.b64decode(text.encode("ascii"), validate=True)
    except (binascii.Error, UnicodeEncodeError) as exc:
        raise ValueError("invalid base64") from exc
    if len(raw) != length:
        raise ValueError("wrong key length")
    return raw


# ------------------------------------------------------------------ identity
@dataclass(frozen=True)
class Identity:
    """This device: its signing key, display name and listening port."""

    private_key: Ed25519PrivateKey
    name: str
    port: int = DEFAULT_PORT

    @property
    def public_bytes(self) -> bytes:
        return self.private_key.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )

    @property
    def fingerprint(self) -> str:
        return fingerprint(self.public_bytes)

    @property
    def id(self) -> str:
        return device_id(self.public_bytes)

    def sign(self, data: bytes) -> bytes:
        return self.private_key.sign(data)


def _default_name() -> str:
    return clean_name(socket.gethostname(), "Emdee")


def load_identity(directory: Path | None = None) -> Identity:
    """Load this device's identity, creating the key pair on first use."""
    root = _ensure_private_dir(directory or config_dir())
    key_path = root / _KEY_FILE
    if key_path.exists():
        raw = _read_private(key_path)
        if len(raw) != 32:
            raise SyncError(f"The device key in {key_path} is damaged.")
        private_key = Ed25519PrivateKey.from_private_bytes(raw)
    else:
        private_key = Ed25519PrivateKey.generate()
        write_private(
            key_path,
            private_key.private_bytes(
                serialization.Encoding.Raw,
                serialization.PrivateFormat.Raw,
                serialization.NoEncryption(),
            ),
        )
        log.info("created a new sync identity in %s", key_path)

    name, port = _default_name(), DEFAULT_PORT
    try:
        info = json.loads((root / _NAME_FILE).read_text(encoding="utf-8"))
        if isinstance(info, dict):
            name = clean_name(info.get("name"), name)
            port = _valid_port(info.get("port"), DEFAULT_PORT)
    except (OSError, ValueError):
        pass
    return Identity(private_key, name, port)


def save_device_settings(identity: Identity, directory: Path | None = None) -> None:
    """Persist the display name and port (the key itself never changes)."""
    root = directory or config_dir()
    payload = {"name": identity.name, "port": identity.port}
    write_private(root / _NAME_FILE, json.dumps(payload, ensure_ascii=False).encode("utf-8"))


def with_settings(identity: Identity, *, name: str | None = None, port: int | None = None) -> Identity:
    return replace(
        identity,
        name=clean_name(name, identity.name) if name is not None else identity.name,
        port=_valid_port(port, identity.port) if port is not None else identity.port,
    )


def _valid_port(value: object, default: int) -> int:
    try:
        port = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return port if 1024 <= port <= 65535 else default


# ------------------------------------------------------------------ contacts
@dataclass(frozen=True)
class Contact:
    """A paired device: who it is (its key) and where it was last seen."""

    public_key: bytes
    name: str
    host: str
    port: int = DEFAULT_PORT
    added: float = 0.0

    @property
    def id(self) -> str:
        return device_id(self.public_key)

    @property
    def fingerprint(self) -> str:
        return fingerprint(self.public_key)

    @property
    def address(self) -> str:
        host = f"[{self.host}]" if ":" in self.host else self.host
        return f"{host}:{self.port}"

    def to_json(self) -> dict[str, object]:
        return {
            "public_key": _b64(self.public_key),
            "name": self.name,
            "host": self.host,
            "port": self.port,
            "added": self.added,
        }

    @classmethod
    def from_json(cls, data: object) -> Contact:
        if not isinstance(data, dict):
            raise ValueError("contact entry is not an object")
        host = data.get("host")
        if not isinstance(host, str) or not host or len(host) > 255:
            raise ValueError("bad host")
        added = data.get("added", 0.0)
        return cls(
            public_key=_unb64(data.get("public_key"), 32),
            name=clean_name(data.get("name")),
            host=host,
            port=_valid_port(data.get("port"), DEFAULT_PORT),
            added=float(added) if isinstance(added, (int, float)) else 0.0,
        )


class ContactBook:
    """The trusted devices, persisted to ``contacts.json``."""

    def __init__(self, directory: Path | None = None) -> None:
        self._dir = directory or config_dir()
        self._path = self._dir / _CONTACTS_FILE
        self._contacts: dict[str, Contact] = {}
        self.reload()

    def reload(self) -> None:
        self._contacts = {}
        try:
            data = json.loads(_read_private(self._path).decode("utf-8"))
        except FileNotFoundError:
            return
        except (OSError, ValueError) as exc:
            raise SyncError(f"Could not read the contact list {self._path}: {exc}") from exc
        entries = data.get("contacts", []) if isinstance(data, dict) else []
        for entry in entries if isinstance(entries, list) else []:
            try:
                contact = Contact.from_json(entry)
            except ValueError as exc:
                log.warning("skipping a damaged contact entry: %s", exc)
                continue
            self._contacts[contact.id] = contact

    def _save(self) -> None:
        payload = {"version": 1, "contacts": [c.to_json() for c in self.all()]}
        write_private(self._path, json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"))

    def all(self) -> list[Contact]:
        return sorted(self._contacts.values(), key=lambda c: (c.name.casefold(), c.id))

    def by_key(self, public_key: bytes) -> Contact | None:
        return self._contacts.get(device_id(public_key))

    def find(self, query: str) -> Contact:
        """A contact by exact name (case-insensitive) or by id/fingerprint prefix."""
        wanted = query.strip().casefold()
        if not wanted:
            raise SyncError("No contact given.")
        named = [c for c in self._contacts.values() if c.name.casefold() == wanted]
        if len(named) == 1:
            return named[0]
        compact = wanted.replace(" ", "")
        if len(compact) >= 4:
            by_print = [
                c for c in self._contacts.values()
                if c.fingerprint.replace(" ", "").startswith(compact) or c.id.startswith(compact)
            ]
            if len(by_print) == 1:
                return by_print[0]
        if len(named) > 1:
            raise SyncError(f"Several contacts are called {query!r}; use the fingerprint.")
        raise SyncError(f"No contact matches {query!r}.")

    def _unique_name(self, name: str, keep_id: str) -> str:
        taken = {c.name.casefold() for c in self._contacts.values() if c.id != keep_id}
        candidate, counter = name, 2
        while candidate.casefold() in taken:
            candidate = f"{name} ({counter})"
            counter += 1
        return candidate

    def add(self, public_key: bytes, name: str, host: str, port: int) -> Contact:
        """Add a device or refresh an existing one (same key = same device)."""
        existing = self.by_key(public_key)
        cid = device_id(public_key)
        contact = Contact(
            public_key=public_key,
            name=self._unique_name(existing.name if existing else clean_name(name), cid),
            host=host,
            port=_valid_port(port, DEFAULT_PORT),
            added=existing.added if existing else time.time(),
        )
        self._contacts[cid] = contact
        self._save()
        return contact

    def update(self, contact: Contact, *, name: str | None = None, host: str | None = None,
               port: int | None = None) -> Contact:
        current = self._contacts.get(contact.id)
        if current is None:
            raise SyncError("That contact no longer exists.")
        updated = replace(
            current,
            name=self._unique_name(clean_name(name, current.name), current.id) if name else current.name,
            host=host.strip() if host else current.host,
            port=_valid_port(port, current.port) if port is not None else current.port,
        )
        self._contacts[current.id] = updated
        self._save()
        return updated

    def remove(self, contact: Contact) -> None:
        if self._contacts.pop(contact.id, None) is not None:
            self._save()
