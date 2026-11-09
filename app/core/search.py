# SPDX-License-Identifier: GPL-3.0-or-later
"""Full-text search across a folder of notes, forgiving about word forms.

Built for studying: searching ``forma`` should also find *formas*,
*formando* and *formación*; ``crear`` should find *creación* and *creando*.
Each query word is expanded against the vocabulary of the vault in tiers:

1. the word itself (case and accents ignored: ``creacion`` = ``creación``);
2. words that start with it (``forma`` → ``formas``, ``formación``);
3. words with the same stem, from a light Spanish/English stemmer
   (``crear`` → ``creación``, ``creado``);
4. words that start with a long stem (``construir`` → ``construcción``);
5. one typo away, for words of five letters or more (``froma`` → ``forma``).

Query syntax (all parts combine with AND)::

    mitosis fases          notes containing both words (any form)
    "división celular"     an exact phrase
    -meiosis               notes NOT containing the word
    tag:examen             front-matter or inline #tag (prefix match)
    in:biologia/tema-3     only notes under that folder
    title:célula           a word in the note title
    asignatura:historia    any other front-matter field

Like :mod:`app.core.vault`, this module has no Qt import: the same index
serves the search panel and ``emdee vault search``.  The index is inverted
(word → positions) and refreshed incrementally from the vault, so a query
over thousands of notes answers in milliseconds.
"""

from __future__ import annotations

import bisect
import contextlib
import functools
import gc
import hashlib
import marshal
import math
import os
import re
import sys
import unicodedata
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from .vault import Vault, parse_front_matter

__all__ = [
    "HeadingHit",
    "Query",
    "SearchIndex",
    "SearchResult",
    "TitleResult",
    "LineHit",
    "Vocabulary",
    "cache_path",
    "normalize",
    "parse_query",
    "stem",
]

_WORD_RE = re.compile(r"[^\W_]+", re.UNICODE)
_QUERY_TOKEN_RE = re.compile(r'(-?)(?:([A-Za-z][\w\-]*):)?(?:"([^"]*)"?|(\S+))')

#: Field names with a meaning of their own; any other ``key:`` is front matter.
_TAG_KEYS = frozenset({"tag", "tags"})
_PATH_KEYS = frozenset({"in", "path", "folder", "carpeta"})
_TITLE_KEYS = frozenset({"title", "titulo"})

#: Score weight per expansion tier.
EXACT, PREFIX, STEM, STEM_PREFIX, FUZZY = 4.0, 2.5, 2.0, 1.5, 1.0
#: A stem must be this long before words merely starting with it count.
_MIN_STEM_PREFIX = 6

#: Suffixes the stemmer removes, longest first.  Spanish first (notes are
#: mostly Spanish), with the common English endings after.  Deliberately
#: light: two words only share a stem when they really look alike.
_SUFFIXES: tuple[str, ...] = tuple(sorted({
    # Spanish nouns / adjectives
    "amientos", "imientos", "amiento", "imiento", "aciones", "iciones", "uciones",
    "acion", "icion", "ucion", "adoras", "adores", "adora", "ador", "ancias", "encias",
    "ancia", "encia", "idades", "idad", "ativos", "ativas", "ativo", "ativa", "itivos",
    "itivas", "itivo", "itiva", "ables", "ibles", "able", "ible", "istas", "ista",
    "ismos", "ismo", "osos", "osas", "oso", "osa", "mente", "ales", "al", "ivos", "ivas",
    "ivo", "iva",
    # Spanish verb forms
    "ando", "iendo", "yendo", "aron", "ieron", "aban", "abamos", "aremos", "eremos",
    "iremos", "aria", "eria", "iria", "aran", "eran", "iran", "ados", "adas", "ado",
    "ada", "idos", "idas", "ido", "ida", "amos", "emos", "imos", "ais", "eis", "aba",
    "ia", "an", "en", "ar", "er", "ir", "as", "es", "os", "a", "e", "o", "s",
    # English
    "ations", "ation", "ating", "ated", "ates", "ate", "ings", "ing", "ed", "ers",
    "ness", "ments", "ment", "ions", "ion", "ly",
}, key=len, reverse=True))

_MIN_STEM = 3
_MIN_FUZZY = 5
#: Bump whenever what the index stores changes; the Python version is part
#: of it because :mod:`marshal`'s format may change between versions.
_CACHE_VERSION = f"1-py{sys.version_info[0]}.{sys.version_info[1]}"
_SNIPPET_CHARS = 160
_MAX_LINES_PER_NOTE = 50
#: Title search: how much a match counts by where it is (0 = the note title).
_PLACE_WEIGHT = {0: 3.0, 1: 2.0, 2: 1.6, 3: 1.3}


# ------------------------------------------------------------ normalising
def normalize(word: str) -> str:
    """Case- and accent-insensitive form of ``word``; ``ñ`` is kept."""
    if word.isascii():
        return word.lower()
    if len(word) <= 40:
        return _normalize_word(word)
    return _normalize(word)


@functools.lru_cache(maxsize=65536)
def _normalize_word(word: str) -> str:
    return _normalize(word)


#: Combining marks (accents, diaeresis…) in the Unicode blocks text uses.
_COMBINING_RE = re.compile("[\u0300-\u036f\u1ab0-\u1aff\u1dc0-\u1dff\u20d0-\u20ff\ufe20-\ufe2f]")


def _normalize(text: str) -> str:
    decomposed = unicodedata.normalize("NFD", text.casefold()).replace("n\u0303", "ñ")
    return _COMBINING_RE.sub("", decomposed)


def stem(word: str) -> str:
    """A light stem of an already normalised word (see :data:`_SUFFIXES`)."""
    if len(word) <= _MIN_STEM:
        return word
    for suffix in _SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= _MIN_STEM:
            return word[: -len(suffix)]
    return word


def _within_one_edit(a: str, b: str) -> bool:
    """True when ``a`` and ``b`` differ by one edit or one adjacent swap."""
    la, lb = len(a), len(b)
    if abs(la - lb) > 1 or a == b:
        return a == b
    if la == lb:
        diff = [i for i in range(la) if a[i] != b[i]]
        if len(diff) == 1:
            return True
        return (len(diff) == 2 and diff[1] == diff[0] + 1
                and a[diff[0]] == b[diff[1]] and a[diff[1]] == b[diff[0]])
    if la > lb:
        a, b = b, a
    i = 0
    while i < len(a) and a[i] == b[i]:
        i += 1
    return a[i:] == b[i + 1:]


# ------------------------------------------------------------ vocabulary
class Vocabulary:
    """The distinct words of an index and their accepted variants.

    ``terms`` is the index's own ``word → postings`` mapping, read live;
    call :meth:`mark_dirty` whenever a word appears in or vanishes from it.
    """

    def __init__(self, terms: dict[str, object]) -> None:
        self._terms = terms
        self._sorted: list[str] = []
        self._stems: dict[str, set[str]] = {}
        self._by_length: dict[int, list[str]] = {}
        self._dirty = True
        self._cache: dict[tuple[str, bool, bool, bool], dict[str, float]] = {}

    def mark_dirty(self) -> None:
        self._dirty = True

    def _ensure(self) -> None:
        if not self._dirty:
            return
        self._sorted = sorted(self._terms)
        self._stems = {}
        self._by_length = {}
        for term in self._sorted:
            self._stems.setdefault(stem(term), set()).add(term)
            self._by_length.setdefault(len(term), []).append(term)
        self._cache.clear()
        self._dirty = False

    def same_stem(self, word: str) -> set[str]:
        self._ensure()
        return set(self._stems.get(stem(word), ()))

    def expand(
        self, word: str, *, exact: bool = False, fuzzy: bool = True, prefix: bool = True
    ) -> dict[str, float]:
        """Words that count as a match for ``word``, with tier weights."""
        self._ensure()
        key = (word, exact, fuzzy, prefix)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        found: dict[str, float] = {}
        if word in self._terms:
            found[word] = EXACT
        if not exact:
            if prefix and len(word) >= 2:
                self._starting_with(word, PREFIX, found)
            if len(word) >= _MIN_STEM:
                root = stem(word)
                for term in self._stems.get(root, ()):
                    found.setdefault(term, STEM)
                if prefix and len(root) >= _MIN_STEM_PREFIX and root != word:
                    self._starting_with(root, STEM_PREFIX, found)
            if fuzzy and len(word) >= _MIN_FUZZY:
                for length in (len(word) - 1, len(word), len(word) + 1):
                    for term in self._by_length.get(length, ()):
                        if term not in found and term[0] == word[0] and _within_one_edit(word, term):
                            found[term] = FUZZY
        if len(self._cache) > 2048:
            self._cache.clear()
        self._cache[key] = found
        return found

    def _starting_with(self, prefix: str, weight: float, found: dict[str, float]) -> None:
        start = bisect.bisect_left(self._sorted, prefix)
        for term in self._sorted[start:]:
            if not term.startswith(prefix):
                break
            # ``df`` should not reach every ``df.something`` of the code.
            if "." not in term[len(prefix):]:
                found.setdefault(term, weight)


# ----------------------------------------------------------------- query
@dataclass
class Query:
    """A parsed search query."""

    words: list[str] = field(default_factory=list)
    phrases: list[list[str]] = field(default_factory=list)
    exclude_words: list[str] = field(default_factory=list)
    exclude_phrases: list[list[str]] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    exclude_tags: list[str] = field(default_factory=list)
    folders: list[str] = field(default_factory=list)
    title_words: list[str] = field(default_factory=list)
    fields: list[tuple[str, str, bool]] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not (self.words or self.phrases or self.tags or self.folders
                    or self.title_words or self.fields)


def _words(text: str) -> list[str]:
    return [normalize(m.group(0)) for m in _WORD_RE.finditer(text)]


def parse_query(text: str) -> Query:
    """Split a query string into words, phrases, exclusions and filters."""
    query = Query()
    for match in _QUERY_TOKEN_RE.finditer(text):
        negated, key, quoted, bare = match.group(1) == "-", match.group(2), match.group(3), match.group(4)
        value = quoted if quoted is not None else (bare or "")
        if key:
            key = key.lower()
            if not value:
                continue
            if key in _TAG_KEYS:
                (query.exclude_tags if negated else query.tags).append(normalize(value.lstrip("#")))
            elif key in _PATH_KEYS:
                query.folders.append(normalize(value.replace("\\", "/").strip("/")))
            elif key in _TITLE_KEYS:
                query.title_words.extend(_words(value))
            else:
                query.fields.append((key, normalize(value), negated))
            continue
        if quoted is not None:
            words = _words(quoted)
            if len(words) > 1:
                (query.exclude_phrases if negated else query.phrases).append(words)
                continue
            target = query.exclude_words if negated else query.words
            target.extend(words)
            continue
        if bare.startswith("#") and len(bare) > 1 and not negated:
            query.tags.append(normalize(bare[1:]))
            continue
        (query.exclude_words if negated else query.words).extend(_words(bare))
    return query


# ----------------------------------------------------------------- index
@dataclass
class _Doc:
    path: Path
    rel: str
    #: The indexed text; None after loading from the cache (read on demand).
    source: str | None
    title: str
    title_words: frozenset[str]
    tags: tuple[str, ...]
    front: dict[str, object]
    #: The distinct normalised words (what the postings hold).
    words: frozenset[str]
    mtime_ns: int
    size: int
    #: Indexed from an unsaved editor buffer rather than the file.
    from_buffer: bool = False
    # Word positions, worked out only for notes a search actually looks at.
    _tokens: list[tuple[str, int, int]] | None = field(default=None, repr=False)
    _terms: dict[str, list[int]] | None = field(default=None, repr=False)
    _line_starts: list[int] | None = field(default=None, repr=False)

    @property
    def text(self) -> str:
        if self.source is None:
            try:
                self.source = self.path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                self.source = ""
        return self.source

    @property
    def tokens(self) -> list[tuple[str, int, int]]:
        """``(normalised word, start, end)`` in document order."""
        if self._tokens is None:
            self._tokens = [(normalize(m.group(0)), m.start(), m.end())
                            for m in _WORD_RE.finditer(self.text)]
        return self._tokens

    @property
    def terms(self) -> dict[str, list[int]]:
        """Normalised word → indices into :attr:`tokens`."""
        if self._terms is None:
            terms: dict[str, list[int]] = {}
            for index, (term, _start, _end) in enumerate(self.tokens):
                terms.setdefault(term, []).append(index)
            self._terms = terms
        return self._terms

    @property
    def line_starts(self) -> list[int]:
        if self._line_starts is None:
            self._line_starts = [0] + [m.end() for m in re.finditer("\n", self.text)]
        return self._line_starts

    def line_of(self, offset: int) -> int:
        """1-based line number of a character offset."""
        return bisect.bisect_right(self.line_starts, offset)


@dataclass
class LineHit:
    """One line of a note with the matched spans (columns within ``text``)."""

    line: int
    text: str
    spans: list[tuple[int, int]]


@dataclass
class SearchResult:
    """A matching note."""

    path: Path
    rel: str
    title: str
    score: float
    lines: list[LineHit]
    #: Absolute ``(start, end)`` offsets of every highlighted word.
    ranges: list[tuple[int, int]]
    #: The words as written in the note, for highlighting the preview.
    surface: list[str]

    @property
    def count(self) -> int:
        return len(self.ranges)


@dataclass
class HeadingHit(LineHit):
    """A heading that matched a title search."""

    level: int = 1


@dataclass
class TitleResult:
    """A note whose title or headings match a title search."""

    path: Path
    rel: str
    title: str
    score: float
    #: Matched spans within :attr:`title`.
    title_spans: list[tuple[int, int]]
    #: Matching headings, in document order.
    headings: list[HeadingHit]


class SearchIndex:
    """An inverted index over a :class:`Vault`'s notes."""

    def __init__(self, vault: Vault) -> None:
        from .section_search import SectionIndex  # imports this module

        self.vault = vault
        self.docs: dict[Path, _Doc] = {}
        #: The same notes, indexed by section (see :mod:`app.core.section_search`).
        self.sections = SectionIndex(self._text_of)
        self._postings: dict[str, set[Path]] = {}
        self._vocab = Vocabulary(self._postings)

    # ------------------------------------------------------------- indexing
    def refresh(self) -> bool:
        """Re-index notes the vault saw change.  Returns True if any did."""
        with _gc_paused():
            return self._refresh()

    def _refresh(self) -> bool:
        changed = False
        notes = self.vault.notes
        for path in list(self.docs):
            if path not in notes:
                self._remove(path)
                changed = True
        for path, note in notes.items():
            doc = self.docs.get(path)
            if doc is not None and doc.mtime_ns == note.mtime_ns and doc.size == note.size:
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            self._add(path, text, note.mtime_ns, note.size)
            changed = True
        return changed

    def update_text(self, path: Path, text: str) -> None:
        """Index an unsaved buffer in place of the file on disk."""
        path = Path(path).resolve()
        doc = self.docs.get(path)
        if doc is None and path not in self.vault.notes:
            return
        if doc is not None and doc.text == text:
            return
        note = self.vault.notes.get(path)
        self._add(path, text, note.mtime_ns if note else 0, note.size if note else 0,
                  from_buffer=True)

    def _text_of(self, path: Path) -> str:
        doc = self.docs.get(path)
        return doc.text if doc is not None else ""

    def _remove(self, path: Path) -> None:
        self.sections.remove(path)
        doc = self.docs.pop(path, None)
        if doc is None:
            return
        for term in doc.words:
            holders = self._postings.get(term)
            if holders is not None:
                holders.discard(path)
                if not holders:
                    del self._postings[term]
                    self._vocab.mark_dirty()

    def _add(
        self, path: Path, text: str, mtime_ns: int, size: int, *, from_buffer: bool = False
    ) -> None:
        self._remove(path)
        note = self.vault.notes.get(path)
        front, _ = parse_front_matter(text)
        # Normalising the whole text at once is far cheaper than word by word.
        words = frozenset(_WORD_RE.findall(normalize(text)))
        title = note.title if note is not None else path.stem
        doc = _Doc(
            path=path,
            rel=self.vault.rel(path),
            source=text,
            title=title,
            title_words=frozenset(_words(title)) | frozenset(_words(path.stem)),
            tags=tuple(normalize(t) for t in (note.tags if note is not None else [])),
            front={k.lower(): v for k, v in front.items()},
            words=words,
            mtime_ns=mtime_ns,
            size=size,
            from_buffer=from_buffer,
        )
        self.docs[path] = doc
        self.sections.add(path, doc.rel, text, title, doc.tags, doc.front)
        for term in words:
            holders = self._postings.setdefault(term, set())
            if not holders:
                self._vocab.mark_dirty()
            holders.add(path)

    # ---------------------------------------------------------------- cache
    def save(self, path: Path | None = None) -> bool:
        """Write the index to ``path`` (default :func:`cache_path`) for a fast reopen.

        Notes indexed from unsaved buffers are stored as stale, so the next
        :meth:`refresh` reads them from disk again.  Returns False when the
        cache cannot be written (it is only ever an optimisation).
        """
        path = path or cache_path(self.vault.root)
        docs = [
            (str(d.path), d.rel, d.title, tuple(d.title_words), d.tags, d.front,
             tuple(d.words), -1 if d.from_buffer else d.mtime_ns, d.size)
            for d in self.docs.values()
        ]
        data = {"version": _CACHE_VERSION, "root": str(self.vault.root), "docs": docs,
                "sections": self.sections.dump()}
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_bytes(marshal.dumps(data, 4))
            os.replace(tmp, path)
        except (OSError, ValueError):
            return False
        return True

    def load(self, path: Path | None = None) -> bool:
        """Start from a cache written by :meth:`save`; then call :meth:`refresh`.

        Returns False (and leaves the index empty) when there is no usable
        cache for this vault, e.g. one written by another emdee version.
        """
        path = path or cache_path(self.vault.root)
        with _gc_paused():
            try:
                # marshal.loads on the whole file: marshal.load reads it piecemeal.
                data = marshal.loads(path.read_bytes())
                if not isinstance(data, dict) or data.get("version") != _CACHE_VERSION \
                        or data.get("root") != str(self.vault.root):
                    return False
                docs: dict[Path, _Doc] = {}
                postings: dict[str, set[Path]] = {}
                for (doc_path, rel, title, title_words, tags, front, words, mtime_ns,
                     size) in data["docs"]:
                    owner = Path(doc_path)
                    docs[owner] = _Doc(owner, rel, None, title, frozenset(title_words),
                                       tuple(tags), front, frozenset(words), mtime_ns, size)
                    for term in words:
                        holders = postings.get(term)
                        if holders is None:
                            postings[term] = {owner}
                        else:
                            holders.add(owner)
                self.sections.load(data["sections"])
            except (OSError, EOFError, ValueError, TypeError, KeyError):
                self.docs.clear()
                self._postings.clear()
                self.sections = type(self.sections)(self._text_of)
                return False
        self.docs = docs
        self._postings.clear()
        self._postings.update(postings)
        self._vocab.mark_dirty()
        return True

    # ------------------------------------------------------------ expanding
    def expand(self, word: str, *, exact: bool = False) -> dict[str, float]:
        """Vocabulary words that count as a match for ``word``, with weights."""
        return self._vocab.expand(word, exact=exact)

    # ------------------------------------------------------------ searching
    def search_sections(self, text: str, *, scope: str = "", exact: bool = False,
                        limit: int | None = 50):
        """Sections that explain ``text`` (words, a question, code or an error).

        Returns ``(results, analysis)``; see :meth:`SectionIndex.search`.
        """
        return self.sections.search(text, scope=scope, exact=exact, limit=limit)

    def folders(self) -> list[str]:
        """Vault-relative folders that contain notes (for a scope picker)."""
        seen: set[str] = set()
        for doc in self.docs.values():
            parts = doc.rel.split("/")[:-1]
            for depth in range(1, len(parts) + 1):
                seen.add("/".join(parts[:depth]))
        return sorted(seen, key=str.casefold)

    def search(
        self,
        query: Query | str,
        *,
        scope: str = "",
        exact: bool = False,
        limit: int | None = None,
    ) -> list[SearchResult]:
        """Notes matching ``query``, best first.

        ``scope`` is a vault-relative folder that results must lie under; it
        combines with any ``in:`` in the query.
        """
        if isinstance(query, str):
            query = parse_query(query)
        if query.is_empty:
            return []

        expansions = [self.expand(w, exact=exact) for w in query.words]
        title_expansions = [self.expand(w, exact=exact) for w in query.title_words]
        excluded: set[str] = set()
        for word in query.exclude_words:
            excluded |= set(self.expand(word, exact=True)) | ({word} if exact else self._vocab.same_stem(word))
        if any(not e for e in expansions):
            return []

        # Narrow the candidates with the rarest word first.
        candidates: set[Path] | None = None
        for expansion in sorted(expansions, key=lambda e: sum(len(self._postings[t]) for t in e)):
            holders: set[Path] = set()
            for term in expansion:
                holders |= self._postings[term]
            candidates = holders if candidates is None else candidates & holders
            if not candidates:
                return []
        for phrase in query.phrases:
            holders = set(self._postings.get(phrase[0], ()))
            candidates = holders if candidates is None else candidates & holders
        if candidates is None:
            candidates = set(self.docs)

        scope_key = normalize(scope.replace("\\", "/").strip("/"))
        folders = [f for f in (scope_key, *query.folders) if f]
        results: list[SearchResult] = []
        for path in candidates:
            doc = self.docs.get(path)
            if doc is None or not self._passes_filters(doc, query, folders, title_expansions):
                continue
            if excluded and not excluded.isdisjoint(doc.words):
                continue
            if any(self._phrase_hits(doc, p) for p in query.exclude_phrases):
                continue
            hit_tokens: set[int] = set()
            score = 0.0
            for expansion in expansions:
                for term, weight in expansion.items():
                    positions = doc.terms.get(term)
                    if positions:
                        hit_tokens.update(positions)
                        score += weight * math.log1p(len(positions))
                        if term in doc.title_words:
                            score += weight * 2
            phrase_ok = True
            for phrase in query.phrases:
                found = self._phrase_hits(doc, phrase)
                if not found:
                    phrase_ok = False
                    break
                hit_tokens.update(found)
                score += EXACT * 2 * math.log1p(len(found))
            if not phrase_ok:
                continue
            if not hit_tokens:
                score = 1.0  # filters only: every note in scope matches equally
            results.append(self._result(doc, sorted(hit_tokens), score))

        results.sort(key=lambda r: (-r.score, r.rel.casefold()))
        return results[:limit] if limit else results

    def search_titles(
        self,
        query: Query | str,
        *,
        scope: str = "",
        exact: bool = False,
        limit: int | None = None,
    ) -> list[TitleResult]:
        """Notes whose title or headings (H1–H6) match ``query``, best first.

        Every word must appear in the note title (or file name) or in one of its headings
        (any form, or the start of a word while typing); phrases must sit
        within one of them.  ``-word`` drops notes with it in a heading, and
        the note filters (``tag:``, ``in:``, ``title:``, ``field:value``) work
        as in :meth:`search`.  With filters or a ``scope`` but no words,
        every note there is listed.
        """
        if isinstance(query, str):
            query = parse_query(query)
        scope_key = normalize(scope.replace("\\", "/").strip("/"))
        folders = [f for f in (scope_key, *query.folders) if f]
        if query.is_empty and not folders:
            return []

        words = query.words
        expansions = [self.expand(w, exact=exact) for w in words]
        title_expansions = [self.expand(w, exact=exact) for w in query.title_words]
        excluded: set[str] = set()
        for word in query.exclude_words:
            excluded |= set(self.expand(word, exact=True)) | ({word} if exact else self._vocab.same_stem(word))

        def weight(term: str, i: int) -> float:
            found = expansions[i].get(term, 0.0)
            if not found and not exact and term.startswith(words[i]):
                found = PREFIX
            return found

        results: list[TitleResult] = []
        for doc in self.docs.values():
            if not self._passes_filters(doc, query, folders, title_expansions):
                continue
            # A cheap check before looking at the headings: every word must be
            # somewhere in the note (file names are not in the vocabulary).
            if any(doc.words.isdisjoint(expansions[i]) and doc.title_words.isdisjoint(expansions[i])
                   and (exact or not any(
                    t.startswith(w) for t in doc.title_words))
                   for i, w in enumerate(words)):
                continue
            places = [(0, doc.title, 1)]
            if normalize(doc.path.stem) != normalize(doc.title):
                places.append((0, doc.path.stem, 1))  # the file name counts as a title
            places += [(s.level, s.title, s.line) for s in self.sections.headings(doc.path)]
            tokens = [[(normalize(m.group(0)), m.start(), m.end()) for m in _WORD_RE.finditer(text)]
                      for _level, text, _line in places]
            if excluded and any(t in excluded for toks in tokens for t, _s, _e in toks):
                continue
            if any(_token_phrase(toks, p) for toks in tokens for p in query.exclude_phrases):
                continue
            spans: list[set[tuple[int, int]]] = [set() for _ in places]
            hit_words: list[set[int]] = [set() for _ in places]
            score = 0.0
            for i in range(len(words)):
                best = 0.0
                for p, toks in enumerate(tokens):
                    for term, start, end in toks:
                        w = weight(term, i)
                        if w:
                            spans[p].add((start, end))
                            hit_words[p].add(i)
                            best = max(best, w * _PLACE_WEIGHT.get(places[p][0], 1.0))
                if not best:
                    break
                score += best
            else:
                for phrase in query.phrases:
                    found = False
                    for p, toks in enumerate(tokens):
                        for first in _token_phrase(toks, phrase):
                            found = True
                            spans[p].add((toks[first][1], toks[first + len(phrase) - 1][2]))
                    if not found:
                        break
                    score += EXACT * 2
                else:
                    if len(words) > 1 and any(len(h) == len(words) for h in hit_words):
                        score *= 1.5  # every word in the same heading
                    if not words and not query.phrases:
                        score = 1.0  # filters only: every note there matches equally
                    headings = [
                        HeadingHit(line, text, sorted(spans[p]), level)
                        for p, (level, text, line) in enumerate(places) if level and spans[p]
                    ][:_MAX_LINES_PER_NOTE]
                    results.append(TitleResult(doc.path, doc.rel, doc.title, score,
                                               sorted(spans[0]), headings))

        results.sort(key=lambda r: (-r.score, r.rel.casefold()))
        return results[:limit] if limit else results

    def title_ranges(self, text: str, query: Query | str, *, exact: bool = False) -> list[tuple[int, int]]:
        """Like :meth:`highlight_ranges`, but only on the heading lines of ``text``."""
        from .sections import parse_sections  # imports this module

        lines = {s.line for s in parse_sections(text) if s.level}
        starts = [0] + [m.end() for m in re.finditer("\n", text)]
        return [(s, e) for s, e in self.highlight_ranges(text, query, exact=exact)
                if bisect.bisect_right(starts, s) in lines]

    def _passes_filters(
        self,
        doc: _Doc,
        query: Query,
        folders: list[str],
        title_expansions: list[dict[str, float]],
    ) -> bool:
        rel = normalize(doc.rel)
        for folder in folders:
            if not (rel.startswith(folder + "/") or f"/{folder}/" in f"/{rel}"):
                return False
        for tag in query.tags:
            if not any(t == tag or t.startswith(tag) for t in doc.tags):
                return False
        for tag in query.exclude_tags:
            if any(t == tag or t.startswith(tag) for t in doc.tags):
                return False
        for expansion in title_expansions:
            if not any(t in doc.title_words for t in expansion):
                return False
        for key, value, negated in query.fields:
            raw = doc.front.get(key)
            values = raw if isinstance(raw, list) else [raw] if raw is not None else []
            present = any(value in normalize(str(v)) for v in values)
            if present == negated:
                return False
        return True

    @staticmethod
    def _phrase_hits(doc: _Doc, phrase: list[str]) -> list[int]:
        """Token indices covered by every occurrence of ``phrase``.

        Every word must match exactly except the last, which may be a prefix
        ("división celul" finds "división celular").
        """
        out: list[int] = []
        tokens = doc.tokens
        for first in doc.terms.get(phrase[0], ()):
            end = first + len(phrase)
            if end > len(tokens):
                continue
            if all(tokens[first + i][0] == phrase[i] for i in range(len(phrase) - 1)) and \
                    tokens[end - 1][0].startswith(phrase[-1]):
                out.extend(range(first, end))
        return out

    @staticmethod
    def _result(doc: _Doc, hit_tokens: list[int], score: float) -> SearchResult:
        ranges = [(doc.tokens[i][1], doc.tokens[i][2]) for i in hit_tokens]
        surface = sorted({doc.text[s:e] for s, e in ranges}, key=len, reverse=True)
        by_line: dict[int, list[tuple[int, int]]] = {}
        for start, end in ranges:
            by_line.setdefault(doc.line_of(start), []).append((start, end))
        lines: list[LineHit] = []
        for number in sorted(by_line)[:_MAX_LINES_PER_NOTE]:
            line_start = doc.line_starts[number - 1]
            line_end = doc.text.find("\n", line_start)
            line_text = doc.text[line_start: line_end if line_end >= 0 else len(doc.text)]
            spans = [(s - line_start, e - line_start) for s, e in by_line[number]]
            lines.append(_snippet(number, line_text, spans))
        return SearchResult(doc.path, doc.rel, doc.title, score, lines, ranges, surface)

    def highlight_ranges(self, text: str, query: Query | str, *, exact: bool = False) -> list[tuple[int, int]]:
        """Offsets in ``text`` (e.g. the live editor buffer) of words the query matches."""
        if isinstance(query, str):
            query = parse_query(query)
        terms: set[str] = set()
        for word in query.words:
            terms |= set(self.expand(word, exact=exact))
        tokens = [(normalize(m.group(0)), m.start(), m.end()) for m in _WORD_RE.finditer(text)]
        hits = {i for i, (word, _s, _e) in enumerate(tokens) if word in terms}
        for phrase in query.phrases:
            for first in range(len(tokens) - len(phrase) + 1):
                if all(tokens[first + i][0] == phrase[i] for i in range(len(phrase) - 1)) and \
                        tokens[first + len(phrase) - 1][0].startswith(phrase[-1]):
                    hits.update(range(first, first + len(phrase)))
        return [(tokens[i][1], tokens[i][2]) for i in sorted(hits)]


def _token_phrase(tokens: list[tuple[str, int, int]], phrase: list[str]) -> list[int]:
    """Indices where ``phrase`` starts in ``tokens`` (the last word may be a prefix)."""
    size = len(phrase)
    return [first for first in range(len(tokens) - size + 1)
            if all(tokens[first + i][0] == phrase[i] for i in range(size - 1))
            and tokens[first + size - 1][0].startswith(phrase[-1])]


@contextlib.contextmanager
def _gc_paused() -> Iterator[None]:
    """Indexing creates millions of small objects, none in cycles; the cyclic
    GC would rescan them again and again while they are made, for nothing.

    Afterwards they are frozen out of later collections too, which would
    otherwise walk the whole index every so often (a pause in the UI).
    """
    enabled = gc.isenabled()
    gc.disable()
    try:
        yield
    finally:
        gc.freeze()
        if enabled:
            gc.enable()


def cache_path(root: Path) -> Path:
    """Where the search cache of the vault at ``root`` lives.

    ``$XDG_CACHE_HOME/emdee/search`` (``~/.cache``) on Linux, the user cache
    folder on macOS and Windows; one file per vault, named by its path.
    """
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Caches"
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    digest = hashlib.sha1(str(Path(root).resolve()).encode("utf-8")).hexdigest()[:16]
    return base / "emdee" / "search" / f"{digest}.idx"


def _snippet(number: int, text: str, spans: list[tuple[int, int]]) -> LineHit:
    """Trim a long line around its first match, shifting the spans to fit."""
    text = text.rstrip("\r")
    stripped = len(text) - len(text.lstrip())
    text = text.strip()
    spans = [(max(0, s - stripped), max(0, e - stripped)) for s, e in spans]
    if len(text) <= _SNIPPET_CHARS:
        return LineHit(number, text, spans)
    first = spans[0][0]
    begin = max(0, first - _SNIPPET_CHARS // 3)
    end = min(len(text), begin + _SNIPPET_CHARS)
    prefix = "…" if begin else ""
    suffix = "…" if end < len(text) else ""
    shift = begin - len(prefix)
    kept = [(s - shift, e - shift) for s, e in spans if s >= begin and e <= end]
    return LineHit(number, prefix + text[begin:end] + suffix, kept)
