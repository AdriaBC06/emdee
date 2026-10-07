# SPDX-License-Identifier: GPL-3.0-or-later
"""The index of linked notes behind the graph view and ``emdee vault``."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.cli import main as vault_main
from app.core.renderer import MarkdownRenderer, WikiTarget
from app.core.vault import Link, Vault, extract_links, note_title, parse_front_matter


def _write(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture
def vault(tmp_path: Path) -> Vault:
    _write(tmp_path, "Index.md", "# Index\n\n- [[Cell]]\n- [[Mitosis#Phases|phases]]\n- [[Missing]]\n")
    _write(tmp_path, "bio/Cell.md", "# Cell\n\nHolds [[DNA]] and divides by [[mitosis]].\n")
    _write(tmp_path, "bio/Mitosis.md", "---\naliases: [cell division]\n---\n# Mitosis\n\nSee [Cell](Cell.md).\n")
    _write(tmp_path, "DNA.md", "# DNA\n")
    _write(tmp_path, "Lonely.md", "# Lonely\n")
    _write(tmp_path, "AGENTS.md", "[[Nothing]]\n")
    v = Vault(tmp_path)
    v.refresh()
    return v


# ---------------------------------------------------------------- parsing
def test_extract_links_forms() -> None:
    links = extract_links("a [[A]] b [[B#H|shown]] c ![[img.png]] d [x](c.md#top) [w](https://x.org)")
    assert [(lk.target, lk.heading, lk.alias, lk.embed, lk.kind) for lk in links] == [
        ("A", "", "", False, "wiki"),
        ("B", "H", "shown", False, "wiki"),
        ("img.png", "", "", True, "wiki"),
        ("c.md", "top", "", False, "markdown"),
    ]


def test_links_in_code_are_ignored() -> None:
    text = "```\n[[Fenced]]\n```\n\n`[[Inline]]` and [[Real]]\n\n    [[Indented]]\n"
    assert [lk.target for lk in extract_links(text)] == ["Real"]


def test_link_lines_count_front_matter() -> None:
    links = extract_links("---\ntitle: X\n---\n\n[[A]]\n")
    assert links[0].line == 5


def test_front_matter_subset() -> None:
    data, lines = parse_front_matter("---\ntitle: 'Hi'\ntags: [a, b]\naliases:\n  - one\n  - two\n---\nbody")
    assert data == {"title": "Hi", "tags": ["a", "b"], "aliases": ["one", "two"]}
    assert lines == 7


def test_title_precedence(tmp_path: Path) -> None:
    assert note_title("---\ntitle: FM\n---\n# H1\n", tmp_path / "f.md") == "FM"
    assert note_title("intro\n# H1\n", tmp_path / "f.md") == "H1"
    assert note_title("no heading\n", tmp_path / "file.md") == "file"


# --------------------------------------------------------------- resolving
def test_resolution_by_name_case_and_alias(vault: Vault) -> None:
    root = vault.root
    assert vault.resolve("cell") == root / "bio/Cell.md"
    assert vault.resolve("bio/Mitosis") == root / "bio/Mitosis.md"
    assert vault.resolve("Cell Division") == root / "bio/Mitosis.md"
    assert vault.resolve(Link("Cell.md", kind="markdown"), root / "bio/Mitosis.md") == root / "bio/Cell.md"
    assert vault.resolve("Missing") is None


def test_nearest_note_wins_on_duplicate_names(tmp_path: Path) -> None:
    _write(tmp_path, "a/Topic.md", "")
    _write(tmp_path, "b/Topic.md", "")
    src = _write(tmp_path, "b/Note.md", "[[Topic]]")
    v = Vault(tmp_path)
    v.refresh()
    assert v.resolve("Topic", src.resolve()) == v.root / "b/Topic.md"


def test_agent_instruction_files_are_not_notes(vault: Vault) -> None:
    assert not any(p.name == "AGENTS.md" for p in vault.notes)


def test_graph_queries(vault: Vault) -> None:
    root = vault.root
    assert sorted(vault.rel(s) for s, _ in vault.backlinks(root / "bio/Cell.md")) == [
        "Index.md", "bio/Mitosis.md",
    ]
    assert [(vault.rel(s), lk.target) for s, lk in vault.broken_links()] == [("Index.md", "Missing")]
    assert vault.orphans() == [root / "Lonely.md"]
    assert root / "DNA.md" in vault.neighbourhood(root / "bio/Cell.md", 1)
    assert root / "DNA.md" not in vault.neighbourhood(root / "Index.md", 1)


def test_refresh_is_incremental(vault: Vault) -> None:
    assert vault.refresh() is False
    _write(vault.root, "New.md", "[[DNA]]")
    assert vault.refresh() is True
    assert vault.root / "New.md" in vault.notes
    (vault.root / "New.md").unlink()
    assert vault.refresh() is True
    assert vault.root / "New.md" not in vault.notes


def test_update_text_reports_link_changes(vault: Vault) -> None:
    path = vault.root / "DNA.md"
    assert vault.update_text(path, "# DNA\n\nmore words\n") is False
    assert vault.update_text(path, "# DNA\n\n[[Cell]]\n") is True
    assert vault.resolve("Cell") in {t for s, t in vault.edges() if s == path}


def test_new_note_path_stays_inside(vault: Vault) -> None:
    src = vault.root / "bio/Cell.md"
    assert vault.new_note_path("Ribosome", src) == vault.root / "bio/Ribosome.md"
    assert vault.new_note_path("topics/Ribosome", src) == vault.root / "topics/Ribosome.md"
    assert vault.new_note_path("../../etc/passwd", src).is_relative_to(vault.root)


def test_json_export(vault: Vault) -> None:
    data = json.loads(vault.to_json())
    ids = {n["id"]: n for n in data["nodes"]}
    assert ids["Missing"]["exists"] is False
    assert {"source": "Index.md", "target": "bio/Cell.md"} in data["edges"]


# ---------------------------------------------------------------- renderer
def test_renderer_wikilinks_default() -> None:
    html = MarkdownRenderer().render_html("[[My Note|shown]] and [[Other#Some Part]]")
    assert '<a class="wikilink" href="My%20Note.md"' in html and ">shown</a>" in html
    assert 'href="Other.md#some-part"' in html


def test_renderer_uses_resolver_and_marks_missing() -> None:
    renderer = MarkdownRenderer()
    renderer.wiki_resolver = lambda link: WikiTarget(href=f"x/{link.target}.md", exists=False)
    html = renderer.render_html("[[Gone]]")
    assert 'class="wikilink is-missing"' in html and 'href="x/Gone.md"' in html


def test_renderer_leaves_code_alone() -> None:
    html = MarkdownRenderer().render_html("`[[code]]`")
    assert "wikilink" not in html


def test_renderer_wikilink_cannot_inject_markup() -> None:
    html = MarkdownRenderer().render_html('[[a"><script>alert(1)</script>]]')
    assert "<script" not in html


# --------------------------------------------------------------------- cli
def test_cli_check_exit_codes(vault: Vault, capsys: pytest.CaptureFixture[str]) -> None:
    assert vault_main(["check", str(vault.root)]) == 1
    assert "[[Missing]]" in capsys.readouterr().out
    (vault.root / "Index.md").write_text("[[Cell]] [[Lonely]]", encoding="utf-8")
    assert vault_main(["check", str(vault.root), "--strict"]) == 0


def test_cli_init_does_not_overwrite(tmp_path: Path) -> None:
    (tmp_path / "CLAUDE.md").write_text("mine", encoding="utf-8")
    assert vault_main(["init", str(tmp_path)]) == 0
    assert (tmp_path / "CLAUDE.md").read_text(encoding="utf-8") == "mine"
    assert "[[" in (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
