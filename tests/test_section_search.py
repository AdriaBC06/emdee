# SPDX-License-Identifier: GPL-3.0-or-later
"""Search by section: metadata, query analysis, ranking quality and the cache.

The ranking regression cases live in ``fixtures/section_queries.json`` against
the annotated notes in ``fixtures/section_vault``: add a case there whenever a
query that matters ranks badly, then tune until it passes.
"""

from __future__ import annotations

import io
import json
import os
import shutil
from pathlib import Path

import pytest

from app.cli import main as vault_main
from app.core.query_analysis import analyze, code_terms, is_code_line, synonyms
from app.core.search import SearchIndex
from app.core.sections import parse_meta, parse_sections
from app.core.vault import Vault

FIXTURES = Path(__file__).parent / "fixtures"
SECTION_VAULT = FIXTURES / "section_vault"
CASES = json.loads((FIXTURES / "section_queries.json").read_text(encoding="utf-8"))


def _index(root: Path) -> SearchIndex:
    vault = Vault(root)
    vault.refresh()
    index = SearchIndex(vault)
    index.refresh()
    return index


@pytest.fixture(scope="module")
def sections() -> SearchIndex:
    return _index(SECTION_VAULT)


@pytest.fixture
def vault_copy(tmp_path: Path) -> Path:
    target = tmp_path / "vault"
    shutil.copytree(SECTION_VAULT, target)
    return target


def _keys(index: SearchIndex, query: str, **kwargs) -> list[str]:
    results, _analysis = index.search_sections(query, **kwargs)
    return [f"{r.rel}#{r.title}" for r in results]


# ------------------------------------------------------------- sections
NOTE = """\
---
title: Pandas
claves: tablas
api: pd.DataFrame
---
Texto antes del primer título.

# Pandas
Intro.

## Construcción de **DataFrames**
<!-- busca
claves: crear dataframe, construir tabla
api: pd.DataFrame, pd.merge(left, right, on="id")
preguntas: ¿cómo creo un df?; ¿y desde un dict, con columnas?
  continúa la pregunta
-->
Texto con `pd.DataFrame`.

```python
# esto no es un título
df = pd.DataFrame({"a": [1]})
```

### Ejemplo
<!-- un comentario normal, no metadatos -->
x
"""


def test_sections_follow_the_headings() -> None:
    sections = parse_sections(NOTE, "Pandas")
    assert [(s.level, s.title) for s in sections] == [
        (0, "Pandas"), (1, "Pandas"), (2, "Construcción de DataFrames"), (3, "Ejemplo"),
    ]
    assert sections[3].parents == ["Pandas", "Construcción de DataFrames"]
    construction = sections[2]
    assert construction.line == 11
    assert NOTE.split("\n")[construction.end_line - 1] == "```"
    assert "esto no es un título" in construction.code
    assert "pd.DataFrame" in construction.code  # inline code counts as code
    assert "busca" not in construction.prose


def test_metadata_fields_and_aliases() -> None:
    meta = parse_sections(NOTE, "Pandas")[2].meta
    assert meta["keys"] == ["crear dataframe", "construir tabla"]
    assert meta["api"] == ["pd.DataFrame", 'pd.merge(left, right, on="id")']
    assert meta["questions"][0] == "¿cómo creo un df?"
    assert "continúa la pregunta" in meta["questions"][-1]
    assert parse_sections(NOTE, "Pandas")[3].meta == {}


def test_metadata_in_english_and_without_opener() -> None:
    assert parse_meta("keywords: a, b\nerrors: KeyError") == {"keys": ["a", "b"],
                                                              "errors": ["KeyError"]}
    assert parse_meta("search\nintent: build a table") == {"intent": ["build a table"]}
    assert parse_meta("TODO: revisar esto") is None


def test_metadata_line_numbers_are_known() -> None:
    section = parse_sections(NOTE, "Pandas")[2]
    assert section.meta_lines == (12, 17)


# -------------------------------------------------------------- queries
@pytest.mark.parametrize(
    ("text", "mode"),
    [
        ("crear un dataframe", "words"),
        ("pd.read_csv('a.csv', sep=';')", "code"),
        ("for k, v in d.items():\n    print(k)", "code"),
        ("KeyError: 'edad'", "traceback"),
        ("Traceback (most recent call last):\n  File \"a.py\", line 1\nValueError: x", "traceback"),
        ("Dado un fichero de ventas calcula la media por ciudad y ordena el resultado", "text"),
    ],
)
def test_query_modes(text: str, mode: str) -> None:
    assert analyze(text).mode == mode


def test_natural_language_drops_stopwords_and_adds_synonyms() -> None:
    concepts = analyze("cómo crear un dataframe").concepts
    assert [c.head for c in concepts] == ["crear", "dataframe"]
    assert {"construir", "create"} <= set(concepts[0].terms)


def test_synonyms_match_by_stem() -> None:
    assert "create" in synonyms("creando")
    assert "create" not in synonyms("creer")
    assert synonyms("fotosintesis") == frozenset()


def test_code_features() -> None:
    analysis = analyze('import numpy as np\ndf = pd.read_csv("x.csv", sep=";")\ndf.groupby("col").sum()')
    labels = {c.label for c in analysis.concepts}
    assert {"pd.read_csv", "sep=", "numpy", "df.groupby", ".sum()", "col"} <= labels
    read_csv = next(c for c in analysis.concepts if c.label == "pd.read_csv")
    assert "read_csv" in read_csv.terms  # also found when the note calls it differently


def test_traceback_weighs_the_exception_most() -> None:
    analysis = analyze(
        'Traceback (most recent call last):\n  File "a.py", line 3, in cargar\n'
        '    df["edad"]\nKeyError: \'edad\''
    )
    assert analysis.concepts[0].head == "keyerror"
    assert {"edad", "cargar"} <= {c.head for c in analysis.concepts}


def test_code_terms_keep_identifiers_whole() -> None:
    assert code_terms("x = pd.read_csv(f)") == ["pd.read_csv", "read_csv"]
    assert "my_var" in code_terms("my_var = 1")


def test_code_line_heuristic() -> None:
    assert is_code_line("df = pd.DataFrame(data)")
    assert is_code_line("print(x)")
    assert not is_code_line("La media de las ventas por ciudad.")


# -------------------------------------------------------- ranking quality
@pytest.mark.parametrize("case", CASES, ids=[c["query"][:40] for c in CASES])
def test_ranking_regression(sections: SearchIndex, case: dict) -> None:
    keys = _keys(sections, case["query"], limit=10)
    assert case["expect"] in keys[: case["top"]], keys[:5]


def test_result_carries_section_details(sections: SearchIndex) -> None:
    results, analysis = sections.search_sections("crear un dataframe")
    best = results[0]
    assert analysis.mode == "words"
    assert best.crumbs == ["DataFrames en pandas", "Construcción de DataFrames"]
    assert best.line == 12 and best.level == 2
    assert {"keys", "aka"} & set(best.fields)
    assert best.snippet and all(not h.text.startswith(("<!--", "claves:")) for h in best.snippet)
    assert "dataframe" in best.terms


def test_snippet_shows_the_matching_code(sections: SearchIndex) -> None:
    results, _analysis = sections.search_sections("df.sort_values('ventas', ascending=False)")
    best = results[0]
    assert best.snippet_is_code
    text = " ".join(h.text for h in best.snippet)
    assert "sort_values" in text
    hit = next(h for h in best.snippet if h.spans)
    assert any("sort_values" in hit.text[s:e] or hit.text[s:e] == "ascending" for s, e in hit.spans)


# ---------------------------------------------------------------- filters
def test_filters(sections: SearchIndex) -> None:
    assert all(k.startswith("python/") for k in _keys(sections, "crear in:python"))
    assert all(k.startswith("pandas/") for k in _keys(sections, "crear", scope="pandas"))
    assert all(not k.startswith("pandas/") for k in _keys(sections, "crear -dataframe"))
    assert _keys(sections, "tag:biologia fases") == ["biologia/Mitosis.md#Fases"]
    assert _keys(sections, '"valores nulos"')[0] == "pandas/DataFrames.md#Valores nulos"


def test_colons_in_statements_are_not_filters(sections: SearchIndex) -> None:
    keys = _keys(sections, "Ejercicio 2: crear un dataframe")
    assert keys[0] == "pandas/DataFrames.md#Construcción de DataFrames"


def test_exact_mode_has_no_synonyms(sections: SearchIndex) -> None:
    assert "pandas/Combinar tablas.md#merge" not in _keys(sections, "juntar", exact=True)


def test_notes_without_metadata_still_work(sections: SearchIndex) -> None:
    assert _keys(sections, "items")[0] == "python/Diccionarios.md#Recorrer un diccionario"


# ------------------------------------------------------------ freshness
def test_changes_are_indexed_incrementally(vault_copy: Path) -> None:
    index = _index(vault_copy)
    note = vault_copy / "python" / "Tuplas.md"
    note.write_text("# Tuplas\n\n## Desempaquetar\n<!-- busca\nclaves: unpacking\n-->\nx\n",
                    encoding="utf-8")
    index.vault.refresh()
    assert index.refresh()
    assert _keys(index, "unpacking")[0] == "python/Tuplas.md#Desempaquetar"
    note.unlink()
    index.vault.refresh()
    index.refresh()
    assert _keys(index, "unpacking") == []


def test_unsaved_buffer_is_searched(vault_copy: Path) -> None:
    index = _index(vault_copy)
    path = (vault_copy / "python" / "Listas.md").resolve()
    index.update_text(path, "# Listas\n\n## Slicing\n\nlista[1:3] corta trozos\n")
    assert _keys(index, "slicing") == ["python/Listas.md#Slicing"]


# ------------------------------------------------------------------ cache
def test_cache_round_trip(vault_copy: Path, tmp_path: Path) -> None:
    cache = tmp_path / "cache.idx"
    index = _index(vault_copy)
    assert index.save(cache)
    vault = Vault(vault_copy)
    vault.refresh()
    loaded = SearchIndex(vault)
    assert loaded.load(cache)
    assert not loaded.refresh()  # nothing changed since it was saved
    for case in CASES[:8]:
        assert _keys(loaded, case["query"]) == _keys(index, case["query"])
    best = loaded.search_sections("crear un dataframe")[0][0]
    assert best.snippet  # text is read back from disk on demand


def test_cache_sees_changes_made_while_closed(vault_copy: Path, tmp_path: Path) -> None:
    cache = tmp_path / "cache.idx"
    _index(vault_copy).save(cache)
    note = vault_copy / "python" / "Listas.md"
    note.write_text("# Listas\n\n## Slicing\n\ntrozos\n", encoding="utf-8")
    os.utime(note, ns=(1, 1))
    vault = Vault(vault_copy)
    vault.refresh()
    loaded = SearchIndex(vault)
    assert loaded.load(cache)
    assert loaded.refresh()
    assert _keys(loaded, "slicing") == ["python/Listas.md#Slicing"]


def test_unsaved_buffers_are_not_trusted_from_the_cache(vault_copy: Path, tmp_path: Path) -> None:
    cache = tmp_path / "cache.idx"
    index = _index(vault_copy)
    index.update_text((vault_copy / "python" / "Listas.md").resolve(), "# Listas\n\nzanahoria\n")
    index.save(cache)
    vault = Vault(vault_copy)
    vault.refresh()
    loaded = SearchIndex(vault)
    assert loaded.load(cache)
    loaded.refresh()
    assert _keys(loaded, "zanahoria") == []


def test_bad_or_foreign_cache_is_ignored(vault_copy: Path, tmp_path: Path) -> None:
    cache = tmp_path / "cache.idx"
    cache.write_bytes(b"not marshal")
    vault = Vault(vault_copy)
    vault.refresh()
    assert not SearchIndex(vault).load(cache)
    other = _index(SECTION_VAULT)
    other.save(cache)
    assert not SearchIndex(vault).load(cache)  # written for another folder


# -------------------------------------------------------------------- cli
def test_cli_sections_json(capsys: pytest.CaptureFixture[str]) -> None:
    assert vault_main(["search", str(SECTION_VAULT), "crear", "un", "dataframe", "--json",
                       "--no-cache"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["mode"] == "words"
    best = data["results"][0]
    assert (best["note"], best["section"], best["line"]) == (
        "pandas/DataFrames.md", "Construcción de DataFrames", 12)
    assert best["path"] == ["DataFrames en pandas", "Construcción de DataFrames"]


def test_cli_reads_pasted_code_from_stdin(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    code = "resultado = pd.merge(a, b, on='id', how='left')\n"
    monkeypatch.setattr("sys.stdin", io.StringIO(code))
    assert vault_main(["search", str(SECTION_VAULT), "--stdin", "--json", "--no-cache"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["mode"] == "code"
    assert data["results"][0]["section"] == "merge"


def test_cli_text_output_and_no_match(capsys: pytest.CaptureFixture[str]) -> None:
    assert vault_main(["search", str(SECTION_VAULT), "lanzar", "excepción", "--no-cache"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("Excepciones › Lanzar excepciones con raise  python/Excepciones.md:")
    assert vault_main(["search", str(SECTION_VAULT), "zzzqqq", "--no-cache"]) == 1


def test_cli_writes_and_uses_the_cache(
    vault_copy: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "xdg"))
    assert vault_main(["search", str(vault_copy), "merge"]) == 0
    assert list((tmp_path / "xdg").rglob("*.idx"))
    assert vault_main(["search", str(vault_copy), "merge"]) == 0
    capsys.readouterr()
