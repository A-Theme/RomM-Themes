"""Input decoding: gif / apng / animated webp / video / static image -> list[Frame]."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from PIL import Image, ImageSequence

DEFAULT_DURATION_MS = 100

ANIMATION_EXTS = {".gif", ".apng", ".png", ".webp"}
VIDEO_EXTS = {".mp4", ".webm", ".mov", ".avi", ".mkv", ".m4v"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".apng", ".tif", ".tiff"}
SUPPORTED_EXTS = sorted(VIDEO_EXTS | IMAGE_EXTS)


@dataclass
class Frame:
    """A single animation frame: an RGBA image plus how long it shows for."""

    image: Image.Image
    duration_ms: int = DEFAULT_DURATION_MS

    def copy(self) -> "Frame":
        return Frame(self.image.copy(), self.duration_ms)


@dataclass
class FrameOptions:
    """Frame-level controls applied after decoding."""

    fps: float | None = None            # resample to this frame rate
    max_frames: int | None = None       # hard cap (uniform subsample)
    trim_mode: str = "none"             # none | seconds | frames
    trim_start: float = 0.0
    trim_end: float | None = None       # exclusive; None = to the end
    scale_mode: str = "none"            # none | percent | exact
    scale_percent: float = 100.0
    scale_w: int | None = None
    scale_h: int | None = None
    nearest: bool = False               # nearest-neighbour resampling (pixel art)

    @classmethod
    def from_dict(cls, d: dict | None) -> "FrameOptions":
        d = d or {}
        def num(key, cast, default=None):
            v = d.get(key, default)
            if v in (None, ""):
                return default
            try:
                return cast(v)
            except (TypeError, ValueError):
                return default
        return cls(
            fps=num("fps", float),
            max_frames=num("max_frames", int),
            trim_mode=str(d.get("trim_mode") or "none"),
            trim_start=num("trim_start", float, 0.0) or 0.0,
            trim_end=num("trim_end", float),
            scale_mode=str(d.get("scale_mode") or "none"),
            scale_percent=num("scale_percent", float, 100.0) or 100.0,
            scale_w=num("scale_w", int),
            scale_h=num("scale_h", int),
            nearest=bool(d.get("nearest")),
        )


class ExtractError(Exception):
    """Raised for unusable input; surfaced to the UI as a 400."""


ProgressFn = Callable[[float, str], None]


def _noop(_pct: float, _msg: str) -> None:
    return None


# --------------------------------------------------------------------------
# animation decoding
# --------------------------------------------------------------------------

def _frame_duration(im: Image.Image) -> int:
    raw = im.info.get("duration", 0)
    try:
        ms = int(round(float(raw)))
    except (TypeError, ValueError):
        ms = 0
    # GIF encoders write 0 for "as fast as possible"; browsers clamp it.
    return ms if ms > 0 else DEFAULT_DURATION_MS


def _composited_frames(im: Image.Image) -> list[Frame]:
    """Decode a multi-frame image into full, already-composited RGBA canvases.

    Pillow renders each frame onto its internal canvas while seeking *forward
    one step at a time*, applying the GIF/APNG disposal and blend ops. Copying
    the raw (often partial, palette-mode) frame instead is what produces
    ghosting, so every frame is converted to RGBA and copied out of the
    composited canvas, and the canvas is carried forward manually for the
    formats that hand back a partial region.
    """
    frames: list[Frame] = []
    canvas: Image.Image | None = None
    size = im.size
    for raw in ImageSequence.Iterator(im):
        duration = _frame_duration(raw)
        rendered = raw.convert("RGBA")
        if rendered.size != size:
            # Partial frame: paste it at its offset onto the running canvas.
            box = raw.info.get("bbox") or (0, 0, rendered.size[0], rendered.size[1])
            base = canvas.copy() if canvas is not None else Image.new("RGBA", size, (0, 0, 0, 0))
            base.alpha_composite(rendered, (int(box[0]), int(box[1])))
            rendered = base
        canvas = rendered
        frames.append(Frame(rendered.copy(), duration))
    if not frames:
        raise ExtractError("No frames could be decoded from this file.")
    return frames


def _is_animated(im: Image.Image) -> bool:
    return getattr(im, "n_frames", 1) > 1


# --------------------------------------------------------------------------
# video decoding
# --------------------------------------------------------------------------

def _tool(name: str) -> str | None:
    """Find an ffmpeg tool: on PATH, or sitting next to a packaged build.

    A one-file build has no PATH of its own, and the usual way people install
    ffmpeg on Windows is to drop the exe in a folder — often the same folder.
    """
    found = shutil.which(name)
    if found:
        return found
    # Beside the app itself, and - only in a packaged build - beside the
    # executable. Outside one, sys.executable is the Python interpreter, so
    # /usr/bin/python3 would make every tool in /usr/bin look bundled.
    roots = [Path(__file__).resolve().parent.parent]
    if getattr(sys, "frozen", False):
        roots.append(Path(sys.executable).resolve().parent)
    for base in roots:
        for candidate in (base / f"{name}.exe", base / name):
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate)
    return None


def ffmpeg_exe() -> str | None:
    return _tool("ffmpeg")


def ffprobe_exe() -> str | None:
    return _tool("ffprobe")


def _probe_with_ffmpeg(path: Path) -> dict:
    """Video metadata from ffmpeg itself, for when ffprobe is not around.

    The packaged bundle ships ffmpeg alone - ffprobe is another 140MB for three
    numbers - so the same numbers are read out of ffmpeg's own report, which it
    writes to stderr and then exits non-zero for having no output file.
    """
    exe = ffmpeg_exe()
    if not exe:
        return {}
    try:
        out = subprocess.run([exe, "-hide_banner", "-i", str(path)],
                             capture_output=True, text=True, timeout=60)
    except Exception:  # noqa: BLE001
        return {}
    text = out.stderr or ""
    info: dict = {}

    m = re.search(r"Duration:\s*(\d+):(\d\d):(\d\d(?:\.\d+)?)", text)
    if m:
        h, mnt, sec = int(m.group(1)), int(m.group(2)), float(m.group(3))
        info["duration"] = h * 3600 + mnt * 60 + sec

    video = re.search(r"Stream #\d+:\d+.*?: Video: .*", text)
    if video:
        line = video.group(0)
        size = re.search(r"(?<![\d])(\d{2,5})x(\d{2,5})(?![\d])", line)
        if size:
            info["width"], info["height"] = int(size.group(1)), int(size.group(2))
        rate = re.search(r"([\d.]+)\s+fps", line) or re.search(r"([\d.]+)\s+tbr", line)
        if rate:
            try:
                info["fps"] = float(rate.group(1))
            except ValueError:
                pass
    return info


def probe_video(path: Path) -> dict:
    """Return {width, height, duration, fps} for a video, best effort."""
    exe = ffprobe_exe()
    if not exe:
        return _probe_with_ffmpeg(path)
    cmd = [
        exe, "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height,avg_frame_rate,nb_frames",
        "-show_entries", "format=duration", "-of", "json", str(path),
    ]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        data = json.loads(out.stdout or "{}")
    except Exception:
        return _probe_with_ffmpeg(path)
    stream = (data.get("streams") or [{}])[0]
    fmt = data.get("format") or {}
    fps = None
    rate = stream.get("avg_frame_rate") or ""
    if "/" in rate:
        num, den = rate.split("/", 1)
        try:
            fps = float(num) / float(den) if float(den) else None
        except (TypeError, ValueError, ZeroDivisionError):
            fps = None
    duration = None
    try:
        duration = float(fmt.get("duration"))
    except (TypeError, ValueError):
        duration = None
    return {
        "width": stream.get("width"),
        "height": stream.get("height"),
        "fps": fps,
        "duration": duration,
    }


def extract_video_frames(
    path: Path,
    opts: FrameOptions,
    progress: ProgressFn = _noop,
) -> list[Frame]:
    """Decode a video into frames by writing PNGs to a temp dir with ffmpeg."""
    exe = ffmpeg_exe()
    if not exe:
        raise ExtractError(
            "ffmpeg is not installed, so video input is unavailable. "
            "Install ffmpeg and restart the server."
        )
    fps = opts.fps or 12.0
    if fps <= 0:
        raise ExtractError("Target FPS must be greater than zero for video input.")

    pre_args: list[str] = []
    post_args: list[str] = []
    if opts.trim_mode == "seconds":
        if opts.trim_start:
            pre_args += ["-ss", f"{opts.trim_start:.6f}"]
        if opts.trim_end is not None and opts.trim_end > opts.trim_start:
            post_args += ["-t", f"{opts.trim_end - opts.trim_start:.6f}"]

    with tempfile.TemporaryDirectory(prefix="ssm-video-") as tmp:
        tmpdir = Path(tmp)
        cmd = [exe, "-hide_banner", "-loglevel", "error", "-nostdin"]
        cmd += pre_args
        cmd += ["-i", str(path)]
        cmd += post_args
        cmd += ["-vf", f"fps={fps}", "-vsync", "0"]
        if opts.max_frames:
            # +8 gives trimming by frame index a little room to work with.
            cmd += ["-frames:v", str(int(opts.max_frames) + 8)]
        cmd += [str(tmpdir / "frame_%06d.png")]
        progress(10.0, "Decoding video with ffmpeg...")
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise ExtractError(f"ffmpeg failed to decode this video: {result.stderr.strip()[:400]}")
        files = sorted(tmpdir.glob("frame_*.png"))
        if not files:
            raise ExtractError("ffmpeg produced no frames — the trim range may be empty.")
        duration_ms = int(round(1000.0 / fps))
        frames: list[Frame] = []
        total = len(files)
        for i, f in enumerate(files):
            with Image.open(f) as im:
                frames.append(Frame(im.convert("RGBA").copy(), duration_ms))
            if i % 10 == 0:
                progress(10.0 + 60.0 * (i + 1) / total, f"Loading frame {i + 1}/{total}")
    return frames


# --------------------------------------------------------------------------
# frame operations
# --------------------------------------------------------------------------

def _timeline(frames: list[Frame]) -> tuple[list[float], float]:
    """Start time (seconds) of each frame plus the total duration."""
    starts: list[float] = []
    t = 0.0
    for f in frames:
        starts.append(t)
        t += max(f.duration_ms, 1) / 1000.0
    return starts, t


def trim_frames(frames: list[Frame], opts: FrameOptions) -> list[Frame]:
    if opts.trim_mode == "frames":
        start = max(int(opts.trim_start), 0)
        end = int(opts.trim_end) if opts.trim_end is not None else len(frames)
        end = min(max(end, start), len(frames))
        out = frames[start:end]
    elif opts.trim_mode == "seconds":
        starts, total = _timeline(frames)
        begin = max(float(opts.trim_start), 0.0)
        finish = float(opts.trim_end) if opts.trim_end is not None else total
        out = [f for f, s in zip(frames, starts) if begin <= s < finish]
    else:
        return frames
    if not out:
        raise ExtractError("The trim range selected zero frames.")
    return out


def resample_frames(frames: list[Frame], fps: float) -> list[Frame]:
    """Resample an irregular frame timeline to a uniform frame rate."""
    if fps <= 0 or not frames:
        return frames
    starts, total = _timeline(frames)
    step = 1.0 / fps
    duration_ms = int(round(step * 1000.0))
    out: list[Frame] = []
    t = 0.0
    idx = 0
    while t < total - 1e-9:
        while idx + 1 < len(frames) and starts[idx + 1] <= t + 1e-9:
            idx += 1
        out.append(Frame(frames[idx].image.copy(), duration_ms))
        t += step
    if not out:
        out = [Frame(frames[0].image.copy(), duration_ms)]
    return out


def cap_frames(frames: list[Frame], max_frames: int) -> list[Frame]:
    """Uniformly subsample down to max_frames, folding dropped time into duration."""
    if max_frames <= 0 or len(frames) <= max_frames:
        return frames
    n = len(frames)
    picks = [int(round(i * (n - 1) / (max_frames - 1))) if max_frames > 1 else 0
             for i in range(max_frames)]
    picks = sorted(set(picks))
    total_ms = sum(max(f.duration_ms, 1) for f in frames)
    per = max(int(round(total_ms / len(picks))), 1)
    return [Frame(frames[i].image.copy(), per) for i in picks]


def scale_frames(frames: list[Frame], opts: FrameOptions) -> list[Frame]:
    if opts.scale_mode == "none" or not frames:
        return frames
    resample = Image.NEAREST if opts.nearest else Image.LANCZOS
    out: list[Frame] = []
    for f in frames:
        w, h = f.image.size
        if opts.scale_mode == "percent":
            pct = max(float(opts.scale_percent), 1.0) / 100.0
            nw, nh = max(int(round(w * pct)), 1), max(int(round(h * pct)), 1)
        else:
            nw = int(opts.scale_w) if opts.scale_w else w
            nh = int(opts.scale_h) if opts.scale_h else h
            if opts.scale_w and not opts.scale_h:
                nh = max(int(round(h * (nw / w))), 1)
            elif opts.scale_h and not opts.scale_w:
                nw = max(int(round(w * (nh / h))), 1)
            nw, nh = max(nw, 1), max(nh, 1)
        if (nw, nh) == (w, h):
            out.append(f)
        else:
            out.append(Frame(f.image.resize((nw, nh), resample), f.duration_ms))
    return out


def apply_frame_options(
    frames: list[Frame],
    opts: FrameOptions,
    skip_trim: bool = False,
    skip_fps: bool = False,
) -> list[Frame]:
    """Trim -> resample -> cap -> scale. Video input handles trim/fps in ffmpeg."""
    if not skip_trim:
        frames = trim_frames(frames, opts)
    elif opts.trim_mode == "frames":
        frames = trim_frames(frames, opts)
    if opts.fps and not skip_fps:
        frames = resample_frames(frames, opts.fps)
    if opts.max_frames:
        frames = cap_frames(frames, int(opts.max_frames))
    frames = scale_frames(frames, opts)
    if not frames:
        raise ExtractError("No frames left after applying the frame options.")
    return frames


# --------------------------------------------------------------------------
# entry points
# --------------------------------------------------------------------------

def probe(path: Path) -> dict:
    """Describe an input file without fully decoding it."""
    ext = path.suffix.lower()
    if ext in VIDEO_EXTS:
        info = probe_video(path)
        info.update({"kind": "video", "animated": True, "frame_count": None})
        return info
    try:
        with Image.open(path) as im:
            n = getattr(im, "n_frames", 1)
            duration = im.info.get("duration", DEFAULT_DURATION_MS)
            return {
                "kind": "animation" if n > 1 else "image",
                "animated": n > 1,
                "frame_count": n,
                "width": im.size[0],
                "height": im.size[1],
                "format": im.format,
                "duration": (n * float(duration or DEFAULT_DURATION_MS)) / 1000.0,
                "fps": (1000.0 / float(duration)) if duration else None,
            }
    except Exception as exc:  # noqa: BLE001 - surfaced to the UI
        raise ExtractError(f"Could not read this file: {exc}") from None


def load_frames(path: Path, progress: ProgressFn = _noop) -> list[Frame]:
    """Decode any supported input into raw frames, before frame options."""
    ext = path.suffix.lower()
    if ext in VIDEO_EXTS:
        raise ExtractError("Video input must go through extract_video_frames().")
    progress(5.0, "Decoding image...")
    try:
        im = Image.open(path)
    except Exception as exc:  # noqa: BLE001
        raise ExtractError(f"Could not open this image: {exc}") from None
    with im:
        if _is_animated(im):
            frames = _composited_frames(im)
        else:
            frames = [Frame(im.convert("RGBA").copy(), DEFAULT_DURATION_MS)]
    progress(40.0, f"Decoded {len(frames)} frame(s)")
    return frames


def extract(
    path: Path,
    opts: FrameOptions | None = None,
    progress: ProgressFn = _noop,
) -> list[Frame]:
    """Decode `path` and apply the frame options, returning ready-to-pack frames."""
    opts = opts or FrameOptions()
    ext = path.suffix.lower()
    if ext in VIDEO_EXTS:
        frames = extract_video_frames(path, opts, progress)
        frames = apply_frame_options(frames, opts, skip_trim=True, skip_fps=True)
    else:
        frames = load_frames(path, progress)
        frames = apply_frame_options(frames, opts)
    progress(75.0, f"{len(frames)} frame(s) ready")
    return frames
