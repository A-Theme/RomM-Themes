"""Checkpoint 5 verification: 3s testsrc video -> ~30 frames at 10fps."""
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.extract import FrameOptions, extract, probe

TMP = Path(__file__).resolve().parent.parent / "tmp"
TMP.mkdir(parents=True, exist_ok=True)


def main():
    mp4 = TMP / "check_video.mp4"
    if not mp4.exists():
        subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
             "-f", "lavfi", "-i", "testsrc=size=320x240:rate=30:duration=3",
             "-pix_fmt", "yuv420p", str(mp4)],
            check=True,
        )
    print(f"built {mp4.name} ({mp4.stat().st_size} bytes)")

    info = probe(mp4)
    assert info["kind"] == "video", info
    assert abs(info["duration"] - 3.0) < 0.2, info
    print(f"PASS probe: {info['width']}x{info['height']}, {info['duration']:.2f}s, {info['fps']:.1f} fps")

    frames = extract(mp4, FrameOptions.from_dict({"fps": 10}))
    assert 29 <= len(frames) <= 31, f"expected ~30 frames at 10fps, got {len(frames)}"
    assert all(f.image.size == (320, 240) for f in frames)
    assert all(f.duration_ms == 100 for f in frames)
    print(f"PASS 3s video @ 10fps -> {len(frames)} frames, 320x240, 100ms each")

    trimmed = extract(mp4, FrameOptions.from_dict(
        {"fps": 10, "trim_mode": "seconds", "trim_start": 1.0, "trim_end": 2.0}))
    assert 9 <= len(trimmed) <= 11, f"expected ~10 frames for a 1s trim, got {len(trimmed)}"
    print(f"PASS trim 1.0-2.0s @ 10fps -> {len(trimmed)} frames")

    capped = extract(mp4, FrameOptions.from_dict({"fps": 10, "max_frames": 8}))
    assert len(capped) == 8, len(capped)
    print("PASS max frames 8 -> 8 frames")

    scaled = extract(mp4, FrameOptions.from_dict({"fps": 5, "scale_mode": "exact", "scale_w": 64}))
    assert all(f.image.size == (64, 48) for f in scaled), scaled[0].image.size
    print(f"PASS exact width 64 (aspect kept) -> {scaled[0].image.size}, {len(scaled)} frames @ 5fps")

    frames_idx = extract(mp4, FrameOptions.from_dict(
        {"fps": 10, "trim_mode": "frames", "trim_start": 5, "trim_end": 15}))
    assert len(frames_idx) == 10, len(frames_idx)
    print("PASS trim by frame index [5,15) -> 10 frames")

    print("\nCHECKPOINT 5 OK")


if __name__ == "__main__":
    main()
