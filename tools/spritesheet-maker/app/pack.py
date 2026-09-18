"""Frame list -> sheet image + frame rects.

This module is the single source of truth for cell geometry. `slice.py`
builds its rects with the helpers here so a pack/slice round trip is exact.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass

from PIL import Image

from .extract import Frame


class PackError(Exception):
    """Raised for impossible layouts; surfaced to the UI as a 400."""


@dataclass
class FrameRect:
    """Where one frame actually lives on the sheet (the drawn area, not the cell)."""

    index: int
    x: int
    y: int
    w: int
    h: int
    duration_ms: int = 100

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class Layout:
    """Grid geometry for a sheet."""

    cols: int
    rows: int
    cell_w: int
    cell_h: int
    padding: int
    margin: int
    width: int
    height: int
    count: int

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class PackOptions:
    """Layout controls."""

    cols: int | None = None       # None / 0 => auto square
    padding: int = 0              # between cells
    margin: int = 0               # outside the grid
    background: str = "transparent"   # "transparent" or a #rrggbb colour
    power_of_two: bool = False

    @classmethod
    def from_dict(cls, d: dict | None) -> "PackOptions":
        d = d or {}
        cols = d.get("cols")
        if d.get("auto_square") or cols in (None, "", 0, "0"):
            cols = None
        else:
            try:
                cols = max(int(cols), 1)
            except (TypeError, ValueError):
                cols = None

        def intval(key, default=0):
            try:
                return max(int(d.get(key) or 0), 0)
            except (TypeError, ValueError):
                return default

        return cls(
            cols=cols,
            padding=intval("padding"),
            margin=intval("margin"),
            background=str(d.get("background") or "transparent"),
            power_of_two=bool(d.get("power_of_two")),
        )


def auto_cols(count: int) -> int:
    """Columns for the squarest grid that holds `count` frames."""
    return max(int(math.ceil(math.sqrt(max(count, 1)))), 1)


def grid_size(cols: int, rows: int, cell_w: int, cell_h: int, padding: int, margin: int) -> tuple[int, int]:
    """Sheet size: margin on all four sides, padding only *between* cells."""
    width = margin * 2 + cols * cell_w + max(cols - 1, 0) * padding
    height = margin * 2 + rows * cell_h + max(rows - 1, 0) * padding
    return width, height


def next_power_of_two(n: int) -> int:
    return 1 << max(int(n - 1), 0).bit_length() if n > 1 else 1


def cell_origin(index: int, cols: int, cell_w: int, cell_h: int, padding: int, margin: int) -> tuple[int, int]:
    """Top-left pixel of cell `index`. The one place cell positions are computed."""
    col = index % cols
    row = index // cols
    x = margin + col * (cell_w + padding)
    y = margin + row * (cell_h + padding)
    return x, y


def compute_layout(
    count: int,
    cell_w: int,
    cell_h: int,
    opts: PackOptions,
) -> Layout:
    if count <= 0:
        raise PackError("There are no frames to pack.")
    cols = opts.cols or auto_cols(count)
    cols = min(max(cols, 1), count)
    rows = int(math.ceil(count / cols))
    width, height = grid_size(cols, rows, cell_w, cell_h, opts.padding, opts.margin)
    if opts.power_of_two:
        width = next_power_of_two(width)
        height = next_power_of_two(height)
    return Layout(cols, rows, cell_w, cell_h, opts.padding, opts.margin, width, height, count)


def grid_rects(layout: Layout, count: int | None = None) -> list[FrameRect]:
    """Full-cell rects for a grid — used when slicing an arbitrary sheet."""
    n = layout.count if count is None else count
    return [
        FrameRect(i, *cell_origin(i, layout.cols, layout.cell_w, layout.cell_h,
                                  layout.padding, layout.margin),
                  layout.cell_w, layout.cell_h)
        for i in range(n)
    ]


def parse_background(background: str) -> tuple[int, int, int, int]:
    if not background or background == "transparent":
        return (0, 0, 0, 0)
    value = background.strip()
    if value.startswith("#"):
        value = value[1:]
    if len(value) == 3:
        value = "".join(c * 2 for c in value)
    if len(value) == 6:
        try:
            r, g, b = (int(value[i:i + 2], 16) for i in (0, 2, 4))
            return (r, g, b, 255)
        except ValueError:
            pass
    raise PackError(f"Unrecognised background colour: {background!r}")


def pack(frames: list[Frame], opts: PackOptions | None = None) -> tuple[Image.Image, list[FrameRect], Layout]:
    """Pack frames into a sheet. Cells are sized to the largest frame; each
    frame is drawn at the cell's top-left and its rect records the drawn area,
    so slicing by those rects returns the original frames pixel for pixel."""
    opts = opts or PackOptions()
    if not frames:
        raise PackError("There are no frames to pack.")
    cell_w = max(f.image.width for f in frames)
    cell_h = max(f.image.height for f in frames)
    layout = compute_layout(len(frames), cell_w, cell_h, opts)
    bg = parse_background(opts.background)
    sheet = Image.new("RGBA", (layout.width, layout.height), bg)
    solid = bg[3] == 255

    rects: list[FrameRect] = []
    for i, frame in enumerate(frames):
        x, y = cell_origin(i, layout.cols, cell_w, cell_h, opts.padding, opts.margin)
        img = frame.image if frame.image.mode == "RGBA" else frame.image.convert("RGBA")
        if solid:
            # Flatten onto the chosen colour so it shows through the frame's
            # transparent areas, as a solid background is meant to.
            sheet.alpha_composite(img, (x, y))
        else:
            # Replace, don't blend: a transparent sheet must keep each frame's
            # own alpha so slicing returns it byte for byte.
            sheet.paste(img, (x, y))
        rects.append(FrameRect(i, x, y, img.width, img.height, frame.duration_ms))
    return sheet, rects, layout
