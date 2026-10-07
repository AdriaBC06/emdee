# SPDX-License-Identifier: GPL-3.0-or-later
"""A folder of Markdown notes seen as a graph of linked concepts.

This is the Obsidian-style layer of Emdee: ``[[wiki links]]`` between notes,
backlinks, orphan and broken-link detection, and the node/edge data the graph
view draws.  It is pure Python with no Qt import on purpose — the same index
backs the GUI and the ``emdee vault`` command line, which is what lets an AI
agent (or a shell script) generate notes and then *check* that they connect.

Link syntax understood:

* ``[[Note]]``, ``[[Note|shown text]]``, ``[[Note#Heading]]``,
  ``[[folder/Note]]`` and the embed form ``![[Note]]``;
* ordinary Markdown links to local Markdown files, ``[text](other.md)``.

A wiki link resolves like Obsidian's: an exact folder-relative path first, then
a note whose file name (without suffix) matches case-insensitively, then a note
that lists the name in its front-matter ``aliases``.  When several notes share
a name, the one nearest to the linking note wins.
"""

from __future__ import annotations

import json
import os
import re
import unicodedata
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote

from .file_service import MARKDOWN_SUFFIXES

__all__ = [
    "WIKILINK_RE",
    "Link",
    "Note",
    "Vault",
    "extract_links",
    "parse_front_matter",
    "note_title",
]

#: Suffixes indexed as notes.  ``.txt`` is openable in the editor but is not a
#: note format, so it stays out of the graph.
NOTE_SUFFIXES: tuple[str, ...] = tuple(s for s in MARKDOWN_SUFFIXES if s != ".txt")

#: Folders never descended into while indexing.
SKIPPED_DIRS: frozenset[str] = frozenset(
    {".git", ".hg", ".svn", ".obsidian", ".trash", "node_modules", "__pycache__", ".venv"}
)

#: Instructions for AI agents (written by ``emdee vault init``), not notes.
SKIPPED_FILES: frozenset[str] = frozenset({"agents.md", "claude.md"})

#: Upper bound on indexed notes, so pointing the explorer at ``~`` cannot hang
#: the interface.
MAX_NOTES = 5000

#: ``[[target#heading|alias]]`` with an optional leading ``!`` (embed).
WIKILINK_RE = re.compile(
    r"(?P<embed>!?)\[\[(?P<target>[^\[\]|#\n]*)(?:#(?P<heading>[^\[\]|\n]*))?"
    r"(?:\|(?P<alias>[^\[\]\n]*))?\]\]"
)

_MD_LINK_RE = re.compile(r"(?<!!)\[(?:[^\[\]\n]|\[[^\[\]\n]*\])*\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")
_FENCE_RE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")
_INLINE_CODE_RE = re.compile(r"(`+)(?!`).+?(?<!`)\1(?!`)")
_TAG_RE = re.compile(r"(?:^|(?<=\s))#([A-Za-zÀ-￿_][\w\-/]*)", re.UNICODE)
_H1_RE = re.compile(r"^#\s+(.+?)\s*#*\s*$")
_SCHEME_RE = re.compile(r"^[a-z][a-z0-9+.-]*:", re.IGNORECASE)


@dataclass(frozen=True)
class Link:
    """One outgoing link as written in a note."""

    target: str
    heading: str = ""
    alias: str = ""
    line: int = 0
    embed: bool = False
    #: ``"wiki"`` for ``[[…]]``, ``"markdown"`` for ``[…](file.md)``.
    kind: str = "wiki"


@dataclass
class Note:
    """An indexed Markdown file."""

    path: Path
    title: str
    links: list[Link] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    aliases: list[str] = field(default_factory=list)
    words: int = 0
    mtime_ns: int = 0
    size: int = 0


# ----------------------------------------------------------------- parsing
def _strip_quotes(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def parse_front_matter(text: str) -> tuple[dict[str, object], int]:
    """Parse the small YAML subset notes actually use in front matter.

    Returns the mapping and the number of lines the block occupies (0 when
    there is none).  Supports ``key: value``, inline lists ``[a, b]`` and
    block lists of ``- item`` lines — enough for ``title``, ``tags`` and
    ``aliases`` without depending on a YAML library.
    """
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return {}, 0
    for end in range(1, len(lines)):
        if lines[end].strip() in ("---", "..."):
            break
    else:
        return {}, 0

    data: dict[str, object] = {}
    current: str | None = None
    for raw in lines[1:end]:
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        item = re.match(r"^\s+-\s*(.*)$", raw) or re.match(r"^-\s+(.*)$", raw)
        if item and current is not None:
            existing = data.get(current)
            if not isinstance(existing, list):
                existing = []
                data[current] = existing
            existing.append(_strip_quotes(item.group(1)))
            continue
        pair = re.match(r"^([A-Za-z_][\w\-]*)\s*:\s*(.*)$", raw)
        if not pair:
            continue
        current = pair.group(1).lower()
        value = pair.group(2).strip()
        if value.startswith("[") and value.endswith("]"):
            data[current] = [_strip_quotes(v) for v in value[1:-1].split(",") if v.strip()]
        elif value:
            data[current] = _strip_quotes(value)
        else:
            data[current] = []
    return data, end + 1


def _as_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(v).strip().lstrip("#") for v in value if str(v).strip()]
    if isinstance(value, str) and value.strip():
        return [v.strip().lstrip("#") for v in re.split(r"[,\s]+", value) if v.strip()]
    return []


def _prose_lines(text: str, start: int = 0) -> Iterator[tuple[int, str]]:
    """Yield ``(1-based line, text)`` outside fenced code, inline code blanked."""
    fence: str | None = None
    for number, line in enumerate(text.split("\n"), start=1):
        if number <= start:
            continue
        match = _FENCE_RE.match(line)
        if fence is not None:
            if match and match.group(1)[0] == fence[0] and len(match.group(1)) >= len(fence):
                fence = None
            continue
        if match:
            fence = match.group(1)
            continue
        # Indented code block — unless it is a nested list item.
        if line.startswith(("    ", "\t")) and not re.match(r"^\s+([*+-]|\d+[.)])\s", line):
            continue
        yield number, _INLINE_CODE_RE.sub(lambda m: " " * len(m.group(0)), line)


def extract_links(text: str) -> list[Link]:
    """Every wiki link and local Markdown link in ``text``, in order.

    Links inside fenced or inline code are ignored, exactly as the renderer
    ignores them.
    """
    _, skip = parse_front_matter(text)
    found: list[Link] = []
    for number, line in _prose_lines(text, skip):
        for match in WIKILINK_RE.finditer(line):
            target = match.group("target").strip()
            heading = (match.group("heading") or "").strip()
            if not target and not heading:
                continue
            found.append(
                Link(
                    target=target,
                    heading=heading,
                    alias=(match.group("alias") or "").strip(),
                    line=number,
                    embed=bool(match.group("embed")),
                )
            )
        for match in _MD_LINK_RE.finditer(WIKILINK_RE.sub("", line)):
            href = match.group(1)
            if _SCHEME_RE.match(href) or href.startswith(("#", "//")):
                continue
            path_part, _, fragment = href.partition("#")
            if Path(unquote(path_part)).suffix.lower() not in NOTE_SUFFIXES:
                continue
            found.append(
                Link(target=unquote(path_part), heading=unquote(fragment), line=number,
                     kind="markdown")
            )
    return found


def extract_tags(text: str, front: dict[str, object]) -> list[str]:
    """Front-matter ``tags`` plus inline ``#tags`` in prose (not headings)."""
    tags = _as_list(front.get("tags")) + _as_list(front.get("tag"))
    _, skip = parse_front_matter(text)
    for _number, line in _prose_lines(text, skip):
        if re.match(r"^\s{0,3}#{1,6}\s", line):
            continue
        tags.extend(m.group(1) for m in _TAG_RE.finditer(WIKILINK_RE.sub("", line)))
    seen: dict[str, None] = {}
    for tag in tags:
        seen.setdefault(tag.lower(), None)
    return list(seen)


def note_title(text: str, path: Path) -> str:
    """Front-matter ``title``, else the first ``# H1``, else the file stem."""
    front, skip = parse_front_matter(text)
    title = front.get("title")
    if isinstance(title, str) and title.strip():
        return title.strip()
    for _number, line in _prose_lines(text, skip):
        match = _H1_RE.match(line)
        if match:
            return match.group(1).strip()
    return path.stem


def _key(name: str) -> str:
    """Normalised lookup key for a note name."""
    # NFC: macOS and some sync tools store "é" decomposed in file names.
    return unicodedata.normalize("NFC", name.strip().replace("\\", "/")).casefold()


# -------------------------------------------------------------------- vault
class Vault:
    """An incrementally refreshed index over a folder of notes."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root).resolve()
        self.notes: dict[Path, Note] = {}
        self.truncated = False
        self._by_stem: dict[str, list[Path]] = {}
        self._by_relpath: dict[str, Path] = {}
        self._by_alias: dict[str, list[Path]] = {}

    # ------------------------------------------------------------- indexing
    def _walk(self) -> Iterator[Path]:
        count = 0
        for current, dirs, files in os.walk(self.root):
            dirs[:] = sorted(d for d in dirs if d not in SKIPPED_DIRS and not d.startswith("."))
            for name in sorted(files):
                if Path(name).suffix.lower() in NOTE_SUFFIXES and name.lower() not in SKIPPED_FILES:
                    count += 1
                    if count > MAX_NOTES:
                        self.truncated = True
                        return
                    yield Path(current) / name

    def refresh(self) -> bool:
        """Re-read notes that changed on disk.  Returns True if anything did.

        Only files whose size or modification time moved are parsed again, so
        calling this every second or two on a few hundred notes is cheap.
        """
        changed = False
        seen: set[Path] = set()
        self.truncated = False
        for path in self._walk():
            seen.add(path)
            try:
                stat = path.stat()
            except OSError:
                continue
            known = self.notes.get(path)
            if known is not None and known.mtime_ns == stat.st_mtime_ns and known.size == stat.st_size:
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            self.notes[path] = self._parse(path, text, stat.st_mtime_ns, stat.st_size)
            changed = True
        for gone in set(self.notes) - seen:
            del self.notes[gone]
            changed = True
        if changed:
            self._rebuild_lookup()
        return changed

    def update_text(self, path: Path, text: str) -> bool:
        """Index an in-memory buffer (unsaved edits) in place of the file.

        Returns True when the note's links, title or aliases changed — the
        things the graph and link resolution depend on.
        """
        path = Path(path).resolve()
        if not path.is_relative_to(self.root) or path.suffix.lower() not in NOTE_SUFFIXES:
            return False
        previous = self.notes.get(path)
        note = self._parse(
            path, text, previous.mtime_ns if previous else 0, previous.size if previous else 0
        )
        if previous is not None and (previous.links, previous.title, previous.aliases) == (
            note.links, note.title, note.aliases
        ):
            previous.tags, previous.words = note.tags, note.words
            return False
        self.notes[path] = note
        self._rebuild_lookup()
        return True

    @staticmethod
    def _parse(path: Path, text: str, mtime_ns: int, size: int) -> Note:
        front, _ = parse_front_matter(text)
        return Note(
            path=path,
            title=note_title(text, path),
            links=extract_links(text),
            tags=extract_tags(text, front),
            aliases=_as_list(front.get("aliases")) + _as_list(front.get("alias")),
            words=len(text.split()),
            mtime_ns=mtime_ns,
            size=size,
        )

    def _rebuild_lookup(self) -> None:
        self._by_stem.clear()
        self._by_relpath.clear()
        self._by_alias.clear()
        for path, note in self.notes.items():
            rel = path.relative_to(self.root).as_posix()
            self._by_relpath[_key(rel)] = path
            self._by_relpath[_key(rel[: -len(path.suffix)])] = path
            self._by_stem.setdefault(_key(path.stem), []).append(path)
            for alias in note.aliases:
                self._by_alias.setdefault(_key(alias), []).append(path)

    # ------------------------------------------------------------ resolving
    def _nearest(self, candidates: list[Path], source: Path | None) -> Path:
        if len(candidates) == 1 or source is None:
            return min(candidates, key=lambda p: (len(p.parts), str(p)))

        def distance(p: Path) -> tuple[int, str]:
            common = len(os.path.commonpath([p.parent, source.parent]).split(os.sep))
            return (len(p.parent.parts) + len(source.parent.parts) - 2 * common, str(p))

        return min(candidates, key=distance)

    def resolve(self, link: Link | str, source: Path | None = None) -> Path | None:
        """The note a link points at, or ``None`` when it does not exist."""
        if isinstance(link, str):
            link = Link(target=link)
        target = link.target.strip()
        if not target:
            return source  # [[#Heading]] — a link into the same note
        if link.kind == "markdown":
            base = source.parent if source is not None else self.root
            try:
                candidate = (base / target).resolve()
            except OSError:
                return None
            return candidate if candidate in self.notes else None

        key = _key(target)
        if key.startswith("./") or key.startswith("../"):
            base = source.parent if source is not None else self.root
            candidate = (base / target).resolve()
            for suffix in ("", *NOTE_SUFFIXES):
                full = candidate.with_name(candidate.name + suffix) if suffix else candidate
                if full in self.notes:
                    return full
            return None
        if key in self._by_relpath:
            return self._by_relpath[key]
        stem = key.rsplit("/", 1)[-1]
        for suffix in NOTE_SUFFIXES:
            if stem.endswith(suffix):
                stem = stem[: -len(suffix)]
                break
        for table in (self._by_stem, self._by_alias):
            candidates = table.get(stem) or table.get(key)
            if candidates:
                if "/" in key:
                    # [[folder/Note]] must match the trailing folders too.
                    tail = [c for c in candidates if _key(c.relative_to(self.root).as_posix()).removesuffix(c.suffix.lower()).endswith(key)]
                    candidates = tail or candidates
                return self._nearest(candidates, source)
        return None

    def new_note_path(self, target: str, source: Path | None = None) -> Path:
        """Where a note for an unresolved ``[[target]]`` would be created.

        Plain names go next to the linking note (Obsidian's default); names
        with a folder are relative to the vault root.  The result never
        escapes the vault.
        """
        name = target.strip().replace("\\", "/").strip("/") or "Untitled"
        if Path(name).suffix.lower() not in NOTE_SUFFIXES:
            name += ".md"
        base = self.root if "/" in name or source is None else source.parent
        candidate = (base / name).resolve()
        if not candidate.is_relative_to(self.root):
            candidate = self.root / Path(name).name
        return candidate

    # --------------------------------------------------------------- graph
    def outgoing(self, path: Path) -> list[tuple[Link, Path | None]]:
        note = self.notes.get(Path(path))
        if note is None:
            return []
        return [(link, self.resolve(link, note.path)) for link in note.links]

    def backlinks(self, path: Path) -> list[tuple[Path, Link]]:
        """Notes linking to ``path``, with the link that does it."""
        path = Path(path).resolve()
        result: list[tuple[Path, Link]] = []
        for source, note in self.notes.items():
            if source == path:
                continue
            for link in note.links:
                if self.resolve(link, source) == path:
                    result.append((source, link))
        return result

    def edges(self) -> list[tuple[Path, Path | str]]:
        """Unique directed edges.  Unresolved targets appear as strings."""
        seen: set[tuple[Path, Path | str]] = set()
        out: list[tuple[Path, Path | str]] = []
        for source, note in self.notes.items():
            for link in note.links:
                resolved = self.resolve(link, source)
                if resolved == source:
                    continue
                target: Path | str = resolved if resolved is not None else link.target.strip()
                if not target:
                    continue
                edge = (source, target)
                if edge not in seen:
                    seen.add(edge)
                    out.append(edge)
        return out

    def broken_links(self) -> list[tuple[Path, Link]]:
        out: list[tuple[Path, Link]] = []
        for source, note in self.notes.items():
            for link in note.links:
                if self.resolve(link, source) is None:
                    out.append((source, link))
        return out

    def orphans(self) -> list[Path]:
        """Notes with no links in or out."""
        connected: set[Path] = set()
        for source, target in self.edges():
            connected.add(source)
            if isinstance(target, Path):
                connected.add(target)
        return sorted(p for p in self.notes if p not in connected)

    def neighbourhood(self, centre: Path, depth: int = 1) -> set[Path | str]:
        """Nodes within ``depth`` hops of ``centre``, in either direction."""
        adjacency: dict[Path | str, set[Path | str]] = {}
        for source, target in self.edges():
            adjacency.setdefault(source, set()).add(target)
            adjacency.setdefault(target, set()).add(source)
        frontier: set[Path | str] = {centre}
        reached: set[Path | str] = {centre}
        for _ in range(max(0, depth)):
            frontier = {n for node in frontier for n in adjacency.get(node, ())} - reached
            reached |= frontier
        return reached

    def find(self, query: str) -> Path | None:
        """A note by path (absolute or vault-relative) or by name."""
        candidate = Path(query).expanduser()
        for option in (candidate, self.root / candidate):
            try:
                resolved = option.resolve()
            except OSError:
                continue
            if resolved in self.notes:
                return resolved
        return self.resolve(query)

    def note_names(self) -> list[str]:
        """Names to offer when completing ``[[``: stems, plus aliases."""
        names = {p.stem for p in self.notes}
        for note in self.notes.values():
            names.update(note.aliases)
        return sorted(names, key=str.casefold)

    def rel(self, path: Path) -> str:
        try:
            return path.relative_to(self.root).as_posix()
        except ValueError:
            return str(path)

    # -------------------------------------------------------------- export
    def to_dict(self) -> dict[str, object]:
        """The whole graph as plain data (what ``emdee vault graph`` prints)."""
        nodes: list[dict[str, object]] = []
        degree: dict[Path | str, int] = {}
        edges = self.edges()
        for source, target in edges:
            degree[source] = degree.get(source, 0) + 1
            degree[target] = degree.get(target, 0) + 1
        for path, note in sorted(self.notes.items()):
            nodes.append(
                {
                    "id": self.rel(path),
                    "title": note.title,
                    "tags": note.tags,
                    "aliases": note.aliases,
                    "words": note.words,
                    "degree": degree.get(path, 0),
                    "exists": True,
                }
            )
        missing = sorted({t for _, t in edges if isinstance(t, str)}, key=str.casefold)
        for name in missing:
            nodes.append({"id": name, "title": name, "tags": [], "aliases": [], "words": 0,
                          "degree": degree.get(name, 0), "exists": False})
        return {
            "root": str(self.root),
            "nodes": nodes,
            "edges": [
                {"source": self.rel(s), "target": self.rel(t) if isinstance(t, Path) else t}
                for s, t in edges
            ],
            "truncated": self.truncated,
        }

    def to_json(self, indent: int | None = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    def to_dot(self) -> str:
        lines = ["digraph vault {", "  node [shape=box];"]
        for path, note in sorted(self.notes.items()):
            lines.append(f"  {json.dumps(self.rel(path))} [label={json.dumps(note.title)}];")
        for source, target in self.edges():
            if isinstance(target, Path):
                lines.append(f"  {json.dumps(self.rel(source))} -> {json.dumps(self.rel(target))};")
            else:
                lines.append(f"  {json.dumps(target)} [style=dashed];")
                lines.append(f"  {json.dumps(self.rel(source))} -> {json.dumps(target)} [style=dashed];")
        lines.append("}")
        return "\n".join(lines) + "\n"

    def to_mermaid(self) -> str:
        ids: dict[Path | str, str] = {}

        def node_id(node: Path | str) -> str:
            return ids.setdefault(node, f"n{len(ids)}")

        lines = ["graph LR"]
        for path, note in sorted(self.notes.items()):
            lines.append(f'  {node_id(path)}["{note.title.replace(chr(34), "#quot;")}"]')
        for source, target in self.edges():
            if isinstance(target, str) and target not in ids:
                lines.append(f'  {node_id(target)}["{target.replace(chr(34), "#quot;")}"]:::missing')
            lines.append(f"  {node_id(source)} --> {node_id(target)}")
        lines.append("  classDef missing stroke-dasharray: 4 4;")
        return "\n".join(lines) + "\n"

