---
title: Welcome to Emdee
author: Emdee
---

# Welcome to Emdee

**Emdee** is a Markdown editor for Linux: a plain-text editor on the left, a live
preview on the right, six themes and nothing else in the way.

This document exercises every Markdown feature Emdee understands, so it doubles
as a visual test bed. Try switching themes from the **Preferences** panel
(`F10`) and watch the editor, the chrome and this preview change together.

---

## Text formatting

Ordinary paragraphs flow naturally. You can make text **bold** with `Ctrl+B`,
*italic* with `Ctrl+I`, ***both at once***, ~~struck through~~ with
`Ctrl+Shift+X`, and mark things as `inline code` with ``Ctrl+` ``.

Line breaks inside a paragraph are collapsed,  
unless you end a line with two spaces.

Some Unicode to keep the font honest: αβγ · ñ · 日本語 · émoji 🎨 · ← → ⇒ ∀x∈ℝ.

Bare URLs are linked automatically: https://www.gnu.org/licenses/gpl-3.0.html

## Headings

### Third level

#### Fourth level

##### Fifth level

###### Sixth level

Every heading gets an anchor — hover one and click the `#` that appears.

## Lists

### Unordered

- Coffee
- Tea
  - Green
  - Black
    - Assam
- Water

### Ordered

1. Open a folder with `Ctrl+Shift+O`
2. Pick a file in the explorer
3. Start typing
   1. The preview follows along
   2. Scrolling stays in sync

### Tasks

- [x] Port the PyDracula shell to PyQt6
- [x] Generate every theme from one set of tokens
- [x] Two-way scroll synchronisation
- [ ] Convince you to star the repository

### Definition list

Markdown
: A plain-text formatting syntax that stays readable as source.

CommonMark
: The specification Emdee follows, plus the GitHub extensions above.

## Quotes

> The best thing about a boolean is that even if you are wrong, you are only off
> by a bit.
>
> > Nested quotes work too.

## Code

Inline `git commit --amend`, and fenced blocks with syntax highlighting:

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class Palette:
    """A theme expressed purely as colour tokens."""

    name: str
    accent: str

    def is_readable_on(self, background: str) -> bool:
        return contrast_ratio(self.accent, background) >= 4.5
```

```bash
# Install the desktop entry and the icon theme
./packaging/install.sh
```

```json
{
  "theme": "dracula",
  "view_mode": "split",
  "editor_font_size": 14
}
```

A block with no language tag keeps its plain monospace shape:

```
$ emdee ~/notes/README.md
```

## Tables

| Shortcut       | Action                | Works on selection |
| -------------- | --------------------- | :----------------: |
| `Ctrl+B`       | Bold                  |         yes        |
| `Ctrl+I`       | Italic                |         yes        |
| `Ctrl+K`       | Insert link           |         yes        |
| `Ctrl+Shift+K` | Fenced code block     |         yes        |
| `Ctrl+1` … `6` | Heading level         |         yes        |

Alignment is honoured:

| Left | Centre | Right |
| :--- | :----: | ----: |
| a    |   b    |     c |
| 1    |   22   |   333 |

## Links and images

An [external link](https://commonmark.org), a [link to a heading](#tables) and a
relative link to [the project README](README.md) — clicking that one opens the
file in the editor instead of a browser.

![The Emdee application icon](app/resources/icons/app/logo.svg)

Pasting an image from the clipboard writes it into an `assets/` folder next to
the document and inserts the reference for you.

## Horizontal rules

---

## Footnotes

Emdee renders footnotes[^why] at the bottom of the document, with a link back to
where they were referenced[^second].

[^why]: Because long-form notes deserve them.
[^second]: And because it proves the plugin is wired up.

## Raw HTML

Inline HTML is allowed, which is handy for the occasional
<kbd>Ctrl</kbd> + <kbd>S</kbd> or a <mark>highlighted phrase</mark>.

It is not passed through blindly, though: every document goes through an
allow-list sanitiser first, so `<script>`, `<iframe>`, `on*` handlers and
`javascript:` links are stripped before the preview ever sees them — and before
they could reach anyone you send an HTML export to.

<figure>
  <img src="app/resources/icons/app/logo-mono.svg" width="96" alt="Monochrome logo">
  <figcaption>The monochrome variant, used for the exported favicon.</figcaption>
</figure>

---

## Where to go next

| I want to…                | Do this                                    |
| ------------------------- | ------------------------------------------ |
| Change the theme          | `F10`, then pick a swatch                  |
| Hide the preview          | `Ctrl+Shift+1`                             |
| Search with a regex       | `Ctrl+F`, then toggle `.*`                 |
| Publish this as a webpage | `Ctrl+Shift+E` — one self-contained file   |
| Print it                  | `Ctrl+P` — PDF with the current theme      |

Happy writing.
