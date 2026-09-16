# Changelog

Notable changes per release. The build workflow reads the section matching the
version being released and uses it as the release body, so this file is the one
place release notes are written.

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
