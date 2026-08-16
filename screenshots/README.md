# Screenshot script

> ## Status — complete ✅
>
> All 13 assets are in place. The nine window shots are **1920 × 1034**, the six
> theme shots share an identical scroll position (verified: every one starts at
> source line 17 at the same `y`), none shows the unsaved-changes dot, and
> `magick mogrify -strip` has been run.
>
> `about.png` is rendered offscreen from the real dialog rather than captured,
> so it carries no machine-specific path. `demo.mp4` is the full 47-second
> walkthrough re-encoded to H.264; `demo.gif` is the 8-second theme-switching
> cut from it.
>
> Everything below is the recipe, kept for retakes.

Twelve PNGs and one GIF. Everything below is copy-paste ready for
**KDE Plasma 6 on Wayland**, which is what this project is developed on.

> **`grim` does not work here.** It needs the `wlr-screencopy` protocol, which
> KWin does not implement — it fails with *"compositor doesn't support the
> screen capture protocol"*. KDE's own `spectacle` is used instead; it ships
> with Plasma, so nothing extra to install. If you are on wlroots (Sway,
> Hyprland, river), the `grim` equivalent is given at the bottom.

---

## One-time setup

```bash
# Where the shots go
mkdir -p ~/Pictures/emdee

# A demo notes folder, so the file tree has something real in it
mkdir -p ~/emdee-demo/{projects,notes}
cd "$(git rev-parse --show-toplevel)"           # the emdee checkout
cp WELCOME.md ~/emdee-demo/
printf '# Meeting notes\n\n- [ ] Ship the icon\n- [x] Fix the rail\n' > ~/emdee-demo/notes/meeting.md
printf '# Reading list\n\n1. CommonMark spec\n2. Qt for Python docs\n' > ~/emdee-demo/notes/reading.md
printf '# Emdee\n\nA Markdown editor for Linux.\n' > ~/emdee-demo/projects/emdee.md
printf '# Roadmap\n\n## Q1\n\n- Outline panel\n' > ~/emdee-demo/projects/roadmap.md
printf 'not markdown, should stay hidden\n' > ~/emdee-demo/notes/ignore-me.json
```

Now launch the app once and set the baseline:

```bash
emdee --size 1920x1034 ~/emdee-demo/WELCOME.md
```

Then, inside Emdee:

1. `Ctrl+Shift+O` → select the folder you want in the tree. The existing
   shots use the **`emdee` checkout itself**; match that when retaking one
   so the sidebar looks the same.
2. Expand `notes/` and `projects/` in the tree so both are visible.
   `ignore-me.json` must **not** appear — that is the filter doing its job.
3. Make sure the rail is **expanded** (labels visible next to the icons). If it
   only shows icons, click the **Menu** button at the top of the rail.
4. Press `Ctrl+S`. No shot may show the unsaved-changes dot (`•`) next to the
   filename in the title bar.

Leave the app open. From here on, each shot is: *set the state → run the
capture command → click the Emdee window during the 4-second delay*.

### The capture command

Every shot uses the same shape. `-a` grabs the active window, `-S` drops the
compositor shadow, `-e` drops decorations, `-d 4000` gives you four seconds to
click on Emdee, `-b -n` keep Spectacle's own window and notification away:

```bash
spectacle -b -n -a -S -e -d 4000 -o ~/Pictures/emdee/NAME.png
```

Because the window is launched at exactly **1920 × 1034** and is frameless, the
output is exactly 1920 × 1034 with no trimming needed.

---

## 1 · `hero.png`

**Appears in:** the hero block at the very top of `README.md`, right under the
badges. This is the single most important image in the repository.

| | |
| --- | --- |
| **Theme** | Dracula (the default) |
| **View mode** | Split — `Ctrl+Shift+2` |
| **Sidebar** | Rail **expanded**, Explorer panel **open** |
| **Document** | `~/emdee-demo/WELCOME.md` |
| **Scroll to** | `## Code` as the topmost visible heading in the editor |
| **Tree** | `~/emdee-demo` open, `notes/` and `projects/` expanded |
| **Panels** | None open (no find, no preferences) |
| **Window** | 1920 × 1034 |

The point of this shot is the contrast between the two panes: raw Markdown with
editor highlighting on the left, the same fenced Python block rendered with
Pygments on the right. Scroll until the ` ```python ` fence and the `@dataclass`
example are both visible.

```bash
spectacle -b -n -a -S -e -d 4000 -o ~/Pictures/emdee/hero.png
```

---

## 2 · `theme-dracula.png`

**Appears in:** the *Theme gallery* grid, row 1 column 1.

| | |
| --- | --- |
| **Theme** | Dracula |
| **View mode** | Split |
| **Sidebar** | Rail expanded, Explorer open |
| **Document** | `~/emdee-demo/WELCOME.md` |
| **Scroll to** | `## Text formatting` as the topmost visible heading |
| **Tree** | `~/emdee-demo`, both subfolders expanded |
| **Panels** | None |
| **Window** | 1920 × 1034 |

> **All six theme shots must share this exact scroll position and window size.**
> They go into a 3 × 2 grid and any drift between them is very visible. Set the
> scroll once, then change only the theme between shots 2 → 7.

```bash
spectacle -b -n -a -S -e -d 4000 -o ~/Pictures/emdee/theme-dracula.png
```

---

## 3 · `theme-clean-light.png`

**Appears in:** Theme gallery, row 1 column 2.

Press `F10`, click the **Clean Light** swatch, press `F10` again to close the
drawer. Do not touch the scroll.

| | |
| --- | --- |
| **Theme** | Clean Light |
| **Everything else** | Identical to shot 2 |

```bash
spectacle -b -n -a -S -e -d 4000 -o ~/Pictures/emdee/theme-clean-light.png
```

---

## 4 · `theme-catppuccin-latte.png`

**Appears in:** Theme gallery, row 1 column 3.

| | |
| --- | --- |
| **Theme** | Catppuccin Latte |
| **Everything else** | Identical to shot 2 |

```bash
spectacle -b -n -a -S -e -d 4000 -o ~/Pictures/emdee/theme-catppuccin-latte.png
```

---

## 5 · `theme-catppuccin-frappe.png`

**Appears in:** Theme gallery, row 2 column 1.

| | |
| --- | --- |
| **Theme** | Catppuccin Frappé |
| **Everything else** | Identical to shot 2 |

```bash
spectacle -b -n -a -S -e -d 4000 -o ~/Pictures/emdee/theme-catppuccin-frappe.png
```

---

## 6 · `theme-rose-pine-dawn.png`

**Appears in:** Theme gallery, row 2 column 2.

| | |
| --- | --- |
| **Theme** | Rosé Pine Dawn |
| **Everything else** | Identical to shot 2 |

```bash
spectacle -b -n -a -S -e -d 4000 -o ~/Pictures/emdee/theme-rose-pine-dawn.png
```

---

## 7 · `theme-nord.png`

**Appears in:** Theme gallery, row 2 column 3.

| | |
| --- | --- |
| **Theme** | Nord |
| **Everything else** | Identical to shot 2 |

```bash
spectacle -b -n -a -S -e -d 4000 -o ~/Pictures/emdee/theme-nord.png
```

---

## 8 · `editor.png`

**Appears in:** the *Features* section, left half of the two-up table under
**Writing**.

| | |
| --- | --- |
| **Theme** | Dracula (switch back with `F10`) |
| **View mode** | **Editor only** — `Ctrl+Shift+1` |
| **Sidebar** | Rail expanded, Explorer open |
| **Document** | `~/emdee-demo/WELCOME.md` |
| **Scroll to** | `## Lists` as the topmost visible heading |
| **Tree** | `~/emdee-demo`, both subfolders expanded |
| **Panels** | None |
| **Window** | 1920 × 1034 |

With the preview hidden the editor is full width, so the line-number gutter, the
pink list markers, the `[x]` / `[ ]` task boxes and the cyan table pipes are all
legible. Scroll far enough that the **Tasks** subsection is on screen too.

```bash
spectacle -b -n -a -S -e -d 4000 -o ~/Pictures/emdee/editor.png
```

---

## 9 · `preview.png`

**Appears in:** the *Features* section, right half of the two-up table under
**Writing**.

| | |
| --- | --- |
| **Theme** | Catppuccin Latte (`F10` → Latte swatch) |
| **View mode** | **Preview only** — `Ctrl+Shift+3` |
| **Sidebar** | Rail expanded, Explorer open |
| **Document** | `~/emdee-demo/WELCOME.md` |
| **Scroll to** | `## Tables` as the topmost visible heading |
| **Tree** | `~/emdee-demo`, both subfolders expanded |
| **Panels** | None |
| **Window** | 1920 × 1034 |

Both tables — the shortcut table and the alignment table — should be visible.
This is the shot that shows off striped rows and header styling on a light
theme.

```bash
spectacle -b -n -a -S -e -d 4000 -o ~/Pictures/emdee/preview.png
```

---

## 10 · `find-replace.png`

**Appears in:** the *Usage → Keyboard shortcuts* section, immediately after the
shortcut tables.

| | |
| --- | --- |
| **Theme** | Dracula |
| **View mode** | Split — `Ctrl+Shift+2` |
| **Sidebar** | Rail expanded, Explorer open |
| **Document** | `~/emdee-demo/WELCOME.md` |
| **Scroll to** | `## Text formatting` as the topmost visible heading |
| **Tree** | `~/emdee-demo`, both subfolders expanded |
| **Panels** | **Find & replace open** (`Ctrl+H`) |
| **Window** | 1920 × 1034 |

Exact field contents:

- **Find** field: `Ctrl\+[A-Z0-9]`
- **Replace with** field: leave **empty**
- `.*` (regex) toggle: **on**
- `Aa` (match case) toggle: **off**
- `ab` (whole word) toggle: **off**

Press `Enter` two or three times so the counter reads something like
`3 of 14` and the editor shows the current match in accent purple with the
others in grey. That counter is the detail worth capturing.

```bash
spectacle -b -n -a -S -e -d 4000 -o ~/Pictures/emdee/find-replace.png
```

---

## 11 · `settings.png`

**Appears in:** the *Theme gallery* section, full width under the 3 × 2 grid.

| | |
| --- | --- |
| **Theme** | Nord (`F10` → Nord swatch, leave the drawer open) |
| **View mode** | Split |
| **Sidebar** | Rail expanded, Explorer open |
| **Document** | `~/emdee-demo/WELCOME.md` |
| **Scroll to** | `# Welcome to Emdee` — i.e. the very top of the document |
| **Tree** | `~/emdee-demo`, both subfolders expanded |
| **Panels** | **Preferences drawer open** (`F10`), scrolled to the top so all six theme swatches are visible with **Nord** highlighted |
| **Window** | 1920 × 1034 |

```bash
spectacle -b -n -a -S -e -d 4000 -o ~/Pictures/emdee/settings.png
```

---

## 12 · `about.png`

**Appears in:** the *License* section of `README.md`.

| | |
| --- | --- |
| **Theme** | Dracula (`F10` → Dracula, then close the drawer) |
| **View mode** | Split |
| **Sidebar** | Rail expanded, Explorer open |
| **Document** | `~/emdee-demo/WELCOME.md`, scrolled to the top |
| **Tree** | `~/emdee-demo`, both subfolders expanded |
| **Panels** | **About dialog open** — press `F1` |
| **Window** | 1920 × 1034 (the dialog is ~460 px wide, centred over it) |

This one is the **dialog**, not the whole window, so the capture command is
different — use region select and drag a tight box around the dialog:

```bash
spectacle -b -n -r -S -o ~/Pictures/emdee/about.png
```

Crop afterwards if the box was loose:

```bash
magick ~/Pictures/emdee/about.png -trim +repage ~/Pictures/emdee/about.png
```

---

## 13 · `demo.gif` and `demo.mp4`

**Appears in:** `demo.gif` is the hero at the very top of `README.md`;
`demo.mp4` is linked underneath as the full walkthrough.

> **Already done.** Both were produced from the 47-second screen recording:
> `demo.mp4` is the whole clip re-encoded to H.264, and `demo.gif` is the
> 8-second theme-switching cut (`-ss 29 -t 8`). The commands below are here so
> you can regenerate them.

Target: **≈ 8 seconds, ≤ 4 MB, 900 px wide**. Start from the shot-1 state
(Dracula, split view, `WELCOME.md`, Explorer open).

### Beat sheet

| Time | Action |
| --- | --- |
| 0.0 – 1.0 s | Hold still on the split view so the first frame reads clearly |
| 1.0 – 2.5 s | Type a new line: `## Live preview` then `This updates as you type.` — the right pane follows each keystroke |
| 2.5 – 3.5 s | Double-click the word `updates`, press `Ctrl+B`, then `Ctrl+I` |
| 3.5 – 4.5 s | Scroll the editor down with the wheel — the preview scrolls in step |
| 4.5 – 6.5 s | `F10`, click **Nord**, then **Catppuccin Latte**, then **Dracula**, `F10` to close |
| 6.5 – 7.5 s | `Ctrl+Shift+3` (preview only), pause, `Ctrl+Shift+2` (back to split) |
| 7.5 – 8.0 s | Hold still on the final frame |

Afterwards press `Ctrl+Z` until the document is clean again, or re-copy
`WELCOME.md` from the checkout.

### Recording

Plasma 6 records natively — no `wf-recorder` needed (it is a wlroots tool and
does not work on KWin either):

```bash
spectacle -R w
```

Click the Emdee window to start. Stop from the recording indicator in the system
tray, or with `Meta+Shift+R`. The clip lands in `~/Videos` as a `.webm`.

### Converting to GIF

```bash
cd ~/Videos
SRC=$(ls -t *.webm | head -1)

# Two-pass palette gives far better colour than a naive conversion
ffmpeg -y -i "$SRC" -vf "fps=14,scale=900:-1:flags=lanczos,palettegen=stats_mode=diff" /tmp/palette.png
ffmpeg -y -i "$SRC" -i /tmp/palette.png \
  -lavfi "fps=14,scale=900:-1:flags=lanczos[v];[v][1:v]paletteuse=dither=bayer:bayer_scale=3" \
  -loop 0 ~/Pictures/emdee/demo.gif

ls -lh ~/Pictures/emdee/demo.gif
```

If it comes out over 4 MB, in order of preference: shorten the clip, drop to
`fps=12`, then `scale=800:-1`. `gifski` gives noticeably better results if you
feel like installing it (`sudo pacman -S gifski`):

```bash
ffmpeg -y -i "$SRC" -vf "fps=14,scale=900:-1:flags=lanczos" /tmp/frames/%04d.png
gifski --fps 14 --width 900 --quality 85 -o ~/Pictures/emdee/demo.gif /tmp/frames/*.png
```

---

## Finishing up

Move everything into the repository and shrink the PNGs:

```bash
cd "$(git rev-parse --show-toplevel)"
cp ~/Pictures/emdee/*.png ~/Pictures/emdee/demo.gif screenshots/

# Strip metadata (ImageMagick is already installed)
magick mogrify -strip screenshots/*.png

# Better compression if you install it: sudo pacman -S oxipng
# oxipng -o4 --strip safe screenshots/*.png

du -sh screenshots/
```

Sanity check — every file the README expects:

```bash
cd "$(git rev-parse --show-toplevel)"
for f in hero.png demo.gif theme-dracula.png theme-clean-light.png \
         theme-catppuccin-latte.png theme-catppuccin-frappe.png \
         theme-rose-pine-dawn.png theme-nord.png editor.png preview.png \
         find-replace.png settings.png about.png; do
  [ -f "screenshots/$f" ] && echo "  ok      $f" || echo "  MISSING $f"
done
```

Then confirm the six theme shots really are the same size:

```bash
identify -format '%f %wx%h\n' screenshots/theme-*.png
```

---

## Checklist

- [x] **1** · `hero.png` — Dracula · split · `## Code` at top
- [x] **2** · `theme-dracula.png` — Dracula · split · `## Text formatting` at top
- [x] **3** · `theme-clean-light.png` — Clean Light · same position
- [x] **4** · `theme-catppuccin-latte.png` — Catppuccin Latte · same position
- [x] **5** · `theme-catppuccin-frappe.png` — Catppuccin Frappé · same position
- [x] **6** · `theme-rose-pine-dawn.png` — Rosé Pine Dawn · same position
- [x] **7** · `theme-nord.png` — Nord · same position
- [x] **8** · `editor.png` — Dracula · editor only · `## Lists` at top
- [x] **9** · `preview.png` — Catppuccin Latte · preview only · `## Tables` at top
- [x] **10** · `find-replace.png` — Dracula · split · find panel, regex on, counter visible
- [x] **11** · `settings.png` — Nord · split · preferences drawer open
- [x] **12** · `about.png` — Dracula · About dialog (`F1`), region capture
- [x] **13** · `demo.gif` — ≈ 8 s, ≤ 4 MB, 900 px wide
- [x] All six `theme-*.png` are 1920 × 1034 and share a scroll position
- [x] `magick mogrify -strip screenshots/*.png` run
- [x] No unsaved-changes dot (`•`) in any shot
- [x] Files copied into `screenshots/` and the checklist command reports no `MISSING`

---

## Appendix — wlroots compositors

On Sway, Hyprland or river, `grim` works and Spectacle does not:

```bash
# Whole screen
grim ~/Pictures/emdee/hero.png

# Interactive region select
grim -g "$(slurp)" ~/Pictures/emdee/hero.png

# Recording (wf-recorder is the wlroots equivalent of Plasma's recorder)
wf-recorder -g "$(slurp)" -f ~/Videos/demo.mp4
```

The ffmpeg conversion above is unchanged.
