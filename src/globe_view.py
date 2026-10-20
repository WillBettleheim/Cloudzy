"""The Weather Explorer's 3D globe (Milestone 1): a Streamlit custom component (components v2) on three.js.

What it draws
- Earth: a sphere textured with NASA Blue Marble (shaded relief and bathymetry; a static, cloud-free
  composite, public domain), with a fresnel "atmosphere" rim. The rim is a visual approximation, not
  a physical scattering model. Lighting is a soft headlight so the globe reads as 3D; it does not
  show the real position of the sun (day/night lighting is a later milestone).
- Temperature: the GFS 2 m temperature of the selected forecast step, from the same frames the 2D map
  uses (src.explorer_data), painted onto a slightly larger transparent sphere. The 1° grid is sampled
  bilinearly, exactly as on the 2D map; nothing is invented between real grid values.

Interaction
- Drag to rotate, wheel or pinch to zoom, arrow keys and +/- on the focused globe; gentle inertia,
  none under reduced motion. Rendering happens only while something changes (no idle loop).
- Click selects a point: a ray finds the latitude/longitude on the sphere and Python receives it
  (the page then shows the same point panel as the 2D map).

Reliability
- No WebGL, a lost WebGL context or a failed texture → the component reports `fallback` and the page
  switches to the 2D map. All GPU resources and listeners are released when the component goes away.
"""

from __future__ import annotations

JS = r"""
const THREE_URL = "app/static/vendor/three/three.module.js";
const EARTH_URL = "app/static/vendor/earth/blue-marble-4096.jpg";
const TEMP_STOPS = [[-40,"#2a1b5f"],[-25,"#3c3fae"],[-10,"#3f7fd6"],[0,"#62b6e0"],[8,"#8fd3c0"],[16,"#e8e08c"],[24,"#f5a54a"],[32,"#e5582f"],[42,"#a31d3a"]];
const CACHE_MAX = 12;

function hexToRgb(h) { const n = parseInt(h.slice(1), 16); return [(n >> 16) & 255, (n >> 8) & 255, n & 255]; }
function makeRamp(stops) {
  const s = stops.map(([v, c]) => [v, hexToRgb(c)]);
  return (v) => {
    if (v <= s[0][0]) return s[0][1];
    for (let i = 1; i < s.length; i++) if (v <= s[i][0]) {
      const t = (v - s[i - 1][0]) / (s[i][0] - s[i - 1][0]), a = s[i - 1][1], b = s[i][1];
      return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t];
    }
    return s[s.length - 1][1];
  };
}
const fmtTemp = (c, u) => (u === "°F" ? c * 9 / 5 + 32 : c);

// Sphere UVs follow three.js SphereGeometry: u = (lon + 180) / 360, v from the north pole.
function toVec(THREE, lat, lon, r) {
  const theta = (90 - lat) * Math.PI / 180, phi = (lon + 180) * Math.PI / 180;
  return new THREE.Vector3(-r * Math.cos(phi) * Math.sin(theta), r * Math.cos(theta), r * Math.sin(phi) * Math.sin(theta));
}
function toLatLon(p) {
  const v = p.clone().normalize();
  const lat = Math.asin(Math.max(-1, Math.min(1, v.y))) * 180 / Math.PI;
  let lon = Math.atan2(v.z, -v.x) * 180 / Math.PI - 180;
  lon = ((lon + 540) % 360) - 180;
  return { lat, lon };
}

export default function (component) {
  const { data, parentElement, setTriggerValue } = component;
  if (!data) return;
  let S = parentElement.__czg;
  if (!S) {
    S = parentElement.__czg = { data, ready: false };
    init(S, parentElement, setTriggerValue).catch((e) => fail(S, setTriggerValue, "The 3D globe couldn't start: " + (e && e.message ? e.message : e)));
  } else {
    S.data = data;
    if (S.ready) applyData(S);
  }
}

function fail(S, setTriggerValue, msg) {
  if (S.failed) return;
  S.failed = true;
  const box = S.el ? S.el(".czg-status") : null;
  if (box) { box.textContent = msg + " Switching to the 2D map…"; box.className = "czg-status show error"; }
  setTriggerValue("fallback", { reason: msg, t: Date.now() });
}

function webglAvailable() {
  try { const c = document.createElement("canvas"); return !!(c.getContext("webgl2") || c.getContext("webgl")); }
  catch (e) { return false; }
}

async function init(S, root, setTriggerValue) {
  const wrap = document.createElement("div");      // append: Streamlit keeps this component's CSS in root
  root.appendChild(wrap);
  wrap.innerHTML = `
    <div class="czg" role="region" aria-label="3D globe">
      <div class="czg-stage" tabindex="0" aria-label="3D globe. Drag or use arrow keys to rotate, plus and minus to zoom, click to select a point."></div>
      <div class="czg-top">
        <div class="czg-layers" role="toolbar" aria-label="Globe layers">
          <button type="button" class="czg-layer" data-layer="temperature" aria-pressed="true"><span class="ico" aria-hidden="true">thermostat</span><span class="t">Temperature</span></button>
          <button type="button" class="czg-layer" data-layer="none" aria-pressed="false"><span class="ico" aria-hidden="true">public</span><span class="t">Earth only</span></button>
        </div>
      </div>
      <div class="czg-side">
        <button type="button" class="czg-btn" data-act="zoomin" aria-label="Zoom in"><span class="ico">add</span></button>
        <button type="button" class="czg-btn" data-act="zoomout" aria-label="Zoom out"><span class="ico">remove</span></button>
        <button type="button" class="czg-btn" data-act="reset" aria-label="Reset view" title="Reset view"><span class="ico">public</span></button>
        <button type="button" class="czg-btn" data-act="city" aria-label="Turn to the selected place" title="Turn to the selected place"><span class="ico">my_location</span></button>
      </div>
      <div class="czg-legend" aria-live="polite"></div>
      <div class="czg-readout" aria-live="polite"></div>
      <div class="czg-status show" role="status">Loading the globe…</div>
      <div class="czg-time">
        <button type="button" class="czg-btn" data-act="prev" aria-label="Previous forecast step"><span class="ico">skip_previous</span></button>
        <button type="button" class="czg-btn" data-act="next" aria-label="Next forecast step"><span class="ico">skip_next</span></button>
        <input class="czg-slider" type="range" min="0" max="0" step="1" value="0" aria-label="Forecast time" />
        <button type="button" class="czg-btn now" data-act="now" aria-label="Jump to the step nearest to now">Now</button>
        <div class="czg-tl-label" aria-live="polite"></div>
      </div>
      <div class="czg-attr"></div>
    </div>`;
  S.root = root;
  S.el = (s) => root.querySelector(s);
  S.status = (t, kind) => { const b = S.el(".czg-status"); b.textContent = t || ""; b.className = "czg-status" + (t ? " show" : "") + (kind ? " " + kind : ""); };

  if (!webglAvailable()) return fail(S, setTriggerValue, "This browser or device doesn't support WebGL.");
  const THREE = await import(new URL(THREE_URL, document.baseURI).href);
  S.THREE = THREE;
  S.reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  const stage = S.el(".czg-stage");
  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false, powerPreference: "high-performance" });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, window.innerWidth < 640 ? 1.5 : 2));
  renderer.setClearColor(0x040914, 1);
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  stage.appendChild(renderer.domElement);
  renderer.domElement.addEventListener("webglcontextlost", (e) => { e.preventDefault(); fail(S, setTriggerValue, "The graphics context was lost."); });
  S.renderer = renderer;

  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(32, 1, 0.1, 100);
  S.scene = scene; S.camera = camera;
  S.dist = 4;
  S.minDist = 1.35; S.maxDist = 12;

  // Globe group: rotated by the controls.
  const globe = new THREE.Group(); scene.add(globe); S.globe = globe;
  const loader = new THREE.TextureLoader();
  const earthTex = await new Promise((res, rej) => loader.load(new URL(EARTH_URL, document.baseURI).href, res, undefined,
    () => rej(new Error("the Earth texture didn't load"))));
  earthTex.colorSpace = THREE.SRGBColorSpace;
  earthTex.anisotropy = Math.min(8, renderer.capabilities.getMaxAnisotropy());
  const earthGeo = new THREE.SphereGeometry(1, 128, 64);
  const earthMat = new THREE.MeshStandardMaterial({ map: earthTex, roughness: 0.95, metalness: 0 });
  globe.add(new THREE.Mesh(earthGeo, earthMat));
  S.earthMesh = globe.children[0];

  // Temperature overlay sphere (texture painted from the GFS grid).
  S.overlayCanvas = document.createElement("canvas"); S.overlayCanvas.width = 720; S.overlayCanvas.height = 360;
  S.overlayTex = new THREE.CanvasTexture(S.overlayCanvas);
  S.overlayTex.colorSpace = THREE.SRGBColorSpace;
  const overlay = new THREE.Mesh(new THREE.SphereGeometry(1.004, 128, 64),
    new THREE.MeshBasicMaterial({ map: S.overlayTex, transparent: true, opacity: 0.66, depthWrite: false }));
  globe.add(overlay); S.overlay = overlay;

  // Atmosphere rim (fresnel glow on a back-facing shell): a visual approximation, not physical scattering.
  const atmo = new THREE.Mesh(new THREE.SphereGeometry(1.07, 96, 48), new THREE.ShaderMaterial({
    side: THREE.BackSide, transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
    vertexShader: "varying vec3 vN; varying vec3 vP; void main(){ vN = normalize(normalMatrix*normal); vec4 mv = modelViewMatrix*vec4(position,1.0); vP = mv.xyz; gl_Position = projectionMatrix*mv; }",
    fragmentShader: "varying vec3 vN; varying vec3 vP; void main(){ float f = pow(clamp(1.0 - abs(dot(normalize(-vP), vN)), 0.0, 1.0), 2.4); gl_FragColor = vec4(0.29,0.60,1.0,1.0) * f * 1.35; }",
  }));
  scene.add(atmo); S.atmo = atmo;

  // Soft headlight so the globe reads as a sphere; deliberately not the sun.
  scene.add(new THREE.AmbientLight(0xffffff, 0.55));
  const head = new THREE.DirectionalLight(0xffffff, 1.6); camera.add(head); head.position.set(-1.5, 1.2, 1); scene.add(camera);

  // Markers.
  S.cityMarker = new THREE.Mesh(new THREE.SphereGeometry(0.012, 16, 12), new THREE.MeshBasicMaterial({ color: 0xf2a93b }));
  S.pickMarker = new THREE.Mesh(new THREE.SphereGeometry(0.010, 16, 12), new THREE.MeshBasicMaterial({ color: 0xffffff }));
  S.cityMarker.visible = S.pickMarker.visible = false;
  globe.add(S.cityMarker, S.pickMarker);

  // Orientation: rotX (tilt) and rotY (spin); the front of the globe faces the camera along +z.
  S.rot = { x: 0.35, y: 0 }; S.vel = { x: 0, y: 0 };
  faceLonLat(S, 10, 20);

  layout(S); setupControls(S, setTriggerValue);
  S.ro = new ResizeObserver(() => { layout(S); requestRender(S); }); S.ro.observe(stage);

  for (const b of S.root.querySelectorAll(".czg-layer")) b.addEventListener("click", () => setLayer(S, b.dataset.layer));
  S.el(".czg-slider").addEventListener("input", (ev) => setFrame(S, +ev.target.value));
  root.addEventListener("click", (ev) => {
    const b = ev.target.closest && ev.target.closest("[data-act]"); if (!b) return;
    const a = b.dataset.act;
    if (a === "zoomin") zoom(S, 0.85); if (a === "zoomout") zoom(S, 1 / 0.85);
    if (a === "reset") { S.zoomed = false; S.dist = fitDist(S); faceLonLat(S, 10, 20); requestRender(S); }
    if (a === "city" && S.data.city) { faceLonLat(S, S.data.city.lon, S.data.city.lat); S.zoomed = true; S.dist = Math.min(S.dist, 2.6); requestRender(S); }
    if (a === "prev") setFrame(S, S.frameIdx - 1);
    if (a === "next") setFrame(S, S.frameIdx + 1);
    if (a === "now") goNow(S);
  });

  S.cache = new Map(); S.layer = "temperature"; S.frameIdx = 0; S.ready = true;
  S.status("Loading forecast data…");
  applyData(S);
  await loadManifest(S);
  goNow(S); layout(S); requestRender(S);
  S.poll = setInterval(() => { if (!S.root.isConnected) return teardown(S); loadManifest(S); }, 30000);
}

function teardown(S) {
  clearInterval(S.poll); cancelAnimationFrame(S.raf);
  if (S.abort) S.abort.abort();
  if (S.ro) S.ro.disconnect();
  if (S.unbind) S.unbind();
  S.scene.traverse((o) => {
    if (o.geometry) o.geometry.dispose();
    if (o.material) { for (const k of ["map"]) if (o.material[k]) o.material[k].dispose(); o.material.dispose(); }
  });
  S.renderer.dispose(); try { S.renderer.forceContextLoss(); } catch (e) {}
  S.cache.clear(); S.ready = false;
}

function resize(S) {
  const stage = S.el(".czg-stage"); const w = stage.clientWidth || 1, h = stage.clientHeight || 1;
  S.renderer.setSize(w, h, false); S.renderer.domElement.style.width = w + "px"; S.renderer.domElement.style.height = h + "px";
  S.camera.aspect = w / h; S.camera.updateProjectionMatrix();
}

function faceLonLat(S, lon, lat) {
  // Spin so that (lat, lon) faces the camera (+z), with a tilt that keeps the poles sensible.
  const p = toVec(S.THREE, 0, lon, 1);
  S.rot.y = -Math.atan2(p.x, p.z);
  S.rot.x = Math.max(-1.2, Math.min(1.2, lat * Math.PI / 180));
}

// --- Rendering on demand ---
function requestRender(S) {
  if (S.pending || !S.ready) return;
  S.pending = true;
  S.raf = requestAnimationFrame(() => { S.pending = false; frame(S); });
}
function frame(S) {
  if (!S.root.isConnected) return teardown(S);
  // Inertia (none under reduced motion).
  let moving = false;
  if (!S.dragging && !S.reduce && (Math.abs(S.vel.x) > 1e-4 || Math.abs(S.vel.y) > 1e-4)) {
    S.rot.y += S.vel.y; S.rot.x += S.vel.x; S.vel.x *= 0.92; S.vel.y *= 0.92; moving = true;
  }
  S.rot.x = Math.max(-1.35, Math.min(1.35, S.rot.x));
  S.globe.rotation.set(S.rot.x, S.rot.y, 0, "XYZ");
  S.camera.position.set(0, 0, S.dist); S.camera.lookAt(0, 0, 0);
  S.renderer.render(S.scene, S.camera);
  if (moving) requestRender(S);
}

function layout(S) { resize(S); placePanels(S); if (!S.zoomed) S.dist = fitDist(S); }

// Camera distance at which the globe and its rim fill about 90% of the free area's shorter side.
function fitDist(S) {
  const t = Math.tan(S.camera.fov * Math.PI / 360), h = S.view.h, side = Math.min(S.view.w, S.view.freeH);
  return Math.max(S.minDist, Math.min(S.maxDist, 1.08 * (h / 2) / (t * 0.45 * side)));
}
// Keep the legend and readout just above the timeline, whatever height it wraps to. On narrow screens the
// legend spans the width, so the globe is centred in the space between the top controls and the legend.
function placePanels(S) {
  const tl = S.el(".czg-time"), stage = S.el(".czg-stage"); if (!tl) return;
  const bottom = tl.offsetHeight + 30, lg = S.el(".czg-legend");
  lg.style.bottom = bottom + "px"; S.el(".czg-readout").style.bottom = bottom + "px";
  const w = stage.clientWidth || 1, h = stage.clientHeight || 1;
  let top = 0, end = h;
  if (w < 640) { top = S.el(".czg-top").offsetHeight + 16; end = h - bottom - lg.offsetHeight - 8; }
  S.view = { w, h, freeH: Math.max(120, end - top), offY: Math.round(h / 2 - (top + end) / 2) };
  if (S.view.offY) S.camera.setViewOffset(w, h, 0, S.view.offY, w, h); else S.camera.clearViewOffset();
}
function zoom(S, k) { S.zoomed = true; S.dist = Math.max(S.minDist, Math.min(S.maxDist, S.dist * k)); requestRender(S); }

function setupControls(S, setTriggerValue) {
  const el = S.renderer.domElement, stage = S.el(".czg-stage");
  const pointers = new Map(); let start = null, lastPinch = null;
  const speed = () => 0.0055 * (S.dist - 0.9) / 2.7;
  const down = (e) => { el.setPointerCapture(e.pointerId); pointers.set(e.pointerId, [e.clientX, e.clientY]);
    S.dragging = true; S.vel.x = S.vel.y = 0; start = { x: e.clientX, y: e.clientY, t: Date.now(), moved: 0 }; };
  const move = (e) => {
    if (!pointers.has(e.pointerId)) return;
    const prev = pointers.get(e.pointerId); pointers.set(e.pointerId, [e.clientX, e.clientY]);
    if (pointers.size === 2) {
      const [a, b] = [...pointers.values()]; const d = Math.hypot(a[0] - b[0], a[1] - b[1]);
      if (lastPinch) zoom(S, lastPinch / d); lastPinch = d; return;
    }
    const dx = e.clientX - prev[0], dy = e.clientY - prev[1];
    if (start) start.moved += Math.abs(dx) + Math.abs(dy);
    S.rot.y += dx * speed(); S.rot.x += dy * speed();
    S.vel.y = dx * speed(); S.vel.x = dy * speed();
    requestRender(S);
  };
  const up = (e) => {
    pointers.delete(e.pointerId); if (pointers.size < 2) lastPinch = null;
    if (pointers.size === 0) { S.dragging = false; requestRender(S); }
    if (start && start.moved < 6 && Date.now() - start.t < 600) pick(S, e, setTriggerValue);
    start = null;
  };
  const wheel = (e) => { e.preventDefault(); zoom(S, e.deltaY > 0 ? 1.08 : 1 / 1.08); };
  const key = (e) => {
    const k = e.key, st = 0.08;
    if (k === "ArrowLeft") S.rot.y += st; else if (k === "ArrowRight") S.rot.y -= st;
    else if (k === "ArrowUp") S.rot.x += st; else if (k === "ArrowDown") S.rot.x -= st;
    else if (k === "+" || k === "=") return zoom(S, 0.85); else if (k === "-" || k === "_") return zoom(S, 1 / 0.85);
    else return;
    e.preventDefault(); requestRender(S);
  };
  el.addEventListener("pointerdown", down); el.addEventListener("pointermove", move);
  el.addEventListener("pointerup", up); el.addEventListener("pointercancel", up);
  el.addEventListener("wheel", wheel, { passive: false }); stage.addEventListener("keydown", key);
  el.style.touchAction = "none"; el.style.cursor = "grab";
  S.unbind = () => { el.removeEventListener("pointerdown", down); el.removeEventListener("pointermove", move);
    el.removeEventListener("pointerup", up); el.removeEventListener("pointercancel", up);
    el.removeEventListener("wheel", wheel); stage.removeEventListener("keydown", key); };
}

function pick(S, e, setTriggerValue) {
  const THREE = S.THREE, rect = S.renderer.domElement.getBoundingClientRect();
  const ndc = new THREE.Vector2(((e.clientX - rect.left) / rect.width) * 2 - 1, -((e.clientY - rect.top) / rect.height) * 2 + 1);
  const ray = new THREE.Raycaster(); ray.setFromCamera(ndc, S.camera);
  const hit = ray.intersectObject(S.earthMesh, false)[0];
  if (!hit) return;
  const local = S.globe.worldToLocal(hit.point.clone());
  const { lat, lon } = toLatLon(local);
  S.picked = { lat: +lat.toFixed(3), lon: +lon.toFixed(3) };
  S.pickMarker.position.copy(toVec(THREE, S.picked.lat, S.picked.lon, 1.012)); S.pickMarker.visible = true;
  readout(S); requestRender(S);
  setTriggerValue("picked", { lat: S.picked.lat, lon: S.picked.lon, t: Date.now() });
}

// --- Data ---
function applyData(S) {
  const d = S.data;
  S.units = d.units || { temperature: "°C" };
  if (d.city) {
    S.cityMarker.position.copy(toVec(S.THREE, d.city.lat, d.city.lon, 1.012)); S.cityMarker.visible = true;
    if (!S.centered) { S.centered = true; faceLonLat(S, d.city.lon, d.city.lat); }
  } else S.cityMarker.visible = false;
  S.el('[data-act="city"]').disabled = !d.city;
  legend(S); readout(S); requestRender(S);
}

async function loadManifest(S) {
  try {
    const r = await fetch(new URL(S.data.manifest, document.baseURI).href + "?t=" + Date.now(), { cache: "no-store" });
    if (r.status === 404) { S.status("Preparing global forecast data… the Earth is shown meanwhile."); return; }
    if (!r.ok) throw new Error("HTTP " + r.status);
    const m = await r.json();
    const first = !S.manifest; const runChanged = S.manifest && m.run_id !== S.manifest.run_id;
    S.manifest = m; S.frames = m.frames.filter((f) => f.ready);
    if (!S.frames.length) { S.status("Preparing global forecast data…"); return; }
    if (runChanged) S.cache.clear();
    S.el(".czg-slider").max = String(S.frames.length - 1);
    S.status(m.status === "complete" ? "" : `Preparing forecast steps: ${S.frames.length} of ${m.frames.length} ready…`, m.status === "complete" ? "" : "info");
    S.el(".czg-attr").title = S.el(".czg-attr").textContent = `${m.attribution} · ${m.grid.resolution} grid · run ${m.run_utc.replace("T", " ")} · Earth: NASA Blue Marble (GIBS) · Rendering: three.js`;
    if (first || runChanged) { S.frameIdx = Math.min(S.frameIdx, S.frames.length - 1); await paint(S); }
  } catch (e) {
    S.status("Forecast data couldn't be loaded (" + e.message + "). The Earth is still shown; retrying shortly.", "error");
  }
}

async function getField(S, fhr, v, signal) {
  const key = `${S.manifest.run_id}/${fhr}/${v}`;
  if (S.cache.has(key)) { const f = S.cache.get(key); S.cache.delete(key); S.cache.set(key, f); return f; }
  const url = `${S.manifest.base_url}/f${String(fhr).padStart(3, "0")}_${v}.bin`;
  const r = await fetch(new URL(url, document.baseURI), { signal });
  if (r.status === 404) return null;
  if (!r.ok) throw new Error("HTTP " + r.status);
  const q = new Uint16Array(await r.arrayBuffer()), enc = S.manifest.encoding[v], miss = S.manifest.missing;
  const f = new Float32Array(q.length);
  for (let i = 0; i < q.length; i++) f[i] = q[i] === miss ? NaN : enc.offset + q[i] * enc.scale;
  S.cache.set(key, f); while (S.cache.size > CACHE_MAX) S.cache.delete(S.cache.keys().next().value);
  return f;
}

function sampler(grid) {
  return (lat, lon) => {
    const y = 90 - lat, x = ((lon % 360) + 360) % 360;
    const y0 = Math.floor(y), x0 = Math.floor(x), fy = y - y0, fx = x - x0, y1 = Math.min(y0 + 1, 180), x1 = (x0 + 1) % 360;
    const a = grid[y0 * 360 + x0], b = grid[y0 * 360 + x1], c = grid[y1 * 360 + x0], d = grid[y1 * 360 + x1];
    return (a * (1 - fx) + b * fx) * (1 - fy) + (c * (1 - fx) + d * fx) * fy;
  };
}

async function paint(S) {
  if (!S.frames || !S.frames.length) return;
  const ctx = S.overlayCanvas.getContext("2d"), W = S.overlayCanvas.width, H = S.overlayCanvas.height;
  if (S.layer === "none") { ctx.clearRect(0, 0, W, H); S.overlayTex.needsUpdate = true; S.field = null; readout(S); requestRender(S); return; }
  const fr = S.frames[S.frameIdx];
  if (S.abort) S.abort.abort();
  const ac = new AbortController(); S.abort = ac; const token = (S.token = (S.token || 0) + 1);
  let f;
  try { f = await getField(S, fr.fhr, "t2m", ac.signal); }
  catch (e) { if (e.name !== "AbortError") S.status("This forecast step couldn't be loaded (" + e.message + ").", "error"); return; }
  if (token !== S.token) return;
  if (!f) { S.status("Temperature isn't available for this step.", "warn"); return; }
  S.field = f;
  const s = sampler(f), ramp = makeRamp(TEMP_STOPS), img = ctx.createImageData(W, H), px = img.data;
  for (let y = 0; y < H; y++) {
    const lat = 90 - (y + 0.5) * 180 / H;
    for (let x = 0; x < W; x++) {
      const v = s(lat, -180 + (x + 0.5) * 360 / W), i = (y * W + x) * 4;
      if (!isFinite(v)) { px[i + 3] = 0; continue; }
      const c = ramp(v); px[i] = c[0]; px[i + 1] = c[1]; px[i + 2] = c[2]; px[i + 3] = 255;
    }
  }
  ctx.putImageData(img, 0, 0); S.overlayTex.needsUpdate = true;
  readout(S); requestRender(S);
}

function setFrame(S, idx) {
  if (!S.frames || !S.frames.length) return;
  S.frameIdx = Math.max(0, Math.min(idx, S.frames.length - 1));
  S.el(".czg-slider").value = String(S.frameIdx);
  const f = S.frames[S.frameIdx], t = new Date(f.valid_utc);
  const local = t.toLocaleString(undefined, { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
  S.el(".czg-tl-label").innerHTML = `<b>${local}</b> <span class="mut">(your time) · +${f.fhr} h from the ${S.manifest.run_utc.slice(11, 16)} UTC GFS run · forecast${f.fhr === 0 ? " (analysis)" : ""}</span>`;
  S.el(".czg-slider").setAttribute("aria-valuetext", `${local}, plus ${f.fhr} hours`);
  layout(S);
  paint(S);
}
function goNow(S) {
  if (!S.frames || !S.frames.length) return;
  const now = Date.now(); let best = 0, bd = Infinity;
  S.frames.forEach((f, i) => { const d = Math.abs(Date.parse(f.valid_utc) - now); if (d < bd) { bd = d; best = i; } });
  setFrame(S, best);
}
function setLayer(S, key) {
  S.layer = key;
  for (const b of S.root.querySelectorAll(".czg-layer")) b.setAttribute("aria-pressed", String(b.dataset.layer === key));
  legend(S); paint(S);
}

function legend(S) {
  const el = S.el(".czg-legend"), u = (S.units && S.units.temperature) || "°C";
  if (S.layer === "none") { el.innerHTML = `<div class="lg-h"><b>Earth only</b></div><div class="note">NASA Blue Marble: a cloud-free composite, not current conditions.</div>`; return; }
  const g = TEMP_STOPS.map(([v, c], i) => `${c} ${Math.round(i / (TEMP_STOPS.length - 1) * 100)}%`).join(",");
  const ticks = TEMP_STOPS.filter((_, i) => i % 2 === 0).map(([v]) => `<span>${fmtTemp(v, u).toFixed(0)}</span>`).join("");
  el.innerHTML = `<div class="lg-h"><span class="ico" aria-hidden="true">thermostat</span><b>Temperature at 2 m</b><span class="kind">GFS forecast</span></div>
    <div class="bar" style="background:linear-gradient(90deg,${g})"></div><div class="ticks">${ticks}</div><div class="unit">${u}</div>`;
}

function readout(S) {
  const el = S.el(".czg-readout");
  if (!S.picked) { el.innerHTML = `<span class="mut">Click the globe to read the value at a point.</span>`; return; }
  const { lat, lon } = S.picked, u = (S.units && S.units.temperature) || "°C";
  const coord = `${Math.abs(lat).toFixed(2)}°${lat >= 0 ? "N" : "S"}, ${Math.abs(lon).toFixed(2)}°${lon >= 0 ? "E" : "W"}`;
  let val = "";
  if (S.layer === "temperature") {
    const v = S.field ? sampler(S.field)(lat, lon) : NaN;
    val = isFinite(v) ? ` · ${fmtTemp(v, u).toFixed(1)} ${u}` : " · Not available";
  }
  el.innerHTML = `<b>${coord}</b>${val}<div class="mut">GFS 1° grid value (interpolated) at the selected step</div>`;
}
"""

CSS = r"""
.czg,.czg *{box-sizing:border-box}
.czg{position:relative;height:clamp(460px,calc(100vh - 220px),1000px);border-radius:22px;overflow:hidden;
  background:radial-gradient(ellipse at 50% 45%,#0b1838 0%,#060d22 45%,#040914 100%);border:1px solid rgba(140,160,195,.18);
  box-shadow:0 2px 8px rgba(2,6,18,.4),0 24px 60px rgba(2,6,18,.45);color:#eaf0fa;font-family:inherit}
.czg-stage{position:absolute;inset:0}
.czg-stage:focus-visible{outline:3px solid #4aa3ff;outline-offset:-3px}
.czg-stage canvas{display:block}
.ico{font-family:"Material Symbols Rounded";font-weight:normal;font-style:normal;font-size:20px;line-height:1;
  display:inline-block;-webkit-font-smoothing:antialiased;font-feature-settings:"liga";vertical-align:middle}
.mut{opacity:.72;font-weight:400}
.czg-top{position:absolute;top:10px;left:10px;right:56px;z-index:5;display:flex;pointer-events:none}
.czg-layers{display:flex;gap:6px;padding:4px;border-radius:14px;pointer-events:auto;background:rgba(8,16,38,.82);
  border:1px solid rgba(120,150,200,.25)}
.czg-layer{display:flex;align-items:center;gap:6px;white-space:nowrap;border:1px solid transparent;border-radius:10px;
  padding:6px 10px;background:transparent;color:#d6e2f5;font:inherit;font-size:.85rem;cursor:pointer;
  transition:background .15s ease,border-color .15s ease}
.czg-layer:hover{background:rgba(74,163,255,.14)}
.czg-layer[aria-pressed="true"]{background:linear-gradient(180deg,rgba(74,163,255,.32),rgba(74,163,255,.14));
  border-color:rgba(74,163,255,.8);color:#fff;box-shadow:0 0 0 1px rgba(74,163,255,.35),0 0 18px rgba(74,163,255,.28),inset 0 -2px 0 #f2a93b}
.czg-layer .ico{font-size:18px}
.czg button:focus-visible,.czg input:focus-visible{outline:3px solid #4aa3ff;outline-offset:2px}
.czg-side{position:absolute;right:10px;top:10px;z-index:5;display:flex;flex-direction:column;gap:6px}
.czg-btn{display:inline-flex;align-items:center;justify-content:center;min-width:36px;height:36px;border-radius:10px;
  border:1px solid rgba(120,150,200,.3);background:rgba(8,16,38,.85);color:#e9f1fc;cursor:pointer;font:inherit;padding:0 8px}
.czg-btn:hover{background:rgba(74,163,255,.2)}
.czg-btn:disabled{opacity:.4;cursor:not-allowed}
.czg-btn.now{font-size:.8rem;font-weight:600}
.czg-legend{position:absolute;left:10px;bottom:104px;z-index:5;width:min(300px,calc(100% - 20px));padding:10px 12px;border-radius:12px;
  background:rgba(8,16,38,.86);border:1px solid rgba(120,150,200,.25);font-size:.78rem}
.lg-h{display:flex;align-items:center;gap:6px;white-space:nowrap;margin-bottom:8px;font-family:Outfit,Inter,sans-serif;font-size:.92rem}
.lg-h .kind{margin-left:auto;font-size:.7rem;padding:1px 7px;border-radius:999px;border:1px solid rgba(74,163,255,.6);color:#9ccaff}
.czg-legend .bar{height:9px;border-radius:5px;border:1px solid rgba(255,255,255,.15)}
.czg-legend .ticks{display:flex;justify-content:space-between;margin-top:3px;opacity:.85;font-variant-numeric:tabular-nums}
.czg-legend .unit{text-align:right;opacity:.7}
.czg-legend .note{opacity:.75;margin-top:2px}
.czg-readout{position:absolute;right:10px;bottom:104px;z-index:5;max-width:min(320px,calc(100% - 20px));padding:8px 12px;border-radius:12px;
  background:rgba(8,16,38,.86);border:1px solid rgba(120,150,200,.25);font-size:.8rem}
.czg-time{position:absolute;left:10px;right:10px;bottom:22px;z-index:5;display:flex;flex-wrap:wrap;align-items:center;gap:6px;
  padding:8px 10px;border-radius:14px;background:rgba(8,16,38,.9);border:1px solid rgba(120,150,200,.25)}
.czg-slider{flex:1;min-width:80px;accent-color:#f2a93b;height:24px;cursor:pointer}
.czg-tl-label{flex-basis:100%;font-size:.78rem;font-variant-numeric:tabular-nums}
.czg-tl-label b{font-family:Outfit,Inter,sans-serif;font-size:1rem;font-weight:500}
.czg-status{position:absolute;left:50%;top:64px;transform:translateX(-50%);z-index:6;max-width:calc(100% - 40px);padding:8px 14px;
  border-radius:999px;background:rgba(8,16,38,.92);border:1px solid rgba(74,163,255,.5);font-size:.82rem;display:none;text-align:center}
.czg-status.show{display:block}
.czg-status.error{border-color:#e2566a}
.czg-status.warn{border-color:#e39a1c}
.czg-attr{position:absolute;right:8px;bottom:3px;z-index:5;font-size:.66rem;opacity:.75;max-width:calc(100% - 16px);
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
@media (max-width:640px){
  .czg{height:clamp(420px,calc(100vh - 170px),760px);border-radius:14px}
  .czg-layer .t{display:none}
  .czg-readout{display:none}
  .czg-legend{bottom:118px;width:calc(100% - 20px)}
  .czg-legend .note{display:none}
}
@media (prefers-reduced-motion:reduce){.czg-layer{transition:none}}
"""

_component = None


def globe(data: dict, key: str = "cloudzy_globe"):
    """Mount the globe. `.picked` → {lat, lon} after a click; `.fallback` → {reason} if 3D can't run."""
    global _component
    import streamlit as st
    if _component is None:
        _component = st.components.v2.component("cloudzy_globe", js=JS, css=CSS)
    return _component(key=key, data=data, on_picked_change=lambda: None, on_fallback_change=lambda: None)
