"""Frame rect list -> sidecar metadata in each supported format.

Every writer takes the rect list that pack.py actually drew with, so the
coordinates describe the real sheet rather than a recomputed layout.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from . import romm as romm_mod
from .pack import FrameRect, Layout

FORMATS = {
    "phaser-hash": ("Phaser 3 JSON Hash", ".json"),
    "phaser-array": ("Phaser 3 JSON Array", ".json"),
    "godot": ("Godot SpriteFrames", ".tres"),
    "texturepacker": ("TexturePacker JSON", ".json"),
    "plain": ("Plain JSON", ".json"),
    "romm": ("RomM theme.json animation", ".json"),
}


class AtlasError(Exception):
    """Raised for an unknown atlas format; surfaced to the UI as a 400."""


@dataclass
class AtlasOptions:
    format: str = "plain"
    image_name: str = "sheet.png"
    animation_name: str = "animation"
    romm: dict = field(default_factory=dict)   # RomM animation settings, for the romm format

    @classmethod
    def from_dict(cls, d: dict | None) -> "AtlasOptions":
        d = d or {}
        fmt = str(d.get("format") or "plain")
        if fmt not in FORMATS:
            raise AtlasError(f"Unknown atlas format {fmt!r}.")
        return cls(
            format=fmt,
            image_name=Path(str(d.get("image_name") or "sheet.png")).name,
            animation_name=str(d.get("animation_name") or "animation"),
            romm=dict(d.get("romm") or {}),
        )


def frame_name(index: int, total: int) -> str:
    width = max(len(str(max(total - 1, 0))), 3)
    return f"frame_{index:0{width}d}"


def _frame_entry(r: FrameRect, total: int) -> dict:
    return {
        "filename": frame_name(r.index, total),
        "frame": {"x": r.x, "y": r.y, "w": r.w, "h": r.h},
        "rotated": False,
        "trimmed": False,
        "spriteSourceSize": {"x": 0, "y": 0, "w": r.w, "h": r.h},
        "sourceSize": {"w": r.w, "h": r.h},
        "duration": r.duration_ms,
    }


def _meta(layout: Layout, opts: AtlasOptions) -> dict:
    return {
        "app": "spritesheet-maker",
        "version": "1.0",
        "image": opts.image_name,
        "format": "RGBA8888",
        "size": {"w": layout.width, "h": layout.height},
        "scale": "1",
    }


def phaser_hash(rects: list[FrameRect], layout: Layout, opts: AtlasOptions) -> str:
    total = len(rects)
    frames = {frame_name(r.index, total): _frame_entry(r, total) for r in rects}
    for entry in frames.values():
        entry.pop("filename")
    return json.dumps({"frames": frames, "meta": _meta(layout, opts)}, indent=2)


def phaser_array(rects: list[FrameRect], layout: Layout, opts: AtlasOptions) -> str:
    total = len(rects)
    return json.dumps(
        {"frames": [_frame_entry(r, total) for r in rects], "meta": _meta(layout, opts)},
        indent=2,
    )


def texturepacker(rects: list[FrameRect], layout: Layout, opts: AtlasOptions) -> str:
    total = len(rects)
    frames = []
    for r in rects:
        entry = _frame_entry(r, total)
        entry["filename"] = f"{frame_name(r.index, total)}.png"
        entry.pop("duration")
        frames.append(entry)
    meta = _meta(layout, opts)
    meta["smartupdate"] = "spritesheet-maker"
    return json.dumps({"frames": frames, "meta": meta}, indent=2)


def plain(rects: list[FrameRect], layout: Layout, opts: AtlasOptions) -> str:
    return json.dumps(
        {
            "image": opts.image_name,
            "sheet": {"w": layout.width, "h": layout.height},
            "grid": {
                "cols": layout.cols,
                "rows": layout.rows,
                "cell_w": layout.cell_w,
                "cell_h": layout.cell_h,
                "padding": layout.padding,
                "margin": layout.margin,
            },
            "frames": [
                {
                    "frame_index": r.index,
                    "x": r.x,
                    "y": r.y,
                    "w": r.w,
                    "h": r.h,
                    "duration_ms": r.duration_ms,
                }
                for r in rects
            ],
        },
        indent=2,
    )


def godot(rects: list[FrameRect], layout: Layout, opts: AtlasOptions) -> str:
    """Godot 4 SpriteFrames .tres: one AtlasTexture sub-resource per frame."""
    total = len(rects)
    load_steps = total + 2
    lines = [
        f'[gd_resource type="SpriteFrames" load_steps={load_steps} format=3]',
        "",
        f'[ext_resource type="Texture2D" path="res://{opts.image_name}" id="1_sheet"]',
        "",
    ]
    for r in rects:
        lines += [
            f'[sub_resource type="AtlasTexture" id="Atlas_{r.index}"]',
            'atlas = ExtResource("1_sheet")',
            f"region = Rect2({r.x}, {r.y}, {r.w}, {r.h})",
            "",
        ]
    # Godot stores one speed for the animation, so use the mean frame duration.
    avg_ms = sum(r.duration_ms for r in rects) / max(total, 1)
    speed = 1000.0 / avg_ms if avg_ms else 10.0
    frame_entries = ", ".join(
        '{{\n"duration": {dur:.3f},\n"texture": SubResource("Atlas_{i}")\n}}'.format(
            dur=(r.duration_ms / avg_ms if avg_ms else 1.0), i=r.index
        )
        for r in rects
    )
    lines += [
        "[resource]",
        "animations = [{",
        f'"frames": [{frame_entries}],',
        f'"loop": true,',
        f'"name": &"{opts.animation_name}",',
        f'"speed": {speed:.3f}',
        "}]",
        "",
    ]
    return "\n".join(lines)


def romm_theme(rects: list[FrameRect], layout: Layout, opts: AtlasOptions) -> str:
    """theme.json for the RomM Switch client's animated background.

    The frame size defaults to the packed cell, and the file name to the sheet
    that was written, so the block describes this sheet rather than an ideal one.
    """
    spec = dict(opts.romm)
    spec.setdefault("file", opts.image_name)
    spec.setdefault("frame_width", layout.cell_w)
    spec.setdefault("frame_height", layout.cell_h)
    settings = romm_mod.RommOptions.from_dict(spec)
    return romm_mod.theme_json(settings, len(rects))


WRITERS = {
    "phaser-hash": phaser_hash,
    "phaser-array": phaser_array,
    "godot": godot,
    "texturepacker": texturepacker,
    "plain": plain,
    "romm": romm_theme,
}


def build(rects: list[FrameRect], layout: Layout, opts: AtlasOptions | None = None) -> str:
    opts = opts or AtlasOptions()
    writer = WRITERS.get(opts.format)
    if writer is None:
        raise AtlasError(f"Unknown atlas format {opts.format!r}.")
    if not rects:
        raise AtlasError("There are no frames to describe.")
    return writer(rects, layout, opts)


def sidecar_name(fmt: str) -> str:
    """What the emitted file is called. The RomM one is a theme.json, and
    calling it anything else invites someone to rename it wrongly."""
    if fmt == "romm":
        return "theme.json"
    return f"atlas-{fmt}{FORMATS[fmt][1]}"


def write(rects: list[FrameRect], layout: Layout, out_dir: Path,
          opts: AtlasOptions | None = None) -> Path:
    opts = opts or AtlasOptions()
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / sidecar_name(opts.format)
    path.write_text(build(rects, layout, opts), encoding="utf-8")
    return path


def parse_rects(text: str, fmt: str,
                sheet_size: tuple[int, int] | None = None) -> list[tuple[int, int, int, int]]:
    """Read frame rects back out of an emitted file — used to verify output.

    For the RomM format there are no rects in the file: it declares a frame size
    and a count, and the client derives the rects. So the sheet's real size is
    fed through the client's own slicing, which is the thing worth checking.
    """
    if fmt == "romm":
        anim = romm_mod.parse_animation(text)
        if anim.get("kind") != "sheet":
            return []
        if sheet_size is None:
            raise AtlasError("Verifying a RomM theme.json needs the sheet's size.")
        return romm_mod.client_rects(
            int(anim.get("frame_width") or 0), int(anim.get("frame_height") or 0),
            int(anim.get("frames") or 0), sheet_size[0], sheet_size[1],
        )
    if fmt == "godot":
        out = []
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("region = Rect2("):
                nums = line[len("region = Rect2("):].rstrip(")").split(",")
                out.append(tuple(int(float(n)) for n in nums))
        return out
    data = json.loads(text)
    frames = data["frames"]
    if fmt == "plain":
        return [(f["x"], f["y"], f["w"], f["h"]) for f in frames]
    if isinstance(frames, dict):
        items = [frames[k] for k in sorted(frames)]
    else:
        items = frames
    return [(f["frame"]["x"], f["frame"]["y"], f["frame"]["w"], f["frame"]["h"]) for f in items]
