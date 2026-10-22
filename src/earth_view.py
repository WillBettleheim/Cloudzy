"""Cloudzy Earth Intelligence globe: a Streamlit custom component (components v2) on MapLibre GL JS 5.

What it draws
- A real 3D globe (MapLibre globe projection) that zooms continuously to street level. Detail comes from the
  map sources, not from magnifying one image:
  - NASA GIBS Blue Marble shaded relief and bathymetry (a 2004 composite, labelled as such) up to zoom 8, or
    NASA VIIRS true-colour imagery of a recent day (250 m; clouds and swath gaps are real);
  - OpenFreeMap vector tiles (OpenStreetMap data): coastlines, country and state borders, settlements, roads,
    railways, waterways, land use and, from about zoom 13, OpenStreetMap building footprints. These are mapped
    outlines, not imagery: no source here shows individual houses from above;
  - terrain shading from the AWS Terrain Tiles (Terrarium) elevation dataset.
- Hazard layers from src.hazards, read from content-hashed static files: earthquakes (radius by magnitude,
  colour by depth), other events as category shapes with icons (clustered at global scale, the severe ones never
  clustered), official warning polygons, cyclone cones and observed/forecast tracks, VIIRS fire detections
  (heat map far out, points close in), U.S. drought categories and the Holocene volcano catalogue.

Interaction
- The disaster situation selector (src.hazards.situations): 18 situations with search, presets, a selection saved
  in the viewer's browser, record layers and per-situation filters. Active filters are removable chips. Every
  change redraws in place (no new map) and is reported to Python as `view`, so the page's tables follow it.
  A selected record that the selection hides says why on its card, with a one-click fix.
- Drag, wheel/trackpad and pinch to move and zoom, double-click to zoom in, keyboard (arrows, +/-) on the
  focused map; buttons for zoom, whole-globe view, auto-rotation (stops on any interaction), fullscreen, layers
  and the event list. Clicking a marker or list entry selects it on both, flies to it and reports the id to
  Python, which shows the full source-backed details. Python can focus a searched place (`focus`, once per token)
  and highlight a region outline.
- Timeline: records are shown by their own times inside a sliding window; play/pause and steps move the
  cursor. Feeds that only provide current records (NWS, NHC, volcano alerts, tsunami bulletins, U.S. drought)
  appear only at "Now"; forecast track points too. Fire detections have real times within the last 24 hours.

Reliability
- No WebGL → a message (the page's table still lists every event). Map tiles that fail to load are counted and
  reported; hazard layers keep working. A failed base style falls back to imagery only.
- Static files are fetched once per version with an AbortController; switching versions cancels the old
  request. The map, timers and observers are released when the component leaves the page.
"""

from __future__ import annotations

JS = r"""
const ML_JS = "app/static/vendor/maplibre/maplibre-gl.js";
const ML_CSS = "app/static/vendor/maplibre/maplibre-gl.css";
const STYLE_URL = "https://tiles.openfreemap.org/styles/dark";
const FONT = ["Noto Sans Regular"];
const GIBS = "https://gibs.earthdata.nasa.gov/wmts/epsg3857/best";
const TERRAIN = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png";
const DEPTH = [[0, "#f2a541", "Shallow, under 70 km"], [70, "#d9584f", "Intermediate, 70-300 km"], [300, "#8b6fd6", "Deep, over 300 km"]];
const DROUGHT = ["#f5e6a3", "#e8c06b", "#d98e3a", "#b5402e", "#6e1b1b"];
const ZOOM_NAMES = [[2.5, "Earth"], [4, "Continent"], [6, "Country"], [8, "State or province"], [11, "City"], [14, "Neighbourhood"], [16, "Streets"], [99, "Building footprints (OpenStreetMap)"]];
const CURRENT_ONLY = new Set(["nws", "nhc", "usgs_volcano", "tsunami", "usdm"]);
// Filter defaults. The page mirrors the same rules for its table and summaries (src.hazards.situations.matches).
const F0 = { rank: 0, official: false, current: false, eqMag: 0, eqDepth: ["shallow", "intermediate", "deep"], tcBasin: "", tcRank: 0,
             flood: "", fireFrp: 0, fireConf: "", fireHours: 24, dmMin: 0, svProd: "", svActive: false };
const LK0 = { observed: true, official: true, provider: true, ml: true, historical: false };
const WARNING_SITS = new Set(["thunderstorm", "tornado", "wind", "dust", "heat", "cold", "snow", "other"]);
const SAVE_KEY = "cloudzy-earth-selection-v1";
const FILTER_NAMES = { rank: "Severity", official: "Official only", current: "Current only", eqMag: "Earthquakes from", eqDepth: "Depth",
  tcBasin: "Basin", tcRank: "Cyclone intensity", flood: "Flood records", fireFrp: "Fire power from", fireConf: "Fire confidence",
  fireHours: "Fires within", dmMin: "Drought from", svProd: "Warning product", svActive: "Warnings in force" };

export default function (component) {
  const { data, parentElement, setTriggerValue } = component;
  if (!data) return;
  let S = parentElement.__cze;
  if (!S) {
    S = parentElement.__cze = { data, ready: false, trigger: setTriggerValue };
    init(S, parentElement).catch((e) => fatal(S, "The globe couldn't start: " + (e && e.message ? e.message : e)));
  } else {
    S.data = data;
    S.trigger = setTriggerValue;
    if (S.ready) applyData(S);
  }
}

// --- Selection state: presets, saving, chips and the page sync ---------------------------------------------

function loadSaved() {
  try { const v = JSON.parse(window.localStorage.getItem(SAVE_KEY) || "null"); return v && v.on ? v : null; } catch (e) { return null; }
}

function config(S) {
  return { on: Object.keys(S.on).filter((k) => S.on[k]), lk: Object.assign({}, S.lk),
           f: Object.fromEntries(Object.keys(F0).map((k) => [k, S.f[k]])) };
}

function applyConfig(S, c) {
  const known = new Set(S.data.situations.filter((x) => x.connected).map((x) => x.key));
  if (c.on) for (const k of Object.keys(S.on)) S.on[k] = c.on.includes(k) && known.has(k);
  if (c.lk) Object.assign(S.lk, LK0, c.lk);
  if (c.f) for (const k of Object.keys(F0)) if (k in c.f) S.f[k] = c.f[k];
  syncControls(S);
}

function syncControls(S) {
  S.all("[data-cat]").forEach((i) => { i.checked = !!S.on[i.dataset.cat]; });
  S.all("[data-lk]").forEach((i) => { i.checked = !!S.lk[i.dataset.lk]; });
  S.all("[data-depth]").forEach((i) => { i.checked = S.f.eqDepth.includes(i.dataset.depth); });
  S.all("select[data-f], input[data-f]").forEach((i) => {
    const k = i.dataset.f;
    if (!(k in F0)) return;
    if (i.type === "checkbox") i.checked = !!S.f[k]; else i.value = String(S.f[k]);
  });
  S.all("details.sf").forEach((d) => {
    const k = d.dataset.for;
    d.hidden = k === "warnings" ? ![...WARNING_SITS].some((w) => S.on[w]) : !S.on[k];
  });
}

function presetKeys(S, name) {
  if (name === "mine") { const v = loadSaved(); return v ? v.on : null; }
  const p = (S.data.presets || {})[name];
  return p ? p.keys : null;
}

function changed(S) { syncControls(S); refresh(S); sendView(S); }

function sendView(S) {
  clearTimeout(S.viewTimer);
  S.viewTimer = setTimeout(() => S.trigger("view", Object.assign(config(S), { t: Date.now() })), 450);
}

function differs(a, b) { return JSON.stringify(a) !== JSON.stringify(b); }

function chips(S) {
  const out = [], d = S.data;
  const conn = S.data.situations.filter((c) => c.connected);
  const nOn = conn.filter((c) => S.on[c.key]).length;
  if (differs(S.on, S.defaults.on)) out.push(["sits", `${nOn} of ${conn.length} situations`]);
  for (const [k, t] of Object.entries(S.data.record_layers)) if (S.lk[k] !== LK0[k]) out.push(["lk:" + k, (S.lk[k] ? "Showing " : "Hiding ") + t.toLowerCase()]);
  for (const k of Object.keys(F0)) {
    if (!differs(S.f[k], F0[k])) continue;
    const v = S.f[k];
    const txt = typeof v === "boolean" ? FILTER_NAMES[k] : k === "rank" || k === "tcRank" ? `${FILTER_NAMES[k]}: ${["any", "minor+", "moderate+", "severe+", "extreme"][v]}`
      : k === "eqMag" ? `${FILTER_NAMES[k]} M${v}` : k === "fireFrp" ? `${FILTER_NAMES[k]} ${v} MW` : k === "fireHours" ? `${FILTER_NAMES[k]} ${v} h`
      : k === "dmMin" ? `${FILTER_NAMES[k]} D${v}` : k === "eqDepth" ? `${FILTER_NAMES[k]}: ${v.join(", ") || "none"}` : k === "fireConf" ? `${FILTER_NAMES[k]}: ${v === "h" ? "high" : "nominal+"}` : `${FILTER_NAMES[k]}: ${v}`;
    out.push(["f:" + k, txt]);
  }
  if (S.cursor != null) out.push(["time", "Timeline: " + fmtTime(S.cursor)]);
  if (d.chips && d.chips.period) out.push(["py:period", d.chips.period]);
  if (d.chips && d.chips.region) out.push(["py:region", "Near " + d.chips.region]);
  const el = S.el(".cze-chips");
  el.innerHTML = out.map(([k, t]) => `<span class="fchip">${esc(t)}<button type="button" data-clear="${esc(k)}" aria-label="Remove filter: ${esc(t)}"><span class="ico">close</span></button></span>`).join("")
    + (out.length ? `<button type="button" class="fchip all" data-clear="all">Clear all</button>` : "");
}

function clearChip(S, k) {
  if (k === "all" || k === "sits") Object.assign(S.on, S.defaults.on);
  if (k === "all") { Object.assign(S.lk, LK0); Object.assign(S.f, F0, { eqDepth: F0.eqDepth.slice() }); stopPlay(S); S.cursor = null; }
  if (k.startsWith("lk:")) S.lk[k.slice(3)] = LK0[k.slice(3)];
  if (k.startsWith("f:")) { const f = k.slice(2); S.f[f] = Array.isArray(F0[f]) ? F0[f].slice() : F0[f]; }
  if (k === "time") { stopPlay(S); S.cursor = null; }
  if (k.startsWith("py:") || k === "all") S.trigger("chip", { what: k === "all" ? "all" : k.slice(3), t: Date.now() });
  changed(S);
}

// Why a record isn't drawn, in words (shown on its card instead of letting it vanish silently).
function whyHidden(S, e, at) {
  const c = sitOf(S, e.sit);
  if (!S.on[e.sit]) return { text: `${c.label} are turned off in the situation selector.`, fix: ["sit", e.sit, "Show " + c.label.toLowerCase()] };
  if (!S.lk[e.lk]) return { text: `The "${S.data.record_layers[e.lk]}" layer is hidden.`, fix: ["lk", e.lk, "Show that layer"] };
  if (S.cursor != null && (CURRENT_ONLY.has(e.s) || e.tms == null || e.tms > at || (S.windowMs && (e.ems || e.tms) < at - S.windowMs)))
    return { text: "It's outside the timeline's current window.", fix: ["time", "", "Back to Now"] };
  return { text: "A filter excludes it (see the active filters at the top of the globe).", fix: ["all", "", "Clear all filters"] };
}

function sitOf(S, key) { return S.sitMap[key] || { key, label: key, color: "#9aa7bb", icon: "warning", shape: "square" }; }

function fatal(S, msg) {
  S.failed = true;
  if (S.el) { S.el(".cze-loading").hidden = true; status(S, msg, "error"); }
}

function status(S, text, kind) {
  const b = S.el(".cze-status");
  b.textContent = text || "";
  b.className = "cze-status" + (text ? " show" : "") + (kind ? " " + kind : "");
}

function webgl() {
  try { const c = document.createElement("canvas"); return !!(c.getContext("webgl2") || c.getContext("webgl")); }
  catch (e) { return false; }
}

function loadScript(src) {
  if (window.maplibregl) return Promise.resolve(window.maplibregl);
  if (window.__czMlPromise) return window.__czMlPromise;
  window.__czMlPromise = new Promise((resolve, reject) => {
    const s = document.createElement("script");
    s.src = src; s.async = true;
    s.onload = () => resolve(window.maplibregl);
    s.onerror = () => reject(new Error("the map library couldn't be loaded"));
    document.head.appendChild(s);
  });
  return window.__czMlPromise;
}

const esc = (t) => String(t == null ? "" : t).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const reduced = () => window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

async function init(S, root) {
  const wrap = document.createElement("div");
  root.appendChild(wrap);
  const css = document.createElement("link");
  css.rel = "stylesheet"; css.href = ML_CSS;
  wrap.appendChild(css);
  const cats = S.data.situations;
  const presets = Object.entries(S.data.presets || {});
  const opt = (v, t, sel) => `<option value="${esc(v)}"${sel ? " selected" : ""}>${esc(t)}</option>`;
  wrap.insertAdjacentHTML("beforeend", `
    <div class="cze" role="region" aria-label="Earth Intelligence globe">
      <div class="cze-map" tabindex="0" aria-label="Interactive globe. Drag or use the arrow keys to move, plus and minus to zoom. Select a marker for details."></div>
      <div class="cze-loading" role="status"><span class="spin" aria-hidden="true"></span><span>Loading the globe…</span></div>
      <div class="cze-top">
        <div class="cze-feed" role="status" aria-live="polite"></div>
        <div class="cze-notice">Situational awareness only, not an official warning service.</div>
        <div class="cze-chips" role="group" aria-label="Active filters"></div>
      </div>
      <div class="cze-side" role="toolbar" aria-label="Globe controls">
        <button type="button" class="cze-btn" data-act="zin" aria-label="Zoom in" title="Zoom in"><span class="ico">add</span></button>
        <button type="button" class="cze-btn" data-act="zout" aria-label="Zoom out" title="Zoom out"><span class="ico">remove</span></button>
        <button type="button" class="cze-btn" data-act="globe" aria-label="Back to the whole globe" title="Whole globe"><span class="ico">public</span></button>
        <button type="button" class="cze-btn" data-act="north" aria-label="Reset camera (north up, no tilt)" title="Reset camera"><span class="ico">explore</span></button>
        <button type="button" class="cze-btn" data-act="spin" aria-pressed="false" aria-label="Rotate the globe automatically" title="Auto-rotate (stops when you interact)"><span class="ico">autorenew</span></button>
        <button type="button" class="cze-btn" data-act="layers" aria-expanded="false" aria-label="Layers and filters" title="Layers and filters"><span class="ico">layers</span></button>
        <button type="button" class="cze-btn" data-act="list" aria-expanded="false" aria-label="Event list" title="Event list"><span class="ico">list</span></button>
        <button type="button" class="cze-btn" data-act="fs" aria-pressed="false" aria-label="Fullscreen" title="Fullscreen"><span class="ico">fullscreen</span></button>
      </div>
      <aside class="cze-panel cze-layers" hidden aria-label="Disaster situations, layers and filters">
        <div class="ph"><b>Situations and layers</b><button type="button" class="cze-btn sm" data-act="layers" aria-label="Close"><span class="ico">close</span></button></div>
        <div class="pg"><div class="pt">Disaster situations <span class="sel-n"></span></div>
          <input class="cze-sq" type="search" placeholder="Search situations" aria-label="Search the situation list"/>
          <div class="presets" role="group" aria-label="Presets">${presets.map(([k, p]) => `<button type="button" class="pre" data-preset="${k}">${esc(p.label)}</button>`).join("")}<button type="button" class="pre" data-preset="mine" title="Your saved selection (this browser only)">My selection</button></div>
          <div class="sits">${cats.map((c) => `<label class="tog sit${c.connected ? "" : " nc"}" data-sitrow="${c.key}" title="${esc(c.covers)}"><input type="checkbox" data-cat="${c.key}" ${c.on ? "checked" : ""} ${c.connected ? "" : "disabled"}/><span class="mk" style="--c:${c.color}"><span class="ico">${c.icon}</span></span><span class="lbl">${esc(c.label)}${c.connected ? "" : '<span class="ncb">not connected</span>'}</span><span class="n" data-count="${c.key}"></span></label>`).join("")}
            <p class="pnote nomatch" hidden>No situation matches.</p></div>
          <div class="row wrap"><button type="button" class="lnk" data-act="all">Show all</button><button type="button" class="lnk" data-act="none">All off</button><button type="button" class="lnk" data-act="defaults">Restore defaults</button><button type="button" class="lnk" data-act="save">Save as my selection</button></div>
          <p class="pnote">Hover a situation to see what it covers. "Not connected" means Cloudzy has no source for it: no markers there says nothing about events.</p>
        </div>
        <div class="pg"><div class="pt">Record layers</div>
          ${Object.entries(S.data.record_layers).map(([k, t]) => `<label class="tog"><input type="checkbox" data-lk="${k}" ${k === "historical" ? "" : "checked"}/><span class="lbl">${esc(t)}${k === "ml" ? ' <span class="dim">(Delhi temperature only)</span>' : k === "historical" ? ' <span class="dim">(Holocene volcano catalogue)</span>' : ""}</span></label>`).join("")}
        </div>
        <div class="pg"><div class="pt">Filters</div>
          <label class="fld">Minimum severity
            <select data-f="rank"><option value="0">Any</option><option value="1">Minor and above</option><option value="2">Moderate and above</option><option value="3">Severe and above</option><option value="4">Extreme only</option></select></label>
          <label class="tog"><input type="checkbox" data-f="official"/><span class="lbl">Official sources only</span></label>
          <label class="tog"><input type="checkbox" data-f="current"/><span class="lbl">Current records only</span></label>
          <details class="sf" data-for="earthquake"><summary>Earthquakes</summary>
            <label class="fld">Magnitude from <select data-f="eqMag">${[0, 3, 4, 5, 6, 7].map((m) => opt(m, m ? "M" + m : "Any loaded", !m)).join("")}</select></label>
            <div class="fld">Depth ${[["shallow", "Shallow, under 70 km"], ["intermediate", "70-300 km"], ["deep", "Over 300 km"]].map(([k, t]) => `<label class="tog"><input type="checkbox" data-depth="${k}" checked/><span class="lbl">${t}</span></label>`).join("")}</div></details>
          <details class="sf" data-for="cyclone"><summary>Tropical cyclones</summary>
            <label class="fld">Basin <select data-f="tcBasin">${opt("", "All basins", true)}${(S.data.basins || []).map((b) => opt(b, b)).join("")}</select></label>
            <label class="fld">Intensity <select data-f="tcRank">${opt(0, "Any", true)}${opt(2, "Moderate and above")}${opt(3, "Severe and above")}${opt(4, "Extreme only")}</select></label>
            <label class="tog"><input type="checkbox" data-ov="forecast" checked/><span class="lbl">Forecast positions and cones (official forecast, at Now)</span></label></details>
          <details class="sf" data-for="flood"><summary>Floods</summary>
            <label class="fld">Record type <select data-f="flood">${opt("", "All", true)}${opt("official", "Official warnings (NWS)")}${opt("automatic", "Automatic alerts (GDACS)")}</select></label>
            <p class="pnote">Flood forecasts (GloFAS) and observed inundation maps aren't connected.</p></details>
          <details class="sf" data-for="wildfire"><summary>Wildfires and fire detections</summary>
            <label class="fld">Fire radiative power from <select data-f="fireFrp">${[0, 5, 20, 50, 100].map((v) => opt(v, v ? v + " MW" : "Any", !v)).join("")}</select></label>
            <label class="fld">Detection confidence <select data-f="fireConf">${opt("", "Any", true)}${opt("nh", "Nominal or high")}${opt("h", "High only")}</select></label>
            <label class="fld">Detected within <select data-f="fireHours">${[24, 12, 6, 3].map((v) => opt(v, v + " h", v === 24)).join("")}</select></label></details>
          <details class="sf" data-for="drought"><summary>Drought</summary>
            <label class="fld">U.S. Drought Monitor from <select data-f="dmMin">${["D0 abnormally dry", "D1 moderate", "D2 severe", "D3 extreme", "D4 exceptional"].map((t, i) => opt(i, t, !i)).join("")}</select></label></details>
          <details class="sf" data-for="warnings"><summary>Severe-weather warnings</summary>
            <label class="fld">Product <select data-f="svProd">${opt("", "All", true)}${["Warning", "Watch", "Advisory", "Statement"].map((v) => opt(v, v)).join("")}</select></label>
            <label class="tog"><input type="checkbox" data-f="svActive"/><span class="lbl">In force now (not past its expiry)</span></label>
            <p class="pnote">Applies to thunderstorm, tornado, wind, dust, heat, cold, snow and other warnings.</p></details>
        </div>
        <div class="pg"><div class="pt">Overlays</div>
          <label class="tog"><input type="checkbox" data-ov="fires" checked/><span class="mk" style="--c:#ef6a45"><span class="ico">local_fire_department</span></span><span class="lbl">Satellite fire detections (VIIRS)</span></label>
          <label class="tog"><input type="checkbox" data-ov="areas" checked/><span class="mk sq" style="--c:#b58cf2"></span><span class="lbl">Official warning areas</span></label>
          <label class="tog"><input type="checkbox" data-ov="tracks" checked/><span class="mk" style="--c:#7ab8ff"><span class="ico">route</span></span><span class="lbl">Cyclone tracks and cones</span></label>
          <label class="tog"><input type="checkbox" data-ov="drought" checked/><span class="mk sq" style="--c:#d98e3a"></span><span class="lbl">U.S. Drought Monitor</span></label>
        </div>
        <div class="pg"><div class="pt">Filters</div>
          <label class="fld">Minimum severity
            <select data-f="rank"><option value="0">Any</option><option value="1">Minor and above</option><option value="2">Moderate and above</option><option value="3">Severe and above</option><option value="4">Extreme only</option></select></label>
          <label class="tog"><input type="checkbox" data-f="official"/><span class="lbl">Official sources only</span></label>
          <label class="tog"><input type="checkbox" data-f="current"/><span class="lbl">Current records only</span></label>
        </div>
        <div class="pg"><div class="pt">Base map</div>
          <label class="tog"><input type="radio" name="cze-base" value="relief" checked/><span class="lbl">Relief imagery (NASA Blue Marble, 2004 composite)</span></label>
          <label class="tog"><input type="radio" name="cze-base" value="viirs"/><span class="lbl">Satellite, <span class="vday"></span> (NASA VIIRS, 250 m)</span></label>
          <label class="tog"><input type="radio" name="cze-base" value="map"/><span class="lbl">Map only (OpenStreetMap)</span></label>
          <label class="tog"><input type="checkbox" data-ov="terrain" checked/><span class="lbl">Terrain shading</span></label>
          <label class="tog"><input type="checkbox" data-ov="labels" checked/><span class="lbl">Borders, places and roads</span></label>
        </div>
        <p class="pnote">Hiding a layer hides it; it doesn't mean there are no events.</p>
      </aside>
      <aside class="cze-panel cze-list" hidden aria-label="Event list">
        <div class="ph"><b>Events</b><span class="cnt"></span><button type="button" class="cze-btn sm" data-act="list" aria-label="Close list"><span class="ico">close</span></button></div>
        <input class="cze-q" type="search" placeholder="Filter by name or place" aria-label="Filter the event list"/>
        <div class="lopts"><label><input type="checkbox" data-f="inview"/> Only in view</label>
          <select data-f="sort" aria-label="Sort"><option value="sev">Most severe</option><option value="new">Newest</option></select></div>
        <ul class="cze-items" role="listbox" aria-label="Events"></ul>
      </aside>
      <div class="cze-card" hidden role="dialog" aria-label="Selected event"></div>
      <details class="cze-legend" open><summary>Legend</summary><div class="lgs" aria-live="polite"></div></details>
      <div class="cze-coord"><span class="lvl"></span><span class="ll"></span><button type="button" class="cze-btn sm" data-act="copy" aria-label="Copy the coordinates of the map centre" title="Copy centre coordinates"><span class="ico">content_copy</span></button></div>
      <div class="cze-time" role="group" aria-label="Timeline">
        <button type="button" class="cze-btn" data-act="play" aria-pressed="false" aria-label="Play" title="Play the period"><span class="ico">play_arrow</span></button>
        <button type="button" class="cze-btn" data-act="back" aria-label="Step back" title="Step back"><span class="ico">skip_previous</span></button>
        <button type="button" class="cze-btn" data-act="fwd" aria-label="Step forward" title="Step forward"><span class="ico">skip_next</span></button>
        <input class="cze-slider" type="range" min="0" max="1000" step="1" value="1000" aria-label="Timeline position"/>
        <select class="cze-win" aria-label="Show records from the last"><option value="3600000">1 h</option><option value="21600000">6 h</option><option value="86400000" selected>24 h</option><option value="604800000">7 days</option><option value="0">Whole period</option></select>
        <button type="button" class="cze-btn now" data-act="now" aria-label="Jump to now">Now</button>
        <div class="cze-tlabel" aria-live="polite"></div>
      </div>
      <div class="cze-status" role="status"></div>
    </div>`);
  S.root = root;
  S.sitMap = Object.fromEntries(cats.map((c) => [c.key, c]));
  S.el = (s) => root.querySelector(s);
  S.all = (s) => Array.from(root.querySelectorAll(s));
  S.defaults = { on: Object.fromEntries(cats.map((c) => [c.key, !!c.on])), f: Object.assign({}, F0), lk: Object.assign({}, LK0) };
  S.on = Object.assign({}, S.defaults.on);
  S.lk = Object.assign({}, LK0);
  S.ov = { fires: true, areas: true, tracks: true, forecast: true, drought: true, terrain: true, labels: true };
  S.f = Object.assign({ inview: false, sort: "sev", q: "" }, F0);
  const saved = loadSaved();
  if (saved) applyConfig(S, saved);
  S.base = "relief";
  S.cursor = null;                 // null = Now
  S.windowMs = 86400000;
  S.events = []; S.loaded = {}; S.tileErrors = 0;
  S.el(".vday").textContent = S.data.imagery_day;

  if (!webgl()) { fatal(S, "This browser or device doesn't support WebGL, so the globe can't be drawn. Every event is still listed in the table below."); return; }
  const maplibregl = await loadScript(ML_JS);
  let style;
  try { style = await (await fetch(STYLE_URL)).json(); S.vector = true; }
  catch (e) { style = { version: 8, sources: {}, layers: [{ id: "background", type: "background", paint: { "background-color": "#040914" } }], glyphs: "https://tiles.openfreemap.org/fonts/{fontstack}/{range}.pbf" }; S.vector = false; }
  prepareStyle(S, style);
  const map = new maplibregl.Map({
    container: S.el(".cze-map"), style, center: [20, 18], zoom: S.el(".cze").clientWidth < 700 ? 1.05 : 1.6, minZoom: 0.6, maxZoom: 19,
    attributionControl: false, renderWorldCopies: false, maxPitch: 60, dragRotate: true, touchPitch: false,
  });
  S.map = map;
  map.addControl(new maplibregl.AttributionControl({ compact: true, customAttribution: S.data.attribution }), "bottom-right");
  // Start collapsed (the "i" button opens it); the full source list is also printed under the globe.
  const attr = S.el(".maplibregl-ctrl-attrib");
  if (attr) { attr.classList.remove("maplibregl-compact-show"); attr.removeAttribute("open"); }
  map.addControl(new maplibregl.ScaleControl({ unit: "metric" }), "bottom-left");
  map.on("error", (e) => {
    const src = e && e.sourceId;
    if (src || (e && e.tile)) { S.tileErrors++; if (S.tileErrors === 5) status(S, "Some map tiles couldn't load; hazard layers are unaffected.", "warn"); }
  });
  map.on("style.load", () => {
    try { map.setProjection({ type: "globe" }); } catch (e) { /* older engines stay flat */ }
    try {
      map.setSky({ "sky-color": "#061024", "horizon-color": "#2d5ea8", "fog-color": "#0b1733", "sky-horizon-blend": 0.6,
                   "horizon-fog-blend": 0.6, "fog-ground-blend": 0.2,
                   "atmosphere-blend": ["interpolate", ["linear"], ["zoom"], 0, 1, 5, 1, 7, 0] });
    } catch (e) { /* atmosphere is decorative */ }
  });
  await new Promise((resolve) => map.once("load", resolve));
  await addImages(S);
  addLayers(S);
  wire(S);
  S.el(".cze-loading").hidden = true;
  if (S.el(".cze").clientWidth < 700) S.el(".cze-legend").open = false;   // collapsed on phones; one tap opens it
  S.ready = true;
  applyData(S);
  S.ro = new ResizeObserver(() => map.resize());
  S.ro.observe(S.el(".cze"));
  S.poll = setInterval(() => { if (!S.root.isConnected) teardown(S); }, 5000);
}

function teardown(S) {
  clearInterval(S.poll); clearInterval(S.playTimer);
  cancelAnimationFrame(S.spinRaf);
  if (S.ro) S.ro.disconnect();
  Object.values(S.aborts || {}).forEach((c) => c.abort());
  if (S.map) S.map.remove();
  S.ready = false;
}

function prepareStyle(S, style) {
  // Imagery and terrain go under waterways, buildings, roads, borders and labels; imagery fades out as vector detail
  // takes over. The style's own borders are brightened so countries and states read clearly on the globe.
  const relief = { type: "raster", tileSize: 256, maxzoom: 8, attribution: "Imagery: NASA GIBS Blue Marble (2004 composite)",
    tiles: [GIBS + "/BlueMarble_ShadedRelief_Bathymetry/default/GoogleMapsCompatible_Level8/{z}/{y}/{x}.jpeg"] };
  const viirs = { type: "raster", tileSize: 256, maxzoom: 9, attribution: "Imagery: NASA GIBS, VIIRS SNPP corrected reflectance",
    tiles: [GIBS + "/VIIRS_SNPP_CorrectedReflectance_TrueColor/default/" + S.data.imagery_day + "/GoogleMapsCompatible_Level9/{z}/{y}/{x}.jpg"] };
  style.sources["cz-relief"] = relief;
  style.sources["cz-viirs"] = viirs;
  style.sources["cz-dem"] = { type: "raster-dem", tiles: [TERRAIN], tileSize: 256, maxzoom: 14, encoding: "terrarium",
    attribution: "Terrain: Mapzen/AWS Terrain Tiles (SRTM, GMTED, ETOPO1 and others)" };
  const fade = ["interpolate", ["linear"], ["zoom"], 5, 1, 9, 0.15];
  const extra = [
    { id: "cz-relief", type: "raster", source: "cz-relief", paint: { "raster-opacity": fade, "raster-saturation": -0.15 } },
    { id: "cz-viirs", type: "raster", source: "cz-viirs", layout: { visibility: "none" }, paint: { "raster-opacity": fade } },
    { id: "cz-hill", type: "hillshade", source: "cz-dem", paint: { "hillshade-exaggeration": 0.35, "hillshade-shadow-color": "#02060f", "hillshade-highlight-color": "#8fa3c4" } },
  ];
  const at = Math.max(1, style.layers.findIndex((l) => l.id === "waterway"));
  style.layers.splice(at < 1 ? 1 : at, 0, ...extra);
  S.labelLayers = style.layers.filter((l) => l.type === "symbol" || /boundary|highway|railway|road|aeroway|building/.test(l.id)).map((l) => l.id);
  const english = ["coalesce", ["get", "name:en"], ["get", "name_en"], ["get", "name:latin"], ["get", "name"]];
  for (const l of style.layers) {
    if (l.type === "symbol" && /^place_|water_name/.test(l.id) && l.layout) l.layout["text-field"] = english;
    if (l.id.startsWith("boundary_country")) l.paint = Object.assign({}, l.paint, { "line-color": "#c9d6ea", "line-opacity": 0.75 });
    if (l.id === "boundary_state") l.paint = Object.assign({}, l.paint, { "line-color": "#93a6c4", "line-opacity": 0.55, "line-dasharray": [2, 2] });
  }
}

async function addImages(S) {
  try { await document.fonts.load('48px "Material Symbols Rounded"', "cyclone"); } catch (e) { /* glyphs fall back to shapes only */ }
  for (const c of S.data.situations.concat([{ key: "ml", color: "#34b37a", icon: "neurology", shape: "circle" }])) {
    for (const big of [false, true]) {
      const size = big ? 64 : 48, cv = document.createElement("canvas");
      cv.width = cv.height = size;
      const g = cv.getContext("2d"), m = size / 2, r = size * 0.4;
      g.beginPath();
      shape(g, c.shape, m, r);
      g.fillStyle = c.color; g.fill();
      g.lineWidth = size * 0.06; g.strokeStyle = "#ffffff"; g.stroke();
      g.fillStyle = "#06101f"; g.textAlign = "center"; g.textBaseline = "middle";
      g.font = `${Math.round(size * 0.46)}px "Material Symbols Rounded"`;
      try { g.fillText(c.icon, m, m + size * 0.04); } catch (e) { /* no glyph */ }
      S.map.addImage(`cz-${c.key}${big ? "-big" : ""}`, g.getImageData(0, 0, size, size), { pixelRatio: 2 });
    }
  }
}

function shape(g, kind, m, r) {
  const poly = (n, rot) => { for (let i = 0; i < n; i++) { const a = rot + i * 2 * Math.PI / n; const x = m + r * Math.cos(a), y = m + r * Math.sin(a); i ? g.lineTo(x, y) : g.moveTo(x, y); } g.closePath(); };
  if (kind === "triangle") poly(3, -Math.PI / 2);
  else if (kind === "triangle_down") poly(3, Math.PI / 2);
  else if (kind === "diamond") poly(4, 0);
  else if (kind === "square") { g.rect(m - r * 0.85, m - r * 0.85, r * 1.7, r * 1.7); }
  else if (kind === "hexagon") poly(6, 0);
  else if (kind === "star") { for (let i = 0; i < 10; i++) { const a = -Math.PI / 2 + i * Math.PI / 5, rr = i % 2 ? r * 0.55 : r; const x = m + rr * Math.cos(a), y = m + rr * Math.sin(a); i ? g.lineTo(x, y) : g.moveTo(x, y); } g.closePath(); }
  else if (kind === "drop") { g.moveTo(m, m - r); g.bezierCurveTo(m + r, m, m + r * 0.7, m + r, m, m + r); g.bezierCurveTo(m - r * 0.7, m + r, m - r, m, m, m - r); }
  else if (kind === "wave") { g.ellipse(m, m, r, r * 0.8, 0, 0, 2 * Math.PI); }
  else if (kind === "hurricane") { poly(8, Math.PI / 8); }
  else g.arc(m, m, r, 0, 2 * Math.PI);
}

const EMPTY = { type: "FeatureCollection", features: [] };

function addLayers(S) {
  const map = S.map;
  const src = (id, extra) => map.addSource(id, Object.assign({ type: "geojson", data: EMPTY }, extra || {}));
  src("cz-drought"); src("cz-fires"); src("cz-areas"); src("cz-tracks"); src("cz-trackpts"); src("cz-quakes");
  src("cz-major"); src("cz-volcat"); src("cz-region"); src("cz-selarea"); src("cz-sel"); src("cz-ml");
  src("cz-minor", { cluster: true, clusterRadius: 46, clusterMaxZoom: 4, clusterProperties: { maxr: ["max", ["get", "r"]] } });
  const catColor = ["match", ["get", "c"], ...S.data.situations.flatMap((c) => [c.key, c.color]), "#9aa7bb"];
  map.addLayer({ id: "drought", type: "fill", source: "cz-drought", paint: { "fill-color": ["match", ["get", "dm"], 0, DROUGHT[0], 1, DROUGHT[1], 2, DROUGHT[2], 3, DROUGHT[3], DROUGHT[4]], "fill-opacity": 0.33 } });
  map.addLayer({ id: "region-line", type: "line", source: "cz-region", paint: { "line-color": "#ffd58f", "line-width": 2 } });
  map.addLayer({ id: "areas-fill", type: "fill", source: "cz-areas", paint: { "fill-color": catColor, "fill-opacity": ["case", ["==", ["get", "kind"], "cone"], 0.10, 0.2] } });
  map.addLayer({ id: "areas-line", type: "line", source: "cz-areas", paint: { "line-color": catColor, "line-width": ["case", ["==", ["get", "kind"], "cone"], 1.4, 1.6], "line-dasharray": ["case", ["==", ["get", "kind"], "cone"], ["literal", [3, 2]], ["literal", [1, 0]]] } });
  map.addLayer({ id: "selarea-fill", type: "fill", source: "cz-selarea", paint: { "fill-color": "#ffd58f", "fill-opacity": 0.15 } });
  map.addLayer({ id: "selarea-line", type: "line", source: "cz-selarea", paint: { "line-color": "#ffd58f", "line-width": 2 } });
  map.addLayer({ id: "fires-heat", type: "heatmap", source: "cz-fires", maxzoom: 7, paint: {
    "heatmap-weight": ["interpolate", ["linear"], ["coalesce", ["get", "frp"], 1], 0, 0.2, 50, 1],
    "heatmap-radius": ["interpolate", ["linear"], ["zoom"], 0, 1.2, 3, 3, 6, 8],
    "heatmap-intensity": ["interpolate", ["linear"], ["zoom"], 0, 0.08, 3, 0.25, 6, 0.7], "heatmap-opacity": 0.75,
    "heatmap-color": ["interpolate", ["linear"], ["heatmap-density"], 0, "rgba(0,0,0,0)", 0.2, "#7a2a10", 0.5, "#d9532c", 0.8, "#f5a142", 1, "#fff1b8"] } });
  map.addLayer({ id: "fires-pt", type: "circle", source: "cz-fires", minzoom: 5, paint: {
    "circle-radius": ["interpolate", ["linear"], ["zoom"], 5, 1.6, 10, 4, 14, 7], "circle-color": ["interpolate", ["linear"], ["coalesce", ["get", "frp"], 0], 0, "#f5a142", 20, "#ef6a45", 100, "#c4223b"],
    "circle-stroke-color": "#2a0a05", "circle-stroke-width": 0.5, "circle-opacity": 0.9 } });
  map.addLayer({ id: "tracks-obs", type: "line", source: "cz-tracks", filter: ["==", ["get", "status"], "observed"], paint: { "line-color": "#cfe3ff", "line-width": 2.2 } });
  map.addLayer({ id: "tracks-fc", type: "line", source: "cz-tracks", filter: ["==", ["get", "status"], "forecast"], paint: { "line-color": "#7ab8ff", "line-width": 2, "line-dasharray": [2, 1.5] } });
  map.addLayer({ id: "trackpts", type: "circle", source: "cz-trackpts", paint: {
    "circle-radius": ["case", ["==", ["get", "status"], "current"], 6, 3.5], "circle-color": ["match", ["get", "status"], "forecast", "rgba(0,0,0,0)", "current", "#ffffff", "#cfe3ff"],
    "circle-stroke-color": ["match", ["get", "status"], "forecast", "#7ab8ff", "#0b1733"], "circle-stroke-width": 1.6 } });
  map.addLayer({ id: "trackpts-lbl", type: "symbol", source: "cz-trackpts", minzoom: 4, layout: { "text-field": ["get", "label"], "text-font": FONT, "text-size": 10, "text-offset": [0, 1.1], "text-anchor": "top", "text-optional": true },
    paint: { "text-color": "#dce8fa", "text-halo-color": "#040914", "text-halo-width": 1.2 } });
  map.addLayer({ id: "volcat", type: "symbol", source: "cz-volcat", minzoom: 2, layout: { "text-field": "▲", "text-font": FONT, "text-size": 11, "text-allow-overlap": false },
    paint: { "text-color": "#e8a08f", "text-halo-color": "#1a0603", "text-halo-width": 1 } });
  map.addLayer({ id: "volcat-lbl", type: "symbol", source: "cz-volcat", minzoom: 6, layout: { "text-field": ["get", "name"], "text-font": FONT, "text-size": 10, "text-offset": [0, 1], "text-anchor": "top" },
    paint: { "text-color": "#f3cbbf", "text-halo-color": "#1a0603", "text-halo-width": 1 } });
  map.addLayer({ id: "quakes", type: "circle", source: "cz-quakes", paint: {
    "circle-radius": ["interpolate", ["exponential", 1.7], ["coalesce", ["get", "mag"], 2], 2, 2.5, 4, 4.5, 5, 7, 6, 11, 7, 17, 8, 25],
    "circle-color": ["step", ["coalesce", ["get", "depth"], 0], DEPTH[0][1], 70, DEPTH[1][1], 300, DEPTH[2][1]],
    "circle-opacity": 0.78, "circle-stroke-color": "#ffffff", "circle-stroke-width": ["interpolate", ["linear"], ["coalesce", ["get", "mag"], 2], 2, 0.4, 6, 1.4] } });
  map.addLayer({ id: "quakes-lbl", type: "symbol", source: "cz-quakes", filter: [">=", ["coalesce", ["get", "mag"], 0], 5.5], layout: { "text-field": ["concat", "M", ["to-string", ["get", "mag"]]], "text-font": FONT, "text-size": 11, "text-offset": [0, 1.3], "text-anchor": "top", "text-optional": true },
    paint: { "text-color": "#fff3dc", "text-halo-color": "#040914", "text-halo-width": 1.3 } });
  map.addLayer({ id: "clusters", type: "circle", source: "cz-minor", filter: ["has", "point_count"], paint: {
    "circle-color": "rgba(14,26,54,0.86)", "circle-stroke-color": ["step", ["get", "maxr"], "#9aa7bb", 2, "#e8a33d", 3, "#e2566a"], "circle-stroke-width": 2,
    "circle-radius": ["step", ["get", "point_count"], 13, 20, 17, 80, 22] } });
  map.addLayer({ id: "cluster-n", type: "symbol", source: "cz-minor", filter: ["has", "point_count"], layout: { "text-field": ["get", "point_count_abbreviated"], "text-font": FONT, "text-size": 11, "text-allow-overlap": true },
    paint: { "text-color": "#eef3fb" } });
  map.addLayer({ id: "minor", type: "symbol", source: "cz-minor", filter: ["!", ["has", "point_count"]], layout: { "icon-image": ["concat", "cz-", ["get", "c"]], "icon-size": 0.85, "symbol-sort-key": ["-", 0, ["get", "r"]] } });
  map.addLayer({ id: "major", type: "symbol", source: "cz-major", layout: { "icon-image": ["concat", "cz-", ["get", "c"], "-big"], "icon-allow-overlap": true, "symbol-sort-key": ["-", 0, ["get", "r"]],
    "text-field": ["get", "short"], "text-font": FONT, "text-size": 11, "text-offset": [0, 1.6], "text-anchor": "top", "text-optional": true },
    paint: { "text-color": "#eef3fb", "text-halo-color": "#040914", "text-halo-width": 1.3 } });
  map.addLayer({ id: "ml", type: "symbol", source: "cz-ml", layout: { "icon-image": "cz-ml-big", "icon-allow-overlap": true,
    "text-field": ["get", "label"], "text-font": FONT, "text-size": 11, "text-offset": [0, 1.7], "text-anchor": "top", "text-optional": true },
    paint: { "text-color": "#bff0d8", "text-halo-color": "#040914", "text-halo-width": 1.3 } });
  map.addLayer({ id: "sel-ring", type: "circle", source: "cz-sel", paint: { "circle-radius": 19, "circle-color": "rgba(0,0,0,0)", "circle-stroke-color": "#ffd58f", "circle-stroke-width": 2.5 } });
}

function wire(S) {
  const map = S.map;
  const stopSpin = () => { if (S.spinning) toggleSpin(S, false); };
  ["mousedown", "touchstart", "wheel", "keydown"].forEach((ev) => S.el(".cze-map").addEventListener(ev, stopSpin, { passive: true }));
  S.root.addEventListener("click", (ev) => {
    const b = ev.target.closest("[data-act]");
    if (!b) return;
    const act = b.dataset.act;
    if (act === "zin") map.zoomIn(); else if (act === "zout") map.zoomOut();
    else if (act === "globe") fly(S, { center: [20, 18], zoom: S.el(".cze").clientWidth < 700 ? 1.05 : 1.6, bearing: 0, pitch: 0 });
    else if (act === "north") map.easeTo({ bearing: 0, pitch: 0, duration: reduced() ? 0 : 600 });
    else if (act === "spin") toggleSpin(S, !S.spinning);
    else if (act === "layers" || act === "list") togglePanel(S, act);
    else if (act === "fs") toggleFs(S);
    else if (act === "all" || act === "none") { S.all("[data-cat]:not([disabled])").forEach((i) => { S.on[i.dataset.cat] = act === "all"; }); changed(S); }
    else if (act === "defaults") { Object.assign(S.on, S.defaults.on); Object.assign(S.lk, LK0); Object.assign(S.f, F0, { eqDepth: F0.eqDepth.slice() }); changed(S); }
    else if (act === "save") {
      try { window.localStorage.setItem(SAVE_KEY, JSON.stringify(config(S))); status(S, "Saved as your selection in this browser."); }
      catch (e) { status(S, "This browser doesn't allow saving here (private mode or storage blocked).", "warn"); }
      setTimeout(() => status(S, ""), 2500);
    }
    else if (act === "fix") {
      const [what, k] = [b.dataset.what, b.dataset.k];
      if (what === "sit") S.on[k] = true; else if (what === "lk") S.lk[k] = true;
      else if (what === "time") { stopPlay(S); S.cursor = null; } else if (what === "all") return clearChip(S, "all");
      changed(S);
    }
    else if (act === "hide-sit") { S.on[b.dataset.k] = false; changed(S); }
    else if (act === "copy-ll") { const t = b.dataset.ll; if (navigator.clipboard) navigator.clipboard.writeText(t).then(() => status(S, "Copied " + t), () => status(S, t)); else status(S, t); setTimeout(() => status(S, ""), 2500); }
    else if (act === "play") togglePlay(S);
    else if (act === "back" || act === "fwd") step(S, act === "fwd" ? 1 : -1);
    else if (act === "now") { stopPlay(S); S.cursor = null; refresh(S); }
    else if (act === "copy") { const c = map.getCenter(); const t = `${c.lat.toFixed(5)}, ${c.lng.toFixed(5)}`; if (navigator.clipboard) navigator.clipboard.writeText(t).then(() => status(S, "Copied " + t), () => status(S, t)); else status(S, t); setTimeout(() => status(S, ""), 2500); }
    else if (act === "close-card") { S.el(".cze-card").hidden = true; S.cardClosed = true; }
  });
  S.root.addEventListener("click", (ev) => {
    const p = ev.target.closest("[data-preset]");
    if (p) {
      const keys = presetKeys(S, p.dataset.preset);
      if (!keys) { status(S, "No saved selection yet: choose situations, then “Save as my selection”."); setTimeout(() => status(S, ""), 3000); return; }
      for (const k of Object.keys(S.on)) S.on[k] = keys.includes(k) && !!sitOf(S, k).connected;
      changed(S);
      return;
    }
    const c = ev.target.closest("[data-clear]");
    if (c) clearChip(S, c.dataset.clear);
  });
  S.el(".cze-sq").addEventListener("input", (ev) => {
    const q = ev.target.value.trim().toLowerCase();
    let any = false;
    S.all("[data-sitrow]").forEach((r) => { const hit = !q || r.textContent.toLowerCase().includes(q) || (r.title || "").toLowerCase().includes(q); r.hidden = !hit; any = any || hit; });
    S.el(".nomatch").hidden = any;
  });
  S.root.addEventListener("change", (ev) => {
    const t = ev.target;
    if (t.dataset.cat) { S.on[t.dataset.cat] = t.checked; changed(S); }
    else if (t.dataset.lk) { S.lk[t.dataset.lk] = t.checked; changed(S); }
    else if (t.dataset.depth) { S.f.eqDepth = S.all("[data-depth]").filter((i) => i.checked).map((i) => i.dataset.depth); changed(S); }
    else if (t.dataset.ov) { S.ov[t.dataset.ov] = t.checked; refresh(S); }
    else if (t.dataset.f) {
      const k = t.dataset.f, num = typeof F0[k] === "number" || k === "rank";
      S.f[k] = t.type === "checkbox" ? t.checked : (num ? +t.value : t.value);
      if (k in F0) changed(S); else refresh(S);
    }
    else if (t.name === "cze-base") { S.base = t.value; refresh(S); }
    else if (t.classList.contains("cze-win")) { S.windowMs = +t.value; refresh(S); }
  });
  S.el(".cze-q").addEventListener("input", (ev) => { S.f.q = ev.target.value.toLowerCase(); renderList(S); });
  S.el(".cze-slider").addEventListener("input", (ev) => { stopPlay(S); const v = +ev.target.value; S.cursor = v >= 1000 ? null : S.t0 + (S.t1 - S.t0) * v / 1000; refresh(S); });
  S.el(".cze-items").addEventListener("click", (ev) => { const li = ev.target.closest("[data-id]"); if (li) select(S, li.dataset.id, true); });
  S.el(".cze-items").addEventListener("keydown", (ev) => { const li = ev.target.closest("[data-id]"); if (li && (ev.key === "Enter" || ev.key === " ")) { ev.preventDefault(); select(S, li.dataset.id, true); } });
  const clickable = ["major", "minor", "quakes", "areas-fill", "trackpts", "ml"];
  map.on("click", (e) => {
    const f = map.queryRenderedFeatures(e.point, { layers: clickable.concat(["clusters", "fires-pt", "volcat"]) })[0];
    if (!f) return;
    if (f.layer.id === "clusters") {
      map.getSource("cz-minor").getClusterExpansionZoom(f.properties.cluster_id).then((z) => fly(S, { center: f.geometry.coordinates, zoom: z + 0.3 }));
    } else if (f.layer.id === "fires-pt") {
      showFire(S, f);
    } else if (f.layer.id === "volcat") {
      showVolcano(S, f);
    } else if (f.layer.id === "ml") {
      showMl(S);
    } else if (f.properties.id) {
      select(S, f.properties.id, false);
    }
  });
  map.on("mousemove", (e) => {
    S.el(".ll").textContent = `${e.lngLat.lat.toFixed(3)}°, ${e.lngLat.lng.toFixed(3)}°`;
    const hit = map.queryRenderedFeatures(e.point, { layers: clickable.concat(["clusters", "fires-pt", "volcat"]) }).length;
    map.getCanvas().style.cursor = hit ? "pointer" : "";
  });
  map.on("zoomend", () => { zoomLabel(S); if (S.f.inview) renderList(S); });
  map.on("moveend", () => { if (S.f.inview) renderList(S); });
  zoomLabel(S);
  document.addEventListener("fullscreenchange", () => { const on = document.fullscreenElement === S.el(".cze"); S.el('[data-act="fs"]').setAttribute("aria-pressed", on); S.el('[data-act="fs"] .ico').textContent = on ? "fullscreen_exit" : "fullscreen"; setTimeout(() => map.resize(), 50); });
}

function zoomLabel(S) {
  const z = S.map.getZoom();
  S.el(".lvl").textContent = (ZOOM_NAMES.find(([lim]) => z < lim) || ZOOM_NAMES[ZOOM_NAMES.length - 1])[1] + ` · z${z.toFixed(1)}`;
}

function fly(S, opts) {
  if (reduced()) S.map.jumpTo(opts); else S.map.flyTo(Object.assign({ speed: 1.3, curve: 1.5, essential: false }, opts));
}

function togglePanel(S, which) {
  const p = S.el(which === "layers" ? ".cze-layers" : ".cze-list");
  const other = S.el(which === "layers" ? ".cze-list" : ".cze-layers");
  const open = p.hidden;
  p.hidden = !open;
  if (open && window.innerWidth < 700) other.hidden = true;
  S.all(`[data-act="${which}"]`).forEach((b) => b.setAttribute("aria-expanded", open));
  if (open && which === "list") renderList(S);
}

function toggleFs(S) {
  const el = S.el(".cze");
  if (document.fullscreenElement) document.exitFullscreen();
  else if (el.requestFullscreen) el.requestFullscreen().catch(() => { el.classList.toggle("expanded"); S.map.resize(); });
  else { el.classList.toggle("expanded"); S.map.resize(); }
}

function toggleSpin(S, on) {
  S.spinning = on && !reduced();
  S.el('[data-act="spin"]').setAttribute("aria-pressed", S.spinning);
  cancelAnimationFrame(S.spinRaf);
  if (!S.spinning) return;
  let last = performance.now();
  const tick = (t) => {
    if (!S.spinning || !S.root.isConnected) return;
    const dt = Math.min(t - last, 100); last = t;
    if (S.map.getZoom() < 3.5 && !S.map.isMoving()) { const c = S.map.getCenter(); S.map.jumpTo({ center: [c.lng + dt * 0.004, c.lat] }); }
    S.spinRaf = requestAnimationFrame(tick);
  };
  S.spinRaf = requestAnimationFrame(tick);
}

// --- Data ------------------------------------------------------------------------------------------

async function getJson(S, key, url) {
  S.aborts = S.aborts || {};
  if (S.aborts[key]) S.aborts[key].abort();
  const ctl = new AbortController();
  S.aborts[key] = ctl;
  const r = await fetch(url, { signal: ctl.signal });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

async function applyData(S) {
  const d = S.data, L = d.layers || {};
  S.t0 = d.period.start; S.t1 = d.period.end;
  feedChip(S);
  for (const [name, loader] of [["events", loadEvents], ["fires", loadFires], ["drought", loadDrought], ["volcano_catalogue", loadVolcat]]) {
    if (L[name] && S.loaded[name] !== L[name]) {
      S.loaded[name] = L[name];
      loader(S, L[name]).catch((e) => { if (e.name !== "AbortError") status(S, `A data layer couldn't load (${name}): ${e.message}`, "warn"); });
    }
  }
  // The page's situation picker (a fallback without WebGL, and in sync with this selector) wins when it changes.
  if (d.sit_sync && d.sit_sync.token && d.sit_sync.token !== S.sitToken) {
    S.sitToken = d.sit_sync.token;
    for (const k of Object.keys(S.on)) S.on[k] = d.sit_sync.on.includes(k) && !!sitOf(S, k).connected;
    syncControls(S);
  }
  if (!S.sentFirst) { S.sentFirst = true; sendView(S); }      // the page mirrors the globe's starting selection
  S.sitN = Object.fromEntries(d.situations.map((c) => [c.key, c.n]));
  setGeo(S, "cz-ml", d.ml && d.ml.lat != null ? { type: "Feature", geometry: { type: "Point", coordinates: [d.ml.lon, d.ml.lat] }, properties: { c: "ml", label: "Cloudzy ML" } } : EMPTY);
  setGeo(S, "cz-region", d.region ? { type: "Feature", geometry: d.region, properties: {} } : EMPTY);
  setGeo(S, "cz-selarea", d.selected_geometry ? { type: "Feature", geometry: d.selected_geometry, properties: {} } : EMPTY);
  if (d.focus && d.focus.token !== S.focusToken) {
    S.focusToken = d.focus.token;
    const f = d.focus;
    if (f.bbox) { const pad = Math.min(60, S.el(".cze").clientWidth / 8); if (reduced()) S.map.fitBounds(f.bbox, { padding: pad, duration: 0, maxZoom: f.maxzoom || 17 }); else S.map.fitBounds(f.bbox, { padding: pad, maxZoom: f.maxzoom || 17, speed: 1.2 }); }
    else fly(S, { center: [f.lon, f.lat], zoom: f.zoom || 9 });
  }
  // Follow Python's selection only when Python changes it (a table click), never when a rerun merely repeats an
  // older value while the viewer's newer click on the globe is still on its way to Python.
  if (d.selected !== S.pySelected) {
    S.pySelected = d.selected;
    if (d.selected && d.selected !== S.selected) select(S, d.selected, true, true);
  }
  refresh(S);
}

function setGeo(S, id, data) { const s = S.map.getSource(id); if (s) s.setData(data); }

function feedChip(S) {
  const st = S.data.statuses || [];
  const bad = st.filter((s) => s.label === "Temporarily unavailable" || s.label === "Delayed data");
  const live = st.filter((s) => s.label === "Live feed").length;
  const el = S.el(".cze-feed");
  el.className = "cze-feed " + (bad.length ? "warn" : "ok");
  el.innerHTML = `<span class="ico" aria-hidden="true">${bad.length ? "error" : "sensors"}</span><span>${st.length - bad.length} of ${st.length} feeds current · ${live} live${bad.length ? " · " + esc(bad.map((b) => b.name + ": " + b.label.toLowerCase()).join("; ")) : ""}</span>`;
}

async function loadEvents(S, url) {
  const list = await getJson(S, "events", url);
  S.events = list.map((e) => Object.assign(e, { tms: e.time ? Date.parse(e.time) : null, ems: e.end ? Date.parse(e.end) : null }));
  S.byId = new Map(S.events.map((e) => [e.id, e]));
  refresh(S);
  if (S.selected || S.data.selected) select(S, S.selected || S.data.selected, false, true);
}

async function loadFires(S, url) {
  const f = await getJson(S, "fires", url);
  const feats = new Array(f.lat.length);
  for (let i = 0; i < f.lat.length; i++) {
    feats[i] = { type: "Feature", geometry: { type: "Point", coordinates: [f.lon[i] / f.scale, f.lat[i] / f.scale] },
      properties: { frp: f.frp[i], tms: (f.base_minute + f.minute[i]) * 60000, conf: f.conf[i], night: f.night[i] } };
  }
  S.fires = { type: "FeatureCollection", features: feats };
  S.fireTimes = feats.map((x) => x.properties.tms);
  refresh(S);
}

async function loadDrought(S, url) { S.drought = await getJson(S, "drought", url); refresh(S); }
async function loadVolcat(S, url) { S.volcat = await getJson(S, "volcat", url); refresh(S); }

function visible(S, e, at) {
  if (!S.on[e.sit] || !S.lk[e.lk]) return false;
  if (e.r < S.f.rank || (S.f.official && !e.o) || (S.f.current && !e.cur)) return false;
  if (e.sit === "earthquake" && e.s === "usgs") {
    if (e.mag != null && e.mag < S.f.eqMag) return false;
    if (e.depth != null && !S.f.eqDepth.includes(e.depth < 70 ? "shallow" : e.depth < 300 ? "intermediate" : "deep")) return false;
  }
  if (e.sit === "cyclone" && ((S.f.tcBasin && e.basin !== S.f.tcBasin) || e.r < S.f.tcRank)) return false;
  if (e.sit === "flood" && S.f.flood && e.fl !== S.f.flood) return false;
  if (WARNING_SITS.has(e.sit)) {
    if (S.f.svProd && e.prod !== S.f.svProd) return false;
    if (S.f.svActive && !(e.cur && (e.ems == null || e.ems > Date.now()))) return false;
  }
  if (S.cursor != null) {
    if (CURRENT_ONLY.has(e.s)) return false;                          // current-only feed: shown at Now only
    if (e.tms == null || e.tms > at) return false;
    if (S.windowMs && (e.ems || e.tms) < at - S.windowMs) return false;
  }
  return true;
}

function refresh(S) {
  if (!S.ready) return;
  const map = S.map, at = S.cursor == null ? S.t1 : S.cursor;
  const ev = S.events.filter((e) => visible(S, e, at));
  S.shown = ev;
  const pt = (e, extra) => ({ type: "Feature", geometry: { type: "Point", coordinates: [e.lon, e.lat] },
    properties: Object.assign({ id: e.id, c: e.sit, r: e.r, short: e.c === "cyclone" ? e.t.split(" ").slice(-1)[0] : "" }, extra || {}) });
  const located = ev.filter((e) => e.lat != null && e.lon != null);
  setGeo(S, "cz-quakes", { type: "FeatureCollection", features: located.filter((e) => e.c === "earthquake").map((e) => pt(e, { mag: e.mag, depth: e.depth })) });
  setGeo(S, "cz-major", { type: "FeatureCollection", features: located.filter((e) => e.c !== "earthquake" && e.r >= 3).map((e) => pt(e)) });
  setGeo(S, "cz-minor", { type: "FeatureCollection", features: located.filter((e) => e.c !== "earthquake" && e.r < 3).map((e) => pt(e)) });
  const areas = [], tracks = [], tpts = [];
  for (const e of ev) {
    if (e.g && e.c !== "cyclone" && S.ov.areas) areas.push({ type: "Feature", geometry: e.g, properties: { id: e.id, c: e.sit, kind: "area" } });
    if (e.c === "cyclone" && S.ov.tracks) {
      if (e.g && S.cursor == null && S.ov.forecast) areas.push({ type: "Feature", geometry: e.g, properties: { id: e.id, c: "cyclone", kind: "cone" } });
      const pts = (e.track || []).map(([lon, lat, t, s]) => ({ lon, lat, tms: Date.parse(t), s, t }))
        .filter((p) => p.s !== "forecast" ? p.tms <= at : (S.cursor == null && S.ov.forecast));
      const obs = pts.filter((p) => p.s !== "forecast"), fc = pts.filter((p) => p.s === "forecast");
      if (obs.length > 1) tracks.push({ type: "Feature", geometry: { type: "LineString", coordinates: obs.map((p) => [p.lon, p.lat]) }, properties: { status: "observed" } });
      if (fc.length && obs.length) tracks.push({ type: "Feature", geometry: { type: "LineString", coordinates: [[obs[obs.length - 1].lon, obs[obs.length - 1].lat]].concat(fc.map((p) => [p.lon, p.lat])) }, properties: { status: "forecast" } });
      for (const p of pts) tpts.push({ type: "Feature", geometry: { type: "Point", coordinates: [p.lon, p.lat] }, properties: { id: e.id, status: p.s, label: new Date(p.tms).toISOString().slice(5, 13).replace("T", " ") + "Z" + (p.s === "forecast" ? " fcst" : "") } });
    }
  }
  setGeo(S, "cz-areas", { type: "FeatureCollection", features: areas });
  setGeo(S, "cz-tracks", { type: "FeatureCollection", features: tracks });
  setGeo(S, "cz-trackpts", { type: "FeatureCollection", features: tpts });
  const vis = (id, on) => { if (map.getLayer(id)) map.setLayoutProperty(id, "visibility", on ? "visible" : "none"); };
  // Fire detections and the drought map belong to their situations and are observed data.
  const showFires = S.ov.fires && S.fires && S.on.wildfire && S.lk.observed;
  if (showFires) {
    const lo = Math.max(S.windowMs ? at - S.windowMs : -Infinity, at - S.f.fireHours * 3600000);
    const conf = S.f.fireConf === "h" ? new Set(["h"]) : S.f.fireConf === "nh" ? new Set(["n", "h"]) : null;
    const keep = (p) => p.tms <= at && p.tms >= lo && (p.frp == null ? !S.f.fireFrp : p.frp >= S.f.fireFrp) && (!conf || conf.has(p.conf));
    const all = S.cursor == null && !S.windowMs && S.f.fireHours >= 24 && !S.f.fireFrp && !conf;
    S.fireShown = all ? S.fires : { type: "FeatureCollection", features: S.fires.features.filter((f) => keep(f.properties)) };
    setGeo(S, "cz-fires", S.fireShown);
  } else S.fireShown = null;
  const showDrought = S.ov.drought && S.drought && S.cursor == null && S.on.drought && S.lk.observed;
  setGeo(S, "cz-drought", showDrought ? (S.f.dmMin ? Object.assign({}, S.drought, { features: S.drought.features.filter((f) => f.properties.dm >= S.f.dmMin) }) : S.drought) : EMPTY);
  setGeo(S, "cz-volcat", S.lk.historical && S.volcat ? S.volcat : EMPTY);
  vis("ml", !!(S.lk.ml && S.data.ml && S.data.ml.lat != null));
  vis("fires-heat", showFires); vis("fires-pt", showFires);
  vis("cz-relief", S.base === "relief"); vis("cz-viirs", S.base === "viirs"); vis("cz-hill", S.ov.terrain);
  for (const id of S.labelLayers || []) vis(id, S.ov.labels);
  counts(S); legend(S); timeLabel(S); renderList(S); chips(S);
  if (S.selected && S.cardMode === "event" && !S.cardClosed) { const e = S.byId && S.byId.get(S.selected); if (e) card(S, e, at); }
}

function counts(S) {
  const n = {};
  for (const e of S.shown) n[e.sit] = (n[e.sit] || 0) + 1;
  const loaded = {};
  for (const e of S.events) loaded[e.sit] = (loaded[e.sit] || 0) + 1;
  S.all("[data-count]").forEach((el) => {
    const k = el.dataset.count, c = sitOf(S, k);
    el.textContent = !c.connected ? "" : S.on[k] ? (n[k] || 0).toLocaleString() : `off · ${(loaded[k] || 0).toLocaleString()}`;
    el.title = !c.connected ? "" : S.on[k] ? "Shown with the current filters and time" : "Hidden; loaded records";
  });
  const conn = S.data.situations.filter((c) => c.connected);
  S.el(".sel-n").textContent = `· ${conn.filter((c) => S.on[c.key]).length} of ${conn.length} on`;
}

function legend(S) {
  const parts = [];
  if (S.on.earthquake) parts.push(`<div class="lg"><b>Earthquakes</b> size = magnitude · colour = depth: ${DEPTH.map((d) => `<span class="sw" style="background:${d[1]}"></span>${d[2]}`).join(" ")}</div>`);
  const cats = S.data.situations.filter((c) => S.on[c.key] && c.key !== "earthquake");
  if (cats.length) parts.push(`<div class="lg">${cats.map((c) => `<span class="mk" style="--c:${c.color}"><span class="ico">${c.icon}</span></span>${esc(c.label)}`).join(" ")}</div>`);
  if (S.on.cyclone && S.ov.tracks) parts.push(`<div class="lg"><span class="ln"></span>observed track <span class="ln dash"></span>forecast (not a certain path) · dashed area: official forecast cone</div>`);
  if (S.lk.ml && S.data.ml && S.data.ml.lat != null) parts.push(`<div class="lg"><span class="mk" style="--c:#34b37a"><span class="ico">neurology</span></span>Cloudzy ML prediction (experimental, Delhi grid cell temperature only)</div>`);
  if (!S.data.situations.some((c) => S.on[c.key])) parts.push(`<div class="lg">No situation selected: open <b>Situations and layers</b> to choose some.</div>`);
  if (S.fireShown) parts.push(`<div class="lg"><span class="sw" style="background:#ef6a45"></span>satellite fire detection (not a confirmed fire)</div>`);
  if (S.ov.drought && S.drought && S.cursor == null && S.on.drought && S.lk.observed) parts.push(`<div class="lg">U.S. drought, map valid ${esc(S.drought.valid)}: ${["D0", "D1", "D2", "D3", "D4"].map((t, i) => `<span class="sw" style="background:${DROUGHT[i]}"></span>${t}`).join(" ")}</div>`);
  parts.push(`<div class="lg dim">Clusters group nearby minor events; severe ones are always shown.</div>`);
  S.el(".cze-legend .lgs").innerHTML = parts.join("");
}

function fmtTime(ms) {
  const d = new Date(ms);
  return d.toISOString().slice(0, 16).replace("T", " ") + " UTC";
}

function timeLabel(S) {
  const at = S.cursor == null ? S.t1 : S.cursor;
  const slider = S.el(".cze-slider");
  slider.value = S.cursor == null ? 1000 : Math.round((S.cursor - S.t0) / (S.t1 - S.t0) * 1000);
  slider.setAttribute("aria-valuetext", fmtTime(at));
  const local = new Date(at).toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
  S.el(".cze-tlabel").innerHTML = S.cursor == null
    ? `<b>Now</b> · all feeds · ${esc(fmtTime(at))}`
    : `<b>${esc(fmtTime(at))}</b> · ${esc(local)} your time · playback: records by their own times; current-only feeds hidden`;
}

function step(S, dir) {
  const span = S.t1 - S.t0, inc = Math.max(span / 48, 3600000);
  const cur = S.cursor == null ? S.t1 : S.cursor;
  const next = Math.min(S.t1, Math.max(S.t0, cur + dir * inc));
  S.cursor = next >= S.t1 ? null : next;
  refresh(S);
}

function togglePlay(S) {
  if (S.playTimer) return stopPlay(S);
  if (S.cursor == null) S.cursor = S.t0;
  S.el('[data-act="play"]').setAttribute("aria-pressed", "true");
  S.el('[data-act="play"] .ico').textContent = "pause";
  S.playTimer = setInterval(() => { step(S, 1); if (S.cursor == null) stopPlay(S); }, reduced() ? 1600 : 700);
}

function stopPlay(S) {
  clearInterval(S.playTimer); S.playTimer = null;
  S.el('[data-act="play"]').setAttribute("aria-pressed", "false");
  S.el('[data-act="play"] .ico').textContent = "play_arrow";
}

function ago(ms) {
  if (ms == null) return "time not given";
  const s = (Date.now() - ms) / 1000;
  if (s < 0) return "upcoming";
  if (s < 3600) return Math.round(s / 60) + " min ago";
  if (s < 172800) return Math.round(s / 3600) + " h ago";
  return Math.round(s / 86400) + " days ago";
}

function renderList(S) {
  const ul = S.el(".cze-items");
  if (S.el(".cze-list").hidden) return;
  let items = S.shown || [];
  if (S.f.q) items = items.filter((e) => (e.t + " " + e.area).toLowerCase().includes(S.f.q));
  if (S.f.inview) { const b = S.map.getBounds(); items = items.filter((e) => e.lat != null && b.contains([e.lon, e.lat])); }
  items = items.slice().sort(S.f.sort === "new" ? (a, b) => (b.tms || 0) - (a.tms || 0) : (a, b) => (b.r - a.r) || ((b.tms || 0) - (a.tms || 0)));
  S.el(".cze-list .cnt").textContent = `${items.length.toLocaleString()} shown`;
  const shown = items.slice(0, 250);
  ul.innerHTML = shown.map((e) => `<li role="option" tabindex="0" data-id="${esc(e.id)}" aria-selected="${e.id === S.selected}" class="${e.id === S.selected ? "on" : ""}">
      <span class="mk" style="--c:${sitOf(S, e.sit).color}"><span class="ico">${sitOf(S, e.sit).icon}</span></span>
      <span class="tx"><b>${esc(e.t)}</b><span class="sub">${esc(e.sev)} · ${esc(S.data.kinds[e.k] || e.k)} · ${esc(S.data.source_names[e.s] || e.s)}${e.lat == null ? " · no map location" : ""}</span><span class="sub">${esc(e.area)}${e.area ? " · " : ""}${ago(e.tms)}</span></span></li>`).join("")
    + (items.length > shown.length ? `<li class="more">${(items.length - shown.length).toLocaleString()} more: narrow the filters or zoom in with “Only in view”.</li>` : "")
    + (!items.length ? `<li class="more">${S.data.situations.some((c) => S.on[c.key]) ? "No records match the current situations, filters and time." : "No situation is selected."} That doesn't mean nothing is happening: check the selector, the active filters and the feed status.</li>` : "");
  const on = ul.querySelector(".on");
  if (on) on.scrollIntoView({ block: "nearest" });
}

function select(S, id, flyTo, fromPython) {
  const e = S.byId && S.byId.get(id);
  S.selected = id;
  setGeo(S, "cz-sel", e && e.lat != null ? { type: "Feature", geometry: { type: "Point", coordinates: [e.lon, e.lat] }, properties: {} } : EMPTY);
  S.cardMode = "event"; S.cardClosed = false;
  if (e) {
    S.el(".cze-card").dataset.id = "";
    card(S, e);
    if (flyTo && e.lat != null) fly(S, { center: [e.lon, e.lat], zoom: Math.max(S.map.getZoom(), e.c === "cyclone" ? 3.5 : 5.5) });
  }
  renderList(S);
  if (!fromPython) S.trigger("select", { id, t: Date.now() });
}

function card(S, e, at) {
  const c = sitOf(S, e.sit);
  const el = S.el(".cze-card");
  if (at === undefined) at = S.cursor == null ? S.t1 : S.cursor;
  const shown = visible(S, e, at), why = shown ? null : whyHidden(S, e, at);
  const ll = e.lat != null ? `${e.lat.toFixed(5)}, ${e.lon.toFixed(5)}` : "";
  if (el.dataset.id === e.id && el.dataset.shown === String(shown) && !el.hidden) return;   // nothing changed: keep focus
  el.dataset.id = e.id; el.dataset.shown = String(shown);
  el.hidden = false;
  el.innerHTML = `<div class="ch"><span class="mk" style="--c:${c.color}"><span class="ico">${c.icon}</span></span><b>${esc(e.t)}</b>
    <button type="button" class="cze-btn sm" data-act="close-card" aria-label="Close"><span class="ico">close</span></button></div>
    ${why ? `<div class="why" role="status"><span class="ico">visibility_off</span><span>Hidden on the globe: ${esc(why.text)}</span><button type="button" class="lnk" data-act="fix" data-what="${why.fix[0]}" data-k="${esc(why.fix[1])}">${esc(why.fix[2])}</button></div>` : ""}
    <div class="cb"><span class="chip">${esc(c.label)}</span><span class="chip">${esc(S.data.kinds[e.k] || e.k)}</span>${e.o ? '<span class="chip off">Official source</span>' : '<span class="chip">Not an official warning</span>'}</div>
    <p>${esc(e.sev)}${e.area ? " · " + esc(e.area) : ""}</p>
    <p class="dim">${e.tms ? esc(fmtTime(e.tms)) + " · " + ago(e.tms) : "Time not given"} · ${esc(S.data.source_names[e.s] || e.s)}</p>
    <div class="acts">${ll ? `<button type="button" class="lnk" data-act="copy-ll" data-ll="${ll}">Copy coordinates</button>` : ""}${shown ? `<button type="button" class="lnk" data-act="hide-sit" data-k="${esc(e.sit)}">Hide ${esc(c.label.toLowerCase())}</button>` : ""}</div>
    <p class="dim">Full details, source link and related events are below the globe.</p>`;
}

function showMl(S) {
  const m = S.data.ml, el = S.el(".cze-card");
  S.cardMode = "other";
  el.hidden = false; el.dataset.id = "";
  el.innerHTML = `<div class="ch"><span class="mk" style="--c:#34b37a"><span class="ico">neurology</span></span><b>Cloudzy Core ML prediction</b>
    <button type="button" class="cze-btn sm" data-act="close-card" aria-label="Close"><span class="ico">close</span></button></div>
    <div class="cb"><span class="chip">Cloudzy ML prediction</span><span class="chip">Experimental</span><span class="chip">Not a hazard forecast</span></div>
    <p>2 m temperature at the ${esc(m.where)}, issued ${esc(m.issue)} UTC by <code>${esc(m.version)}</code>${m.current ? "" : " (an earlier model version)"}.</p>
    <ul class="mlp">${m.rows.map((r) => `<li>+${r.h} h · ${esc(r.valid)} · <b>${r.value.toFixed(1)} °C</b>${r.lo != null ? ` · 80% range ${r.lo.toFixed(1)} to ${r.hi.toFixed(1)}` : ""} · ${esc(r.status)}</li>`).join("")}</ul>
    <p class="dim">${esc(m.note)}</p>`;
}

function showFire(S, f) {
  const p = f.properties, el = S.el(".cze-card");
  S.cardMode = "other";
  const conf = { l: "low", n: "nominal", h: "high", u: "unknown" }[p.conf] || "unknown";
  const [lon, lat] = f.geometry.coordinates;
  el.hidden = false;
  el.innerHTML = `<div class="ch"><span class="mk" style="--c:#ef6a45"><span class="ico">local_fire_department</span></span><b>Satellite fire detection</b>
    <button type="button" class="cze-btn sm" data-act="close-card" aria-label="Close"><span class="ico">close</span></button></div>
    <div class="cb"><span class="chip">Satellite detection</span><span class="chip">Not a confirmed fire</span></div>
    <p>${esc(fmtTime(p.tms))} · ${p.night ? "night" : "day"} overpass · VIIRS NOAA-20, 375 m pixel</p>
    <p>Fire radiative power ${p.frp == null ? "not given" : esc(p.frp) + " MW"} · confidence ${conf}</p>
    <p class="dim">${lat.toFixed(3)}°, ${lon.toFixed(3)}°. Industry, gas flares and volcanoes are also detected. Source: NASA FIRMS.</p>`;
  S.trigger("select", { fire: { lat, lon, frp: p.frp, tms: p.tms, conf, night: !!p.night }, t: Date.now() });
}

function showVolcano(S, f) {
  const p = f.properties, el = S.el(".cze-card");
  S.cardMode = "other";
  el.hidden = false;
  el.innerHTML = `<div class="ch"><span class="mk" style="--c:#d9584f"><span class="ico">landscape</span></span><b>${esc(p.name)}</b>
    <button type="button" class="cze-btn sm" data-act="close-card" aria-label="Close"><span class="ico">close</span></button></div>
    <div class="cb"><span class="chip">Reference catalogue</span></div>
    <p>${esc(p.type || "Volcano")} · ${esc(p.country || "")}${p.elev != null ? " · " + esc(p.elev) + " m" : ""}</p>
    <p>Last known eruption: ${p.last == null || p.last === "null" ? "unknown or none in the Holocene record" : esc(p.last)}</p>
    <p class="dim">Smithsonian GVP catalogue entry, not current activity. <a href="${esc(p.url)}" target="_blank" rel="noopener">Open the GVP page</a></p>`;
}
"""

CSS = r"""
.cze{position:relative;height:clamp(560px,78vh,860px);border-radius:var(--cz-r-xl,26px);overflow:hidden;background:#040914;
  color:#eef3fb;font-family:Inter,-apple-system,sans-serif;box-shadow:0 2px 8px rgba(2,6,18,.3),0 18px 48px rgba(2,6,18,.35);
  container-type:inline-size}
.cze:fullscreen,.cze.expanded{height:100vh;border-radius:0}
.cze.expanded{position:fixed;inset:0;z-index:999999}
.cze .cze-map{position:absolute;inset:0;width:100%;height:100%}   /* MapLibre's CSS would otherwise make it relative */
.cze [hidden]{display:none !important}     /* author display rules must never override the hidden attribute */
.cze-map:focus-visible{outline:3px solid #4aa3ff;outline-offset:-3px}
.cze .ico{font-family:"Material Symbols Rounded";font-weight:normal;font-style:normal;line-height:1;letter-spacing:normal;
  text-transform:none;white-space:nowrap;direction:ltr;font-feature-settings:"liga";-webkit-font-smoothing:antialiased;
  font-variation-settings:"FILL" 1,"wght" 400,"GRAD" 0,"opsz" 24;display:inline-block}
.cze-loading{position:absolute;inset:0;display:grid;place-content:center;gap:10px;justify-items:center;background:#040914;z-index:20;font-size:.9rem}
.cze-loading .spin{width:30px;height:30px;border-radius:50%;border:3px solid rgba(74,163,255,.25);border-top-color:#4aa3ff;animation:czespin 1s linear infinite}
@keyframes czespin{to{transform:rotate(360deg)}}
.cze-top{position:absolute;top:10px;left:10px;right:64px;z-index:6;display:flex;flex-wrap:wrap;gap:6px;pointer-events:none}
.cze-feed,.cze-notice{pointer-events:auto;display:inline-flex;align-items:center;gap:6px;padding:5px 10px;border-radius:999px;font-size:.74rem;
  background:rgba(6,13,32,.82);border:1px solid rgba(120,150,200,.25);backdrop-filter:blur(6px);-webkit-backdrop-filter:blur(6px);max-width:100%}
.cze-feed .ico{font-size:1rem}
.cze-feed.ok .ico{color:#34b37a}.cze-feed.warn .ico{color:#e9a636}
.cze-notice{color:#ffd9a8;border-color:rgba(242,169,59,.4)}
.cze-chips{flex-basis:100%;display:flex;flex-wrap:wrap;gap:4px;pointer-events:none}
.fchip{pointer-events:auto;display:inline-flex;align-items:center;gap:2px;padding:2px 4px 2px 9px;border-radius:999px;font-size:.7rem;
  background:rgba(42,120,214,.32);border:1px solid rgba(74,163,255,.6);color:#eef3fb}
.fchip button{display:inline-grid;place-items:center;width:18px;height:18px;border:0;border-radius:50%;background:none;color:inherit;cursor:pointer;padding:0}
.fchip button:hover{background:rgba(255,255,255,.15)}.fchip .ico{font-size:.8rem}
.fchip.all{padding:2px 9px;cursor:pointer;background:rgba(6,13,32,.85);font:500 .7rem Inter,sans-serif}
.cze-btn{display:inline-grid;place-items:center;width:38px;height:38px;border-radius:12px;border:1px solid rgba(120,150,200,.28);
  background:rgba(6,13,32,.84);color:#eef3fb;cursor:pointer;backdrop-filter:blur(6px);-webkit-backdrop-filter:blur(6px);
  transition:background .14s,box-shadow .14s}
.cze-btn:hover{background:rgba(25,45,90,.92);box-shadow:0 0 0 1px rgba(74,163,255,.45)}
.cze-btn:focus-visible{outline:3px solid #4aa3ff;outline-offset:2px}
.cze-btn[aria-pressed="true"],.cze-btn[aria-expanded="true"]{background:rgba(42,120,214,.5);border-color:#4aa3ff}
.cze-btn.sm{width:28px;height:28px;border-radius:8px}
.cze-btn.now{width:auto;padding:0 12px;font:600 .78rem Inter,sans-serif}
.cze-btn .ico{font-size:1.25rem}
.cze-side{position:absolute;top:10px;right:10px;z-index:7;display:flex;flex-direction:column;gap:6px}
.cze-panel{position:absolute;z-index:8;top:10px;width:300px;max-height:calc(100% - 86px);overflow:auto;padding:10px 12px;border-radius:16px;
  background:rgba(6,13,32,.94);border:1px solid rgba(120,150,200,.25);box-shadow:0 18px 48px rgba(2,6,18,.5);font-size:.82rem}
.cze-layers{left:10px;top:48px}
.cze-list{right:58px;width:340px}
.cze-panel .ph{display:flex;align-items:center;gap:8px;margin-bottom:6px}
.cze-panel .ph b{font-size:.95rem}.cze-panel .ph .cze-btn{margin-left:auto}
.cze-panel .cnt{opacity:.7;font-size:.75rem}
.cze-panel .pg{border-top:1px solid rgba(120,150,200,.18);padding:8px 0}
.cze-panel .pt{font-size:.66rem;text-transform:uppercase;letter-spacing:.12em;opacity:.65;font-weight:600;margin-bottom:4px}
.cze-panel .tog{display:flex;align-items:center;gap:8px;padding:3px 0;cursor:pointer;line-height:1.3}
.cze-panel .tog input{accent-color:#4aa3ff;flex:none}
.cze-panel .tog .lbl{flex:1}
.cze-panel .n{font-size:.72rem;opacity:.7;font-variant-numeric:tabular-nums}
.cze-panel .fld{display:flex;flex-direction:column;gap:3px;margin:3px 0 6px}
.cze-panel select,.cze-q,.cze-win{background:#0c1730;color:#eef3fb;border:1px solid rgba(120,150,200,.35);border-radius:8px;padding:4px 6px;font:inherit}
.cze-panel .row{display:flex;gap:10px;margin-top:4px}
.cze-panel .row.wrap{flex-wrap:wrap;gap:4px 12px}
.cze-panel .dim{opacity:.7}
.cze-sq{width:100%;box-sizing:border-box;margin:2px 0 6px;background:#0c1730;color:#eef3fb;border:1px solid rgba(120,150,200,.35);border-radius:8px;padding:5px 8px;font:inherit}
.presets{display:flex;flex-wrap:wrap;gap:4px;margin-bottom:6px}
.presets .pre{background:rgba(74,163,255,.1);color:#cfe3ff;border:1px solid rgba(74,163,255,.35);border-radius:999px;padding:2px 9px;font:500 .72rem Inter,sans-serif;cursor:pointer}
.presets .pre:hover{background:rgba(74,163,255,.25)}
.presets .pre:focus-visible,.fchip button:focus-visible,.cze-panel .lnk:focus-visible{outline:2px solid #4aa3ff;outline-offset:2px}
.sits{display:grid;gap:0;max-height:300px;overflow:auto;padding-right:2px}
.tog.sit.nc{opacity:.6;cursor:help}
.ncb{display:inline-block;margin-left:6px;padding:0 6px;border-radius:999px;font-size:.62rem;border:1px dashed rgba(200,215,240,.55);vertical-align:1px}
details.sf{border:1px solid rgba(120,150,200,.18);border-radius:10px;padding:4px 8px;margin:6px 0}
details.sf summary{cursor:pointer;font-weight:600;font-size:.78rem}
details.sf[open] summary{margin-bottom:4px}
.cze-panel .lnk{background:none;border:0;color:#9ccaff;cursor:pointer;font:inherit;padding:0}
.cze-panel .pnote{margin:6px 0 0;font-size:.72rem;opacity:.7}
.mk{display:inline-grid;place-items:center;width:20px;height:20px;border-radius:6px;background:var(--c);color:#06101f;flex:none;border:1px solid rgba(255,255,255,.7)}
.mk .ico{font-size:.85rem}
.mk.sq{border-radius:3px;opacity:.75}
.mk.dash{background:none;border:2px dashed #7ab8ff}
.cze-q{width:100%;margin:2px 0 6px;box-sizing:border-box}
.lopts{display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;font-size:.76rem}
.cze-items{list-style:none;margin:0;padding:0;display:grid;gap:2px}
.cze-items li{display:flex;gap:8px;padding:6px;border-radius:10px;cursor:pointer;border:1px solid transparent}
.cze-items li:hover,.cze-items li:focus-visible{background:rgba(74,163,255,.12);outline:none}
.cze-items li.on{background:rgba(255,213,143,.12);border-color:rgba(255,213,143,.55)}
.cze-items li.more{cursor:default;opacity:.75;font-size:.76rem}
.cze-items .tx{display:flex;flex-direction:column;gap:1px;min-width:0}
.cze-items .tx b{font-weight:600;font-size:.8rem;overflow-wrap:anywhere}
.cze-items .sub{font-size:.7rem;opacity:.72;overflow-wrap:anywhere}
.cze-card{position:absolute;left:10px;bottom:120px;z-index:8;width:min(340px,calc(100% - 80px));padding:10px 12px;border-radius:14px;
  background:rgba(6,13,32,.95);border:1px solid rgba(255,213,143,.45);font-size:.8rem;box-shadow:0 12px 40px rgba(2,6,18,.5)}
.cze-card .ch{display:flex;align-items:center;gap:8px}.cze-card .ch b{flex:1;font-size:.88rem}
.cze-card p{margin:5px 0}.cze-card .dim{opacity:.72;font-size:.74rem}
.cze-card a{color:#9ccaff}
.cze-card .cb{display:flex;gap:4px;flex-wrap:wrap;margin-top:6px}
.cze-card .why{display:flex;flex-wrap:wrap;align-items:center;gap:6px;margin-top:6px;padding:6px 8px;border-radius:10px;background:rgba(233,166,54,.14);border:1px solid rgba(233,166,54,.5)}
.cze-card .why .ico{font-size:1rem;color:#e9a636}
.cze-card .acts{display:flex;flex-wrap:wrap;gap:4px 14px;margin:4px 0}
.cze-card .lnk{background:none;border:0;color:#9ccaff;cursor:pointer;font:inherit;padding:0;text-decoration:underline}
.cze-card .mlp{margin:4px 0;padding-left:16px}.cze-card code{font-size:.7rem}
.chip{display:inline-block;padding:1px 8px;border-radius:999px;font-size:.68rem;border:1px solid rgba(200,215,240,.45)}
.chip.off{border-color:#34b37a;color:#9fe3c2}
.cze-legend{position:absolute;left:10px;bottom:62px;z-index:5;max-width:min(720px,calc(100% - 140px));padding:6px 10px;border-radius:12px;
  background:rgba(6,13,32,.8);border:1px solid rgba(120,150,200,.2);font-size:.7rem}
.cze-legend summary{cursor:pointer;font-weight:600;font-size:.72rem;letter-spacing:.06em;text-transform:uppercase;opacity:.85}
.cze-legend[open] summary{margin-bottom:3px}
.cze-legend .lgs{display:grid;gap:3px;max-height:150px;overflow:auto}
.cze-legend .lg{display:flex;flex-wrap:wrap;align-items:center;gap:4px 6px}
.cze-legend .dim{opacity:.65}
.cze-legend .mk{width:16px;height:16px}.cze-legend .mk .ico{font-size:.7rem}
.sw{display:inline-block;width:10px;height:10px;border-radius:3px;border:1px solid rgba(255,255,255,.5)}
.ln{display:inline-block;width:18px;border-top:2px solid #cfe3ff}.ln.dash{border-top:2px dashed #7ab8ff}
.cze-coord{position:absolute;right:10px;bottom:62px;z-index:5;display:flex;align-items:center;gap:6px;padding:3px 4px 3px 10px;border-radius:10px;
  background:rgba(6,13,32,.8);border:1px solid rgba(120,150,200,.2);font-size:.7rem;font-variant-numeric:tabular-nums}
.cze-coord .lvl{color:#ffd58f}
.cze-time{position:absolute;left:10px;right:10px;bottom:10px;z-index:6;display:flex;align-items:center;gap:6px;padding:6px;border-radius:14px;
  background:rgba(6,13,32,.86);border:1px solid rgba(120,150,200,.25);flex-wrap:wrap}
.cze-slider{flex:1;min-width:120px;accent-color:#4aa3ff}
.cze-tlabel{flex-basis:100%;font-size:.72rem;opacity:.85;padding:0 4px}
.cze-status{position:absolute;left:50%;top:52px;transform:translateX(-50%);z-index:9;padding:6px 12px;border-radius:10px;font-size:.78rem;
  background:rgba(6,13,32,.95);border:1px solid rgba(120,150,200,.3);opacity:0;pointer-events:none;transition:opacity .2s;max-width:80%}
.cze-status.show{opacity:1}.cze-status.error{border-color:#e2566a}.cze-status.warn{border-color:#e9a636}
.cze .maplibregl-ctrl-attrib,.cze .maplibregl-ctrl-attrib.maplibregl-compact{background:rgba(6,13,32,.88);color:#cfd9ea;font-size:10px}
.cze .maplibregl-ctrl-attrib.maplibregl-compact-show{max-width:min(520px,calc(100% - 20px))}
.cze .maplibregl-ctrl-attrib-button{filter:invert(1);background-color:transparent}
.cze .maplibregl-ctrl-attrib a{color:#9ccaff}
.cze .maplibregl-ctrl-bottom-right,.cze .maplibregl-ctrl-bottom-left{bottom:106px}
.cze .maplibregl-ctrl-scale{background:rgba(6,13,32,.7);color:#dce6f5;border-color:#dce6f5}
@container (min-width:701px){.cze-layers:not([hidden]) ~ .cze-card{left:346px}}
@container (max-width:700px){
  .cze-panel{left:0 !important;right:0 !important;top:auto !important;bottom:0;width:auto;max-height:70%;border-radius:18px 18px 0 0;z-index:12}
  .cze-legend{bottom:112px;max-width:calc(100% - 20px)}
  .cze-card{bottom:118px;left:8px;width:calc(100% - 70px)}
  .cze-coord{display:none}
  .cze-notice{font-size:.68rem}
}
@media (prefers-reduced-motion:reduce){.cze-loading .spin{animation:none}.cze-btn,.cze-status{transition:none}}
"""

_component = None


def earth_globe(data: dict, key: str = "cloudzy_earth"):
    """Mount the globe. `.select` → {id} or {fire: {...}} when the viewer selects something on the globe or list;
    `.view` → the selector state ({on, lk, f, t}) after each change; `.chip` → {what} when the viewer removes a
    filter chip that the page owns (period, region).

    `data`: layer URLs (from the hazard store), feed statuses, situations (src.hazards.situations.selector_data),
    presets, record layers and basins, kinds and source names, the period
    {start, end} in UTC milliseconds, the imagery day, attribution text, and optionally `focus` (a place to fly to,
    once per token), `region` (outline GeoJSON geometry), `selected` (an event id) and `selected_geometry`."""
    global _component
    import streamlit as st
    if _component is None:
        _component = st.components.v2.component("cloudzy_earth", js=JS, css=CSS)
    return _component(key=key, data=data, on_select_change=lambda: None, on_view_change=lambda: None,
                      on_chip_change=lambda: None)
