<div align="center">

<img src="https://capsule-render.vercel.app/api?type=waving&color=0:4C8DFF,50:8A6BFF,100:9CC2FF&height=190&section=header&text=RomM-Themes&fontSize=56&fontColor=07080F&animation=fadeIn&fontAlignY=38&desc=themes%20for%20the%20RomM%20Switch%20client&descAlignY=58&descSize=18" width="100%"/>

[![Typing SVG](https://readme-typing-svg.demolab.com?font=Fira+Code&pause=1200&color=4C8DFF&center=true&vCenter=true&width=640&lines=19+semantic+colour+roles.;Backgrounds%2C+motion%2C+sprite-sheet+animation.;Your+own+font%2C+mascot+and+music.;One+folder+on+the+SD+card.;Validated+in+CI+before+it+ever+boots.)](#)

<img src="assets/hero.svg" alt="The RomM client library screen in the Midnight Borb theme, with the focus ring travelling between cards" width="100%"/>

<br/>

[![themes](https://img.shields.io/badge/dynamic/json?label=themes&query=%24.count&url=https%3A%2F%2Fraw.githubusercontent.com%2FA-Theme%2FRomM-Themes%2Fmain%2Fmanifest.json&style=for-the-badge&color=4C8DFF&labelColor=141A2C)](manifest.json)
[![colour roles](https://img.shields.io/badge/colour%20roles-19-8A6BFF?style=for-the-badge&labelColor=141A2C)](#the-format)
[![validated in CI](https://img.shields.io/github/actions/workflow/status/A-Theme/RomM-Themes/validate.yml?branch=main&label=validated&style=for-the-badge&color=4FC08D&labelColor=141A2C)](../../actions/workflows/validate.yml)
[![editor](https://img.shields.io/badge/visual-editor-9CC2FF?style=for-the-badge&labelColor=141A2C)](https://github.com/A-Theme/Theme-App/blob/main/romm-theme-editor.html)
[![licence](https://img.shields.io/badge/licence-MIT-5AA9E6?style=for-the-badge&labelColor=141A2C)](LICENSE)

</div>

---

Themes for the **RomM Switch client** — recolour it, give it a background, its
own font, mascot art, and music. A theme is a folder on the SD card, and the
smallest useful one is a single file.

<div align="center">

<!-- SCREENSHOTS:START -->
<!-- SCREENSHOTS:END -->

</div>

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

## Make one without writing JSON

There is a visual editor for this format:
**[RomM Theme Editor](https://github.com/A-Theme/Theme-App/blob/main/romm-theme-editor.html)**
(in [Theme-App](https://github.com/A-Theme/Theme-App) — runs in a browser,
installs as an app, works offline).

It previews the real client screens at the console's actual 1280×720, checks
your colours for readability, shows the animated-background memory budget live,
and exports either a `theme.json` or a ready-to-drop `.zip` pack.

**It reads this repo directly.** "Browse the catalog" lists every theme in
`manifest.json` — the same index the client reads on the console — and opens one
into the editor with its background, font, mascot and music downloaded, so the
quickest way to start a new theme is usually to open the nearest one here and
change what you want. It also imports a theme folder, a `.zip` pack, or a bare
`theme.json`, and ships 20 starting palettes if you would rather begin from
scratch.

Worth using even if you are comfortable with JSON, for two reasons it can catch
and a text editor cannot: whether `focus_ring` is actually visible against the
card it outlines, and whether an animated background fits in the texture budget.

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

<div align="center">

<img src="assets/palette.svg" alt="The 19 semantic colour roles, shown with the Midnight Borb values" width="100%"/>

</div>

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

## Part of the A-Theme project

| repo | what it is |
|---|---|
| **RomM-Themes** | you are here — themes for the RomM Switch client |
| [Theme-App](https://github.com/A-Theme/Theme-App) | the visual editors, for this format and for Tinfoil |
| [Tinfoil-Themes](https://github.com/A-Theme/Tinfoil-Themes) | the Tinfoil theme database (a separate format) |
| [Switch-Theme-Installer](https://github.com/A-Theme/Switch-Theme-Installer) | on-console installer for Tinfoil themes |
| [A-Theme](https://github.com/A-Theme) | the org |

## Licence

Theme metadata and this repo's tooling: MIT (see [LICENSE](LICENSE)).

Individual themes may bundle fonts, music and artwork under their own terms —
each theme's `theme.json` credits its author. **Only submit assets you have the
right to redistribute.** Fonts and music are the usual traps: plenty of both
are free to *use* but not to *bundle and redistribute*.

<div align="center">

<br/>

[![A-Theme](https://img.shields.io/badge/A--Theme-org-8A6BFF?style=for-the-badge&labelColor=141A2C)](https://github.com/A-Theme)
[![Theme-App](https://img.shields.io/badge/Theme--App-editors-4C8DFF?style=for-the-badge&labelColor=141A2C)](https://github.com/A-Theme/Theme-App)
[![Tinfoil-Themes](https://img.shields.io/badge/Tinfoil--Themes-tinfoil%20database-5AA9E6?style=for-the-badge&labelColor=141A2C)](https://github.com/A-Theme/Tinfoil-Themes)

<img src="https://capsule-render.vercel.app/api?type=waving&color=0:9CC2FF,50:8A6BFF,100:4C8DFF&height=110&section=footer" width="100%"/>

</div>
