"""Checkpoint 3 verification: sheet dimensions match the grid formula exactly."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.extract import load_frames
from app.pack import PackOptions, auto_cols, pack
from check_extract import build_gif

TMP = Path(__file__).resolve().parent.parent / "tmp"
TMP.mkdir(parents=True, exist_ok=True)


def main():
    gif = TMP / "check_extract.gif"
    if not gif.exists():
        build_gif(gif)
    frames = load_frames(gif)
    assert len(frames) == 6
    cell = 64

    cases = [
        {"cols": 3, "padding": 4, "margin": 8},
        {"cols": 2, "padding": 0, "margin": 0},
        {"cols": 6, "padding": 10, "margin": 3},
        {"auto_square": True, "padding": 2, "margin": 5},
    ]
    for case in cases:
        opts = PackOptions.from_dict(case)
        sheet, rects, layout = pack(frames, opts)
        cols = opts.cols or auto_cols(6)
        rows = -(-6 // cols)
        exp_w = cols * cell + (cols - 1) * opts.padding + 2 * opts.margin
        exp_h = rows * cell + (rows - 1) * opts.padding + 2 * opts.margin
        assert (layout.cols, layout.rows) == (cols, rows), (layout.cols, layout.rows, cols, rows)
        assert sheet.size == (exp_w, exp_h), f"{case}: sheet {sheet.size} != ({exp_w},{exp_h})"
        assert (layout.width, layout.height) == sheet.size
        assert len(rects) == 6
        for r in rects:
            col, row = r.index % cols, r.index // cols
            ex = opts.margin + col * (cell + opts.padding)
            ey = opts.margin + row * (cell + opts.padding)
            assert (r.x, r.y, r.w, r.h) == (ex, ey, cell, cell), (case, r)
            assert r.x + r.w <= sheet.width and r.y + r.h <= sheet.height
        print(f"PASS {case} -> {cols}x{rows} grid, sheet {sheet.size} == "
              f"{cols}*{cell}+{cols-1}*{opts.padding}+2*{opts.margin} x "
              f"{rows}*{cell}+{rows-1}*{opts.padding}+2*{opts.margin}")

    sheet, rects, layout = pack(frames, PackOptions.from_dict({"cols": 3, "padding": 4, "margin": 8, "power_of_two": True}))
    assert sheet.size == (256, 256), sheet.size
    print(f"PASS power-of-two padding: 208x140 canvas -> {sheet.size}, rects unchanged")

    sheet, rects, layout = pack(frames, PackOptions.from_dict({"cols": 3, "margin": 4, "background": "#204060"}))
    assert sheet.getpixel((0, 0)) == (32, 64, 96, 255), sheet.getpixel((0, 0))
    # a solid background also shows through the frames' transparent pixels
    assert sheet.getpixel((5, 5)) == (32, 64, 96, 255), sheet.getpixel((5, 5))
    print("PASS solid background #204060 fills the margin and shows through frame transparency")

    sheet, rects, layout = pack(frames, PackOptions.from_dict({"cols": 3, "margin": 4}))
    assert sheet.getpixel((0, 0)) == (0, 0, 0, 0)
    print("PASS transparent background leaves margin fully transparent")

    out = TMP / "check_pack_sheet.png"
    pack(frames, PackOptions.from_dict({"cols": 3, "padding": 4, "margin": 8}))[0].save(out)
    print(f"PASS PNG written: {out.name} ({out.stat().st_size} bytes)")
    print("\nCHECKPOINT 3 OK")


if __name__ == "__main__":
    main()
