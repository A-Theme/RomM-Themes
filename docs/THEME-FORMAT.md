# Theme format

> Vendored copy, kept here so theme authors have the reference without needing
> access to the client repository. `source/ui/theme_spec.h` in the client is
> normative — if the two ever disagree, that file is right and this copy needs
> updating.

A theme changes how the client looks and sounds: colours, background, font,
mascot art, and music. It is a folder on the SD card with a `theme.json` in it.

```
sdmc:/switch/romm-client/themes/
    Midnight Borb/
        theme.json          required
        bg.png              optional
        font.ttf            optional
        mascot.png          optional
        song.mp3            optional
```

The **folder name** is the theme's identity — it is what the client remembers
as your selection, so renaming a folder deselects the theme. The `name` inside
`theme.json` is only what the picker displays.

Nothing is required except `theme.json`. **A theme that sets one colour is a
valid theme**; every role it does not mention keeps the built-in value. That
also means a theme written for an older build keeps working when new roles are
added.

## Choosing one

**Settings → Theme**. Press A to step to the next theme on the card; the list
cycles through the built-in look and every folder under `themes/`, so there is
no separate picker screen to open.

**Colours change the moment you press A.** A theme's font, background, mascot
and music are opened once when the client starts, so those appear at the next
launch. The row says so, and it shows the first warning from a theme that
half-applied rather than leaving you to guess.

Your choice is saved as the folder name, so renaming a theme's folder
deselects it, while renaming the theme *inside* `theme.json` does not.

## A minimal theme

```json
{
  "name": "Just Blue",
  "colors": { "accent": "#4488FF" }
}
```

## A complete theme

```json
{
  "name": "Midnight Borb",
  "author": "Aramaki",
  "version": "1.0.0",

  "colors": {
    "bg": "#000010",
    "surface": "#0A0A1E",
    "text": "#EEEEFF",
    "accent": "#4488FF",
    "focus_ring": "#88CCFF"
  },

  "background": {
    "image": "bg.png",
    "dim": 0.35,
    "motion": { "kind": "drift", "speed": 0.4, "amount": 32 }
  },

  "font":   { "file": "font.ttf" },
  "assets": { "mascot": "mascot.png" },
  "audio":  { "bgm": "song.mp3", "volume": 0.4 }
}
```

## Colours

Values are CSS-style hex: `#RGB`, `#RGBA`, `#RRGGBB`, or `#RRGGBBAA`. The `#`
is optional and case does not matter. `#fff` expands to white, as in CSS.

A colour **name** like `"red"` is rejected on purpose — a name that silently
parsed as black would look like a rendering bug rather than the typo it is. The
role keeps its default and you get a warning in Settings.

There are 19 roles. They are semantic, not raw palette slots, so a theme stays
coherent as screens are added.

| role | what it paints |
|---|---|
| `bg` | the app background, behind everything |
| `surface` | panels and sheets sitting on the background |
| `surface_raised` | cards, list rows, raised blocks |
| `surface_hover` | the selected/hovered row |
| `border` | dividers, outlines, hairlines |
| `text` | default body text |
| `text_strong` | titles and emphasis |
| `text_muted` | secondary text, metadata, hints |
| `text_disabled` | unavailable actions |
| `text_on_accent` | text drawn on top of an accent fill |
| `accent` | primary brand colour: buttons, progress, highlights |
| `accent_hover` | the accent when focused or pressed |
| `accent_alt` | secondary accent |
| `focus_ring` | the ring around whatever has focus |
| `success` | completed installs, healthy state |
| `warning` | cautions |
| `danger` | errors, destructive actions |
| `info` | neutral notices |
| `scrim` | the dimming behind a modal — **use alpha here**, e.g. `#000000BE` |

Unknown role names are ignored with a warning rather than failing the theme, so
a typo costs you one role and not the whole file.

### Readability

`focus_ring` and `text` are the two that break a theme when chosen badly: the
focus ring is how anyone knows where they are on a controller, and body text
sits on `bg`, `surface` and `surface_raised` alike. Keep real contrast against
all three.

## Background

```json
"background": { "image": "bg.png", "dim": 0.35 }
```

The image is drawn behind the UI at 1280x720 — that is the Switch framebuffer
in both handheld and docked, so author at exactly that size.

`dim` (0.0–1.0) blends the image toward black. It exists so you can use a busy
image without pre-darkening the file: start around `0.3` and raise it until
body text is comfortable. Out-of-range values are clamped with a warning.

`"background": "bg.png"` is accepted as shorthand when you only want an image.

### Motion — free animation

```json
"motion": { "kind": "drift", "speed": 0.4, "amount": 32 }
```

Motion moves the *still* image and costs **no extra memory**, which makes it
the best-value way to make a background feel alive.

| kind | effect |
|---|---|
| `drift` | slow diagonal wander, loops seamlessly |
| `pan` | horizontal sweep |
| `zoom` | slow breathing scale, Ken Burns style |
| `none` | static (the default) |

`speed` multiplies the cycle rate (clamped 0.1–8.0). `amount` is travel in
pixels for `drift`/`pan`, or percent of size for `zoom` (clamped 0–256).

Because motion samples outside the image edges, a `drift` or `pan` background
should either tile or have calm edges.

### Animation — real frames

```json
"animation": {
  "kind": "sheet", "file": "sheet.png",
  "frame_width": 320, "frame_height": 180,
  "frames": 8, "fps": 10
}
```

Frames cost memory: one 1280x720 frame is **3.6 MB** as a texture, and under
the Homebrew Menu the client gets whatever heap hbloader hands over rather than
the ~3.2 GB an application-class launch reports. So there is a **48 MB texture
budget**, a 240-frame ceiling, and a 60 fps ceiling. An animation that busts
the budget is dropped with a warning and the still `image` is used instead.

**`sheet` is the recommended kind.** Every frame lives in one image laid out
left-to-right, top-to-bottom, so it is one texture and one decode no matter how
many frames, and drawing a sub-rect is free. At 320x180 you can afford ~200
frames; at 640x360, ~50.

**`gif`** plays an animated GIF or WEBP, but needs SDL2_image 2.6.0+
(`IMG_LoadAnimation`). Where the build is older, the client says so in Settings
and falls back to the still image. A GIF is billed at full-screen per frame, so
a 30-frame 720p GIF is 105 MB and will be refused.

**Always ship a still `image` alongside an animation.** It is the fallback for
an over-budget animation, a missing file, or a build without GIF support.

**The numbers are checked against the file, not taken on trust.** The validator
reads a gif's real dimensions and frame count out of the file and costs the
budget on those, and checks that a sheet is actually large enough to hold the
frames it declares. A `frames` that disagrees with the file is an error: the
client decodes the file, so a wrong count does not make an animation cheaper,
it just hides that it is too expensive. One theme here declared 8 frames for a
gif holding 125, which priced a 48.8 MB animation at 29 MB and passed.

`loop` defaults to `true`; `false` plays the sequence once and holds the last
frame.

## Effects — per-element particles

Motion and animation act on the background. **Effects** act on a single UI
element, and live at the top level of `theme.json`, beside `background`:

```json
"effects": {
  "focus": { "kind": "embers", "speed": 1.2, "amount": 65, "color": "accent_alt" }
}
```

| slot | where it draws |
|---|---|
| `focus` | around the currently selected element |

| kind | effect |
|---|---|
| `embers` | drifting sparks that rise and fade |
| `none` | nothing (the default) |

`speed` multiplies the cycle rate (clamped 0.1–8.0). `amount` sets particle
density (clamped 0–256); the client caps what it will actually draw at **48
particles per element** and clips them to a 26px band, so very large values
buy nothing. On `Borb's Lair`, `amount: 65` yields about 21 particles around a
library card.

`color` takes **either a colour role or a hex value**. Prefer the role — a
role follows the palette if the theme is ever recoloured, where a hex does
not. A role that the theme does not set is a warning; a name that is neither
a role nor a hex is an error.

> The validator mirrors the slots and kinds above from `source/ui/theme_spec.h`
> in the client, and can fall back behind it. An unknown slot or kind is
> therefore reported as a *warning*, not an error, so a theme using something
> newer than the validator still lands. Structural mistakes — a bad type, an
> out-of-range number, a colour that names nothing — are always errors.

## Font

```json
"font": { "file": "font.ttf" }
```

Replaces the UI face at all five type sizes (36/28/24/20/17 px). `"font":
"font.ttf"` is accepted as shorthand.

Two warnings from experience. The built-in path uses the console's **shared
font**, which covers CJK and the Nintendo button glyphs; a Latin-only
replacement will show blanks for those. And the layout is tuned to the default
metrics, so a much wider face can overflow rows — check the library and detail
screens before publishing.

## Mascot

```json
"assets": { "mascot": "mascot.png" }
```

Replaces the Borb mascot. PNG with transparency.

## Music

```json
"audio": { "bgm": "song.mp3", "volume": 0.4 }
```

The client links mpg123, vorbis, opus, FLAC **and modplug**, so `.mp3`, `.ogg`,
`.opus`, `.flac` and tracker modules (`.mod`, `.xm`, `.it`, `.s3m`) all work. A
tracker module is often a few KB for minutes of music, which suits a theme pack
well.

`volume` accepts either a `0.0`–`1.0` fraction or SDL_mixer's `0`–`128`. A
value of `1` is read as *full*, not 1/128.

To silence the built-in music without shipping your own:

```json
"audio": { "bgm": "none" }
```

`"bgm_disabled": true` does the same thing.

## Asset naming rules

Asset values are **plain file names inside the theme folder**. These are all
rejected, with a warning, because theme packs get downloaded and shared:

- `../../../switch/prod.keys` — parent traversal
- `/switch/romm-client/config.json` — absolute paths
- `sdmc:/switch/prod.keys` — device paths
- `sub/dir/bg.png` — subdirectories; keep assets flat

Allowed characters are letters, digits, space, and `- _ . [ ] ( ) + !`. A name
needing any other character is refused rather than quietly rewritten, since the
rewritten name would not match the file on disk anyway.

Rejecting one asset never fails the theme — its colours still apply.

## Troubleshooting

Every recoverable problem becomes a **warning shown in Settings** next to the
theme, rather than a silent half-application. The common ones:

| symptom | cause |
|---|---|
| theme not in the picker | no `theme.json` in the folder |
| theme fails to load | `theme.json` is not valid JSON — check trailing commas |
| one colour ignored | bad hex, or a role name typo |
| background missing | the file named is not in the folder |
| animation not playing | over the 48 MB budget, or a GIF on an older build |
| text unreadable | raise `background.dim`, or fix `text` contrast |

## Notes for tools

`source/ui/theme_spec.h` is the normative description of the format, and
`theme_color_roles()` is the single source of truth for the role list — an
editor should read roles from there rather than hardcoding a copy.

The format is covered by `tests/test_theme.cpp` (parsing, validation,
rejections) and `tests/test_theme_runtime.cpp` (discovery, asset resolution,
theme switching), both of which run on the host with plain `g++` via
`./tests/run-tests.sh`.
