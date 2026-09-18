"""Sheet image + geometry -> frame list. The exact inverse of pack.py.

Cell geometry is never recomputed here: every rect comes from the helpers in
pack.py, so slicing a sheet returns the frames that were packed into it.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from PIL import Image

from .extract import DEFAULT_DURATION_MS, Frame
from .pack import FrameRect, Layout, PackError, cell_origin, grid_size


@dataclass
class SliceOptions:
    """Either rows+cols or an explicit cell size, plus the sheet's padding/margin."""

    mode: str = "grid"              # grid (rows/cols) | cell (cell_w/cell_h)
    cols: int | None = None
    rows: int | None = None
    cell_w: int | None = None
    cell_h: int | None = None
    padding: int = 0
    margin: int = 0
    count: int | None = None        # stop after N frames (trailing cells are blanks)
    duration_ms: int = DEFAULT_DURATION_MS
    drop_empty: bool = False        # skip fully transparent cells

    @classmethod
    def from_dict(cls, d: dict | None) -> "SliceOptions":
        d = d or {}

        def intval(key):
            v = d.get(key)
            if v in (None, "", 0, "0"):
                return None
            try:
                return max(int(v), 1)
            except (TypeError, ValueError):
                return None

        def nonneg(key):
            try:
                return max(int(d.get(key) or 0), 0)
            except (TypeError, ValueError):
                return 0

        return cls(
            mode=str(d.get("mode") or "grid"),
            cols=intval("cols"),
            rows=intval("rows"),
            cell_w=intval("cell_w"),
            cell_h=intval("cell_h"),
            padding=nonneg("padding"),
            margin=nonneg("margin"),
            count=intval("count"),
            duration_ms=intval("duration_ms") or DEFAULT_DURATION_MS,
            drop_empty=bool(d.get("drop_empty")),
        )


def layout_for_sheet(sheet_w: int, sheet_h: int, opts: SliceOptions) -> Layout:
    """Resolve rows/cols/cell size for a sheet, inverting pack.grid_size()."""
    padding, margin = opts.padding, opts.margin
    inner_w = sheet_w - 2 * margin
    inner_h = sheet_h - 2 * margin
    if inner_w <= 0 or inner_h <= 0:
        raise PackError("The outer margin is larger than the sheet.")

    if opts.mode == "cell":
        if not opts.cell_w or not opts.cell_h:
            raise PackError("Cell width and height are required in cell mode.")
        cell_w, cell_h = int(opts.cell_w), int(opts.cell_h)
        cols = max(int((inner_w + padding) // (cell_w + padding)), 1)
        rows = max(int((inner_h + padding) // (cell_h + padding)), 1)
    else:
        cols = int(opts.cols or 0)
        rows = int(opts.rows or 0)
        if not cols and not rows:
            raise PackError("Give a column count, a row count, or a cell size.")
        if cols and not rows:
            rows = max(int(math.ceil((opts.count or cols) / cols)), 1)
        if rows and not cols:
            cols = max(int(math.ceil((opts.count or rows) / rows)), 1)
        cell_w = (inner_w - max(cols - 1, 0) * padding) // cols
        cell_h = (inner_h - max(rows - 1, 0) * padding) // rows

    if cell_w <= 0 or cell_h <= 0:
        raise PackError("That geometry gives a zero-sized cell — check padding and margin.")
    width, height = grid_size(cols, rows, cell_w, cell_h, padding, margin)
    count = opts.count or cols * rows
    count = min(count, cols * rows)
    return Layout(cols, rows, cell_w, cell_h, padding, margin, max(width, sheet_w),
                  max(height, sheet_h), count)


def rects_for_layout(layout: Layout, duration_ms: int = DEFAULT_DURATION_MS) -> list[FrameRect]:
    """Full-cell rects in reading order, positioned by pack.cell_origin()."""
    out: list[FrameRect] = []
    for i in range(layout.count):
        x, y = cell_origin(i, layout.cols, layout.cell_w, layout.cell_h,
                           layout.padding, layout.margin)
        out.append(FrameRect(i, x, y, layout.cell_w, layout.cell_h, duration_ms))
    return out


def slice_by_rects(sheet: Image.Image, rects: list[FrameRect]) -> list[Frame]:
    """Cut a sheet into frames using rects produced by pack.py."""
    img = sheet if sheet.mode == "RGBA" else sheet.convert("RGBA")
    frames: list[Frame] = []
    for r in rects:
        if r.x < 0 or r.y < 0 or r.x + r.w > img.width or r.y + r.h > img.height:
            raise PackError(
                f"Frame {r.index} ({r.x},{r.y} {r.w}x{r.h}) falls outside the "
                f"{img.width}x{img.height} sheet."
            )
        frames.append(Frame(img.crop((r.x, r.y, r.x + r.w, r.y + r.h)), r.duration_ms))
    return frames


def slice_sheet(sheet: Image.Image, opts: SliceOptions) -> tuple[list[Frame], list[FrameRect], Layout]:
    """Slice an arbitrary sheet image by grid or cell geometry."""
    layout = layout_for_sheet(sheet.width, sheet.height, opts)
    rects = rects_for_layout(layout, opts.duration_ms)
    frames = slice_by_rects(sheet, rects)
    if opts.drop_empty:
        kept = [(f, r) for f, r in zip(frames, rects) if f.image.getbbox() is not None]
        if kept:
            frames = [f for f, _ in kept]
            rects = [FrameRect(i, r.x, r.y, r.w, r.h, r.duration_ms)
                     for i, (_, r) in enumerate(kept)]
            layout.count = len(frames)
    return frames, rects, layout
