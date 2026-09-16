"""Step 6 check: every transform / easing / loop mode produces sane frames."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image, ImageChops

from app.generate import EASINGS, LOOP_MODES, TRANSFORMS, GenerateOptions, ease, generate, progress_values
from app.pack import PackOptions, pack

TMP = Path(__file__).resolve().parent.parent / "tmp"
TMP.mkdir(parents=True, exist_ok=True)


def sample_image():
    im = Image.new("RGBA", (48, 48), (0, 0, 0, 0))
    im.paste((255, 80, 40, 255), (6, 8, 34, 26))     # deliberately asymmetric on both axes
    im.paste((40, 200, 255, 255), (20, 30, 44, 40))
    return im


def diff(a, b):
    return ImageChops.difference(a, b).getbbox() is not None


def main():
    src = sample_image()
    src.save(TMP / "check_generate_src.png")

    for name in TRANSFORMS:
        frames = generate(src, GenerateOptions.from_dict({"transform": name, "frames": 8}))
        assert len(frames) == 8, (name, len(frames))
        assert all(f.image.size == (48, 48) for f in frames), name
        assert all(f.image.mode == "RGBA" for f in frames), name
        moved = sum(1 for f in frames[1:] if diff(f.image, frames[0].image))
        assert moved >= 1, f"{name}: no frame differs from the first — transform did nothing"
        print(f"PASS transform {name}: 8 frames, 48x48, {moved}/7 differ from frame 0")

    for e in EASINGS:
        vals = progress_values(9, e, "loop")
        assert len(vals) == 9 and all(0.0 <= v <= 1.01 for v in vals), (e, vals)
        assert vals[0] == 0.0, (e, vals)
        print(f"PASS easing {e}: {[round(v, 3) for v in vals[:5]]}…")
    assert ease(0.5, "linear") == 0.5 and ease(0.5, "ease-in-out") == 0.5
    assert ease(1.0, "bounce") == 1.0

    pp = progress_values(9, "linear", "ping-pong")
    assert pp[0] == 0.0 and abs(pp[4] - 1.0) < 1e-9 and pp[-1] == 0.0, pp
    print(f"PASS ping-pong progress rises to 1.0 mid-sequence and returns: {[round(v, 2) for v in pp]}")
    lp = progress_values(8, "linear", "loop")
    assert lp[-1] < 1.0, lp
    print(f"PASS loop progress stops short of 1.0 so the cycle does not stutter: {[round(v, 3) for v in lp]}")

    left = generate(src, GenerateOptions.from_dict({"transform": "pan", "frames": 4, "direction": "left", "distance": 16}))
    right = generate(src, GenerateOptions.from_dict({"transform": "pan", "frames": 4, "direction": "right", "distance": 16}))
    assert diff(left[1].image, right[1].image), "pan left and right produced the same frame"
    print("PASS pan direction changes the result (left != right)")

    fade = generate(src, GenerateOptions.from_dict(
        {"transform": "opacity", "frames": 5, "opacity_min": 0, "opacity_max": 100}))
    a_first = fade[0].image.getchannel("A").getextrema()[1]
    a_last = fade[-1].image.getchannel("A").getextrema()[1]
    assert a_first == 0 and a_last > a_first, (a_first, a_last)
    print(f"PASS opacity fade 0%→100%: max alpha {a_first} → {a_last}")

    flip = generate(src, GenerateOptions.from_dict({"transform": "flip", "frames": 4, "flip_axis": "both"}))
    assert diff(flip[0].image, flip[1].image) and diff(flip[1].image, flip[2].image)
    assert not diff(flip[0].image, src), "flip cycle should start on the unflipped frame"
    print("PASS flip cycle (both axes): 4 distinct mirror states, starting unflipped")

    rot = generate(src, GenerateOptions.from_dict(
        {"transform": "rotate", "frames": 12, "degrees": 360, "loop_mode": "loop"}))
    assert diff(rot[3].image, rot[0].image)
    print("PASS rotate 360° over 12 frames")

    sheet, rects, layout = pack(generate(src, GenerateOptions.from_dict({"transform": "bounce", "frames": 9})),
                                PackOptions.from_dict({"auto_square": True, "padding": 2}))
    assert layout.cols == 3 and layout.rows == 3 and sheet.size == (3 * 48 + 2 * 2, 3 * 48 + 2 * 2)
    print(f"PASS generated frames feed the same packer: 9 frames -> {layout.cols}x{layout.rows}, {sheet.size}")

    print("\nSTEP 6 OK")


if __name__ == "__main__":
    main()
