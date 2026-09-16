"""Checkpoint 4 verification: every sliced frame is pixel-identical to its source frame."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image, ImageChops

from app.extract import load_frames
from app.pack import PackOptions, pack
from app.slice import SliceOptions, slice_by_rects, slice_sheet
from check_extract import build_gif

TMP = Path(__file__).resolve().parent.parent / "tmp"
TMP.mkdir(parents=True, exist_ok=True)


def identical(a: Image.Image, b: Image.Image) -> bool:
    if a.size != b.size or a.mode != b.mode:
        return False
    return ImageChops.difference(a, b).getbbox() is None


def main():
    gif = TMP / "check_extract.gif"
    if not gif.exists():
        build_gif(gif)
    sources = load_frames(gif)

    cases = [
        {"cols": 3, "padding": 4, "margin": 8},
        {"cols": 2, "padding": 0, "margin": 0},
        {"cols": 6, "padding": 7, "margin": 11},
        {"auto_square": True, "padding": 3, "margin": 2},
        {"cols": 4, "padding": 5, "margin": 6, "power_of_two": True},
    ]
    for case in cases:
        sheet, rects, layout = pack(sources, PackOptions.from_dict(case))

        # (a) slice by the packer's own rect list
        sliced = slice_by_rects(sheet, rects)
        assert len(sliced) == len(sources), (len(sliced), len(sources))
        for i, (out, src) in enumerate(zip(sliced, sources)):
            assert identical(out.image, src.image), f"{case}: frame {i} differs from its source"
            assert out.duration_ms == src.duration_ms, f"{case}: frame {i} duration lost"

        # (b) slice the same sheet from geometry alone (what the UI does for an
        #     uploaded sheet) — must land on the same rects
        geo = SliceOptions.from_dict({
            "mode": "grid", "cols": layout.cols, "rows": layout.rows,
            "padding": layout.padding, "margin": layout.margin, "count": len(sources),
        })
        # POT sheets carry dead space, so slice the un-padded grid region
        sub = sheet.crop((0, 0,
                          layout.margin * 2 + layout.cols * layout.cell_w + (layout.cols - 1) * layout.padding,
                          layout.margin * 2 + layout.rows * layout.cell_h + (layout.rows - 1) * layout.padding))
        frames2, rects2, layout2 = slice_sheet(sub, geo)
        assert [(r.x, r.y, r.w, r.h) for r in rects2] == [(r.x, r.y, r.w, r.h) for r in rects], \
            f"{case}: geometry rects != packer rects"
        for i, (out, src) in enumerate(zip(frames2, sources)):
            assert identical(out.image, src.image), f"{case}: geometry-sliced frame {i} differs"
        print(f"PASS {case}: 6/6 frames pixel-identical via rects AND via rows/cols geometry")

    # cell-size geometry path
    sheet, rects, layout = pack(sources, PackOptions.from_dict({"cols": 3, "padding": 4, "margin": 8}))
    frames3, rects3, layout3 = slice_sheet(sheet, SliceOptions.from_dict(
        {"mode": "cell", "cell_w": 64, "cell_h": 64, "padding": 4, "margin": 8, "count": 6}))
    assert (layout3.cols, layout3.rows) == (3, 2), (layout3.cols, layout3.rows)
    for i, (out, src) in enumerate(zip(frames3, sources)):
        assert identical(out.image, src.image), f"cell mode: frame {i} differs"
    print("PASS cell-size geometry (64x64, pad 4, margin 8) -> 3x2 grid, 6/6 pixel-identical")

    # round trip through a written-and-reloaded PNG, as the UI does
    path = TMP / "check_slice_sheet.png"
    sheet.save(path)
    with Image.open(path) as reloaded:
        frames4 = slice_by_rects(reloaded.convert("RGBA"), rects)
    for i, (out, src) in enumerate(zip(frames4, sources)):
        assert identical(out.image, src.image), f"PNG round trip: frame {i} differs"
    print("PASS PNG write -> reload -> slice: 6/6 frames still pixel-identical")

    print("\nCHECKPOINT 4 OK — pixel identity holds")


if __name__ == "__main__":
    main()
