# Changelog

Notable changes per release. The build workflow reads the section matching the
version being released and uses it as the release body, so this file is the one
place release notes are written.

## 1.1.0

### ffmpeg, included

Each platform now also has a `-with-ffmpeg.zip`: the app with an ffmpeg beside
it, which is where it already looks, so video input and WebM export work with
nothing installed and nothing configured.

It is a folder rather than one fatter executable on purpose — a one-file build
unpacks its whole payload to a temp directory on **every** launch, and ~140 MB
of ffmpeg would be paid for on every start. The lean download stays for anyone
who has ffmpeg already or only needs GIF, APNG and stills.

The bundle ships ffmpeg only, not ffprobe: that is another ~140 MB for three
numbers, which `probe_video()` now reads out of ffmpeg's own report instead,
matching ffprobe on size, duration and frame rate.

LGPL builds, unmodified, with the licence text and a source link in the zip —
so this stays an aggregation of two programs rather than a source obligation on
the whole repo. macOS keeps the lean download, since no LGPL macOS build is
published; the in-app steps cover `brew install ffmpeg`.

### When ffmpeg is missing, the way back is short

The banner used to state the problem and stop. It now gives the steps for the
platform you are on, links the builds, and has a **Check again** button that
re-probes without a restart — drop the binary in the folder, click, it lights
up. It also says what still works, which is most of the tool.

### A page you can see at once

The controls were one column of eight panels, about 3000px tall. They now flow
into balanced columns beside the sheet and the player, which stay put while you
work — a little over a third of the height, with nothing hidden behind a
disclosure triangle. Each panel header carries a small status chip: the frame
count and size, the sheet dimensions, the RomM verdict.

## 1.0.0

First release. A local tool that packs an animation into a sprite sheet the RomM
Switch client can actually read, and writes the `background.animation` block to
match it.

### What it does

- **Input**: GIF, APNG, animated WebP, MP4/WebM/MOV/AVI, or a single still image.
  GIF and APNG frames are composited with their disposal and transparency rather
  than copied raw, so frames come out clean instead of ghosted; video goes
  through ffmpeg.
- **Frame controls**: target FPS, max frames, trim by seconds or frame index,
  uniform scale by percent or exact size, nearest-neighbour for pixel art.
- **Layout**: columns or auto-square, padding, margin, transparent or solid
  background, power-of-two canvas padding.
- **Still to animation**: pan, rotate, scale/pulse, mirror cycle, bounce or fade
  one image into an N-frame loop, with linear, ease-in-out or bounce easing and
  loop or ping-pong.
- **Sheet to frames**: slice an existing sheet by rows/cols or cell size and play
  it back — play/pause, FPS, scrubber, frame index, onion skin.
- **Export**: animated GIF, WebM, APNG, or a zip of PNG frames, with FPS, loop
  count and a GIF dither/palette toggle.
- **Metadata**: RomM `theme.json`, plus Phaser 3 (hash and array), Godot
  SpriteFrames `.tres`, TexturePacker, and plain JSON.

### What it knows about the client

`app/romm.py` ports the parts of the RomM editor that decide whether the client
will play a sheet at all, so the tool reports refusals before a console does:

- The client slices `cols = sheet_w / frame_width` from the top-left, so padding
  between cells or an outer margin misaligns every frame after the first.
- The 48 MB texture budget: 218 frames at 320x180, 54 at 640x360, 13 at 720p.
- The 240-frame and 60 fps ceilings, a non-16:9 cell that will be stretched over
  the screen, and a frame count larger than the sheet can hold.
- Frame brightness, measured the way `scripts/validate-themes.py` measures it,
  because `dim` is tuned against the still image while the frames are what text
  actually sits on.

### Packaging

Single-file builds for Windows, macOS and Linux, so nothing needs installing.
ffmpeg is not bundled — put it on PATH or beside the executable for video input
and WebM export; everything else works without it and the page says so.
