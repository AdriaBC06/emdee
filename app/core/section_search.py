# SPDX-License-Identifier: GPL-3.0-or-later
"""Search by section: find the note *and* the heading where something is explained.

The unit of search is a section (see :mod:`app.core.sections`).  Each one is
indexed as separate fields, and a match counts more or less depending on the
field it is in (BM25F)::

    title  >  aka  >  keys ≈ api ≈ errors  >  intent/questions ≈ path  >  body  >  code

``path`` is the enclosing headings and the note title, so a ``### Ejemplo``
under ``## Merge`` still matches ``merge``.  Pasted code turns the weights
around: then ``api``, ``errors`` and ``code`` lead (see :data:`WEIGHTS`).

Matching is soft.  Every concept of the query (see
:mod:`app.core.query_analysis`) adds to the score by how rare it is and where
it matches, and the total is scaled by how much of the query a section
covers, so a natural-language question still finds the section that answers
it even when a word or two are missing.  Note-level filters (``tag:``,
``in:``, ``title:``, front-matter ``field:value``, ``-word``) work as in the
note search.
"""

from __future__ import annotations

import heapq
import math
import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .query_analysis import Concept, QueryAnalysis, analyze, code_terms
from .search import (
    EXACT,
    LineHit,
    Query,
    Vocabulary,
    _snippet,
    normalize,
    parse_query,
)
from .sections import Section, front_matter_meta, parse_sections

__all__ = ["FIELDS", "SectionIndex", "SectionResult", "WEIGHTS", "term_ranges"]

_WORD_RE = re.compile(r"[^\W_]+", re.UNICODE)

#: Indexed fields, in a fixed order (their position is their id).
FIELDS = ("title", "aka", "keys", "api", "errors", "intent", "path", "body", "code")
_F = {name: i for i, name in enumerate(FIELDS)}

#: Field weights for typed words and for pasted code / errors.
WEIGHTS: dict[str, tuple[float, ...]] = {
    #         title  aka  keys  api  errors intent path body code
    "words": (9.0, 7.0, 5.0, 4.0, 3.5, 3.0, 2.0, 1.0, 0.6),
    "text": (5.0, 4.0, 4.0, 3.0, 3.0, 3.0, 1.5, 1.2, 0.6),
    "code": (2.0, 2.0, 3.0, 6.0, 5.0, 1.0, 1.0, 0.7, 3.0),
    "traceback": (2.0, 2.0, 3.0, 5.0, 7.0, 1.5, 1.0, 1.0, 2.5),
}
#: How much a field's length dampens it (BM25 ``b``), per field.
_B = (0.3, 0.3, 0.3, 0.3, 0.3, 0.4, 0.2, 0.75, 0.75)
_K1 = 1.2
#: Most distinctive words kept from a long pasted statement.
_TEXT_TERMS = 20
_SNIPPET_LINES = 3


@dataclass
class _Entry:
    path: Path
    rel: str
    note_title: str
    tags: tuple[str, ...]
    front: dict[str, object]
    section: Section
    #: field id → number of indexed terms in it (its "length").
    lengths: tuple[int, ...]
    #: Normalised words of title + aka + keys, for phrase bonuses.
    head_text: str
    #: Normalised words of the whole section, for phrase checks.
    all_text: str
    #: Every index term of the section.
    terms: tuple[str, ...]


@dataclass
class SectionResult:
    """A matching section."""

    path: Path
    rel: str
    note_title: str
    title: str
    level: int
    #: Enclosing headings, outermost first (without the note title).
    parents: list[str]
    line: int
    end_line: int
    score: float
    #: Lines of the section that best show the match.
    snippet: list[LineHit]
    #: Whether the snippet comes from a code block.
    snippet_is_code: bool
    #: Index terms that matched, for highlighting the note.
    terms: list[str]
    #: Fields that matched, best first (``title``, ``keys``, ``api``…).
    fields: list[str]
    meta: dict[str, list[str]]
    #: Share of the query this section covers (0–1).
    coverage: float

    @property
    def crumbs(self) -> list[str]:
        """``note title › parents › title``, without repeating the note title."""
        path = [*self.parents, self.title] if self.level else list(self.parents)
        if path and normalize(path[0]) == normalize(self.note_title):
            path = path[1:]
        return [self.note_title, *path]


class SectionIndex:
    """Field-weighted index of every section in a set of notes.

    Fed by :class:`app.core.search.SearchIndex`, which owns the reading of
    files; each note is (re)indexed whole when it changes.
    """

    def __init__(self, text_of: Callable[[Path], str] | None = None) -> None:
        """``text_of`` returns a note's current text (for snippets); without
        it the index keeps its own copy of every note."""
        self._text_of = text_of
        self._texts: dict[Path, str] = {}
        self.entries: dict[int, _Entry] = {}
        self._by_path: dict[Path, list[int]] = {}
        #: term → section id → per-field counts ``((field, count), …)``.
        self._postings: dict[str, dict[int, tuple[tuple[int, int], ...]]] = {}
        self._vocab = Vocabulary(self._postings)
        self._next_id = 0
        self._total_length = [0] * len(FIELDS)
        self._tf_cache: dict[tuple[str, str], dict[int, float]] = {}
        self._avg: list[float] | None = None

    def _changed(self) -> None:
        self._tf_cache.clear()
        self._avg = None

    # ------------------------------------------------------------- indexing
    def remove(self, path: Path) -> None:
        if path in self._by_path:
            self._changed()
        self._texts.pop(path, None)
        for sid in self._by_path.pop(path, ()):
            entry = self.entries.pop(sid)
            for i, length in enumerate(entry.lengths):
                self._total_length[i] -= length
            for term in entry.terms:
                holders = self._postings.get(term)
                if holders is not None:
                    holders.pop(sid, None)
                    if not holders:
                        del self._postings[term]
                        self._vocab.mark_dirty()

    def add(
        self,
        path: Path,
        rel: str,
        text: str,
        note_title: str,
        tags: tuple[str, ...],
        front: dict[str, object],
    ) -> None:
        """(Re)index the sections of one note."""
        self.remove(path)
        self._changed()
        if self._text_of is None:
            self._texts[path] = text
        note_meta = _note_meta_terms(front_matter_meta(front))
        ids: list[int] = []
        for section in parse_sections(text, note_title):
            sid = self._next_id
            self._next_id += 1
            counts, body_words = _field_counts(section, note_title, note_meta)
            lengths = tuple(c.total() for c in counts)
            for i, length in enumerate(lengths):
                self._total_length[i] += length
            head = " ".join(_words(" ".join([section.title, *section.meta.get("aka", []),
                                             *section.meta.get("keys", [])])))
            body = " ".join(body_words)
            merged: dict[str, list[tuple[int, int]]] = {}
            for f, field_counts in enumerate(counts):
                for term, count in field_counts.items():
                    merged.setdefault(term, []).append((f, count))
            self.entries[sid] = _Entry(
                path, rel, note_title, tags, front, section, lengths, head,
                f"{head} {body}", tuple(merged),
            )
            ids.append(sid)
            for term, pairs in merged.items():
                holders = self._postings.get(term)
                if holders is None:
                    holders = self._postings[term] = {}
                    self._vocab.mark_dirty()
                holders[sid] = tuple(pairs)
        self._by_path[path] = ids

    def __len__(self) -> int:
        return len(self.entries)

    def headings(self, path: Path) -> list[Section]:
        """The headings of one note (not its preamble), in document order."""
        sections = [self.entries[sid].section for sid in self._by_path.get(path, ())]
        return [s for s in sections if s.level]

    # ------------------------------------------------------------ searching
    def search(
        self,
        text: str,
        *,
        scope: str = "",
        exact: bool = False,
        limit: int | None = 50,
        filters: Query | None = None,
    ) -> tuple[list[SectionResult], QueryAnalysis]:
        """Sections that explain ``text``, best first, and how it was read.

        ``text`` may be words with the usual operators, a question, pasted
        code, a traceback or a long statement.
        """
        text = text.strip()
        if not text:
            return [], QueryAnalysis("words")
        single_line = "\n" not in text
        query = filters or Query()
        if single_line and filters is None:
            parsed = parse_query(text)
            looks_like_code = analyze(text).mode in ("code", "traceback")
            if not looks_like_code:
                query = self._known_fields_only(parsed)
        words = None
        if single_line and query is not filters and query is not None and (
            query.words or query.phrases or query.tags or query.folders or query.fields
            or query.title_words or query.exclude_words
        ):
            words = [*query.words, *[w for p in query.phrases for w in p]]
        analysis = analyze(text if words is None else " ".join(words), words,
                          synonyms_on=not exact)
        if words is not None:
            analysis.phrases = list(query.phrases)
        concepts = self._prepare(analysis, exact)
        if not concepts and not _has_filters(query, scope):
            return [], analysis

        mode = analysis.mode if analysis.mode in WEIGHTS else "words"
        n = max(1, len(self.entries))
        scores: dict[int, float] = {}
        matched_weight: dict[int, float] = {}
        total_weight = sum(c.weight for c, _e in concepts) or 1.0

        for concept, expansion in concepts:
            holders: dict[int, float] = {}
            for term, tier in expansion.items():
                for sid, tf in self._term_tf(term, mode).items():
                    holders[sid] = holders.get(sid, 0.0) + tf * tier
            if not holders:
                continue
            df = len(holders)
            gain = concept.weight * math.log(1 + (n - df + 0.5) / (df + 0.5)) * (_K1 + 1)
            for sid, tf in holders.items():
                scores[sid] = scores.get(sid, 0.0) + gain * tf / (tf + _K1 * 2.0)
                matched_weight[sid] = matched_weight.get(sid, 0.0) + concept.weight

        if not concepts:
            scores = {sid: 1.0 for sid in self.entries}
            matched_weight = {sid: 1.0 for sid in self.entries}
            total_weight = 1.0

        scope_key = normalize(scope.replace("\\", "/").strip("/"))
        folders = [f for f in (scope_key, *query.folders) if f]
        excluded = self._excluded_terms(query, exact)
        minimum = _min_coverage(analysis, len(concepts))
        adjacent = _adjacent_pairs(analysis)
        ranked: list[tuple[float, int, float]] = []
        for sid, score in scores.items():
            coverage = min(1.0, matched_weight[sid] / total_weight)
            if coverage + 1e-9 < minimum:
                continue
            entry = self.entries[sid]
            if not _passes(entry, query, folders):
                continue
            if excluded and not excluded.isdisjoint(entry.terms):
                continue
            if analysis.phrases and not all(" ".join(p) in entry.all_text for p in analysis.phrases):
                continue
            if mode in ("code", "traceback"):
                score *= 0.5 + 0.5 * coverage
            else:
                score *= 0.25 + 0.75 * coverage ** 2
            for pair in adjacent:
                if pair in entry.head_text:
                    score *= 1.25
                elif pair in entry.all_text:
                    score *= 1.08
            if entry.section.meta:
                score *= 1.03  # annotated sections were written to be found
            ranked.append((score, sid, coverage))

        def order(item: tuple[float, int, float]) -> tuple[float, str, int]:
            entry = self.entries[item[1]]
            return (-item[0], entry.rel.casefold(), entry.section.line)

        if limit and len(ranked) > limit * 2:
            # Only the best few need the full (tie-breaking) sort.
            ranked = heapq.nlargest(limit * 2, ranked, key=lambda item: item[0])
        ranked.sort(key=order)
        if limit:
            ranked = ranked[:limit]
        results = [self._result(sid, score, coverage, concepts, mode)
                   for score, sid, coverage in ranked]
        return results, analysis

    def _term_tf(self, term: str, mode: str) -> dict[int, float]:
        """Section → field-weighted, length-normalised frequency of ``term``.

        Cached until the index changes, so repeated and refined queries only
        pay for the terms they add.
        """
        key = (term, mode)
        cached = self._tf_cache.get(key)
        if cached is not None:
            return cached
        weights = WEIGHTS[mode]
        avg = self._avg_lengths()
        out: dict[int, float] = {}
        entries = self.entries
        for sid, pairs in self._postings.get(term, {}).items():
            lengths = entries[sid].lengths
            tf = 0.0
            for f, count in pairs:
                tf += weights[f] * count / (1 - _B[f] + _B[f] * lengths[f] / avg[f])
            out[sid] = tf
        if len(self._tf_cache) > 4096:
            self._tf_cache.clear()
        self._tf_cache[key] = out
        return out

    def _avg_lengths(self) -> list[float]:
        if self._avg is None:
            n = max(1, len(self.entries))
            self._avg = [max(1.0, total / n) for total in self._total_length]
        return self._avg

    def _known_fields_only(self, query: Query) -> Query:
        """Treat ``word:value`` as a filter only for fields some note has.

        Pasted statements are full of colons ("Ejercicio: calcula…", URLs);
        those stay words instead of filtering everything out.
        """
        if not query.fields:
            return query
        known = {key for entry in self.entries.values() for key in entry.front}
        kept: list[tuple[str, str, bool]] = []
        for key, value, negated in query.fields:
            if key in known:
                kept.append((key, value, negated))
            elif not negated:
                query.words.extend([normalize(key), *_words(value)])
        query.fields = kept
        return query

    def _prepare(self, analysis: QueryAnalysis, exact: bool) -> list[tuple[Concept, dict[str, float]]]:
        """Each concept with the index terms it matches (and their weights)."""
        fuzzy = analysis.mode == "words"
        out: list[tuple[Concept, dict[str, float]]] = []
        for concept in analysis.concepts:
            expansion: dict[str, float] = {}
            for term, share in concept.terms.items():
                if concept.expand and not exact:
                    # Synonyms match their own forms only, not every word they start.
                    written = share == 1.0
                    found = self._vocab.expand(term, fuzzy=fuzzy and written, prefix=written)
                elif term in self._postings:
                    found = {term: EXACT}
                else:
                    found = {}
                for word, tier in found.items():
                    weight = share * tier / EXACT
                    if weight > expansion.get(word, 0.0):
                        expansion[word] = weight
            if expansion:
                out.append((concept, expansion))
        if analysis.mode == "text" and len(out) > _TEXT_TERMS:
            # A long statement: keep the words that best tell sections apart.
            n = max(1, len(self.entries))

            def distinctiveness(item: tuple[Concept, dict[str, float]]) -> float:
                concept, expansion = item
                df = len({sid for t in expansion for sid in self._postings.get(t, ())})
                return concept.weight * math.log(1 + n / (1 + df))

            out = sorted(out, key=distinctiveness, reverse=True)[:_TEXT_TERMS]
        # Concepts nothing matches still count against coverage.
        matched = {id(c) for c, _e in out}
        out += [(c, {}) for c in analysis.concepts if id(c) not in matched]
        return out

    def _excluded_terms(self, query: Query, exact: bool) -> set[str]:
        out: set[str] = set()
        for word in query.exclude_words:
            out |= set(self._vocab.expand(word, exact=True, fuzzy=False))
            if not exact:
                out |= self._vocab.same_stem(word)
        return out

    def _result(
        self,
        sid: int,
        score: float,
        coverage: float,
        concepts: list[tuple[Concept, dict[str, float]]],
        mode: str,
    ) -> SectionResult:
        entry = self.entries[sid]
        section = entry.section
        weights = WEIGHTS[mode]
        terms: set[str] = set()
        hits: dict[int, float] = {}
        for _concept, expansion in concepts:
            for term, tier in expansion.items():
                pairs = self._postings.get(term, {}).get(sid)
                if pairs is None:
                    continue
                terms.add(term)
                for f, count in pairs:
                    hits[f] = hits.get(f, 0.0) + weights[f] * count * tier
        snippet, is_code = _best_lines(entry, terms, self._entry_text(entry))
        fields = [FIELDS[f] for f, _v in sorted(hits.items(), key=lambda kv: -kv[1])]
        return SectionResult(
            path=entry.path,
            rel=entry.rel,
            note_title=entry.note_title,
            title=section.title,
            level=section.level,
            parents=list(section.parents),
            line=section.line,
            end_line=section.end_line,
            score=score,
            snippet=snippet,
            snippet_is_code=is_code,
            terms=sorted(terms),
            fields=fields,
            meta=section.meta,
            coverage=coverage,
        )

    def _entry_text(self, entry: _Entry) -> str:
        if self._text_of is not None:
            text = self._text_of(entry.path)
        else:
            text = self._texts.get(entry.path, "")
        return text[entry.section.start:entry.section.end]

    def section_text(self, result: SectionResult) -> str:
        """The current source of a result's section (metadata comment kept)."""
        for sid in self._by_path.get(result.path, ()):
            entry = self.entries[sid]
            if entry.section.line == result.line:
                return self._entry_text(entry)
        return ""

    # ------------------------------------------------------------- snapshot
    def dump(self) -> dict[str, object]:
        """The index as plain data (for :mod:`marshal`); see :meth:`load`."""
        entries = []
        for sid, e in self.entries.items():
            sec = e.section
            entries.append((
                sid, str(e.path), e.rel, e.note_title, e.tags, e.front,
                (sec.level, sec.title, tuple(sec.parents), sec.line, sec.end_line, sec.start,
                 sec.end, sec.meta, sec.meta_lines),
                e.lengths, e.head_text, e.all_text, e.terms,
            ))
        return {
            "entries": entries,
            "postings": self._postings,
            "next_id": self._next_id,
            "total_length": list(self._total_length),
        }

    def load(self, data: dict[str, object]) -> None:
        """Replace the index with a :meth:`dump` of one."""
        paths: dict[str, Path] = {}
        self.entries = {}
        self._by_path = {}
        for (sid, path, rel, note_title, tags, front, sec, lengths, head, all_text,
             terms) in data["entries"]:  # type: ignore[attr-defined]
            owner = paths.get(path)
            if owner is None:
                owner = paths[path] = Path(path)
            level, title, parents, line, end_line, start, end, meta, meta_lines = sec
            section = Section(level, title, list(parents), line, end_line, start, end, meta,
                              meta_lines=tuple(meta_lines) if meta_lines else None)
            self.entries[sid] = _Entry(owner, rel, note_title, tuple(tags), front, section,
                                       tuple(lengths), head, all_text, tuple(terms))
            self._by_path.setdefault(owner, []).append(sid)
        self._postings.clear()
        self._postings.update(data["postings"])  # type: ignore[arg-type]
        self._next_id = int(data["next_id"])  # type: ignore[arg-type]
        self._total_length = list(data["total_length"])  # type: ignore[arg-type]
        self._vocab.mark_dirty()
        self._changed()


# ------------------------------------------------------------------ helpers
def _words(text: str) -> list[str]:
    # Normalising the whole text first is much cheaper than word by word.
    return _WORD_RE.findall(normalize(text))


def _code_field(text: str) -> list[str]:
    return _words(text) + code_terms(text) if text else []


#: Where front-matter metadata goes: the weak ``path`` field for prose, the
#: matching field for code and errors.
_NOTE_META_FIELD = {"aka": "path", "keys": "path", "intent": "path", "questions": "path",
                    "api": "api", "errors": "errors"}


def _note_meta_terms(note_meta: dict[str, list[str]]) -> list[tuple[int, set[str]]]:
    return [(_F[_NOTE_META_FIELD[name]], set(_code_field(" ".join(items))))
            for name, items in note_meta.items()]


def _field_counts(
    section: Section,
    note_title: str,
    note_meta: list[tuple[int, set[str]]],
) -> tuple[list[Counter[str]], list[str]]:
    """Term counts per field, and the normalised words of the prose."""
    meta = section.meta
    body = _words(section.prose)
    counts = [
        Counter(_words(section.title) + code_terms(section.title)),
        Counter(_words(" ".join(meta.get("aka", ())))),
        Counter(_code_field(" ".join(meta.get("keys", ())))),
        Counter(_code_field(" ".join(meta.get("api", ())))),
        Counter(_code_field(" ".join(meta.get("errors", ())))),
        Counter(_words(" ".join([*meta.get("intent", ()), *meta.get("questions", ())]))),
        Counter(_words(" ".join([note_title, *section.parents]))),
        Counter(body),
        Counter(_code_field(section.code)),
    ]
    # Front-matter metadata counts once per section, whatever its repetitions.
    for field_id, terms in note_meta:
        target = counts[field_id]
        for term in terms:
            target.setdefault(term, 1)
    return counts, body


def _has_filters(query: Query, scope: str) -> bool:
    return bool(scope or query.tags or query.folders or query.fields or query.title_words
                or query.exclude_tags)


def _passes(entry: _Entry, query: Query, folders: list[str]) -> bool:
    rel = normalize(entry.rel)
    for folder in folders:
        if not (rel.startswith(folder + "/") or f"/{folder}/" in f"/{rel}"):
            return False
    for tag in query.tags:
        if not any(t == tag or t.startswith(tag) for t in entry.tags):
            return False
    for tag in query.exclude_tags:
        if any(t == tag or t.startswith(tag) for t in entry.tags):
            return False
    if query.title_words:
        title = set(_words(entry.note_title)) | set(_words(entry.section.title))
        for word in query.title_words:
            if not any(t.startswith(word) for t in title):
                return False
    for key, value, negated in query.fields:
        raw = entry.front.get(key)
        values = raw if isinstance(raw, list) else [raw] if raw is not None else []
        present = any(value in normalize(str(v)) for v in values)
        if present == negated:
            return False
    return True


def _min_coverage(analysis: QueryAnalysis, count: int) -> float:
    if analysis.mode != "words" or count <= 1:
        return 0.0
    # Two words: either may be missing, but not both; more: at least half.
    return 0.34 if count <= 3 else 0.5


def _adjacent_pairs(analysis: QueryAnalysis) -> list[str]:
    if analysis.mode not in ("words", "text"):
        return []
    heads = [c.head for c in analysis.concepts if c.kind == "word"]
    return [f"{a} {b}" for a, b in zip(heads, heads[1:], strict=False)]


def _best_lines(entry: _Entry, terms: set[str], text: str) -> tuple[list[LineHit], bool]:
    """The few consecutive lines of the section with the most matched words.

    Blank lines, fence markers and the metadata comment are skipped; when
    nothing in the body matched (the hit was in the title or metadata) the
    section's opening lines are shown instead.
    """
    section = entry.section
    meta = section.meta_lines
    plain = {t for t in terms if "." not in t and "_" not in t}
    compound = [t for t in terms if "." in t or "_" in t]
    # (line number, text, spans, weight, in code)
    lines: list[tuple[int, str, list[tuple[int, int]], int, bool]] = []
    in_fence = False
    for i, line in enumerate(text.split("\n")):
        number = section.line + i
        stripped = line.strip()
        if stripped.startswith(("```", "~~~")):
            in_fence = not in_fence
            continue
        if (i == 0 and section.level) or not stripped or (meta and meta[0] <= number <= meta[1]):
            continue
        spans = [(m.start(), m.end()) for m in _WORD_RE.finditer(line)
                 if normalize(m.group(0)) in plain]
        lower = line.lower()
        for term in compound:
            at = lower.find(term)
            while at >= 0:
                spans.append((at, at + len(term)))
                at = lower.find(term, at + 1)
        weight = len({lower[a:b] for a, b in spans}) * 2 + len(spans)
        lines.append((number, line, sorted(spans), weight, in_fence))
    if not lines:
        return [], False
    best, best_start = -1, 0
    for start in range(len(lines)):
        # Windows that open on a matching line read better: they win ties.
        window = sum(item[3] for item in lines[start:start + _SNIPPET_LINES]) * 2 \
            + (1 if lines[start][3] else 0)
        if window > best:
            best, best_start = window, start
    if best <= 1:
        best_start = next((k for k, item in enumerate(lines) if not item[4]), 0)
    out: list[LineHit] = []
    is_code = False
    for number, line, spans, _weight, code in lines[best_start:best_start + _SNIPPET_LINES]:
        is_code = is_code or code
        out.append(_snippet(number, line, spans) if spans else _snippet_plain(number, line))
    return out, is_code


def term_ranges(
    text: str, terms: list[str] | set[str], first_line: int = 1, last_line: int | None = None
) -> list[tuple[int, int]]:
    """Offsets in ``text`` of the words a result matched, within those lines.

    ``terms`` are a result's index terms: normalised words, and code
    identifiers such as ``read_csv`` or ``pd.merge`` (found case-insensitively).
    Used to mark the hits of a section in the live editor buffer.
    """
    lines = text.split("\n")
    last_line = len(lines) if last_line is None else min(last_line, len(lines))
    start = sum(len(line) + 1 for line in lines[: max(0, first_line - 1)])
    end = start + sum(len(line) + 1 for line in lines[max(0, first_line - 1):last_line])
    chunk = text[start:end]
    plain = {t for t in terms if "." not in t and "_" not in t}
    out: list[tuple[int, int]] = []
    for match in _WORD_RE.finditer(chunk):
        if normalize(match.group(0)) in plain:
            out.append((start + match.start(), start + match.end()))
    lower = chunk.lower()
    for term in terms:
        if "." in term or "_" in term:
            at = lower.find(term)
            while at >= 0:
                out.append((start + at, start + at + len(term)))
                at = lower.find(term, at + 1)
    # Longest first wins where a compound term covers a plain word.
    out.sort(key=lambda r: (r[0], -(r[1] - r[0])))
    merged: list[tuple[int, int]] = []
    for a, b in out:
        if merged and a < merged[-1][1]:
            continue
        merged.append((a, b))
    return merged


def _snippet_plain(number: int, line: str) -> LineHit:
    hit = _snippet(number, line, [(0, 0)])
    return LineHit(hit.line, hit.text, [])
