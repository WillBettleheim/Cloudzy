"""The Weather Explorer's 2D map: a Streamlit custom component (components v2) built on Leaflet.

This is the flat map only; the 3D globe is a separate component (src.globe_view) and shares nothing here but
the data contract (the GFS frames and manifest from src.explorer_data, and the same colour scales).

Layout (three bands, so nothing covers anything else)
- Above the map: the layer toolbar (wrapping on narrow screens) and display options.
- The map: only the legend (bottom left, collapsible), the view buttons (top right: zoom, reset, the Weather
  page's place, fullscreen), the status line and the attribution. Values at a clicked point open in a popup
  anchored at that point, never in a fixed box.
- Below the map: the forecast timeline (play/pause, previous/next, a slider with day and step ticks, "Now",
  speed) and its label: the rendered step's time in your time zone and in UTC, hours from the model run, source.

Rendering
- Basemap: Natural Earth coastlines, land borders (India's point of view, as on the globe) and places with
  collision-checked labels by zoom (public domain), drawn with Leaflet's canvas renderer. No tile service, no key.
- Raster layers: every 2 × 2 screen block is mapped to latitude/longitude and the 1° grid is sampled with
  bilinear interpolation, then coloured with the layer's scale; "Grid cells" shows the nearest grid value instead,
  so the real 1° resolution is visible.
- Wind: particles advected by the frame's own u/v components (static streamlines under reduced motion).
- Pressure: isobars every 4 hPa (8 when zoomed out) and H/L at local extremes of the sea-level pressure grid.
- The time label always describes the step that is drawn: while a new step loads, the old image is dimmed and the
  label says it is loading; a step without the layer's data is cleared and reported, never left showing.
- The map is resized whenever its container changes (ResizeObserver), including fullscreen.
"""

from __future__ import annotations

JS = r"""
const LEAFLET = "app/static/vendor/leaflet/leaflet-src.esm.js";
const LEAFLET_CSS = "app/static/vendor/leaflet/leaflet.css";
const BASEMAP = "app/static/vendor/naturalearth/basemap.json";
const BORDERS = "app/static/vendor/geo/borders.json";      // land borders in India's point of view (tools/build_geo.py)
const PLACES = "app/static/vendor/geo/places.json";        // Natural Earth populated places (tools/build_geo.py)
const CACHE_MAX = 64;
const WORLD = [[20, 10], 2];

// Same stops as the globe (src.globe_view), so a colour means the same value in both views.
const SCALES = {
  temperature: { v: "t2m", stops: [[-40,"#2a1b5f"],[-25,"#3c3fae"],[-10,"#3f7fd6"],[0,"#62b6e0"],[8,"#8fd3c0"],[16,"#e8e08c"],[24,"#f5a54a"],[32,"#e5582f"],[42,"#a31d3a"]], alpha: 0.8 },
  wind: { v: "speed", stops: [[0,"#2c4a80"],[3,"#3480c4"],[6,"#36b0c9"],[10,"#5cc79a"],[14,"#e6d36a"],[20,"#f08c46"],[28,"#d6455a"],[40,"#9b2f7f"]], alpha: 0.5 },
  gusts: { v: "gust", stops: [[0,"#2c4a80"],[5,"#3480c4"],[10,"#36b0c9"],[15,"#5cc79a"],[20,"#e6d36a"],[28,"#f08c46"],[36,"#d6455a"],[50,"#9b2f7f"]], alpha: 0.7 },
  clouds: { v: "tcc", stops: [[0,"#ffffff"],[100,"#ffffff"]], alpha: 0.0, cloud: true },
  humidity: { v: "rh2m", stops: [[0,"#8a5a3c"],[25,"#c79a5b"],[50,"#d9d6a0"],[75,"#6cc0a5"],[100,"#2a7fb8"]], alpha: 0.65 },
  cape: { v: "cape", stops: [[0,"#000000"],[100,"#fff3a8"],[500,"#ffd36b"],[1000,"#ff9d4d"],[2000,"#f0544f"],[3500,"#b8336a"],[5000,"#6c2a8f"]], alpha: 0.7, floor: 100 },
  pressure: { v: "prmsl", stops: [[960,"#4b2c8c"],[985,"#3b6fb6"],[1000,"#5fa6c9"],[1013,"#93b7c2"],[1025,"#d9b77b"],[1040,"#d9822b"]], alpha: 0.4 },
  snowdepth: { v: "snod", stops: [[0.5,"#dbe9ff"],[5,"#b5ceff"],[20,"#8da7f0"],[50,"#7a6fd6"],[100,"#6a3fb5"],[200,"#4b1f80"]], alpha: 0.85, floor: 0.5 },
  waves: { v: "swh", stops: [[0,"#1d3b6e"],[1,"#2b6cb0"],[2,"#2fa2c4"],[3,"#48c19a"],[4,"#d9d06a"],[6,"#f0954a"],[9,"#d6455a"],[14,"#8f2a7a"]], alpha: 0.8 },
  rain: { stops: [[0.1,"#5ac8fa"],[1,"#2f8cf0"],[3,"#3b57d6"],[8,"#7b3fd1"],[20,"#c42f8c"],[50,"#f04b4b"]] },
  snow: { stops: [[0.1,"#dff1ff"],[1,"#bcd7ff"],[3,"#9fb2ff"],[8,"#b28cff"]] },
  mixed: { stops: [[0.1,"#ffb0d0"],[1,"#ff7fb4"],[5,"#e84d8a"]] },
};
const LAYER_INFO = {
  none: { label: "Basemap", icon: "map" },
  wind: { label: "Wind", icon: "air" },
  temperature: { label: "Temperature", icon: "thermostat" },
  precipitation: { label: "Precipitation", icon: "rainy" },
  clouds: { label: "Cloud cover", icon: "cloud" },
  pressure: { label: "Pressure", icon: "compress" },
  gusts: { label: "Wind gusts", icon: "storm" },
  humidity: { label: "Humidity", icon: "humidity_percentage" },
  cape: { label: "CAPE", icon: "bolt" },
  snowdepth: { label: "Snow depth", icon: "ac_unit" },
  waves: { label: "Wave height", icon: "waves" },
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
const esc = (t) => String(t).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

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
    <div class="czx" role="region" aria-label="Weather map">
      <div class="czx-bar">
        <div class="czx-layers" role="toolbar" aria-label="Weather layers"></div>
        <div class="czx-opts" role="group" aria-label="Display options">
          <button type="button" class="czx-chip" data-act="motion" aria-pressed="true" title="Animate wind particles (wind layer)"><span class="ico" aria-hidden="true">animation</span><span class="t">Motion</span></button>
          <button type="button" class="czx-chip" data-act="grid" aria-pressed="false" title="Show the 1° grid cells without smoothing"><span class="ico" aria-hidden="true">grid_on</span><span class="t">Grid cells</span></button>
        </div>
      </div>
      <div class="czx-stage">
        <div class="czx-map" tabindex="0" aria-label="Map. Arrow keys pan, plus and minus zoom. Click or press Enter to select a point."></div>
        <div class="czx-ctrl" role="toolbar" aria-label="Map view">
          <button type="button" class="czx-btn" data-act="zoomin" aria-label="Zoom in" title="Zoom in"><span class="ico">add</span></button>
          <button type="button" class="czx-btn" data-act="zoomout" aria-label="Zoom out" title="Zoom out"><span class="ico">remove</span></button>
          <span class="sep" aria-hidden="true"></span>
          <button type="button" class="czx-btn" data-act="world" aria-label="Reset to the world view" title="Reset to the world view"><span class="ico">public</span></button>
          <button type="button" class="czx-btn" data-act="city" aria-label="Go to the place from your Weather page" title="Go to the place from your Weather page"><span class="ico">my_location</span></button>
          <button type="button" class="czx-btn" data-act="fs" aria-pressed="false" aria-label="Fullscreen" title="Fullscreen"><span class="ico">fullscreen</span></button>
        </div>
        <div class="czx-legend" aria-live="polite"></div>
        <div class="czx-status show" role="status">Loading map…</div>
        <div class="czx-attr"></div>
      </div>
      <div class="czx-time" role="group" aria-label="Forecast time">
        <div class="czx-tl-row">
          <button type="button" class="czx-btn" data-act="prev" aria-label="Previous forecast step" title="Previous step"><span class="ico">skip_previous</span></button>
          <button type="button" class="czx-btn play" data-act="play" aria-label="Play the forecast" title="Play"><span class="ico">play_arrow</span></button>
          <button type="button" class="czx-btn" data-act="next" aria-label="Next forecast step" title="Next step"><span class="ico">skip_next</span></button>
          <div class="czx-track">
            <input class="czx-slider" type="range" min="0" max="0" step="1" value="0" aria-label="Forecast time" />
            <div class="czx-ticks" aria-hidden="true"></div>
          </div>
          <button type="button" class="czx-btn now" data-act="now" aria-label="Jump to the step nearest to now">Now</button>
          <select class="czx-speed" aria-label="Playback speed" title="Playback speed"><option value="0.5">0.5×</option><option value="1" selected>1×</option><option value="2">2×</option></select>
        </div>
        <div class="czx-tl-label" aria-live="polite"></div>
      </div>
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

  S.reduce = !!(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
  S.motion = !S.reduce; S.gridCells = false;
  S.el('[data-act="motion"]').setAttribute("aria-pressed", String(S.motion));

  const map = L.map(S.el(".czx-map"), { worldCopyJump: true, minZoom: 1, maxZoom: 9, zoomControl: false,
    preferCanvas: true, attributionControl: false, zoomSnap: 0.5, zoomDelta: 0.5, wheelPxPerZoomLevel: 90,
    zoomAnimation: !S.reduce, fadeAnimation: !S.reduce, markerZoomAnimation: !S.reduce,
    maxBounds: [[-89, -540], [89, 540]], maxBoundsViscosity: 0.8 });
  S.map = map;
  map.setView(WORLD[0], window.innerWidth < 640 ? 1 : WORLD[1]);

  map.createPane("weather"); map.getPane("weather").style.zIndex = 350; map.getPane("weather").style.pointerEvents = "none";
  map.createPane("lines"); map.getPane("lines").style.zIndex = 420; map.getPane("lines").style.pointerEvents = "none";
  map.createPane("labels"); map.getPane("labels").style.zIndex = 450; map.getPane("labels").style.pointerEvents = "none";
  S.lineRenderer = L.canvas({ pane: "lines", padding: 0.5 });

  // Basemap (Natural Earth): coastlines and land borders, drawn above the weather for context.
  try {
    const bm = await fetch(new URL(BASEMAP, document.baseURI)).then((r) => { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); });
    // Land borders follow the same point of view as the globe; the basemap's default lines are only a fallback.
    const pov = await fetch(new URL(BORDERS, document.baseURI)).then((r) => (r.ok ? r.json() : null)).catch(() => null);
    if (pov) bm.borders = pov.borders.map((f) => { const out = []; for (let i = 0; i + 1 < f.length; i += 2) out.push([f[i], f[i + 1]]); return out; });
    const toLL = (line) => line.map(([x, y]) => [y, x]);
    for (const off of [-360, 0, 360]) {
      const shift = (line) => line.map(([x, y]) => [y, x + off]);
      L.polyline(bm.coast.map(off ? shift : toLL), { renderer: S.lineRenderer, color: "#c9daf2", weight: 1, opacity: 0.85, interactive: false }).addTo(map);
      L.polyline(bm.borders.map(off ? shift : toLL), { renderer: S.lineRenderer, color: "#a9bbd8", weight: 0.8, opacity: 0.7, dashArray: "4 3", interactive: false }).addTo(map);
    }
    S.cities = bm.cities.map(([name, lat, lon, rank]) => [name, lat, lon, rank * 2, 0]);   // fallback until places load
  } catch (e) {
    status("The basemap couldn't load (" + e.message + "). Weather layers still work.", "warn");
  }
  S.cityLayer = L.layerGroup().addTo(map);
  fetch(new URL(PLACES, document.baseURI)).then((r) => (r.ok ? r.json() : null)).then((p) => {
    if (!p || !S.root.isConnected) return;
    S.cities = p.places.map((c) => [c[0], c[1], c[2], c[3], c[7]]);       // name, lat, lon, scale rank, capital
    drawCities(S);
  }).catch(() => {});

  // Weather canvas overlay (raster + isobars), and the particle canvas above it.
  const pane = map.getPane("weather");
  S.canvas = document.createElement("canvas"); S.canvas.className = "czx-canvas"; pane.appendChild(S.canvas);
  S.pcanvas = document.createElement("canvas"); S.pcanvas.className = "czx-canvas"; pane.appendChild(S.pcanvas);
  map.on("movestart zoomstart", () => { S.moving = true; clearParticles(S); });
  map.on("moveend zoomend", () => { S.moving = false; drawCities(S); redraw(S); });
  map.on("click", (ev) => pickAt(S, ev.latlng.wrap(), setTriggerValue));
  S.el(".czx-map").addEventListener("keydown", (e) => {     // Enter selects the point at the centre of the view
    if (e.key === "Enter" && e.target === S.el(".czx-map")) { e.preventDefault(); pickAt(S, map.getCenter().wrap(), setTriggerValue); }
  });
  // Leaflet only notices window resizes; the container also changes size (layout, fullscreen, orientation).
  let rt = null;
  S.ro = new ResizeObserver(() => { clearTimeout(rt); rt = setTimeout(() => { map.invalidateSize({ pan: false }); drawCities(S); ticks(S); redraw(S); }, 80); });
  S.ro.observe(S.el(".czx-stage")); S.ro.observe(S.el(".czx-time"));
  S.onFs = () => { S.fs = !!document.fullscreenElement; fsButton(S); };
  document.addEventListener("fullscreenchange", S.onFs);

  // Controls.
  const layersEl = S.el(".czx-layers");
  for (const [key, info] of Object.entries(LAYER_INFO)) {
    const b = document.createElement("button");
    b.type = "button"; b.className = "czx-layer"; b.dataset.layer = key;
    b.setAttribute("aria-pressed", "false"); b.setAttribute("aria-label", info.label); b.title = info.label;
    b.innerHTML = `<span class="ico" aria-hidden="true">${info.icon}</span><span class="t">${info.label}</span>`;
    b.addEventListener("click", () => setLayer(S, key));
    layersEl.appendChild(b);
  }
  root.addEventListener("click", (ev) => {
    const b = ev.target.closest && ev.target.closest("[data-act]");
    if (!b) return;
    const act = b.dataset.act;
    if (act === "zoomin") map.zoomIn(0.5); if (act === "zoomout") map.zoomOut(0.5);
    if (act === "world") map.setView(WORLD[0], window.innerWidth < 640 ? 1 : WORLD[1], { animate: !S.reduce });
    if (act === "city" && S.data.city) map.setView([S.data.city.lat, S.data.city.lon], Math.max(map.getZoom(), 5), { animate: !S.reduce });
    if (act === "fs") toggleFs(S);
    if (act === "prev") step(S, -1);
    if (act === "next") step(S, 1);
    if (act === "play") togglePlay(S);
    if (act === "now") goNow(S);
    if (act === "motion") { S.motion = !S.motion; b.setAttribute("aria-pressed", String(S.motion)); legend(S); redraw(S); }
    if (act === "grid") { S.gridCells = !S.gridCells; b.setAttribute("aria-pressed", String(S.gridCells)); redraw(S); }
    if (act === "legend") { S.legendOpen = !S.legendOpen; legend(S); }
  });
  S.el(".czx").addEventListener("keydown", (e) => { if (e.key === "Escape" && S.expanded) toggleFs(S); });
  S.el(".czx-slider").addEventListener("input", (ev) => { stopPlay(S); setFrame(S, +ev.target.value); });
  S.el(".czx-speed").addEventListener("change", (ev) => { S.speed = +ev.target.value; });
  S.speed = 1; S.legendOpen = window.innerWidth >= 640;
  S.onVis = () => { if (document.hidden) stopPlay(S); };
  document.addEventListener("visibilitychange", S.onVis);

  S.cache = new Map(); S.layer = S.data.layer || "wind"; S.frameIdx = 0; S.shownIdx = -1; S.ready = true;
  drawCities(S);
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
  if (S.ro) S.ro.disconnect();
  if (S.onFs) document.removeEventListener("fullscreenchange", S.onFs);
  if (S.onVis) document.removeEventListener("visibilitychange", S.onVis);
  try { S.map.remove(); } catch (e) {}
  S.cache.clear(); S.ready = false;
}

// --- Fullscreen (the browser's, or the map filling the window where element fullscreen isn't allowed) ---
function toggleFs(S) {
  const box = S.el(".czx");
  if (S.fs) { document.exitFullscreen && document.exitFullscreen(); return; }
  if (S.expanded) { S.expanded = false; box.classList.remove("expanded"); fsButton(S); return; }
  const fallback = () => { S.expanded = true; box.classList.add("expanded"); fsButton(S); };
  if (box.requestFullscreen) box.requestFullscreen().catch(fallback); else fallback();
}
function fsButton(S) {
  const on = S.fs || S.expanded, b = S.el('[data-act="fs"]');
  b.setAttribute("aria-pressed", String(!!on)); b.title = on ? "Exit fullscreen" : "Fullscreen";
  b.setAttribute("aria-label", b.title); b.querySelector(".ico").textContent = on ? "fullscreen_exit" : "fullscreen";
}

// --- Place labels: Natural Earth places by zoom, kept apart so they never overlap ---
function drawCities(S) {
  if (!S.cityLayer || !S.cities || !S.map) return;
  const L = S.L, map = S.map, z = map.getZoom(), size = map.getSize(), b = map.getBounds().pad(0.05);
  S.cityLayer.clearLayers();
  const maxRank = z < 2 ? 1 : z < 3 ? 2 : z < 4 ? 4 : z < 5 ? 6 : z < 6 ? 8 : 10;
  const placed = [];
  let n = 0;
  for (const [name, lat, lon, rank, cap] of S.cities) {
    if (rank > maxRank || n >= 120) continue;
    for (const off of [0, -360, 360]) {
      if (!b.contains([lat, lon + off])) continue;
      const p = map.latLngToContainerPoint([lat, lon + off]);
      const box = [p.x - 3, p.y - 8, p.x + 8 + name.length * 6.2, p.y + 8];
      if (box[2] > size.x || box[3] > size.y || box[0] < 0 || box[1] < 0) continue;
      if (placed.some((o) => box[0] < o[2] && box[2] > o[0] && box[1] < o[3] && box[3] > o[1])) continue;
      placed.push(box); n++;
      L.marker([lat, lon + off], { pane: "labels", interactive: false, keyboard: false,
        icon: L.divIcon({ className: "czx-city" + (cap === 2 ? " cap" : ""), html: `<span>${esc(name)}</span>`, iconSize: null }) }).addTo(S.cityLayer);
    }
  }
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
    S.el(".czx-attr").innerHTML = `${esc(m.attribution)} · ${esc(m.grid.resolution)} grid · <span class="run">run ${esc(m.run_utc.replace("T", " "))} ·</span> Natural Earth · <a href="https://leafletjs.com" target="_blank" rel="noopener">Leaflet</a>`;
    ticks(S);
    if (changed) { S.frameIdx = Math.min(S.frameIdx, S.frames.length - 1); S.el(".czx-slider").value = String(S.frameIdx); redraw(S); }
  } catch (e) {
    S.status("Forecast data couldn't be loaded (" + e.message + "). Retrying shortly.", "error");
  }
}

function applyData(S) {
  const L = S.L, d = S.data;
  S.units = d.units || { temperature: "°C", wind: "km/h", precipitation: "mm" };
  if (S.cityMarker) { S.cityMarker.remove(); S.cityMarker = null; }
  if (d.city) {
    S.cityMarker = L.marker([d.city.lat, d.city.lon], { pane: "labels", interactive: false, keyboard: false,
      icon: L.divIcon({ className: "czx-home", html: "<i></i>", iconSize: [14, 14], iconAnchor: [7, 7] }) })
      .bindTooltip(esc(d.city.label), { direction: "top", className: "czx-tip", offset: [0, -6] }).addTo(S.map);
    if (!S.centeredOnce) { S.centeredOnce = true; S.map.setView([d.city.lat, d.city.lon], Math.max(S.map.getZoom(), 3)); }
  }
  S.el('[data-act="city"]').disabled = !d.city;
  // A place or region chosen with the page's search or browser: select it and move the map there (once per choice).
  if (d.focus && d.focus.token !== S.focusToken) {
    S.focusToken = d.focus.token;
    if (isFinite(+d.focus.lat) && isFinite(+d.focus.lon)) {
      const region = ["country", "admin1", "continent"].includes(d.focus.kind);
      const z = region ? (d.focus.kind === "continent" ? 3 : d.focus.kind === "country" ? 4 : 6) : Math.max(S.map.getZoom(), 6);
      if (!region) { S.picked = { lat: +d.focus.lat, lon: +d.focus.lon }; drawPicked(S); }
      if (S.reduce) S.map.setView([+d.focus.lat, +d.focus.lon], z); else S.map.flyTo([+d.focus.lat, +d.focus.lon], z, { duration: 1.2 });
    }
  }
  legend(S); readout(S); redraw(S);
}

// --- Point inspection: a popup anchored at the point (never a fixed box that can collide with the legend) ---
function pickAt(S, ll, setTriggerValue) {
  S.picked = { lat: +ll.lat.toFixed(3), lon: +ll.lng.toFixed(3) };
  drawPicked(S); openPopup(S);
  setTriggerValue("picked", { lat: S.picked.lat, lon: S.picked.lon, t: Date.now() });
}
function drawPicked(S) {
  const L = S.L;
  if (S.pickMarker) S.pickMarker.remove();
  if (!S.picked) return;
  S.pickMarker = L.marker([S.picked.lat, S.picked.lon], { pane: "labels", interactive: false, keyboard: false,
    icon: L.divIcon({ className: "czx-pick", html: "<i></i>", iconSize: [18, 18], iconAnchor: [9, 9] }) }).addTo(S.map);
}
function openPopup(S) {
  if (!S.picked) return;
  if (!S.popup) {
    S.popup = S.L.popup({ className: "czx-pop", closeButton: true, autoPan: true, autoPanPadding: [24, 24], offset: [0, -8], maxWidth: 280 });
    S.popup.on("remove", () => { S.popupOpen = false; });
  }
  S.popup.setLatLng([S.picked.lat, S.picked.lon]).setContent(readoutHtml(S)).openOn(S.map);
  S.popupOpen = true;
}

// --- Frames ---
function framePath(S, fhr, v) { return `${S.manifest.base_url}/f${String(fhr).padStart(3, "0")}_${v}.bin`; }
function varsFor(layer) {
  return { none: [], wind: ["u10", "v10"], temperature: ["t2m"], precipitation: ["prate", "ptype"], clouds: ["tcc"],
           pressure: ["prmsl"], gusts: ["gust"], humidity: ["rh2m"], cape: ["cape"], snowdepth: ["snod"],
           waves: ["swh"] }[layer] || [];
}
const OPTIONAL = new Set(["ptype"]);
// A step has the layer when none of its required fields is listed as missing (and the run knows the field).
function stepHas(S, frame, layer) {
  const enc = (S.manifest && S.manifest.encoding) || {};
  return varsFor(layer).filter((v) => !OPTIONAL.has(v)).every((v) => enc[v] && !(frame.missing || []).includes(v));
}
async function getField(S, fhr, v, signal) {
  const key = `${S.manifest.run_id}/${fhr}/${v}`;
  if (S.cache.has(key)) { const f = S.cache.get(key); S.cache.delete(key); S.cache.set(key, f); return f; }
  if (!S.manifest.encoding[v]) return null;
  const r = await fetch(new URL(framePath(S, fhr, v), document.baseURI), { signal });
  if (r.status === 404) return null;                       // field not in this frame: shown as unavailable
  if (!r.ok) throw new Error("HTTP " + r.status);
  const q = new Uint16Array(await r.arrayBuffer());
  if (q.length !== 181 * 360) throw new Error("unexpected data for " + v);
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
  ticks(S, true);
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
  S.playing = true; playButton(S);
  const tick = async () => {
    if (!S.playing) return;
    await setFrame(S, (S.frameIdx + 1) % S.frames.length);   // waits for the data: the label never runs ahead
    if (S.playing) S.timer = setTimeout(tick, 900 / S.speed);
  };
  S.timer = setTimeout(tick, 300);
}
function stopPlay(S) { S.playing = false; clearTimeout(S.timer); if (S.el) playButton(S); }
function playButton(S) {
  const b = S.el('[data-act="play"]'); if (!b) return;
  b.innerHTML = `<span class="ico">${S.playing ? "pause" : "play_arrow"}</span>`;
  b.setAttribute("aria-label", S.playing ? "Pause the forecast" : "Play the forecast"); b.title = S.playing ? "Pause" : "Play";
}

// Step and day ticks under the slider; steps where the current layer has no data are marked.
function ticks(S, onlyCurrent) {
  const box = S.el(".czx-ticks"); if (!box || !S.frames) return;
  const n = S.frames.length;
  if (!onlyCurrent || box.childElementCount !== n + (box.dataset.days | 0)) {
    let html = "", days = 0, lastDay = "", lastPos = -1e9;
    const width = box.clientWidth || 300;
    S.frames.forEach((f, i) => {
      const pos = n > 1 ? (i / (n - 1)) * 100 : 0, t = new Date(f.valid_utc);
      const day = t.toLocaleDateString(undefined, { weekday: "short", day: "numeric" });
      const miss = S.layer !== "none" && !stepHas(S, f, S.layer);
      html += `<i class="tk${miss ? " miss" : ""}" data-i="${i}" style="left:${pos}%" title="${miss ? "No data for this layer" : ""}"></i>`;
      if (day !== lastDay) {                        // a day label, unless it would crowd the previous one
        if ((pos - lastPos) / 100 * width >= 52) { html += `<span class="dl" style="left:${pos}%">${esc(day)}</span>`; days++; lastPos = pos; }
        lastDay = day;
      }
    });
    box.innerHTML = html; box.dataset.days = String(days);
  }
  for (const t of box.querySelectorAll(".tk")) t.classList.toggle("on", +t.dataset.i === S.frameIdx);
}

// The label describes the step that is drawn (S.shownIdx). While another step loads, it says so.
function timeLabel(S) {
  const el = S.el(".czx-tl-label"); if (!S.frames) return;
  const f = S.frames[S.shownIdx];
  const tz = Intl.DateTimeFormat().resolvedOptions().timeZone || "local time";
  const fmt = (x) => new Date(x.valid_utc).toLocaleString(undefined, { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
  const want = S.frames[S.frameIdx];
  if (!f || (S.loading && want && S.shownIdx !== S.frameIdx)) {
    el.innerHTML = want ? `<b>${fmt(want)}</b> <span class="mut">loading this step…</span>` : "";
    return;
  }
  el.innerHTML = `<b>${fmt(f)}</b> <span class="mut">${esc(tz)}</span> <span class="sep">·</span> <span class="utc">${f.valid_utc.slice(5, 16).replace("T", " ")} UTC</span> ` +
    `<span class="sep">·</span> <span class="mut">+${f.fhr} h from the ${S.manifest.run_utc.slice(11, 16)} UTC GFS run · ${f.fhr === 0 ? "model analysis" : "model forecast"} · step ${S.shownIdx + 1} of ${S.frames.length}</span>`;
  S.el(".czx-slider").setAttribute("aria-valuetext", `${fmt(f)} ${tz}, plus ${f.fhr} hours`);
}

function setLayer(S, key, silent) {
  S.layer = key;
  for (const b of S.root.querySelectorAll(".czx-layer")) b.setAttribute("aria-pressed", String(b.dataset.layer === key));
  S.el('[data-act="motion"]').hidden = key !== "wind";
  S.el('[data-act="grid"]').hidden = key === "none";
  legend(S); ticks(S);
  if (S.frames) setFrame(S, S.frameIdx);
}

// --- Rendering ---
function sampler(grid, nearest) {
  if (nearest) return (lat, lon) => {          // the value of the grid cell the point is in (no smoothing)
    const y = Math.min(180, Math.max(0, Math.round(90 - lat))), x = ((Math.round(lon) % 360) + 360) % 360;
    return grid[y * 360 + x];
  };
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
  const map = S.map, size = map.getSize(), idx = S.frameIdx, L_ = S.layer;
  cancelAnimationFrame(S.raf);
  if (L_ === "none") {
    for (const c of [S.canvas, S.pcanvas]) c.getContext("2d").clearRect(0, 0, c.width, c.height);
    S.status(""); S.drawnLayer = "none"; S.shownIdx = idx; S.fields = {}; timeLabel(S); readout(S); return;
  }
  let fields;
  try {
    S.loading = true; S.el(".czx").classList.add("busy"); timeLabel(S);
    fields = await loadFrame(S, idx);
  } catch (e) {
    if (e.name === "AbortError") return;
    S.loading = false; S.el(".czx").classList.remove("busy");
    clearWeather(S, idx); S.status("This forecast step couldn't be loaded (" + e.message + ").", "error"); return;
  }
  if (token !== S.drawToken) return;                       // a newer redraw started: drop this one
  S.loading = false; S.el(".czx").classList.remove("busy");
  const missing = Object.entries(fields).filter(([v, f]) => !f && !OPTIONAL.has(v)).map(([v]) => v);
  if (missing.length) {                                    // never leave another step's picture on screen
    clearWeather(S, idx);
    S.status(`${LAYER_INFO[L_].label} isn't available for this step in the GFS output. Try another step or layer.`, "warn");
    return;
  }
  S.status(S.manifest.status === "complete" ? "" : `Preparing forecast steps: ${S.frames.length} of ${S.manifest.frames.length} ready…`, S.manifest.status === "complete" ? "" : "info");
  S.fields = fields;
  for (const c of [S.canvas, S.pcanvas]) {
    c.width = size.x; c.height = size.y; c.style.width = size.x + "px"; c.style.height = size.y + "px";
    S.L.DomUtil.setPosition(c, map.containerPointToLayerPoint([0, 0]));
  }
  const ctx = S.canvas.getContext("2d");
  clearParticles(S);
  // Pixel → lat/lon lookup: longitude depends only on x and latitude only on y (Web Mercator).
  const STEP = size.x * size.y > 1600000 ? 3 : 2;
  const lats = new Float64Array(size.y), lons = new Float64Array(size.x);
  for (let y = 0; y < size.y; y++) lats[y] = map.containerPointToLatLng([0, y]).lat;
  for (let x = 0; x < size.x; x++) lons[x] = map.containerPointToLatLng([x, 0]).lng;
  S.lats = lats; S.lons = lons;
  const img = ctx.createImageData(size.x, size.y), px = img.data, near = S.gridCells;
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
  if (L_ === "precipitation") {
    const rate = sampler(fields.prate, near), type = fields.ptype ? sampler(fields.ptype, true) : null;
    const ramps = { 1: makeRamp(SCALES.rain.stops), 2: makeRamp(SCALES.snow.stops), 3: makeRamp(SCALES.mixed.stops) };
    paint((la, lo) => {
      const r = rate(la, lo); if (!(r >= 0.1)) return null;          // below 0.1 mm/h is left clear
      let t = type ? Math.round(type(la, lo)) : 1; if (!(t >= 1 && t <= 3)) t = 1;
      const c = ramps[t](r); return [c[0], c[1], c[2], Math.min(230, 150 + r * 16)];
    });
  } else if (L_ === "clouds") {
    const s = sampler(fields.tcc, near);
    paint((la, lo) => { const v = s(la, lo); if (!(v > 5)) return null; return [226, 233, 244, Math.round(v / 100 * 200)]; });
  } else if (L_ === "wind") {
    const u = sampler(fields.u10, near), v = sampler(fields.v10, near), ramp = makeRamp(SCALES.wind.stops);
    paint((la, lo) => { const a = u(la, lo), b = v(la, lo); if (!isFinite(a) || !isFinite(b)) return null; const c = ramp(Math.hypot(a, b)); return [c[0], c[1], c[2], 255 * SCALES.wind.alpha]; });
  } else {
    const sc = SCALES[L_], s = sampler(fields[sc.v], near), ramp = makeRamp(sc.stops);
    paint((la, lo) => { const v = s(la, lo); if (!isFinite(v)) return null; if (sc.floor && v < sc.floor) return null; const c = ramp(v); return [c[0], c[1], c[2], 255 * sc.alpha]; });
  }
  ctx.putImageData(img, 0, 0);
  if (L_ === "pressure") { isobars(S, ctx, fields.prmsl); highsLows(S, ctx, fields.prmsl); }
  S.drawnLayer = L_; S.shownIdx = idx;
  timeLabel(S); readout(S); ticks(S, true);
  if (L_ === "wind") startParticles(S);
}
function clearWeather(S, idx) {
  for (const c of [S.canvas, S.pcanvas]) c.getContext("2d").clearRect(0, 0, c.width, c.height);
  cancelAnimationFrame(S.raf);
  S.fields = {}; S.shownIdx = idx; timeLabel(S); readout(S);
}

// Isobars every 4 hPa: marching squares on the 1° grid, drawn through the map projection.
function isobars(S, ctx, g) {
  const map = S.map, bounds = map.getBounds();
  const latMin = Math.max(-90, Math.floor(bounds.getSouth()) - 1), latMax = Math.min(90, Math.ceil(bounds.getNorth()) + 1);
  const lonMin = Math.floor(bounds.getWest()) - 1, lonMax = Math.ceil(bounds.getEast()) + 1;
  const at = (la, lo) => g[(90 - la) * 360 + (((lo % 360) + 360) % 360)];
  const proj = (la, lo) => map.latLngToContainerPoint([la, lo]);
  ctx.save(); ctx.strokeStyle = "rgba(240,246,255,0.8)"; ctx.lineWidth = 1.1;
  ctx.font = "600 11px Inter, system-ui, sans-serif"; ctx.fillStyle = "rgba(240,246,255,0.95)";
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
            ctx.save(); ctx.fillStyle = "rgba(6,13,32,.8)"; const w = ctx.measureText(String(level)).width;
            ctx.fillRect(p.x - 3, p.y - 12, w + 6, 15); ctx.restore(); ctx.fillText(String(level), p.x, p.y - 1);
          }
        }
      }
    }
    ctx.stroke();
  }
  ctx.restore();
}
// H and L at pressure centres: the highest (lowest) sea-level pressure within 10 grid cells, at least 4 hPa above
// (below) that neighbourhood's average, so small bumps over mountains (where sea-level pressure is extrapolated)
// don't count. Found on the 1° grid; at most 30, kept at least 90 px apart (strongest first).
function highsLows(S, ctx, g) {
  const map = S.map, b = map.getBounds(), R = 10;
  const latMin = Math.max(-84, Math.floor(b.getSouth())), latMax = Math.min(84, Math.ceil(b.getNorth()));
  const lonMin = Math.floor(b.getWest()), lonMax = Math.ceil(b.getEast());
  if ((latMax - latMin) * (lonMax - lonMin) > 70000) return;
  const at = (la, lo) => g[(90 - la) * 360 + (((lo % 360) + 360) % 360)];
  const marks = [];
  for (let la = latMax - 1; la > latMin; la--) for (let lo = lonMin + 1; lo < lonMax; lo++) {
    const v = at(la, lo); if (!isFinite(v)) continue;
    let hi = true, lo_ = true, sum = 0, n = 0;
    for (let dy = -R; dy <= R && (hi || lo_); dy++) for (let dx = -R; dx <= R; dx++) {
      if (!dx && !dy) continue;
      const la2 = la + dy; if (la2 > 90 || la2 < -90) continue;
      const w = at(la2, lo + dx); if (!isFinite(w)) continue;
      if (w >= v) hi = false; if (w <= v) lo_ = false; sum += w; n++;
    }
    if (!n) continue;
    const mean = sum / n;
    if (hi && v - mean >= 4) marks.push(["H", la, lo, v, v - mean]);
    else if (lo_ && mean - v >= 4) marks.push(["L", la, lo, v, mean - v]);
  }
  marks.sort((a, b2) => b2[4] - a[4]);
  const kept = [];
  for (const m of marks) {
    const p = map.latLngToContainerPoint([m[1], m[2]]);
    if (kept.length < 30 && kept.every((k) => Math.hypot(k.p.x - p.x, k.p.y - p.y) > 90)) kept.push({ m, p });
  }
  ctx.save(); ctx.textAlign = "center"; ctx.textBaseline = "middle";
  for (const { m: [k, , , v], p } of kept) {
    ctx.font = "700 20px Outfit, Inter, sans-serif"; ctx.lineWidth = 4; ctx.strokeStyle = "rgba(6,13,32,.85)";
    ctx.fillStyle = k === "H" ? "#7fb6ff" : "#ff8f8f";
    ctx.strokeText(k, p.x, p.y); ctx.fillText(k, p.x, p.y);
    ctx.font = "600 11px Inter, system-ui, sans-serif"; ctx.fillStyle = "rgba(240,246,255,.95)";
    ctx.strokeText(v.toFixed(0), p.x, p.y + 16); ctx.fillText(v.toFixed(0), p.x, p.y + 16);
  }
  ctx.restore();
}

// Wind particles advected by the frame's u/v components.
function clearParticles(S) { cancelAnimationFrame(S.raf); if (S.pcanvas) S.pcanvas.getContext("2d").clearRect(0, 0, S.pcanvas.width, S.pcanvas.height); }
function startParticles(S) {
  const map = S.map, size = map.getSize(), ctx = S.pcanvas.getContext("2d");
  const u = sampler(S.fields.u10), v = sampler(S.fields.v10), ramp = makeRamp(SCALES.wind.stops);
  const n = Math.round(Math.min(2600, (size.x * size.y) / 420));
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
  const tint = (sp) => { const c = ramp(sp); return `rgba(${Math.min(255, c[0] + 70) | 0},${Math.min(255, c[1] + 70) | 0},${Math.min(255, c[2] + 70) | 0},0.92)`; };
  if (!S.motion) {           // reduced motion or motion off: short static streamlines from the same vectors
    ctx.lineWidth = 1;
    for (let i = 0; i < n / 3; i++) {
      const p = spawn(); ctx.beginPath(); ctx.moveTo(p.x, p.y); let sp = 0;
      for (let k = 0; k < 14; k++) { const w = vel(p); if (!w) break; p.x += w[0] * 2; p.y += w[1] * 2; sp = w[2]; ctx.lineTo(p.x, p.y); }
      ctx.strokeStyle = tint(sp); ctx.stroke();
    }
    return;
  }
  const frame = () => {
    if (!S.root.isConnected) return teardown(S);
    if (document.hidden || S.moving) { S.raf = requestAnimationFrame(frame); return; }
    ctx.globalCompositeOperation = "destination-in"; ctx.fillStyle = "rgba(0,0,0,0.9)"; ctx.fillRect(0, 0, size.x, size.y);
    ctx.globalCompositeOperation = "source-over"; ctx.lineWidth = 1.2;
    for (const p of ps) {
      const w = vel(p);
      if (!w || p.age-- <= 0 || p.x < 0 || p.y < 0 || p.x >= size.x || p.y >= size.y) { Object.assign(p, spawn(), { age: 60 + Math.random() * 40 }); continue; }
      ctx.strokeStyle = tint(w[2]);
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
      .map(([v]) => { const x = conv(v); return `<span>${digits || x === 0 || Math.abs(x) >= 1 ? x.toFixed(digits) : x.toFixed(1)}</span>`; }).join("");
    return `<div class="bar" style="background:linear-gradient(90deg,${g})"></div><div class="ticks">${ticks}<em>${unit}</em></div>`;
  };
  const titles = { none: "Basemap only", temperature: "Temperature at 2 m", wind: "Wind at 10 m", gusts: "Wind gusts (surface)",
    precipitation: "Precipitation rate", clouds: "Total cloud cover", pressure: "Mean sea-level pressure",
    humidity: "Relative humidity at 2 m", snowdepth: "Snow depth", waves: "Significant wave height", cape: "CAPE (surface-based)" };
  let body = "";
  if (L_ === "none") body = `<div class="note">Pick a layer above to show forecast data.</div>`;
  else if (L_ === "temperature") body = bar(SCALES.temperature.stops, (v) => fmtTemp(v, u.temperature), u.temperature);
  else if (L_ === "wind") body = bar(SCALES.wind.stops, (v) => fmtWind(v, u.wind), u.wind) + `<div class="note">${S.motion ? "Particles move with" : "Streamlines follow"} the forecast wind vectors.</div>`;
  else if (L_ === "gusts") body = bar(SCALES.gusts.stops, (v) => fmtWind(v, u.wind), u.wind);
  else if (L_ === "precipitation") {
    const pu = u.precipitation === "in" ? "in/h" : "mm/h", d = u.precipitation === "in" ? 2 : 1;
    body = bar(SCALES.rain.stops, (v) => fmtRate(v, u.precipitation), pu, d) +
      `<div class="types"><span><i style="background:#2f8cf0"></i>Rain</span><span><i style="background:#bcd7ff"></i>Snow</span><span><i style="background:#ff7fb4"></i>Mixed / freezing</span></div><div class="note">Model forecast, not radar. Below 0.1 mm/h is left clear.</div>`;
  } else if (L_ === "clouds") body = `<div class="bar" style="background:linear-gradient(90deg,rgba(226,233,244,0),rgba(226,233,244,.8))"></div><div class="ticks"><span>0</span><span>50</span><span>100</span><em>%</em></div><div class="note">All cloud levels together.</div>`;
  else if (L_ === "pressure") body = bar(SCALES.pressure.stops, (v) => v, "hPa") + `<div class="note">Isobars every 4 hPa (8 zoomed out); H and L mark local extremes.</div>`;
  else if (L_ === "humidity") body = bar(SCALES.humidity.stops, (v) => v, "%");
  else if (L_ === "snowdepth") body = bar(SCALES.snowdepth.stops, (v) => v, "cm") + `<div class="note">Snow on the ground (model). Below 0.5 cm is left clear.</div>`;
  else if (L_ === "waves") body = bar(SCALES.waves.stops, (v) => v, "m") + `<div class="note">GFS-Wave, wind waves and swell. Land and sea ice are left clear.</div>`;
  else if (L_ === "cape") body = bar(SCALES.cape.stops.slice(1), (v) => v, "J/kg") + `<div class="note">Instability, not a storm forecast; below 100 J/kg is clear.</div>`;
  const kind = L_ === "none" ? "" : `<span class="kind">${L_ === "waves" ? "GFS-Wave" : "GFS"} forecast</span>`;
  el.classList.toggle("collapsed", !S.legendOpen);
  el.innerHTML = `<button type="button" class="lg-h" data-act="legend" aria-expanded="${!!S.legendOpen}" title="${S.legendOpen ? "Collapse" : "Expand"} the legend"><span class="ico" aria-hidden="true">${LAYER_INFO[L_].icon}</span><b>${titles[L_]}</b>${kind}<span class="ico chev" aria-hidden="true">${S.legendOpen ? "expand_more" : "expand_less"}</span></button><div class="lg-b">${body}</div>`;
}

// What the popup says for the selected point: the interpolated value at the point and the nearest grid point's
// own value, with the step's valid time, so a model value is never mistaken for an observation.
function readoutHtml(S) {
  const { lat, lon } = S.picked, u = S.units || {}, f = S.fields || {}, L_ = S.layer;
  const coord = `${Math.abs(lat).toFixed(2)}°${lat >= 0 ? "N" : "S"}, ${Math.abs(lon).toFixed(2)}°${lon >= 0 ? "E" : "W"}`;
  const s = (v, near) => (f[v] ? sampler(f[v], near)(lat, lon) : NaN), na = "Not available";
  const glat = Math.round(lat), glon = Math.round(lon);
  const fmt = (x, kind) => {
    if (!isFinite(x)) return na;
    if (kind === "t2m") return `${fmtTemp(x, u.temperature).toFixed(1)} ${u.temperature}`;
    if (kind === "gust") return `${fmtWind(x, u.wind).toFixed(0)} ${u.wind}`;
    if (kind === "prate") return `${fmtRate(x, u.precipitation).toFixed(u.precipitation === "in" ? 2 : 1)} ${u.precipitation === "in" ? "in/h" : "mm/h"}`;
    if (kind === "tcc" || kind === "rh2m") return `${x.toFixed(0)}%`;
    if (kind === "prmsl") return `${x.toFixed(1)} hPa`;
    if (kind === "snod") return `${x.toFixed(x < 10 ? 1 : 0)} cm`;
    if (kind === "swh") return `${x.toFixed(1)} m`;
    if (kind === "cape") return `${x.toFixed(0)} J/kg`;
    return x.toFixed(1);
  };
  let main = "", grid = "";
  const varOf = { temperature: "t2m", gusts: "gust", precipitation: "prate", clouds: "tcc", pressure: "prmsl", humidity: "rh2m", snowdepth: "snod", waves: "swh", cape: "cape" }[L_];
  if (L_ === "wind") {
    const a = s("u10", S.gridCells), b = s("v10", S.gridCells);
    if (isFinite(a) && isFinite(b)) {
      const from = (Math.atan2(-a, -b) * 180 / Math.PI + 360) % 360, dirs = ["N","NE","E","SE","S","SW","W","NW"];
      main = `${fmtWind(Math.hypot(a, b), u.wind).toFixed(0)} ${u.wind} from ${dirs[Math.round(from / 45) % 8]} (${from.toFixed(0)}°)`;
      const ga = s("u10", true), gb = s("v10", true);
      if (isFinite(ga) && isFinite(gb)) grid = `${fmtWind(Math.hypot(ga, gb), u.wind).toFixed(0)} ${u.wind}`;
    } else main = na;
  } else if (varOf) {
    const x = s(varOf, S.gridCells);
    main = L_ === "waves" && !isFinite(x) && f.swh ? "No wave data here (land or sea ice)" : fmt(x, varOf);
    if (L_ === "precipitation" && x >= 0.1 && f.ptype) { const t = Math.round(s("ptype", true)); if (t >= 1 && t <= 3) main += " · " + ["rain", "snow", "mixed/freezing"][t - 1]; }
    const gx = s(varOf, true); if (isFinite(gx)) grid = fmt(gx, varOf);
  }
  const fr = S.frames && S.frames[S.shownIdx];
  const when = fr ? `${new Date(fr.valid_utc).toLocaleString(undefined, { weekday: "short", hour: "2-digit", minute: "2-digit" })} (${fr.valid_utc.slice(11, 16)} UTC)` : "";
  if (L_ === "none") return `<div class="pp-c">${coord}</div><div class="pp-n">Choose a layer to read forecast values here. The page below shows a point forecast.</div>`;
  if (S.loading && S.shownIdx !== S.frameIdx) return `<div class="pp-c">${coord}</div><div class="pp-n">Loading this step…</div>`;
  return `<div class="pp-c">${coord}</div><div class="pp-v">${main}</div>` +
    `<div class="pp-n">${esc(LAYER_INFO[L_].label)} · ${L_ === "waves" ? "GFS-Wave" : "GFS"} model forecast for ${esc(when)}. ` +
    `${S.gridCells ? "Grid cell value (no smoothing)." : "Interpolated between the four nearest 1° grid points."}` +
    `${grid && !S.gridCells ? ` Nearest grid point (${Math.abs(glat)}°${glat >= 0 ? "N" : "S"}, ${Math.abs(glon)}°${glon >= 0 ? "E" : "W"}): ${grid}.` : ""} Not an observation.</div>`;
}
function readout(S) {
  if (S.popup && S.popupOpen && S.picked) S.popup.setContent(readoutHtml(S));
}
"""

CSS = r"""
.czx,.czx *{box-sizing:border-box}
/* Three bands: toolbar, map, timeline. Only the map band holds overlays, and only small ones. */
.czx{display:flex;flex-direction:column;gap:8px;height:clamp(600px,calc(100vh - 32px),1300px);font-family:inherit;color:#eaf0fa}
.czx:fullscreen,.czx.expanded{padding:10px;background:#040914;height:100vh}
.czx.expanded{position:fixed;inset:0;z-index:999990;height:100dvh}
.ico{font-family:"Material Symbols Rounded";font-weight:normal;font-style:normal;font-size:20px;line-height:1;
  display:inline-block;-webkit-font-smoothing:antialiased;font-feature-settings:"liga";vertical-align:middle}
.mut{opacity:.72;font-weight:400}
.czx button:focus-visible,.czx select:focus-visible,.czx input:focus-visible,.czx-map:focus-visible{outline:3px solid #5aa2f0;outline-offset:2px}

/* Toolbar band */
.czx-bar{display:flex;align-items:center;gap:8px;flex-wrap:wrap;padding:6px;border-radius:16px;
  background:linear-gradient(180deg,rgba(13,25,54,.92),rgba(8,16,38,.92));border:1px solid rgba(120,150,200,.22)}
.czx-layers{display:flex;flex-wrap:wrap;gap:4px;flex:1;min-width:0}
.czx-opts{display:flex;gap:4px;margin-left:auto;padding-left:6px;border-left:1px solid rgba(120,150,200,.2)}
.czx-layer,.czx-chip{display:inline-flex;align-items:center;gap:6px;white-space:nowrap;border:1px solid transparent;border-radius:10px;
  padding:6px 10px;min-height:34px;background:transparent;color:#d6e2f5;font:inherit;font-size:.84rem;cursor:pointer;
  transition:background .15s ease,border-color .15s ease,color .15s ease}
.czx-layer:hover,.czx-chip:hover{background:rgba(90,162,240,.14);color:#fff}
.czx-layer[aria-pressed="true"]{background:linear-gradient(180deg,rgba(74,163,255,.34),rgba(74,163,255,.14));
  border-color:rgba(74,163,255,.85);color:#fff;box-shadow:0 0 0 1px rgba(74,163,255,.3),0 0 16px rgba(74,163,255,.22),inset 0 -2px 0 #f2a93b}
.czx-chip[aria-pressed="true"]{border-color:rgba(242,169,59,.8);color:#ffd58f}
.czx-chip[hidden]{display:none}
.czx-layer .ico,.czx-chip .ico{font-size:18px}

/* Map band */
.czx-stage{position:relative;flex:1;min-height:280px;border-radius:18px;overflow:hidden;
  background:radial-gradient(ellipse at 50% 38%,#0e1d42 0%,#081530 55%,#050c20 100%);
  border:1px solid rgba(140,160,195,.2);box-shadow:0 2px 8px rgba(2,6,18,.35),0 20px 50px rgba(2,6,18,.4)}
.czx-map{position:absolute;inset:0}
.czx .leaflet-container{background:transparent;font-family:inherit;outline:none}
.czx-canvas{position:absolute;left:0;top:0;pointer-events:none;transition:opacity 220ms cubic-bezier(.16,1,.3,1)}
.czx.busy .czx-canvas{opacity:.45}
.czx-ctrl{position:absolute;top:10px;right:10px;z-index:800;display:flex;flex-direction:column;gap:6px;padding:5px;border-radius:14px;
  background:rgba(6,13,32,.72);border:1px solid rgba(120,150,200,.22);backdrop-filter:blur(6px);-webkit-backdrop-filter:blur(6px)}
.czx-ctrl .sep{height:1px;margin:1px 4px;background:rgba(120,150,200,.25)}
.czx-btn{display:inline-flex;align-items:center;justify-content:center;min-width:36px;height:36px;border-radius:10px;
  border:1px solid rgba(120,150,200,.28);background:rgba(8,16,38,.85);color:#e9f1fc;cursor:pointer;font:inherit;padding:0 8px;
  transition:background .15s ease,border-color .15s ease}
.czx-btn:hover{background:rgba(90,162,240,.22);border-color:rgba(90,162,240,.6)}
.czx-btn:disabled{opacity:.4;cursor:not-allowed}
.czx-btn[aria-pressed="true"]{border-color:rgba(242,169,59,.85);color:#ffd58f}
.czx-legend{position:absolute;left:10px;bottom:26px;z-index:800;width:min(270px,calc(100% - 90px));padding:6px 10px 8px;border-radius:12px;
  background:rgba(6,13,32,.8);border:1px solid rgba(120,150,200,.22);font-size:.76rem;backdrop-filter:blur(6px);-webkit-backdrop-filter:blur(6px)}
.czx-legend.collapsed{width:auto;padding-bottom:6px}
.czx-legend.collapsed .lg-b{display:none}
.lg-h{display:flex;align-items:center;gap:6px;width:100%;margin:0 0 6px;padding:2px 0;border:0;background:none;color:inherit;
  font-family:Outfit,Inter,sans-serif;font-size:.88rem;cursor:pointer;text-align:left;white-space:nowrap}
.czx-legend.collapsed .lg-h{margin:0}
.lg-h .kind{margin-left:auto;font-size:.66rem;padding:1px 7px;border-radius:999px;border:1px solid rgba(90,162,240,.6);color:#9ccaff}
.lg-h .chev{font-size:18px;opacity:.7}
.czx-legend .bar{height:9px;border-radius:5px;border:1px solid rgba(255,255,255,.15)}
.czx-legend .ticks{display:flex;justify-content:space-between;align-items:baseline;margin-top:3px;opacity:.9;font-variant-numeric:tabular-nums}
.czx-legend .ticks em{font-style:normal;opacity:.7;margin-left:6px}
.czx-legend .note{opacity:.75;margin-top:4px;line-height:1.35}
.czx-legend .types{display:flex;gap:10px;flex-wrap:wrap;margin-top:5px}
.czx-legend .types i{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:4px;vertical-align:-1px}
.czx-status{position:absolute;left:50%;top:12px;transform:translateX(-50%);z-index:900;max-width:calc(100% - 120px);
  padding:7px 14px;border-radius:999px;background:rgba(6,13,32,.92);border:1px solid rgba(90,162,240,.5);
  font-size:.8rem;display:none;text-align:center}
.czx-status.show{display:block}
.czx-status.error{border-color:#d1495b}
.czx-status.warn{border-color:#d98a00}
.czx-attr{position:absolute;right:0;bottom:0;z-index:800;max-width:calc(100% - 8px);padding:2px 8px;border-top-left-radius:8px;
  background:rgba(4,9,20,.72);font-size:.66rem;color:rgba(234,240,250,.85);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.czx-attr a{color:#9ccaff}
.czx-city{white-space:nowrap}
.czx-city span{position:relative;padding-left:8px;font-size:11px;color:rgba(236,242,252,.92);
  text-shadow:0 0 3px #04091a,0 0 6px #04091a,0 1px 2px #04091a;pointer-events:none}
.czx-city span::before{content:"";position:absolute;left:0;top:50%;width:4px;height:4px;margin-top:-2px;border-radius:50%;background:rgba(236,242,252,.9)}
.czx-city.cap span{font-weight:600}
.czx-city.cap span::before{background:#f2a93b}
.czx-home i{display:block;width:14px;height:14px;border-radius:50%;background:#0b1630;border:2px solid #f5b800;box-shadow:0 0 0 2px rgba(4,9,20,.6)}
.czx-pick i{display:block;width:18px;height:18px;border-radius:50%;border:2px solid #fff;background:rgba(90,162,240,.35);
  box-shadow:0 0 0 3px rgba(90,162,240,.35),0 0 12px rgba(90,162,240,.6);animation:czx-pulse 1.8s ease-out infinite}
@keyframes czx-pulse{0%{box-shadow:0 0 0 0 rgba(90,162,240,.55)}100%{box-shadow:0 0 0 14px rgba(90,162,240,0)}}
.czx-tip{background:#0b1630;color:#e9f1fc;border:1px solid #f5b800}
.czx .leaflet-popup.czx-pop .leaflet-popup-content-wrapper{background:rgba(8,16,38,.95);color:#eaf0fa;border:1px solid rgba(120,150,200,.35);
  border-radius:12px;box-shadow:0 12px 32px rgba(0,0,0,.45)}
.czx .leaflet-popup.czx-pop .leaflet-popup-tip{background:rgba(8,16,38,.95);border:1px solid rgba(120,150,200,.35)}
.czx .leaflet-popup.czx-pop .leaflet-popup-content{margin:10px 14px;font-size:.8rem;line-height:1.4}
.czx .leaflet-popup.czx-pop a.leaflet-popup-close-button{color:#9fb3d4}
.pp-c{font-weight:600}
.pp-v{font-family:Outfit,Inter,sans-serif;font-size:1.15rem;margin:2px 0 4px;color:#fff}
.pp-n{opacity:.75;font-size:.74rem}

/* Timeline band */
.czx-time{padding:8px 10px 8px;border-radius:16px;background:linear-gradient(180deg,rgba(13,25,54,.92),rgba(8,16,38,.92));
  border:1px solid rgba(120,150,200,.22)}
.czx-tl-row{display:flex;align-items:center;gap:6px}
.czx-btn.play{background:#2a78d6;border-color:#5aa2f0;min-width:44px}
.czx-btn.play:hover{background:#3488ea}
.czx-btn.now{font-size:.8rem;font-weight:600}
.czx-track{position:relative;flex:1;min-width:80px;padding-bottom:16px}
.czx-slider{width:100%;accent-color:#f2a93b;height:24px;cursor:pointer;margin:0}
.czx-ticks{position:absolute;left:8px;right:8px;top:24px;height:14px;pointer-events:none}
.czx-ticks .tk{position:absolute;top:0;width:1px;height:5px;background:rgba(200,215,240,.45);transform:translateX(-50%)}
.czx-ticks .tk.on{background:#f2a93b;height:7px;width:2px}
.czx-ticks .tk.miss{background:#d1495b;height:5px}
.czx-ticks .dl{position:absolute;top:5px;font-size:.66rem;opacity:.7;white-space:nowrap;transform:translateX(-2px)}
.czx-speed{background:rgba(8,16,38,.9);color:#e9f1fc;border:1px solid rgba(120,150,200,.3);border-radius:10px;height:36px;font:inherit;padding:0 6px}
.czx-tl-label{font-size:.78rem;margin-top:2px;overflow-wrap:anywhere;font-variant-numeric:tabular-nums;line-height:1.45}
.czx-tl-label b{font-family:Outfit,Inter,sans-serif;font-size:1rem;font-weight:500;letter-spacing:-.01em;color:#fff}
.czx-tl-label .utc{color:#9ccaff}
.czx-tl-label .sep{opacity:.4}

/* Narrower frames: names on the selected layer only, then icons only; the timeline wraps. */
.czx{container-type:inline-size}
@container (max-width:1100px){.czx-layer .t{display:none}.czx-layer[aria-pressed="true"] .t{display:inline}.czx-chip .t{display:none}}
@container (max-width:640px){
  .czx-layer[aria-pressed="true"] .t{display:none}
  .czx-layer,.czx-chip{padding:7px;min-width:38px;justify-content:center}
  .czx-opts{margin-left:0;border-left:0;padding-left:0}
  .czx-tl-row{flex-wrap:wrap}
  .czx-track{order:5;flex-basis:100%}
  .czx-legend{width:calc(100% - 80px)}
  .czx-legend .note{display:none}
  .czx-attr .run{display:none}
}
@media (max-width:640px){.czx{height:clamp(520px,calc(100vh - 24px),900px);height:clamp(480px,calc(100dvh - 90px),900px)}
  .czx-stage{border-radius:14px}}
@media (max-height:500px) and (orientation:landscape){.czx{height:max(340px,calc(100dvh - 40px))}}
@media (pointer:coarse){.czx-slider{height:36px}.czx-btn{min-width:44px;min-height:44px}.czx-speed{height:44px}
  .lg-h{min-height:40px}}
@media (prefers-reduced-motion:reduce){.czx-layer,.czx-chip,.czx-btn,.czx-canvas{transition:none}.czx-pick i{animation:none}}
"""

_component = None


def explorer_map(data: dict, key: str = "cloudzy_explorer"):
    """Mount the 2D map. Returns the component result; `.picked` holds {lat, lon} after a map click."""
    global _component
    import streamlit as st
    if _component is None:
        _component = st.components.v2.component("cloudzy_explorer", js=JS, css=CSS)
    return _component(key=key, data=data, on_picked_change=lambda: None)
