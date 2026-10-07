# SPDX-License-Identifier: GPL-3.0-or-later
"""``emdee vault …`` — the folder of linked notes, from the command line.

Built for AI agents and scripts as much as for people: an agent that writes
study notes can run ``emdee vault check`` to prove every ``[[link]]`` resolves,
``emdee vault graph --format json`` to see the concept map it has built, and
``emdee vault backlinks`` to find what already points at a note before editing
it.  Nothing here imports Qt, so it runs on a machine with no display.

Exit status: 0 on success, 1 when ``check`` finds broken links (or orphans
with ``--strict``), 2 on usage errors.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import APP_ID, APP_NAME
from .core.vault import Vault

__all__ = ["main", "AGENTS_TEMPLATE"]

#: Written by ``emdee vault init``: the conventions an agent should follow so
#: that what it generates shows up connected in Emdee's graph.
AGENTS_TEMPLATE = """\
# Notes for AI agents working in this folder

This folder is an **Emdee vault**: Markdown notes connected with wiki links.
Emdee draws them as a graph (Ctrl+G) and shows each note's backlinks, so the
links you write *are* the concept map. Follow these conventions.

## Writing notes

- **One concept per note.** File name = concept name, e.g. `Mitosis.md`,
  `Teorema de Pitágoras.md`. Spaces and accents are fine.
- Start each note with a `# Title` and, optionally, front matter:

  ```yaml
  ---
  title: Mitosis
  tags: [biologia, tema-3]
  aliases: [división celular]
  ---
  ```

- **Link every concept you mention** that has (or deserves) its own note:
  `[[Mitosis]]`, `[[Mitosis|texto visible]]`, `[[Mitosis#Fases]]`.
  A link to a note that does not exist yet is allowed — it appears as a
  dashed "pending" node — but prefer creating the note.
- Links resolve by file name anywhere in the folder, so `[[Mitosis]]` works
  from any subfolder. Use `[[carpeta/Nota]]` only to disambiguate.
- Images: put them in `assets/` and embed with `![[assets/figura.png]]` or
  `![alt](assets/figura.png)`.
- Code in fenced blocks (```` ```python ````); links inside code are ignored.

## Structure for a course or tutorial

- An index note per subject or unit (e.g. `Índice Biología.md`) that links
  every concept note in a sensible study order — a "map of content".
- Concept notes link to prerequisites ("Antes de esto: [[Célula]]") and to
  related concepts, and end with a short `## Repaso` (key points, questions).
- Tutorials: numbered steps, runnable examples, and links to the concept
  notes that explain the theory behind each step.

## Checking your work

Run after generating or editing notes, and fix what it reports:

```sh
emdee vault check .            # broken [[links]] and orphan notes
emdee vault graph . --format json   # nodes and edges of the concept map
emdee vault backlinks . "Mitosis"   # who links to a note
```

(From a source checkout: `python -m app.cli vault …` in the Emdee folder.)
"""

CLAUDE_TEMPLATE = "@AGENTS.md\n"


def _vault(folder: str) -> Vault:
    root = Path(folder).expanduser()
    if not root.is_dir():
        raise SystemExit(f"{APP_ID}: not a folder: {root}")
    vault = Vault(root)
    vault.refresh()
    return vault


def _cmd_check(args: argparse.Namespace) -> int:
    vault = _vault(args.folder)
    broken = vault.broken_links()
    orphans = vault.orphans()
    if args.json:
        print(json.dumps(
            {
                "notes": len(vault.notes),
                "links": len(vault.edges()),
                "broken": [
                    {"note": vault.rel(src), "line": link.line, "target": link.target}
                    for src, link in broken
                ],
                "orphans": [vault.rel(p) for p in orphans],
            },
            ensure_ascii=False,
            indent=2,
        ))
    else:
        print(f"{len(vault.notes)} notes, {len(vault.edges())} links in {vault.root}")
        if broken:
            print(f"\n{len(broken)} broken link(s) — target note does not exist:")
            for src, link in broken:
                print(f"  {vault.rel(src)}:{link.line}  [[{link.target}]]")
        if orphans:
            print(f"\n{len(orphans)} orphan note(s) — no links in or out:")
            for path in orphans:
                print(f"  {vault.rel(path)}")
        if not broken and not orphans:
            print("All links resolve and every note is connected.")
        if vault.truncated:
            print("\nwarning: stopped indexing at the note limit", file=sys.stderr)
    return 1 if broken or (args.strict and orphans) else 0


def _cmd_graph(args: argparse.Namespace) -> int:
    vault = _vault(args.folder)
    if args.format == "json":
        print(vault.to_json())
    elif args.format == "dot":
        print(vault.to_dot(), end="")
    else:
        print(vault.to_mermaid(), end="")
    return 0


def _find(vault: Vault, query: str) -> Path:
    path = vault.find(query)
    if path is None:
        raise SystemExit(f"{APP_ID}: no note matches {query!r}")
    return path


def _cmd_backlinks(args: argparse.Namespace) -> int:
    vault = _vault(args.folder)
    target = _find(vault, args.note)
    rows = vault.backlinks(target)
    if args.json:
        print(json.dumps(
            [{"note": vault.rel(s), "line": link.line} for s, link in rows],
            ensure_ascii=False, indent=2,
        ))
    else:
        for source, link in rows:
            print(f"{vault.rel(source)}:{link.line}")
    return 0


def _cmd_links(args: argparse.Namespace) -> int:
    vault = _vault(args.folder)
    source = _find(vault, args.note)
    rows = vault.outgoing(source)
    if args.json:
        print(json.dumps(
            [
                {"target": link.target, "line": link.line,
                 "resolved": vault.rel(path) if path else None}
                for link, path in rows
            ],
            ensure_ascii=False, indent=2,
        ))
    else:
        for link, path in rows:
            where = vault.rel(path) if path else "(missing)"
            print(f"{link.line}: [[{link.target}]] -> {where}")
    return 0


def _cmd_init(args: argparse.Namespace) -> int:
    root = Path(args.folder).expanduser()
    root.mkdir(parents=True, exist_ok=True)
    for name, content in (("AGENTS.md", AGENTS_TEMPLATE), ("CLAUDE.md", CLAUDE_TEMPLATE)):
        target = root / name
        if target.exists() and not args.force:
            print(f"kept existing {target}")
            continue
        target.write_text(content, encoding="utf-8")
        print(f"wrote {target}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=f"{APP_ID} vault",
        description=f"{APP_NAME}: inspect a folder of [[linked]] Markdown notes.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser("check", help="report broken links and orphan notes")
    check.add_argument("folder", nargs="?", default=".")
    check.add_argument("--json", action="store_true", help="machine-readable output")
    check.add_argument("--strict", action="store_true", help="orphans also fail the check")
    check.set_defaults(func=_cmd_check)

    graph = sub.add_parser("graph", help="print the note graph")
    graph.add_argument("folder", nargs="?", default=".")
    graph.add_argument("--format", choices=("json", "dot", "mermaid"), default="json")
    graph.set_defaults(func=_cmd_graph)

    back = sub.add_parser("backlinks", help="notes that link to NOTE")
    back.add_argument("folder")
    back.add_argument("note", help="note name or path")
    back.add_argument("--json", action="store_true")
    back.set_defaults(func=_cmd_backlinks)

    links = sub.add_parser("links", help="outgoing links of NOTE and where they resolve")
    links.add_argument("folder")
    links.add_argument("note", help="note name or path")
    links.add_argument("--json", action="store_true")
    links.set_defaults(func=_cmd_links)

    init = sub.add_parser("init", help="write AGENTS.md / CLAUDE.md with note conventions")
    init.add_argument("folder", nargs="?", default=".")
    init.add_argument("--force", action="store_true", help="overwrite existing files")
    init.set_defaults(func=_cmd_init)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Entry point for ``emdee vault …`` (``argv`` excludes the word ``vault``)."""
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    # ``python -m app.cli vault check …`` and ``python -m app.cli check …``
    # both work.
    argv = sys.argv[1:]
    if argv[:1] == ["vault"]:
        argv = argv[1:]
    raise SystemExit(main(argv))
