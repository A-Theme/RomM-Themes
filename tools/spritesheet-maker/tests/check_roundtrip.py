"""Done-when check: .gif, .mp4 and .png each round-trip
upload -> sheet -> slice -> preview -> export, with step 4's pixel identity holding."""
import io
import json
import os
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image, ImageChops

BASE = os.environ.get("SSM_BASE", "http://127.0.0.1:8731")
TMP = Path(__file__).resolve().parent.parent / "tmp"
TMP.mkdir(parents=True, exist_ok=True)


def curl(*args):
    return subprocess.run(["curl", "-s", *args], capture_output=True, text=True).stdout


def post(url, body):
    return json.loads(curl("-X", "POST", "-H", "Content-Type: application/json",
                           "-d", json.dumps(body), f"{BASE}{url}"))


def get_bytes(url):
    return urllib.request.urlopen(f"{BASE}{url}").read()


def frames_of(job, n):
    return [Image.open(io.BytesIO(get_bytes(f"/api/jobs/{job}/frames/{i}.png"))).convert("RGBA")
            for i in range(n)]


def roundtrip(name, path, frame_opts, layout_opts, export_fmt):
    print(f"\n--- {name} ({path.name}) ---")
    up = json.loads(curl("-F", f"file=@{path}", f"{BASE}/api/upload"))
    job = up["job_id"]
    ex = post(f"/api/jobs/{job}/extract", {"frame": frame_opts})
    n = ex["frame_count"]
    print(f"  upload -> extract: {n} frames, {ex['frames'][0]['w']}x{ex['frames'][0]['h']}")
    assert n > 0

    sheet = post(f"/api/jobs/{job}/sheet",
                 {"layout": layout_opts, "webp": True, "atlas": {"format": "plain"}})
    L = sheet["layout"]
    assert sheet["atlas_verified"]
    print(f"  -> sheet {L['width']}x{L['height']} grid {L['cols']}x{L['rows']} "
          f"+ webp + verified atlas")

    before = frames_of(job, n)
    png = TMP / f"rt_{name}.png"
    png.write_bytes(get_bytes(sheet["urls"]["png"]))

    geo = {"mode": "grid", "cols": L["cols"], "rows": L["rows"],
           "padding": L["padding"], "margin": L["margin"], "count": n}
    sl = json.loads(curl("-F", f"file=@{png}", "-F", f"geometry={json.dumps(geo)}", f"{BASE}/api/slice"))
    assert sl["frame_count"] == n, (sl["frame_count"], n)
    after = frames_of(sl["job_id"], n)
    for i, (a, b) in enumerate(zip(before, after)):
        assert a.size == b.size, (i, a.size, b.size)
        assert ImageChops.difference(a, b).getbbox() is None, f"{name}: frame {i} changed in the round trip"
    print(f"  -> slice + preview frames: {n}/{n} pixel-identical to the pre-sheet frames")

    exp = post(f"/api/jobs/{sl['job_id']}/export", {"export": {"format": export_fmt, "fps": 12}})
    data = get_bytes(exp["url"])
    assert len(data) == exp["size_bytes"] > 0
    if export_fmt == "zip":
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            assert len([x for x in zf.namelist() if x.endswith(".png")]) == n
    else:
        with Image.open(io.BytesIO(data)) as im:
            assert getattr(im, "n_frames", 1) == n, (export_fmt, im.n_frames, n)
    print(f"  -> export {export_fmt}: {exp['file']} {exp['size_bytes']}B, {n} frames  ROUND TRIP OK")


def main():
    gif = TMP / "check_extract.gif"
    mp4 = TMP / "check_video.mp4"
    png = TMP / "check_generate_src.png"
    for p in (gif, mp4, png):
        assert p.exists(), f"missing fixture {p}"

    roundtrip("gif", gif, {}, {"cols": 3, "padding": 4, "margin": 8}, "gif")
    roundtrip("mp4", mp4, {"fps": 6, "max_frames": 9, "scale_mode": "percent", "scale_percent": 25},
              {"auto_square": True, "padding": 2, "margin": 2}, "zip")

    # a still image goes through the generator on its way to the sheet
    print("\n--- png (still -> generated frames) ---")
    up = json.loads(curl("-F", f"file=@{png}", f"{BASE}/api/upload"))
    job = up["job_id"]
    gen = post(f"/api/jobs/{job}/generate",
               {"generate": {"transform": "rotate", "frames": 8, "degrees": 360,
                             "easing": "linear", "loop_mode": "loop", "duration_ms": 80}})
    n = gen["frame_count"]
    assert n == 8
    sheet = post(f"/api/jobs/{job}/sheet",
                 {"layout": {"cols": 4, "padding": 2, "margin": 2}, "atlas": {"format": "godot"}})
    L = sheet["layout"]
    print(f"  upload -> generate {n} rotate frames -> sheet {L['width']}x{L['height']} + godot atlas")
    before = frames_of(job, n)
    sp = TMP / "rt_png.png"
    sp.write_bytes(get_bytes(sheet["urls"]["png"]))
    geo = {"mode": "cell", "cell_w": L["cell_w"], "cell_h": L["cell_h"],
           "padding": L["padding"], "margin": L["margin"], "count": n}
    sl = json.loads(curl("-F", f"file=@{sp}", "-F", f"geometry={json.dumps(geo)}", f"{BASE}/api/slice"))
    after = frames_of(sl["job_id"], n)
    for i, (a, b) in enumerate(zip(before, after)):
        assert ImageChops.difference(a, b).getbbox() is None, f"png: frame {i} changed"
    print(f"  -> slice by cell size: {n}/{n} pixel-identical")
    exp = post(f"/api/jobs/{sl['job_id']}/export", {"export": {"format": "apng", "fps": 12}})
    with Image.open(io.BytesIO(get_bytes(exp["url"]))) as im:
        assert im.n_frames == n
    print(f"  -> export apng: {exp['file']} {exp['size_bytes']}B, {n} frames  ROUND TRIP OK")

    print("\nALL ROUND TRIPS OK")


if __name__ == "__main__":
    main()
