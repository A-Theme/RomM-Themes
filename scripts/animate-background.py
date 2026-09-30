#!/usr/bin/env python3
"""Turn a theme's still background into an animated one.

The client already gives `motion` — drift, pan and zoom — for free, at no
texture cost. A sprite sheet is only worth its 48 MB budget when it does
something motion cannot: light that changes, and things that move independently
of the frame. So every effect here is keyed off the art's own luminance, or
drawn over it, rather than moving the whole image about.

Every effect loops seamlessly: intensity comes from a sine over the cycle and
particles wrap by exactly their travel, so frame N-1 leads back into frame 0.

    ./scripts/animate-background.py "Jellyglass" --effect glow_pulse
    ./scripts/animate-background.py "Frostbitten" --effect snow --frames 32 --fps 10

It writes sheet.png into the theme folder, adds background.animation to its
theme.json, and reports what the client and the catalogue will make of it.
"""
from __future__ import annotations

import argparse
import json
import math
import random
import sys
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter

REPO = Path(__file__).resolve().parent.parent
TOOL = REPO / "tools" / "spritesheet-maker"
sys.path.insert(0, str(TOOL))

from app.extract import Frame                      # noqa: E402
from app.pack import PackOptions, pack             # noqa: E402
from app import romm as romm_mod                   # noqa: E402

CELL = (320, 180)      # 16:9, and 218 frames inside the texture budget


# ---------------------------------------------------------------- helpers

def luma_mask(img: Image.Image, floor: int, ceiling: int = 255) -> Image.Image:
    """A mask of the bright parts, so an effect lands on what already glows."""
    grey = img.convert("L")
    span = max(ceiling - floor, 1)
    return grey.point(lambda v: 0 if v < floor else min(255, int((v - floor) * 255 / span)))


def add(base: Image.Image, layer: Image.Image, amount: float, gain: float = 1.0) -> Image.Image:
    """Screen `layer` over `base`, amplified by `gain` first.

    Gain is the difference between an effect you can see and one that costs
    5 MB of texture to change the picture by one level out of 255: a blurred
    glow pulled out of dark art is itself dark, so screening it back at full
    opacity does almost nothing until it is brightened.
    """
    if amount <= 0:
        return base
    scale = min(max(amount, 0.0), 1.0) * gain
    lifted = layer.point(lambda v: min(255, int(v * scale)))
    return ImageChops.screen(base, lifted.convert(base.mode))


def dip(base: Image.Image, layer_mask: Image.Image, depth: float) -> Image.Image:
    """Darken where the mask is, by up to `depth`.

    A glow that dips and returns reads the same as one that flares, and it
    cannot break the brightness ceiling: the still image stays the brightest
    frame in the sheet, and the still is what the theme's dim was tuned on.
    """
    if depth <= 0:
        return base
    shade = layer_mask.point(lambda v: 255 - int(v * min(depth, 1.0)))
    return ImageChops.multiply(base, Image.merge("RGB", (shade, shade, shade)))


def wave(t: float, phase: float = 0.0) -> float:
    """0..1 and back, once per cycle — the reason a loop does not jump."""
    return (math.sin(2 * math.pi * (t + phase) - math.pi / 2) + 1) / 2


# ---------------------------------------------------------------- effects

def fx_glow_pulse(base, t, o):
    """Bioluminescence: the bright parts breathe, everything else holds still."""
    mask = luma_mask(base, o.threshold).filter(ImageFilter.GaussianBlur(o.blur))
    if o.polarity == "down":
        return dip(base, mask, o.amount * wave(t))
    glow = Image.composite(base, Image.new("RGB", base.size, (0, 0, 0)), mask)
    return add(base, glow.filter(ImageFilter.GaussianBlur(o.blur)), o.amount * wave(t), o.gain)


def fx_flicker(base, t, o):
    """A flame: two sines that do not share a period, so it never looks metered."""
    mask = luma_mask(base, o.threshold)
    glow = Image.composite(base, Image.new("RGB", base.size, (0, 0, 0)), mask)
    glow = glow.filter(ImageFilter.GaussianBlur(o.blur))
    # 1 and 3 cycles per loop: irregular to the eye, still seamless
    flame = 0.55 * wave(t) + 0.45 * wave(t * 3.0, 0.31)
    if o.polarity == "down":
        return dip(base, mask.filter(ImageFilter.GaussianBlur(o.blur)), o.amount * flame)
    return add(base, glow, o.amount * flame, o.gain)


def fx_beam_sweep(base, t, o):
    """Light that travels: a soft band walks the frame, lifting what it crosses."""
    w, h = base.size
    mask = luma_mask(base, o.threshold)
    glow = Image.composite(base, Image.new("RGB", base.size, (0, 0, 0)), mask)
    glow = glow.filter(ImageFilter.GaussianBlur(o.blur))
    band = Image.new("L", (w, h), 0)
    d = ImageDraw.Draw(band)
    centre = (t * (w + 2 * o.band)) - o.band       # travels fully off both ends
    for i in range(o.band):
        v = int(255 * math.sin(math.pi * i / o.band))
        d.line([(centre - o.band / 2 + i, 0), (centre - o.band / 2 + i - h * 0.3, h)], fill=v, width=2)
    lit = ImageChops.multiply(glow, Image.merge("RGB", (band, band, band)))
    return add(add(base, glow, o.amount * 0.35, o.gain), lit, o.amount, o.gain)


def fx_hue_drift(base, t, o):
    """Nebula colour crawl: rotate hue where the art is already saturated."""
    hsv = base.convert("HSV")
    h, s, v = hsv.split()
    shift = int(o.degrees / 360 * 255 * math.sin(2 * math.pi * t))
    h = h.point(lambda p: (p + shift) % 256)
    drifted = Image.merge("HSV", (h, s, v)).convert("RGB")
    # only where there is colour to move, so greys stay put
    return Image.composite(drifted, base, s.point(lambda p: min(255, int(p * o.amount * 2))))


def _particles(base, t, o, kind):
    """Motes, snow or embers, drifting with a wrap so the loop closes."""
    w, h = base.size
    layer = Image.new("RGB", (w, h), (0, 0, 0))
    d = ImageDraw.Draw(layer)
    rng = random.Random(o.seed)
    for _ in range(o.count):
        x0, y0 = rng.uniform(0, w), rng.uniform(0, h)
        size = rng.uniform(*o.size_range)
        speed = rng.uniform(0.6, 1.4)
        sway = rng.uniform(4, 14)
        tint = rng.choice(o.tints)
        phase = rng.random()
        if kind == "snow":
            x = (x0 + math.sin(2 * math.pi * (t + phase)) * sway) % w
            y = (y0 + t * h * speed) % h
        elif kind == "embers":
            x = (x0 + math.sin(2 * math.pi * (t + phase)) * sway) % w
            y = (y0 - t * h * speed) % h
        else:                                   # motes: slow diagonal drift
            x = (x0 + t * w * 0.25 * speed) % w
            y = (y0 + math.sin(2 * math.pi * (t + phase)) * sway) % h
        fade = 0.45 + 0.55 * wave(t, phase)     # twinkle, seamless
        c = tuple(int(ch * fade) for ch in tint)
        d.ellipse([x - size, y - size, x + size, y + size], fill=c)
    layer = layer.filter(ImageFilter.GaussianBlur(o.particle_blur))
    return add(base, layer, o.amount, o.gain)


def fx_snow(base, t, o):
    return _particles(base, t, o, "snow")


def fx_embers(base, t, o):
    return _particles(base, t, o, "embers")


def fx_motes(base, t, o):
    return _particles(base, t, o, "motes")


def _phase_field(size, cells_across, seed) -> Image.Image:
    """A coarse map of per-region phases, upscaled to the frame.

    Scattering dots and hoping they land on the lit pixels does not work: on
    this art the lights are a tenth of the frame, so most dots fall on sky.
    Giving every region its own phase modulates whatever light is already
    there, which is what a city looks like.
    """
    w, h = size
    small = Image.new("L", (cells_across, max(1, int(cells_across * h / w))))
    rng = random.Random(seed)
    small.putdata([rng.randrange(256) for _ in range(small.width * small.height)])
    return small.resize(size, Image.BILINEAR)


def fx_twinkle(base, t, o):
    """City lights: each region of the existing highlights runs on its own phase."""
    mask = luma_mask(base, o.threshold)
    phases = _phase_field(base.size, max(int(o.count), 4), o.seed)
    mod = phases.point(lambda p: int(255 * wave(t * 2, p / 255.0)))
    both = ImageChops.multiply(mask, mod)
    if o.polarity == "down":
        return dip(base, both, o.amount)
    lit = Image.composite(base, Image.new("RGB", base.size, (0, 0, 0)), both)
    return add(base, lit.filter(ImageFilter.GaussianBlur(o.blur)), o.amount, o.gain)


EFFECTS = {
    "glow_pulse": fx_glow_pulse, "flicker": fx_flicker, "beam_sweep": fx_beam_sweep,
    "hue_drift": fx_hue_drift, "snow": fx_snow, "embers": fx_embers,
    "motes": fx_motes, "twinkle": fx_twinkle,
}


# ---------------------------------------------------------------- driving

def over_ceiling(images, dim) -> float:
    """How far the worst frame is over the catalogue's brightness limits.

    1.0 means exactly at a limit, above that is over. The still was tuned to
    pass; an effect that adds light can push the frames past it, and the frames
    are what text sits on.
    """
    if dim is None:
        return 0.0
    worst = 0.0
    step = max(1, len(images) // romm_mod.MAX_FRAMES_SAMPLED)
    for i in range(0, len(images), step):
        for (_lbl, _q, _f, limit), v in zip(romm_mod.BRIGHTNESS_LIMITS,
                                            romm_mod.frame_brightness(images[i], dim)):
            worst = max(worst, v / limit)
    return worst


def fit_to_ceiling(base, opts, dim, render) -> tuple[list, float]:
    """Turn the effect down until the frames sit inside the brightness ceiling.

    Fitting beats hand-tuning ten themes: each one has its own dim and its own
    art, so the same amount is invisible on one and illegible on another.
    """
    amount = opts.amount
    for _ in range(6):
        opts.amount = amount
        frames = [render(base, i / opts.frames, opts) for i in range(opts.frames)]
        ratio = over_ceiling(frames, dim)
        if ratio <= 1.0 or amount < 0.02:
            return frames, amount
        amount *= min(0.75, 1.0 / ratio)      # aim just under, then re-check
    return frames, amount


def build(theme: Path, opts) -> dict:
    tj = theme / "theme.json"
    theme_data = json.loads(tj.read_text(encoding="utf-8"))
    bg = theme_data.setdefault("background", {})
    still = bg.get("image")
    if not still or not (theme / still).is_file():
        raise SystemExit(f"{theme.name}: no still background to animate")

    src = Image.open(theme / still).convert("RGB")
    if src.size != (1280, 720):
        src = src.resize((1280, 720), Image.LANCZOS)
    base = src.resize(opts.cell, Image.LANCZOS)

    chain = [EFFECTS[name] for name in opts.effect.split(",")]

    def render(img, t, o):
        for fx in chain:
            img = fx(img, t, o)
        return img

    asked = opts.amount
    images, used = fit_to_ceiling(base, opts, bg.get("dim"), render)
    frames = [Frame(im.convert("RGBA"), int(1000 / opts.fps)) for im in images]

    cols = opts.cols or best_cols(opts.frames, opts.cell)
    sheet, rects, layout = pack(frames, PackOptions.from_dict(
        {"cols": cols, "padding": 0, "margin": 0, "background": "#000000"}))

    out = theme / opts.name
    flat = sheet.convert("RGB")
    if opts.colors:
        flat = flat.quantize(colors=opts.colors, method=Image.MEDIANCUT, dither=Image.FLOYDSTEINBERG)
    flat.save(out, optimize=True)

    bg["animation"] = {"kind": "sheet", "file": opts.name,
                       "frame_width": opts.cell[0], "frame_height": opts.cell[1],
                       "frames": opts.frames, "fps": opts.fps, "loop": True}
    tj.write_text(json.dumps(theme_data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    settings = romm_mod.RommOptions.from_dict(
        {"kind": "sheet", "file": opts.name, "frame_width": opts.cell[0],
         "frame_height": opts.cell[1], "fps": opts.fps, "loop": True,
         "background_image": still, "dim": bg.get("dim")})
    checks = romm_mod.check(settings, len(frames), sheet.width, sheet.height)
    checks += romm_mod.brightness_checks([f.image for f in frames], bg.get("dim"))
    return {"theme": theme.name, "sheet": out, "layout": layout, "checks": checks,
            "bytes": out.stat().st_size, "frames": len(frames),
            "amount": used, "asked": asked,
            "movement": movement(images)}


def movement(images) -> float:
    """Mean levels of change between the calmest and busiest frame.

    Reported because the failure mode of a subtle effect is costing 5 MB of
    texture to change the picture by one level out of 255.
    """
    from PIL import ImageChops, ImageStat
    return max(ImageStat.Stat(ImageChops.difference(images[0], f)).mean[0] for f in images)


def best_cols(n: int, cell) -> int:
    """The squarest grid with no spare cells, so no frame slot goes unused."""
    divisors = [c for c in range(1, n + 1) if n % c == 0]
    return min(divisors, key=lambda c: abs(c * cell[0] - (n // c) * cell[1]))


RECIPES = REPO / "scripts" / "animation-recipes.json"


def run_all(defaults) -> None:
    """Rebuild every animated background from the recipes file."""
    recipes = json.loads(RECIPES.read_text(encoding="utf-8"))
    for entry in recipes["themes"]:
        entry = {k: v for k, v in entry.items() if not k.startswith("_") and k != "why"}
        o = argparse.Namespace(**{**vars(defaults), **entry})
        o.cell = tuple(int(v) for v in o.cell.split("x")) if isinstance(o.cell, str) else o.cell
        o.size_range = (o.size_min, o.size_max)
        o.tints = [tuple(int(v) for v in g.split(",")) for g in o.tint.split(";")] \
            if isinstance(o.tint, str) else o.tint
        theme = REPO / "themes" / o.theme
        r = build(theme, o)
        L = r["layout"]
        note = "" if abs(r["amount"] - r["asked"]) < 1e-6 else \
            f" · fitted {r['asked']:.2f} -> {r['amount']:.2f}"
        errs = [c for c in r["checks"] if c["level"] == "error"]
        print(f"{r['theme']:20} {o.effect:22} {r['frames']:3} frames  "
              f"{L.width}x{L.height}  {r['bytes']/1048576:5.2f} MB  "
              f"movement {r['movement']:4.1f}{note}"
              + ("  ERRORS: " + "; ".join(e["message"][:60] for e in errs) if errs else ""))


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("theme", nargs="?", help="theme folder name, or --all")
    p.add_argument("--all", action="store_true", help="rebuild everything in animation-recipes.json")
    p.add_argument("--effect", default="glow_pulse",
                   help="one effect, or several comma-separated and applied in order: "
                        + ", ".join(sorted(EFFECTS)))
    p.add_argument("--frames", type=int, default=24)
    p.add_argument("--fps", type=int, default=10)
    p.add_argument("--cols", type=int, default=0)
    p.add_argument("--name", default="sheet.png")
    p.add_argument("--amount", type=float, default=0.5)
    p.add_argument("--threshold", type=int, default=150)
    p.add_argument("--blur", type=float, default=6.0)
    p.add_argument("--polarity", choices=("up", "down"), default="down",
                   help="down dips the light and returns, which cannot break the ceiling")
    p.add_argument("--gain", type=float, default=4.0,
                   help="brighten the effect layer before it is screened back")
    p.add_argument("--degrees", type=float, default=12.0)
    p.add_argument("--band", type=int, default=90)
    p.add_argument("--count", type=int, default=60)
    p.add_argument("--size-min", type=float, default=0.6)
    p.add_argument("--size-max", type=float, default=1.8)
    p.add_argument("--particle-blur", type=float, default=0.8)
    p.add_argument("--tint", default="255,255,255")
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--colors", type=int, default=0, help="quantise the sheet to N colours")
    p.add_argument("--cell", default="320x180")
    o = p.parse_args()
    for name in o.effect.split(","):
        if name not in EFFECTS:
            raise SystemExit(f"unknown effect {name!r}; have {', '.join(sorted(EFFECTS))}")
    o.cell = tuple(int(v) for v in o.cell.split("x"))
    o.size_range = (o.size_min, o.size_max)
    o.tints = [tuple(int(v) for v in group.split(",")) for group in o.tint.split(";")]

    if o.all:
        run_all(o)
        return
    if not o.theme:
        raise SystemExit("name a theme, or pass --all")
    theme = REPO / "themes" / o.theme
    if not theme.is_dir():
        raise SystemExit(f"no theme folder: {theme}")
    r = build(theme, o)
    L = r["layout"]
    fitted = "" if abs(r["amount"] - r["asked"]) < 1e-6 else \
        f" · amount {r['asked']:.2f} -> {r['amount']:.2f} to stay under the brightness ceiling"
    print(f"{r['theme']}: {r['frames']} frames of {L.cell_w}x{L.cell_h} in a {L.cols}x{L.rows} "
          f"grid -> {L.width}x{L.height}, {r['bytes']/1048576:.2f} MB · "
          f"movement {r['movement']:.1f} levels{fitted}")
    for c in r["checks"]:
        print(f"  [{c['level']}] {c['message']}")


if __name__ == "__main__":
    main()
