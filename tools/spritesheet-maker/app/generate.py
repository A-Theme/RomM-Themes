"""Static image -> frame list. One transform per call, fed into the same packer."""
from __future__ import annotations

import math
from dataclasses import dataclass

from PIL import Image

from .extract import Frame

TRANSFORMS = ("pan", "rotate", "scale", "flip", "bounce", "opacity")
EASINGS = ("linear", "ease-in-out", "bounce")
LOOP_MODES = ("loop", "ping-pong")
DIRECTIONS = ("left", "right", "up", "down")


class GenerateError(Exception):
    """Raised for unusable transform settings; surfaced to the UI as a 400."""


@dataclass
class GenerateOptions:
    transform: str = "pan"
    frames: int = 12
    easing: str = "linear"
    loop_mode: str = "loop"
    duration_ms: int = 100
    nearest: bool = False
    # pan
    direction: str = "left"
    distance: int = 32
    # rotate
    degrees: float = 360.0
    # scale / pulse
    scale_min: float = 100.0
    scale_max: float = 150.0
    # flip / mirror
    flip_axis: str = "horizontal"   # horizontal | vertical | both
    # bounce
    bounce_height: int = 24
    # opacity
    opacity_min: float = 0.0
    opacity_max: float = 100.0

    @classmethod
    def from_dict(cls, d: dict | None) -> "GenerateOptions":
        d = d or {}

        def num(key, cast, default):
            v = d.get(key, default)
            if v in (None, ""):
                return default
            try:
                return cast(v)
            except (TypeError, ValueError):
                return default

        opts = cls(
            transform=str(d.get("transform") or "pan"),
            frames=max(int(num("frames", int, 12)), 1),
            easing=str(d.get("easing") or "linear"),
            loop_mode=str(d.get("loop_mode") or "loop"),
            duration_ms=max(int(num("duration_ms", int, 100)), 1),
            nearest=bool(d.get("nearest")),
            direction=str(d.get("direction") or "left"),
            distance=int(num("distance", int, 32)),
            degrees=num("degrees", float, 360.0),
            scale_min=num("scale_min", float, 100.0),
            scale_max=num("scale_max", float, 150.0),
            flip_axis=str(d.get("flip_axis") or "horizontal"),
            bounce_height=int(num("bounce_height", int, 24)),
            opacity_min=num("opacity_min", float, 0.0),
            opacity_max=num("opacity_max", float, 100.0),
        )
        if opts.transform not in TRANSFORMS:
            raise GenerateError(f"Unknown transform {opts.transform!r}.")
        if opts.easing not in EASINGS:
            raise GenerateError(f"Unknown easing {opts.easing!r}.")
        if opts.loop_mode not in LOOP_MODES:
            raise GenerateError(f"Unknown loop mode {opts.loop_mode!r}.")
        if opts.frames > 512:
            raise GenerateError("Frame count is capped at 512.")
        return opts


# ------------------------------------------------------------------- easing

def ease(t: float, kind: str) -> float:
    t = min(max(t, 0.0), 1.0)
    if kind == "ease-in-out":
        return t * t * (3.0 - 2.0 * t)
    if kind == "bounce":
        # Standard "easeOutBounce" curve.
        n1, d1 = 7.5625, 2.75
        if t < 1 / d1:
            return n1 * t * t
        if t < 2 / d1:
            t -= 1.5 / d1
            return n1 * t * t + 0.75
        if t < 2.5 / d1:
            t -= 2.25 / d1
            return n1 * t * t + 0.9375
        t -= 2.625 / d1
        return n1 * t * t + 0.984375
    return t


def progress_values(count: int, easing: str, loop_mode: str) -> list[float]:
    """Eased 0..1 progress per frame; ping-pong runs 0 -> 1 -> 0 over the set."""
    if count == 1:
        return [0.0]
    out: list[float] = []
    for i in range(count):
        if loop_mode == "ping-pong":
            half = (count - 1) / 2.0
            raw = i / half if i <= half else (count - 1 - i) / half
        else:
            raw = i / count          # loop: never repeats the first frame at the end
        out.append(ease(min(max(raw, 0.0), 1.0), easing))
    return out


# --------------------------------------------------------------- transforms

def _resample(nearest: bool) -> int:
    return Image.NEAREST if nearest else Image.BICUBIC


def _blank(size) -> Image.Image:
    return Image.new("RGBA", size, (0, 0, 0, 0))


def _pan(src: Image.Image, t: float, o: GenerateOptions) -> Image.Image:
    dx = dy = 0
    shift = int(round(o.distance * t))
    if o.direction == "left":
        dx = -shift
    elif o.direction == "right":
        dx = shift
    elif o.direction == "up":
        dy = -shift
    elif o.direction == "down":
        dy = shift
    else:
        raise GenerateError(f"Unknown pan direction {o.direction!r}.")
    out = _blank(src.size)
    # Wrap around so a full-distance pan of the image width tiles seamlessly.
    w, h = src.size
    ox, oy = dx % w if w else 0, dy % h if h else 0
    for px in (ox - w, ox):
        for py in (oy - h, oy):
            out.alpha_composite(src, (px, py))
    return out


def _rotate(src: Image.Image, t: float, o: GenerateOptions) -> Image.Image:
    angle = o.degrees * t
    return src.rotate(-angle, resample=_resample(o.nearest), expand=False)


def _scale(src: Image.Image, t: float, o: GenerateOptions) -> Image.Image:
    lo, hi = o.scale_min / 100.0, o.scale_max / 100.0
    if lo <= 0 or hi <= 0:
        raise GenerateError("Scale percentages must be greater than zero.")
    factor = lo + (hi - lo) * t
    w, h = src.size
    nw, nh = max(int(round(w * factor)), 1), max(int(round(h * factor)), 1)
    scaled = src.resize((nw, nh), _resample(o.nearest))
    # Every frame stays the source size: pad when scaled down, centre-crop when up.
    out = _blank(src.size)
    left, top = (w - nw) // 2, (h - nh) // 2
    if nw >= w and nh >= h:
        return scaled.crop((-left, -top, -left + w, -top + h))
    out.alpha_composite(scaled, (max(left, 0), max(top, 0)) if (nw <= w and nh <= h) else (0, 0))
    if nw > w or nh > h:
        out = scaled.crop((max(-left, 0), max(-top, 0), max(-left, 0) + w, max(-top, 0) + h))
    return out


def _flip(src: Image.Image, index: int, o: GenerateOptions) -> Image.Image:
    """Cycle through the mirror states, one per frame."""
    if o.flip_axis == "horizontal":
        states = [(False, False), (True, False)]
    elif o.flip_axis == "vertical":
        states = [(False, False), (False, True)]
    elif o.flip_axis == "both":
        states = [(False, False), (True, False), (True, True), (False, True)]
    else:
        raise GenerateError(f"Unknown flip axis {o.flip_axis!r}.")
    fx, fy = states[index % len(states)]
    out = src
    if fx:
        out = out.transpose(Image.FLIP_LEFT_RIGHT)
    if fy:
        out = out.transpose(Image.FLIP_TOP_BOTTOM)
    return out.copy()


def _bounce(src: Image.Image, t: float, o: GenerateOptions) -> Image.Image:
    offset = int(round(o.bounce_height * t))
    out = _blank(src.size)
    out.alpha_composite(src, (0, -offset))
    return out


def _opacity(src: Image.Image, t: float, o: GenerateOptions) -> Image.Image:
    lo, hi = o.opacity_min / 100.0, o.opacity_max / 100.0
    alpha = min(max(lo + (hi - lo) * t, 0.0), 1.0)
    out = src.copy()
    a = out.getchannel("A").point(lambda v: int(round(v * alpha)))
    out.putalpha(a)
    return out


def generate(image: Image.Image, opts: GenerateOptions | None = None) -> list[Frame]:
    """Turn one still image into an N-frame sequence."""
    opts = opts or GenerateOptions()
    src = image if image.mode == "RGBA" else image.convert("RGBA")
    ts = progress_values(opts.frames, opts.easing, opts.loop_mode)
    frames: list[Frame] = []
    for i, t in enumerate(ts):
        if opts.transform == "pan":
            img = _pan(src, t, opts)
        elif opts.transform == "rotate":
            img = _rotate(src, t, opts)
        elif opts.transform == "scale":
            img = _scale(src, t, opts)
        elif opts.transform == "flip":
            img = _flip(src, i, opts)
        elif opts.transform == "bounce":
            img = _bounce(src, t, opts)
        elif opts.transform == "opacity":
            img = _opacity(src, t, opts)
        else:
            raise GenerateError(f"Unknown transform {opts.transform!r}.")
        frames.append(Frame(img, opts.duration_ms))
    return frames
