# RomM-Themes

Themes for the RomM Switch client — recolour it, give it a background, its own
font, mascot art, and music.

<!-- SCREENSHOTS:START -->
<!-- SCREENSHOTS:END -->

## Install a theme

1. Download the theme's folder from [`themes/`](themes/).
2. Copy the **whole folder** to your SD card:

   ```
   sdmc:/switch/romm-client/themes/<Theme Name>/
   ```

3. Launch the client and pick it in **Settings → Theme**.

The folder name is the theme's identity, so keep it intact — renaming it
deselects the theme. Everything else about a theme lives inside its folder;
nothing is installed anywhere else and nothing outside that folder is touched.

## What a theme looks like

```
themes/Midnight Borb/
    theme.json          required - colours and settings
    bg.png              optional - 1280x720 background
    font.ttf            optional - replaces the UI font
    mascot.png          optional - replaces the mascot
    song.mp3            optional - looping music
```

The smallest valid theme is one file:

```json
{
  "name": "Just Blue",
  "author": "you",
  "colors": { "accent": "#4488FF" }
}
```

Anything a theme does not mention keeps the client's built-in value, so you can
tint one thing without restating the other eighteen colours.

## Themes here

See [`manifest.json`](manifest.json) for the machine-readable index — it is
generated, one entry per theme, with what each one changes and how big it is.

## Contributing a theme

Read [CONTRIBUTING.md](CONTRIBUTING.md). The short version:

```bash
mkdir -p "themes/My Theme"
$EDITOR "themes/My Theme/theme.json"
./scripts/validate-themes.py          # validates and updates manifest.json
```

CI runs the same validator on every pull request, so a typo'd colour role or a
background you forgot to commit is caught before it reaches anyone's console.

## The format

The full format reference is [`docs/THEME-FORMAT.md`](docs/THEME-FORMAT.md) —
a copy of the client's own theme documentation, kept here so theme authors have
it to hand. `source/ui/theme_spec.h` in the client is the normative
implementation this repo validates against.

Quick reference:

| block | what it sets |
|---|---|
| `colors` | 19 semantic roles, CSS hex (`#RGB`, `#RRGGBB`, `#RRGGBBAA`) |
| `background` | still image, `dim`, free `motion`, or a frame `animation` |
| `font` | a `.ttf`/`.otf` replacing the UI face at all sizes |
| `assets` | `mascot` art |
| `audio` | `bgm` track and `volume`; `"bgm": "none"` for silence |

Two things worth knowing before you build something ambitious:

- **Animated backgrounds are budgeted.** One 720p frame is 3.6 MB of texture,
  so there is a 48 MB ceiling. A **sprite sheet** (`"kind": "sheet"`) is one
  texture no matter how many frames and is the cheap path; an animated GIF is
  billed at full screen per frame, and 30 frames already busts the budget.
  Always ship a still `image` as the fallback.
- **`motion` is free.** Drift, pan or zoom on a still image costs no extra
  memory at all and often looks better than a short loop.

## Licence

Theme metadata and this repo's tooling: MIT (see [LICENSE](LICENSE)).

Individual themes may bundle fonts, music and artwork under their own terms —
each theme's `theme.json` credits its author. **Only submit assets you have the
right to redistribute.** Fonts and music are the usual traps: plenty of both
are free to *use* but not to *bundle and redistribute*.
