# Contributing a theme

## Make it

```bash
mkdir -p "themes/My Theme"
```

Write `themes/My Theme/theme.json`. Start from
[`themes/Midnight Borb/theme.json`](themes/Midnight%20Borb/theme.json) — it
sets all 19 colour roles, so it is a complete worked example.

```json
{
  "name": "My Theme",
  "author": "your name",
  "version": "1.0.0",
  "colors": {
    "bg": "#07080F",
    "accent": "#4C8DFF",
    "focus_ring": "#9CC2FF"
  }
}
```

## Validate it

```bash
./scripts/validate-themes.py
```

This checks your theme and rewrites `manifest.json`. **Commit the manifest
change along with your theme** — CI runs `--check` and fails if it is stale.

The same validator enforces what the client enforces, so a green run means it
will load on a console.

## Rules

**Folder naming.** The folder name is the theme's identity on the SD card.
Letters, digits, spaces and `- _ . [ ] ( ) + !`. No slashes, no leading or
trailing dots or spaces.

**Assets are flat and local.** Reference them by plain file name — no
subdirectories, no paths. `"bg.png"`, never `"images/bg.png"` or
`"../something"`. The client refuses anything else, since themes get downloaded
and shared.

**Commit what you reference.** A `theme.json` naming a background you did not
commit is the single most common failure. The validator catches it.

**Sizes.** Backgrounds are 1280x720 — that is the Switch framebuffer in both
handheld and docked. Keep a theme under ~10 MB unless it ships music; if it
needs a sprite sheet, keep frames small (320x180 gives you ~200 frames inside
budget; 640x360 gives you ~50). [`tools/spritesheet-maker`](tools/spritesheet-maker)
packs the sheet, keeps the layout to what the client can read, and shows the
budget as you go.

**Readability is not optional.** `focus_ring` is how someone on a controller
knows where they are — it must stand out against `surface` and
`surface_raised`. `text` sits on `bg`, `surface` *and* `surface_raised`, so
check it against all three. If you use a busy background, raise
`background.dim` until body text is comfortable; start near `0.3`.

**Rights.** Only submit assets you can redistribute. Fonts and music are the
usual traps — many are free to use but not to bundle. Credit sources in your
PR.

## Screenshots

Drop a screenshot at `screenshots/<Folder Name>.png` and the validator links it
from `manifest.json` automatically. The README gallery rotates through whatever
is in `screenshots/` daily, so a screenshot is how a theme gets seen.

## Open the PR

One theme per pull request, please — it keeps review and reverts simple. Say
what you were going for and where the assets came from.

## Tooling in this repo

| script | does |
|---|---|
| `scripts/validate-themes.py` | validate every theme, rebuild `manifest.json` |
| `scripts/validate-themes.py --check` | validate only; fails on a stale manifest (what CI runs) |
| `scripts/scan-secrets.sh` | check nothing credential-shaped is about to be committed |
| `scripts/pre-commit` | git hook: regenerates a stale manifest, blocks credentials |
| `scripts/install-hooks.sh` | install that hook into this clone |

Git never copies hooks when you clone, so every fresh clone starts without
them. Install them once:

```bash
./scripts/install-hooks.sh
```

With the hook in place, committing a change under `themes/` regenerates
`manifest.json` and stages it for you, so the index can't fall behind the
themes it describes.

## If the validator disagrees with the client

`source/ui/theme_spec.h` in the client is normative, and
[`docs/THEME-FORMAT.md`](docs/THEME-FORMAT.md) is a copy of its documentation.
The validator mirrors those rules, and a mirror can drift — if you hit a case
where they disagree, the client is right and the validator needs fixing. Please
report it.
