# SPDX-License-Identifier: GPL-3.0-or-later
"""Pairing and vault transfers between two devices on the same network.

One side *listens* (:class:`Listener`): it opens a port only on request,
shows a one-time code, accepts at most one authenticated connection and then
closes the port again.  Three wrong codes, the timeout, or a cancel close it
too.  The other side *connects* (:func:`pair`, :func:`transfer`) and types the
code.  Every operation therefore needs a fresh code, and the listening user
also approves each request explicitly after seeing who is asking and what for.

Operations, named from the connecting device's point of view:

``pull``  receive the other device's vault into a local folder;
``push``  send a local folder into the other device's vault;
``sync``  two-way merge (see :func:`files.compute_plan`).

The listener never trusts the client's view of its own vault: every path is
validated again, every overwrite and deletion carries the SHA-256 the client
expects to replace and is refused if the file has changed, and nothing is
deleted outright — replaced and deleted files go to ``.trash/``.
"""

from __future__ import annotations

import contextlib
import hashlib
import ipaddress
import logging
import socket
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ...platform_support import IS_WINDOWS
from . import files
from .channel import (
    MODE_PAIR,
    MODE_SESSION,
    SecureChannel,
    ServerKeyExchange,
    clean_message,
    client_handshake,
    generate_code,
    server_handshake,
)
from .errors import (
    AuthError,
    ConnectionClosedError,
    ProtocolError,
    RemoteError,
    SecurityError,
    SyncError,
    UnsafePathError,
)
from .files import Entry, IncomingFile, Plan
from .store import Contact, ContactBook, Identity, fingerprint

log = logging.getLogger(__name__)

__all__ = [
    "OPERATIONS",
    "Listener",
    "ListenerOutcome",
    "Report",
    "Request",
    "is_local_address",
    "local_addresses",
    "pair",
    "transfer",
]

OPERATIONS = ("pull", "push", "sync")

#: What each operation lets the *client* ask of the listener.
_ALLOWED = {
    "pull": frozenset({"get"}),
    "push": frozenset({"put", "delete"}),
    "sync": frozenset({"get", "put", "delete"}),
}

CHUNK = 256 * 1024
_MANIFEST_BATCH = 200
#: How long the connecting side waits for the other user to click Accept.
APPROVAL_TIMEOUT = 180.0
#: A connection must complete the handshake within this many seconds.
HANDSHAKE_DEADLINE = 20.0
CONNECT_TIMEOUT = 10.0
DEFAULT_LISTEN_SECONDS = 300
MAX_ATTEMPTS = 3

EventFn = Callable[[str], None]


def _quiet(_message: str) -> None:
    pass


# ---------------------------------------------------------------- network
def is_local_address(host: str) -> bool:
    """True for loopback, private (RFC 1918 / ULA) and link-local addresses.

    Sharing is LAN-only by design: the listener drops connections from any
    other address before reading a byte, and the client refuses to dial one.
    """
    try:
        ip = ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if ip.is_unspecified or ip.is_multicast:
        return False
    return ip.is_loopback or ip.is_link_local or ip.is_private


def local_addresses() -> list[str]:
    """This machine's LAN addresses, to tell the other device where to dial.

    Asks the routing table which source address would be used to reach each
    private range; ``connect`` on a UDP socket sends nothing.
    """
    found: list[str] = []
    for family, probe in ((socket.AF_INET, "192.168.255.255"), (socket.AF_INET, "10.255.255.255"),
                          (socket.AF_INET, "172.31.255.255"), (socket.AF_INET6, "fd00::1")):
        try:
            with socket.socket(family, socket.SOCK_DGRAM) as sock:
                sock.connect((probe, 9))
                address = str(sock.getsockname()[0])
        except OSError:
            continue
        if is_local_address(address) and not ipaddress.ip_address(address).is_loopback \
                and address not in found:
            found.append(address)
    return found


def _connect(host: str, port: int) -> socket.socket:
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise SyncError(f"Could not find {host} on the network.") from exc
    candidates = [info for info in infos if is_local_address(str(info[4][0]))]
    if not candidates:
        raise SecurityError(
            f"{host} is not an address on your local network; vault sharing only works on a LAN."
        )
    last: OSError | None = None
    for family, kind, proto, _name, address in candidates:
        sock = socket.socket(family, kind, proto)
        sock.settimeout(CONNECT_TIMEOUT)
        try:
            sock.connect(address)
            return sock
        except OSError as exc:
            sock.close()
            last = exc
    raise SyncError(
        f"Could not connect to {host}:{port} — is the other device accepting connections? ({last})"
    )


def _bind(port: int) -> socket.socket:
    if socket.has_dualstack_ipv6():
        try:
            return _bind_family(socket.AF_INET6, port)
        except SyncError:
            pass  # IPv6 disabled on this machine: IPv4 is enough on a LAN
    return _bind_family(socket.AF_INET, port)


def _bind_family(family: socket.AddressFamily, port: int) -> socket.socket:
    sock = socket.socket(family, socket.SOCK_STREAM)
    address: tuple[Any, ...]
    if family == socket.AF_INET6:
        sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        address = ("::", port)
    else:
        address = ("0.0.0.0", port)
    try:
        if IS_WINDOWS:
            # Without this another program could bind the same port and steal
            # connections meant for us.
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)  # type: ignore[attr-defined]
        else:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(address)
        sock.listen(1)
    except OSError as exc:
        sock.close()
        raise SyncError(f"Could not open port {port}: {exc.strerror or exc}") from exc
    return sock


# ---------------------------------------------------------------- reports
@dataclass
class Report:
    """What an operation did, from the point of view of the device reporting."""

    op: str
    peer: str
    received: list[str] = field(default_factory=list)
    sent: list[str] = field(default_factory=list)
    trashed: list[str] = field(default_factory=list)
    trashed_remote: list[str] = field(default_factory=list)
    conflicts: dict[str, str] = field(default_factory=dict)
    skipped: list[tuple[str, str]] = field(default_factory=list)

    def summary(self) -> str:
        parts = [
            f"{len(self.received)} received",
            f"{len(self.sent)} sent",
            f"{len(self.trashed)} moved to .trash here",
        ]
        if self.trashed_remote:
            parts.append(f"{len(self.trashed_remote)} moved to .trash on {self.peer}")
        if self.conflicts:
            parts.append(f"{len(self.conflicts)} conflict(s) kept as copies")
        if self.skipped:
            parts.append(f"{len(self.skipped)} skipped")
        return ", ".join(parts)


# ---------------------------------------------------------------- helpers
def _send_manifest(channel: SecureChannel, kind: str, entries: dict[str, Entry]) -> None:
    rows = [entries[rel].to_wire() for rel in sorted(entries)]
    for start in range(0, max(len(rows), 1), _MANIFEST_BATCH):
        batch = rows[start : start + _MANIFEST_BATCH]
        channel.send({"t": kind, "files": batch, "more": start + _MANIFEST_BATCH < len(rows)})


def _recv_manifest(channel: SecureChannel, kind: str, timeout: float | None = None) -> dict[str, Entry]:
    found: dict[str, Entry] = {}
    keys: set[str] = set()
    while True:
        message = channel.recv(kind, timeout=timeout)
        rows = message.get("files")
        if not isinstance(rows, list) or len(rows) > _MANIFEST_BATCH:
            raise ProtocolError("Malformed file list from the other device.")
        for row in rows:
            entry = files.entry_from_wire(row)
            key = files._collision_key(entry.path)
            if key in keys:
                raise ProtocolError("The other device listed the same file twice.")
            keys.add(key)
            found[entry.path] = entry
        if len(found) > files.MAX_FILES:
            raise ProtocolError("The other device listed too many files.")
        if sum(e.size for e in found.values()) > files.MAX_TOTAL_SIZE:
            raise ProtocolError("The other device listed more data than the sharing limit.")
        if message.get("more") is not True:
            return found


def _send_file(channel: SecureChannel, root: Path, rel: str) -> bool:
    """Send one vault file (header + data).  False if it vanished."""
    try:
        path = files.safe_target(root, rel)
        if not path.is_file() or path.stat().st_size > files.MAX_FILE_SIZE:
            raise FileNotFoundError(rel)
        # Read once, hash what was read: the header always matches the bytes,
        # even if the file is being edited right now.
        data = path.read_bytes()
    except (OSError, UnsafePathError):
        channel.send({"t": "missing", "path": rel})
        return False
    if len(data) > files.MAX_FILE_SIZE:
        channel.send({"t": "missing", "path": rel})
        return False
    channel.send({"t": "file", "path": rel, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()})
    for start in range(0, len(data), CHUNK):
        channel.send_data(data[start : start + CHUNK])
    return True


def _receive_body(channel: SecureChannel, incoming: IncomingFile | None, size: int) -> None:
    """Read exactly ``size`` bytes of data frames into ``incoming`` (or drop them)."""
    received = 0
    while received < size:
        chunk = channel.recv_data()
        received += len(chunk)
        if received > size:
            raise ProtocolError("The other device sent more data than announced.")
        if incoming is not None:
            incoming.write(chunk)


def _announce(message: dict[str, Any]) -> tuple[str, int, str]:
    rel = files.validate_rel_path(message.get("path"))
    size, digest = message.get("size"), message.get("sha256")
    if (
        not isinstance(size, int) or isinstance(size, bool)
        or not 0 <= size <= files.MAX_FILE_SIZE
        or not files._is_sha(digest)
    ):
        raise ProtocolError("Malformed file announcement.")
    return rel, size, str(digest)


def _optional_sha(value: object) -> str | None:
    if value is None:
        return None
    if not files._is_sha(value):
        raise ProtocolError("Malformed checksum.")
    return str(value)


def _common(local: dict[str, Entry], remote: dict[str, Entry]) -> list[Entry]:
    return [e for rel, e in local.items() if rel in remote and remote[rel].sha256 == e.sha256]


# ================================================================= listener
@dataclass(frozen=True)
class Request:
    """Something a connected, authenticated device is asking for."""

    kind: str  # "pair" or one of OPERATIONS
    peer_name: str
    fingerprint: str
    address: str
    vault: str = ""
    known: bool = False

    @property
    def description(self) -> str:
        who = f"“{self.peer_name}” ({self.address}, fingerprint {self.fingerprint})"
        if self.kind == "pair":
            again = " It is already a contact; its details will be refreshed." if self.known else ""
            return f"{who} wants to add this device as a contact.{again}"
        if self.kind == "pull":
            return f"{who} wants to receive a copy of your vault “{self.vault}”. Nothing here changes."
        if self.kind == "push":
            return (
                f"{who} wants to send its files into your vault “{self.vault}”. "
                "Files it replaces or deletes are moved to .trash/."
            )
        return (
            f"{who} wants to synchronise both ways with your vault “{self.vault}”. "
            "Files replaced or deleted are moved to .trash/."
        )


@dataclass
class ListenerOutcome:
    ok: bool
    message: str
    report: Report | None = None
    contact: Contact | None = None


ApproveFn = Callable[[Request], bool]


class Listener:
    """Accept exactly one authenticated connection, then close.

    ``vault`` is the only folder a client can reach; without one, only pairing
    is offered.  ``approve`` is called (on the listener's thread) for every
    request and must return True for anything to happen.
    """

    def __init__(
        self,
        identity: Identity,
        contacts: ContactBook,
        *,
        vault: Path | None,
        approve: ApproveFn,
        on_event: EventFn = _quiet,
        port: int | None = None,
        seconds: float = DEFAULT_LISTEN_SECONDS,
        max_attempts: int = MAX_ATTEMPTS,
        allow_pairing: bool = True,
    ) -> None:
        self.identity = identity
        self.contacts = contacts
        self.vault = vault.resolve() if vault is not None else None
        self.approve = approve
        self.on_event = on_event
        self.port = port if port is not None else identity.port
        self.seconds = seconds
        self.max_attempts = max_attempts
        self.code = generate_code()
        modes = [MODE_PAIR] if allow_pairing else []
        if self.vault is not None:
            modes.append(MODE_SESSION)
        if not modes:
            raise SyncError("Nothing to offer: open a vault or allow pairing.")
        self._modes = tuple(modes)
        self._cancel = threading.Event()
        self._server: socket.socket | None = None
        self._client: socket.socket | None = None
        self._lock = threading.Lock()

    # ---------------------------------------------------------------- life
    def open(self) -> int:
        """Bind the port now (so a busy port is reported immediately)."""
        if self._server is None:
            self._server = _bind(self.port)
            self.port = self._server.getsockname()[1]
        return self.port

    def cancel(self) -> None:
        self._cancel.set()
        with self._lock:
            for sock in (self._client, self._server):
                if sock is not None:
                    with contextlib.suppress(OSError):
                        sock.shutdown(socket.SHUT_RDWR)

    def serve(self) -> ListenerOutcome:
        """Block until one operation has run, or the listener gives up."""
        server = self._server or _bind(self.port)
        self._server = server
        server.settimeout(0.25)
        deadline = time.monotonic() + self.seconds
        attempts = 0
        exchange: ServerKeyExchange | None = None
        try:
            while True:
                if self._cancel.is_set():
                    return ListenerOutcome(False, "Stopped accepting connections.")
                if time.monotonic() >= deadline:
                    return ListenerOutcome(False, "No one connected in time; the code has expired.")
                # Computed before accept(): answering a hello must not take
                # code-dependent time.  Single use, so one per connection.
                if exchange is None:
                    exchange = ServerKeyExchange(self.code)
                try:
                    conn, address = server.accept()
                except TimeoutError:
                    continue
                except OSError:
                    if self._cancel.is_set():
                        return ListenerOutcome(False, "Stopped accepting connections.")
                    raise
                host = str(address[0])
                host = host[7:] if host.startswith("::ffff:") else host
                if not is_local_address(host):
                    self.on_event(f"Refused a connection from {host}: not on the local network.")
                    _hard_close(conn)
                    continue
                with self._lock:
                    self._client = conn
                used, exchange = exchange, None
                try:
                    outcome = self._handle(conn, host, used)
                except AuthError:
                    attempts += 1
                    left = self.max_attempts - attempts
                    self.on_event(f"Wrong code from {host} ({attempts}/{self.max_attempts}).")
                    time.sleep(1.0)
                    if left <= 0:
                        return ListenerOutcome(False, "Too many wrong codes; stopped listening.")
                    continue
                except (ProtocolError, ConnectionClosedError) as exc:
                    if self._cancel.is_set():
                        return ListenerOutcome(False, "Stopped accepting connections.")
                    self.on_event(f"Dropped a connection from {host}: {exc}")
                    time.sleep(0.2)
                    continue
                finally:
                    with self._lock:
                        self._client = None
                if outcome is not None:
                    return outcome
        finally:
            self.close()

    def close(self) -> None:
        with self._lock:
            if self._server is not None:
                _hard_close(self._server)
                self._server = None

    # ------------------------------------------------------------- session
    def _handle(self, conn: socket.socket, host: str, exchange: ServerKeyExchange) -> ListenerOutcome | None:
        watchdog = threading.Timer(HANDSHAKE_DEADLINE, _shutdown_quietly, (conn,))
        watchdog.daemon = True
        watchdog.start()
        try:
            channel, mode = server_handshake(
                conn, exchange=exchange, identity=self.identity, modes=self._modes
            )
        except (SecurityError, SyncError, OSError) as exc:
            _hard_close(conn)
            if isinstance(exc, AuthError | ProtocolError | ConnectionClosedError):
                raise
            if isinstance(exc, OSError):
                raise ConnectionClosedError(str(exc)) from exc
            # A bad signature after a correct code: count it as a failed attempt.
            raise AuthError(str(exc)) from exc
        finally:
            watchdog.cancel()

        # From here the code has been used: whatever happens, stop listening.
        try:
            if mode == MODE_PAIR:
                return self._pair(channel, host)
            return self._session(channel, host)
        except RemoteError as exc:
            return ListenerOutcome(False, f"The other device stopped: {exc}")
        except (SyncError, OSError) as exc:
            # Our own error texts can name local absolute paths; the peer
            # only needs to know the operation stopped.
            channel.send_error(
                "The other device refused an unsafe request."
                if isinstance(exc, SecurityError) else "The operation failed on the other device."
            )
            return ListenerOutcome(False, str(exc))
        finally:
            channel.close()

    def _pair(self, channel: SecureChannel, host: str) -> ListenerOutcome:
        known = self.contacts.by_key(channel.peer_key)
        request = Request(
            "pair", channel.peer_name, fingerprint(channel.peer_key), host, known=known is not None
        )
        if not self.approve(request):
            channel.send_error("The other user declined the contact request.")
            return ListenerOutcome(False, "Contact request declined.")
        channel.send({"t": "paired"})
        reply = channel.recv("ok")
        port = reply.get("port")
        if not isinstance(port, int) or isinstance(port, bool) or not 1024 <= port <= 65535:
            port = self.identity.port
        contact = self.contacts.add(channel.peer_key, channel.peer_name, host, port)
        self.on_event(f"Added “{contact.name}” to your contacts.")
        return ListenerOutcome(True, f"“{contact.name}” is now a contact.", contact=contact)

    def _session(self, channel: SecureChannel, host: str) -> ListenerOutcome:
        assert self.vault is not None
        contact = self.contacts.by_key(channel.peer_key)
        if contact is None:
            channel.send_error("This device is not in the other device's contacts. Pair first.")
            return ListenerOutcome(False, f"Refused {channel.peer_name} ({host}): not a contact.")
        message = channel.recv("request")
        op = message.get("op")
        if op not in OPERATIONS:
            raise ProtocolError("Unknown operation requested.")
        request = Request(
            op, contact.name, contact.fingerprint, host, vault=self.vault.name, known=True
        )
        if not self.approve(request):
            channel.send_error("The other user declined the request.")
            return ListenerOutcome(False, "Request declined.")
        if contact.host != host:
            self.contacts.update(contact, host=host)
        channel.send({"t": "accepted", "name": self.vault.name})
        report = _serve_operation(channel, self.vault, contact, op, self.on_event)
        return ListenerOutcome(True, f"{op} with “{contact.name}”: {report.summary()}", report=report)


def _shutdown_quietly(sock: socket.socket) -> None:
    with contextlib.suppress(OSError):
        sock.shutdown(socket.SHUT_RDWR)


def _hard_close(sock: socket.socket) -> None:
    _shutdown_quietly(sock)
    sock.close()


def _serve_operation(
    channel: SecureChannel, root: Path, contact: Contact, op: str, on_event: EventFn
) -> Report:
    report = Report(op, contact.name)
    base = files.load_base(root, contact.id)
    manifest = files.scan(root, base)
    _send_manifest(channel, "manifest", manifest)
    allowed = _ALLOWED[op]
    budget = _Budget()
    requests = 0

    while True:
        message = channel.recv("get", "put", "delete", "finish", timeout=APPROVAL_TIMEOUT)
        kind = message["t"]
        requests += 1
        if requests > 3 * files.MAX_FILES + 10:
            raise ProtocolError("Too many requests.")
        if kind == "finish":
            break
        if kind not in allowed:
            raise SecurityError(f"The other device asked for “{kind}”, which {op} does not allow.")

        if kind == "get":
            rel = files.validate_rel_path(message.get("path"))
            if rel not in manifest:
                raise SecurityError("The other device asked for a file that was not offered.")
            if _send_file(channel, root, rel):
                report.sent.append(rel)
                on_event(f"Sent {rel}")
            continue

        if kind == "delete":
            rel = files.validate_rel_path(message.get("path"))
            expected = _optional_sha(message.get("sha256"))
            if expected is None:
                raise ProtocolError("Malformed delete request.")
            if files.current_sha(root, rel) != expected:
                channel.send({"t": "nack", "reason": "changed here since the file list was sent"})
                report.skipped.append((rel, "changed during the sync"))
                continue
            files.move_to_trash(root, rel)
            report.trashed.append(rel)
            channel.send({"t": "ack"})
            on_event(f"Moved {rel} to .trash")
            continue

        # put
        rel, size, digest = _announce(message)
        replace = _optional_sha(message.get("replace"))
        keep_as = message.get("keep_as")
        if keep_as is not None:
            keep_as = files.validate_rel_path(keep_as)
        budget.spend(size)
        incoming = IncomingFile(root, rel, size, digest)
        try:
            _receive_body(channel, incoming, size)
            # Check the data *before* touching anything: a conflict rename
            # must never happen for a file that then fails to arrive intact.
            incoming.verify()
            if files.current_sha(root, rel) != replace:
                raise SyncError("changed here since the file list was sent")
            if keep_as is not None:
                if replace is None:
                    raise ProtocolError("A conflict copy needs an existing file.")
                files.rename_within(root, rel, keep_as)
                report.conflicts[rel] = keep_as
                incoming.commit(expect_current=None)
            else:
                incoming.commit(expect_current=replace)
        except (ProtocolError, SecurityError):
            incoming.abort()
            raise
        except (SyncError, OSError) as exc:
            incoming.abort()
            reason = str(exc) if isinstance(exc, SyncError) else (exc.strerror or str(exc))
            channel.send({"t": "nack", "reason": reason[:200]})
            report.skipped.append((rel, reason))
            continue
        report.received.append(rel)
        channel.send({"t": "ack"})
        on_event(f"Received {rel}")

    # Both sides record what they now have in common, which is what the next
    # sync compares against to tell edits from deletions.
    final = files.scan(root, manifest)
    _send_manifest(channel, "final", final)
    common: dict[str, Entry] = {}
    pages = 0
    while True:
        message = channel.recv("commit")
        rows = message.get("files")
        pages += 1
        if not isinstance(rows, list) or len(rows) > _MANIFEST_BATCH:
            raise ProtocolError("Malformed commit.")
        if pages * _MANIFEST_BATCH > files.MAX_FILES + _MANIFEST_BATCH:
            raise ProtocolError("The other device sent too many entries.")
        for row in rows:
            entry = files.entry_from_wire(row)
            mine = final.get(entry.path)
            if mine is not None and mine.sha256 == entry.sha256:
                common[entry.path] = mine
        if message.get("more") is not True:
            break
    files.save_base(root, contact.id, common.values())
    channel.send({"t": "done"})
    return report


# =================================================================== client
def pair(
    identity: Identity,
    contacts: ContactBook,
    host: str,
    port: int,
    code: str,
    *,
    on_event: EventFn = _quiet,
) -> Contact:
    """Pair with a device that is accepting connections; both store each other."""
    sock = _connect(host, port)
    channel: SecureChannel | None = None
    try:
        channel = client_handshake(sock, code=code, mode=MODE_PAIR, identity=identity)
        if channel.peer_key == identity.public_bytes:
            raise SecurityError("That is this device.")
        on_event(f"Connected to “{channel.peer_name}”; waiting for them to accept…")
        channel.recv("paired", timeout=APPROVAL_TIMEOUT)
        contact = contacts.add(channel.peer_key, channel.peer_name, host, port)
        channel.send({"t": "ok", "port": identity.port})
        return contact
    finally:
        if channel is not None:
            channel.close()
        else:
            _hard_close(sock)


def transfer(
    identity: Identity,
    contact: Contact,
    root: Path,
    op: str,
    code: str,
    *,
    on_event: EventFn = _quiet,
) -> Report:
    """Run ``op`` (pull/push/sync) between ``root`` and ``contact``'s vault."""
    if op not in OPERATIONS:
        raise SyncError(f"Unknown operation {op!r}.")
    root = root.resolve()
    if not root.is_dir():
        raise SyncError(f"Not a folder: {root}")
    sock = _connect(contact.host, contact.port)
    channel: SecureChannel | None = None
    try:
        channel = client_handshake(sock, code=code, mode=MODE_SESSION, identity=identity)
        if channel.peer_key != contact.public_key:
            raise SecurityError(
                f"The device at {contact.address} is not “{contact.name}”: its identity key is "
                "different. Someone may be impersonating it; nothing was sent."
            )
        channel.send({"t": "request", "op": op})
        on_event(f"Waiting for “{contact.name}” to accept…")
        channel.recv("accepted", timeout=APPROVAL_TIMEOUT)
        return _run_operation(channel, root, contact, op, on_event)
    finally:
        if channel is not None:
            channel.close()
        else:
            _hard_close(sock)


def _expect_ack(channel: SecureChannel) -> str | None:
    """None on success, the reason on a refusal."""
    reply = channel.recv("ack", "nack")
    if reply["t"] == "ack":
        return None
    return clean_message(reply.get("reason"))[:200]


class _Budget:
    """Caps the total bytes one session may write, whatever the peer claims."""

    def __init__(self, limit: int = files.MAX_TOTAL_SIZE) -> None:
        self.left = limit

    def spend(self, size: int) -> None:
        self.left -= size
        if self.left < 0:
            raise SecurityError("The other device sent more data than the sharing limit.")


def _download(channel: SecureChannel, root: Path, rel: str, save_as: str,
              expect_current: str | None, budget: _Budget) -> str | None:
    """Fetch ``rel`` and store it at ``save_as``.  Returns a skip reason or None."""
    channel.send({"t": "get", "path": rel})
    header = channel.recv("file", "missing")
    if header.get("path") != rel:
        raise ProtocolError("The other device answered with a different file.")
    if header["t"] == "missing":
        return "no longer exists on the other device"
    _, size, digest = _announce(header)
    budget.spend(size)
    incoming = IncomingFile(root, save_as, size, digest)
    try:
        _receive_body(channel, incoming, size)
        incoming.commit(expect_current=expect_current)
    except (ProtocolError, SecurityError):
        incoming.abort()
        raise
    except (SyncError, OSError) as exc:
        incoming.abort()
        return str(exc) if isinstance(exc, SyncError) else (exc.strerror or str(exc))
    return None


def _upload(channel: SecureChannel, root: Path, rel: str, replace: str | None,
            keep_as: str | None = None) -> str | None:
    try:
        path = files.safe_target(root, rel)
        data = path.read_bytes()
    except OSError as exc:
        return exc.strerror or str(exc)
    if len(data) > files.MAX_FILE_SIZE:
        return "too large"
    channel.send({
        "t": "put", "path": rel, "size": len(data), "sha256": hashlib.sha256(data).hexdigest(),
        "replace": replace, "keep_as": keep_as,
    })
    for start in range(0, len(data), CHUNK):
        channel.send_data(data[start : start + CHUNK])
    return _expect_ack(channel)


def _run_operation(
    channel: SecureChannel, root: Path, contact: Contact, op: str, on_event: EventFn
) -> Report:
    report = Report(op, contact.name)
    # The other side may be hashing a large vault for the first time.
    remote = _recv_manifest(channel, "manifest", timeout=APPROVAL_TIMEOUT)
    base = files.load_base(root, contact.id)
    local = files.scan(root, base)
    plan: Plan = files.compute_plan(op, local, remote, base, contact.name)
    on_event(
        f"Plan: {len(plan.download)} to receive, {len(plan.upload)} to send, "
        f"{len(plan.delete_local)} to trash here, {len(plan.delete_remote)} to trash there, "
        f"{len(plan.conflicts)} conflict(s)."
    )
    sha = lambda m, p: m[p].sha256 if p in m else None  # noqa: E731
    budget = _Budget()

    for rel, copy in plan.conflicts.items():
        # The other device's version is kept beside ours under ``copy`` on
        # both sides; ours stays at ``rel`` on both sides.
        reason = _download(channel, root, rel, copy, expect_current=None, budget=budget)
        if reason is None:
            reason = _upload(channel, root, rel, sha(remote, rel), keep_as=copy)
        if reason is None:
            report.conflicts[rel] = copy
            on_event(f"Conflict in {rel}: their version kept as {copy}")
        else:
            report.skipped.append((rel, reason))

    for rel in plan.download:
        reason = _download(channel, root, rel, rel, expect_current=sha(local, rel), budget=budget)
        if reason is None:
            report.received.append(rel)
            on_event(f"Received {rel}")
        else:
            report.skipped.append((rel, reason))

    for rel in plan.upload:
        reason = _upload(channel, root, rel, sha(remote, rel))
        if reason is None:
            report.sent.append(rel)
            on_event(f"Sent {rel}")
        else:
            report.skipped.append((rel, reason))

    for rel in plan.delete_remote:
        channel.send({"t": "delete", "path": rel, "sha256": sha(remote, rel)})
        reason = _expect_ack(channel)
        if reason is None:
            report.trashed_remote.append(rel)
            on_event(f"Moved {rel} to .trash on {contact.name}")
        else:
            report.skipped.append((rel, reason))

    for rel in plan.delete_local:
        try:
            if files.current_sha(root, rel) == sha(local, rel):
                files.move_to_trash(root, rel)
                report.trashed.append(rel)
                on_event(f"Moved {rel} to .trash")
            else:
                report.skipped.append((rel, "changed during the sync"))
        except OSError as exc:
            report.skipped.append((rel, exc.strerror or str(exc)))

    channel.send({"t": "finish"})
    final_remote = _recv_manifest(channel, "final", timeout=APPROVAL_TIMEOUT)
    final_local = files.scan(root, local)
    common = _common(final_local, final_remote)
    rows = [e.to_wire() for e in common]
    for start in range(0, max(len(rows), 1), _MANIFEST_BATCH):
        channel.send({
            "t": "commit", "files": rows[start : start + _MANIFEST_BATCH],
            "more": start + _MANIFEST_BATCH < len(rows),
        })
    channel.recv("done")
    files.save_base(root, contact.id, common)
    return report
