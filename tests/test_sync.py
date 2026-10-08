# SPDX-License-Identifier: GPL-3.0-or-later
"""Vault sharing: pairing, pull/push/sync, and the attacks it must withstand.

Two "devices" run in one process, each with its own key and contact list,
talking over loopback — the same sockets and the same code path as two
machines on a LAN.
"""

from __future__ import annotations

import contextlib
import hashlib
import socket
import threading
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pytest

from app.core.sync import channel as channel_mod
from app.core.sync import files, session
from app.core.sync.channel import (
    MODE_PAIR,
    MODE_SESSION,
    SecureChannel,
    ServerKeyExchange,
    client_handshake,
    normalize_code,
    server_handshake,
)
from app.core.sync.errors import (
    AuthError,
    ConnectionClosedError,
    ProtocolError,
    RemoteError,
    SecurityError,
    SyncError,
    UnsafePathError,
)
from app.core.sync.session import Listener, is_local_address, pair, transfer
from app.core.sync.store import ContactBook, clean_name, load_identity
from tests.conftest import requires_posix_permissions, requires_symlinks

LOOPBACK = "127.0.0.1"


@pytest.fixture(autouse=True)
def _sync_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EMDEE_SYNC_HOME", str(tmp_path / "shared-state"))
    # Keep failing-code tests fast.
    monkeypatch.setattr(session.time, "sleep", lambda _s: None)


class Device:
    def __init__(self, root: Path, name: str) -> None:
        self.config = root / f"{name}-config"
        self.vault = root / f"{name}-vault"
        self.vault.mkdir(parents=True)
        self.identity = replace(load_identity(self.config), name=name)
        self.contacts = ContactBook(self.config)

    def write(self, rel: str, text: str) -> Path:
        path = self.vault / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def read(self, rel: str) -> str:
        return (self.vault / rel).read_text(encoding="utf-8")

    def listener(self, approve: Callable[[session.Request], bool] | None = None, **kw: object) -> Listener:
        self.requests: list[session.Request] = []

        def _approve(req: session.Request) -> bool:
            self.requests.append(req)
            return approve(req) if approve else True

        listener = Listener(
            self.identity, self.contacts, vault=kw.pop("vault", self.vault),  # type: ignore[arg-type]
            approve=_approve, port=0, seconds=kw.pop("seconds", 20), **kw,  # type: ignore[arg-type]
        )
        listener.open()
        return listener


class Serving:
    """Run a listener on a background thread for the duration of a block."""

    def __init__(self, listener: Listener) -> None:
        self.listener = listener
        self.outcome: session.ListenerOutcome | None = None
        self.error: BaseException | None = None
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        try:
            self.outcome = self.listener.serve()
        except BaseException as exc:  # noqa: BLE001 - surfaced by the test
            self.error = exc

    def __enter__(self) -> Serving:
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.wait()

    def wait(self) -> session.ListenerOutcome:
        self._thread.join(30)
        if self._thread.is_alive():
            self.listener.cancel()
            self._thread.join(5)
            raise AssertionError("listener did not finish")
        if self.error:
            raise self.error
        assert self.outcome is not None
        return self.outcome


@pytest.fixture
def alice(tmp_path: Path) -> Device:
    return Device(tmp_path, "alice")


@pytest.fixture
def bob(tmp_path: Path) -> Device:
    return Device(tmp_path, "bob")


def _pair(a: Device, b: Device) -> None:
    """a dials b; both end up with each other as contacts."""
    listener = b.listener()
    with Serving(listener) as srv:
        pair(a.identity, a.contacts, LOOPBACK, listener.port, listener.code)
    assert srv.outcome and srv.outcome.ok
    # Point a's contact at the ephemeral port b will use next time.
    contact = a.contacts.by_key(b.identity.public_bytes)
    assert contact is not None


def _run(a: Device, b: Device, op: str, **listener_kw: object) -> tuple[session.Report, session.ListenerOutcome]:
    listener = b.listener(**listener_kw)
    contact = a.contacts.by_key(b.identity.public_bytes)
    assert contact is not None
    contact = a.contacts.update(contact, host=LOOPBACK, port=listener.port)
    with Serving(listener) as srv:
        report = transfer(a.identity, contact, a.vault, op, listener.code)
    return report, srv.wait()


@pytest.fixture
def paired(alice: Device, bob: Device) -> tuple[Device, Device]:
    _pair(alice, bob)
    return alice, bob


# ================================================================= pairing
def test_pairing_is_mutual(alice: Device, bob: Device) -> None:
    _pair(alice, bob)
    on_alice = alice.contacts.by_key(bob.identity.public_bytes)
    on_bob = bob.contacts.by_key(alice.identity.public_bytes)
    assert on_alice is not None and on_alice.name == "bob"
    assert on_bob is not None and on_bob.name == "alice"
    assert on_bob.host == LOOPBACK
    assert bob.requests[0].kind == "pair"
    # Persisted and reloadable.
    assert ContactBook(alice.config).by_key(bob.identity.public_bytes) is not None


def test_pairing_declined_adds_nobody(alice: Device, bob: Device) -> None:
    listener = bob.listener(approve=lambda _r: False)
    with Serving(listener) as srv, pytest.raises(RemoteError):
        pair(alice.identity, alice.contacts, LOOPBACK, listener.port, listener.code)
    assert not srv.wait().ok
    assert alice.contacts.all() == [] and bob.contacts.all() == []


@requires_posix_permissions
def test_identity_and_contacts_are_private(paired: tuple[Device, Device]) -> None:
    alice, _ = paired
    assert (alice.config / "identity.key").stat().st_mode & 0o777 == 0o600
    assert (alice.config / "contacts.json").stat().st_mode & 0o777 == 0o600
    assert alice.config.stat().st_mode & 0o077 == 0


def test_identity_is_stable(alice: Device) -> None:
    again = load_identity(alice.config)
    assert again.public_bytes == alice.identity.public_bytes


# ================================================================ transfers
def test_pull_copies_vault(paired: tuple[Device, Device]) -> None:
    alice, bob = paired
    bob.write("Index.md", "# Index\n[[Cell]]\n")
    bob.write("bio/Cell.md", "# Cell\n")
    (bob.vault / "assets").mkdir()
    (bob.vault / "assets" / "fig.png").write_bytes(bytes(range(256)) * 4000)
    bob.write(".obsidian/config.md", "hidden")
    bob.write("script.sh", "rm -rf /")
    report, outcome = _run(alice, bob, "pull")
    assert outcome.ok, outcome.message
    assert sorted(report.received) == ["Index.md", "assets/fig.png", "bio/Cell.md"]
    assert alice.read("bio/Cell.md") == "# Cell\n"
    assert (alice.vault / "assets" / "fig.png").read_bytes() == bytes(range(256)) * 4000
    assert not (alice.vault / ".obsidian").exists()
    assert not (alice.vault / "script.sh").exists()
    assert bob.requests[-1].kind == "pull"


def test_pull_never_writes_to_the_server(paired: tuple[Device, Device]) -> None:
    alice, bob = paired
    alice.write("mine.md", "only here")
    _run(alice, bob, "pull")
    assert not (bob.vault / "mine.md").exists()


def test_push_and_trash(paired: tuple[Device, Device]) -> None:
    alice, bob = paired
    alice.write("a.md", "v1")
    bob.write("a.md", "bob's old")
    report, outcome = _run(alice, bob, "push")
    assert outcome.ok
    assert report.sent == ["a.md"]
    assert bob.read("a.md") == "v1"
    trashed = list((bob.vault / ".trash").rglob("*.md"))
    assert [p.read_text() for p in trashed] == ["bob's old"]


def test_sync_two_way_with_deletions_and_conflicts(paired: tuple[Device, Device]) -> None:
    alice, bob = paired
    alice.write("shared.md", "base")
    alice.write("gone.md", "will be deleted by bob")
    alice.write("both.md", "base")
    _run(alice, bob, "sync")
    assert bob.read("shared.md") == "base" and bob.read("gone.md").startswith("will")

    # Independent changes on both sides.
    alice.write("from-alice.md", "a")
    bob.write("from-bob.md", "b")
    (bob.vault / "gone.md").unlink()
    alice.write("shared.md", "alice edit")
    alice.write("both.md", "alice version")
    bob.write("both.md", "bob version")

    report, outcome = _run(alice, bob, "sync")
    assert outcome.ok, outcome.message
    assert bob.read("from-alice.md") == "a"
    assert alice.read("from-bob.md") == "b"
    assert bob.read("shared.md") == "alice edit"
    assert not (alice.vault / "gone.md").exists()
    assert any(p.name == "gone.md" for p in (alice.vault / ".trash").rglob("*"))
    copy = report.conflicts["both.md"]
    for device in (alice, bob):
        assert device.read("both.md") == "alice version"
        assert device.read(copy) == "bob version"

    # A third sync has nothing to do: the shared base was recorded on both sides.
    report, _ = _run(alice, bob, "sync")
    assert not (report.received or report.sent or report.trashed or report.trashed_remote)
    # …and the base works the other way round too (bob initiating).
    bob.write("later.md", "x")
    report, _ = _run(bob, alice, "sync")
    assert report.sent == ["later.md"] and not report.trashed_remote


def test_edit_beats_deletion(paired: tuple[Device, Device]) -> None:
    alice, bob = paired
    alice.write("n.md", "v1")
    _run(alice, bob, "sync")
    (alice.vault / "n.md").unlink()
    bob.write("n.md", "v2 edited")
    _run(alice, bob, "sync")
    assert alice.read("n.md") == "v2 edited"


def test_request_declined(paired: tuple[Device, Device]) -> None:
    alice, bob = paired
    bob.write("x.md", "x")
    with pytest.raises(RemoteError, match="declined"):
        _run(alice, bob, "pull", approve=lambda _r: False)
    assert not (alice.vault / "x.md").exists()


def test_listener_serves_one_operation_then_closes(paired: tuple[Device, Device]) -> None:
    alice, bob = paired
    listener = bob.listener()
    contact = alice.contacts.update(
        alice.contacts.by_key(bob.identity.public_bytes), host=LOOPBACK, port=listener.port  # type: ignore[arg-type]
    )
    with Serving(listener):
        transfer(alice.identity, contact, alice.vault, "pull", listener.code)
    # Same code, second time: the port is closed.
    with pytest.raises(SyncError):
        transfer(alice.identity, contact, alice.vault, "pull", listener.code)


# =================================================================== attacks
def test_wrong_code_is_rejected_and_attempts_are_capped(paired: tuple[Device, Device]) -> None:
    alice, bob = paired
    bob.write("secret.md", "s")
    listener = bob.listener(max_attempts=3)
    contact = alice.contacts.update(
        alice.contacts.by_key(bob.identity.public_bytes), host=LOOPBACK, port=listener.port  # type: ignore[arg-type]
    )
    wrong = f"{(int(listener.code) + 1) % 10**8:08d}"
    with Serving(listener) as srv:
        for _ in range(3):
            with pytest.raises(AuthError):
                transfer(alice.identity, contact, alice.vault, "pull", wrong)
    outcome = srv.wait()
    assert not outcome.ok and "Too many wrong codes" in outcome.message
    # Even the right code is useless now.
    with pytest.raises(SyncError):
        transfer(alice.identity, contact, alice.vault, "pull", listener.code)
    assert not (alice.vault / "secret.md").exists()
    assert bob.requests == []


def test_unknown_device_with_right_code_gets_nothing(tmp_path: Path, paired: tuple[Device, Device]) -> None:
    _alice, bob = paired
    bob.write("secret.md", "s")
    mallory = Device(tmp_path, "mallory")
    listener = bob.listener()
    # Mallory forges a contact entry pointing at bob's real key.
    fake = mallory.contacts.add(bob.identity.public_bytes, "bob", LOOPBACK, listener.port)
    with Serving(listener) as srv, pytest.raises(RemoteError, match="not in the other device's contacts"):
        transfer(mallory.identity, fake, mallory.vault, "pull", listener.code)
    assert not srv.wait().ok
    assert not (mallory.vault / "secret.md").exists()
    assert bob.requests == []


def test_impostor_server_is_detected(tmp_path: Path, paired: tuple[Device, Device]) -> None:
    """Someone else answers at bob's address (even with a code the user was
    tricked into typing): alice notices the key is not bob's and sends nothing."""
    alice, bob = paired
    mallory = Device(tmp_path, "mallory")
    mallory.contacts.add(alice.identity.public_bytes, "alice", LOOPBACK, 1)
    alice.write("private.md", "do not leak")
    listener = mallory.listener()
    contact = alice.contacts.update(
        alice.contacts.by_key(bob.identity.public_bytes), host=LOOPBACK, port=listener.port  # type: ignore[arg-type]
    )
    with Serving(listener), pytest.raises(SecurityError, match="identity key is different"):
        transfer(alice.identity, contact, alice.vault, "push", listener.code)
    assert not (mallory.vault / "private.md").exists()


def _proxy(target_port: int, mutate: Callable[[int, bytes], bytes | None]) -> tuple[int, threading.Thread]:
    """A man in the middle that can rewrite frames client→server."""
    server = socket.create_server((LOOPBACK, 0))
    port = server.getsockname()[1]

    def pump(src: socket.socket, dst: socket.socket, upstream: bool) -> None:
        index = 0
        try:
            while True:
                header = channel_mod._recv_exact(src, 4)
                body = channel_mod._recv_exact(src, int.from_bytes(header, "big"))
                out = mutate(index, body) if upstream else body
                index += 1
                if out is None:
                    continue
                dst.sendall(len(out).to_bytes(4, "big") + out)
        except (SyncError, OSError):
            pass
        finally:
            for s in (src, dst):
                with contextlib.suppress(OSError):
                    s.shutdown(socket.SHUT_RDWR)

    def run() -> None:
        conn, _ = server.accept()
        upstream = socket.create_connection((LOOPBACK, target_port))
        t = threading.Thread(target=pump, args=(upstream, conn, False), daemon=True)
        t.start()
        pump(conn, upstream, True)
        t.join(5)
        server.close()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return port, thread


@pytest.mark.parametrize("frame", [0, 1, 2, 3])
def test_tampered_frames_abort(paired: tuple[Device, Device], frame: int) -> None:
    alice, bob = paired
    bob.write("x.md", "x")

    def flip(index: int, body: bytes) -> bytes:
        if index == frame:
            body = bytearray(body)
            body[len(body) // 2] ^= 0x01
            return bytes(body)
        return body

    listener = bob.listener()
    port, _ = _proxy(listener.port, flip)
    contact = alice.contacts.update(
        alice.contacts.by_key(bob.identity.public_bytes), host=LOOPBACK, port=port  # type: ignore[arg-type]
    )
    with Serving(listener) as srv, pytest.raises(SyncError):
        transfer(alice.identity, contact, alice.vault, "pull", listener.code)
    srv.listener.cancel()
    assert not (alice.vault / "x.md").exists()


def test_dropped_or_replayed_frames_abort(paired: tuple[Device, Device]) -> None:
    alice, bob = paired
    bob.write("x.md", "x")
    seen: list[bytes] = []

    def replay(index: int, body: bytes) -> bytes | None:
        seen.append(body)
        if index == 3:
            return seen[2]  # replay the previous encrypted frame instead
        return body

    listener = bob.listener()
    port, _ = _proxy(listener.port, replay)
    contact = alice.contacts.update(
        alice.contacts.by_key(bob.identity.public_bytes), host=LOOPBACK, port=port  # type: ignore[arg-type]
    )
    with Serving(listener) as srv, pytest.raises(SyncError):
        transfer(alice.identity, contact, alice.vault, "pull", listener.code)
    outcome = srv.wait()
    assert not outcome.ok
    assert "integrity" in outcome.message


def test_traffic_is_encrypted(paired: tuple[Device, Device]) -> None:
    alice, bob = paired
    marker = "TOP-SECRET-NOTE-CONTENT-" * 10
    alice.write("Secret note name.md", marker)
    captured = bytearray()

    def record(_index: int, body: bytes) -> bytes:
        captured.extend(body)
        return body

    listener = bob.listener()
    port, thread = _proxy(listener.port, record)
    contact = alice.contacts.update(
        alice.contacts.by_key(bob.identity.public_bytes), host=LOOPBACK, port=port  # type: ignore[arg-type]
    )
    with Serving(listener):
        transfer(alice.identity, contact, alice.vault, "push", listener.code)
    thread.join(5)
    assert bob.read("Secret note name.md") == marker
    assert b"TOP-SECRET" not in captured
    assert b"Secret note name" not in captured
    assert alice.identity.name.encode() not in captured


# ------------------------------------------------------- hostile peer (raw)
class RawPeer:
    """An authenticated, *paired* but malicious client speaking the protocol
    by hand, to check that the listener validates everything itself."""

    def __init__(self, alice: Device, bob: Device, op: str, **listener_kw: object) -> None:
        self.listener = bob.listener(**listener_kw)
        self.serving = Serving(self.listener).__enter__()
        sock = socket.create_connection((LOOPBACK, self.listener.port))
        self.ch = client_handshake(sock, code=self.listener.code, mode=MODE_SESSION, identity=alice.identity)
        self.ch.send({"t": "request", "op": op})
        self.ch.recv("accepted")
        self.manifest = session._recv_manifest(self.ch, "manifest")

    def put(self, rel: str, data: bytes, replace: str | None = None, keep_as: str | None = None) -> None:
        self.ch.send({"t": "put", "path": rel, "size": len(data),
                      "sha256": hashlib.sha256(data).hexdigest(), "replace": replace, "keep_as": keep_as})
        if data:
            self.ch.send_data(data)

    def outcome(self) -> session.ListenerOutcome:
        self.ch.close()
        return self.serving.wait()


EVIL_PATHS = [
    "../escape.md", "a/../../escape.md", "/etc/passwd.md", "C:/Windows/x.md", "a\\..\\x.md",
    ".trash/x.md", ".git/hooks/pre-commit.md", "a/.hidden/x.md", "x.exe", "x.md.sh", "CON.md",
    "nul.txt", "a:b.md", "x.md ", "x.md.", "", "a//b.md", "./a.md", "a\x00.md", "a\nb.md",
    "\u202eevil.md", "x" * 500 + ".md", "node_modules/x.md", "GIT~1/x.md", "TRASH~1/x.md",
]


@pytest.mark.parametrize("rel", EVIL_PATHS)
def test_validate_rejects_evil_paths(rel: str) -> None:
    with pytest.raises(UnsafePathError):
        files.validate_rel_path(rel)


@pytest.mark.parametrize("rel", ["../escape.md", "/tmp/abs.md", ".trash/x.md", "x.desktop"])
def test_listener_refuses_path_traversal(paired: tuple[Device, Device], rel: str) -> None:
    alice, bob = paired
    peer = RawPeer(alice, bob, "push")
    peer.put(rel, b"pwned")
    with pytest.raises((RemoteError, ConnectionClosedError, SecurityError)):
        peer.ch.recv("ack")
    outcome = peer.outcome()
    assert not outcome.ok
    assert not (bob.vault.parent / "escape.md").exists()
    assert not Path("/tmp/abs.md").exists() or Path("/tmp/abs.md").read_bytes() != b"pwned"


def test_listener_enforces_operation_permissions(paired: tuple[Device, Device]) -> None:
    alice, bob = paired
    bob.write("keep.md", "keep")
    peer = RawPeer(alice, bob, "pull")
    peer.put("new.md", b"injected")
    with pytest.raises(RemoteError, match="unsafe request"):
        peer.ch.recv("ack")
    peer.outcome()
    assert not (bob.vault / "new.md").exists()

    peer = RawPeer(alice, bob, "pull")
    peer.ch.send({"t": "delete", "path": "keep.md", "sha256": hashlib.sha256(b"keep").hexdigest()})
    with pytest.raises(RemoteError):
        peer.ch.recv("ack")
    peer.outcome()
    assert bob.read("keep.md") == "keep"


def test_listener_only_serves_offered_files(paired: tuple[Device, Device]) -> None:
    alice, bob = paired
    (bob.vault.parent / "outside.md").write_text("outside")
    bob.write(".hidden/secret.md", "hidden")
    peer = RawPeer(alice, bob, "pull")
    peer.ch.send({"t": "get", "path": ".hidden/secret.md"})
    with pytest.raises(RemoteError):
        peer.ch.recv("file")
    peer.outcome()


def test_listener_rejects_stale_overwrite(paired: tuple[Device, Device]) -> None:
    alice, bob = paired
    bob.write("n.md", "bob's current")
    peer = RawPeer(alice, bob, "push")
    peer.put("n.md", b"blind overwrite", replace="0" * 64)
    reply = peer.ch.recv("ack", "nack")
    assert reply["t"] == "nack"
    peer.ch.send({"t": "delete", "path": "n.md", "sha256": "0" * 64})
    assert peer.ch.recv("ack", "nack")["t"] == "nack"
    peer.outcome()
    assert bob.read("n.md") == "bob's current"


def test_listener_rejects_lying_checksum(paired: tuple[Device, Device]) -> None:
    alice, bob = paired
    peer = RawPeer(alice, bob, "push")
    peer.ch.send({"t": "put", "path": "n.md", "size": 4, "sha256": "0" * 64, "replace": None})
    peer.ch.send_data(b"evil")
    assert peer.ch.recv("ack", "nack")["t"] == "nack"
    peer.outcome()
    assert not (bob.vault / "n.md").exists()
    assert not list(bob.vault.rglob("*.part"))


def test_listener_rejects_oversized_data(paired: tuple[Device, Device]) -> None:
    alice, bob = paired
    peer = RawPeer(alice, bob, "push")
    peer.ch.send({"t": "put", "path": "n.md", "size": 2, "sha256": "0" * 64, "replace": None})
    peer.ch.send_data(b"too long")
    with pytest.raises((RemoteError, ConnectionClosedError)):
        peer.ch.recv("ack", "nack")
    assert not peer.outcome().ok
    assert not (bob.vault / "n.md").exists()


@requires_symlinks
def test_symlink_in_vault_cannot_redirect_writes(paired: tuple[Device, Device], tmp_path: Path) -> None:
    alice, bob = paired
    outside = tmp_path / "outside"
    outside.mkdir()
    (bob.vault / "notes").symlink_to(outside, target_is_directory=True)
    peer = RawPeer(alice, bob, "push")
    peer.put("notes/x.md", b"through the link")
    with pytest.raises((RemoteError, ConnectionClosedError)):
        peer.ch.recv("ack")
    peer.outcome()
    assert list(outside.iterdir()) == []


@requires_symlinks
def test_symlinks_are_never_shared(paired: tuple[Device, Device], tmp_path: Path) -> None:
    alice, bob = paired
    secret = tmp_path / "secret.md"
    secret.write_text("ssh keys etc")
    (bob.vault / "link.md").symlink_to(secret)
    bob.write("real.md", "ok")
    report, _ = _run(alice, bob, "pull")
    assert report.received == ["real.md"]


def test_malicious_manifest_is_rejected(alice: Device, bob: Device, tmp_path: Path) -> None:
    """A hostile *listener* sends a manifest with a traversal path."""
    _pair(alice, bob)
    contact = alice.contacts.by_key(bob.identity.public_bytes)
    assert contact is not None
    server = socket.create_server((LOOPBACK, 0))
    port = server.getsockname()[1]
    contact = alice.contacts.update(contact, host=LOOPBACK, port=port)
    code = "12345678"

    def evil() -> None:
        conn, _ = server.accept()
        ch, _mode = server_handshake(conn, exchange=ServerKeyExchange(code), identity=bob.identity,
                                     modes=(MODE_SESSION,))
        ch.recv("request")
        ch.send({"t": "accepted"})
        ch.send({"t": "manifest", "files": [["../../evil.md", 1, "0" * 64]], "more": False})
        with contextlib.suppress(SyncError):
            ch.recv()
        ch.close()
        server.close()

    thread = threading.Thread(target=evil, daemon=True)
    thread.start()
    with pytest.raises(UnsafePathError):
        transfer(alice.identity, contact, alice.vault, "pull", code)
    thread.join(5)
    assert not (tmp_path / "evil.md").exists()


def test_raw_garbage_does_not_cost_an_attempt(paired: tuple[Device, Device]) -> None:
    alice, bob = paired
    listener = bob.listener(max_attempts=1)
    contact = alice.contacts.update(
        alice.contacts.by_key(bob.identity.public_bytes), host=LOOPBACK, port=listener.port  # type: ignore[arg-type]
    )
    with Serving(listener) as srv:
        for payload in (b"GET / HTTP/1.1\r\n\r\n", b"\x00\x00\x00\x05hello", b"\xff\xff\xff\xff"):
            with socket.create_connection((LOOPBACK, listener.port)) as s:
                s.sendall(payload)
                s.settimeout(5)
                with contextlib.suppress(OSError):
                    s.recv(100)
        transfer(alice.identity, contact, alice.vault, "pull", listener.code)
    assert srv.wait().ok


def test_server_reveals_nothing_before_the_code_is_proven(paired: tuple[Device, Device]) -> None:
    """With a wrong code the listener sends only its hello, never an
    encrypted frame an attacker could test guesses against offline."""
    _alice, bob = paired
    listener = bob.listener()
    with Serving(listener) as srv:
        sock = socket.create_connection((LOOPBACK, listener.port))
        from spake2 import SPAKE2_A

        spake = SPAKE2_A(b"wrong", idA=channel_mod._ID_CLIENT, idB=channel_mod._ID_SERVER)
        hello = channel_mod.MAGIC + bytes([1, MODE_SESSION]) + b"\0" * 32 + spake.start()
        channel_mod._write_frame(sock, hello)
        channel_mod._read_frame(sock, 1000)  # server hello
        channel_mod._write_frame(sock, b"\0" * 64)  # a bogus "encrypted" auth frame
        sock.settimeout(10)
        assert sock.recv(100) == b""  # closed without another byte
        sock.close()
        listener.cancel()
    assert not srv.wait().ok


def test_listener_refuses_pairing_mode_when_disabled(alice: Device, bob: Device) -> None:
    listener = bob.listener(allow_pairing=False)
    with Serving(listener), pytest.raises(RemoteError, match="not accepting"):
        pair(alice.identity, alice.contacts, LOOPBACK, listener.port, listener.code)
    listener.cancel()


def test_pairing_with_self_is_refused(alice: Device) -> None:
    listener = alice.listener()
    with Serving(listener), pytest.raises(SecurityError):
        pair(alice.identity, alice.contacts, LOOPBACK, listener.port, listener.code)


# =================================================================== units
@pytest.mark.parametrize("host,ok", [
    ("127.0.0.1", True), ("192.168.1.20", True), ("10.0.0.5", True), ("172.16.3.4", True),
    ("169.254.10.1", True), ("::1", True), ("fe80::1%eth0", True), ("fd12::1", True),
    ("::ffff:192.168.1.2", True),
    ("8.8.8.8", False), ("1.1.1.1", False), ("2001:4860:4860::8888", False),
    ("0.0.0.0", False), ("224.0.0.1", False), ("::ffff:8.8.8.8", False), ("example.com", False),
])
def test_is_local_address(host: str, ok: bool) -> None:
    assert is_local_address(host) is ok


def test_client_refuses_public_addresses(paired: tuple[Device, Device]) -> None:
    alice, bob = paired
    contact = alice.contacts.update(alice.contacts.by_key(bob.identity.public_bytes), host="8.8.8.8")  # type: ignore[arg-type]
    with pytest.raises(SecurityError, match="local network"):
        transfer(alice.identity, contact, alice.vault, "pull", "12345678")


@pytest.mark.parametrize("text,ok", [
    ("1234 5678", True), ("1234-5678", True), ("12345678", True),
    ("1234567", False), ("123456789", False), ("abcd efgh", False), ("１２３４５６７８", False),
])
def test_normalize_code(text: str, ok: bool) -> None:
    if ok:
        assert normalize_code(text) == "12345678"
    else:
        with pytest.raises(AuthError):
            normalize_code(text)


def test_codes_are_random_and_well_formed() -> None:
    codes = {channel_mod.generate_code() for _ in range(200)}
    assert len(codes) > 190
    assert all(len(c) == 8 and c.isdigit() for c in codes)


def test_clean_name_defangs_names() -> None:
    assert clean_name("bob\u202eexe.md") == "bobexe.md"
    assert clean_name("a\nb\tc") == "a b c"
    assert clean_name("x" * 200) == "x" * 48
    assert clean_name("../../etc") == "etc"
    assert clean_name(None) == "device"


def test_plan_pull_keeps_local_only_files() -> None:
    e = lambda p, s: files.Entry(p, 1, s * 64)  # noqa: E731
    local = {"mine.md": e("mine.md", "a"), "old.md": e("old.md", "b")}
    remote = {"x.md": e("x.md", "c")}
    base = {"old.md": e("old.md", "b")}
    plan = files.compute_plan("pull", local, remote, base, "bob")
    assert plan.download == ["x.md"]
    assert plan.delete_local == ["old.md"]  # bob deleted it, unchanged here


def test_conflict_names_are_valid_and_unique() -> None:
    name = files.conflict_name("dir/Note.md", "Bob's \u202ePC", {"dir/Note.md"})
    assert files.validate_rel_path(name) == name
    assert name.startswith("dir/Note (conflict Bob's PC ")


def test_scan_rejects_case_collisions(tmp_path: Path) -> None:
    (tmp_path / "Note.md").write_text("a")
    if (tmp_path / "note.md").exists():
        pytest.skip("case-insensitive filesystem")
    (tmp_path / "note.md").write_text("b")
    with pytest.raises(SyncError, match="letter case"):
        files.scan(tmp_path)


def test_channel_rejects_oversized_frames() -> None:
    a, b = socket.socketpair()
    try:
        a.sendall((10**8).to_bytes(4, "big"))
        ch = SecureChannel(b, b"\0" * 32, b"\0" * 32, b"")
        with pytest.raises(ProtocolError):
            ch.recv()
    finally:
        a.close()
        b.close()


def test_pair_mode_handshake_roundtrip(alice: Device, bob: Device) -> None:
    a, b = socket.socketpair()
    result: dict[str, object] = {}

    def server() -> None:
        ch, mode = server_handshake(b, exchange=ServerKeyExchange("11112222"), identity=bob.identity,
                                    modes=(MODE_PAIR,))
        result["mode"], result["peer"] = mode, ch.peer_key

    t = threading.Thread(target=server)
    t.start()
    ch = client_handshake(a, code="1111 2222", mode=MODE_PAIR, identity=alice.identity)
    t.join(5)
    assert ch.peer_key == bob.identity.public_bytes
    assert result == {"mode": MODE_PAIR, "peer": alice.identity.public_bytes}
    a.close()
    b.close()


def test_damaged_conflict_upload_leaves_the_original_in_place(paired: tuple[Device, Device]) -> None:
    alice, bob = paired
    original = bob.write("n.md", "bob's version")
    current = hashlib.sha256(original.read_bytes()).hexdigest()
    peer = RawPeer(alice, bob, "sync")
    peer.ch.send({"t": "put", "path": "n.md", "size": 4, "sha256": "0" * 64,
                  "replace": current, "keep_as": "n (conflict).md"})
    peer.ch.send_data(b"evil")
    assert peer.ch.recv("ack", "nack")["t"] == "nack"
    peer.outcome()
    assert bob.read("n.md") == "bob's version"
    assert not (bob.vault / "n (conflict).md").exists()


def test_client_refuses_a_manifest_bigger_than_the_limit(
    alice: Device, bob: Device, monkeypatch: pytest.MonkeyPatch
) -> None:
    _pair(alice, bob)
    monkeypatch.setattr(files, "MAX_TOTAL_SIZE", 1000)
    contact = alice.contacts.by_key(bob.identity.public_bytes)
    assert contact is not None
    server = socket.create_server((LOOPBACK, 0))
    contact = alice.contacts.update(contact, host=LOOPBACK, port=server.getsockname()[1])

    def evil() -> None:
        conn, _ = server.accept()
        ch, _mode = server_handshake(conn, exchange=ServerKeyExchange("12345678"),
                                     identity=bob.identity, modes=(MODE_SESSION,))
        ch.recv("request")
        ch.send({"t": "accepted"})
        rows = [[f"f{i}.md", 600, "0" * 64] for i in range(2)]
        ch.send({"t": "manifest", "files": rows, "more": False})
        with contextlib.suppress(SyncError):
            ch.recv()
        ch.close()
        server.close()

    thread = threading.Thread(target=evil, daemon=True)
    thread.start()
    with pytest.raises(ProtocolError, match="sharing limit"):
        transfer(alice.identity, contact, alice.vault, "pull", "12345678")
    thread.join(5)
    assert list(alice.vault.iterdir()) == []
