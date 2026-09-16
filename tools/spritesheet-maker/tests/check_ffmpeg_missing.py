"""ffmpeg check: the app degrades honestly without ffmpeg, and recovers without a restart.

Runs a second server with ffmpeg hidden from it, drives the page in a browser,
then drops an ffmpeg beside the app and presses Check again.
"""
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

TOOL = Path(__file__).resolve().parent.parent
TMP = TOOL / "tmp"
TMP.mkdir(parents=True, exist_ok=True)


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main():
    port = free_port()
    env = dict(os.environ)
    # A PATH with no ffmpeg on it: what a fresh Windows machine looks like.
    env["PATH"] = str(TMP / "empty-path")
    env["SSM_TMP"] = str(TMP / "ffmpeg-check-jobs")
    (TMP / "empty-path").mkdir(exist_ok=True)

    server = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
         "--port", str(port), "--log-level", "warning"],
        cwd=TOOL, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    planted = TOOL / "ffmpeg"
    try:
        for _ in range(60):
            with socket.socket() as probe:
                probe.settimeout(0.3)
                if probe.connect_ex(("127.0.0.1", port)) == 0:
                    break
            time.sleep(0.25)

        launch = {"args": ["--no-sandbox"]}
        if os.environ.get("SSM_CHROMIUM"):
            launch["executable_path"] = os.environ["SSM_CHROMIUM"]
        with sync_playwright() as pw:
            browser = pw.chromium.launch(**launch)
            page = browser.new_page(viewport={"width": 1400, "height": 1000})
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(f"http://127.0.0.1:{port}/", wait_until="networkidle")

            assert "no ffmpeg" in page.inner_text("#health-tag"), page.inner_text("#health-tag")
            banner = page.inner_text("#ffmpeg-banner")
            assert not page.is_hidden("#ffmpeg-banner"), "the banner should be visible"
            assert "ffmpeg was not found" in banner, banner
            # it must say what still works, not just what is broken
            for works in ["GIF", "APNG", "still images", "ZIP export"]:
                assert works in banner, (works, banner)
            steps = page.locator("#ffmpeg-banner ol li").count()
            assert steps >= 2, steps
            assert page.locator("#ffmpeg-banner a[href*='ffmpeg.org']").count() >= 1, "no link to ffmpeg.org"
            print(f"PASS without ffmpeg: banner names {steps} steps, links out, and lists what still works")

            # GIF work must still be possible with no ffmpeg at all
            page.set_input_files("#file-input", str(TMP / "check_extract.gif"))
            page.wait_for_function("document.getElementById('source-info').textContent.includes('frames')")
            page.click("#btn-extract")
            page.wait_for_function("document.getElementById('frames-tag').textContent.startsWith('6 frames')", timeout=30000)
            page.click("#btn-sheet")
            page.wait_for_function("!document.getElementById('sheet-wrap').hidden", timeout=30000)
            print("PASS without ffmpeg: a GIF still packs into a sheet end to end")

            # the recovery path: drop an ffmpeg beside the app, press Check again
            real = shutil.which("ffmpeg", path="/usr/bin:/bin:/usr/local/bin")
            assert real, "this check needs a real ffmpeg on the test machine"
            shutil.copy(real, planted)
            planted.chmod(0o755)
            page.click("#ffmpeg-recheck")
            page.wait_for_function(
                "document.getElementById('health-tag').textContent.includes('ffmpeg ready')",
                timeout=20000)
            assert "video input and WebM export are on" in page.inner_text("#ffmpeg-banner")
            print("PASS recovery: ffmpeg dropped beside the app is picked up by Check again, no restart")

            assert not errors, errors
            browser.close()

        # And the case the with-ffmpeg download relies on: ffmpeg sitting beside
        # the app is found at startup, with nothing on PATH and nothing configured.
        server.terminate()
        server.wait(timeout=10)
        port2 = free_port()
        second = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
             "--port", str(port2), "--log-level", "warning"],
            cwd=TOOL, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        try:
            import json
            import urllib.request
            for _ in range(60):
                with socket.socket() as probe:
                    probe.settimeout(0.3)
                    if probe.connect_ex(("127.0.0.1", port2)) == 0:
                        break
                time.sleep(0.25)
            health = json.load(urllib.request.urlopen(f"http://127.0.0.1:{port2}/health"))
            assert health["ffmpeg"] is True, health
            assert health["ffmpeg_path"] == str(planted), health
            print(f"PASS a sibling ffmpeg is used automatically at startup: {health['ffmpeg_path']}")
        finally:
            second.terminate()
            try:
                second.wait(timeout=10)
            except subprocess.TimeoutExpired:
                second.kill()
    finally:
        if planted.exists():
            planted.unlink()
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
        shutil.rmtree(TMP / "ffmpeg-check-jobs", ignore_errors=True)
        shutil.rmtree(TMP / "empty-path", ignore_errors=True)

    print("\nFFMPEG-MISSING CHECK OK")


if __name__ == "__main__":
    main()
