"""Frame list -> animated gif / webm / apng / zip of PNGs."""
from __future__ import annotations

import subprocess
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from .extract import Frame, ffmpeg_exe

FORMATS = ("gif", "webm", "apng", "zip")


class ExportError(Exception):
    """Raised when an export cannot be produced; surfaced to the UI as a 400."""


@dataclass
class ExportOptions:
    format: str = "gif"
    fps: float | None = None       # None => keep each frame's own duration
    loop: int = 0                  # 0 = forever
    dither: bool = True            # GIF: Floyd-Steinberg + adaptive palette
    palette_colors: int = 256      # GIF palette size when dithering

    @classmethod
    def from_dict(cls, d: dict | None) -> "ExportOptions":
        d = d or {}

        def num(key, cast, default):
            v = d.get(key, default)
            if v in (None, ""):
                return default
            try:
                return cast(v)
            except (TypeError, ValueError):
                return default

        fmt = str(d.get("format") or "gif").lower()
        if fmt not in FORMATS:
            raise ExportError(f"Unknown export format {fmt!r}.")
        colors = max(min(int(num("palette_colors", int, 256)), 256), 2)
        return cls(
            format=fmt,
            fps=num("fps", float, None),
            loop=max(int(num("loop", int, 0)), 0),
            dither="dither" not in d or bool(d.get("dither")),
            palette_colors=colors,
        )


def _durations(frames: list[Frame], fps: float | None) -> list[int]:
    if fps and fps > 0:
        return [max(int(round(1000.0 / fps)), 1)] * len(frames)
    return [max(int(f.duration_ms), 1) for f in frames]


def _check(frames: list[Frame]) -> None:
    if not frames:
        raise ExportError("There are no frames to export.")


def _shared_palette(frames: list[Frame], colors: int) -> Image.Image:
    """One adaptive palette for the whole animation, so colours stay stable."""
    w = max(f.image.width for f in frames)
    strip = Image.new("RGB", (w, sum(f.image.height for f in frames)), (0, 0, 0))
    y = 0
    for f in frames:
        rgb = Image.new("RGB", f.image.size, (0, 0, 0))
        img = f.image.convert("RGBA")
        rgb.paste(img, (0, 0), img)
        strip.paste(rgb, (0, y))
        y += f.image.height
    return strip.quantize(colors=colors, method=Image.MEDIANCUT)


def export_gif(frames: list[Frame], out: Path, opts: ExportOptions) -> Path:
    _check(frames)
    durations = _durations(frames, opts.fps)
    # One index is reserved for transparency, so the palette holds colors-1 entries.
    colors = max(min(opts.palette_colors, 256) - 1, 2)
    transparent = colors
    palette = _shared_palette(frames, colors)
    pal_data = (palette.getpalette() or [])[: colors * 3]
    pal_data = pal_data + [0, 0, 0] * (colors + 1 - len(pal_data) // 3)
    dither = Image.FLOYDSTEINBERG if opts.dither else Image.NONE

    converted: list[Image.Image] = []
    for f in frames:
        img = f.image.convert("RGBA")
        rgb = Image.new("RGB", img.size, (0, 0, 0))
        rgb.paste(img, (0, 0), img)
        # dither is honoured here (unlike convert("P", palette=ADAPTIVE), which ignores it)
        q = rgb.quantize(palette=palette, dither=dither)
        q.putpalette(pal_data)
        mask = img.getchannel("A").point(lambda v: 255 if v < 128 else 0)
        if mask.getbbox() is not None:
            q.paste(transparent, (0, 0), mask)
        converted.append(q)

    converted[0].save(
        out,
        save_all=True,
        append_images=converted[1:],
        duration=durations,
        loop=opts.loop,
        optimize=False,
        disposal=2,
        transparency=transparent,
    )
    return out


def export_apng(frames: list[Frame], out: Path, opts: ExportOptions) -> Path:
    _check(frames)
    durations = _durations(frames, opts.fps)
    images = [f.image.convert("RGBA") for f in frames]
    images[0].save(
        out,
        format="PNG",
        save_all=True,
        append_images=images[1:],
        duration=durations,
        loop=opts.loop,
        disposal=1,   # APNG_DISPOSE_OP_BACKGROUND
        blend=0,      # APNG_BLEND_OP_SOURCE
        default_image=False,
    )
    return out


def export_zip(frames: list[Frame], out: Path, opts: ExportOptions) -> Path:
    _check(frames)
    durations = _durations(frames, opts.fps)
    width = max(len(str(len(frames) - 1)), 3)
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for i, (frame, ms) in enumerate(zip(frames, durations)):
            tmp = out.parent / f".frame_{i:0{width}d}.png"
            frame.image.convert("RGBA").save(tmp, format="PNG")
            zf.write(tmp, arcname=f"frame_{i:0{width}d}.png")
            tmp.unlink()
        lines = ["index,file,duration_ms"] + [
            f"{i},frame_{i:0{width}d}.png,{ms}" for i, ms in enumerate(durations)
        ]
        zf.writestr("frames.csv", "\n".join(lines) + "\n")
    return out


def export_webm(frames: list[Frame], out: Path, opts: ExportOptions) -> Path:
    _check(frames)
    exe = ffmpeg_exe()
    if not exe:
        raise ExportError(
            "ffmpeg is not installed, so WebM export is unavailable. "
            "GIF, APNG and ZIP still work."
        )
    durations = _durations(frames, opts.fps)
    fps = opts.fps if opts.fps and opts.fps > 0 else 1000.0 / max(
        sum(durations) / len(durations), 1.0
    )
    # Even dimensions keep VP9 happy with any input size.
    w = frames[0].image.width + (frames[0].image.width % 2)
    h = frames[0].image.height + (frames[0].image.height % 2)
    with tempfile.TemporaryDirectory(prefix="ssm-webm-") as tmp:
        tmpdir = Path(tmp)
        for i, frame in enumerate(frames):
            img = frame.image.convert("RGBA")
            if (img.width, img.height) != (w, h):
                canvas = Image.new("RGBA", (w, h), (0, 0, 0, 0))
                canvas.alpha_composite(img)
                img = canvas
            img.save(tmpdir / f"f_{i:06d}.png")
        cmd = [
            exe, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
            "-framerate", f"{fps:.6f}",
            "-i", str(tmpdir / "f_%06d.png"),
            "-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p", "-b:v", "0", "-crf", "28",
            "-loop", "0" if opts.loop == 0 else str(opts.loop),
            str(out),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0 or not out.exists():
            raise ExportError(f"ffmpeg failed to write WebM: {result.stderr.strip()[:400]}")
    return out


def export(frames: list[Frame], out_dir: Path, opts: ExportOptions | None = None) -> Path:
    """Write one export and return its path."""
    opts = opts or ExportOptions()
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"animation.{opts.format}"
    if opts.format == "gif":
        return export_gif(frames, target, opts)
    if opts.format == "apng":
        return export_apng(frames, out_dir / "animation.apng", opts)
    if opts.format == "webm":
        return export_webm(frames, target, opts)
    if opts.format == "zip":
        return export_zip(frames, out_dir / "frames.zip", opts)
    raise ExportError(f"Unknown export format {opts.format!r}.")
