#!/usr/bin/env python3
"""
Sort source art into what will make a good theme background and what will not.

    python3 scripts/triage-art.py "../Tinfoil-Themes/Midjourney Backups"
    python3 scripts/triage-art.py <dir> --report TRIAGE.md

Reads every image (and zip of images) under a directory and judges each against
what the client actually needs, writing the verdicts to a report so they are not
re-decided by eye every time - and so a session that never saw the picture can
still act on them.

The rules come from building themes against this client, not from taste:

ASPECT      The screen is 1280x720. Art within ~8% of 16:9 cover-crops with
            nothing lost. Much wider is a banner: usable, but it needs
            feathering and a bloom or it reads as a letterboxed mistake.
            Portrait cannot be cropped to 16:9 without losing the subject.

RESOLUTION  Below 1280 wide the art is being upscaled. Glowing line work
            survives 4x; photographic detail does not.

ANIMATION   A sheet is bounded by 48 MB of texture and 240 frames. One
            1280x720 frame is 3.6 MB, so full-size sheets are almost always
            refused - the budget here is computed at 320x180 and 640x360, the
            sizes worth shipping.

BRIGHTNESS  A bright, busy image needs a heavier dim, and past about p90 0.6
            the art ends up dimmed so far that there was little point choosing
            it. Dark art with bright accents is the easy case.

PALETTE     A theme needs 19 roles. Art with one hue gives nothing to map an
            accent to; art with no dark region gives nothing to sit text on.
"""
import sys, os, zipfile, tempfile, colorsys, json
from PIL import Image, ImageSequence

SCREEN_AR = 1280 / 720
MAX_ANIM_BYTES, MAX_ANIM_FRAMES = 48 * 1024 * 1024, 240
EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}

def judge(path):
    r = {"file": os.path.basename(path), "notes": [], "verdict": "good"}
    def demote(to, why):
        order = {"good": 0, "workable": 1, "reject": 2}
        if order[to] > order[r["verdict"]]: r["verdict"] = to
        r["notes"].append(why)
    try:
        im = Image.open(path)
    except Exception as e:
        r["verdict"] = "reject"; r["notes"] = [f"unreadable: {e}"]; return r

    W, H = im.size
    r["size"] = f"{W}x{H}"
    ar = W / H
    r["aspect"] = round(ar, 3)
    if abs(ar - SCREEN_AR) / SCREEN_AR <= 0.08:
        r["notes"].append("fits 16:9 with a crop of a few pixels")
    elif ar > SCREEN_AR:
        demote("workable", f"banner {ar:.2f}:1 - needs feathering and a bloom")
    else:
        demote("reject" if ar < 1.2 else "workable",
               f"taller than 16:9 ({ar:.2f}:1) - cropping loses the subject")

    if W < 640:   demote("workable", f"only {W}px wide - {1280/W:.1f}x upscale")
    elif W < 1280: demote("workable", f"{W}px wide - {1280/W:.1f}x upscale")

    frames = getattr(im, "n_frames", 1)
    if frames > 1:
        durs = []
        for f in ImageSequence.Iterator(im): durs.append(f.info.get("duration", 100) or 100)
        total = sum(durs) or 1
        r["animation"] = {"frames": frames, "ms": total, "fps": round(frames * 1000 / total, 1)}
        fits = [n for n, (fw, fh) in (("320x180", (320, 180)), ("640x360", (640, 360)))
                if frames * fw * fh * 4 <= MAX_ANIM_BYTES and frames <= MAX_ANIM_FRAMES]
        r["notes"].append(f"animated: {frames} frames, fits a sheet at "
                          + (", ".join(fits) if fits else "NO size - must be subsampled"))

    rgb = im.convert("RGB").resize((160, 90))
    raw = rgb.tobytes()
    px = [tuple(raw[i:i+3]) for i in range(0, len(raw), 3)]
    L = sorted(colorsys.rgb_to_hls(*[v/255 for v in p])[1] for p in px)
    p50, p90 = L[len(L)//2], L[int(len(L)*0.9)]
    r["lum"] = {"p50": round(p50, 2), "p90": round(p90, 2)}
    if p90 > 0.72:  demote("workable", f"very bright (p90 {p90:.2f}) - needs a heavy dim")
    if p50 < 0.04:  demote("workable", f"almost entirely black (p50 {p50:.2f}) - little to show")

    hues = {round(colorsys.rgb_to_hls(*[v/255 for v in p])[0] * 12)
            for p in px if colorsys.rgb_to_hls(*[v/255 for v in p])[2] > 0.25}
    r["hues"] = len(hues)
    if len(hues) < 2: demote("workable", "one hue - little to map 19 roles onto")
    if not any(l < 0.18 for l in L[:len(L)//4]):
        demote("workable", "no dark region for text to sit on")
    return r

def collect(root):
    out = []
    for dirpath, _, files in os.walk(root):
        for f in sorted(files):
            p = os.path.join(dirpath, f)
            if os.path.splitext(f)[1].lower() in EXT:
                out.append(p)
            elif f.lower().endswith(".zip"):
                td = tempfile.mkdtemp()
                try:
                    with zipfile.ZipFile(p) as z:
                        for n in z.namelist():
                            if os.path.splitext(n)[1].lower() in EXT and not n.startswith("__"):
                                z.extract(n, td); out.append(os.path.join(td, n))
                except zipfile.BadZipFile:
                    print(f"  skipped {f}: not a readable zip")
    return out

def main():
    if len(sys.argv) < 2: sys.exit(__doc__)
    root = sys.argv[1]
    files = collect(root)
    print(f"{len(files)} image(s) under {root}\n")
    rows = [judge(p) for p in files]
    for v in ("good", "workable", "reject"):
        group = [r for r in rows if r["verdict"] == v]
        if not group: continue
        print(f"=== {v.upper()}  ({len(group)})")
        for r in group:
            print(f"  {r['file'][:64]:<64} {r.get('size','?'):>10}")
            for n in r["notes"]: print(f"      - {n}")
        print()
    if "--report" in sys.argv:
        dest = sys.argv[sys.argv.index("--report") + 1]
        with open(dest, "w") as fh:
            fh.write("# Source art triage\n\nGenerated by `scripts/triage-art.py`. "
                     "Re-run it after adding art; do not edit by hand.\n\n")
            for v in ("good", "workable", "reject"):
                group = [r for r in rows if r["verdict"] == v]
                if not group: continue
                fh.write(f"## {v} ({len(group)})\n\n")
                for r in group:
                    fh.write(f"- **{r['file']}** - {r.get('size','?')}"
                             f"{', ' + str(r['animation']['frames']) + ' frames' if 'animation' in r else ''}\n")
                    for n in r["notes"]: fh.write(f"  - {n}\n")
                fh.write("\n")
        print(f"wrote {dest}")

main()
