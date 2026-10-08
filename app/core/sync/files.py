# SPDX-License-Identifier: GPL-3.0-or-later
"""What a vault contains, what has to move, and writing it without surprises.

Every path that comes from the other device is untrusted, even from a paired
contact (its machine could be compromised).  :func:`validate_rel_path` is the
single gate: relative, ``/``-separated, no ``..``, no hidden or skipped
folders, no characters or names that some filesystem would reinterpret, and
an allow-listed file type.  :func:`safe_target` then refuses to go through a
symlink or a junction, so even a link planted inside the vault cannot redirect
a write outside it.

Nothing is ever destroyed: a file about to be overwritten or deleted by a sync
is first moved to the vault's ``.trash/`` folder (Obsidian's convention, and a
folder Emdee never indexes nor syncs).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import stat
import tempfile
import time
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from ...platform_support import IS_WINDOWS
from ..vault import SKIPPED_DIRS
from .errors import SyncError, UnsafePathError
from .store import clean_name, config_dir, write_private

log = logging.getLogger(__name__)

__all__ = [
    "ALLOWED_SUFFIXES",
    "Entry",
    "Plan",
    "TRASH_DIR",
    "compute_plan",
    "load_base",
    "move_to_trash",
    "safe_target",
    "save_base",
    "scan",
    "validate_rel_path",
    "write_received",
]

#: File types that travel: notes and the attachments notes embed.  Anything
#: else (scripts, executables, archives, dotfiles) stays where it is.
ALLOWED_SUFFIXES: frozenset[str] = frozenset({
    ".md", ".markdown", ".mdown", ".mkd", ".mdx", ".txt",
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp", ".avif",
    ".pdf", ".csv",
    ".mp3", ".wav", ".ogg", ".m4a", ".mp4", ".webm",
})

TRASH_DIR = ".trash"

MAX_FILE_SIZE = 100 * 1024 * 1024
MAX_TOTAL_SIZE = 4 * 1024 * 1024 * 1024
MAX_FILES = 20000
MAX_PATH_CHARS = 400
MAX_DEPTH = 24

_WINDOWS_RESERVED = frozenset(
    {"con", "prn", "aux", "nul", "conin$", "conout$"}
    | {f"com{i}" for i in range(10)} | {f"lpt{i}" for i in range(10)}
    | {f"com{c}" for c in "¹²³"} | {f"lpt{c}" for c in "¹²³"}
)
_FORBIDDEN_CHARS = frozenset('<>:"\\|?*')
_SHA_HEX = frozenset("0123456789abcdef")
_SHORT_NAME = re.compile(r"~\d+$")


# -------------------------------------------------------------------- paths
def validate_rel_path(rel: object) -> str:
    """Return ``rel`` if it is a safe vault-relative file path, else raise.

    The rules are the intersection of what Linux, Windows and macOS accept,
    so a path that passes can be written identically everywhere.
    """
    if not isinstance(rel, str) or not rel or len(rel) > MAX_PATH_CHARS:
        raise UnsafePathError("Invalid file path.")
    try:
        rel.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise UnsafePathError("Invalid file path.") from exc
    parts = rel.split("/")
    if len(parts) > MAX_DEPTH:
        raise UnsafePathError("File path too deep.")
    for part in parts:
        if part in ("", ".", ".."):
            raise UnsafePathError(f"Unsafe file path: {rel!r}")
        if part.startswith(".") or part in SKIPPED_DIRS:
            raise UnsafePathError(f"Hidden or excluded path: {rel!r}")
        if part[-1] in " .":
            raise UnsafePathError(f"Unsafe file name: {rel!r}")
        if _SHORT_NAME.search(part.split(".", 1)[0]):
            # Windows 8.3 aliases (GIT~1 = .git, TRASH~1 = .trash) would
            # otherwise reach hidden folders under another name.
            raise UnsafePathError(f"Reserved file name: {rel!r}")
        if len(part.encode("utf-8")) > 255:
            raise UnsafePathError("File name too long.")
        for char in part:
            if char in _FORBIDDEN_CHARS or unicodedata.category(char) in ("Cc", "Cf", "Cs", "Co", "Cn"):
                raise UnsafePathError(f"Unsafe character in file name: {rel!r}")
        if part.split(".", 1)[0].rstrip(" ").casefold() in _WINDOWS_RESERVED:
            raise UnsafePathError(f"Reserved file name: {rel!r}")
    if PurePosixPath(parts[-1]).suffix.lower() not in ALLOWED_SUFFIXES:
        raise UnsafePathError(f"File type not shared: {rel!r}")
    return rel


def _collision_key(rel: str) -> str:
    """Two paths with the same key are the same file on Windows and macOS."""
    return unicodedata.normalize("NFC", rel).casefold()


def _is_link_like(path: Path) -> bool:
    """A symlink, or on Windows any reparse point (junctions included)."""
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    if stat.S_ISLNK(info.st_mode):
        return True
    attrs = getattr(info, "st_file_attributes", 0)
    return bool(attrs & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def _check_chain(root: Path, parts: Iterable[str]) -> Path:
    current = root
    for part in parts:
        current = current / part
        if _is_link_like(current):
            raise UnsafePathError(f"Refusing to write through a link: {current}")
    return current


def safe_target(root: Path, rel: str) -> Path:
    """Map a validated relative path to a location inside ``root``.

    Fails if any component that already exists is a link, or if the result
    would land outside the vault for any other reason.
    """
    validate_rel_path(rel)
    resolved_root = root.resolve()
    if _is_link_like(root):
        raise UnsafePathError("The vault folder itself is a link.")
    target = _check_chain(resolved_root, rel.split("/"))
    if not target.resolve().is_relative_to(resolved_root):
        raise UnsafePathError(f"Path escapes the vault: {rel!r}")
    return target


# -------------------------------------------------------------------- scan
@dataclass(frozen=True)
class Entry:
    path: str
    size: int
    sha256: str
    mtime_ns: int = 0

    def to_wire(self) -> list[object]:
        return [self.path, self.size, self.sha256]


def entry_from_wire(item: object) -> Entry:
    """Validate one manifest row received from the peer."""
    if not isinstance(item, list) or len(item) != 3:
        raise SyncError("Malformed file list from the other device.")
    rel, size, digest = item
    validate_rel_path(rel)
    if not isinstance(size, int) or isinstance(size, bool) or not 0 <= size <= MAX_FILE_SIZE:
        raise SyncError("The other device listed a file that is too large.")
    if not _is_sha(digest):
        raise SyncError("Malformed file list from the other device.")
    return Entry(rel, size, digest)


def _is_sha(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and set(value) <= _SHA_HEX


def _hash_file(path: Path, limit: int) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with open(path, "rb") as handle:
        while chunk := handle.read(1 << 20):
            size += len(chunk)
            if size > limit:
                raise SyncError(f"{path.name} is larger than {limit // (1024 * 1024)} MB.")
            digest.update(chunk)
    return digest.hexdigest(), size


def scan(root: Path, cache: dict[str, Entry] | None = None) -> dict[str, Entry]:
    """Every shareable file in the vault, keyed by its relative path.

    Links are never followed and hidden folders (``.git``, ``.trash`` …) are
    skipped.  ``cache`` (the last sync's state) avoids re-hashing files whose
    size and modification time have not changed.
    """
    root = root.resolve()
    if not root.is_dir():
        raise SyncError(f"Not a folder: {root}")
    cache = cache or {}
    found: dict[str, Entry] = {}
    seen_keys: dict[str, str] = {}
    total = 0
    for current, dirs, files in os.walk(root, followlinks=False):
        here = Path(current)
        dirs[:] = sorted(
            d for d in dirs
            if not d.startswith(".") and d not in SKIPPED_DIRS and not _is_link_like(here / d)
        )
        for name in sorted(files):
            path = here / name
            rel = path.relative_to(root).as_posix()
            try:
                validate_rel_path(rel)
            except UnsafePathError:
                continue
            try:
                info = path.lstat()
            except OSError:
                continue
            if not stat.S_ISREG(info.st_mode) or _is_link_like(path):
                continue
            if info.st_size > MAX_FILE_SIZE:
                log.warning("not sharing %s: larger than the per-file limit", rel)
                continue
            key = _collision_key(rel)
            if key in seen_keys:
                raise SyncError(
                    f"“{seen_keys[key]}” and “{rel}” differ only in letter case; "
                    "rename one so the vault works on every system."
                )
            seen_keys[key] = rel
            if len(found) >= MAX_FILES:
                raise SyncError(f"The vault has more than {MAX_FILES} files to share.")
            known = cache.get(rel)
            if known is not None and known.size == info.st_size and known.mtime_ns == info.st_mtime_ns:
                digest, size = known.sha256, known.size
            else:
                try:
                    digest, size = _hash_file(path, MAX_FILE_SIZE)
                except OSError:
                    continue
            total += size
            if total > MAX_TOTAL_SIZE:
                raise SyncError("The vault is larger than the 4 GB sharing limit.")
            found[rel] = Entry(rel, size, digest, info.st_mtime_ns)
    return found


# -------------------------------------------------------------- base state
def _state_path(root: Path, peer_id: str) -> Path:
    key = hashlib.sha256(f"{root.resolve()}\0{peer_id}".encode()).hexdigest()[:40]
    return config_dir() / "state" / f"{key}.json"


def load_base(root: Path, peer_id: str) -> dict[str, Entry]:
    """What both devices had in common after their last completed sync."""
    try:
        data = json.loads(_state_path(root, peer_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    files = data.get("files") if isinstance(data, dict) else None
    base: dict[str, Entry] = {}
    for rel, row in (files or {}).items() if isinstance(files, dict) else ():
        if isinstance(row, list) and len(row) == 3 and _is_sha(row[0]):
            size, mtime = row[1], row[2]
            if isinstance(size, int) and isinstance(mtime, int):
                base[rel] = Entry(rel, size, row[0], mtime)
    return base


def save_base(root: Path, peer_id: str, entries: Iterable[Entry]) -> None:
    payload = {
        "root": str(root.resolve()),
        "peer": peer_id,
        "saved": time.time(),
        "files": {e.path: [e.sha256, e.size, e.mtime_ns] for e in entries},
    }
    write_private(_state_path(root, peer_id), json.dumps(payload, ensure_ascii=False).encode("utf-8"))


# -------------------------------------------------------------------- plan
@dataclass
class Plan:
    """What the initiating device will do, from its own point of view."""

    download: list[str] = field(default_factory=list)
    upload: list[str] = field(default_factory=list)
    delete_local: list[str] = field(default_factory=list)
    delete_remote: list[str] = field(default_factory=list)
    #: path → name under which the *other* device's version is kept.
    conflicts: dict[str, str] = field(default_factory=dict)

    @property
    def empty(self) -> bool:
        return not (self.download or self.upload or self.delete_local or self.delete_remote or self.conflicts)


def conflict_name(rel: str, device: str, taken: set[str]) -> str:
    """``Note.md`` → ``Note (conflict Laptop 2026-10-08).md``, unused on both sides."""
    path = PurePosixPath(rel)
    label = clean_name(device, "other device")
    stamp = time.strftime("%Y-%m-%d")
    taken_keys = {_collision_key(t) for t in taken}
    for counter in range(1, 1000):
        extra = f" {counter}" if counter > 1 else ""
        name = f"{path.stem} (conflict {label} {stamp}{extra}){path.suffix}"
        candidate = str(path.with_name(name))
        if _collision_key(candidate) not in taken_keys:
            try:
                return validate_rel_path(candidate)
            except UnsafePathError:
                break
    raise SyncError(f"Could not find a free name for the conflicting copy of {rel}.")


def compute_plan(
    op: str,
    local: dict[str, Entry],
    remote: dict[str, Entry],
    base: dict[str, Entry],
    remote_name: str,
) -> Plan:
    """Decide what moves.

    * ``pull`` — make the local copy match the remote for every remote file;
      delete locally only what the remote deleted *since the last sync* and
      that was not edited here since.
    * ``push`` — the mirror image.
    * ``sync`` — three-way merge against the last common state: a change on
      one side wins; edits on both sides keep both versions (the remote's
      under a "conflict" name); an edit beats a deletion.
    """
    plan = Plan()
    sha = lambda m, p: m[p].sha256 if p in m else None  # noqa: E731

    if op == "pull":
        for rel in remote:
            if sha(local, rel) != sha(remote, rel):
                plan.download.append(rel)
        for rel in local.keys() - remote.keys():
            if rel in base and sha(base, rel) == sha(local, rel):
                plan.delete_local.append(rel)
    elif op == "push":
        for rel in local:
            if sha(local, rel) != sha(remote, rel):
                plan.upload.append(rel)
        for rel in remote.keys() - local.keys():
            if rel in base and sha(base, rel) == sha(remote, rel):
                plan.delete_remote.append(rel)
    elif op == "sync":
        taken = set(local) | set(remote)
        for rel in sorted(taken):
            mine, theirs, common = sha(local, rel), sha(remote, rel), sha(base, rel)
            if mine == theirs:
                continue
            if mine == common:
                (plan.download if theirs else plan.delete_local).append(rel)
            elif theirs == common:
                (plan.upload if mine else plan.delete_remote).append(rel)
            elif mine is None:
                plan.download.append(rel)
            elif theirs is None:
                plan.upload.append(rel)
            else:
                copy = conflict_name(rel, remote_name, taken)
                taken.add(copy)
                plan.conflicts[rel] = copy
    else:
        raise SyncError(f"Unknown operation {op!r}.")

    for items in (plan.download, plan.upload, plan.delete_local, plan.delete_remote):
        items.sort()
    return plan


# ------------------------------------------------------------------ writes
def _trash_target(root: Path, rel: str) -> Path:
    trash = root.resolve() / TRASH_DIR
    if _is_link_like(trash):
        raise UnsafePathError("The vault's .trash folder is a link.")
    parts = rel.split("/")
    folder = _check_chain(trash, parts[:-1])
    folder.mkdir(parents=True, exist_ok=True)
    path = PurePosixPath(parts[-1])
    candidate = folder / path.name
    counter = 1
    while candidate.exists() or candidate.is_symlink():
        stamp = time.strftime("%Y%m%d-%H%M%S")
        suffix = f" {counter}" if counter > 1 else ""
        candidate = folder / f"{path.stem} ({stamp}{suffix}){path.suffix}"
        counter += 1
    return candidate


def move_to_trash(root: Path, rel: str) -> Path | None:
    """Move a vault file into ``.trash/`` (same relative layout).  Returns
    where it went, or ``None`` if it was already gone."""
    source = safe_target(root, rel)
    if not source.exists():
        return None
    if not source.is_file():
        raise UnsafePathError(f"Not a regular file: {rel}")
    destination = _trash_target(root, rel)
    os.replace(source, destination)
    return destination


def current_sha(root: Path, rel: str) -> str | None:
    """SHA-256 of a vault file right now, ``None`` if it does not exist."""
    path = safe_target(root, rel)
    if not path.exists():
        return None
    if not path.is_file():
        raise UnsafePathError(f"Not a regular file: {rel}")
    return _hash_file(path, MAX_FILE_SIZE)[0]


def _make_parents(root: Path, rel: str) -> None:
    """Create the folders above ``rel``, refusing links and files in the way."""
    current = root.resolve()
    for part in rel.split("/")[:-1]:
        current = current / part
        if _is_link_like(current):
            raise UnsafePathError(f"Refusing to write through a link: {current}")
        if current.exists() and not current.is_dir():
            raise UnsafePathError(f"A file is in the way of {rel}.")
        current.mkdir(exist_ok=True)


class IncomingFile:
    """A file being received: bytes go to a private temporary file next to
    the destination, are hashed as they arrive, and replace the destination
    only once the size and SHA-256 match what was announced."""

    def __init__(self, root: Path, rel: str, size: int, sha256: str) -> None:
        if not _is_sha(sha256) or not isinstance(size, int) or not 0 <= size <= MAX_FILE_SIZE:
            raise SyncError("Malformed file announcement.")
        self.root = root
        self.rel = rel
        self.size = size
        self.sha256 = sha256
        self.target = safe_target(root, rel)
        if self.target.exists() and not self.target.is_file():
            raise UnsafePathError(f"A folder is in the way of {rel}.")
        _make_parents(root, rel)
        fd, name = tempfile.mkstemp(prefix=".emdee-sync-", suffix=".part", dir=str(self.target.parent))
        self._tmp = Path(name)
        self._handle = os.fdopen(fd, "wb")
        self._digest = hashlib.sha256()
        self._received = 0

    def write(self, chunk: bytes) -> None:
        self._received += len(chunk)
        if self._received > self.size:
            raise SyncError(f"{self.rel}: more data than announced.")
        self._digest.update(chunk)
        self._handle.write(chunk)

    @property
    def complete(self) -> bool:
        return self._received >= self.size

    def verify(self) -> None:
        """Raise unless exactly the announced bytes, with the announced
        SHA-256, have arrived."""
        if self._received != self.size or self._digest.hexdigest() != self.sha256:
            raise SyncError(f"{self.rel} arrived damaged (checksum mismatch); not saved.")

    def commit(self, *, expect_current: str | None | bool = False) -> None:
        """Verify and move into place.

        ``expect_current`` (when not ``False``) is the SHA-256 the destination
        must still have — ``None`` meaning "must not exist" — so a file edited
        locally while the transfer ran is never silently overwritten.
        """
        try:
            self._handle.flush()
            os.fsync(self._handle.fileno())
        finally:
            self._handle.close()
        try:
            self.verify()
            if expect_current is not False and current_sha(self.root, self.rel) != expect_current:
                raise SyncError(f"{self.rel} changed on this device during the transfer; kept as is.")
            mode = 0o600
            if self.target.exists():
                mode = self.target.stat().st_mode & 0o666
                if current_sha(self.root, self.rel) != self.sha256:
                    move_to_trash(self.root, self.rel)
            if not IS_WINDOWS:
                os.chmod(self._tmp, mode)
            # Re-check right before the rename: nothing may have become a link.
            safe_target(self.root, self.rel)
            os.replace(self._tmp, self.target)
        finally:
            self._tmp.unlink(missing_ok=True)

    def abort(self) -> None:
        try:
            self._handle.close()
        finally:
            self._tmp.unlink(missing_ok=True)


def write_received(root: Path, rel: str, data: bytes) -> None:
    """Convenience for tests and small files: receive ``data`` in one go."""
    incoming = IncomingFile(root, rel, len(data), hashlib.sha256(data).hexdigest())
    incoming.write(data)
    incoming.commit()


def rename_within(root: Path, source_rel: str, target_rel: str) -> None:
    """Rename a vault file (used to keep a conflicting version aside)."""
    source = safe_target(root, source_rel)
    target = safe_target(root, target_rel)
    if not source.is_file():
        raise SyncError(f"{source_rel} is missing.")
    if target.exists():
        raise SyncError(f"{target_rel} already exists.")
    _make_parents(root, target_rel)
    os.replace(source, target)

