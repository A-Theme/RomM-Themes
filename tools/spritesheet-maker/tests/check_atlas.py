"""Checkpoint 8 verification: parse each emitted atlas back and check it against
the real packed sheet — every rect inside bounds, matching pack.py's rect list,
and cropping to the same pixels the packer drew."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image, ImageChops

from app.atlas import FORMATS, AtlasOptions, parse_rects, write
from app.extract import load_frames
from app.pack import PackOptions, pack
from check_extract import build_gif

TMP = Path(__file__).resolve().parent.parent / "tmp"
TMP.mkdir(parents=True, exist_ok=True)
OUT = TMP / "atlas"


def main():
    gif = TMP / "check_extract.gif"
    if not gif.exists():
        build_gif(gif)
    sources = load_frames(gif)

    for case in [{"cols": 3, "padding": 4, "margin": 8},
                 {"cols": 4, "padding": 3, "margin": 5, "power_of_two": True},
                 {"cols": 3, "padding": 0, "margin": 0}]:
        sheet, rects, layout = pack(sources, PackOptions.from_dict(case))
        sheet_path = OUT / "sheet.png"
        OUT.mkdir(exist_ok=True)
        sheet.save(sheet_path)
        expected = [(r.x, r.y, r.w, r.h) for r in rects]
        print(f"\nsheet {sheet.size} from {case}")

        for fmt, (label, suffix) in FORMATS.items():
            path = write(rects, layout, OUT, AtlasOptions.from_dict(
                {"format": fmt, "image_name": "sheet.png", "animation_name": "spin"}))
            text = path.read_text(encoding="utf-8")
            if suffix == ".json":
                json.loads(text)                       # must be valid JSON
            if fmt == "romm":
                # The RomM client derives rects from the frame size alone, so it can only
                # agree with the packer on a tight grid. tmp/check_romm.py covers the rest.
                got = parse_rects(text, fmt, sheet.size)
                if layout.padding or layout.margin:
                    assert got != expected, "a padded sheet should not slice like the client's"
                    print(f"  PASS {label:24s} {path.name:26s} padded sheet correctly "
                          f"disagrees with the client's slicing")
                    continue
            else:
                got = parse_rects(text, fmt)
            assert len(got) == len(expected), f"{fmt}: {len(got)} rects, expected {len(expected)}"
            assert got == expected, f"{fmt}: rects differ\n got {got}\n exp {expected}"
            for (x, y, w, h) in got:
                assert 0 <= x and 0 <= y and x + w <= sheet.width and y + h <= sheet.height, \
                    f"{fmt}: rect {(x, y, w, h)} outside {sheet.size}"
            # the rect must actually cut the frame the packer drew there
            for i, (x, y, w, h) in enumerate(got):
                crop = sheet.crop((x, y, x + w, y + h))
                assert ImageChops.difference(crop, sources[i].image).getbbox() is None, \
                    f"{fmt}: rect {i} does not crop to source frame {i}"
            print(f"  PASS {label:24s} {path.name:26s} {len(got)} rects — in bounds, "
                  f"== pack.py rects, crops to the right pixels")

    # per-frame durations survive in the formats that carry them
    sheet, rects, layout = pack(sources, PackOptions.from_dict({"cols": 3}))
    plain = json.loads(write(rects, layout, OUT, AtlasOptions.from_dict({"format": "plain"})).read_text())
    assert [f["duration_ms"] for f in plain["frames"]] == [80, 120, 160, 200, 240, 280]
    assert [f["frame_index"] for f in plain["frames"]] == list(range(6))
    print("\nPASS plain JSON carries frame_index/x/y/w/h/duration_ms with source durations")

    ph = json.loads(write(rects, layout, OUT, AtlasOptions.from_dict({"format": "phaser-hash"})).read_text())
    assert set(ph) == {"frames", "meta"} and isinstance(ph["frames"], dict) and len(ph["frames"]) == 6
    assert ph["meta"]["size"] == {"w": layout.width, "h": layout.height}
    pa = json.loads(write(rects, layout, OUT, AtlasOptions.from_dict({"format": "phaser-array"})).read_text())
    assert isinstance(pa["frames"], list) and pa["frames"][0]["filename"] == "frame_000"
    tp = json.loads(write(rects, layout, OUT, AtlasOptions.from_dict({"format": "texturepacker"})).read_text())
    assert tp["frames"][0]["filename"].endswith(".png") and "smartupdate" in tp["meta"]
    print("PASS Phaser hash/array and TexturePacker shapes are correct, meta size == sheet size")

    tres = write(rects, layout, OUT, AtlasOptions.from_dict(
        {"format": "godot", "animation_name": "spin"})).read_text()
    assert tres.startswith('[gd_resource type="SpriteFrames" load_steps=8 format=3]'), tres[:80]
    assert tres.count('[sub_resource type="AtlasTexture"') == 6
    assert '&"spin"' in tres and 'ExtResource("1_sheet")' in tres
    print("PASS Godot .tres: SpriteFrames header, 6 AtlasTexture sub-resources, named animation")

    print("\nCHECKPOINT 8 OK")


if __name__ == "__main__":
    main()
