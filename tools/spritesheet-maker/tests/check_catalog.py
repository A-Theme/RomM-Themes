"""Catalogue check: a theme built with this tool passes the repo's own validator.

`scripts/validate-themes.py` is what CI runs and what decides whether a theme
loads on a console. It is imported here and pointed at a throwaway theme folder,
so nothing in `themes/` and nothing in `manifest.json` is touched.
"""
import importlib.util
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image

from app.atlas import AtlasOptions, write
from app.extract import Frame
from app.pack import PackOptions, pack
from app.romm import RommOptions, brightness_checks, frame_brightness

TOOL = Path(__file__).resolve().parent.parent
REPO = TOOL.parent.parent
TMP = TOOL / "tmp"
TMP.mkdir(parents=True, exist_ok=True)


def load_validator():
    path = REPO / "scripts" / "validate-themes.py"
    if not path.is_file():
        print(f"SKIP no validator at {path} — run this from inside the repo")
        raise SystemExit(0)
    spec = importlib.util.spec_from_file_location("validate_themes", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def dark_frames(n, w, h, bright=False):
    """A calm animated background: a slow bright dot over a near-black field."""
    out = []
    for i in range(n):
        base = (170, 170, 175, 255) if bright else (7, 8, 15, 255)
        im = Image.new("RGBA", (w, h), base)
        x = int((w - 40) * i / max(n - 1, 1))
        im.paste((60, 90, 160, 255), (x, h // 2 - 12, x + 40, h // 2 + 12))
        out.append(Frame(im, 60))
    return out


def build_theme(folder: Path, frames, dim=0.34, fps=16):
    folder.mkdir(parents=True, exist_ok=True)
    sheet, rects, layout = pack(frames, PackOptions.from_dict(
        {"cols": 4, "padding": 0, "margin": 0, "background": "#000000"}))
    sheet.convert("RGB").save(folder / "sheet.png")
    frames[0].image.convert("RGB").resize((1280, 720)).save(folder / "bg.png")

    spec = {
        "format": "romm",
        "image_name": "sheet.png",
        "romm": {
            "kind": "sheet", "file": "sheet.png",
            "frame_width": layout.cell_w, "frame_height": layout.cell_h,
            "fps": fps, "loop": True,
            "theme_name": folder.name, "author": "spritesheet-maker",
            "background_image": "bg.png", "dim": dim,
        },
    }
    emitted = write(rects, layout, TMP, AtlasOptions.from_dict(spec))
    theme = json.loads(emitted.read_text())
    theme["version"] = "1.0.0"
    theme["colors"] = {
        "bg": "#07080F", "surface": "#10121C", "surface_raised": "#181B28",
        "surface_hover": "#232838", "border": "#2E3448", "text": "#E7ECF7",
        "text_strong": "#FFFFFF", "text_muted": "#A8B2C7", "text_disabled": "#6C7690",
        "text_on_accent": "#07080F", "accent": "#4C8DFF", "accent_hover": "#77A8FF",
        "accent_alt": "#8A6BFF", "focus_ring": "#9CC2FF", "success": "#4FC08D",
        "warning": "#E0A33E", "danger": "#F0616B", "info": "#5AA9E6",
        "scrim": "#04060CD4",
    }
    (folder / "theme.json").write_text(json.dumps(theme, indent=2) + "\n")
    return sheet, layout, theme


def check_brightness_port(validator):
    """The tool's brightness maths must be the validator's, not an approximation."""
    import random
    random.seed(7)
    for _ in range(3):
        im = Image.new("RGB", (64, 36))
        im.putdata([(random.randrange(256), random.randrange(256), random.randrange(256))
                    for _ in range(64 * 36)])
        for dim in (0.0, 0.34, 0.75):
            mine = frame_brightness(im, dim)
            theirs = validator.frame_brightness(im, dim)
            assert mine == theirs, (dim, mine, theirs)
    print("PASS app/romm.py frame_brightness is identical to the validator's over "
          "random art at three dim levels")


def main():
    import warnings
    # the validator still calls Image.getdata(), deprecated in Pillow 12
    warnings.filterwarnings("ignore", category=DeprecationWarning)
    validator = load_validator()
    check_brightness_port(validator)
    work = TMP / "catalog"
    shutil.rmtree(work, ignore_errors=True)
    (work / "themes").mkdir(parents=True)
    validator.THEMES_DIR = str(work / "themes")

    frames = dark_frames(8, 320, 180)
    folder = work / "themes" / "Sheet Maker Check"
    sheet, layout, theme = build_theme(folder, frames)
    print(f"built a theme: sheet {sheet.size}, {len(frames)} frames of "
          f"{layout.cell_w}x{layout.cell_h}, theme.json from app/atlas.py")

    entry, problems = validator.validate_theme(folder.name)
    for p in problems:
        print(f"   {'ERROR' if p.fatal else 'warn '} {p.message}")
    assert entry is not None, "the validator refused the theme outright"
    assert not [p for p in problems if p.fatal], "the validator found fatal problems"
    print(f"PASS scripts/validate-themes.py accepts it — manifest entry: "
          f"{json.dumps({k: entry[k] for k in list(entry)[:4]})}")

    anim = theme["background"]["animation"]
    assert anim["frames"] == len(frames) and anim["kind"] == "sheet"
    assert anim["frame_width"] == 320 and anim["frame_height"] == 180
    assert (sheet.size[0] // anim["frame_width"]) * (sheet.size[1] // anim["frame_height"]) \
        >= anim["frames"], "the sheet must hold every frame theme.json declares"
    print(f"PASS the emitted block describes the sheet that was written: {json.dumps(anim)}")

    # the tool's brightness warning must agree with the validator's
    bright = dark_frames(8, 320, 180, bright=True)
    ours = brightness_checks([f.image for f in bright], 0.34)
    assert ours, "the tool should warn about a sheet this bright"
    loud = work / "themes" / "Too Bright"
    build_theme(loud, bright)
    _entry, loud_problems = validator.validate_theme(loud.name)
    theirs = [p for p in loud_problems if "dims to" in p.message]
    assert theirs, "the validator should warn about the same sheet"
    ours_first = ours[0]["message"].split(" at the ")[1]
    assert ours_first.split(",")[0] in theirs[0].message, (ours[0], theirs[0].message)
    print(f"PASS brightness agrees with the validator — tool: "
          f"{ours[0]['message'][:72]}… / validator: {theirs[0].message[:72]}…")

    calm = brightness_checks([f.image for f in frames], 0.34)
    assert not calm, calm
    print("PASS a calm sheet raises no brightness warning in either")

    shutil.rmtree(work, ignore_errors=True)
    print("\nCATALOG CHECK OK")


if __name__ == "__main__":
    main()
