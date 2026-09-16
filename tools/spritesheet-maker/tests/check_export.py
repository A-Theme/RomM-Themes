"""Step 7 check: gif / webm / apng / zip exports are real, readable files."""
import subprocess
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image

from app.export import ExportOptions, export
from app.extract import load_frames
from check_extract import DURATIONS, build_gif

TMP = Path(__file__).resolve().parent.parent / "tmp"
TMP.mkdir(parents=True, exist_ok=True)
OUT = TMP / "exports"


def main():
    gif = TMP / "check_extract.gif"
    if not gif.exists():
        build_gif(gif)
    frames = load_frames(gif)
    OUT.mkdir(exist_ok=True)

    # GIF, source timing preserved, dithered palette
    p = export(frames, OUT, ExportOptions.from_dict({"format": "gif", "dither": True}))
    with Image.open(p) as im:
        assert im.n_frames == 6, im.n_frames
        got = []
        for i in range(im.n_frames):
            im.seek(i)
            got.append(im.info.get("duration"))
    assert got == DURATIONS, got
    print(f"PASS gif: {p.name} {p.stat().st_size}B, 6 frames, durations {got} (source timing kept)")

    # GIF at a forced FPS + finite loop count, undithered
    kept = p.rename(OUT / "animation_source_timing.gif")
    p2 = export(frames, OUT, ExportOptions.from_dict(
        {"format": "gif", "fps": 20, "loop": 3, "dither": False}))
    with Image.open(p2) as im:
        assert im.n_frames == 6
        assert im.info.get("duration") == 50, im.info
        assert im.info.get("loop") == 3, im.info
    print(f"PASS gif: fps 20 -> 50ms per frame, loop count 3 written ({p2.stat().st_size}B)")

    # a gradient shows the dither/palette toggle doing real work
    grad = Image.new("RGBA", (64, 64))
    for y in range(64):
        for x in range(64):
            grad.putpixel((x, y), (x * 4, y * 4, 128, 255))
    from app.extract import Frame
    gframes = [Frame(grad, 100), Frame(grad.rotate(90), 100)]
    d_on = export(gframes, OUT, ExportOptions.from_dict({"format": "gif", "dither": True, "palette_colors": 16}))
    size_on = d_on.rename(OUT / "grad_dither.gif").stat().st_size
    d_off = export(gframes, OUT, ExportOptions.from_dict({"format": "gif", "dither": False, "palette_colors": 16}))
    size_off = d_off.rename(OUT / "grad_flat.gif").stat().st_size
    assert size_on != size_off, (size_on, size_off)
    print(f"PASS gif dither/palette toggle changes the encode: 16-colour gradient {size_on}B dithered vs {size_off}B flat")

    # exported GIF keeps transparency and frame placement
    from app.extract import load_frames as _lf
    rt = _lf(kept)
    assert len(rt) == 6
    for i, f in enumerate(rt):
        opaque = [(x, y) for x in range(64) for y in range(64) if f.image.getpixel((x, y))[3] > 0]
        x0, y0 = (i % 3) * 20 + 2, (i // 3) * 20 + 2
        assert len(opaque) == 256, (i, len(opaque))
        assert min(p[0] for p in opaque) == x0 and min(p[1] for p in opaque) == y0, (i, x0, y0)
    print("PASS gif round trip: transparency and per-frame placement survive the export")

    # APNG
    p3 = export(frames, OUT, ExportOptions.from_dict({"format": "apng", "fps": 12}))
    with Image.open(p3) as im:
        assert im.format == "PNG" and getattr(im, "is_animated", False), im.format
        assert im.n_frames == 6, im.n_frames
        assert im.info.get("duration") in (83, 84), im.info.get("duration")
        im.seek(3)
        rgba = im.convert("RGBA")
        assert rgba.getpixel((5, 25))[3] > 0, rgba.getpixel((5, 25))   # frame 3's square
        assert rgba.getpixel((50, 50))[3] == 0, rgba.getpixel((50, 50))  # still transparent
    print(f"PASS apng: {p3.name} {p3.stat().st_size}B, 6 frames @ ~83ms, alpha preserved")

    # ZIP of individual PNGs
    p4 = export(frames, OUT, ExportOptions.from_dict({"format": "zip"}))
    with zipfile.ZipFile(p4) as zf:
        names = sorted(zf.namelist())
        pngs = [n for n in names if n.endswith(".png")]
        assert len(pngs) == 6, names
        assert "frames.csv" in names
        csv = zf.read("frames.csv").decode().strip().splitlines()
        assert csv[0] == "index,file,duration_ms" and len(csv) == 7, csv
        assert csv[1].endswith(",80"), csv[1]
    assert not list(OUT.glob(".frame_*.png")), "temp frame files were left behind"
    print(f"PASS zip: {p4.name} {p4.stat().st_size}B, {len(pngs)} PNGs + frames.csv, no temp files left")

    # WebM via ffmpeg
    p5 = export(frames, OUT, ExportOptions.from_dict({"format": "webm", "fps": 12}))
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=codec_name,width,height,nb_read_packets",
         "-count_packets", "-of", "csv=p=0", str(p5)],
        capture_output=True, text=True,
    ).stdout.strip()
    codec, w, h, packets = probe.split(",")
    assert codec == "vp9", codec
    assert (int(w), int(h)) == (64, 64), (w, h)
    assert int(packets) == 6, packets
    print(f"PASS webm: {p5.name} {p5.stat().st_size}B, vp9 {w}x{h}, {packets} packets @ 12fps")

    print("\nSTEP 7 OK")


if __name__ == "__main__":
    main()
