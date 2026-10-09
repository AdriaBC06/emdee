# SPDX-License-Identifier: GPL-3.0-or-later
"""``emdee sync …`` — share vaults with other devices on the local network.

Typical use, on two machines::

    bob$   emdee sync listen ~/notes          # shows a one-time code
    alice$ emdee sync pair 192.168.1.20       # types bob's code -> contacts

    bob$   emdee sync listen ~/notes          # a new code for every operation
    alice$ emdee sync pull bob ~/notes        # or push / sync

The listening side approves every request interactively.  Nothing here
imports Qt.  Exit status: 0 on success, 1 when the operation failed or was
refused, 2 on usage errors.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import APP_ID, APP_NAME
from .core.sync.channel import format_code, normalize_code
from .core.sync.errors import AuthError, SyncError
from .core.sync.session import OPERATIONS, Listener, Request, local_addresses, pair, transfer
from .core.sync.store import (
    DEFAULT_PORT,
    ContactBook,
    config_dir,
    load_identity,
    save_device_settings,
    with_settings,
)

__all__ = ["main", "parse_address"]


def parse_address(text: str, default_port: int = DEFAULT_PORT) -> tuple[str, int]:
    """``host``, ``host:port``, ``[v6]:port`` or a bare IPv6 address."""
    text = text.strip()
    if text.startswith("["):
        host, _, rest = text[1:].partition("]")
        port = rest[1:] if rest.startswith(":") else ""
    elif text.count(":") == 1:
        host, port = text.split(":")
    else:
        host, port = text, ""
    if not host:
        raise SyncError(f"Not an address: {text!r}")
    if port:
        if not port.isdigit() or not 1 <= int(port) <= 65535:
            raise SyncError(f"Not a port: {port!r}")
        return host, int(port)
    return host, default_port


def _say(message: str) -> None:
    print(message, flush=True)


def _ask_code() -> str:
    for _ in range(3):
        try:
            text = input("Code shown on the other device: ")
        except EOFError as exc:
            raise SyncError("No code given.") from exc
        try:
            return normalize_code(text)
        except AuthError as exc:
            print(exc, file=sys.stderr)
    raise SyncError("No valid code given.")


def _approve(request: Request) -> bool:
    print(f"\n{request.description}")
    try:
        answer = input("Allow? [y/N] ").strip().lower()
    except EOFError:
        return False
    return answer in ("y", "yes", "s", "si", "sí")


def _cmd_id(args: argparse.Namespace) -> int:
    identity = load_identity()
    if args.name is not None or args.port is not None:
        identity = with_settings(identity, name=args.name, port=args.port)
        save_device_settings(identity)
    print(f"name:        {identity.name}")
    print(f"fingerprint: {identity.fingerprint}")
    print(f"port:        {identity.port}")
    print(f"stored in:   {config_dir()}")
    return 0


def _cmd_contacts(args: argparse.Namespace) -> int:
    contacts = ContactBook().all()
    if args.json:
        print(json.dumps(
            [{"name": c.name, "address": c.address, "fingerprint": c.fingerprint} for c in contacts],
            ensure_ascii=False, indent=2,
        ))
    elif not contacts:
        print("No contacts yet. Pair with `emdee sync pair HOST` while the other device listens.")
    else:
        for c in contacts:
            print(f"{c.name:<24} {c.address:<28} {c.fingerprint}")
    return 0


def _cmd_listen(args: argparse.Namespace) -> int:
    identity = load_identity()
    vault = Path(args.folder).expanduser() if args.folder else None
    if vault is not None and not vault.is_dir():
        raise SyncError(f"Not a folder: {vault}")
    listener = Listener(
        identity, ContactBook(), vault=vault, approve=_approve, on_event=_say,
        port=args.port, seconds=args.minutes * 60, allow_pairing=not args.no_pair,
        open_firewall=True,
    )
    port = listener.open()
    print(f"{APP_NAME} is accepting ONE connection on port {port} for {args.minutes} min.")
    print(f"This device:  {identity.name}  ({identity.fingerprint})")
    addresses = local_addresses()
    if addresses:
        shown = ", ".join(f"[{a}]:{port}" if ":" in a else f"{a}:{port}" for a in addresses)
        print(f"Address:      {shown}")
    print(f"Vault:        {vault.resolve() if vault else '(none — pairing only)'}")
    print(f"\n    Code:  {format_code(listener.code)}\n")
    print("Type it on the other device. Ctrl+C to stop.", flush=True)
    try:
        outcome = listener.serve()
    except KeyboardInterrupt:
        listener.cancel()
        print("\nStopped.")
        return 1
    print(outcome.message)
    if outcome.report and outcome.report.skipped:
        for rel, reason in outcome.report.skipped:
            print(f"  skipped {rel}: {reason}")
    return 0 if outcome.ok else 1


def _cmd_pair(args: argparse.Namespace) -> int:
    identity = load_identity()
    host, port = parse_address(args.address)
    code = _ask_code()
    contact = pair(identity, ContactBook(), host, port, code, on_event=_say)
    print(f"Paired with “{contact.name}” at {contact.address}.")
    print(f"Its fingerprint: {contact.fingerprint}  (it can show this with `emdee sync id`)")
    return 0


def _cmd_transfer(args: argparse.Namespace) -> int:
    identity = load_identity()
    contacts = ContactBook()
    contact = contacts.find(args.contact)
    if args.address:
        host, port = parse_address(args.address, contact.port)
        contact = contacts.update(contact, host=host, port=port)
    folder = Path(args.folder).expanduser()
    if args.command == "pull":
        folder.mkdir(parents=True, exist_ok=True)
    code = _ask_code()
    report = transfer(identity, contact, folder, args.command, code, on_event=_say)
    print(f"Done: {report.summary()}.")
    for rel, reason in report.skipped:
        print(f"  skipped {rel}: {reason}")
    for rel, copy in report.conflicts.items():
        print(f"  conflict {rel}: their version kept as {copy}")
    return 0


def _cmd_forget(args: argparse.Namespace) -> int:
    contacts = ContactBook()
    contact = contacts.find(args.contact)
    contacts.remove(contact)
    print(f"Removed “{contact.name}”. It can no longer connect to this device.")
    return 0


def _cmd_edit(args: argparse.Namespace) -> int:
    contacts = ContactBook()
    contact = contacts.find(args.contact)
    host = port = None
    if args.address:
        host, port = parse_address(args.address, contact.port)
    contact = contacts.update(contact, name=args.name, host=host, port=port)
    print(f"{contact.name}  {contact.address}  {contact.fingerprint}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=f"{APP_ID} sync",
        description=f"{APP_NAME}: share vaults with paired devices on your local network "
        "(end-to-end encrypted; every operation needs a one-time code).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    ident = sub.add_parser("id", help="show (or set) this device's name, port and fingerprint")
    ident.add_argument("--name")
    ident.add_argument("--port", type=int)
    ident.set_defaults(func=_cmd_id)

    contacts = sub.add_parser("contacts", help="list paired devices")
    contacts.add_argument("--json", action="store_true")
    contacts.set_defaults(func=_cmd_contacts)

    listen = sub.add_parser("listen", help="accept one connection (shows a one-time code)")
    listen.add_argument("folder", nargs="?", help="vault to offer (omit to only accept pairing)")
    listen.add_argument("--port", type=int, help="port to listen on (default: this device's port)")
    listen.add_argument("--minutes", type=int, default=5, choices=range(1, 31), metavar="1-30")
    listen.add_argument("--no-pair", action="store_true", help="do not accept new contacts")
    listen.set_defaults(func=_cmd_listen)

    pairing = sub.add_parser("pair", help="add a listening device as a contact")
    pairing.add_argument("address", help="HOST or HOST:PORT of the listening device")
    pairing.set_defaults(func=_cmd_pair)

    helps = {
        "pull": "receive a contact's vault into FOLDER",
        "push": "send FOLDER into a contact's vault",
        "sync": "two-way sync of FOLDER with a contact's vault",
    }
    for op in OPERATIONS:
        cmd = sub.add_parser(op, help=helps[op])
        cmd.add_argument("contact", help="contact name or fingerprint prefix")
        cmd.add_argument("folder", nargs="?", default=".")
        cmd.add_argument("--address", help="contact's current HOST[:PORT], if it changed")
        cmd.set_defaults(func=_cmd_transfer)

    forget = sub.add_parser("forget", help="remove a contact")
    forget.add_argument("contact")
    forget.set_defaults(func=_cmd_forget)

    edit = sub.add_parser("edit", help="rename a contact or change its address")
    edit.add_argument("contact")
    edit.add_argument("--name")
    edit.add_argument("--address", help="HOST[:PORT]")
    edit.set_defaults(func=_cmd_edit)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Entry point for ``emdee sync …`` (``argv`` excludes the word ``sync``)."""
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except SyncError as exc:
        print(f"{APP_ID}: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print(file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
