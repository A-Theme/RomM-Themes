#!/usr/bin/env python3
"""
Validate every theme in this repo and rebuild manifest.json.

    ./scripts/validate-themes.py            validate, report, rewrite manifest
    ./scripts/validate-themes.py --check     validate only, touch nothing
                                             (exits 1 if the manifest is stale)

Exit 0 = every theme is valid and the manifest is current.

Why this exists: this repo is data, and data repos rot quietly. A theme with a
typo'd colour role or a background it forgot to commit looks fine in a diff and
only fails on someone's console. So the same rules the client enforces are
enforced here, at PR time.

These rules mirror source/ui/theme_spec.cpp in romm-switch-client and the
format described in its docs/THEMES.md. That file is normative; when the two
disagree, IT is right and this needs updating. Keep the constants below in step
with theme_spec.h.
"""

import argparse
import json
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
THEMES_DIR = os.path.join(REPO, "themes")
MANIFEST = os.path.join(REPO, "manifest.json")
SHOTS_DIR = "screenshots"

# --- Limits, mirroring theme_spec.h -----------------------------------------
try:
    from PIL import Image
except ImportError:          # the brightness check is skipped without Pillow
    Image = None

MAX_ANIMATION_BYTES = 48 * 1024 * 1024
MAX_ANIMATION_FRAMES = 240
MAX_ANIMATION_FPS = 60

# The 19 semantic roles, in the order theme_spec.cpp lists them.
ROLES = [
    "bg", "surface", "surface_raised", "surface_hover", "border",
    "text", "text_strong", "text_muted", "text_disabled", "text_on_accent",
    "accent", "accent_hover", "accent_alt", "focus_ring",
    "success", "warning", "danger", "info",
    "scrim",
]

MOTION_KINDS = {"none", "drift", "pan", "zoom"}
ANIMATION_KINDS = {"none", "sheet", "gif"}

# Effects are drawn per UI element rather than over the whole screen.
#
# The normative list of slots and kinds lives in source/ui/theme_spec.h in
# romm-switch-client; this is a mirror of it and can fall behind. An
# unrecognised slot or kind is therefore a WARNING, not an error - a theme
# should not fail CI for using something the client gained after the last sync.
# Everything structural below (types, ranges, colour references) is still fatal,
# because those are wrong against any version of the spec.
EFFECT_SLOTS = {"focus"}
# Every kind theme_spec.cpp parses. This list was short because only
# embers had shipped; the client draws all six.
# Every kind theme_spec.cpp parses, in the order focus_kind_names() lists them.
# The eleven border treatments replace the focus ring rather than decorating
# it; the client's focus_ring() checks replaces_outline() before drawing its
# own, so they are not buried under it.
EFFECT_KINDS = {
    "none", "smoke", "embers", "glow", "shimmer", "pulse", "fade",
    "ring", "runner", "gradient", "notched", "ticks", "breathe",
    "rails", "sidebar", "brackets", "inner_glow", "lift",
}
EFFECT_KINDS_PENDING = set()   # nothing is waiting on a renderer any more

HEX_RE = re.compile(r"^#?(?:[0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")

# retro::sanitize_component's allowed set: alnum, dash, underscore, period,
# space, and the ROM punctuation it permits. A name needing anything else is
# refused rather than rewritten, because a rewritten name would not match the
# file on disk.
SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9\-_. \[\]()+!]+$")

AUDIO_EXTS = {".mp3", ".ogg", ".opus", ".flac", ".mod", ".xm", ".it", ".s3m"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
FONT_EXTS = {".ttf", ".otf"}


class Problem:
    def __init__(self, theme, message, fatal=True):
        self.theme = theme
        self.message = message
        self.fatal = fatal

    def __str__(self):
        tag = "ERROR" if self.fatal else "warn "
        return f"  [{tag}] {self.theme}: {self.message}"


# Brightness ceilings for a dimmed background, as luminance percentiles.
#
# docs/THEME-FORMAT.md quotes the targets the generator aimed at (0.095 / 0.165
# / 0.115). The shipped catalogue misses those narrowly almost everywhere - 120
# of 134 themes exceed at least one - so enforcing them would be 120 warnings of
# noise. These are the catalogue's own observed ceiling, rounded up: every theme
# in the repo today passes silently, and only art genuinely worse than anything
# shipped gets flagged.
BRIGHTNESS_LIMITS = (
    ("90th percentile", 90, 1.00, 0.105),
    ("97th percentile", 97, 1.00, 0.210),
    ("95th percentile of the left 42%", 95, 0.42, 0.145),
)
MAX_FRAMES_SAMPLED = 16

_LINEAR = [(c / 255.0) / 12.92 if c / 255.0 <= 0.04045
           else (((c / 255.0) + 0.055) / 1.055) ** 2.4 for c in range(256)]


def _percentile(values, q):
    values = sorted(values)
    k = (len(values) - 1) * q / 100.0
    lo = int(k)
    hi = min(lo + 1, len(values) - 1)
    return values[lo] + (values[hi] - values[lo]) * (k - lo)


def frame_brightness(image, dim):
    """The percentiles of BRIGHTNESS_LIMITS for one frame, dimmed.

    dim is a straight sRGB multiply, as render_theme_background does it, so the
    multiply happens on the channel values before linearising.
    """
    keep = 1.0 - dim
    rgb = image.convert("RGB")
    # tobytes() rather than getdata(): the same pixels in the same order, but
    # getdata() is deprecated in Pillow 12 and goes away in 14, and this does
    # not build a list of 921600 tuples for a 720p frame on the way past.
    raw = rgb.tobytes()
    width = rgb.size[0]
    edge = int(width * 0.42)
    full, left = [], []
    for i in range(0, len(raw), 3):
        y = (0.2126 * _LINEAR[int(raw[i] * keep)]
             + 0.7152 * _LINEAR[int(raw[i + 1] * keep)]
             + 0.0722 * _LINEAR[int(raw[i + 2] * keep)])
        full.append(y)
        if ((i // 3) % width) < edge:
            left.append(y)
    return [_percentile(left if frac < 1.0 else full, q)
            for _, q, frac, _ in BRIGHTNESS_LIMITS]


def read_png_size(path):
    """(width, height) from a PNG's IHDR, or None if it is not readable."""
    try:
        with open(path, "rb") as f:
            head = f.read(24)
    except OSError:
        return None
    if len(head) < 24 or head[:8] != b"\x89PNG\r\n\x1a\n" or head[12:16] != b"IHDR":
        return None
    return (int.from_bytes(head[16:20], "big"), int.from_bytes(head[20:24], "big"))


def read_gif_info(path):
    """(width, height, frames) read from the GIF itself, or None.

    Declared frame counts have been wrong by 15x in this repo, and the client
    decodes the file, not the JSON - so the file is what the budget is judged
    on. Walked by hand rather than with Pillow, which CI does not install.
    """
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        return None
    n = len(data)
    if n < 13 or data[:6] not in (b"GIF87a", b"GIF89a"):
        return None
    width = int.from_bytes(data[6:8], "little")
    height = int.from_bytes(data[8:10], "little")
    i = 13
    if data[10] & 0x80:                      # global colour table
        i += 3 * (1 << ((data[10] & 0x07) + 1))

    def skip_sub_blocks(j):
        while j < n and data[j]:
            j += data[j] + 1
        return j + 1

    frames = 0
    while 0 <= i < n:
        block = data[i]
        if block == 0x3B:                    # trailer
            break
        if block == 0x21:                    # extension
            i = skip_sub_blocks(i + 2)
        elif block == 0x2C:                  # image descriptor: one frame
            frames += 1
            if i + 9 >= n:
                break
            local = data[i + 9]
            i += 10
            if local & 0x80:
                i += 3 * (1 << ((local & 0x07) + 1))
            i = skip_sub_blocks(i + 1)       # past the LZW code-size byte
        else:
            return None                      # not a structure we can walk
    return (width, height, frames)


def is_safe_asset_name(name):
    """Plain file name inside the theme folder, nothing else."""
    if not name or len(name) > 180:
        return False
    if "/" in name or "\\" in name:
        return False
    if name in (".", ".."):
        return False
    if not SAFE_NAME_RE.match(name):
        return False
    # sanitize_component strips leading/trailing dots and spaces, so a name
    # with them would not survive the client's own pass unchanged.
    if name[0] in ". " or name[-1] in ". ":
        return False
    return True


def check_asset(problems, theme, folder, value, field, exts=None):
    """Validate one asset reference and confirm the file is committed."""
    if not is_safe_asset_name(value):
        problems.append(Problem(
            theme, f'{field}: "{value}" is not a plain file name inside the '
                   f'theme folder'))
        return None
    path = os.path.join(folder, value)
    if not os.path.isfile(path):
        problems.append(Problem(
            theme, f'{field}: "{value}" is referenced but not committed'))
        return None
    if exts:
        ext = os.path.splitext(value)[1].lower()
        if ext not in exts:
            problems.append(Problem(
                theme, f'{field}: "{value}" has an unexpected extension '
                       f'{ext or "(none)"} - expected one of '
                       f'{", ".join(sorted(exts))}', fatal=False))
    return value


def check_sheet_brightness(problems, folder_name, sheet_path, fw, fh, frames, dim):
    """Sample frames out of a sprite sheet and check how bright they dim to.

    dim is tuned against background.image, but the frames are what is actually
    on screen. A sheet can be calm on frame 1 and blow past the ceiling ninety
    frames later, and nothing looked at that until now.
    """
    if Image is None or not os.path.isfile(sheet_path):
        return
    try:
        sheet = Image.open(sheet_path)
        sheet.load()
    except Exception:
        return                       # unreadable art is the asset check's problem

    across = sheet.size[0] // fw if fw else 0
    if not across or not frames:
        return

    step = max(1, frames // MAX_FRAMES_SAMPLED)
    worst = {}
    for index in range(0, frames, step):
        row, col = divmod(index, across)
        box = (col * fw, row * fh, (col + 1) * fw, (row + 1) * fh)
        if box[2] > sheet.size[0] or box[3] > sheet.size[1]:
            break
        for (label, _, _, limit), value in zip(BRIGHTNESS_LIMITS,
                                               frame_brightness(sheet.crop(box), dim)):
            if value > limit and value > worst.get(label, (0, 0))[0]:
                worst[label] = (value, index)

    for label, _, _, limit in BRIGHTNESS_LIMITS:
        if label in worst:
            value, index = worst[label]
            problems.append(Problem(
                folder_name,
                f"background.animation: frame {index} dims to {value:.4f} at the "
                f"{label}, over {limit} - dim {dim} suits the still image but not "
                f"the frames, and text sits on the frames", fatal=False))


def validate_theme(folder_name):
    """Validate one theme folder. Returns (manifest_entry_or_None, problems)."""
    problems = []
    folder = os.path.join(THEMES_DIR, folder_name)
    theme_json = os.path.join(folder, "theme.json")

    if not os.path.isfile(theme_json):
        problems.append(Problem(folder_name, "no theme.json"))
        return None, problems

    if not is_safe_asset_name(folder_name):
        problems.append(Problem(
            folder_name,
            "the folder name must itself be a plain name - the client uses it "
            "as the theme's identity on the SD card"))
        return None, problems

    try:
        with open(theme_json, encoding="utf-8") as f:
            doc = json.load(f)
    except json.JSONDecodeError as e:
        problems.append(Problem(folder_name, f"theme.json is not valid JSON: {e}"))
        return None, problems

    if not isinstance(doc, dict):
        problems.append(Problem(folder_name, "theme.json must be an object"))
        return None, problems

    # --- identity ----------------------------------------------------------
    name = doc.get("name") or folder_name
    if not isinstance(name, str):
        problems.append(Problem(folder_name, "name must be a string"))
        name = folder_name
    author = doc.get("author", "")
    version = doc.get("version", "")
    for field, value in (("author", author), ("version", version)):
        if value and not isinstance(value, str):
            problems.append(Problem(folder_name, f"{field} must be a string"))
    if not author:
        problems.append(Problem(
            folder_name, "no author - please credit yourself", fatal=False))

    # --- colours -----------------------------------------------------------
    colors = doc.get("colors", {})
    roles_set = 0
    if colors and not isinstance(colors, dict):
        problems.append(Problem(
            folder_name, "colors must be an object of role -> hex colour"))
    elif isinstance(colors, dict):
        for role, value in colors.items():
            if role not in ROLES:
                problems.append(Problem(
                    folder_name,
                    f'colors: "{role}" is not a colour role - the client will '
                    f'ignore it'))
                continue
            if not isinstance(value, str) or not HEX_RE.match(value):
                problems.append(Problem(
                    folder_name,
                    f'colors.{role}: "{value}" is not a hex colour '
                    f'(#RGB, #RRGGBB, #RRGGBBAA)'))
                continue
            roles_set += 1

    # --- background --------------------------------------------------------
    has_background = False
    animated = False
    bg = doc.get("background")
    if isinstance(bg, str):
        has_background = bool(check_asset(problems, folder_name, folder, bg,
                                          "background", IMAGE_EXTS))
    elif isinstance(bg, dict):
        image = bg.get("image")
        if image is not None:
            has_background = bool(check_asset(
                problems, folder_name, folder, image, "background.image",
                IMAGE_EXTS))

        dim = bg.get("dim")
        if dim is not None:
            if not isinstance(dim, (int, float)) or isinstance(dim, bool):
                problems.append(Problem(folder_name, "background.dim must be a number"))
            elif not 0.0 <= dim <= 1.0:
                problems.append(Problem(
                    folder_name, f"background.dim {dim} is outside 0.0-1.0 and "
                                 f"will be clamped", fatal=False))

        motion = bg.get("motion")
        if isinstance(motion, dict):
            kind = motion.get("kind", "none")
            if kind not in MOTION_KINDS:
                problems.append(Problem(
                    folder_name,
                    f'background.motion.kind: "{kind}" is not one of '
                    f'{", ".join(sorted(MOTION_KINDS))}'))
        elif motion is not None:
            problems.append(Problem(folder_name, "background.motion must be an object"))

        anim = bg.get("animation")
        if isinstance(anim, dict):
            kind = anim.get("kind", "none")
            if kind not in ANIMATION_KINDS:
                problems.append(Problem(
                    folder_name,
                    f'background.animation.kind: "{kind}" is not one of '
                    f'{", ".join(sorted(ANIMATION_KINDS))}'))
            elif kind != "none":
                animated = True
                anim_file = anim.get("file")
                if anim_file is None:
                    problems.append(Problem(
                        folder_name, "background.animation: no file"))
                    animated = False
                elif not check_asset(problems, folder_name, folder, anim_file,
                                     "background.animation.file", IMAGE_EXTS):
                    animated = False

                frames = anim.get("frames", 0)
                fps = anim.get("fps", 12)
                fw = anim.get("frame_width", 0)
                fh = anim.get("frame_height", 0)

                if not isinstance(frames, int) or isinstance(frames, bool):
                    problems.append(Problem(
                        folder_name, "background.animation.frames must be an integer"))
                    frames = 0
                if isinstance(fps, int) and not 1 <= fps <= MAX_ANIMATION_FPS:
                    problems.append(Problem(
                        folder_name,
                        f"background.animation.fps {fps} is outside "
                        f"1-{MAX_ANIMATION_FPS} and will be clamped", fatal=False))

                if kind == "sheet":
                    if not fw or not fh:
                        problems.append(Problem(
                            folder_name,
                            "background.animation: a sheet needs frame_width and "
                            "frame_height, or the client drops the animation"))
                        animated = False
                    elif not frames:
                        problems.append(Problem(
                            folder_name,
                            "background.animation: a sheet needs frames"))
                        animated = False
                    else:
                        size = (read_png_size(os.path.join(folder, anim_file))
                                if anim_file else None)
                        if size:
                            sw, sh = size
                            capacity = (sw // fw) * (sh // fh)
                            if capacity < frames:
                                problems.append(Problem(
                                    folder_name,
                                    f"background.animation: {anim_file} is "
                                    f"{sw}x{sh}, which holds {capacity} "
                                    f"{fw}x{fh} frames, not the {frames} "
                                    f"declared"))
                                animated = False
                        check_sheet_brightness(
                            problems, folder_name, os.path.join(folder, anim_file),
                            fw, fh, frames, dim if isinstance(dim, (int, float))
                            and not isinstance(dim, bool) else 0.0)
                        cost = fw * fh * frames * 4
                        if cost > MAX_ANIMATION_BYTES:
                            problems.append(Problem(
                                folder_name,
                                f"background.animation needs {cost // 1048576} MB "
                                f"of texture, over the "
                                f"{MAX_ANIMATION_BYTES // 1048576} MB budget - the "
                                f"client will refuse it and use the still image"))
                            animated = False
                elif kind == "gif":
                    # The client decodes the file, so measure the file. A theme
                    # once declared 8 frames for a gif holding 125, which priced
                    # a 48.8 MB animation at 29 MB and let it through.
                    info = (read_gif_info(os.path.join(folder, anim_file))
                            if anim_file else None)
                    if info:
                        gw, gh, real_frames = info
                        if real_frames and frames and frames != real_frames:
                            problems.append(Problem(
                                folder_name,
                                f"background.animation.frames says {frames}, but "
                                f"{anim_file} holds {real_frames} - the file is "
                                f"what the client decodes"))
                        if real_frames:
                            frames = real_frames
                    else:
                        # Unreadable: fall back to billing at full screen.
                        gw, gh = 1280, 720
                    cost = gw * gh * frames * 4
                    if frames and cost > MAX_ANIMATION_BYTES:
                        problems.append(Problem(
                            folder_name,
                            f"background.animation: {frames} gif frames at "
                            f"{gw}x{gh} is {cost // 1048576} MB, over the "
                            f"{MAX_ANIMATION_BYTES // 1048576} MB budget"))
                        animated = False
                    problems.append(Problem(
                        folder_name,
                        "background.animation: gif needs SDL2_image 2.6+ on the "
                        "client; prefer kind \"sheet\", and ship a still image "
                        "either way", fatal=False))

                if frames > MAX_ANIMATION_FRAMES:
                    problems.append(Problem(
                        folder_name,
                        f"background.animation.frames {frames} is over the "
                        f"{MAX_ANIMATION_FRAMES}-frame cap"))
                    animated = False

            if animated and not has_background:
                problems.append(Problem(
                    folder_name,
                    "an animated theme should also ship background.image - it is "
                    "the fallback when the animation cannot be used", fatal=False))
        elif anim is not None:
            problems.append(Problem(
                folder_name, "background.animation must be an object"))
    elif bg is not None:
        problems.append(Problem(
            folder_name, "background must be an object or a file name"))

    # --- effects -----------------------------------------------------------
    effects = doc.get("effects")
    if isinstance(effects, dict):
        for slot, spec in effects.items():
            if slot not in EFFECT_SLOTS:
                problems.append(Problem(
                    folder_name,
                    f'effects: "{slot}" is not an effect slot the validator '
                    f'knows ({", ".join(sorted(EFFECT_SLOTS))}) - it will do '
                    f'nothing unless the client has gained it since',
                    fatal=False))
                continue
            if not isinstance(spec, dict):
                problems.append(Problem(
                    folder_name, f"effects.{slot} must be an object"))
                continue

            kind = spec.get("kind", "none")
            if not isinstance(kind, str):
                problems.append(Problem(
                    folder_name, f"effects.{slot}.kind must be a string"))
            elif kind in EFFECT_KINDS_PENDING:
                problems.append(Problem(
                    folder_name,
                    f'effects.{slot}.kind: "{kind}" is specified but no shipped '
                    f'client draws it yet - the focus-effects patch adding it is '
                    f'not merged, so this falls back to the built-in ring',
                    fatal=False))
            elif kind not in EFFECT_KINDS:
                problems.append(Problem(
                    folder_name,
                    f'effects.{slot}.kind: "{kind}" is not one of '
                    f'{", ".join(sorted(EFFECT_KINDS | EFFECT_KINDS_PENDING))} - '
                    f'if the client has gained it, add it to EFFECT_KINDS',
                    fatal=False))

            # theme_spec.cpp clamps the effect's amount to 0-100 and warns
            # outside it - unlike background.motion.amount, which really is
            # 0-256. They are different fields with different ranges.
            for field, lo, hi in (("speed", 0.1, 8.0), ("amount", 0.0, 100.0)):
                value = spec.get(field)
                if value is None:
                    continue
                if not isinstance(value, (int, float)) or isinstance(value, bool):
                    problems.append(Problem(
                        folder_name, f"effects.{slot}.{field} must be a number"))
                elif not lo <= value <= hi:
                    problems.append(Problem(
                        folder_name,
                        f"effects.{slot}.{field} {value} is outside {lo}-{hi} "
                        f"and will be clamped", fatal=False))

            # A ROLE only - not a hex. theme_spec.cpp checks the value against
            # theme_color_roles() and warns "is not a colour role - using
            # focus_ring" for anything else, so a hex here silently loses the
            # colour the theme asked for. That is the point of a role: the
            # effect keeps following the palette when the theme is recoloured.
            color = spec.get("color")
            if color is not None:
                if not isinstance(color, str):
                    problems.append(Problem(
                        folder_name, f"effects.{slot}.color must be a string"))
                elif color not in ROLES:
                    hexish = " - a hex is not accepted here, only a role" \
                             if HEX_RE.match(color) else ""
                    problems.append(Problem(
                        folder_name,
                        f'effects.{slot}.color: "{color}" is not a colour '
                        f'role{hexish}'))
                elif color in ROLES and isinstance(colors, dict) \
                        and color not in colors:
                    problems.append(Problem(
                        folder_name,
                        f'effects.{slot}.color references role "{color}", '
                        f'which this theme does not set', fatal=False))
    elif effects is not None:
        problems.append(Problem(folder_name, "effects must be an object"))

    # --- font, assets, audio ----------------------------------------------
    has_font = False
    font = doc.get("font")
    if isinstance(font, str):
        has_font = bool(check_asset(problems, folder_name, folder, font, "font",
                                    FONT_EXTS))
    elif isinstance(font, dict):
        if font.get("file") is not None:
            has_font = bool(check_asset(problems, folder_name, folder,
                                        font["file"], "font.file", FONT_EXTS))
    elif font is not None:
        problems.append(Problem(folder_name, "font must be an object or a file name"))

    has_mascot = False
    assets = doc.get("assets")
    if isinstance(assets, dict):
        if assets.get("mascot") is not None:
            has_mascot = bool(check_asset(problems, folder_name, folder,
                                          assets["mascot"], "assets.mascot",
                                          IMAGE_EXTS))
    elif assets is not None:
        problems.append(Problem(folder_name, "assets must be an object"))

    has_music = False
    audio = doc.get("audio")
    if isinstance(audio, dict):
        bgm = audio.get("bgm")
        if bgm is not None and bgm not in ("none", "off"):
            has_music = bool(check_asset(problems, folder_name, folder, bgm,
                                         "audio.bgm", AUDIO_EXTS))
        vol = audio.get("volume")
        if vol is not None:
            if not isinstance(vol, (int, float)) or isinstance(vol, bool):
                problems.append(Problem(folder_name, "audio.volume must be a number"))
            elif vol < 0 or vol > 128:
                problems.append(Problem(
                    folder_name, f"audio.volume {vol} is outside 0.0-1.0 and "
                                 f"0-128 - it will be clamped", fatal=False))
    elif audio is not None:
        problems.append(Problem(folder_name, "audio must be an object"))

    # Nothing themed at all is almost certainly a mistake.
    if (roles_set == 0 and not has_background and not has_font
            and not has_mascot and not has_music and not animated):
        problems.append(Problem(
            folder_name, "this theme changes nothing - no colours and no assets"))

    # Files that are committed but never referenced: usually a rename that was
    # half finished, so worth saying, never worth failing over.
    referenced = {"theme.json"}
    for key in ("background", "font"):
        v = doc.get(key)
        if isinstance(v, str):
            referenced.add(v)
        elif isinstance(v, dict):
            for sub in ("image", "file"):
                if isinstance(v.get(sub), str):
                    referenced.add(v[sub])
    if isinstance(bg, dict) and isinstance(bg.get("animation"), dict):
        f = bg["animation"].get("file")
        if isinstance(f, str):
            referenced.add(f)
    if isinstance(assets, dict) and isinstance(assets.get("mascot"), str):
        referenced.add(assets["mascot"])
    if isinstance(audio, dict) and isinstance(audio.get("bgm"), str):
        referenced.add(audio["bgm"])

    total_bytes = 0
    for entry in sorted(os.listdir(folder)):
        full = os.path.join(folder, entry)
        if not os.path.isfile(full):
            continue
        total_bytes += os.path.getsize(full)
        if entry not in referenced:
            problems.append(Problem(
                folder_name,
                f'"{entry}" is committed but not referenced by theme.json',
                fatal=False))

    # A screenshot is optional but drives the README gallery.
    screenshot = None
    for ext in (".png", ".jpg", ".jpeg", ".webp"):
        candidate = f"{folder_name}{ext}"
        if os.path.isfile(os.path.join(REPO, SHOTS_DIR, candidate)):
            screenshot = f"{SHOTS_DIR}/{candidate}"
            break

    entry = {
        "id": re.sub(r"[^a-z0-9]+", "-", folder_name.lower()).strip("-"),
        "folder": folder_name,
        "name": name,
        "author": author,
        "version": version,
        "path": f"themes/{folder_name}",
        "roles_set": roles_set,
        "has_background": has_background,
        "has_font": has_font,
        "has_mascot": has_mascot,
        "has_music": has_music,
        "animated": animated,
        "bytes": total_bytes,
        "screenshot": screenshot,
    }
    return entry, problems


def build_manifest(entries):
    return {
        "format": 1,
        "repo": "A-Theme/RomM-Themes",
        "count": len(entries),
        "themes": entries,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true",
                    help="validate only; fail if manifest.json is stale")
    args = ap.parse_args()

    if not os.path.isdir(THEMES_DIR):
        print(f"no themes/ directory at {THEMES_DIR}")
        return 1

    folders = sorted(
        (d for d in os.listdir(THEMES_DIR)
         if os.path.isdir(os.path.join(THEMES_DIR, d))),
        key=str.lower)

    if not folders:
        print("themes/ is empty")
        return 1

    entries = []
    all_problems = []
    for folder in folders:
        entry, problems = validate_theme(folder)
        all_problems.extend(problems)
        if entry:
            entries.append(entry)

    errors = [p for p in all_problems if p.fatal]
    warnings = [p for p in all_problems if not p.fatal]

    print(f"{len(folders)} theme folder(s)\n")
    for folder in folders:
        mine = [p for p in all_problems if p.theme == folder]
        bad = sum(1 for p in mine if p.fatal)
        if bad:
            print(f"  FAIL {folder}")
        elif mine:
            print(f"  warn {folder}")
        else:
            print(f"  ok   {folder}")

    if all_problems:
        print()
        for p in all_problems:
            print(p)

    manifest = build_manifest(entries)
    rendered = json.dumps(manifest, indent=2, ensure_ascii=False) + "\n"

    current = None
    if os.path.isfile(MANIFEST):
        with open(MANIFEST, encoding="utf-8") as f:
            current = f.read()

    stale = current != rendered
    if args.check:
        if stale:
            print("\nmanifest.json is out of date - run ./scripts/validate-themes.py")
    elif stale:
        with open(MANIFEST, "w", encoding="utf-8") as f:
            f.write(rendered)
        print(f"\nmanifest.json rewritten: {len(entries)} theme(s)")
    else:
        print(f"\nmanifest.json already current: {len(entries)} theme(s)")

    print()
    if errors:
        print(f"FAILED: {len(errors)} error(s), {len(warnings)} warning(s)")
        return 1
    if args.check and stale:
        print("FAILED: manifest is stale")
        return 1
    print(f"OK: {len(entries)} valid theme(s), {len(warnings)} warning(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
