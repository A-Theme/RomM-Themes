#!/usr/bin/env python3
"""
Catalog thumbnails: the background art, and nothing else.

The editor's catalog used to identify a theme by four colour bands, which told
you its palette and nothing about what it looks like. The obvious fix - point
an <img> at the theme's own background - costs about 1.3 MB per row, and a
catalogue of 160 of those is not a list anyone wants to scroll.

So each theme gets one small 16:9 crop of its background here, around 25 KB,
and the manifest carries its path next to the screenshot's. An animated theme
gets an animated thumbnail, because a still frame of something that moves is
the thing this change exists to stop doing.

    ./scripts/make-thumbs.py            # only what is missing or out of date
    ./scripts/make-thumbs.py --force    # all of them

Rerun it after changing a background, then re-run validate-themes.py so the
manifest picks the thumbnail up.
"""

import json
import os
import sys

try:
    from PIL import Image
except ImportError:
    sys.exit("Pillow is needed: pip install Pillow")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
THEMES_DIR = os.path.join(REPO, "themes")
THUMBS_DIR = os.path.join(REPO, "thumbs")

STILL_SIZE = (480, 270)
ANIM_SIZE = (320, 180)
ANIM_MAX_FRAMES = 24          # a loop, not the whole animation
STILL_QUALITY = 80
ANIM_QUALITY = 55


def cover(img, size):
    """Fill `size` with the middle of `img`, keeping its aspect ratio."""
    want_w, want_h = size
    src_w, src_h = img.size
    if src_w * want_h > want_w * src_h:          # too wide - trim the sides
        cut = int(round(src_h * want_w / want_h))
        left = (src_w - cut) // 2
        img = img.crop((left, 0, left + cut, src_h))
    else:                                        # too tall - trim top/bottom
        cut = int(round(src_w * want_h / want_w))
        top = (src_h - cut) // 2
        img = img.crop((0, top, src_w, top + cut))
    return img.convert("RGB").resize(size, Image.LANCZOS)


def sheet_frames(path, spec):
    """The sprite sheet's frames, in order, as the client reads them."""
    fw = int(spec.get("frame_width") or 0)
    fh = int(spec.get("frame_height") or 0)
    count = int(spec.get("frames") or 0)
    if fw <= 0 or fh <= 0 or count <= 0:
        return []

    with Image.open(path) as sheet:
        sheet.load()
        cols = max(1, sheet.width // fw)
        out = []
        # Evenly spaced across the whole animation, so a 24-frame thumbnail of
        # a 120-frame sheet is still the same loop and not its first fifth.
        step = max(1, count // ANIM_MAX_FRAMES)
        for i in range(0, count, step):
            x, y = (i % cols) * fw, (i // cols) * fh
            if x + fw > sheet.width or y + fh > sheet.height:
                break
            out.append(cover(sheet.crop((x, y, x + fw, y + fh)), ANIM_SIZE))
            if len(out) >= ANIM_MAX_FRAMES:
                break
    return out


def newest(paths):
    times = [os.path.getmtime(p) for p in paths if os.path.isfile(p)]
    return max(times) if times else 0.0


def build(folder, force):
    """(status, note) for one theme folder."""
    d = os.path.join(THEMES_DIR, folder)
    spec_path = os.path.join(d, "theme.json")
    if not os.path.isfile(spec_path):
        return "skip", "no theme.json"

    with open(spec_path, encoding="utf-8") as fh:
        spec = json.load(fh)

    bg = spec.get("background") or {}
    still = bg.get("image") or ""
    anim = bg.get("animation") or {}
    sheet = anim.get("file") or ""
    animated = bool(sheet) and (anim.get("kind") or "sheet") == "sheet"

    if not still and not sheet:
        return "skip", "no background"

    sources = [os.path.join(d, n) for n in (still, sheet) if n]
    sources.append(spec_path)
    out = os.path.join(THUMBS_DIR, folder + ".webp")

    if not force and os.path.isfile(out) and os.path.getmtime(out) >= newest(sources):
        return "fresh", ""

    frames = []
    if animated and os.path.isfile(os.path.join(d, sheet)):
        frames = sheet_frames(os.path.join(d, sheet), anim)

    os.makedirs(THUMBS_DIR, exist_ok=True)

    if len(frames) > 1:
        fps = float(anim.get("fps") or 10) or 10.0
        # The source frames were thinned, so each thumbnail frame stands for
        # several and has to be held that much longer or the loop runs fast.
        step = max(1, int(anim.get("frames") or len(frames)) // ANIM_MAX_FRAMES)
        ms = max(20, int(round(1000.0 * step / fps)))
        frames[0].save(out, "WEBP", save_all=True, append_images=frames[1:],
                       duration=ms, loop=0, quality=ANIM_QUALITY, method=4)
        return "anim", f"{len(frames)} frames at {ms} ms"

    src = os.path.join(d, still) if still else os.path.join(d, sheet)
    if not os.path.isfile(src):
        return "skip", f'"{os.path.basename(src)}" is missing'
    with Image.open(src) as img:
        img.load()
        cover(img, STILL_SIZE).save(out, "WEBP", quality=STILL_QUALITY, method=6)
    return "still", ""


def main():
    force = "--force" in sys.argv[1:]
    if not os.path.isdir(THEMES_DIR):
        sys.exit("no themes/ directory here")

    counts = {}
    for folder in sorted(os.listdir(THEMES_DIR)):
        if not os.path.isdir(os.path.join(THEMES_DIR, folder)):
            continue
        try:
            status, note = build(folder, force)
        except Exception as exc:                         # one bad theme, not a bad run
            status, note = "error", str(exc)
        counts[status] = counts.get(status, 0) + 1
        if status in ("error", "skip"):
            print(f"  {status}: {folder} - {note}")
        elif status != "fresh":
            print(f"  {status}: {folder}" + (f" ({note})" if note else ""))

    print()
    print("  ".join(f"{k} {v}" for k, v in sorted(counts.items())))
    return 1 if counts.get("error") else 0


if __name__ == "__main__":
    sys.exit(main())
