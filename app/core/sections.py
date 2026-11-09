# SPDX-License-Identifier: GPL-3.0-or-later
"""Split a note into its sections and read their search metadata.

A section runs from one heading (``#`` … ``######``) to the next heading of
any level; the text before the first heading is a section of its own, titled
after the note.  Headings inside fenced code blocks do not count.

Each section may carry a metadata comment, invisible in the preview because
the sanitizer drops comments.  It is normally placed right under the heading::

    ## Construcción de DataFrames
    <!-- busca
    claves: crear dataframe, construir tabla, create DataFrame
    aka: crear DataFrame, DataFrame desde diccionario
    para: crear un DataFrame a partir de listas, diccionarios o arrays
    preguntas: ¿cómo creo un dataframe?; ¿cómo paso un dict a tabla?
    api: pd.DataFrame, pd.DataFrame.from_dict, pd.DataFrame.from_records
    errores: ValueError: All arrays must be of the same length
    -->

Every field is optional and the English names work too (see
:data:`FIELD_ALIASES`).  A line without ``key:`` continues the previous
field.  The same keys in a note's front matter apply to all its sections.
See ``docs/guia-metadatos-notas.md`` for the authoring guide.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .search import normalize
from .vault import parse_front_matter

__all__ = [
    "FIELD_ALIASES",
    "META_FIELDS",
    "Section",
    "front_matter_meta",
    "parse_meta",
    "parse_sections",
]

#: Canonical metadata fields.
META_FIELDS = ("aka", "keys", "intent", "questions", "api", "errors")

#: Accepted spellings (normalised) of each field, Spanish and English.
FIELD_ALIASES: dict[str, str] = {
    **dict.fromkeys(("claves", "clave", "keywords", "keys", "kw", "palabras", "etiquetas",
                     "conceptos", "temas"), "keys"),
    **dict.fromkeys(("aka", "sinonimos", "sinonimo", "alias", "aliases", "tambien",
                     "titulos", "synonyms"), "aka"),
    **dict.fromkeys(("para", "intent", "intencion", "uso", "sirve", "objetivo", "purpose",
                     "como"), "intent"),
    **dict.fromkeys(("preguntas", "pregunta", "questions", "question", "q"), "questions"),
    **dict.fromkeys(("api", "code", "codigo", "funciones", "funcion", "metodos",
                     "comandos", "sintaxis"), "api"),
    **dict.fromkeys(("errores", "error", "errors", "excepciones", "exceptions", "fallos"),
                    "errors"),
}

#: Words that may open the comment (``<!-- busca`` …); any is optional.
_META_OPENERS = frozenset({"busca", "buscar", "search", "meta", "emdee", "indice", "index"})

_HEADING_RE = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+(.*?))?(?:[ \t]+#+)?[ \t]*$")
_FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
_MARKUP_START = frozenset("#`~ ")
_COMMENT_RE = re.compile(r"<!--(.*?)-->", re.DOTALL)
_META_LINE_RE = re.compile(r"^\s*([^\W\d_][\w\s]{0,24}?)\s*:\s*(.*)$")
_INLINE_CODE_RE = re.compile(r"(`+)(?!`)(.+?)(?<!`)\1(?!`)")
_EMPHASIS_RE = re.compile(r"(\*\*|__|\*|_|~~)(?=\S)(.+?)(?<=\S)\1")
_WIKI_RE = re.compile(r"\[\[([^\]|#]*)(?:#[^\]|]*)?(?:\|([^\]]*))?\]\]")
_LINK_RE = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")


@dataclass
class Section:
    """One heading and the text under it (up to the next heading)."""

    #: 1–6 for headings, 0 for the text before the first heading.
    level: int
    title: str
    #: Titles of the enclosing headings, outermost first.
    parents: list[str]
    #: 1-based line of the heading (first line of the note for level 0).
    line: int
    #: Last line (1-based, inclusive) that belongs to the section.
    end_line: int
    #: Character offsets of the section within the note.
    start: int
    end: int
    #: Canonical field (see :data:`META_FIELDS`) → items.
    meta: dict[str, list[str]] = field(default_factory=dict)
    #: Prose with code blocks and the metadata comment taken out.
    prose: str = ""
    #: Contents of fenced blocks and inline code spans.
    code: str = ""
    #: ``(first line, last line)`` of the metadata comment, if any.
    meta_lines: tuple[int, int] | None = None

    @property
    def path(self) -> list[str]:
        """Enclosing titles followed by this one."""
        return [*self.parents, self.title] if self.level else list(self.parents)


def clean_heading(text: str) -> str:
    """Heading text as read: emphasis, code ticks and link syntax removed."""
    text = _WIKI_RE.sub(lambda m: m.group(2) or m.group(1), text)
    text = _LINK_RE.sub(r"\1", text)
    text = _INLINE_CODE_RE.sub(r"\2", text)
    for _ in range(2):
        text = _EMPHASIS_RE.sub(r"\2", text)
    return text.strip()


def _split_items(value: str, field_name: str) -> list[str]:
    """Split a field value into items.

    Questions and intents split on ``;`` and ``?`` only (they are sentences
    with commas); the rest also on commas outside brackets, so
    ``pd.merge(left, right, on="id")`` stays one item.
    """
    items: list[str] = []
    depth = 0
    current: list[str] = []
    sentences = field_name in ("questions", "intent")
    for char in value:
        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth = max(0, depth - 1)
        if depth == 0 and (char == ";" or char == "|" or (char == "," and not sentences)):
            items.append("".join(current))
            current = []
            continue
        current.append(char)
        if sentences and char == "?":
            items.append("".join(current))
            current = []
    items.append("".join(current))
    return [item.strip() for item in items if item.strip(" ¿?")]


def parse_meta(body: str) -> dict[str, list[str]] | None:
    """Fields of a metadata comment's inside, or None when it is not one.

    A comment counts as metadata when it opens with a word such as ``busca``
    or when at least one of its lines starts with a known ``field:``.
    """
    lines = body.strip().split("\n")
    if not lines:
        return None
    first = lines[0].strip()
    opener = normalize(first.split(None, 1)[0]).rstrip(":") if first else ""
    if opener in _META_OPENERS:
        rest = first.split(None, 1)[1] if " " in first else ""
        lines[0] = rest
    elif not any(_known_key(line) for line in lines):
        return None
    meta: dict[str, list[str]] = {}
    current: str | None = None
    for line in lines:
        if not line.strip():
            continue
        key = _known_key(line)
        if key is not None:
            current = key
            value = line.split(":", 1)[1]
        elif current is None:
            current, value = "keys", line
        else:
            value = line
        meta.setdefault(current, []).extend(_split_items(value.strip(), current))
    return {k: v for k, v in meta.items() if v} or None


def _known_key(line: str) -> str | None:
    match = _META_LINE_RE.match(line)
    if match is None:
        return None
    return FIELD_ALIASES.get(normalize(match.group(1)).replace(" ", ""))


def front_matter_meta(front: dict[str, object]) -> dict[str, list[str]]:
    """Metadata fields given in a note's front matter (apply to every section)."""
    meta: dict[str, list[str]] = {}
    for key, value in front.items():
        name = FIELD_ALIASES.get(normalize(str(key)))
        if name is None:
            continue
        values = value if isinstance(value, list) else [value]
        for item in values:
            meta.setdefault(name, []).extend(_split_items(str(item), name))
    return meta


def parse_sections(text: str, note_title: str = "") -> list[Section]:
    """The sections of a note, in document order."""
    lines = text.split("\n")
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line) + 1)
    _front, skip = parse_front_matter(text)

    # Pass 1: the headings (outside fences) and which lines are code.
    headings: list[tuple[int, int, str]] = []  # (line index, level, title)
    code_line = [False] * len(lines)
    fence: str | None = None
    for i in range(skip, len(lines)):
        line = lines[i]
        # Only lines starting with "#", "`", "~" or a space can open anything.
        if fence is None and (not line or line[0] not in _MARKUP_START):
            continue
        found = _FENCE_RE.match(line)
        if fence is not None:
            code_line[i] = True
            if found and found.group(1)[0] == fence[0] and len(found.group(1)) >= len(fence) \
                    and not found.group(2).strip():
                fence = None
            continue
        if found and not (found.group(1)[0] == "`" and "`" in found.group(2)):
            fence = found.group(1)
            code_line[i] = True
            continue
        heading = _HEADING_RE.match(line)
        if heading:
            headings.append((i, len(heading.group(1)), clean_heading(heading.group(2) or "")))

    # Pass 2: one section per heading, plus the preamble if it has text.
    bounds: list[tuple[int, int, int, str]] = []  # (first line, last line, level, title)
    first = headings[0][0] if headings else len(lines)
    if any(lines[i].strip() for i in range(skip, first)) or not headings:
        bounds.append((skip, first - 1, 0, note_title))
    for n, (i, level, title) in enumerate(headings):
        last = headings[n + 1][0] - 1 if n + 1 < len(headings) else len(lines) - 1
        bounds.append((i, last, level, title))

    sections: list[Section] = []
    stack: list[tuple[int, str]] = []
    for first_line, last_line, level, title in bounds:
        if level:
            while stack and stack[-1][0] >= level:
                stack.pop()
        parents = [t for _l, t in stack]
        if level:
            stack.append((level, title))
        while last_line > first_line and not lines[last_line].strip():
            last_line -= 1
        section = Section(
            level=level,
            title=title,
            parents=parents,
            line=first_line + 1,
            end_line=last_line + 1,
            start=offsets[first_line],
            end=min(len(text), offsets[last_line + 1]),
        )
        _fill_body(section, lines, code_line, offsets, first_line + (1 if level else 0), last_line)
        sections.append(section)
    return sections


def _fill_body(
    section: Section,
    lines: list[str],
    code_line: list[bool],
    offsets: list[int],
    first: int,
    last: int,
) -> None:
    prose: list[str] = []
    code: list[str] = []
    for i in range(first, last + 1):
        (code if code_line[i] else prose).append(lines[i])
    prose_text = "\n".join(prose)
    # The first metadata comment of the section, wherever it sits.
    for match in _COMMENT_RE.finditer(prose_text):
        meta = parse_meta(match.group(1))
        if meta is None:
            continue
        section.meta = meta
        before = prose_text[: match.start()].count("\n")
        span = match.group(0).count("\n")
        # Map back to note lines, skipping code lines that were removed.
        prose_lines = [i for i in range(first, last + 1) if not code_line[i]]
        if before < len(prose_lines):
            start_line = prose_lines[before]
            end_index = min(before + span, len(prose_lines) - 1)
            section.meta_lines = (start_line + 1, prose_lines[end_index] + 1)
        prose_text = prose_text[: match.start()] + prose_text[match.end():]
        break
    prose_text = _COMMENT_RE.sub(" ", prose_text)
    inline = [m.group(2) for m in _INLINE_CODE_RE.finditer(prose_text)]
    section.prose = prose_text
    section.code = "\n".join(code + inline)
