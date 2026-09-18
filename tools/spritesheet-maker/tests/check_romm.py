"""RomM check: the sheet packer's output against the RomM Switch client's own rules,
ported from A-Theme/Theme-App romm-theme-editor.html."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image, ImageChops

from app.atlas import AtlasOptions, parse_rects, write
from app.extract import Frame
from app.pack import PackOptions, pack
from app.romm import (MAX_ANIMATION_BYTES, MAX_ANIMATION_FRAMES, RommOptions, anim_bytes,
                      check, client_rects, max_frames_for_cell, sheet_capacity, sheet_source,
                      theme_json)

TMP = Path(__file__).resolve().parent.parent / "tmp"
TMP.mkdir(parents=True, exist_ok=True)
OUT = TMP / "romm"


def frames_of(n, w, h):
    out = []
    for i in range(n):
        im = Image.new("RGBA", (w, h), (10 + i * 7 % 200, 40, 90, 255))
        im.paste((255, 220, 0, 255), (i % w, 0, min(i % w + 12, w), 10))
        out.append(Frame(im, 83))
    return out


def levels(cs, lvl):
    return [c["message"] for c in cs if c["level"] == lvl]


def main():
    OUT.mkdir(exist_ok=True)

    # --- the client's arithmetic, against its own documented behaviour
    assert sheet_capacity(320, 180, 1280, 720) == 4 * 4
    assert sheet_capacity(320, 180, 1279, 720) == 3 * 4      # floor division, leftover ignored
    assert sheet_capacity(320, 180, 100, 100) == 0
    assert sheet_source(320, 180, 5, 1280, 720) == (320, 180, 320, 180)   # row 1, col 1
    assert sheet_source(320, 180, 16, 1280, 720) == (0, 0, 0, 0)          # past capacity
    print("PASS sheetCapacity / sheetSource match the client (floor division, row-major)")

    assert anim_bytes("sheet", 320, 180, 8) == 320 * 180 * 8 * 4
    assert anim_bytes("gif", 0, 0, 8) == 1280 * 720 * 8 * 4
    assert max_frames_for_cell("sheet", 320, 180) == MAX_ANIMATION_BYTES // (320 * 180 * 4) == 218
    assert max_frames_for_cell("sheet", 640, 360) == 54
    assert max_frames_for_cell("sheet", 1280, 720) == 13
    assert max_frames_for_cell("gif", 0, 0) == 13
    print(f"PASS texture budget caps frames per cell size: 320x180 -> 218, 640x360 -> 54, "
          f"1280x720 -> 13 (cap is {MAX_ANIMATION_FRAMES} frames)")

    # --- a sheet packed the RomM way: 8 frames of 320x180, no padding, no margin
    frames = frames_of(8, 320, 180)
    sheet, rects, layout = pack(frames, PackOptions.from_dict({"cols": 4, "padding": 0, "margin": 0}))
    assert sheet.size == (1280, 360), sheet.size
    opts = RommOptions.from_dict({"file": "sheet.png", "frame_width": 320, "frame_height": 180, "fps": 12})
    packed = [(r.x, r.y, r.w, r.h) for r in rects]
    derived = client_rects(320, 180, 8, *sheet.size)
    assert derived == packed, (derived[:3], packed[:3])
    for i, (x, y, w, h) in enumerate(derived):
        crop = sheet.crop((x, y, x + w, y + h))
        assert ImageChops.difference(crop, frames[i].image).getbbox() is None, i
    cs = check(opts, 8, *sheet.size, padding=0, margin=0)
    assert not levels(cs, "error"), levels(cs, "error")
    print(f"PASS 4x2 sheet of 320x180: the client's own rects equal pack.py's and crop to the "
          f"right frames; no blocking checks ({len(levels(cs, 'warning'))} warning)")

    # --- padding and margin, which the client cannot see
    sheet2, rects2, layout2 = pack(frames, PackOptions.from_dict({"cols": 4, "padding": 4, "margin": 8}))
    cs2 = check(opts, 8, *sheet2.size, padding=4, margin=8)
    errs = levels(cs2, "error")
    assert any("padding" in e for e in errs) and any("margin" in e for e in errs), errs
    derived2 = client_rects(320, 180, 8, *sheet2.size)
    packed2 = [(r.x, r.y, r.w, r.h) for r in rects2]
    assert derived2 != packed2
    print("PASS padded/margined sheet is refused: "
          f"client rect {derived2[1]} vs packed {packed2[1]} — both errors raised")

    # --- caps
    big = check(RommOptions.from_dict({"file": "s.png", "frame_width": 1280, "frame_height": 720}),
                40, 1280, 28800)
    assert any("MB budget" in e for e in levels(big, "error")), levels(big, "error")
    many = check(opts, 300, 1280, 43200)
    assert any("240-frame cap" in e for e in levels(many, "error")), levels(many, "error")
    print("PASS 48MB texture budget and 240-frame cap both raise the client's own error")

    # --- aspect ratio and capacity mismatches
    square = check(RommOptions.from_dict({"file": "s.png", "frame_width": 200, "frame_height": 200}),
                   4, 400, 400)
    assert any("distorted" in w for w in levels(square, "warning")), levels(square, "warning")
    short = check(opts, 12, 1280, 360)
    assert any("holds 8" in e for e in levels(short, "error")), levels(short, "error")
    leftover = check(opts, 8, 1290, 365)
    assert any("leftover strip" in w for w in levels(leftover, "warning")), levels(leftover, "warning")
    spare = check(opts, 6, 1280, 360)
    assert any("holds 8 frames" in i for i in levels(spare, "info")), levels(spare, "info")
    bad_name = check(RommOptions.from_dict({"file": "art/sheet.png", "frame_width": 320, "frame_height": 180}),
                     8, 1280, 360)
    assert any("path separator" in e for e in levels(bad_name, "error")), levels(bad_name, "error")
    print("PASS aspect, capacity, leftover strip, spare cells and unsafe file names all reported")

    # --- the emitted theme.json
    path = write(rects, layout, OUT, AtlasOptions.from_dict({
        "format": "romm", "image_name": "sheet.png",
        "romm": {"frame_width": 320, "frame_height": 180, "fps": 15, "loop": True,
                 "theme_name": "Aramaki Midjourney", "author": "Aramaki",
                 "background_image": "background.png", "dim": 0.35},
    }))
    data = json.loads(path.read_text())
    anim = data["background"]["animation"]
    assert anim == {"kind": "sheet", "file": "sheet.png", "frame_width": 320,
                    "frame_height": 180, "frames": 8, "fps": 15, "loop": True}, anim
    assert data["background"]["image"] == "background.png" and data["background"]["dim"] == 0.35
    assert data["name"] == "Aramaki Midjourney" and data["author"] == "Aramaki"
    print(f"PASS theme.json block matches the committed themes' shape: {json.dumps(anim)}")

    once = json.loads(theme_json(RommOptions.from_dict(
        {"file": "s.png", "frame_width": 320, "frame_height": 180, "loop": False}), 8))
    assert once["background"]["animation"]["loop"] is False
    print("PASS loop:false is written when the animation should not repeat")

    # --- round trip: parse the emitted file and re-derive the rects with the client's slicing
    back = parse_rects(path.read_text(), "romm", sheet.size)
    assert back == packed, (back[:2], packed[:2])
    print("PASS emitted theme.json -> client slicing -> same rects pack.py drew")

    print("\nROMM CHECK OK")


if __name__ == "__main__":
    main()
