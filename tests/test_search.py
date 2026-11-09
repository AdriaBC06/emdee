# SPDX-License-Identifier: GPL-3.0-or-later
"""Vault-wide search: word forms, filters, scope and incremental indexing."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from app.cli import main as vault_main
from app.core.search import SearchIndex, normalize, parse_query, stem
from app.core.vault import Vault


def _write(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture
def root(tmp_path: Path) -> Path:
    _write(
        tmp_path,
        "historia/tema1/Estado.md",
        "---\ntags: [examen, historia]\nasignatura: Historia\n---\n"
        "# La forma del estado\n\nLas formas de gobierno se fueron formando.\n"
        "La creación del estado moderno.\n",
    )
    _write(tmp_path, "arte/Arte.md", "# Arte\n\nCrear una obra. Creando arte. Formación artística.\n")
    _write(tmp_path, "Biologia.md", "# Célula\n\nLa división celular ocurre en la mitosis. #examen\n")
    return tmp_path


@pytest.fixture
def index(root: Path) -> SearchIndex:
    vault = Vault(root)
    vault.refresh()
    idx = SearchIndex(vault)
    idx.refresh()
    return idx


def _notes(index: SearchIndex, query: str, **kwargs) -> list[str]:
    return [r.rel for r in index.search(query, **kwargs)]


def _surface(index: SearchIndex, query: str) -> set[str]:
    return {word for r in index.search(query) for word in r.surface}


# ------------------------------------------------------------- word forms
def test_normalize_ignores_case_and_accents_but_keeps_enye() -> None:
    assert normalize("Creación") == "creacion"
    assert normalize("AÑO") == "año"


@pytest.mark.parametrize(
    ("a", "b"),
    [("crear", "creacion"), ("crear", "creando"), ("crear", "creado"), ("forma", "formas"),
     ("forma", "formacion"), ("forma", "formando")],
)
def test_stem_groups_word_families(a: str, b: str) -> None:
    assert stem(a) == stem(b)


def test_stem_keeps_short_words_whole() -> None:
    assert stem("mes") == "mes"
    assert stem("crecer") != stem("crear")


def test_prefix_and_stem_matches(index: SearchIndex) -> None:
    assert {"forma", "formas", "formando", "Formación"} <= _surface(index, "forma")
    assert {"Crear", "Creando", "creación"} <= _surface(index, "crear")


def test_accents_are_optional(index: SearchIndex) -> None:
    assert _notes(index, "celula") == ["Biologia.md"]
    assert _notes(index, "división") == _notes(index, "division")


def test_long_stems_reach_derived_words(root: Path) -> None:
    _write(root, "Obra.md", "# Obra\n\nLa construcción del puente.\n")
    vault = Vault(root)
    vault.refresh()
    idx = SearchIndex(vault)
    idx.refresh()
    assert "Obra.md" in _notes(idx, "construir")


def test_typos_are_forgiven_for_longer_words(index: SearchIndex) -> None:
    assert "historia/tema1/Estado.md" in _notes(index, "froma")
    assert _notes(index, "mitosys") == ["Biologia.md"]


def test_exact_mode_only_matches_the_word(index: SearchIndex) -> None:
    assert _surface(index, "forma") != {"forma"}
    words = {w for r in index.search("forma", exact=True) for w in r.surface}
    assert words == {"forma"}


def test_title_matches_rank_first(index: SearchIndex) -> None:
    assert _notes(index, "forma")[0] == "historia/tema1/Estado.md"


# --------------------------------------------------------------- syntax
def test_parse_query() -> None:
    q = parse_query('mitosis "división celular" -meiosis tag:examen in:bio/t1 title:cel curso:2')
    assert q.words == ["mitosis"]
    assert q.phrases == [["division", "celular"]]
    assert q.exclude_words == ["meiosis"]
    assert q.tags == ["examen"]
    assert q.folders == ["bio/t1"]
    assert q.title_words == ["cel"]
    assert q.fields == [("curso", "2", False)]


def test_all_words_must_appear(index: SearchIndex) -> None:
    assert _notes(index, "forma gobierno") == ["historia/tema1/Estado.md"]
    assert _notes(index, "forma mitosis") == []


def test_phrase(index: SearchIndex) -> None:
    assert _notes(index, '"division celular"') == ["Biologia.md"]
    assert _notes(index, '"celular division"') == []
    assert _notes(index, '"division celul"') == ["Biologia.md"]  # last word is a prefix


def test_exclusion(index: SearchIndex) -> None:
    assert _notes(index, "forma -arte") == ["historia/tema1/Estado.md"]


def test_tags_front_matter_and_inline(index: SearchIndex) -> None:
    assert sorted(_notes(index, "tag:examen")) == ["Biologia.md", "historia/tema1/Estado.md"]
    assert _notes(index, "#hist") == ["historia/tema1/Estado.md"]
    assert _notes(index, "tag:examen -tag:historia") == ["Biologia.md"]


def test_front_matter_field(index: SearchIndex) -> None:
    assert _notes(index, "asignatura:histo") == ["historia/tema1/Estado.md"]
    assert _notes(index, "asignatura:arte") == []


def test_title_filter(index: SearchIndex) -> None:
    assert _notes(index, "title:celula") == ["Biologia.md"]


# ---------------------------------------------------------------- scope
def test_scope_and_in_filter(index: SearchIndex) -> None:
    assert _notes(index, "crear", scope="historia") == ["historia/tema1/Estado.md"]
    assert _notes(index, "crear in:arte") == ["arte/Arte.md"]
    assert _notes(index, "crear in:tema1") == ["historia/tema1/Estado.md"]


def test_folders(index: SearchIndex) -> None:
    assert index.folders() == ["arte", "historia", "historia/tema1"]


# -------------------------------------------------------------- results
def test_line_hits_point_at_the_words(index: SearchIndex) -> None:
    result = index.search("formando")[0]
    hit = next(h for h in result.lines if h.line == 7)
    assert [hit.text[s:e] for s, e in hit.spans] == ["formas", "formando"]
    text = result.path.read_text(encoding="utf-8")
    assert all(text[s:e].lower().startswith("form") for s, e in result.ranges)


def test_highlight_ranges_on_live_text(index: SearchIndex) -> None:
    text = "Una forma nueva, sin formatos. Formulario."
    words = [text[s:e] for s, e in index.highlight_ranges(text, "forma")]
    assert "forma" in words
    assert "Formulario" not in words


# ------------------------------------------------------------ freshness
def test_refresh_picks_up_changes(root: Path, index: SearchIndex) -> None:
    note = _write(root, "Nueva.md", "# Nueva\n\nfotosíntesis\n")
    index.vault.refresh()
    assert index.refresh()
    assert _notes(index, "fotosintesis") == ["Nueva.md"]
    note.write_text("# Nueva\n\nrespiración\n", encoding="utf-8")
    os.utime(note, ns=(1, 1))
    index.vault.refresh()
    index.refresh()
    assert _notes(index, "fotosintesis") == []
    note.unlink()
    index.vault.refresh()
    index.refresh()
    assert _notes(index, "respiracion") == []


def test_unsaved_buffer_is_searchable(root: Path, index: SearchIndex) -> None:
    path = (root / "Biologia.md").resolve()
    index.update_text(path, "# Célula\n\nribosoma\n")
    assert _notes(index, "ribosoma") == ["Biologia.md"]
    assert _notes(index, "mitosis") == []


# ------------------------------------------------------------------ cli
def test_cli_search_notes_json(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert vault_main(["search", str(root), "crear", "--notes", "--json", "--no-cache"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert {row["note"] for row in data} == {"arte/Arte.md", "historia/tema1/Estado.md"}
    assert vault_main(["search", str(root), "inexistente", "--notes", "--no-cache"]) == 1


# ------------------------------------------------------------ title search
@pytest.fixture
def titled(tmp_path: Path) -> SearchIndex:
    _write(tmp_path, "pandas/DataFrames.md",
           "# DataFrames\n\nTexto.\n\n## Creación de DataFrames\n\nCon `pd.DataFrame`.\n\n"
           "### Ejemplo\n\nCrear una tabla.\n\n```python\n# Creación en código\n```\n")
    _write(tmp_path, "pandas/Merge.md", "# Combinar tablas\n\n## merge\n\nCrear una tabla nueva.\n")
    _write(tmp_path, "biologia/Mitosis.md", "---\ntags: [biologia]\n---\n# Fases\n\nProfase.\n")
    vault = Vault(tmp_path)
    vault.refresh()
    idx = SearchIndex(vault)
    idx.refresh()
    return idx


def _titles(index: SearchIndex, query: str, **kwargs) -> list[str]:
    return [r.rel for r in index.search_titles(query, **kwargs)]


def test_title_search_matches_headings_not_body(titled: SearchIndex) -> None:
    assert _titles(titled, "creación") == ["pandas/DataFrames.md"]
    assert _titles(titled, "tabla") == ["pandas/Merge.md"]  # "una tabla" in bodies does not count
    result = titled.search_titles("crear")[0]
    assert [(h.level, h.text) for h in result.headings] == [(2, "Creación de DataFrames")]


def test_title_search_ignores_headings_in_code(titled: SearchIndex) -> None:
    headings = titled.search_titles("codigo")
    assert headings == []


def test_title_search_uses_file_name_and_word_starts(titled: SearchIndex) -> None:
    assert _titles(titled, "mitos") == ["biologia/Mitosis.md"]
    assert _titles(titled, "datafr") == ["pandas/DataFrames.md"]
    result = titled.search_titles("dataframes")[0]
    assert result.title_spans  # the note title is marked too


def test_title_search_needs_every_word(titled: SearchIndex) -> None:
    assert _titles(titled, "combinar merge") == ["pandas/Merge.md"]
    assert _titles(titled, "combinar fases") == []
    assert _titles(titled, '"combinar tablas"') == ["pandas/Merge.md"]
    assert _titles(titled, "tablas -merge") == []


def test_title_search_scope_and_filters(titled: SearchIndex) -> None:
    assert sorted(_titles(titled, "", scope="pandas")) == ["pandas/DataFrames.md", "pandas/Merge.md"]
    assert _titles(titled, "") == []
    assert _titles(titled, "tag:biologia") == ["biologia/Mitosis.md"]
    assert _titles(titled, "fases", scope="pandas") == []


def test_title_ranges_mark_headings_only(titled: SearchIndex) -> None:
    text = "# Crear tablas\n\nCrear algo.\n\n## Otra\n"
    assert titled.title_ranges(text, "crear") == [(2, 7)]
