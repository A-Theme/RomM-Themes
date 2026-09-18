// Sprite Sheet Maker — frontend. Plain ES module, no build step.

const $ = (id) => document.getElementById(id);

const state = {
  jobId: null,
  info: null,
  frameCount: 0,
  frames: [],        // {index,w,h,duration_ms}
  sheet: null,       // {layout, rects, urls}
  ffmpeg: false,
};

// ---------------------------------------------------------------- utilities

function showError(msg) {
  const b = $("error-banner");
  b.textContent = msg;
  b.hidden = !msg;
  if (msg) setTimeout(() => { b.hidden = true; }, 9000);
}

let pollTimer = null;
function busy(on, msg = "Working…") {
  $("busy").hidden = !on;
  // While a rebuild is in flight, a second click would race it and show the
  // losing answer, so the controls go inert until it lands.
  document.body.classList.toggle("working", on);
  $("busy-msg").textContent = msg;
  if (!on) {
    setBar(0);
    if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
  }
}
function setBar(pct, msg) {
  $("bar").firstElementChild.style.width = `${Math.max(0, Math.min(100, pct))}%`;
  $("busy-pct").textContent = `${Math.round(pct)}%`;
  if (msg) $("busy-msg").textContent = msg;
}

// Poll server-side progress while a long request is in flight, so nothing
// looks frozen during video decode or a big pack.
function pollProgress(jobId) {
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = setInterval(async () => {
    if (!jobId) return;
    try {
      const r = await fetch(`/api/jobs/${jobId}/progress`);
      if (!r.ok) return;
      const p = await r.json();
      setBar(p.pct || 0, p.message || "");
    } catch { /* transient — the main request reports real failures */ }
  }, 250);
}

async function api(url, opts = {}) {
  const r = await fetch(url, opts);
  const text = await r.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = { detail: text }; }
  if (!r.ok) throw new Error((data && data.detail) || `${r.status} ${r.statusText}`);
  return data;
}

const postJSON = (url, body) =>
  api(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });

// A collapsed panel is only acceptable if its header still reports state, so
// each summary carries a chip and every action that changes state updates it.
// The strip at the top is the answer to "what do I do" - it moves as the work
// does, so the page always has exactly one obvious next thing.
function flowStep(n) {
  for (let i = 1; i <= 3; i++) {
    const li = $(`flow-${i}`);
    if (!li) continue;
    li.classList.toggle("on", i === n);
    li.classList.toggle("done", i < n);
  }
}

// Everything past the source is meaningless until a file is loaded, so it says
// so instead of offering controls that quietly do nothing.
function setReady(ready) {
  document.querySelectorAll(".needs-file").forEach((el) => {
    el.classList.toggle("disabled-block", !ready);
  });
}

function chip(id, text, done) {
  const el = $(id);
  if (!el) return;
  el.textContent = text || "";
  el.classList.toggle("done", !!done);
}

function openPanel(id) {
  const el = $(id);
  if (el && el.tagName === "DETAILS") el.open = true;
}

function bytes(n) {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1048576).toFixed(1)} MB`;
}

// ------------------------------------------------------------ option models

function frameOptions() {
  const trimMode = $("trim-mode").value;
  const scaleMode = $("scale-mode").value;
  const num = (id) => ($(id).value === "" ? null : Number($(id).value));
  return {
    fps: num("fps"),
    max_frames: num("max-frames"),
    trim_mode: trimMode,
    trim_start: trimMode === "none" ? 0 : (num("trim-start") ?? 0),
    trim_end: trimMode === "none" ? null : num("trim-end"),
    scale_mode: scaleMode,
    scale_percent: num("scale-percent") ?? 100,
    scale_w: num("scale-w"),
    scale_h: num("scale-h"),
    nearest: $("nearest").checked,
  };
}

function layoutOptions() {
  return {
    auto_square: $("auto-square").checked,
    cols: $("auto-square").checked ? null : Number($("cols").value || 1),
    padding: Number($("padding").value || 0),
    margin: Number($("margin").value || 0),
    background: $("bg-transparent").checked ? "transparent" : $("bg-color").value,
    power_of_two: $("pot").checked,
  };
}

// ------------------------------------------------------------------- source

async function uploadFile(file) {
  if (file.size > 250 * 1024 * 1024) {
    showError(`"${file.name}" is ${bytes(file.size)} — over the 250MB limit.`);
    return;
  }
  busy(true, `Uploading ${file.name}…`);
  setBar(5);
  try {
    const fd = new FormData();
    fd.append("file", file);
    const res = await api("/api/upload", { method: "POST", body: fd });
    state.jobId = res.job_id;
    state.info = res.info;
    state.frameCount = 0;
    state.frames = [];
    state.sheet = null;
    const i = res.info || {};
    $("source-info").innerHTML =
      `<b>${res.name}</b> — ${bytes(res.size_bytes)} · kind <b>${i.kind || "?"}</b>` +
      (i.width ? ` · <b>${i.width}×${i.height}</b>` : "") +
      (i.frame_count ? ` · <b>${i.frame_count}</b> frames` : "") +
      (i.duration ? ` · ${Number(i.duration).toFixed(2)}s` : "") +
      (i.fps ? ` · ${Number(i.fps).toFixed(2)} fps` : "");
    $("btn-extract").disabled = false;
    $("btn-sheet").disabled = true;
    chip("src-chip", `${res.name} · ${bytes(res.size_bytes)}`, true);
    chip("frames-tag", "no frames");
    setReady(true);
    flowStep(2);
    resetPerFileControls(i);
    onSourceLoaded(i);
    if (i.kind === "video" && !state.ffmpeg) {
      showError("ffmpeg is missing — video input cannot be decoded.");
      return;
    }
  } catch (e) {
    showError(e.message);
    return;
  } finally {
    busy(false);
  }
  // Build the thing they came for straight away: a first result beats an empty
  // page and six panels of settings whose effect you cannot see yet.
  await doExtract();
  if (state.frameCount) await doSheet();
}

// Trim is expressed in this file's seconds or frame numbers, so carrying it to
// the next file is how a fresh drop ends up reporting "zero frames selected".
function resetPerFileControls(info) {
  $("trim-mode").value = "none";
  $("trim-mode").dispatchEvent(new Event("change"));
  $("trim-start").value = "";
  $("trim-end").value = "";
  if (!info.animated) {
    // one still image: a frame cap and a source FPS have nothing to act on
    $("max-frames").value = "";
    $("fps").value = "";
  }
}

// Hook other panels extend (still-image transforms, etc.).
let onSourceLoaded = () => {};
export function setSourceHook(fn) { onSourceLoaded = fn; }

// ------------------------------------------------------------------ actions

async function doExtract() {
  if (!state.jobId) return;
  busy(true, "Extracting frames…");
  pollProgress(state.jobId);
  try {
    const res = await postJSON(`/api/jobs/${state.jobId}/extract`, { frame: frameOptions() });
    adoptFrames(res);
  } catch (e) {
    showError(e.message);
  } finally {
    busy(false);
  }
}

function adoptFrames(res) {
  state.frameCount = res.frame_count;
  state.frames = res.frames || [];
  const first = state.frames[0];
  chip("frames-tag",
       first ? `${res.frame_count} frames · ${first.w}×${first.h}` : `${res.frame_count} frames`,
       res.frame_count > 0);
  $("btn-sheet").disabled = res.frame_count === 0;
  $("btn-export").disabled = res.frame_count === 0;
  updateRommBudget();
  onFramesChanged();
}

let onFramesChanged = () => {};
export function setFramesHook(fn) { onFramesChanged = fn; }

async function doSheet() {
  if (!state.jobId || !state.frameCount) return;
  busy(true, "Packing sheet…");
  pollProgress(state.jobId);
  try {
    const res = await postJSON(`/api/jobs/${state.jobId}/sheet`, {
      layout: layoutOptions(),
      webp: $("want-webp").checked,
      atlas: $("atlas-with-sheet").checked ? atlasOptions() : null,
    });
    state.sheet = res;
    renderSheet(res);
    onSheetBuilt(res);
  } catch (e) {
    showError(e.message);
  } finally {
    busy(false);
  }
}

let onSheetBuilt = () => {};
export function setSheetHook(fn) { onSheetBuilt = fn; }

function renderSheet(res) {
  const L = res.layout;
  $("sheet-empty").hidden = true;
  $("sheet-result").hidden = false;
  $("dl-sheet").href = `${res.urls.png}?t=${Date.now()}`;
  $("dl-sheet").textContent = `\u2193 ${res.files.png}`;
  const themeUrl = res.urls.atlas;
  const isRomm = $("atlas-format").value === "romm";
  $("dl-theme").hidden = !themeUrl;
  if (themeUrl) {
    $("dl-theme").href = `${themeUrl}?t=${Date.now()}`;
    $("dl-theme").textContent = `\u2193 ${res.files.atlas}`;
  }
  $("take-these-hint").innerHTML = isRomm
    ? "Put <code>sheet.png</code> in your theme folder and copy the " +
      "<code>background.animation</code> block out of <code>theme.json</code>."
    : "The sidecar describes this sheet's frame rects for your engine.";
  chip("sheet-chip", `${L.width}\u00d7${L.height}`, true);
  flowStep(3);
  $("sheet-meta").innerHTML =
    `<b>${L.width}×${L.height}</b> px · grid <b>${L.cols}×${L.rows}</b> · cell ` +
    `<b>${L.cell_w}×${L.cell_h}</b> · padding ${L.padding} · margin ${L.margin} · ` +
    `<b>${res.frame_count}</b> frames`;
  const files = $("sheet-files");
  files.innerHTML = "";
  for (const [kind, url] of Object.entries(res.urls)) {
    if (kind === "png" || kind === "atlas") continue;   // shown as buttons above
    const a = document.createElement("a");
    a.href = url;
    a.download = "";
    a.textContent = `download ${kind}`;
    files.appendChild(a);
  }
  chip("layout-chip", `${L.width}×${L.height} · ${L.cols}×${L.rows}`, true);
  const img = $("sheet-img");
  img.src = `${res.urls.png}?t=${Date.now()}`;
  $("sheet-wrap").hidden = false;

  const rows = res.rects.map(
    (r) => `<tr><td>${r.index}</td><td>${r.x}</td><td>${r.y}</td><td>${r.w}</td><td>${r.h}</td><td>${r.duration_ms}</td></tr>`
  ).join("");
  $("rect-table").innerHTML =
    `<table class="rects"><thead><tr><th>#</th><th>x</th><th>y</th><th>w</th><th>h</th><th>ms</th></tr></thead><tbody>${rows}</tbody></table>`;
}


// ------------------------------------------------------------ preview player

const player = {
  images: [],       // HTMLImageElement per frame
  meta: [],         // {w,h,duration_ms}
  index: 0,
  playing: false,
  timer: null,
  zoom: 1,
};

function stopPlayback() {
  player.playing = false;
  if (player.timer) { clearInterval(player.timer); player.timer = null; }
  $("btn-play").innerHTML = "&#9654; Play";
}

async function loadPreviewFrames() {
  stopPlayback();
  player.images = [];
  player.meta = state.frames.slice();
  $("preview-empty").hidden = !!state.frameCount;
  $("preview-stage-row").hidden = !state.frameCount;
  if (!state.jobId || !state.frameCount) {
    $("btn-play").disabled = true;
    $("frame-total").textContent = "0";
    $("scrubber").max = 0;
    const ctx = $("stage").getContext("2d");
    ctx.clearRect(0, 0, $("stage").width, $("stage").height);
    return;
  }
  const loads = [];
  for (let i = 0; i < state.frameCount; i++) {
    const img = new Image();
    img.src = `/api/jobs/${state.jobId}/frames/${i}.png?t=${Date.now()}`;
    player.images.push(img);
    loads.push(img.decode().catch(() => {}));
  }
  await Promise.all(loads);
  player.index = 0;
  $("scrubber").max = Math.max(state.frameCount - 1, 0);
  $("scrubber").value = 0;
  $("frame-total").textContent = String(state.frameCount);
  chip("preview-chip", `${state.frameCount} frames`, true);
  $("btn-play").disabled = state.frameCount < 1;
  // Adopt the source timing as the default preview rate.
  const ms = player.meta[0] ? player.meta[0].duration_ms : 100;
  const fps = Math.max(1, Math.min(60, Math.round(1000 / Math.max(ms, 1))));
  $("preview-fps").value = fps;
  $("preview-fps-val").textContent = fps;
  drawFrame();
}

function drawFrame() {
  const canvas = $("stage");
  const ctx = canvas.getContext("2d");
  const img = player.images[player.index];
  const m = player.meta[player.index];
  if (!img || !m) return;
  const z = player.zoom;
  canvas.width = Math.max(1, m.w * z);
  canvas.height = Math.max(1, m.h * z);
  ctx.imageSmoothingEnabled = false;
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  if ($("onion").checked && player.images.length > 1) {
    const prev = player.images[(player.index - 1 + player.images.length) % player.images.length];
    if (prev && prev.complete) {
      ctx.globalAlpha = 0.3;
      ctx.drawImage(prev, 0, 0, canvas.width, canvas.height);
      ctx.globalAlpha = 1;
    }
  }
  if (img.complete) ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
  $("frame-index").textContent = String(player.index);
  $("frame-ms").textContent = `${m.duration_ms} ms · ${m.w}×${m.h}`;
  $("scrubber").value = player.index;
}

function togglePlay() {
  if (player.playing) { stopPlayback(); return; }
  if (!player.images.length) return;
  player.playing = true;
  $("btn-play").innerHTML = "&#10073;&#10073; Pause";
  const tick = () => {
    player.index = (player.index + 1) % player.images.length;
    drawFrame();
  };
  const fps = Number($("preview-fps").value) || 12;
  player.timer = setInterval(tick, 1000 / fps);
}

function restartTimerIfPlaying() {
  if (player.playing) { stopPlayback(); togglePlay(); }
}

function wirePreview() {
  $("btn-play").addEventListener("click", togglePlay);
  $("preview-fps").addEventListener("input", () => {
    $("preview-fps-val").textContent = $("preview-fps").value;
    restartTimerIfPlaying();
  });
  $("scrubber").addEventListener("input", () => {
    stopPlayback();
    player.index = Number($("scrubber").value);
    drawFrame();
  });
  $("onion").addEventListener("change", drawFrame);
  $("preview-zoom").addEventListener("input", () => {
    player.zoom = Number($("preview-zoom").value) || 1;
    $("preview-zoom-val").textContent = player.zoom;
    drawFrame();
  });
}

// --------------------------------------------------------------- slice panel

function sliceGeometry() {
  const num = (id) => ($(id).value === "" ? null : Number($(id).value));
  return {
    mode: $("slice-mode").value,
    cols: num("slice-cols"),
    rows: num("slice-rows"),
    cell_w: num("slice-cell-w"),
    cell_h: num("slice-cell-h"),
    padding: num("slice-padding") ?? 0,
    margin: num("slice-margin") ?? 0,
    count: num("slice-count"),
    duration_ms: num("slice-duration") ?? 100,
    drop_empty: $("slice-drop-empty").checked,
  };
}

async function doSlice(file) {
  if (!file) { showError("Choose a sprite sheet image to slice."); return; }
  busy(true, `Slicing ${file.name}…`);
  setBar(20);
  try {
    const fd = new FormData();
    fd.append("file", file);
    fd.append("geometry", JSON.stringify(sliceGeometry()));
    const res = await api("/api/slice", { method: "POST", body: fd });
    state.jobId = res.job_id;
    state.frameCount = res.frame_count;
    state.frames = res.frames;
    state.sheet = null;
    const L = res.layout;
    $("slice-info").innerHTML =
      `Sliced <b>${res.frame_count}</b> frames from a <b>${res.sheet.width}×${res.sheet.height}</b> ` +
      `sheet · grid <b>${L.cols}×${L.rows}</b> · cell <b>${L.cell_w}×${L.cell_h}</b>`;
    chip("frames-tag", `${res.frame_count} frames (sliced)`, true);
    chip("slice-chip", `${res.frame_count} frames from ${res.sheet.width}×${res.sheet.height}`, true);
    $("btn-sheet").disabled = res.frame_count === 0;
    $("btn-export").disabled = res.frame_count === 0;
    onFramesChanged();
  } catch (e) {
    showError(e.message);
  } finally {
    busy(false);
  }
}

function wireSlice() {
  const drop = $("slice-drop");
  const input = $("slice-input");
  let pending = null;
  drop.addEventListener("click", () => input.click());
  input.addEventListener("change", () => {
    if (input.files[0]) { pending = input.files[0]; doSlice(pending); }
  });
  ["dragenter", "dragover"].forEach((ev) =>
    drop.addEventListener(ev, (e) => { e.preventDefault(); drop.style.borderColor = "var(--accent)"; }));
  ["dragleave", "drop"].forEach((ev) =>
    drop.addEventListener(ev, (e) => { e.preventDefault(); drop.style.borderColor = "var(--line)"; }));
  drop.addEventListener("drop", (e) => {
    const f = e.dataTransfer.files[0];
    if (f) { pending = f; doSlice(f); }
  });
  $("btn-slice").addEventListener("click", () => {
    if (pending) doSlice(pending);
    else input.click();
  });
  $("slice-mode").addEventListener("change", () => {
    const cell = $("slice-mode").value === "cell";
    $("slice-grid-row").hidden = cell;
    $("slice-cell-row").hidden = !cell;
  });
}


// ------------------------------------------------------------ generate panel

function generateOptions() {
  const num = (id) => ($(id).value === "" ? null : Number($(id).value));
  return {
    transform: $("gen-transform").value,
    frames: num("gen-frames") ?? 12,
    duration_ms: num("gen-ms") ?? 100,
    easing: $("gen-easing").value,
    loop_mode: $("gen-loop").value,
    nearest: $("nearest").checked,
    direction: $("gen-direction").value,
    distance: num("gen-distance") ?? 32,
    degrees: num("gen-degrees") ?? 360,
    scale_min: num("gen-scale-min") ?? 100,
    scale_max: num("gen-scale-max") ?? 150,
    flip_axis: $("gen-flip-axis").value,
    bounce_height: num("gen-bounce") ?? 24,
    opacity_min: num("gen-op-min") ?? 0,
    opacity_max: num("gen-op-max") ?? 100,
  };
}

function showGenOptions() {
  const t = $("gen-transform").value;
  document.querySelectorAll(".gen-opt").forEach((row) => {
    row.hidden = row.dataset.for !== t;
  });
}

async function doGenerate() {
  if (!state.jobId) return;
  busy(true, "Generating frames…");
  pollProgress(state.jobId);
  try {
    const res = await postJSON(`/api/jobs/${state.jobId}/generate`, {
      generate: generateOptions(),
      frame: frameOptions(),
    });
    adoptFrames(res);
    chip("gen-chip", `${generateOptions().transform} · ${res.frame_count} frames`, true);
  } catch (e) {
    showError(e.message);
  } finally {
    busy(false);
  }
}

function wireGenerate() {
  $("gen-transform").addEventListener("change", showGenOptions);
  $("btn-generate").addEventListener("click", doGenerate);
  showGenOptions();
  setSourceHook((info) => {
    // The transforms take one still frame; offer them for any input's first frame,
    // but call out that a still image is the intended source.
    $("btn-generate").disabled = false;
    $("generate-hint").textContent = info.animated
      ? "This input is animated — generating will replace its frames using frame 0 as the still."
      : "Still image loaded — pick a transform and generate frames.";
  });
}


// ----------------------------------------------------------------- exporting

function exportOptions() {
  const num = (id) => ($(id).value === "" ? null : Number($(id).value));
  return {
    format: $("export-format").value,
    fps: num("export-fps"),
    loop: num("export-loop") ?? 0,
    dither: $("export-dither").checked,
    palette_colors: num("export-colors") ?? 256,
  };
}

async function doExport() {
  if (!state.jobId || !state.frameCount) return;
  const fmt = $("export-format").value;
  if (fmt === "webm" && !state.ffmpeg) {
    showError("WebM export needs ffmpeg, which is not installed.");
    return;
  }
  busy(true, `Exporting ${fmt}…`);
  pollProgress(state.jobId);
  try {
    const res = await postJSON(`/api/jobs/${state.jobId}/export`, { export: exportOptions() });
    const files = $("export-files");
    const a = document.createElement("a");
    a.href = `${res.url}?t=${Date.now()}`;
    a.download = "";
    a.textContent = `download ${res.file} (${(res.size_bytes / 1024).toFixed(1)} KB, ${res.frame_count} frames)`;
    files.prepend(a);
    chip("export-chip", `${res.file} · ${(res.size_bytes / 1024).toFixed(0)} KB`, true);
  } catch (e) {
    showError(e.message);
  } finally {
    busy(false);
  }
}

function wireExport() {
  $("export-format").addEventListener("change", () => {
    $("export-gif-row").hidden = $("export-format").value !== "gif";
  });
  $("btn-export").addEventListener("click", doExport);
}


// -------------------------------------------------------------- atlas panel

function atlasOptions() {
  return {
    format: $("atlas-format").value,
    image_name: "sheet.png",
    animation_name: $("atlas-anim-name").value || "animation",
    romm: rommOptions(),
  };
}

async function doAtlas() {
  if (!state.jobId || !state.sheet) {
    showError("Build a sheet first — metadata describes the packed sheet.");
    return;
  }
  busy(true, "Writing metadata…");
  try {
    const res = await postJSON(`/api/jobs/${state.jobId}/atlas`, { atlas: atlasOptions() });
    addAtlasLink(res);
    renderRommChecks(res.checks);
  } catch (e) {
    showError(e.message);
  } finally {
    busy(false);
  }
}

function addAtlasLink(res) {
  const a = document.createElement("a");
  a.href = `${res.url}?t=${Date.now()}`;
  a.download = "";
  const verdict = res.verified === false
    ? "the client would refuse this sheet — see the checks"
    : "verified against the sheet";
  a.textContent = `download ${res.file} (${res.frame_count} frames, ${verdict})`;
  chip("atlas-chip", res.file, res.verified !== false);
  $("atlas-files").prepend(a);
}

// Keep the slice panel in step with the sheet just built, so a round trip is one click.
function prefillSliceGeometry(res) {
  const L = res.layout;
  $("slice-cols").value = L.cols;
  $("slice-rows").value = L.rows;
  $("slice-cell-w").value = L.cell_w;
  $("slice-cell-h").value = L.cell_h;
  $("slice-padding").value = L.padding;
  $("slice-margin").value = L.margin;
  $("slice-count").value = res.frame_count;
  const ms = state.frames[0] ? state.frames[0].duration_ms : 100;
  $("slice-duration").value = ms;
}

function wireAtlas() {
  $("btn-atlas").addEventListener("click", doAtlas);
  setSheetHook((res) => {
    $("btn-atlas").disabled = false;
    prefillSliceGeometry(res);
    renderRommChecks(res.atlas_checks);
    if (res.files.atlas) {
      addAtlasLink({
        file: res.files.atlas,
        url: res.urls.atlas,
        frame_count: res.frame_count,
        verified: res.atlas_verified,
      });
    }
  });
}


// ----------------------------------------------------------------- RomM mode

let rommLimits = null;

function rommKind() { return $("romm-kind").value; }

function rommCell() {
  // A gif is billed at the full screen per frame, whatever the art's size.
  if (rommKind() === "gif") return [rommLimits ? rommLimits.screen.w : 1280,
                                    rommLimits ? rommLimits.screen.h : 720];
  const v = $("romm-cell").value;
  if (v === "keep") {
    const f = state.frames[0];
    return f ? [f.w, f.h] : [0, 0];
  }
  return v.split("x").map(Number);
}

function rommOptions() {
  const [fw, fh] = rommCell();
  const dim = $("romm-dim").value === "" ? null : Number($("romm-dim").value);
  return {
    kind: rommKind(),
    file: $("romm-file").value || (rommKind() === "gif" ? "background.gif" : "sheet.png"),
    frame_width: fw,
    frame_height: fh,
    fps: Number($("romm-fps").value) || 12,
    loop: $("romm-loop").checked,
    theme_name: $("romm-name").value,
    author: $("romm-author").value,
    background_image: $("romm-still").value,
    dim,
  };
}

// The client reads a tight grid, so the RomM preset drives the other panels
// rather than quietly disagreeing with them.
function applyRommPreset() {
  const [fw, fh] = rommCell();
  if (rommKind() === "gif") {
    // Nothing to pack for a gif: the export panel makes the file, the panel
    // only writes the theme.json block and checks it against the client's caps.
    $("export-format").value = "gif";
    $("export-format").dispatchEvent(new Event("change"));
    $("export-fps").value = $("romm-fps").value;
    if ($("romm-file").value === "sheet.png") $("romm-file").value = "background.gif";
    updateRommBudget();
    return;
  }
  if (fw > 0 && fh > 0 && $("romm-cell").value !== "keep") {
    $("scale-mode").value = "exact";
    $("scale-mode").dispatchEvent(new Event("change"));
    $("scale-w").value = fw;
    $("scale-h").value = fh;
  }
  $("padding").value = 0;
  $("margin").value = 0;
  $("pot").checked = false;
  $("bg-transparent").checked = false;
  $("bg-transparent").dispatchEvent(new Event("change"));
  $("bg-color").value = "#000000";
  $("atlas-format").value = "romm";
  // Prefer a grid with no spare cells, so no frame slot goes unused.
  const n = state.frameCount;
  if (n > 0) {
    const divisors = [];
    for (let c = 1; c <= n; c++) if (n % c === 0) divisors.push(c);
    let best = divisors[0], bestScore = Infinity;
    for (const c of divisors) {
      const rows = n / c;
      const score = Math.abs(c * fw - rows * fh);   // squarest sheet that stays full
      if (score < bestScore) { bestScore = score; best = c; }
    }
    $("auto-square").checked = false;
    $("auto-square").dispatchEvent(new Event("change"));
    $("cols").value = best;
  }
  $("export-fps").value = $("romm-fps").value;
  updateRommBudget();
}

function updateRommBudget() {
  if (!rommLimits) return;
  const gif = rommKind() === "gif";
  $("romm-cell-row").hidden = gif;
  const [fw, fh] = rommCell();
  const n = state.frameCount;
  const perFrame = fw * fh * 4;
  const cap = perFrame > 0
    ? Math.min(rommLimits.max_frames, Math.floor(rommLimits.max_texture_bytes / perFrame))
    : 0;
  $("romm-cap").textContent = cap ? `max ${cap} frames at this size` : "pick a frame size";
  if (!n || !perFrame) { $("romm-budget").textContent = ""; return; }
  const mb = (perFrame * n) / 1048576;
  const billed = gif ? ` (a gif is billed at ${fw}×${fh} per frame)` : "";
  const budget = rommLimits.max_texture_bytes / 1048576;
  const secs = n / (Number($("romm-fps").value) || 12);
  $("romm-budget").innerHTML =
    `<b>${n}</b> frames × ${fw}×${fh} = <b>${mb.toFixed(1)} MB</b> of the ${budget} MB texture ` +
    `budget${billed} · <b>${secs.toFixed(2)}s</b> per cycle` +
    (n > cap ? ` · <span style="color:var(--danger)">over the ${cap}-frame limit</span>` : "");
}

function renderRommChecks(checks) {
  const box = $("romm-checks");
  box.innerHTML = "";
  if (!checks || !checks.length) return;
  const errors = checks.filter((c) => c.level === "error").length;
  const warnings = checks.filter((c) => c.level === "warning").length;
  if (errors) {
    chip("romm-chip", `${errors} blocking`, false);
    openPanel("romm-panel");
  } else {
    chip("romm-chip", warnings ? `ok · ${warnings} warning` : "ok", true);
  }
  const colors = { error: "error", warning: "warn", info: "ok" };
  for (const c of checks) {
    const div = document.createElement("div");
    div.className = `banner ${colors[c.level] || "ok"}`;
    div.style.margin = "0 0 6px";
    div.textContent = c.message;
    box.appendChild(div);
  }
}

async function loadRommLimits() {
  try {
    rommLimits = await api("/api/romm/limits");
    updateRommBudget();
  } catch { /* the panel still works, it just cannot show the budget */ }
}

async function rommCellChanged() {
  updateRommBudget();
  if ($("romm-cell").value === "keep" || !state.jobId) return;
  // The control says "resize to", so it resizes - declaring a frame size the
  // sheet does not have would just produce a blocking check on good art.
  applyRommPreset();
  await doExtract();
  if (state.frameCount) await doSheet();
}

function wireRomm() {
  $("btn-romm-preset").addEventListener("click", applyRommPreset);
  ["romm-cell", "romm-fps"].forEach((id) =>
    $(id).addEventListener("input", updateRommBudget));
  $("romm-kind").addEventListener("change", updateRommBudget);
  $("romm-cell").addEventListener("change", rommCellChanged);
  loadRommLimits();
}

// -------------------------------------------------------------------- wiring

function wireControls() {
  const drop = $("drop");
  const input = $("file-input");
  drop.addEventListener("click", () => input.click());
  input.addEventListener("change", () => { if (input.files[0]) uploadFile(input.files[0]); });
  ["dragenter", "dragover"].forEach((ev) =>
    drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("over"); }));
  ["dragleave", "drop"].forEach((ev) =>
    drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
  drop.addEventListener("drop", (e) => {
    const f = e.dataTransfer.files[0];
    if (f) uploadFile(f);
  });

  $("trim-mode").addEventListener("change", () => {
    const off = $("trim-mode").value === "none";
    $("trim-start").disabled = off;
    $("trim-end").disabled = off;
  });
  $("scale-mode").addEventListener("change", () => {
    const m = $("scale-mode").value;
    $("scale-percent").hidden = m !== "percent";
    $("scale-w").hidden = m !== "exact";
    $("scale-h").hidden = m !== "exact";
  });
  $("auto-square").addEventListener("change", () => {
    $("cols").disabled = $("auto-square").checked;
  });
  $("bg-transparent").addEventListener("change", () => {
    $("bg-color").disabled = $("bg-transparent").checked;
  });

  $("btn-extract").addEventListener("click", doExtract);
  $("btn-sheet").addEventListener("click", doSheet);
}

// ------------------------------------------------------------ ffmpeg banner

// ffmpeg is not bundled (licence, size, and the CVE treadmill that comes with
// shipping someone else's binary), so the one thing that has to be good is the
// path back from "not found" - real steps for the platform you are on, and a
// re-check that does not need a restart, because the app looks for ffmpeg
// beside itself and shutil.which is re-run on every probe.
function ffmpegHelp() {
  const ua = navigator.userAgent;
  const site = '<a href="https://ffmpeg.org/download.html" target="_blank" rel="noopener">ffmpeg.org</a>';
  const zip = '<a href="https://github.com/A-Theme/RomM-Themes/releases/latest" target="_blank" rel="noopener">' +
              "the with-ffmpeg download</a>";
  if (ua.includes("Win")) {
    return {
      os: "Windows",
      steps: [
        `Easiest: grab ${zip} \u2014 it is this app with ffmpeg already beside it.`,
        `Or get a build from ${site}, and drag <code>ffmpeg.exe</code> out of its ` +
          "<code>bin</code> folder into the folder holding <code>spritesheet-maker.exe</code>.",
        "Then click <b>Check again</b> \u2014 no restart, no PATH editing.",
      ],
    };
  }
  if (ua.includes("Mac")) {
    return {
      os: "macOS",
      steps: [
        "Run <code>brew install ffmpeg</code> in Terminal.",
        `Or take a build from ${site} and put the <code>ffmpeg</code> binary beside this app.`,
        "Then click <b>Check again</b> \u2014 no restart needed.",
      ],
    };
  }
  return {
    os: "Linux",
    steps: [
      "Install it: <code>sudo apt install ffmpeg</code>, <code>sudo dnf install ffmpeg</code>, " +
        "or <code>sudo pacman -S ffmpeg</code>.",
      `Or grab ${zip}, or a build from ${site}, and put <code>ffmpeg</code> beside this app.`,
      "Then click <b>Check again</b> \u2014 no restart needed.",
    ],
  };
}

function renderFfmpegBanner() {
  const banner = $("ffmpeg-banner");
  const help = ffmpegHelp();
  banner.className = "banner warn";
  banner.innerHTML =
    "<b>ffmpeg was not found.</b> Video input (mp4, webm, mov, avi) and WebM export " +
    "are off until it is installed. GIF, APNG, animated WebP, still images, every " +
    "sheet and slice operation, and GIF/APNG/ZIP export all work without it." +
    `<ol style="margin:8px 0 8px 18px;padding:0">${help.steps.map((t) => `<li>${t}</li>`).join("")}</ol>` +
    '<button id="ffmpeg-recheck" class="secondary">Check again</button> ' +
    `<span class="hint" id="ffmpeg-recheck-note" style="margin:0">looking for ${help.os} instructions</span>`;
  banner.hidden = false;
  $("ffmpeg-recheck").addEventListener("click", async () => {
    $("ffmpeg-recheck-note").textContent = "checking\u2026";
    const ok = await checkHealth();
    if (!ok) {
      $("ffmpeg-recheck-note").textContent =
        "still not found \u2014 check the file is named exactly ffmpeg" +
        (help.os === "Windows" ? ".exe" : "") + " and sits beside the app";
    }
  });
}

async function checkHealth() {
  try {
    const h = await api("/health");
    state.ffmpeg = !!h.ffmpeg;
    $("health-tag").textContent = h.ffmpeg
      ? `ffmpeg ready \u00b7 max ${h.max_upload_mb}MB`
      : `no ffmpeg \u00b7 max ${h.max_upload_mb}MB`;
    if (h.ffmpeg) {
      const banner = $("ffmpeg-banner");
      if (!banner.hidden) {
        banner.className = "banner ok";
        banner.textContent = `ffmpeg found at ${h.ffmpeg_path} \u2014 video input and WebM export are on.`;
        setTimeout(() => { banner.hidden = true; }, 6000);
      }
      return true;
    }
    renderFfmpegBanner();
    return false;
  } catch (e) {
    showError(`Could not reach the server: ${e.message}`);
    return false;
  }
}

export { state, api, postJSON, busy, setBar, pollProgress, showError, adoptFrames, $ };

setReady(false);
flowStep(1);
wireControls();
wirePreview();
wireSlice();
wireGenerate();
wireExport();
wireAtlas();
wireRomm();
setFramesHook(loadPreviewFrames);
checkHealth();
// ANCHOR:init
