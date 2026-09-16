# Tests

Eleven checks, all plain `python3` scripts with no test framework. Each prints one
`PASS` line per property it proves and exits non-zero on the first failure.
Everything they write goes to `spritesheet-maker/tmp/`, which is gitignored —
including the fixtures they build for themselves (a synthetic GIF, an ffmpeg
`testsrc` clip, a still PNG), so there is nothing to download or check in.

Run them from anywhere:

```bash
pip install -r requirements.txt          # fastapi, uvicorn, pillow, python-multipart
python3 tests/check_extract.py           # and so on
```

## Offline checks

| script | what it holds to |
|---|---|
| `check_extract.py` | a 6-frame GIF decodes to 6 frames with its per-frame durations, and every frame holds only its own pixels — the disposal/transparency compositing that naive `seek()` + `copy()` gets wrong. Also FPS resampling, max frames, both trim modes, nearest-neighbour scaling. |
| `check_pack.py` | sheet size is exactly `cols·cell + (cols-1)·padding + 2·margin` on both axes, across four layouts, plus power-of-two padding and solid/transparent backgrounds. |
| `check_slice.py` | **the round-trip contract**: every sliced frame is pixel-identical to the frame that was packed — by the packer's own rect list, by rows/cols geometry, by cell size, and after a PNG write/reload. |
| `check_video.py` | a 3-second `testsrc` clip extracts to 30 frames at 10fps, and trims by seconds and by frame index land where they should. |
| `check_generate.py` | all six still-image transforms produce distinct frames at source size; the three easings and both loop modes are checked numerically. |
| `check_export.py` | GIF keeps source timing and honours the dither/palette toggle, APNG keeps alpha, the ZIP holds one PNG per frame plus `frames.csv`, and the WebM probes as VP9 with the right packet count. |
| `check_atlas.py` | every emitted sidecar parses back, and its rects are inside the sheet, equal to `pack.py`'s rects, and crop to the right pixels. |
| `check_romm.py` | **the client contract** — see below. |
| `check_catalog.py` | a theme built by this tool passes this repo's own `scripts/validate-themes.py`, and the brightness maths here is identical to the validator's. It imports the validator and points it at a throwaway folder, so `themes/` and `manifest.json` are never touched. |

## `check_romm.py`

The same idea as `tests/check-motion.mjs` one directory up: the packer and the
RomM Switch client have to agree, so the agreement is a test rather than a
comment.

`app/romm.py` is a port of the parts of
[`romm-theme-editor.html`](../../romm-theme-editor.html) that decide whether the
client will play a sheet at all — `sheetCapacity`, `sheetSource`, `animBytes`,
`animationInUse` and the `renderChecks` warnings. The check runs a packed sheet
through the client's own slicing and requires the rects to equal the ones
`pack.py` drew, then requires each rect to crop to the source frame.

The load-bearing fact it pins down: the client slices `cols = sheet_w //
frame_width` from `(0,0)`, so a sheet packed with padding or an outer margin
misaligns every frame after the first. The check proves the mismatch is caught
rather than shipped, along with the 48 MB texture budget, the 240-frame cap, the
16:9 cell warning, capacity mismatches and unsafe asset names.

If the client changes any of that arithmetic, this check is how you find out.

`check_catalog.py` closes the other half of the loop: it builds a small theme
with the tool — sheet, still background, `theme.json` — and hands it to
`scripts/validate-themes.py`, the same validator CI runs. A sheet this tool
produces has to be one the catalogue accepts, and the brightness check both
sides run has to agree to the digit.

## Server-backed checks

These two need the app running:

```bash
python3 -m uvicorn app.main:app --port 8731 &
python3 tests/check_roundtrip.py
python3 tests/check_ui.py
```

`SSM_BASE` overrides the base URL (default `http://127.0.0.1:8731`), which is
also how both are pointed at a packaged build rather than the source tree:

```bash
pyinstaller --clean --noconfirm spritesheet-maker.spec
./dist/spritesheet-maker --no-browser --port 8801 &
SSM_BASE=http://127.0.0.1:8801 python3 tests/check_roundtrip.py
SSM_BASE=http://127.0.0.1:8801 python3 tests/check_ui.py
```

A frozen build finds its files differently from a source checkout — the page is
unpacked into the bundle, jobs go to the system temp dir — so running these two
against the binary is what proves the packaging, not just the code.

- `check_roundtrip.py` — a `.gif`, an `.mp4` and a `.png` each go upload → sheet
  → slice → preview → export over HTTP, with the pixel-identity assertion
  holding across the whole trip.
- `check_ui.py` — drives the real page in Chromium through Playwright: every
  control clicked, canvas pixels diffed for the scrubber and the onion skin, all
  four exports downloaded, the RomM preset and its checks read back off the page.
  Needs `pip install playwright` and a Chromium; set `SSM_CHROMIUM` to a browser
  binary if Playwright's own download is not the one installed.
