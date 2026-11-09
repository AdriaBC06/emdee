# SPDX-License-Identifier: GPL-3.0-or-later
"""Turn whatever was typed or pasted into weighted search concepts.

The section search accepts four kinds of input and treats each differently:

``words``
    A few words or a question in natural language (``crear un dataframe``).
    Stopwords go, every remaining word becomes a *concept* that matches its
    word forms and its synonyms from :data:`SYNONYM_GROUPS` (Spanish and
    English: ``crear`` ≈ ``construir`` ≈ ``create``).
``code``
    A pasted snippet.  What identifies the topic is pulled out with a weight
    per kind: dotted calls (``pd.read_csv``), methods (``.groupby``),
    functions, keyword arguments (``how=``), imports, exception names, string
    literals and, with little weight, plain identifiers.
``traceback``
    A pasted error.  The exception and its message weigh most, then the code
    lines quoted in the traceback.
``text``
    A long statement (an exam question).  Content words are kept and the
    index later picks the most distinctive ones.

Everything here is pure text processing: no Qt, no index.
"""

from __future__ import annotations

import keyword
import re
from dataclasses import dataclass, field

from .search import normalize, stem

__all__ = [
    "Concept",
    "QueryAnalysis",
    "STOPWORDS",
    "SYNONYM_GROUPS",
    "analyze",
    "code_terms",
    "synonyms",
]

_WORD_RE = re.compile(r"[^\W_]+", re.UNICODE)
#: More content words than this make a query a statement, not a search.
_LONG_QUERY = 6

_STOPWORDS_TEXT = """
a al algo algun alguna algunas alguno algunos ante antes aqui asi aun cada casi como con
contra cual cuales cualquier cuando cuanto de del desde donde dos e el ella ellas ello ellos
en entre era eran es esa esas ese eso esos esta estan estas este esto estos fue fueron ha
habia han hasta hay la las le les lo los mas me mi mis mucho muy nada ni no nos o os otra
otras otro otros para pero poco por porque que quien se sea segun ser si sido sin sino sobre
solo su sus tal tambien tan tanto te tiene tienen todo todos tu tus un una unas uno unos
usted y ya yo puedo puede pueden hacer hace hago hacen quiero quieres debo debe deben
cosa cosas manera mediante dado dada dados dadas siguiente siguientes explica explicar
indica indique responde responder
cuál cómo qué
the of and or to in on for with by from at as is are was were be been being it its this
that these those an not no do does did how what which who when where why can could should
would will may might must i you we they he she my your our their there here into than then
so if else such using use used get make way example all any some each only same must
has have had more less one
"""
STOPWORDS: frozenset[str] = frozenset(normalize(w) for w in _STOPWORDS_TEXT.split())

#: Python keywords and very common names that say nothing about a topic.
_CODE_NOISE: frozenset[str] = frozenset(
    {normalize(k) for k in keyword.kwlist}
    | {"self", "cls", "print", "len", "range", "true", "false", "none", "x", "y", "i", "j",
       "k", "n", "a", "b", "c", "f", "s", "t", "v", "e", "tmp", "args", "kwargs", "file",
       "line", "module", "traceback", "most", "recent", "call", "last", "int", "str",
       "float", "list", "dict", "set", "tuple", "object"}
)

#: Words that mean the same for searching, Spanish and English.  Matching is
#: by word or by stem, so ``construir`` also covers *construcción*.
SYNONYM_GROUPS: tuple[tuple[str, ...], ...] = (
    ("crear", "construir", "generar", "instanciar", "inicializar", "nuevo", "create",
     "build", "construct", "new", "make", "init"),
    ("borrar", "eliminar", "quitar", "suprimir", "delete", "remove", "drop", "erase"),
    ("filtrar", "seleccionar", "escoger", "filter", "select", "where", "query", "mask"),
    ("agrupar", "agrupacion", "groupby", "group", "agregar", "aggregate", "agg"),
    ("ordenar", "ordenacion", "sort", "sorted", "sort_values", "orden"),
    ("unir", "juntar", "combinar", "fusionar", "concatenar", "merge", "join", "concat",
     "combine"),
    ("leer", "cargar", "importar", "read", "load", "import", "read_csv", "read_excel"),
    ("guardar", "escribir", "exportar", "save", "write", "export", "dump", "to_csv"),
    ("contar", "conteo", "count", "value_counts", "frecuencia", "frequency"),
    ("media", "promedio", "mean", "average", "avg"),
    ("suma", "sumar", "sum", "total"),
    ("maximo", "max", "mayor", "maximum"),
    ("minimo", "min", "menor", "minimum"),
    ("nulo", "faltante", "vacio", "nan", "null", "missing", "isna", "isnull", "fillna",
     "dropna"),
    ("duplicado", "repetido", "duplicate", "duplicated", "drop_duplicates"),
    ("columna", "column", "campo", "field", "variable"),
    ("fila", "row", "registro", "record", "observacion"),
    ("tabla", "dataframe", "df"),
    ("lista", "list", "array", "vector", "arreglo"),
    ("diccionario", "dict", "dictionary", "mapa", "mapping"),
    ("cadena", "texto", "string", "str"),
    ("error", "excepcion", "exception", "fallo"),
    ("funcion", "function", "def", "metodo", "method"),
    ("bucle", "iterar", "recorrer", "loop", "iterate", "iteracion", "iteration"),
    ("clase", "class", "objeto", "object"),
    ("grafico", "graficar", "dibujar", "plot", "chart", "figura", "figure", "plt"),
    ("renombrar", "rename"),
    ("reemplazar", "sustituir", "replace", "cambiar", "modificar"),
    ("tipo", "type", "dtype", "astype", "convertir", "conversion", "cast"),
    ("indice", "index", "indexar", "indexing", "loc", "iloc"),
    ("fecha", "date", "datetime", "timestamp"),
    ("longitud", "tamano", "len", "size", "length", "shape", "dimension"),
    ("unico", "unique", "distinct", "nunique", "distinto"),
    ("aplicar", "apply", "map", "transform"),
    ("mostrar", "imprimir", "ver", "print", "show", "display", "head"),
    ("copiar", "copia", "copy"),
    ("pivotar", "pivot", "pivot_table"),
    ("muestra", "sample", "muestreo", "sampling"),
    ("dividir", "separar", "partir", "split"),
    ("descargar", "download"),
    ("instalar", "install", "pip"),
)


def _build_synonyms() -> dict[str, list[frozenset[str]]]:
    out: dict[str, list[frozenset[str]]] = {}
    for group in SYNONYM_GROUPS:
        members = frozenset(normalize(w) for w in group)
        for word in members:
            for key in {word, stem(word)}:
                if members not in out.setdefault(key, []):
                    out[key].append(members)
    return out


_SYNONYMS = _build_synonyms()


def synonyms(word: str) -> frozenset[str]:
    """Other words that mean the same as ``word`` (normalised), possibly none.

    Word forms count (``creando`` → *create*), but a short stem must also
    share its first four letters with the group (``creer`` is not ``crear``).
    """
    groups = _SYNONYMS.get(word)
    if groups is None:
        root = stem(word)
        groups = [g for g in _SYNONYMS.get(root, ())
                  if len(root) >= 4 or any(m[:4] == word[:4] for m in g)]
    found: set[str] = set()
    for group in groups:
        found |= group
    return frozenset(found - {word})


# -------------------------------------------------------------- concepts
@dataclass
class Concept:
    """One thing the query is about.

    ``terms`` are index terms (normalised words or code identifiers) with a
    multiplier each; the first is what was written, the rest synonyms or
    shorter forms of an identifier.  ``expand`` says whether word forms
    (prefix, stem, typos) of the terms also count.
    """

    terms: dict[str, float]
    weight: float = 1.0
    kind: str = "word"
    label: str = ""
    expand: bool = True

    @property
    def head(self) -> str:
        return next(iter(self.terms))


@dataclass
class QueryAnalysis:
    """What the search understood from the input."""

    mode: str  # "words", "code", "traceback" or "text"
    concepts: list[Concept] = field(default_factory=list)
    #: Words that must appear next to each other (from ``"quoted phrases"``).
    phrases: list[list[str]] = field(default_factory=list)

    def describe(self) -> str:
        """A short human summary, e.g. for the search window."""
        labels = [c.label or c.head for c in self.concepts[:12]]
        names = {"words": "words", "code": "code", "traceback": "error",
                 "text": "statement"}
        more = " …" if len(self.concepts) > 12 else ""
        return f"{names.get(self.mode, self.mode)}: {', '.join(labels)}{more}"


# --------------------------------------------------------- code features
_IDENT = r"[A-Za-z_][A-Za-z0-9_]*"
_CHAIN_RE = re.compile(rf"(?<![\w.]){_IDENT}(?:\s*\.\s*{_IDENT})+")
_CALL_RE = re.compile(rf"(?<![\w.])({_IDENT})\s*\(")
_METHOD_RE = re.compile(rf"\.\s*({_IDENT})\s*\(")
_ATTR_RE = re.compile(rf"\.({_IDENT})\b")
_KWARG_RE = re.compile(rf"[(,]\s*({_IDENT})\s*=(?!=)")
_IMPORT_RE = re.compile(rf"^\s*(?:from\s+({_IDENT}(?:\.{_IDENT})*)\s+)?import\s+(.+)$", re.M)
_EXCEPTION_RE = re.compile(r"\b([A-Z][A-Za-z0-9_]*(?:Error|Exception|Warning|Exit|Interrupt))\b")
_ERROR_LINE_RE = re.compile(
    r"^\s*(?:[\w.]+\.)?([A-Z][A-Za-z0-9_]*(?:Error|Exception|Warning))\s*:\s*(.*)$", re.M
)
_STRING_RE = re.compile(r"""(?:[rbfuRBFU]{0,2})("([^"\\\n]|\\.){1,60}"|'([^'\\\n]|\\.){1,60}')""")
_IDENT_RE = re.compile(_IDENT)
#: Identifiers with an inner underscore: ``read_csv``, ``__init__`` excluded.
_SNAKE_RE = re.compile(r"(?<![\w])_*[A-Za-z0-9]+(?:_[A-Za-z0-9]+)+_*(?![\w])")
#: Statements that are a topic in themselves (``try`` → error handling).
_STRUCTURE_RE = re.compile(
    r"^\s*(try|except|finally|raise|with|lambda|yield|assert|while|class|def|async|await"
    r"|global|nonlocal|match|case)\b|\b(lambda|yield|await)\b",
    re.M,
)
_INLINE_KWARG_RE = re.compile(rf"\b({_IDENT})\s*=\s*['\"\w]")
_TRACE_FILE_RE = re.compile(r'^\s*File "[^"]*", line \d+(?:, in (\S+))?', re.M)
_CODE_LINE_RE = re.compile(
    rf"""(
        ^\s*(?:import|from|def|class|return|for|while|if|elif|else|try|except|with|print)\b
      | ^\s*{_IDENT}(?:\[[^\]]*\]|\.{_IDENT})*\s*[-+*/]?=(?!=)
      | {_IDENT}\.{_IDENT}\s*\(
      | ^\s*{_IDENT}\s*\([^)]*\)\s*$
      | ^\s*[>]{{3}}\s
      | ^\s*@{_IDENT}
      | [\[\](){{}}].*[\[\](){{}}].*[;:]\s*$
      | ^\s*\#\s
      | ^\s*In\s*\[\d*\]:
    )""",
    re.X,
)


def is_code_line(line: str) -> bool:
    """Heuristic: does this line look like source code rather than prose?"""
    stripped = line.strip()
    if not stripped:
        return False
    if _CODE_LINE_RE.search(line):
        return True
    words = _WORD_RE.findall(stripped)
    symbols = sum(1 for ch in stripped if ch in "()[]{}=<>:;.,_\"'#+-*/%")
    return len(words) <= 6 and symbols >= max(3, len(stripped) // 6)


def code_terms(code: str) -> list[str]:
    """Index terms of a piece of code beyond its plain words.

    Identifiers with underscores (``read_csv``) and dotted chains
    (``pd.read_csv``) are kept whole, lowercased; their parts are already
    plain words.  Used when indexing code blocks and ``api:`` fields.
    """
    out: list[str] = []
    for match in _CHAIN_RE.finditer(code):
        chain = re.sub(r"\s+", "", match.group(0)).lower()
        parts = chain.split(".")
        out.append(chain)
        for i in range(1, len(parts) - 1):
            out.append(".".join(parts[i:]))
    out.extend(m.lower() for m in _SNAKE_RE.findall(code))
    return out


def _code_concepts(code: str, *, traceback: bool) -> list[Concept]:
    found: dict[str, Concept] = {}

    def add(term: str, weight: float, kind: str, extra: dict[str, float] | None = None,
            label: str = "") -> None:
        term = term.lower()
        if not term or normalize(term) in _CODE_NOISE:
            return
        concept = found.get(term)
        if concept is None:
            terms = {term: 1.0, **(extra or {})}
            found[term] = Concept(terms, weight, kind, label or term, expand=False)
        else:
            concept.weight = max(concept.weight, weight) + 0.25

    # Errors first: the strongest clue in a traceback.
    for match in _ERROR_LINE_RE.finditer(code):
        add(match.group(1), 4.0, "error")
        for literal in re.findall(r"'([^']{1,40})'|\"([^\"]{1,40})\"", match.group(2)):
            text = (literal[0] or literal[1]).lower()
            if _IDENT_RE.fullmatch(text):
                add(text, 1.5, "literal")
        for word in _WORD_RE.findall(match.group(2)):
            norm = normalize(word)
            if len(norm) > 2 and norm not in STOPWORDS and norm not in _CODE_NOISE \
                    and norm not in found:
                found.setdefault(f"msg:{norm}", Concept({norm: 1.0}, 1.2, "message", norm))
    # The message has been read; what follows looks at code only.
    code = _ERROR_LINE_RE.sub("", code)
    for name in _EXCEPTION_RE.findall(code):
        add(name, 3.5, "error")
    if traceback:
        for func in _TRACE_FILE_RE.findall(code):
            if func and not func.startswith("<"):
                add(func, 1.0, "function")
        code = _TRACE_FILE_RE.sub("", code)

    literals = [m.group(1)[1:-1] for m in _STRING_RE.finditer(code)]
    code = _STRING_RE.sub('""', code)
    for match in _IMPORT_RE.finditer(code):
        module = match.group(1)
        if module:
            add(module, 1.5, "import")
        for name in re.split(r"\s*,\s*", match.group(2)):
            name = name.split(" as ")[0].strip(" ()")
            if _IDENT_RE.fullmatch(name.split(".")[-1] or ""):
                add(name, 2.0, "import")
    for match in _CHAIN_RE.finditer(code):
        chain = re.sub(r"\s+", "", match.group(0)).lower()
        parts = chain.split(".")
        last = parts[-1]
        if normalize(last) in _CODE_NOISE:
            continue
        # ``pd.read_csv`` and ``df.groupby``: the full chain counts most, the
        # method alone almost as much (the note may call it on another name).
        extra = {".".join(parts[i:]): 0.9 for i in range(1, len(parts) - 1)}
        extra[last] = 0.85
        is_call = code[match.end():].lstrip().startswith("(")
        add(chain, 3.0 if is_call else 2.0, "api", extra, label=chain)
    for name in _CALL_RE.findall(code):
        add(name, 2.5, "call")
    covered = {t for c in found.values() for t in c.terms}
    for name in _METHOD_RE.findall(code):
        if name.lower() not in covered:
            add(name, 2.0, "method", label=f".{name}()")
    for name in _KWARG_RE.findall(code):
        add(name, 1.2, "kwarg", label=f"{name}=")
    for literal in literals:
        literal = literal.lower()
        if _IDENT_RE.fullmatch(literal) and len(literal) > 1:
            add(literal, 0.8, "literal")
    for match in _STRUCTURE_RE.finditer(code):
        word = match.group(1) or match.group(2)
        if word not in found:
            found[word] = Concept({word: 1.0}, 1.5, "statement", word, expand=False)
    seen = {t for c in found.values() for t in c.terms}
    for ident in _IDENT_RE.findall(code):
        lower = ident.lower()
        if lower in seen or len(lower) < 3 or normalize(lower) in _CODE_NOISE:
            continue
        seen.add(lower)
        found[lower] = Concept({lower: 1.0}, 0.6, "identifier", lower, expand=False)
    return list(found.values())


def _word_concepts(
    words: list[str], *, synonyms_on: bool, weight: float = 1.0, synonym_share: float = 0.6
) -> list[Concept]:
    concepts: list[Concept] = []
    seen: set[str] = set()
    for word in words:
        if word in seen or word in STOPWORDS:
            continue
        seen.add(word)
        terms = {word: 1.0}
        if synonyms_on:
            for other in sorted(synonyms(word)):
                terms.setdefault(other, synonym_share)
        concepts.append(Concept(terms, weight, "word", word))
    return concepts


def analyze(text: str, words: list[str] | None = None, *, synonyms_on: bool = True) -> QueryAnalysis:
    """Work out what kind of input ``text`` is and what it is about.

    ``words`` overrides the free words of a one-line query (after the
    caller has taken out ``tag:``-style filters).
    """
    stripped = text.strip()
    lines = [line for line in stripped.split("\n") if line.strip()]
    if "Traceback (most recent call last)" in stripped or (
        _ERROR_LINE_RE.search(stripped) and len(lines) <= 3
    ):
        concepts = _code_concepts(stripped, traceback=True)
        return QueryAnalysis("traceback", _sorted(concepts))

    code_lines = [line for line in lines if is_code_line(line)]
    prose_lines = [line for line in lines if not is_code_line(line)]
    if code_lines and (len(code_lines) * 2 >= len(lines) or len(lines) == 1):
        concepts = _code_concepts("\n".join(code_lines), traceback=False)
        prose = [normalize(w) for line in prose_lines for w in _WORD_RE.findall(line)]
        concepts += _word_concepts(prose, synonyms_on=synonyms_on, weight=0.8)
        return QueryAnalysis("code", _sorted(concepts))

    if words is None:
        words = [normalize(w) for w in _WORD_RE.findall(stripped)]
    content = [w for w in words if w not in STOPWORDS]
    if len(content) > _LONG_QUERY or len(lines) > 2:
        concepts = _word_concepts(words, synonyms_on=synonyms_on, synonym_share=0.4)
        if code_lines:
            concepts += _code_concepts("\n".join(code_lines), traceback=False)
        else:
            # Code quoted inside the statement: ``how='left'``, ``pd.merge``.
            for term in dict.fromkeys(code_terms(stripped)):
                concepts.append(Concept({term: 1.0}, 1.5, "api", term, expand=False))
            for name in dict.fromkeys(_INLINE_KWARG_RE.findall(stripped)):
                concepts.append(Concept({name.lower(): 1.0}, 1.0, "kwarg", f"{name}=",
                                        expand=False))
        # Words written twice or more in the statement matter more.
        counts: dict[str, int] = {}
        for w in words:
            counts[w] = counts.get(w, 0) + 1
        for concept in concepts:
            if concept.kind == "word":
                concept.weight *= 1 + 0.3 * min(3, counts.get(concept.head, 1) - 1)
        return QueryAnalysis("text", concepts)

    concepts = _word_concepts(words, synonyms_on=synonyms_on)
    # Identifiers typed as words (``read_csv``, ``pd.concat``) count as code too.
    for term in code_terms(stripped):
        concepts.append(Concept({term: 1.0}, 1.5, "api", term, expand=False))
    if not concepts and words:
        # Only stopwords ("como"): search them anyway.
        concepts = [Concept({w: 1.0}, 1.0, "word", w) for w in dict.fromkeys(words)]
    return QueryAnalysis("words", concepts)


def _sorted(concepts: list[Concept]) -> list[Concept]:
    return sorted(concepts, key=lambda c: -c.weight)
