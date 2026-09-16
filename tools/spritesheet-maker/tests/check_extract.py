"""Checkpoint 2 verification: synthetic GIF -> extract -> frame count, durations, no ghosting."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image

from app.extract import FrameOptions, extract, load_frames

TMP = Path(__file__).resolve().parent.parent / "tmp"
TMP.mkdir(parents=True, exist_ok=True)
SIZE = 64
BOX = 16
DURATIONS = [80, 120, 160, 200, 240, 280]
COLORS = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0), (255, 0, 255), (0, 255, 255)]


def build_gif(path: Path):
    """6 frames, each a single coloured square in a different spot, transparent elsewhere."""
    frames = []
    for i, color in enumerate(COLORS):
        im = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
        x = (i % 3) * 20 + 2
        y = (i // 3) * 20 + 2
        im.paste(color + (255,), (x, y, x + BOX, y + BOX))
        frames.append(im)
    frames[0].save(
        path, save_all=True, append_images=frames[1:],
        duration=DURATIONS, loop=0, disposal=2, transparency=0,
    )
    return frames


def opaque_pixels(im):
    return {(x, y) for x in range(im.width) for y in range(im.height) if im.getpixel((x, y))[3] > 0}


def main():
    gif = TMP / "check_extract.gif"
    sources = build_gif(gif)
    frames = load_frames(gif)

    assert len(frames) == 6, f"expected 6 frames, got {len(frames)}"
    print(f"PASS frame count == 6 (got {len(frames)})")

    got = [f.duration_ms for f in frames]
    assert got == DURATIONS, f"expected durations {DURATIONS}, got {got}"
    print(f"PASS per-frame durations preserved: {got}")

    for i, (frame, src) in enumerate(zip(frames, sources)):
        assert frame.image.mode == "RGBA", f"frame {i} mode {frame.image.mode}"
        assert frame.image.size == (SIZE, SIZE), f"frame {i} size {frame.image.size}"
        opaque = opaque_pixels(frame.image)
        expected = opaque_pixels(src)
        assert opaque == expected, (
            f"frame {i}: ghosting — {len(opaque)} opaque px vs {len(expected)} expected "
            f"(extra: {len(opaque - expected)}, missing: {len(expected - opaque)})"
        )
        assert len(opaque) == BOX * BOX, f"frame {i}: {len(opaque)} opaque px, want {BOX*BOX}"
    print(f"PASS no ghosting: every frame has exactly one {BOX}x{BOX} opaque square at its own offset")

    for i, (frame, color) in enumerate(zip(frames, COLORS)):
        x = (i % 3) * 20 + 2
        y = (i // 3) * 20 + 2
        px = frame.image.getpixel((x + 2, y + 2))
        assert px[:3] == color, f"frame {i}: colour {px[:3]} != {color}"
    print("PASS per-frame colours correct (no palette bleed between frames)")

    png = TMP / "check_extract_static.png"
    Image.new("RGBA", (32, 24), (10, 20, 30, 255)).save(png)
    static_frames = extract(png, FrameOptions())
    assert len(static_frames) == 1 and static_frames[0].image.size == (32, 24)
    print("PASS static PNG extracts to 1 frame at native size")

    scaled = extract(gif, FrameOptions.from_dict({"scale_mode": "percent", "scale_percent": 50, "nearest": True}))
    assert all(f.image.size == (32, 32) for f in scaled), [f.image.size for f in scaled]
    print("PASS uniform 50% nearest-neighbour scale -> 32x32")

    resampled = extract(gif, FrameOptions.from_dict({"fps": 10}))
    total_s = sum(DURATIONS) / 1000.0
    assert abs(len(resampled) - round(total_s * 10)) <= 1, f"{len(resampled)} frames for {total_s}s @10fps"
    assert all(f.duration_ms == 100 for f in resampled)
    print(f"PASS fps resample: {total_s:.2f}s @ 10fps -> {len(resampled)} frames @ 100ms")

    capped = extract(gif, FrameOptions.from_dict({"max_frames": 3}))
    assert len(capped) == 3, len(capped)
    print("PASS max frame count 6 -> 3")

    trimmed = extract(gif, FrameOptions.from_dict({"trim_mode": "frames", "trim_start": 2, "trim_end": 5}))
    assert len(trimmed) == 3 and trimmed[0].duration_ms == DURATIONS[2]
    print("PASS trim by frame index [2,5) -> 3 frames starting at frame 2")

    trimmed_s = extract(gif, FrameOptions.from_dict({"trim_mode": "seconds", "trim_start": 0.2, "trim_end": 0.6}))
    # frame starts are 0.00 0.08 0.20 0.36 0.56 0.76s -> three fall in [0.2, 0.6)
    assert len(trimmed_s) == 3, len(trimmed_s)
    assert trimmed_s[0].duration_ms == DURATIONS[2]
    print("PASS trim by seconds [0.2,0.6) -> 3 frames starting at frame 2")

    print("\nCHECKPOINT 2 OK")


if __name__ == "__main__":
    main()
