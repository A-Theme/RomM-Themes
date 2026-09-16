"""FastAPI routes for the sprite sheet maker. Thin: all work lives in the modules."""
from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import Body, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from PIL import Image

from . import atlas as atlas_mod
from . import export as export_mod
from . import extract as extract_mod
from . import generate as generate_mod
from . import pack as pack_mod
from . import romm as romm_mod
from . import slice as slice_mod
from .atlas import AtlasError, AtlasOptions
from .export import ExportError, ExportOptions
from .extract import ExtractError, Frame, FrameOptions
from .generate import GenerateError, GenerateOptions
from .pack import PackError, PackOptions
from .romm import RommError, RommOptions
from .slice import SliceOptions

FROZEN = getattr(sys, "frozen", False)
BASE_DIR = Path(__file__).resolve().parent.parent


def _static_dir() -> Path:
    """Where the page lives: unpacked beside the exe's bundle when frozen."""
    if FROZEN:
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)) / "static"
    return BASE_DIR / "static"


def _tmp_dir() -> Path:
    """Scratch space for uploads and job output.

    A packaged build can sit somewhere unwritable (Program Files), so it works
    out of the system temp dir instead of next to the executable. SSM_TMP wins
    over both, for anyone who wants the jobs somewhere specific.
    """
    override = os.environ.get("SSM_TMP")
    if override:
        return Path(override).expanduser()
    if FROZEN:
        return Path(tempfile.gettempdir()) / "spritesheet-maker"
    return BASE_DIR / "tmp"


STATIC_DIR = _static_dir()
TMP_DIR = _tmp_dir()
JOB_MAX_AGE_SECONDS = 3600
MAX_UPLOAD_BYTES = 250 * 1024 * 1024
UPLOAD_CHUNK = 1024 * 1024


@dataclass
class Job:
    """Server-side state for one upload: its frames and its generated files."""

    id: str
    dir: Path
    input_path: Path | None = None
    source_name: str = ""
    frames: list[Frame] = field(default_factory=list)
    rects: list = field(default_factory=list)
    layout: object | None = None
    progress: dict = field(default_factory=lambda: {"pct": 0.0, "message": "idle", "state": "idle"})

    def set_progress(self, pct: float, message: str, state: str = "working") -> None:
        self.progress = {"pct": round(float(pct), 1), "message": message, "state": state}


JOBS: dict[str, Job] = {}


def ffmpeg_path() -> str | None:
    """ffmpeg on PATH, or shipped next to the executable (extract.py decides)."""
    return extract_mod.ffmpeg_exe()


def ffmpeg_version() -> str | None:
    exe = ffmpeg_path()
    if not exe:
        return None
    try:
        out = subprocess.run([exe, "-version"], capture_output=True, text=True, timeout=10)
        return out.stdout.splitlines()[0].strip() if out.stdout else None
    except Exception:  # noqa: BLE001
        return None


def cleanup_old_jobs() -> int:
    """Remove job dirs older than one hour. Returns how many were removed."""
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    cutoff = time.time() - JOB_MAX_AGE_SECONDS
    removed = 0
    for child in TMP_DIR.iterdir():
        if not child.is_dir():
            continue
        try:
            uuid.UUID(child.name)
        except ValueError:
            continue
        if child.stat().st_mtime < cutoff:
            shutil.rmtree(child, ignore_errors=True)
            removed += 1
    return removed


@asynccontextmanager
async def lifespan(_app: FastAPI):
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    cleanup_old_jobs()
    yield


app = FastAPI(title="Sprite Sheet Maker", lifespan=lifespan)


def new_job() -> Job:
    job_id = str(uuid.uuid4())
    job_dir = TMP_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    job = Job(id=job_id, dir=job_dir)
    JOBS[job_id] = job
    return job


def get_job(job_id: str) -> Job:
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Unknown job — re-upload your file.")
    return job


async def save_upload(upload: UploadFile, job: Job) -> Path:
    """Stream an upload to disk, rejecting anything over the size cap."""
    name = Path(upload.filename or "input").name
    ext = Path(name).suffix.lower()
    if ext not in extract_mod.SUPPORTED_EXTS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type '{ext or name}'. Supported: {', '.join(extract_mod.SUPPORTED_EXTS)}",
        )
    dest = job.dir / f"input{ext}"
    total = 0
    with dest.open("wb") as fh:
        while True:
            chunk = await upload.read(UPLOAD_CHUNK)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_UPLOAD_BYTES:
                fh.close()
                shutil.rmtree(job.dir, ignore_errors=True)
                JOBS.pop(job.id, None)
                raise HTTPException(
                    status_code=413,
                    detail=f"File is larger than the {MAX_UPLOAD_BYTES // (1024 * 1024)}MB limit.",
                )
            fh.write(chunk)
    if total == 0:
        raise HTTPException(status_code=400, detail="The uploaded file is empty.")
    job.input_path = dest
    job.source_name = name
    return dest


def frames_payload(job: Job) -> dict:
    return {
        "job_id": job.id,
        "frame_count": len(job.frames),
        "frames": [
            {"index": i, "w": f.image.width, "h": f.image.height, "duration_ms": f.duration_ms}
            for i, f in enumerate(job.frames)
        ],
    }


def build_atlas(job: Job, spec: dict | None) -> dict:
    """Write a sidecar for the job's packed rects and verify it against the sheet."""
    try:
        opts = AtlasOptions.from_dict(spec or {})
    except AtlasError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    if opts.format == "romm" and str(opts.romm.get("kind") or "sheet") == "gif":
        return build_romm_gif(job, opts)
    if not job.rects or job.layout is None:
        raise HTTPException(status_code=400, detail="Build a sheet before writing atlas metadata.")
    try:
        path = atlas_mod.write(job.rects, job.layout, job.dir, opts)
    except RommError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None

    # Verify against the PNG that was actually written, not against the request.
    sheet_path = job.dir / opts.image_name
    if not sheet_path.is_file():
        sheet_path = job.dir / "sheet.png"
    with Image.open(sheet_path) as im:
        sheet_w, sheet_h = im.size
    text = path.read_text(encoding="utf-8")
    packed = [(r.x, r.y, r.w, r.h) for r in job.rects]
    checks: list[dict] = []

    if opts.format == "romm":
        checks = romm_checks(job, opts, sheet_w, sheet_h)
        # The RomM client derives its own rects from frame size alone; they only
        # line up with what was packed when the sheet is a tight, unpadded grid.
        parsed = atlas_mod.parse_rects(text, opts.format, (sheet_w, sheet_h))
        blocking = [c for c in checks if c["level"] == "error"]
        verified = bool(parsed) and parsed == packed and not blocking
        if parsed and parsed != packed and not blocking:
            checks.insert(0, {
                "level": "error",
                "message": "The rects the client would sample do not match the packed frames.",
            })
            verified = False
        return {"format": opts.format, "file": path.name, "checks": checks, "verified": verified}

    parsed = atlas_mod.parse_rects(text, opts.format)
    if parsed != packed:
        raise HTTPException(status_code=500, detail="Atlas rects do not match the packed sheet.")
    for (x, y, w, h) in parsed:
        if x < 0 or y < 0 or x + w > sheet_w or y + h > sheet_h:
            raise HTTPException(
                status_code=500,
                detail=f"Atlas rect ({x},{y},{w},{h}) falls outside the {sheet_w}x{sheet_h} sheet.",
            )
    return {"format": opts.format, "file": path.name, "checks": checks, "verified": True}


def build_romm_gif(job: Job, opts: AtlasOptions) -> dict:
    """theme.json for a gif background: no sheet, so the frames come from the job."""
    if not job.frames:
        raise HTTPException(status_code=400, detail="Load or extract frames first.")
    spec = dict(opts.romm)
    spec.setdefault("file", "background.gif")
    try:
        settings = RommOptions.from_dict(spec)
    except RommError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    frames = len(job.frames)
    checks = romm_mod.check(settings, frames, 0, 0)
    checks += romm_mod.brightness_checks([f.image for f in job.frames], settings.dim)
    path = job.dir / atlas_mod.sidecar_name("romm")
    path.write_text(romm_mod.theme_json(settings, frames), encoding="utf-8")
    verified = not any(c["level"] == "error" for c in checks)
    return {"format": "romm", "file": path.name, "checks": checks, "verified": verified}


def romm_checks(job: Job, opts: AtlasOptions, sheet_w: int, sheet_h: int) -> list[dict]:
    """Run the RomM client's own acceptance checks against the packed sheet."""
    layout = job.layout
    spec = dict(opts.romm)
    spec.setdefault("file", opts.image_name)
    spec.setdefault("frame_width", layout.cell_w)
    spec.setdefault("frame_height", layout.cell_h)
    try:
        settings = RommOptions.from_dict(spec)
    except RommError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    checks = romm_mod.check(settings, len(job.rects), sheet_w, sheet_h,
                            padding=layout.padding, margin=layout.margin)
    # dim is tuned against the still image, so measure the frames the text sits on
    checks += romm_mod.brightness_checks([f.image for f in job.frames], settings.dim)
    return checks


def sheet_payload(job: Job, files: dict[str, str]) -> dict:
    layout = job.layout
    return {
        "job_id": job.id,
        "layout": layout.as_dict() if layout is not None else None,
        "rects": [r.as_dict() for r in job.rects],
        "frame_count": len(job.frames),
        "files": files,
        "urls": {k: f"/api/jobs/{job.id}/files/{v}" for k, v in files.items()},
    }


# --------------------------------------------------------------------------
# meta
# --------------------------------------------------------------------------

@app.get("/health")
def health() -> JSONResponse:
    exe = ffmpeg_path()
    return JSONResponse(
        {
            "status": "ok",
            "ffmpeg": exe is not None,
            "ffmpeg_path": exe,
            "ffmpeg_version": ffmpeg_version(),
            "max_upload_mb": MAX_UPLOAD_BYTES // (1024 * 1024),
            "supported": extract_mod.SUPPORTED_EXTS,
        }
    )


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


# --------------------------------------------------------------------------
# input -> frames -> sheet
# --------------------------------------------------------------------------

@app.post("/api/upload")
async def upload(file: UploadFile = File(...)) -> JSONResponse:
    job = new_job()
    path = await save_upload(file, job)
    try:
        info = extract_mod.probe(path)
    except ExtractError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    job.set_progress(100.0, "Uploaded", "done")
    return JSONResponse(
        {
            "job_id": job.id,
            "name": job.source_name,
            "size_bytes": path.stat().st_size,
            "info": info,
            "ffmpeg": ffmpeg_path() is not None,
        }
    )


@app.get("/api/jobs/{job_id}/progress")
def job_progress(job_id: str) -> JSONResponse:
    return JSONResponse(get_job(job_id).progress)


@app.post("/api/jobs/{job_id}/extract")
def job_extract(job_id: str, payload: dict = Body(default={})) -> JSONResponse:
    """Decode the upload into frames using the frame controls."""
    job = get_job(job_id)
    if job.input_path is None:
        raise HTTPException(status_code=400, detail="This job has no uploaded file.")
    opts = FrameOptions.from_dict(payload.get("frame") or payload)
    job.set_progress(1.0, "Decoding input...")
    try:
        job.frames = extract_mod.extract(job.input_path, opts, job.set_progress)
    except ExtractError as exc:
        job.set_progress(100.0, str(exc), "error")
        raise HTTPException(status_code=400, detail=str(exc)) from None
    job.set_progress(100.0, f"{len(job.frames)} frames ready", "done")
    return JSONResponse(frames_payload(job))


@app.post("/api/jobs/{job_id}/sheet")
def job_sheet(job_id: str, payload: dict = Body(default={})) -> JSONResponse:
    """Pack the job's frames into a sheet and write PNG (+ optional WebP)."""
    job = get_job(job_id)
    if not job.frames:
        raise HTTPException(status_code=400, detail="Extract frames before packing a sheet.")
    opts = PackOptions.from_dict(payload.get("layout") or payload)
    job.set_progress(80.0, "Packing sheet...")
    try:
        sheet, rects, layout = pack_mod.pack(job.frames, opts)
    except PackError as exc:
        job.set_progress(100.0, str(exc), "error")
        raise HTTPException(status_code=400, detail=str(exc)) from None
    job.rects, job.layout = rects, layout

    files: dict[str, str] = {}
    png_path = job.dir / "sheet.png"
    sheet.save(png_path)
    files["png"] = png_path.name
    if payload.get("webp"):
        job.set_progress(92.0, "Writing WebP...")
        webp_path = job.dir / "sheet.webp"
        sheet.save(webp_path, format="WEBP", lossless=True)
        files["webp"] = webp_path.name

    atlas_spec = payload.get("atlas")
    atlas_result = None
    if atlas_spec:
        job.set_progress(96.0, "Writing atlas metadata...")
        atlas_result = build_atlas(job, atlas_spec)
        files["atlas"] = atlas_result["file"]

    job.set_progress(100.0, f"Sheet {layout.width}x{layout.height} ready", "done")
    result = sheet_payload(job, files)
    result["atlas_verified"] = bool(atlas_result and atlas_result["verified"])
    result["atlas_checks"] = atlas_result["checks"] if atlas_result else []
    return JSONResponse(result)


@app.post("/api/jobs/{job_id}/atlas")
def job_atlas(job_id: str, payload: dict = Body(default={})) -> JSONResponse:
    """Write (or re-write) the sidecar for an already-packed sheet."""
    job = get_job(job_id)
    job.set_progress(50.0, "Writing atlas metadata...")
    result = build_atlas(job, payload.get("atlas") or payload)
    path = job.dir / result["file"]
    job.set_progress(100.0, f"{result['file']} ready", "done")
    return JSONResponse(
        {
            "format": result["format"],
            "file": result["file"],
            "size_bytes": path.stat().st_size,
            "frame_count": len(job.rects),
            "verified": result["verified"],
            "checks": result["checks"],
            "url": f"/api/jobs/{job.id}/files/{result['file']}",
        }
    )


@app.get("/api/romm/limits")
def romm_limits() -> JSONResponse:
    """The RomM client's caps, so the UI can enforce them before packing."""
    return JSONResponse(
        {
            "max_frames": romm_mod.MAX_ANIMATION_FRAMES,
            "max_fps": romm_mod.MAX_ANIMATION_FPS,
            "max_texture_bytes": romm_mod.MAX_ANIMATION_BYTES,
            "screen": {"w": romm_mod.SCREEN_W, "h": romm_mod.SCREEN_H},
            "cell_presets": {k: list(v) for k, v in romm_mod.CELL_PRESETS.items()},
            "frame_budget": {
                name: romm_mod.max_frames_for_cell("sheet", w, h)
                for name, (w, h) in romm_mod.CELL_PRESETS.items()
            },
        }
    )


@app.post("/api/jobs/{job_id}/generate")
def job_generate(job_id: str, payload: dict = Body(default={})) -> JSONResponse:
    """Build an animation from the job's still image using a transform."""
    job = get_job(job_id)
    if job.input_path is None:
        raise HTTPException(status_code=400, detail="This job has no uploaded file.")
    try:
        opts = GenerateOptions.from_dict(payload.get("generate") or payload)
    except GenerateError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    job.set_progress(10.0, "Loading still image...")
    try:
        base = extract_mod.load_frames(job.input_path)[0].image
        frame_opts = FrameOptions.from_dict(payload.get("frame") or {})
        if frame_opts.scale_mode != "none":
            base = extract_mod.scale_frames([Frame(base, opts.duration_ms)], frame_opts)[0].image
        job.set_progress(45.0, f"Generating {opts.frames} frames ({opts.transform})...")
        job.frames = generate_mod.generate(base, opts)
    except (ExtractError, GenerateError) as exc:
        job.set_progress(100.0, str(exc), "error")
        raise HTTPException(status_code=400, detail=str(exc)) from None
    job.set_progress(100.0, f"{len(job.frames)} frames generated", "done")
    return JSONResponse(frames_payload(job))


# --------------------------------------------------------------------------
# sheet -> frames
# --------------------------------------------------------------------------

@app.post("/api/slice")
async def slice_upload(
    file: UploadFile = File(...),
    geometry: str = Form("{}"),
) -> JSONResponse:
    """Slice an uploaded sprite sheet into frames using pack.py's geometry."""
    job = new_job()
    path = await save_upload(file, job)
    try:
        opts = SliceOptions.from_dict(json.loads(geometry or "{}"))
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"Bad geometry: {exc}") from None
    job.set_progress(20.0, "Slicing sheet...")
    try:
        with Image.open(path) as im:
            sheet = im.convert("RGBA")
        frames, rects, layout = slice_mod.slice_sheet(sheet, opts)
    except PackError as exc:
        job.set_progress(100.0, str(exc), "error")
        raise HTTPException(status_code=400, detail=str(exc)) from None
    except Exception as exc:  # noqa: BLE001
        job.set_progress(100.0, str(exc), "error")
        raise HTTPException(status_code=400, detail=f"Could not read that sheet: {exc}") from None
    job.frames, job.rects, job.layout = frames, rects, layout
    job.set_progress(100.0, f"{len(frames)} frames sliced", "done")
    payload = frames_payload(job)
    payload["layout"] = layout.as_dict()
    payload["rects"] = [r.as_dict() for r in rects]
    payload["sheet"] = {"width": sheet.width, "height": sheet.height}
    return JSONResponse(payload)


# --------------------------------------------------------------------------
# exports
# --------------------------------------------------------------------------

@app.post("/api/jobs/{job_id}/export")
def job_export(job_id: str, payload: dict = Body(default={})) -> JSONResponse:
    """Export the job's frames as gif / webm / apng / zip."""
    job = get_job(job_id)
    if not job.frames:
        raise HTTPException(status_code=400, detail="Load or extract frames before exporting.")
    try:
        opts = ExportOptions.from_dict(payload.get("export") or payload)
    except ExportError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    job.set_progress(15.0, f"Encoding {opts.format.upper()}...")
    try:
        path = export_mod.export(job.frames, job.dir, opts)
    except ExportError as exc:
        job.set_progress(100.0, str(exc), "error")
        raise HTTPException(status_code=400, detail=str(exc)) from None
    job.set_progress(100.0, f"{path.name} ready", "done")
    return JSONResponse(
        {
            "format": opts.format,
            "file": path.name,
            "size_bytes": path.stat().st_size,
            "frame_count": len(job.frames),
            "url": f"/api/jobs/{job.id}/files/{path.name}",
        }
    )


# --------------------------------------------------------------------------
# artefacts
# --------------------------------------------------------------------------

@app.get("/api/jobs/{job_id}/frames/{index}.png")
def job_frame(job_id: str, index: int) -> Response:
    job = get_job(job_id)
    if not 0 <= index < len(job.frames):
        raise HTTPException(status_code=404, detail="No such frame.")
    buf = io.BytesIO()
    job.frames[index].image.save(buf, format="PNG")
    return Response(content=buf.getvalue(), media_type="image/png",
                    headers={"Cache-Control": "no-store"})


@app.get("/api/jobs/{job_id}/files/{name}")
def job_file(job_id: str, name: str) -> FileResponse:
    job = get_job(job_id)
    safe = Path(name).name
    path = job.dir / safe
    if not path.is_file():
        raise HTTPException(status_code=404, detail="No such file for this job.")
    return FileResponse(path, filename=safe, headers={"Cache-Control": "no-store"})


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
