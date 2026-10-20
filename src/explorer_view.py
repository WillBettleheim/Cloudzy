"""The Weather Explorer map: a Streamlit custom component (components v2) built on Leaflet.

Everything drawn comes from the GFS frames prepared by src.explorer_data and served as static
files; the browser fetches only the frames of the layer being shown.

- Basemap: Natural Earth coastlines, land borders and major cities (public domain), drawn by
  Leaflet's canvas renderer. No tile service, no key.
- Raster layers: each screen block is mapped to latitude/longitude and the 1° grid is sampled with
  bilinear interpolation, then coloured with the layer's legend scale.
- Wind: particles are advected by the frame's own u/v wind components (bilinear). Under reduced
  motion, or when motion is switched off, short static streamlines are drawn instead.
- Pressure: isobars every 4 hPa are traced on the 1° sea-level pressure grid (marching squares).
- Timeline: the run's 3-hourly steps, with play/pause, previous/next, a slider, speed and "now".
  Frames are cached (bounded LRU), prefetched one step ahead, and stale requests are aborted.
- A click selects a point: the map shows the grid value there, and Python receives the coordinates
  (to show a separate point forecast).
"""

from __future__ import annotations

JS = r"""
const LEAFLET = "app/static/vendor/leaflet/leaflet-src.esm.js";
const LEAFLET_CSS = "app/static/vendor/leaflet/leaflet.css";
const BASEMAP = "app/static/vendor/naturalearth/basemap.json";
const CACHE_MAX = 64;

const SCALES = {
  temperature: { v: "t2m", stops: [[-40,"#2a1b5f"],[-25,"#3c3fae"],[-10,"#3f7fd6"],[0,"#62b6e0"],[8,"#8fd3c0"],[16,"#e8e08c"],[24,"#f5a54a"],[32,"#e5582f"],[42,"#a31d3a"]], alpha: 0.62 },
  wind: { v: "speed", stops: [[0,"#2c4a80"],[3,"#3480c4"],[6,"#36b0c9"],[10,"#5cc79a"],[14,"#e6d36a"],[20,"#f08c46"],[28,"#d6455a"],[40,"#9b2f7f"]], alpha: 0.38 },
  gusts: { v: "gust", stops: [[0,"#2c4a80"],[5,"#3480c4"],[10,"#36b0c9"],[15,"#5cc79a"],[20,"#e6d36a"],[28,"#f08c46"],[36,"#d6455a"],[50,"#9b2f7f"]], alpha: 0.55 },
  clouds: { v: "tcc", stops: [[0,"#ffffff"],[100,"#ffffff"]], alpha: 0.0, cloud: true },
  humidity: { v: "rh2m", stops: [[0,"#8a5a3c"],[25,"#c79a5b"],[50,"#d9d6a0"],[75,"#6cc0a5"],[100,"#2a7fb8"]], alpha: 0.5 },
  cape: { v: "cape", stops: [[0,"#000000"],[100,"#fff3a8"],[500,"#ffd36b"],[1000,"#ff9d4d"],[2000,"#f0544f"],[3500,"#b8336a"],[5000,"#6c2a8f"]], alpha: 0.6, floor: 100 },
  pressure: { v: "prmsl", stops: [[960,"#4b2c8c"],[985,"#3b6fb6"],[1000,"#5fa6c9"],[1013,"#93b7c2"],[1025,"#d9b77b"],[1040,"#d9822b"]], alpha: 0.28 },
  rain: { stops: [[0.1,"#5ac8fa"],[1,"#2f8cf0"],[3,"#3b57d6"],[8,"#7b3fd1"],[20,"#c42f8c"],[50,"#f04b4b"]] },
  snow: { stops: [[0.1,"#dff1ff"],[1,"#bcd7ff"],[3,"#9fb2ff"],[8,"#b28cff"]] },
  mixed: { stops: [[0.1,"#ffb0d0"],[1,"#ff7fb4"],[5,"#e84d8a"]] },
};
const LAYER_INFO = {
  none: { label: "Basemap only", icon: "map" },
  wind: { label: "Wind", icon: "air" },
  temperature: { label: "Temperature", icon: "thermostat" },
  precipitation: { label: "Precipitation", icon: "rainy" },
  clouds: { label: "Cloud cover", icon: "cloud" },
  pressure: { label: "Pressure", icon: "compress" },
  gusts: { label: "Wind gusts", icon: "storm" },
  humidity: { label: "Humidity", icon: "humidity_percentage" },
  cape: { label: "CAPE", icon: "bolt" },
};

function hexToRgb(h) { const n = parseInt(h.slice(1), 16); return [(n >> 16) & 255, (n >> 8) & 255, n & 255]; }
function makeRamp(stops) {
  const s = stops.map(([v, c]) => [v, hexToRgb(c)]);
  return (v) => {
    if (v <= s[0][0]) return s[0][1];
    for (let i = 1; i < s.length; i++) {
      if (v <= s[i][0]) {
        const t = (v - s[i - 1][0]) / (s[i][0] - s[i - 1][0]);
        const a = s[i - 1][1], b = s[i][1];
        return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t];
      }
    }
    return s[s.length - 1][1];
  };
}

// --- Units (display only; data stays metric) ---
function fmtTemp(c, u) { return u === "°F" ? (c * 9 / 5 + 32) : c; }
function fmtWind(ms, u) { return u === "km/h" ? ms * 3.6 : u === "mph" ? ms * 2.23694 : u === "kn" ? ms * 1.94384 : ms; }
function fmtRate(mmh, u) { return u === "in" ? mmh / 25.4 : mmh; }

export default function (component) {
  const { data, parentElement, setTriggerValue } = component;
  if (!data) return;
  let S = parentElement.__cz;
  if (!S) {
    S = parentElement.__cz = { data, ready: false };
    init(S, parentElement, setTriggerValue).catch((e) => fatal(S, parentElement, e));
  } else {
    S.data = data;
    if (S.ready) applyData(S);
  }
}

function fatal(S, root, e) {
  const box = root.querySelector(".czx-status") || root;
  box.textContent = "The map couldn't start: " + (e && e.message ? e.message : e) + ". The rest of Cloudzy still works.";
  box.classList && box.classList.add("show", "error");
}

async function init(S, root, setTriggerValue) {
  // Append (never replace root's contents: Streamlit puts this component's CSS in a <style> there).
  const wrap = document.createElement("div");
  root.appendChild(wrap);
  wrap.innerHTML = `
    <div class="czx" role="region" aria-label="Weather Explorer map">
      <div class="czx-map" tabindex="0" aria-label="Map. Use arrow keys to pan and plus or minus to zoom. Click to select a point."></div>
      <div class="czx-top">
        <div class="czx-layers" role="toolbar" aria-label="Weather layers"></div>
      </div>
      <div class="czx-side">
        <button type="button" class="czx-btn" data-act="world" aria-label="Reset to world view" title="World view"><span class="ico">public</span></button>
        <button type="button" class="czx-btn" data-act="city" aria-label="Recenter on the selected place" title="Recenter on the selected place"><span class="ico">my_location</span></button>
        <button type="button" class="czx-btn" data-act="motion" aria-pressed="true" aria-label="Animate wind particles" title="Wind particle motion"><span class="ico">animation</span></button>
      </div>
      <div class="czx-legend" aria-live="polite"></div>
      <div class="czx-readout" aria-live="polite"></div>
      <div class="czx-status show" role="status">Loading map…</div>
      <div class="czx-time">
        <div class="czx-tl-row">
          <button type="button" class="czx-btn" data-act="prev" aria-label="Previous forecast step"><span class="ico">skip_previous</span></button>
          <button type="button" class="czx-btn play" data-act="play" aria-label="Play forecast animation"><span class="ico">play_arrow</span></button>
          <button type="button" class="czx-btn" data-act="next" aria-label="Next forecast step"><span class="ico">skip_next</span></button>
          <input class="czx-slider" type="range" min="0" max="0" step="1" value="0" aria-label="Forecast time" />
          <button type="button" class="czx-btn now" data-act="now" aria-label="Jump to the step nearest to now">Now</button>
          <label class="czx-speed-l"><span class="sr">Animation speed</span>
            <select class="czx-speed" aria-label="Animation speed"><option value="0.5">0.5×</option><option value="1" selected>1×</option><option value="2">2×</option></select>
          </label>
        </div>
        <div class="czx-tl-label" aria-live="polite"></div>
      </div>
      <div class="czx-attr"></div>
    </div>`;
  S.root = root;
  S.el = (s) => root.querySelector(s);
  const status = (t, kind) => { const b = S.el(".czx-status"); b.textContent = t || ""; b.className = "czx-status" + (t ? " show" : "") + (kind ? " " + kind : ""); };
  S.status = status;

  // Leaflet CSS goes inside this component's root (it may be a shadow root).
  const css = await fetch(new URL(LEAFLET_CSS, document.baseURI)).then((r) => { if (!r.ok) throw new Error("Leaflet CSS " + r.status); return r.text(); });
  const st = document.createElement("style"); st.textContent = css; root.prepend(st);
  const L = await import(new URL(LEAFLET, document.baseURI).href);
  S.L = L;

  const reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  S.motion = !reduce;
  S.el('[data-act="motion"]').setAttribute("aria-pressed", String(S.motion));

  const map = L.map(S.el(".czx-map"), { worldCopyJump: true, minZoom: 1, maxZoom: 9, zoomControl: false,
    preferCanvas: true, attributionControl: false, zoomSnap: 0.5, maxBounds: [[-89, -540], [89, 540]], maxBoundsViscosity: 0.8 });
  S.map = map;
  L.control.zoom({ position: "topright" }).addTo(map);
  map.setView([20, 10], window.innerWidth < 640 ? 1 : 2);

  map.createPane("weather"); map.getPane("weather").style.zIndex = 350; map.getPane("weather").style.pointerEvents = "none";
  map.createPane("lines"); map.getPane("lines").style.zIndex = 420; map.getPane("lines").style.pointerEvents = "none";
  map.createPane("labels"); map.getPane("labels").style.zIndex = 450; map.getPane("labels").style.pointerEvents = "none";
  S.lineRenderer = L.canvas({ pane: "lines", padding: 0.5 });

  // Basemap (Natural Earth): coastlines and land borders, drawn above the weather for context.
  try {
    const bm = await fetch(new URL(BASEMAP, document.baseURI)).then((r) => { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); });
    const toLL = (line) => line.map(([x, y]) => [y, x]);
    for (const off of [-360, 0, 360]) {
      const shift = (line) => line.map(([x, y]) => [y, x + off]);
      L.polyline(bm.coast.map(off ? shift : toLL), { renderer: S.lineRenderer, color: "#9fb7d9", weight: 0.9, opacity: 0.75, interactive: false }).addTo(map);
      L.polyline(bm.borders.map(off ? shift : toLL), { renderer: S.lineRenderer, color: "#7f93b8", weight: 0.6, opacity: 0.5, dashArray: "3 3", interactive: false }).addTo(map);
    }
    S.cities = bm.cities;
    S.cityLayer = L.layerGroup().addTo(map);
    const drawCities = () => {
      S.cityLayer.clearLayers();
      const z = map.getZoom();
      const maxRank = z < 2 ? 0 : z < 3 ? 1 : z < 4 ? 3 : z < 5 ? 5 : 8;
      for (const [name, lat, lon, rank] of S.cities) {
        if (rank > maxRank) continue;
        L.marker([lat, lon], { pane: "labels", interactive: false, keyboard: false,
          icon: L.divIcon({ className: "czx-city", html: `<span>${name.replace(/[<>&]/g, "")}</span>`, iconSize: null }) }).addTo(S.cityLayer);
      }
    };
    map.on("zoomend", drawCities); drawCities();
  } catch (e) {
    status("The basemap couldn't load (" + e.message + "). Weather layers still work.", "warn");
  }

  // Weather canvas overlay (raster + isobars), and the particle canvas above it.
  const pane = map.getPane("weather");
  S.canvas = document.createElement("canvas"); S.canvas.className = "czx-canvas"; pane.appendChild(S.canvas);
  S.pcanvas = document.createElement("canvas"); S.pcanvas.className = "czx-canvas"; pane.appendChild(S.pcanvas);
  map.on("movestart zoomstart", () => { S.moving = true; clearParticles(S); });
  map.on("moveend zoomend resize", () => { S.moving = false; placePanels(S); redraw(S); });
  map.on("click", (ev) => {
    const ll = ev.latlng.wrap();
    S.picked = { lat: +ll.lat.toFixed(3), lon: +ll.lng.toFixed(3) };
    drawPicked(S); readout(S);
    setTriggerValue("picked", { lat: S.picked.lat, lon: S.picked.lon, t: Date.now() });
  });

  // Controls.
  const layersEl = S.el(".czx-layers");
  for (const [key, info] of Object.entries(LAYER_INFO)) {
    const b = document.createElement("button");
    b.type = "button"; b.className = "czx-layer"; b.dataset.layer = key;
    b.setAttribute("aria-pressed", "false");
    b.innerHTML = `<span class="ico" aria-hidden="true">${info.icon}</span><span class="t">${info.label}</span>`;
    b.addEventListener("click", () => setLayer(S, key));
    layersEl.appendChild(b);
  }
  root.addEventListener("click", (ev) => {
    const b = ev.target.closest && ev.target.closest("[data-act]");
    if (!b) return;
    const act = b.dataset.act;
    if (act === "world") map.setView([20, 10], window.innerWidth < 640 ? 1 : 2);
    if (act === "city" && S.data.city) map.setView([S.data.city.lat, S.data.city.lon], Math.max(map.getZoom(), 5));
    if (act === "prev") step(S, -1);
    if (act === "next") step(S, 1);
    if (act === "play") togglePlay(S);
    if (act === "now") goNow(S);
    if (act === "motion") { S.motion = !S.motion; b.setAttribute("aria-pressed", String(S.motion)); redraw(S); }
  });
  S.el(".czx-slider").addEventListener("input", (ev) => { stopPlay(S); setFrame(S, +ev.target.value); });
  S.el(".czx-speed").addEventListener("change", (ev) => { S.speed = +ev.target.value; });
  S.speed = 1;
  document.addEventListener("visibilitychange", () => { if (document.hidden) stopPlay(S); });

  S.cache = new Map(); S.layer = S.data.layer || "wind"; S.frameIdx = 0; S.ready = true;
  status("Loading forecast data…");
  await loadManifest(S);
  applyData(S);
  setLayer(S, S.layer, true);
  goNow(S);
  S.poll = setInterval(() => { if (!S.root.isConnected) return teardown(S); loadManifest(S); }, 30000);
}

function teardown(S) {
  clearInterval(S.poll); stopPlay(S); cancelAnimationFrame(S.raf);
  if (S.abort) S.abort.abort();
  try { S.map.remove(); } catch (e) {}
  S.cache.clear(); S.ready = false;
}

async function loadManifest(S) {
  try {
    const r = await fetch(new URL(S.data.manifest, document.baseURI).href + "?t=" + Date.now(), { cache: "no-store" });
    if (r.status === 404) { S.status("Preparing global forecast data… the first run of the day takes about a minute."); return; }
    if (!r.ok) throw new Error("HTTP " + r.status);
    const m = await r.json();
    const changed = !S.manifest || m.run_id !== S.manifest.run_id || m.updated_utc !== S.manifest.updated_utc;
    S.manifest = m;
    S.frames = m.frames.filter((f) => f.ready);
    if (!S.frames.length) { S.status("Preparing global forecast data…"); return; }
    if (m.run_id !== S.runSeen) { S.cache.clear(); S.runSeen = m.run_id; }
    const sl = S.el(".czx-slider"); sl.max = String(S.frames.length - 1);
    S.status(m.status === "complete" ? "" : `Preparing forecast steps: ${S.frames.length} of ${m.frames.length} ready…`, m.status === "complete" ? "" : "info");
    S.el(".czx-attr").innerHTML = `${m.attribution} · ${m.grid.resolution} grid · run ${m.run_utc.replace("T", " ")} · Basemap: Natural Earth · Map: <a href="https://leafletjs.com" target="_blank" rel="noopener">Leaflet</a>`;
    if (changed) { S.frameIdx = Math.min(S.frameIdx, S.frames.length - 1); redraw(S); }
  } catch (e) {
    S.status("Forecast data couldn't be loaded (" + e.message + "). Retrying shortly.", "error");
  }
}

function applyData(S) {
  const L = S.L, d = S.data;
  S.units = d.units || { temperature: "°C", wind: "km/h", precipitation: "mm" };
  if (S.cityMarker) { S.cityMarker.remove(); S.cityMarker = null; }
  if (d.city) {
    S.cityMarker = L.circleMarker([d.city.lat, d.city.lon], { pane: "labels", radius: 7, color: "#f5b800", weight: 2, fillColor: "#0b1630", fillOpacity: 0.9 })
      .bindTooltip(d.city.label.replace(/[<>&]/g, ""), { direction: "top", className: "czx-tip" }).addTo(S.map);
    if (!S.centeredOnce) { S.centeredOnce = true; S.map.setView([d.city.lat, d.city.lon], Math.max(S.map.getZoom(), 3)); }
  }
  S.el('[data-act="city"]').disabled = !d.city;
  legend(S); readout(S); redraw(S);
}

function drawPicked(S) {
  const L = S.L;
  if (S.pickMarker) S.pickMarker.remove();
  if (!S.picked) return;
  S.pickMarker = L.circleMarker([S.picked.lat, S.picked.lon], { pane: "labels", radius: 6, color: "#ffffff", weight: 2, fillColor: "#5aa2f0", fillOpacity: 1 }).addTo(S.map);
}

// --- Frames ---
function framePath(S, fhr, v) { return `${S.manifest.base_url}/f${String(fhr).padStart(3, "0")}_${v}.bin`; }
function varsFor(layer) {
  return { none: [], wind: ["u10", "v10"], temperature: ["t2m"], precipitation: ["prate", "ptype"], clouds: ["tcc"],
           pressure: ["prmsl"], gusts: ["gust"], humidity: ["rh2m"], cape: ["cape"] }[layer] || [];
}
async function getField(S, fhr, v, signal) {
  const key = `${S.manifest.run_id}/${fhr}/${v}`;
  if (S.cache.has(key)) { const f = S.cache.get(key); S.cache.delete(key); S.cache.set(key, f); return f; }
  const r = await fetch(new URL(framePath(S, fhr, v), document.baseURI), { signal });
  if (r.status === 404) return null;                       // field not in this frame: shown as unavailable
  if (!r.ok) throw new Error("HTTP " + r.status);
  const q = new Uint16Array(await r.arrayBuffer());
  const enc = S.manifest.encoding[v], miss = S.manifest.missing;
  const f = new Float32Array(q.length);
  for (let i = 0; i < q.length; i++) f[i] = q[i] === miss ? NaN : enc.offset + q[i] * enc.scale;
  S.cache.set(key, f);
  while (S.cache.size > CACHE_MAX) S.cache.delete(S.cache.keys().next().value);
  return f;
}
async function loadFrame(S, idx) {
  const fr = S.frames[idx]; const vars = varsFor(S.layer);
  if (S.abort) S.abort.abort();
  const ac = new AbortController(); S.abort = ac;
  const out = {};
  for (const v of vars) out[v] = await getField(S, fr.fhr, v, ac.signal);
  return out;
}

async function setFrame(S, idx) {
  if (!S.frames || !S.frames.length) return;
  S.frameIdx = Math.max(0, Math.min(idx, S.frames.length - 1));
  S.el(".czx-slider").value = String(S.frameIdx);
  timeLabel(S);
  await redraw(S);
  const next = S.frames[S.frameIdx + 1];             // prefetch one step ahead (cheap, cached)
  if (next) for (const v of varsFor(S.layer)) getField(S, next.fhr, v).catch(() => {});
}
function step(S, d) { stopPlay(S); setFrame(S, (S.frameIdx + d + S.frames.length) % S.frames.length); }
function goNow(S) {
  if (!S.frames || !S.frames.length) return;
  const now = Date.now(); let best = 0, bd = Infinity;
  S.frames.forEach((f, i) => { const d = Math.abs(Date.parse(f.valid_utc) - now); if (d < bd) { bd = d; best = i; } });
  stopPlay(S); setFrame(S, best);
}
function togglePlay(S) { S.playing ? stopPlay(S) : startPlay(S); }
function startPlay(S) {
  if (!S.frames || S.frames.length < 2) return;
  S.playing = true;
  const b = S.el('[data-act="play"]'); b.innerHTML = '<span class="ico">pause</span>'; b.setAttribute("aria-label", "Pause forecast animation");
  const tick = async () => {
    if (!S.playing) return;
    await setFrame(S, (S.frameIdx + 1) % S.frames.length);   // waits for the data: no frame shows stale data
    if (S.playing) S.timer = setTimeout(tick, 900 / S.speed);
  };
  S.timer = setTimeout(tick, 300);
}
function stopPlay(S) {
  S.playing = false; clearTimeout(S.timer);
  const b = S.el && S.el('[data-act="play"]');
  if (b) { b.innerHTML = '<span class="ico">play_arrow</span>'; b.setAttribute("aria-label", "Play forecast animation"); }
}
function timeLabel(S) {
  const f = S.frames[S.frameIdx]; if (!f) return;
  const t = new Date(f.valid_utc);
  const local = t.toLocaleString(undefined, { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
  const utc = f.valid_utc.slice(5, 16).replace("T", " ");
  S.el(".czx-tl-label").innerHTML = `<b>${local}</b> <span class="mut">(your time) · ${utc} UTC · +${f.fhr} h from the ${S.manifest.run_utc.slice(11, 16)} UTC run · step ${S.frameIdx + 1} of ${S.frames.length} · forecast${f.fhr === 0 ? " (model analysis)" : ""}</span>`;
  S.el(".czx-slider").setAttribute("aria-valuetext", `${local}, plus ${f.fhr} hours`);
  placePanels(S);
}
// Keep the legend and readout just above the timeline, whatever height its label wraps to.
function placePanels(S) {
  const t = S.el(".czx-time"); if (!t) return;
  const bottom = (t.offsetHeight + parseFloat(getComputedStyle(t).bottom || "0") + 10) + "px";
  S.el(".czx-legend").style.bottom = bottom; S.el(".czx-readout").style.bottom = bottom;
}

function setLayer(S, key, silent) {
  S.layer = key;
  for (const b of S.root.querySelectorAll(".czx-layer")) b.setAttribute("aria-pressed", String(b.dataset.layer === key));
  S.el('[data-act="motion"]').hidden = key !== "wind";
  legend(S); readout(S);
  if (S.frames) setFrame(S, S.frameIdx);
}

// --- Rendering ---
function sampler(grid) {
  return (lat, lon) => {
    const y = 90 - lat, x = ((lon % 360) + 360) % 360;
    const y0 = Math.floor(y), x0 = Math.floor(x), fy = y - y0, fx = x - x0;
    const y1 = Math.min(y0 + 1, 180), x1 = (x0 + 1) % 360;
    const a = grid[y0 * 360 + x0], b = grid[y0 * 360 + x1], c = grid[y1 * 360 + x0], d = grid[y1 * 360 + x1];
    return (a * (1 - fx) + b * fx) * (1 - fy) + (c * (1 - fx) + d * fx) * fy;   // NaN if any is missing
  };
}

async function redraw(S) {
  if (!S.ready || !S.manifest || !S.frames || !S.frames.length || S.moving) return;
  const token = (S.drawToken = (S.drawToken || 0) + 1);
  const map = S.map, size = map.getSize();
  for (const c of [S.canvas, S.pcanvas]) {
    c.width = size.x; c.height = size.y; c.style.width = size.x + "px"; c.style.height = size.y + "px";
    S.L.DomUtil.setPosition(c, map.containerPointToLayerPoint([0, 0]));
  }
  cancelAnimationFrame(S.raf);
  const layerChanged = S.drawnLayer !== S.layer;
  if (layerChanged) { S.canvas.style.opacity = "0"; S.pcanvas.style.opacity = "0"; }
  const ctx = S.canvas.getContext("2d"); ctx.clearRect(0, 0, size.x, size.y);
  clearParticles(S);
  if (S.layer === "none") { S.status(""); S.drawnLayer = "none"; return; }
  let fields;
  try {
    S.loading = true; S.root.querySelector(".czx").classList.add("busy");
    fields = await loadFrame(S, S.frameIdx);
  } catch (e) {
    if (e.name === "AbortError") return;
    S.status("This forecast step couldn't be loaded (" + e.message + ").", "error"); return;
  } finally { S.loading = false; S.root.querySelector(".czx").classList.remove("busy"); }
  if (token !== S.drawToken) return;                       // a newer redraw started: drop this one
  const missing = Object.entries(fields).filter(([, f]) => !f).map(([v]) => v);
  if (missing.length && !(S.layer === "precipitation" && missing.length === 1 && missing[0] === "ptype")) {
    S.status(`${LAYER_INFO[S.layer].label} isn't available for this step in the GFS output. Try another step or layer.`, "warn");
    return;
  }
  S.status(S.manifest.status === "complete" ? "" : S.el(".czx-status").textContent, S.manifest.status === "complete" ? "" : "info");
  S.fields = fields;
  // Pixel → lat/lon lookup: longitude depends only on x and latitude only on y (Web Mercator).
  const STEP = size.x * size.y > 900000 ? 4 : 3;
  const lats = new Float64Array(size.y), lons = new Float64Array(size.x);
  for (let y = 0; y < size.y; y++) lats[y] = map.containerPointToLatLng([0, y]).lat;
  for (let x = 0; x < size.x; x++) lons[x] = map.containerPointToLatLng([x, 0]).lng;
  S.lats = lats; S.lons = lons;
  const img = ctx.createImageData(size.x, size.y), px = img.data;
  const paint = (fn) => {
    for (let y = 0; y < size.y; y += STEP) {
      const lat = lats[y]; if (lat > 90 || lat < -90) continue;
      for (let x = 0; x < size.x; x += STEP) {
        const c = fn(lat, lons[x]); if (!c) continue;
        for (let dy = 0; dy < STEP && y + dy < size.y; dy++) for (let dx = 0; dx < STEP && x + dx < size.x; dx++) {
          const i = ((y + dy) * size.x + x + dx) * 4; px[i] = c[0]; px[i + 1] = c[1]; px[i + 2] = c[2]; px[i + 3] = c[3];
        }
      }
    }
  };
  const L_ = S.layer;
  if (L_ === "precipitation") {
    const rate = sampler(fields.prate), type = fields.ptype ? sampler(fields.ptype) : null;
    const ramps = { 1: makeRamp(SCALES.rain.stops), 2: makeRamp(SCALES.snow.stops), 3: makeRamp(SCALES.mixed.stops) };
    paint((la, lo) => {
      const r = rate(la, lo); if (!(r >= 0.1)) return null;
      let t = type ? Math.round(type(la, lo)) : 1; if (!(t >= 1)) t = 1;
      const c = ramps[t](r); return [c[0], c[1], c[2], Math.min(235, 120 + r * 18)];
    });
  } else if (L_ === "clouds") {
    const s = sampler(fields.tcc);
    paint((la, lo) => { const v = s(la, lo); if (!(v > 5)) return null; return [235, 241, 250, Math.round(v / 100 * 190)]; });
  } else if (L_ === "wind") {
    const u = sampler(fields.u10), v = sampler(fields.v10), ramp = makeRamp(SCALES.wind.stops);
    paint((la, lo) => { const a = u(la, lo), b = v(la, lo); if (!isFinite(a) || !isFinite(b)) return null; const c = ramp(Math.hypot(a, b)); return [c[0], c[1], c[2], 255 * SCALES.wind.alpha]; });
  } else {
    const sc = SCALES[L_], s = sampler(fields[sc.v]), ramp = makeRamp(sc.stops);
    paint((la, lo) => { const v = s(la, lo); if (!isFinite(v)) return null; if (sc.floor && v < sc.floor) return null; const c = ramp(v); return [c[0], c[1], c[2], 255 * sc.alpha]; });
  }
  ctx.putImageData(img, 0, 0);
  S.drawnLayer = L_;
  requestAnimationFrame(() => { S.canvas.style.opacity = "1"; S.pcanvas.style.opacity = "1"; });
  if (L_ === "pressure") isobars(S, ctx, fields.prmsl);
  if (L_ === "wind") startParticles(S);
  readout(S);
}

// Isobars every 4 hPa: marching squares on the 1° grid, drawn through the map projection.
function isobars(S, ctx, g) {
  const map = S.map, bounds = map.getBounds();
  const latMin = Math.max(-90, Math.floor(bounds.getSouth()) - 1), latMax = Math.min(90, Math.ceil(bounds.getNorth()) + 1);
  const lonMin = Math.floor(bounds.getWest()) - 1, lonMax = Math.ceil(bounds.getEast()) + 1;
  const at = (la, lo) => g[(90 - la) * 360 + (((lo % 360) + 360) % 360)];
  const proj = (la, lo) => map.latLngToContainerPoint([la, lo]);
  ctx.save(); ctx.strokeStyle = "rgba(235,242,255,0.75)"; ctx.lineWidth = 1.1;
  ctx.font = "11px system-ui, sans-serif"; ctx.fillStyle = "rgba(235,242,255,0.95)";
  // Wider spacing when zoomed out, and labels kept apart in screen space, so the map stays readable.
  const interval = map.getZoom() < 3 ? 8 : 4;
  const placed = [];
  const canLabel = (x, y) => placed.every(([px, py]) => Math.hypot(px - x, py - y) > 110);
  for (let level = 944; level <= 1060; level += interval) {
    let perLevel = 0;
    ctx.beginPath();
    for (let la = latMax; la > latMin; la--) {
      for (let lo = lonMin; lo < lonMax; lo++) {
        const a = at(la, lo), b = at(la, lo + 1), c = at(la - 1, lo + 1), d = at(la - 1, lo);
        if (![a, b, c, d].every(isFinite)) continue;
        const pts = [];
        const edge = (v1, v2, p1, p2) => { if ((v1 < level) !== (v2 < level)) { const t = (level - v1) / (v2 - v1); pts.push([p1[0] + (p2[0] - p1[0]) * t, p1[1] + (p2[1] - p1[1]) * t]); } };
        edge(a, b, [la, lo], [la, lo + 1]); edge(b, c, [la, lo + 1], [la - 1, lo + 1]);
        edge(c, d, [la - 1, lo + 1], [la - 1, lo]); edge(d, a, [la - 1, lo], [la, lo]);
        for (let i = 0; i + 1 < pts.length; i += 2) {
          const p = proj(pts[i][0], pts[i][1]), q = proj(pts[i + 1][0], pts[i + 1][1]);
          ctx.moveTo(p.x, p.y); ctx.lineTo(q.x, q.y);
          if (perLevel < 6 && canLabel(p.x, p.y)) {
            placed.push([p.x, p.y]); perLevel++;
            ctx.save(); ctx.fillStyle = "rgba(7,16,39,.75)"; const w = ctx.measureText(String(level)).width;
            ctx.fillRect(p.x - 2, p.y - 11, w + 4, 13); ctx.restore(); ctx.fillText(String(level), p.x, p.y - 1);
          }
        }
      }
    }
    ctx.stroke();
  }
  ctx.restore();
}

// Wind particles advected by the frame's u/v components.
function clearParticles(S) { cancelAnimationFrame(S.raf); if (S.pcanvas) S.pcanvas.getContext("2d").clearRect(0, 0, S.pcanvas.width, S.pcanvas.height); }
function startParticles(S) {
  const map = S.map, size = map.getSize(), ctx = S.pcanvas.getContext("2d");
  const u = sampler(S.fields.u10), v = sampler(S.fields.v10), ramp = makeRamp(SCALES.wind.stops);
  const n = Math.round(Math.min(3200, (size.x * size.y) / 340));
  const scale = 0.18 * Math.pow(1.35, map.getZoom() - 2);
  const spawn = () => ({ x: Math.random() * size.x, y: Math.random() * size.y, age: Math.floor(Math.random() * 80) });
  const ps = Array.from({ length: n }, spawn);
  const vel = (p) => {
    const lat = S.lats[Math.max(0, Math.min(size.y - 1, p.y | 0))], lon = S.lons[Math.max(0, Math.min(size.x - 1, p.x | 0))];
    const a = u(lat, lon), b = v(lat, lon);
    if (!isFinite(a) || !isFinite(b)) return null;
    const k = scale / Math.max(0.2, Math.cos(lat * Math.PI / 180));
    return [a * k, -b * k, Math.hypot(a, b)];
  };
  if (!S.motion) {           // reduced motion: short static streamlines from the same vectors
    ctx.lineWidth = 1;
    for (let i = 0; i < n / 3; i++) {
      const p = spawn(); ctx.beginPath(); ctx.moveTo(p.x, p.y); let sp = 0;
      for (let k = 0; k < 14; k++) { const w = vel(p); if (!w) break; p.x += w[0] * 2; p.y += w[1] * 2; sp = w[2]; ctx.lineTo(p.x, p.y); }
      const c = ramp(sp); ctx.strokeStyle = `rgba(${c[0] | 0},${c[1] | 0},${c[2] | 0},0.9)`; ctx.stroke();
    }
    return;
  }
  const frame = () => {
    if (!S.root.isConnected) return teardown(S);
    if (document.hidden || S.moving) { S.raf = requestAnimationFrame(frame); return; }
    ctx.globalCompositeOperation = "destination-in"; ctx.fillStyle = "rgba(0,0,0,0.89)"; ctx.fillRect(0, 0, size.x, size.y);
    ctx.globalCompositeOperation = "source-over"; ctx.lineWidth = 1.2;
    for (const p of ps) {
      const w = vel(p);
      if (!w || p.age-- <= 0 || p.x < 0 || p.y < 0 || p.x >= size.x || p.y >= size.y) { Object.assign(p, spawn(), { age: 60 + Math.random() * 40 }); continue; }
      const c = ramp(w[2]);
      ctx.strokeStyle = `rgba(${c[0] | 0},${Math.min(255, c[1] + 40) | 0},${Math.min(255, c[2] + 40) | 0},0.9)`;
      ctx.beginPath(); ctx.moveTo(p.x, p.y); p.x += w[0]; p.y += w[1]; ctx.lineTo(p.x, p.y); ctx.stroke();
    }
    S.raf = requestAnimationFrame(frame);
  };
  S.raf = requestAnimationFrame(frame);
}

// --- Legend and point readout ---
function legend(S) {
  const el = S.el(".czx-legend"), u = S.units || {}, L_ = S.layer;
  const bar = (stops, conv, unit, digits = 0) => {
    const g = stops.map(([v, c], i) => `${c} ${Math.round(i / (stops.length - 1) * 100)}%`).join(",");
    const ticks = stops.filter((_, i) => i % Math.ceil(stops.length / 5) === 0 || i === stops.length - 1)
      .map(([v]) => `<span>${conv(v).toFixed(digits)}</span>`).join("");
    return `<div class="bar" style="background:linear-gradient(90deg,${g})"></div><div class="ticks">${ticks}</div><div class="unit">${unit}</div>`;
  };
  const head = (t, kind) => `<div class="lg-h"><span class="ico" aria-hidden="true">${LAYER_INFO[L_].icon}</span><b>${t}</b><span class="kind">${kind}</span></div>`;
  const kind = "GFS forecast";
  let html = "";
  if (L_ === "none") html = `<div class="lg-h"><b>Basemap only</b></div><div class="note">Pick a layer above to show forecast data.</div>`;
  else if (L_ === "temperature") html = head("Temperature at 2 m", kind) + bar(SCALES.temperature.stops, (v) => fmtTemp(v, u.temperature), u.temperature);
  else if (L_ === "wind") html = head("Wind at 10 m", kind) + bar(SCALES.wind.stops, (v) => fmtWind(v, u.wind), u.wind) + `<div class="note">Particles follow the forecast wind vectors.</div>`;
  else if (L_ === "gusts") html = head("Wind gusts (surface)", kind) + bar(SCALES.gusts.stops, (v) => fmtWind(v, u.wind), u.wind);
  else if (L_ === "precipitation") {
    const pu = u.precipitation === "in" ? "in/h" : "mm/h", d = u.precipitation === "in" ? 2 : 1;
    html = head("Precipitation rate", kind) + bar(SCALES.rain.stops, (v) => fmtRate(v, u.precipitation), pu, d) +
      `<div class="types"><span><i style="background:#2f8cf0"></i>Rain</span><span><i style="background:#bcd7ff"></i>Snow</span><span><i style="background:#ff7fb4"></i>Mixed / freezing</span></div><div class="note">Model forecast, not radar. Below 0.1 mm/h is left clear.</div>`;
  } else if (L_ === "clouds") html = head("Total cloud cover", kind) + `<div class="bar" style="background:linear-gradient(90deg,rgba(235,241,250,0),rgba(235,241,250,.75))"></div><div class="ticks"><span>0</span><span>50</span><span>100</span></div><div class="unit">%</div>`;
  else if (L_ === "pressure") html = head("Mean sea-level pressure", kind) + bar(SCALES.pressure.stops, (v) => v, "hPa") + `<div class="note">Isobars every 4 hPa (8 hPa when zoomed out), traced on the 1° model grid.</div>`;
  else if (L_ === "humidity") html = head("Relative humidity at 2 m", kind) + bar(SCALES.humidity.stops, (v) => v, "%");
  else if (L_ === "cape") html = head("CAPE (surface-based)", kind) + bar(SCALES.cape.stops.slice(1), (v) => v, "J/kg") + `<div class="note">Instability available to storms; below 100 J/kg is left clear.</div>`;
  el.innerHTML = html;
}

function readout(S) {
  const el = S.el(".czx-readout");
  if (!S.picked) { el.innerHTML = `<span class="mut">Click the map to read the forecast value at a point.</span>`; return; }
  const { lat, lon } = S.picked, u = S.units || {};
  const coord = `${Math.abs(lat).toFixed(2)}°${lat >= 0 ? "N" : "S"}, ${Math.abs(lon).toFixed(2)}°${lon >= 0 ? "E" : "W"}`;
  let val = "";
  const f = S.fields || {};
  const s = (v) => (f[v] ? sampler(f[v])(lat, lon) : NaN);
  const na = "Not available";
  if (S.layer === "temperature") { const x = s("t2m"); val = isFinite(x) ? `${fmtTemp(x, u.temperature).toFixed(1)} ${u.temperature}` : na; }
  else if (S.layer === "wind") {
    const a = s("u10"), b = s("v10");
    if (isFinite(a) && isFinite(b)) { const from = (Math.atan2(-a, -b) * 180 / Math.PI + 360) % 360; const dirs = ["N","NE","E","SE","S","SW","W","NW"]; val = `${fmtWind(Math.hypot(a, b), u.wind).toFixed(0)} ${u.wind} from ${dirs[Math.round(from / 45) % 8]} (${from.toFixed(0)}°)`; } else val = na;
  }
  else if (S.layer === "gusts") { const x = s("gust"); val = isFinite(x) ? `${fmtWind(x, u.wind).toFixed(0)} ${u.wind}` : na; }
  else if (S.layer === "precipitation") { const x = s("prate"); const t = f.ptype ? Math.round(s("ptype")) : NaN; val = isFinite(x) ? `${fmtRate(x, u.precipitation).toFixed(u.precipitation === "in" ? 2 : 1)} ${u.precipitation === "in" ? "in/h" : "mm/h"}${x >= 0.1 && t >= 1 && t <= 3 ? " · " + ["rain", "snow", "mixed/freezing"][t - 1] : ""}` : na; }
  else if (S.layer === "clouds") { const x = s("tcc"); val = isFinite(x) ? `${x.toFixed(0)}% cloud cover` : na; }
  else if (S.layer === "pressure") { const x = s("prmsl"); val = isFinite(x) ? `${x.toFixed(1)} hPa` : na; }
  else if (S.layer === "humidity") { const x = s("rh2m"); val = isFinite(x) ? `${x.toFixed(0)}% relative humidity` : na; }
  else if (S.layer === "cape") { const x = s("cape"); val = isFinite(x) ? `${x.toFixed(0)} J/kg` : na; }
  el.innerHTML = `<b>${coord}</b>${val ? ` · ${val}` : ""}<div class="mut">GFS 1° grid value (interpolated) at the selected step</div>`;
}
"""

CSS = r"""
.czx,.czx *{box-sizing:border-box}
.czx{position:relative;height:clamp(460px,calc(100vh - 220px),1000px);border-radius:22px;overflow:hidden;
  background:#040914;border:1px solid rgba(140,160,195,.18);
  box-shadow:0 2px 8px rgba(2,6,18,.4),0 24px 60px rgba(2,6,18,.45);font-family:inherit;color:#eaf0fa}
.czx::after{content:"";position:absolute;inset:0;z-index:700;pointer-events:none;border-radius:inherit;
  background:radial-gradient(ellipse 120% 90% at 50% 45%,rgba(4,9,20,0) 55%,rgba(4,9,20,.55) 100%)}
.czx-map{position:absolute;inset:0;background:radial-gradient(ellipse at 50% 38%,#0e1d42 0%,#060f26 55%,#040914 100%)}
.czx-map:focus-visible{outline:3px solid #5aa2f0;outline-offset:-3px}
.czx .leaflet-container{background:transparent;font-family:inherit}
.czx-canvas{position:absolute;left:0;top:0;pointer-events:none;transition:opacity 240ms cubic-bezier(.16,1,.3,1)}
.ico{font-family:"Material Symbols Rounded";font-weight:normal;font-style:normal;font-size:20px;line-height:1;
  display:inline-block;-webkit-font-smoothing:antialiased;font-feature-settings:"liga";vertical-align:middle}
.sr{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0)}
.mut{opacity:.72;font-weight:400}
.czx-top{position:absolute;top:10px;left:10px;right:56px;z-index:800;display:flex;pointer-events:none}
.czx-layers{display:flex;gap:6px;overflow-x:auto;padding:4px;border-radius:14px;pointer-events:auto;
  background:rgba(8,16,38,.82);border:1px solid rgba(120,150,200,.25);backdrop-filter:blur(6px);scrollbar-width:thin}
.czx-layer{display:flex;align-items:center;gap:6px;white-space:nowrap;border:1px solid transparent;border-radius:10px;
  padding:6px 10px;background:transparent;color:#d6e2f5;font:inherit;font-size:.85rem;cursor:pointer;
  transition:background .15s ease,border-color .15s ease}
.czx-layer:hover{background:rgba(90,162,240,.14)}
.czx-layer[aria-pressed="true"]{background:linear-gradient(180deg,rgba(74,163,255,.32),rgba(74,163,255,.14));
  border-color:rgba(74,163,255,.8);color:#fff;box-shadow:0 0 0 1px rgba(74,163,255,.35),0 0 18px rgba(74,163,255,.28),
  inset 0 -2px 0 #f2a93b}
.czx-layer .ico{font-size:18px}
.czx button:focus-visible,.czx select:focus-visible,.czx input:focus-visible{outline:3px solid #5aa2f0;outline-offset:2px}
.czx-side{position:absolute;right:10px;top:92px;z-index:800;display:flex;flex-direction:column;gap:6px}
.czx-btn{display:inline-flex;align-items:center;justify-content:center;min-width:36px;height:36px;border-radius:10px;
  border:1px solid rgba(120,150,200,.3);background:rgba(8,16,38,.85);color:#e9f1fc;cursor:pointer;font:inherit;padding:0 8px}
.czx-btn:hover{background:rgba(90,162,240,.2)}
.czx-btn:disabled{opacity:.4;cursor:not-allowed}
.czx-btn[aria-pressed="false"]{opacity:.6}
.czx-btn.play{background:#2a78d6;border-color:#5aa2f0;min-width:44px}
.czx-btn.now{font-size:.8rem;font-weight:600}
.czx .leaflet-control-zoom a{background:rgba(8,16,38,.85);color:#e9f1fc;border-color:rgba(120,150,200,.3)}
.czx .leaflet-control-zoom a:hover{background:rgba(90,162,240,.25)}
.czx .leaflet-top.leaflet-right{top:52px}
.czx-legend{position:absolute;left:10px;bottom:96px;z-index:800;width:min(280px,calc(100% - 20px));padding:10px 12px;
  border-radius:12px;background:rgba(8,16,38,.86);border:1px solid rgba(120,150,200,.25);font-size:.78rem}
.lg-h{display:flex;align-items:center;gap:6px;margin-bottom:8px;font-family:Outfit,Inter,sans-serif;font-size:.92rem}
.lg-h .kind{margin-left:auto;font-size:.7rem;padding:1px 7px;border-radius:999px;border:1px solid rgba(90,162,240,.6);color:#9ccaff}
.czx-legend .bar{height:9px;border-radius:5px;border:1px solid rgba(255,255,255,.15)}
.czx-legend .ticks{display:flex;justify-content:space-between;margin-top:3px;opacity:.85}
.czx-legend .unit{text-align:right;opacity:.7;margin-top:1px}
.czx-legend .note{opacity:.75;margin-top:4px}
.czx-legend .types{display:flex;gap:10px;flex-wrap:wrap;margin-top:5px}
.czx-legend .types i{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:4px;vertical-align:-1px}
.czx-readout{position:absolute;right:10px;bottom:96px;z-index:800;max-width:min(320px,calc(100% - 20px));padding:8px 12px;
  border-radius:12px;background:rgba(8,16,38,.86);border:1px solid rgba(120,150,200,.25);font-size:.8rem}
.czx-time{position:absolute;left:10px;right:10px;bottom:22px;z-index:800;padding:8px 10px;border-radius:14px;
  background:rgba(8,16,38,.9);border:1px solid rgba(120,150,200,.25)}
.czx-tl-row{display:flex;align-items:center;gap:6px}
.czx-slider{flex:1;min-width:60px;accent-color:#f2a93b;height:24px;cursor:pointer}
.czx-speed{background:rgba(8,16,38,.9);color:#e9f1fc;border:1px solid rgba(120,150,200,.3);border-radius:8px;height:32px;font:inherit}
.czx-tl-label{font-size:.78rem;margin-top:6px;overflow-wrap:anywhere;font-variant-numeric:tabular-nums}
.czx-tl-label b{font-family:Outfit,Inter,sans-serif;font-size:1rem;font-weight:500;letter-spacing:-.01em}
.czx-status{position:absolute;left:50%;top:64px;transform:translateX(-50%);z-index:900;max-width:calc(100% - 40px);
  padding:8px 14px;border-radius:999px;background:rgba(8,16,38,.92);border:1px solid rgba(90,162,240,.5);
  font-size:.82rem;display:none;text-align:center}
.czx-status.show{display:block}
.czx-status.error{border-color:#d1495b}
.czx-status.warn{border-color:#d98a00}
.czx.busy .czx-tl-label::after{content:" · loading…";opacity:.7}
.czx-attr{position:absolute;right:8px;bottom:3px;z-index:800;font-size:.66rem;opacity:.75}
.czx-attr a{color:#9ccaff}
.czx-city span{font-size:11px;color:rgba(230,238,250,.85);text-shadow:0 0 3px #04091a,0 0 6px #04091a;white-space:nowrap;
  pointer-events:none;margin-left:4px}
.czx-city::before{content:"";position:absolute;left:-2px;top:5px;width:4px;height:4px;border-radius:50%;background:rgba(230,238,250,.8)}
.czx-tip{background:#0b1630;color:#e9f1fc;border:1px solid #f5b800}
@media (max-width:640px){
  .czx{height:clamp(420px,calc(100vh - 170px),760px);border-radius:14px}
  .czx-layer .t{display:none}
  .czx-layer{padding:7px}
  .czx-legend{bottom:124px;width:calc(100% - 20px)}
  .czx-legend .note{display:none}
  .czx-readout{display:none}
  .czx-side{top:96px}
  .czx-speed-l{display:none}
  .czx-time{bottom:18px}
}
@media (prefers-reduced-motion:reduce){.czx-layer,.czx-canvas{transition:none}}
"""

_component = None


def explorer_map(data: dict, key: str = "cloudzy_explorer"):
    """Mount the map. Returns the component result; `.picked` holds {lat, lon} after a map click."""
    global _component
    import streamlit as st
    if _component is None:
        _component = st.components.v2.component("cloudzy_explorer", js=JS, css=CSS)
    return _component(key=key, data=data, on_picked_change=lambda: None)
