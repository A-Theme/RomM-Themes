"""RomM Switch client contract for animated backgrounds.

A port of the parts of A-Theme/Theme-App's `romm-theme-editor.html` that decide
whether the client will actually play a sheet: `sheetCapacity`, `sheetSource`,
`animBytes`, `animationInUse` and the checks in `renderChecks`. The client
slices a sheet as a tight grid — `cols = sheet_w // frame_width`, no padding and
no outer margin — so a sheet packed with either would misalign every frame.
Same arithmetic here, so the warnings mean what the editor's mean.

The brightness ceiling comes from this repo's own `scripts/validate-themes.py`
instead: `dim` is tuned against the still background, but the frames are what
text actually sits on, so a sheet can be calm on frame 1 and far too bright
ninety frames later. Same percentiles, same limits, so the tool says it before
the validator does.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

MAX_ANIMATION_BYTES = 48 * 1024 * 1024
MAX_ANIMATION_FRAMES = 240
MAX_ANIMATION_FPS = 60
SCREEN_W, SCREEN_H = 1280, 720
SAFE_NAME = re.compile(r"^[A-Za-z0-9\-_. \[\]()+!]+$")

# 16:9 cell sizes that scale cleanly to the console's 1280x720 framebuffer.
CELL_PRESETS = {
    "320x180": (320, 180),
    "426x240": (426, 240),
    "640x360": (640, 360),
    "1280x720": (SCREEN_W, SCREEN_H),
}


# scripts/validate-themes.py: the catalogue's own observed ceiling for a dimmed
# background, as luminance percentiles. Kept identical so the two agree.
BRIGHTNESS_LIMITS = (
    ("90th percentile", 90, 1.00, 0.105),
    ("97th percentile", 97, 1.00, 0.210),
    ("95th percentile of the left 42%", 95, 0.42, 0.145),
)
MAX_FRAMES_SAMPLED = 16

_LINEAR = [(c / 255.0) / 12.92 if c / 255.0 <= 0.04045
           else (((c / 255.0) + 0.055) / 1.055) ** 2.4 for c in range(256)]


class RommError(Exception):
    """Raised for RomM settings that cannot produce a usable animation."""


@dataclass
class RommOptions:
    """The `background.animation` block, plus what is needed to check it."""

    kind: str = "sheet"            # sheet | gif
    file: str = "sheet.png"
    frame_width: int = 320
    frame_height: int = 180
    fps: int = 12
    loop: bool = True
    theme_name: str = ""
    author: str = ""
    background_image: str = ""     # the still fallback the client uses if it refuses
    dim: float | None = None

    @classmethod
    def from_dict(cls, d: dict | None) -> "RommOptions":
        d = d or {}

        def intval(key, default):
            try:
                return int(d.get(key) or default)
            except (TypeError, ValueError):
                return default

        kind = str(d.get("kind") or "sheet").lower()
        if kind not in ("sheet", "gif"):
            raise RommError(f"Unknown RomM animation kind {kind!r} — use 'sheet' or 'gif'.")
        dim = d.get("dim")
        try:
            dim = None if dim in (None, "") else float(dim)
        except (TypeError, ValueError):
            dim = None
        return cls(
            kind=kind,
            file=str(d.get("file") or ("sheet.png" if kind == "sheet" else "background.gif")),
            frame_width=intval("frame_width", 320),
            frame_height=intval("frame_height", 180),
            fps=intval("fps", 12),
            loop=bool(d.get("loop", True)),
            theme_name=str(d.get("theme_name") or ""),
            author=str(d.get("author") or ""),
            background_image=str(d.get("background_image") or ""),
            dim=dim,
        )


# --------------------------------------------------------------------------
# ports of the client's own arithmetic
# --------------------------------------------------------------------------

def sheet_capacity(frame_w: int, frame_h: int, sheet_w: int, sheet_h: int) -> int:
    """How many frames the sheet really holds — the client's `sheetCapacity`."""
    if frame_w <= 0 or frame_h <= 0 or sheet_w <= 0 or sheet_h <= 0:
        return 0
    cols, rows = sheet_w // frame_w, sheet_h // frame_h
    if cols <= 0 or rows <= 0:
        return 0
    return cols * rows


def sheet_source(frame_w: int, frame_h: int, index: int,
                 sheet_w: int, sheet_h: int) -> tuple[int, int, int, int]:
    """Source rect of one frame — the client's `sheetSource`, left-to-right."""
    if index < 0 or frame_w <= 0 or frame_h <= 0:
        return (0, 0, 0, 0)
    cols = sheet_w // frame_w if sheet_w > 0 else 0
    if cols <= 0 or index >= sheet_capacity(frame_w, frame_h, sheet_w, sheet_h):
        return (0, 0, 0, 0)
    return ((index % cols) * frame_w, (index // cols) * frame_h, frame_w, frame_h)


def client_rects(frame_w: int, frame_h: int, frames: int,
                 sheet_w: int, sheet_h: int) -> list[tuple[int, int, int, int]]:
    """Every frame rect the client would sample, in play order."""
    return [sheet_source(frame_w, frame_h, i, sheet_w, sheet_h) for i in range(frames)]


def anim_bytes(kind: str, frame_w: int, frame_h: int, frames: int) -> int:
    """Texture cost the client budgets — the editor's `animBytes`."""
    if kind == "sheet":
        return frame_w * frame_h * frames * 4
    if kind == "gif":
        return SCREEN_W * SCREEN_H * frames * 4
    return 0


def max_frames_for_cell(kind: str, frame_w: int, frame_h: int) -> int:
    """How many frames of this size fit the 48MB texture budget."""
    per = (frame_w * frame_h * 4) if kind == "sheet" else (SCREEN_W * SCREEN_H * 4)
    if per <= 0:
        return 0
    return min(MAX_ANIMATION_FRAMES, MAX_ANIMATION_BYTES // per)


def animation_in_use(opts: RommOptions, frames: int) -> bool:
    """Would the client play this, or fall back to the still image?"""
    if frames <= 0 or frames > MAX_ANIMATION_FRAMES:
        return False
    if anim_bytes(opts.kind, opts.frame_width, opts.frame_height, frames) > MAX_ANIMATION_BYTES:
        return False
    if opts.kind == "sheet" and not (opts.frame_width > 0 and opts.frame_height > 0):
        return False
    return bool(opts.file)


def name_problem(name: str) -> str | None:
    """The client's own asset-name rules (`nameProblem`)."""
    if not name:
        return None
    if len(name) > 180:
        return "longer than 180 characters"
    if "/" in name or "\\" in name:
        return "contains a path separator"
    if name in (".", ".."):
        return "is a directory reference"
    if not SAFE_NAME.match(name):
        return "contains characters the client will not accept"
    if name[0] in ". " or name[-1] in ". ":
        return "starts or ends with a dot or space"
    return None


# --------------------------------------------------------------------------
# brightness, as scripts/validate-themes.py measures it
# --------------------------------------------------------------------------

def _percentile(values: list[float], q: float) -> float:
    values = sorted(values)
    k = (len(values) - 1) * q / 100.0
    lo = int(k)
    hi = min(lo + 1, len(values) - 1)
    return values[lo] + (values[hi] - values[lo]) * (k - lo)


def frame_brightness(image, dim: float) -> list[float]:
    """The percentiles of BRIGHTNESS_LIMITS for one frame, dimmed.

    dim is a straight sRGB multiply, as render_theme_background does it, so the
    multiply happens on the channel values before linearising.
    """
    keep = 1.0 - dim
    rgb = image.convert("RGB")
    # tobytes() rather than getdata(): same pixels, no deprecation, and it does
    # not build a list of tuples for a 1280x720 frame.
    raw = rgb.tobytes()
    width = rgb.size[0]
    full: list[float] = []
    left: list[float] = []
    edge = int(width * 0.42)
    for i in range(0, len(raw), 3):
        r, g, b = raw[i], raw[i + 1], raw[i + 2]
        y = (0.2126 * _LINEAR[int(r * keep)]
             + 0.7152 * _LINEAR[int(g * keep)]
             + 0.0722 * _LINEAR[int(b * keep)])
        full.append(y)
        if ((i // 3) % width) < edge:
            left.append(y)
    return [_percentile(left if frac < 1.0 else full, q)
            for _, q, frac, _ in BRIGHTNESS_LIMITS]


def brightness_checks(images, dim: float | None) -> list[dict]:
    """Warn about frames that dim brighter than anything in the catalogue.

    Samples the same way the validator does — up to MAX_FRAMES_SAMPLED frames,
    evenly spaced — and reports the worst frame per limit.
    """
    if not images or dim is None:
        return []
    total = len(images)
    step = max(1, total // MAX_FRAMES_SAMPLED)
    worst: dict[str, tuple[float, int]] = {}
    for index in range(0, total, step):
        measured = frame_brightness(images[index], dim)
        for (label, _q, _frac, limit), value in zip(BRIGHTNESS_LIMITS, measured):
            if value > limit and value > worst.get(label, (0.0, 0))[0]:
                worst[label] = (value, index)
    out = []
    for label, _q, _frac, limit in BRIGHTNESS_LIMITS:
        if label in worst:
            value, index = worst[label]
            out.append({
                "level": "warning",
                "message": (f"Frame {index} dims to {value:.4f} at the {label}, over "
                            f"{limit} — dim {dim} suits the still image but not the "
                            f"frames, and text sits on the frames."),
            })
    return out


# --------------------------------------------------------------------------
# checks
# --------------------------------------------------------------------------

def check(opts: RommOptions, frames: int, sheet_w: int, sheet_h: int,
          padding: int = 0, margin: int = 0) -> list[dict]:
    """Errors / warnings / info in the editor's own terms, worst first."""
    out: list[dict] = []
    err = lambda m: out.append({"level": "error", "message": m})       # noqa: E731
    warn = lambda m: out.append({"level": "warning", "message": m})    # noqa: E731
    info = lambda m: out.append({"level": "info", "message": m})       # noqa: E731

    problem = name_problem(opts.file)
    if not opts.file:
        err("background.animation.file is empty — the client will ignore the animation.")
    elif problem:
        err(f'background.animation.file: "{opts.file}" {problem}.')

    if opts.kind == "sheet" and not (opts.frame_width > 0 and opts.frame_height > 0):
        err("A sheet needs frame width and height, or the client drops the animation.")
    if frames <= 0:
        err("An animation needs a frame count.")
    if frames > MAX_ANIMATION_FRAMES:
        err(f"{frames} frames is over the {MAX_ANIMATION_FRAMES}-frame cap.")

    used = anim_bytes(opts.kind, opts.frame_width, opts.frame_height, frames)
    if used > MAX_ANIMATION_BYTES:
        err(f"This animation needs {used / 1048576:.0f} MB of texture, over the "
            f"{MAX_ANIMATION_BYTES // 1048576} MB budget. The client will refuse it "
            f"and use the still image.")

    if opts.kind == "sheet":
        # The client slices at a fixed stride from (0,0): padding and margin
        # shift every frame but the first, so it would sample across cells.
        if padding:
            err(f"The sheet was packed with {padding}px padding between cells. "
                f"The client slices at a fixed {opts.frame_width}x{opts.frame_height} "
                f"stride from the top-left, so padding misaligns every frame after the "
                f"first. Pack with padding 0 for RomM.")
        if margin:
            err(f"The sheet was packed with a {margin}px outer margin. The client's "
                f"first frame starts at (0,0), so a margin shifts the whole grid. "
                f"Pack with margin 0 for RomM.")

        cap = sheet_capacity(opts.frame_width, opts.frame_height, sheet_w, sheet_h)
        cols = sheet_w // opts.frame_width if opts.frame_width else 0
        rows = sheet_h // opts.frame_height if opts.frame_height else 0
        if cap == 0:
            err(f"The sheet is {sheet_w}x{sheet_h}, smaller than one "
                f"{opts.frame_width}x{opts.frame_height} frame — nothing will play.")
        elif frames > cap:
            err(f"theme.json says {frames} frames but the sheet is {sheet_w}x{sheet_h}, "
                f"which holds {cap} ({cols}x{rows}). Frames past {cap} will not draw.")
        else:
            if sheet_w % opts.frame_width or sheet_h % opts.frame_height:
                warn(f"The sheet is {sheet_w}x{sheet_h}, not a whole number of "
                     f"{opts.frame_width}x{opts.frame_height} frames. The leftover strip "
                     f"is ignored.")
            if frames < cap:
                info(f"The sheet holds {cap} frames ({cols}x{rows}); theme.json uses {frames}.")

        # One cell is blown up to the whole 1280x720 background.
        if opts.frame_width > 0 and opts.frame_height > 0:
            cell_ar = opts.frame_width / opts.frame_height
            screen_ar = SCREEN_W / SCREEN_H
            if abs(cell_ar - screen_ar) > 0.01:
                warn(f"A {opts.frame_width}x{opts.frame_height} cell is {cell_ar:.2f}:1, but the "
                     f"client stretches one cell over the whole {SCREEN_W}x{SCREEN_H} screen "
                     f"({screen_ar:.2f}:1), so the art will be distorted. 16:9 cells "
                     f"({', '.join(CELL_PRESETS)}) scale cleanly.")

    if opts.kind == "gif":
        warn("Animated GIF needs SDL2_image 2.6+ on the client. A sheet always works, "
             "and is one texture no matter how many frames.")

    if not opts.background_image:
        warn("An animated theme should also ship a still background image — it is the "
             "fallback when the animation cannot be used.")

    if opts.fps < 1 or opts.fps > MAX_ANIMATION_FPS:
        warn(f"fps {opts.fps} is outside 1-{MAX_ANIMATION_FPS}; the client clamps it.")

    if not any(c["level"] == "error" for c in out):
        info(f"Texture cost {used / 1048576:.1f} MB of the {MAX_ANIMATION_BYTES // 1048576} MB "
             f"budget · {frames} frames at {opts.fps} fps = "
             f"{frames / max(opts.fps, 1):.2f}s per cycle"
             + ("" if opts.loop else " (plays once, then holds the last frame)"))
    return out


# --------------------------------------------------------------------------
# theme.json
# --------------------------------------------------------------------------

def animation_block(opts: RommOptions, frames: int) -> dict:
    """The `background.animation` object exactly as the editor writes it."""
    block: dict = {"kind": opts.kind, "file": opts.file}
    if opts.kind == "sheet":
        block["frame_width"] = opts.frame_width
        block["frame_height"] = opts.frame_height
    block["frames"] = frames
    block["fps"] = opts.fps
    # The themes in this repo write loop either way, so it is written either way
    # here too; the editor's reader treats a missing loop as true regardless.
    block["loop"] = bool(opts.loop)
    return block


def theme_json(opts: RommOptions, frames: int) -> str:
    """A theme.json fragment carrying this animation, ready to merge or drop in."""
    theme: dict = {}
    if opts.theme_name:
        theme["name"] = opts.theme_name
    if opts.author:
        theme["author"] = opts.author
    background: dict = {}
    if opts.background_image:
        background["image"] = opts.background_image
    if opts.dim is not None:
        background["dim"] = round(opts.dim, 2)
    background["animation"] = animation_block(opts, frames)
    theme["background"] = background
    return json.dumps(theme, indent=2) + "\n"


def parse_animation(text: str) -> dict:
    """Read an emitted theme.json fragment back (used to verify output)."""
    data = json.loads(text)
    anim = ((data.get("background") or {}).get("animation")) or {}
    if not anim:
        raise RommError("No background.animation block in that theme.json.")
    return anim
