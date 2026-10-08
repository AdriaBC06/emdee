# SPDX-License-Identifier: GPL-3.0-or-later
"""The encrypted, mutually authenticated connection between two devices.

Nothing here is invented: the pieces are standard and come from audited
libraries — SPAKE2 (``spake2``, the PAKE behind Magic Wormhole) and Ed25519,
HKDF-SHA256 and ChaCha20-Poly1305 from PyCA ``cryptography``.

Handshake
=========

The device accepting connections shows an 8-digit one-time code; the device
connecting types it.  Then::

    client → server   hello:  "EMDS" v mode  nonce_c(32)  SPAKE2_A(code)
    server → client   hello:  "EMDS" v ok    nonce_s(32)  SPAKE2_B(code)

Both derive the SPAKE2 key ``K``.  SPAKE2 is a *password-authenticated* key
exchange: an eavesdropper learns nothing that lets them test guesses of the
code offline, and an active attacker gets exactly one guess per connection —
which the listener counts and caps.  ``K`` is ephemeral (fresh Diffie-Hellman
on both sides), so recording the traffic and learning the code later reveals
nothing: forward secrecy.

``th`` = SHA-256 of both hellos; HKDF(K, salt=th) gives one ChaCha20-Poly1305
key per direction.  From here on every frame is encrypted and authenticated,
with a 64-bit counter as nonce, so a frame that is altered, dropped, replayed,
reordered or reflected back fails to decrypt and the connection is dropped.

Inside the encrypted channel each side sends ``auth``: its Ed25519 public key,
its name and a signature over ``th``.  The client speaks first; the server
says nothing encrypted until the client's frame has decrypted, so a wrong code
gives an attacker no ciphertext to test guesses against.  Each side then
checks the other's key against its contact list: knowing a code is not enough
to read or write a vault, the device must also be a paired contact, and a
contact is recognised by key, never by address.
"""

from __future__ import annotations

import base64
import binascii
import contextlib
import hashlib
import json
import re
import secrets
import socket
import time
import unicodedata
from dataclasses import dataclass
from typing import Any

from cryptography.exceptions import InvalidSignature, InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from spake2 import SPAKE2_A, SPAKE2_B
from spake2.spake2 import SPAKEError

from .errors import AuthError, ConnectionClosedError, ProtocolError, RemoteError, SecurityError
from .store import Identity, clean_name

__all__ = [
    "CODE_DIGITS",
    "MODE_PAIR",
    "MODE_SESSION",
    "PROTOCOL",
    "SecureChannel",
    "ServerKeyExchange",
    "client_handshake",
    "format_code",
    "generate_code",
    "normalize_code",
    "server_handshake",
]

PROTOCOL = b"emdee-sync/1"
MAGIC = b"EMDS"
VERSION = 1

MODE_PAIR = 1
MODE_SESSION = 2
_MODES = (MODE_PAIR, MODE_SESSION)

_STATUS_OK = 0
_STATUS_REFUSED = 1

CODE_DIGITS = 8

_SPAKE_LEN = 33
_HELLO_LEN = len(MAGIC) + 2 + 32 + _SPAKE_LEN

#: Largest encrypted frame accepted: 1 MiB of payload plus the AEAD tag and
#: the type byte.  Anything larger is a protocol violation, not a big file —
#: files travel in chunks.
MAX_PAYLOAD = 1 << 20
_MAX_FRAME = MAX_PAYLOAD + 64

#: Seconds any single read may block before the connection is dropped.
IO_TIMEOUT = 30.0

#: The SPAKE2 maths is pure Python; padding it to a fixed duration keeps its
#: (already tiny) code-dependent timing variation off the wire.
_SPAKE_FLOOR = 0.05

_KIND_MSG = 1
_KIND_DATA = 2
_KIND_CLOSE = 3

_AAD = PROTOCOL
_ID_CLIENT = PROTOCOL + b" client"
_ID_SERVER = PROTOCOL + b" server"


# ------------------------------------------------------------------- codes
def generate_code() -> str:
    """A fresh one-time code: 8 decimal digits from the OS CSPRNG."""
    return f"{secrets.randbelow(10**CODE_DIGITS):0{CODE_DIGITS}d}"


def format_code(code: str) -> str:
    """``12345678`` → ``1234 5678``, easier to read out and type."""
    return f"{code[:4]} {code[4:]}"


def normalize_code(text: str) -> str:
    """Accept ``1234 5678`` / ``1234-5678``; return the bare digits."""
    digits = re.sub(r"[\s\-]", "", text or "")
    if len(digits) != CODE_DIGITS or not digits.isascii() or not digits.isdigit():
        raise AuthError(f"The code has {CODE_DIGITS} digits.")
    return digits


def _password(code: str) -> bytes:
    return PROTOCOL + b" code:" + normalize_code(code).encode("ascii")


# ----------------------------------------------------------------- framing
def _recv_exact(sock: socket.socket, size: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < size:
        try:
            chunk = sock.recv(min(size - len(chunks), 1 << 16))
        except TimeoutError as exc:
            raise ConnectionClosedError("The other device stopped responding.") from exc
        except OSError as exc:
            raise ConnectionClosedError(f"The connection was lost ({exc.strerror or exc}).") from exc
        if not chunk:
            raise ConnectionClosedError("The other device closed the connection.")
        chunks += chunk
    return bytes(chunks)


def _read_frame(sock: socket.socket, limit: int) -> bytes:
    size = int.from_bytes(_recv_exact(sock, 4), "big")
    if size == 0 or size > limit:
        raise ProtocolError("The other device sent an oversized or empty message.")
    return _recv_exact(sock, size)


def _write_frame(sock: socket.socket, body: bytes) -> None:
    try:
        sock.sendall(len(body).to_bytes(4, "big") + body)
    except OSError as exc:
        raise ConnectionClosedError(f"The connection was lost ({exc.strerror or exc}).") from exc


def clean_message(value: object) -> str:
    """Bound and de-fang an error message that arrived from the peer."""
    if not isinstance(value, str):
        return "The other device reported an error."
    text = "".join(
        c for c in unicodedata.normalize("NFC", value)
        if unicodedata.category(c) not in ("Cc", "Cf", "Cs", "Co", "Cn") or c == " "
    )
    return text[:300] or "The other device reported an error."


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _unb64(value: object, length: int | None = None) -> bytes:
    if not isinstance(value, str) or len(value) > 200:
        raise ProtocolError("Malformed key or signature.")
    try:
        raw = base64.b64decode(value.encode("ascii"), validate=True)
    except (binascii.Error, UnicodeEncodeError) as exc:
        raise ProtocolError("Malformed key or signature.") from exc
    if length is not None and len(raw) != length:
        raise ProtocolError("Malformed key or signature.")
    return raw


def _reject_constant(name: str) -> Any:
    raise ValueError(f"invalid JSON constant {name}")


# ----------------------------------------------------------------- channel
class SecureChannel:
    """An authenticated-encryption pipe over a connected TCP socket."""

    def __init__(self, sock: socket.socket, send_key: bytes, recv_key: bytes, transcript: bytes) -> None:
        self._sock = sock
        self._send_aead = ChaCha20Poly1305(send_key)
        self._recv_aead = ChaCha20Poly1305(recv_key)
        self._send_counter = 0
        self._recv_counter = 0
        self.transcript = transcript
        #: Filled in once the peer has proven who it is.
        self.peer_key: bytes = b""
        self.peer_name: str = ""
        self.peer_address: str = ""
        self._closed = False
        self._peer_closed = False

    @staticmethod
    def _nonce(counter: int) -> bytes:
        if counter >= 1 << 64:  # pragma: no cover - 2^64 frames
            raise SecurityError("Nonce space exhausted.")
        return b"\0\0\0\0" + counter.to_bytes(8, "big")

    # -------------------------------------------------------------- frames
    def _send_frame(self, kind: int, payload: bytes) -> None:
        if self._closed:
            raise ConnectionClosedError("The connection is closed.")
        if len(payload) > MAX_PAYLOAD:
            raise ProtocolError("Message too large.")
        sealed = self._send_aead.encrypt(self._nonce(self._send_counter), bytes([kind]) + payload, _AAD)
        self._send_counter += 1
        _write_frame(self._sock, sealed)

    def _recv_frame(self, timeout: float | None = None) -> tuple[int, bytes]:
        if self._closed or self._peer_closed:
            raise ConnectionClosedError("The connection is closed.")
        self._sock.settimeout(timeout or IO_TIMEOUT)
        try:
            body = _read_frame(self._sock, _MAX_FRAME)
        finally:
            self._sock.settimeout(IO_TIMEOUT)
        try:
            plain = self._recv_aead.decrypt(self._nonce(self._recv_counter), body, _AAD)
        except InvalidTag as exc:
            raise SecurityError(
                "A message failed its integrity check: the connection was altered in transit."
            ) from exc
        self._recv_counter += 1
        if not plain or plain[0] not in (_KIND_MSG, _KIND_DATA, _KIND_CLOSE):
            raise ProtocolError("Unknown message type.")
        if plain[0] == _KIND_CLOSE:
            self._peer_closed = True
            raise ConnectionClosedError("The other device closed the connection.")
        return plain[0], plain[1:]

    # ------------------------------------------------------------- messages
    def send(self, message: dict[str, Any]) -> None:
        self._send_frame(
            _KIND_MSG, json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        )

    def recv(self, *expected: str, timeout: float | None = None) -> dict[str, Any]:
        kind, payload = self._recv_frame(timeout)
        if kind != _KIND_MSG:
            raise ProtocolError("Expected a message, got file data.")
        try:
            message = json.loads(payload.decode("utf-8"), parse_constant=_reject_constant)
        except (UnicodeDecodeError, ValueError, RecursionError) as exc:
            raise ProtocolError("The other device sent malformed data.") from exc
        if not isinstance(message, dict) or not isinstance(message.get("t"), str):
            raise ProtocolError("The other device sent malformed data.")
        if message["t"] == "error":
            raise RemoteError(clean_message(message.get("message")))
        if expected and message["t"] not in expected:
            raise ProtocolError(f"Unexpected message {message['t'][:40]!r} from the other device.")
        return message

    def send_data(self, data: bytes) -> None:
        self._send_frame(_KIND_DATA, data)

    def recv_data(self) -> bytes:
        kind, payload = self._recv_frame()
        if kind != _KIND_DATA:
            # An error message in place of data still deserves to be shown.
            if kind == _KIND_MSG:
                try:
                    message = json.loads(payload.decode("utf-8"))
                except (UnicodeDecodeError, ValueError, RecursionError):
                    message = None
                if isinstance(message, dict) and message.get("t") == "error":
                    raise RemoteError(clean_message(message.get("message")))
            raise ProtocolError("Expected file data.")
        return payload

    def send_error(self, text: str) -> None:
        """Tell the peer why we are stopping; never raises."""
        with contextlib.suppress(Exception):
            self.send({"t": "error", "message": text[:300]})

    # ---------------------------------------------------------------- close
    def close(self) -> None:
        """Close cleanly: announce it, wait briefly for the peer's goodbye,
        then shut the socket down in both directions."""
        if self._closed:
            return
        try:
            if not self._peer_closed:
                self._send_frame(_KIND_CLOSE, b"")
                # Drain until the peer's own close frame (or a short timeout),
                # so neither side tears down while the other is mid-write.
                deadline = time.monotonic() + 2.0
                while not self._peer_closed and time.monotonic() < deadline:
                    try:
                        self._recv_frame(timeout=max(0.1, deadline - time.monotonic()))
                    except ConnectionClosedError:
                        break
            else:
                self._send_frame(_KIND_CLOSE, b"")
        except Exception:  # noqa: BLE001 - the socket is being torn down anyway
            pass
        finally:
            self._closed = True
            _close_socket(self._sock)


def _close_socket(sock: socket.socket) -> None:
    with contextlib.suppress(OSError):
        sock.shutdown(socket.SHUT_RDWR)
    sock.close()


# --------------------------------------------------------------- handshake
@dataclass
class _Hello:
    version: int
    flag: int
    nonce: bytes
    spake: bytes


def _parse_hello(raw: bytes) -> _Hello:
    if len(raw) != _HELLO_LEN or not raw.startswith(MAGIC):
        raise ProtocolError("That is not an Emdee device.")
    at = len(MAGIC)
    return _Hello(raw[at], raw[at + 1], raw[at + 2 : at + 34], raw[at + 34 :])


def _finish(spake: SPAKE2_A | SPAKE2_B, inbound: bytes) -> bytes:
    started = time.monotonic()
    try:
        key = spake.finish(inbound)
    except (SPAKEError, ValueError, TypeError) as exc:
        raise AuthError("The key exchange failed.") from exc
    remaining = _SPAKE_FLOOR - (time.monotonic() - started)
    if remaining > 0:
        time.sleep(remaining)
    return key


def _keys(key: bytes, client_hello: bytes, server_hello: bytes) -> tuple[bytes, bytes, bytes]:
    transcript = hashlib.sha256(PROTOCOL + b"\0" + client_hello + server_hello).digest()
    material = HKDF(
        algorithm=hashes.SHA256(), length=64, salt=transcript, info=PROTOCOL + b" traffic keys"
    ).derive(key)
    return material[:32], material[32:], transcript


def _auth_payload(role: bytes, transcript: bytes) -> bytes:
    return PROTOCOL + b" auth " + role + b"\0" + transcript


def _send_auth(channel: SecureChannel, identity: Identity, role: bytes) -> None:
    channel.send({
        "t": "auth",
        "key": _b64(identity.public_bytes),
        "name": identity.name,
        "sig": _b64(identity.sign(_auth_payload(role, channel.transcript))),
    })


def _check_auth(channel: SecureChannel, message: dict[str, Any], role: bytes) -> None:
    public = _unb64(message.get("key"), 32)
    signature = _unb64(message.get("sig"), 64)
    try:
        Ed25519PublicKey.from_public_bytes(public).verify(
            signature, _auth_payload(role, channel.transcript)
        )
    except (InvalidSignature, ValueError) as exc:
        raise SecurityError("The other device could not prove its identity.") from exc
    channel.peer_key = public
    channel.peer_name = clean_name(message.get("name"))


def _peer_address(sock: socket.socket) -> str:
    try:
        host = sock.getpeername()[0]
    except (OSError, IndexError, TypeError):
        return ""
    if not isinstance(host, str):
        return ""
    return host[7:] if host.startswith("::ffff:") else host


def client_handshake(sock: socket.socket, *, code: str, mode: int, identity: Identity) -> SecureChannel:
    """Run the client side; returns a channel with ``peer_key`` verified.

    The caller still has to decide whether ``peer_key`` is the device it meant
    to reach (a known contact, or a new one the user is pairing with).
    """
    if mode not in _MODES:
        raise ValueError("unknown mode")
    sock.settimeout(IO_TIMEOUT)
    spake = SPAKE2_A(_password(code), idA=_ID_CLIENT, idB=_ID_SERVER)
    outbound = spake.start()
    hello = MAGIC + bytes([VERSION, mode]) + secrets.token_bytes(32) + outbound
    _write_frame(sock, hello)

    server_raw = _read_frame(sock, _HELLO_LEN)
    server = _parse_hello(server_raw)
    if server.version != VERSION:
        raise ProtocolError("The other device runs an incompatible version of Emdee.")
    if server.flag != _STATUS_OK:
        raise RemoteError(
            "The other device is not accepting this kind of connection "
            "(is it accepting connections, with a vault open?)."
        )
    send_key, recv_key, transcript = _keys(_finish(spake, server.spake), hello, server_raw)
    channel = SecureChannel(sock, send_key, recv_key, transcript)
    channel.peer_address = _peer_address(sock)
    _send_auth(channel, identity, b"client")
    try:
        reply = channel.recv("auth")
    except (ConnectionClosedError, SecurityError) as exc:
        # The server drops the connection without a word when our first frame
        # does not decrypt: that is what a wrong code looks like from here.
        raise AuthError(
            "The code was not accepted. Check it and try again "
            "(after 3 wrong codes the other device stops listening)."
        ) from exc
    _check_auth(channel, reply, b"server")
    return channel


class ServerKeyExchange:
    """The listener's half of SPAKE2, computed *before* a client connects so
    that the time to answer a hello does not depend on the code."""

    def __init__(self, code: str) -> None:
        self._spake = SPAKE2_B(_password(code), idA=_ID_CLIENT, idB=_ID_SERVER)
        self.outbound = self._spake.start()

    def finish(self, inbound: bytes) -> bytes:
        return _finish(self._spake, inbound)


def server_handshake(
    sock: socket.socket,
    *,
    exchange: ServerKeyExchange,
    identity: Identity,
    modes: tuple[int, ...],
) -> tuple[SecureChannel, int]:
    """Run the server side.

    Raises :class:`ProtocolError` for connections that are not an Emdee
    client at all (these do not cost a code attempt) and :class:`AuthError`
    when the client used the wrong code (these do).
    """
    sock.settimeout(IO_TIMEOUT)
    client_raw = _read_frame(sock, _HELLO_LEN)
    client = _parse_hello(client_raw)
    accepted = client.version == VERSION and client.flag in modes
    reply = MAGIC + bytes([VERSION, _STATUS_OK if accepted else _STATUS_REFUSED])
    reply += secrets.token_bytes(32) + exchange.outbound
    _write_frame(sock, reply)
    if not accepted:
        raise ProtocolError("A device asked for a connection this listener does not offer.")

    send_key, recv_key, transcript = _keys(exchange.finish(client.spake), client_raw, reply)
    # Keys are directional: the client's sending key is our receiving key.
    channel = SecureChannel(sock, recv_key, send_key, transcript)
    channel.peer_address = _peer_address(sock)
    try:
        message = channel.recv("auth")
    except SecurityError as exc:
        raise AuthError("A device tried a wrong code.") from exc
    _check_auth(channel, message, b"client")
    _send_auth(channel, identity, b"server")
    return channel, client.flag
