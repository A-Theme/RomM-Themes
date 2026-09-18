"""ffmpeg compatibility check: video decode works on every ffmpeg we can find.

ffmpeg removed `-vsync` in 8.0 and only grew `-fps_mode` in 5.1, so a command
built for one era fails outright on the other — and a master build reports
`N-126593-gbc46eab87c` rather than a version, so the choice cannot be made by
parsing one. This runs a real extraction against each binary available.

Extra binaries can be named with SSM_FFMPEG_ALT (colon-separated paths), which
is how the build bundled with the release gets covered next to the system one.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app.extract as extract_mod
from app.extract import FrameOptions, extract, frame_rate_args, probe

TMP = Path(__file__).resolve().parent.parent / "tmp"
TMP.mkdir(parents=True, exist_ok=True)


def candidates():
    found, seen = [], set()
    for path in [shutil.which("ffmpeg")] + os.environ.get("SSM_FFMPEG_ALT", "").split(":"):
        if not path:
            continue
        real = str(Path(path).resolve())
        if real in seen or not Path(real).is_file():
            continue
        seen.add(real)
        found.append(real)
    return found


def version_of(exe):
    out = subprocess.run([exe, "-version"], capture_output=True, text=True, timeout=30)
    return (out.stdout or "").splitlines()[0].replace("ffmpeg version ", "")[:48]


def main():
    mp4 = TMP / "check_video.mp4"
    if not mp4.exists():
        subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
             "-i", "testsrc=size=320x240:rate=30:duration=3", "-pix_fmt", "yuv420p", str(mp4)],
            check=True)

    exes = candidates()
    assert exes, "no ffmpeg found at all"

    for exe in exes:
        # point the app at this specific binary (the lookup is deliberately
        # uncached, so changing PATH is enough)
        old_path = os.environ.get("PATH", "")
        os.environ["PATH"] = str(Path(exe).parent)
        try:
            assert extract_mod.ffmpeg_exe() == exe, (extract_mod.ffmpeg_exe(), exe)
            args = frame_rate_args(exe)
            assert args in (("-fps_mode", "passthrough"), ("-vsync", "0")), args

            info = probe(mp4)
            assert info["width"] == 320 and abs(info["duration"] - 3.0) < 0.2, info

            frames = extract(mp4, FrameOptions.from_dict({"fps": 10}))
            assert 29 <= len(frames) <= 31, f"{exe}: {len(frames)} frames"
            assert all(f.image.size == (320, 240) for f in frames)
            print(f"PASS {version_of(exe)}")
            print(f"     rate flag {' '.join(args)} · probe {info['width']}x{info['height']} "
                  f"{info['duration']:.2f}s · {len(frames)} frames at 10fps")
        finally:
            os.environ["PATH"] = old_path

    if len(exes) == 1:
        print("NOTE only one ffmpeg available; set SSM_FFMPEG_ALT to cover another build")
    print("\nFFMPEG-VERSIONS CHECK OK")


if __name__ == "__main__":
    main()
