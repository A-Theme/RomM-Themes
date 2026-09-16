"""Browser check: drive every UI control in a real Chromium and assert it does real work."""
import os
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = os.environ.get("SSM_BASE", "http://127.0.0.1:8731") + "/"
TMP = Path(__file__).resolve().parent.parent / "tmp"
TMP.mkdir(parents=True, exist_ok=True)


def main():
    errors, console = [], []
    with sync_playwright() as pw:
        # SSM_CHROMIUM points at a Chromium binary when Playwright's own download
        # is not the one installed (a distro build, or a pinned browser bundle).
        launch = {"args": ["--no-sandbox"]}
        if os.environ.get("SSM_CHROMIUM"):
            launch["executable_path"] = os.environ["SSM_CHROMIUM"]
        browser = pw.chromium.launch(**launch)
        page = browser.new_page(viewport={"width": 1400, "height": 1200})
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: console.append(f"{m.type}: {m.text}") if m.type in ("error", "warning") else None)
        page.goto(BASE, wait_until="networkidle")

        assert page.title() == "Sprite Sheet Maker"
        tag = page.inner_text("#health-tag")
        assert "ffmpeg ready" in tag, tag
        assert page.is_hidden("#ffmpeg-banner")
        print(f"PASS page loads, health probed live: '{tag}', no ffmpeg banner")

        # --- GIF: upload -> frame controls -> extract -> preview player
        page.set_input_files("#file-input", str(TMP / "check_extract.gif"))
        page.wait_for_function("document.getElementById('source-info').textContent.includes('frames')")
        print("PASS drop-zone upload ->", page.inner_text("#source-info"))

        page.select_option("#trim-mode", "frames")
        assert not page.is_disabled("#trim-start"), "trim inputs stayed disabled"
        page.fill("#trim-start", "1")
        page.fill("#trim-end", "5")
        page.select_option("#scale-mode", "percent")
        assert page.is_visible("#scale-percent"), "percent input did not appear"
        page.fill("#scale-percent", "50")
        page.check("#nearest")
        page.click("#btn-extract")
        page.wait_for_function("document.getElementById('frames-tag').textContent.startsWith('4 frames')", timeout=30000)
        print("PASS frame controls (trim 1-5, 50% nearest) -> 4 frames extracted")

        page.wait_for_function("document.getElementById('frame-total').textContent === '4'")
        assert page.inner_text("#frame-ms").startswith("120 ms · 32×32"), page.inner_text("#frame-ms")
        idx0 = page.eval_on_selector("#stage", "c => c.toDataURL()")
        page.fill("#scrubber", "2")
        page.dispatch_event("#scrubber", "input")
        assert page.inner_text("#frame-index") == "2"
        idx2 = page.eval_on_selector("#stage", "c => c.toDataURL()")
        assert idx0 != idx2, "scrubber did not repaint the canvas"
        print("PASS preview: 4 frames loaded, readout '%s', scrubber repaints canvas"
              % page.inner_text("#frame-ms"))

        page.click("#btn-play")
        assert "Pause" in page.inner_text("#btn-play")
        page.wait_for_timeout(700)
        moved = page.inner_text("#frame-index")
        page.click("#btn-play")
        assert "Play" in page.inner_text("#btn-play")
        print(f"PASS play/pause advances frames (stopped on frame {moved})")

        page.fill("#preview-fps", "30")
        page.dispatch_event("#preview-fps", "input")
        assert page.inner_text("#preview-fps-val") == "30"
        before_onion = page.eval_on_selector("#stage", "c => c.toDataURL()")
        assert not page.is_checked("#onion"), "onion skin should default to off"
        page.check("#onion")
        after_onion = page.eval_on_selector("#stage", "c => c.toDataURL()")
        assert before_onion != after_onion, "onion skin toggle did not change the canvas"
        page.uncheck("#onion")
        page.fill("#preview-zoom", "4")
        page.dispatch_event("#preview-zoom", "input")
        assert page.eval_on_selector("#stage", "c => c.width") == 128, "zoom did not resize the canvas"
        print("PASS fps slider, onion skin (default off, changes canvas), 4× zoom -> 128px canvas")

        # --- layout controls -> sheet + atlas sidecar
        page.uncheck("#auto-square")
        assert not page.is_disabled("#cols")
        page.fill("#cols", "2")
        page.fill("#padding", "6")
        page.fill("#margin", "4")
        page.uncheck("#bg-transparent")
        assert not page.is_disabled("#bg-color")
        page.fill("#bg-color", "#204060")
        page.check("#pot")
        page.check("#want-webp")
        page.select_option("#atlas-format", "phaser-hash")
        page.click("#btn-sheet")
        page.wait_for_function("!document.getElementById('sheet-wrap').hidden", timeout=30000)
        meta = page.inner_text("#sheet-meta")
        # 2 cols * 32 + 1*6 padding + 2*4 margin = 78 -> POT padding rounds both axes to 128
        assert "128×128 px · grid 2×2 · cell 32×32 · padding 6 · margin 4" in meta, meta
        print("PASS layout controls ->", meta)
        files = page.inner_text("#sheet-files")
        assert "download png" in files and "download webp" in files, files
        assert page.locator("#rect-table tbody tr").count() == 4
        atlas_links = page.inner_text("#atlas-files")
        assert "atlas-phaser-hash.json" in atlas_links and "verified" in atlas_links, atlas_links
        print(f"PASS sheet files: {files.strip()} | atlas: {atlas_links.strip()}")

        # --- atlas format switch
        page.select_option("#atlas-format", "godot")
        page.fill("#atlas-anim-name", "walk")
        page.click("#btn-atlas")
        page.wait_for_function("document.getElementById('atlas-files').textContent.includes('atlas-godot.tres')", timeout=20000)
        print("PASS atlas format switch -> atlas-godot.tres written for the same sheet")

        # --- exports
        for fmt in ["gif", "webm", "apng", "zip"]:
            page.select_option("#export-format", fmt)
            if fmt == "gif":
                assert page.is_visible("#export-gif-row")
                page.uncheck("#export-dither")
                page.fill("#export-colors", "32")
            page.fill("#export-fps", "15")
            page.fill("#export-loop", "0")
            page.click("#btn-export")
            page.wait_for_function(
                f"document.getElementById('export-files').textContent.includes('{fmt}')", timeout=60000)
        exports = page.inner_text("#export-files")
        for fmt in ["animation.gif", "animation.webm", "animation.apng", "frames.zip"]:
            assert fmt in exports, exports
        print("PASS all four exports produced from the UI:", " | ".join(exports.split("\n")))

        # --- still image -> generated frames
        page.set_input_files("#file-input", str(TMP / "check_generate_src.png"))
        page.wait_for_function("document.getElementById('source-info').textContent.includes('image')")
        page.select_option("#gen-transform", "bounce")
        assert page.is_visible("#gen-bounce") and page.is_hidden("#gen-degrees"), "transform options did not switch"
        page.fill("#gen-bounce", "18")
        page.fill("#gen-frames", "10")
        page.select_option("#gen-easing", "bounce")
        page.select_option("#gen-loop", "ping-pong")
        page.click("#btn-generate")
        page.wait_for_function("document.getElementById('frames-tag').textContent.startsWith('10 frames')", timeout=30000)
        page.wait_for_function("document.getElementById('frame-total').textContent === '10'")
        print("PASS still -> bounce transform, 10 frames, ping-pong + bounce easing, loaded into preview")

        # --- slice an existing sheet back into the player
        page.check("#auto-square")
        page.fill("#padding", "0")
        page.fill("#margin", "0")
        page.check("#bg-transparent")
        page.uncheck("#pot")
        page.click("#btn-sheet")
        page.wait_for_function("document.getElementById('sheet-meta').textContent.includes('grid 4×3')", timeout=30000)
        sheet_url = page.get_attribute("#sheet-img", "src")
        page.evaluate("""async (url) => {
            const blob = await (await fetch(url)).blob();
            const dt = new DataTransfer();
            dt.items.add(new File([blob], 'sheet.png', {type: 'image/png'}));
            document.getElementById('slice-input').files = dt.files;
            document.getElementById('slice-input').dispatchEvent(new Event('change'));
        }""", sheet_url)
        page.wait_for_function("document.getElementById('slice-info').textContent.includes('Sliced')", timeout=30000)
        info = page.inner_text("#slice-info")
        assert "Sliced 10 frames" in info and "grid 4×3" in info, info
        page.wait_for_function("document.getElementById('frame-total').textContent === '10'")
        page.select_option("#slice-mode", "cell")
        assert page.is_visible("#slice-cell-row") and page.is_hidden("#slice-grid-row")
        print("PASS sheet -> frames:", page.inner_text("#slice-info"))

        # --- RomM mode: preset drives the other panels, checks come back from the client's rules
        page.set_input_files("#file-input", str(TMP / "check_video.mp4"))
        page.wait_for_function("document.getElementById('source-info').textContent.includes('video')")
        page.fill("#fps", "8")
        page.fill("#max-frames", "8")
        page.select_option("#trim-mode", "none")
        page.select_option("#scale-mode", "none")
        page.click("#btn-extract")
        page.wait_for_function("document.getElementById('frames-tag').textContent.startsWith('8 frames')", timeout=60000)

        page.select_option("#romm-cell", "320x180")
        page.fill("#romm-fps", "12")
        page.fill("#romm-file", "sheet.png")
        page.fill("#romm-still", "background.png")
        page.fill("#romm-name", "Test Theme")
        page.click("#btn-romm-preset")
        assert page.input_value("#padding") == "0" and page.input_value("#margin") == "0"
        assert page.input_value("#scale-w") == "320" and page.input_value("#scale-h") == "180"
        # 8 frames of 320x180: 2 cols x 4 rows is the squarest grid with no spare cells
        assert page.input_value("#cols") == "2", page.input_value("#cols")
        assert not page.is_checked("#pot")
        assert page.input_value("#atlas-format") == "romm"
        budget = page.inner_text("#romm-budget")
        assert "MB of the 48 MB texture budget" in budget, budget
        assert "max 218 frames at this size" in page.inner_text("#romm-cap")
        print(f"PASS RomM preset: padding/margin 0, cells 320×180, cols 2 (full grid), atlas=romm | {budget}")

        # re-extract so the 320x180 scale the preset set is actually applied; the frame
        # count does not change, so wait on the busy bar rather than on the count
        page.click("#btn-extract")
        page.wait_for_selector("#busy", state="visible")
        page.wait_for_selector("#busy", state="hidden", timeout=60000)
        assert page.inner_text("#frame-ms").endswith("320×180"), page.inner_text("#frame-ms")
        page.click("#btn-sheet")
        page.wait_for_function("document.getElementById('sheet-meta').textContent.includes('640×720')", timeout=60000)
        checks = page.inner_text("#romm-checks")
        assert "Texture cost" in checks and "48 MB budget" in checks, checks
        assert "verified against the sheet" in page.inner_text("#atlas-files")
        print("PASS RomM sheet 640×720 (2×4 of 320×180) + theme.json, client checks clean:",
              checks.replace("\n", " | "))

        page.fill("#padding", "4")
        page.fill("#margin", "8")
        page.click("#btn-sheet")
        page.wait_for_function("document.getElementById('romm-checks').textContent.includes('padding')", timeout=60000)
        bad = page.inner_text("#romm-checks")
        assert "misaligns every frame" in bad and "shifts the whole grid" in bad, bad
        assert "the client would refuse this sheet" in page.inner_text("#atlas-files")
        print("PASS padded sheet is flagged in the UI as one the client would refuse")

        # --- RomM gif kind: no sheet involved, billed at full screen per frame
        page.select_option("#romm-kind", "gif")
        assert page.is_hidden("#romm-cell-row"), "frame-size row should vanish for a gif"
        page.click("#btn-romm-preset")
        assert page.input_value("#export-format") == "gif"
        assert page.input_value("#romm-file") == "background.gif"
        budget_gif = page.inner_text("#romm-budget")
        assert "1280×720 per frame" in budget_gif, budget_gif
        page.click("#btn-atlas")
        page.wait_for_function("document.getElementById('romm-checks').textContent.includes('SDL2_image')", timeout=30000)
        print(f"PASS RomM gif kind: {budget_gif} | {page.inner_text('#romm-checks').splitlines()[0]}")
        page.select_option("#romm-kind", "sheet")

        assert not errors, f"page errors: {errors}"
        bad = [c for c in console if "favicon" not in c]
        assert not bad, f"console errors: {bad}"
        print("PASS no uncaught page errors, no console errors")
        browser.close()
    print("\nUI CHECK OK")


if __name__ == "__main__":
    main()
