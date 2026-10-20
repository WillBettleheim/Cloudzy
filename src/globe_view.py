"""The Weather Explorer's 3D globe: a Streamlit custom component (components v2) on three.js.

What it draws
- Earth: NASA Blue Marble (shaded relief and bathymetry) on the day side and NASA VIIRS Black Marble 2016
  (city lights) on the night side, both public-domain composites from NASA GIBS, not current conditions.
  The sun is placed at its real position for the selected forecast time (a standard low-precision solar
  formula, well under 1° of error), so the day/night terminator is real. Lighting can be switched off
  for even illumination. The blue rim is a visual atmosphere effect, not a physical scattering model.
- Layers from the same GFS frames the 2D map uses (src.explorer_data), with the same colour scales,
  thresholds and units: 2 m temperature, 10 m wind (speed colour plus particles moved by the real u/v
  vectors), precipitation rate with the GFS rain/snow/mixed type, and total cloud cover. The 1° grid is
  sampled bilinearly, exactly as on the map; nothing is invented between real grid values. Data layers
  are only slightly dimmed at night so they stay readable; cloud cover is dimmed more, as clouds are.

Interaction
- Drag to rotate, wheel or pinch to zoom, arrow keys and +/- on the focused globe; gentle inertia,
  none under reduced motion. Rendering happens only while something changes or wind particles are
  moving on screen; particles pause when the globe is scrolled away or the tab is hidden, and become
  static streamlines under reduced motion (or with the motion button).
- Click selects a point: a ray finds the latitude/longitude on the sphere and Python receives it
  (the page then shows the same point panel as the 2D map).

Reliability
- No WebGL, a lost WebGL context or a failed day texture → the component reports `fallback` and the page
  switches to the 2D map. A failed night texture only loses the city lights. All GPU resources and
  listeners are released when the component goes away.
"""

from __future__ import annotations

JS = r"""
const THREE_URL = "app/static/vendor/three/three.module.js";
const EARTH_URL = "app/static/vendor/earth/blue-marble-4096.jpg";
const NIGHT_URL = "app/static/vendor/earth/black-marble-4096.jpg";
const TEMP_STOPS = [[-40,"#2a1b5f"],[-25,"#3c3fae"],[-10,"#3f7fd6"],[0,"#62b6e0"],[8,"#8fd3c0"],[16,"#e8e08c"],[24,"#f5a54a"],[32,"#e5582f"],[42,"#a31d3a"]];
// Same scales as the 2D map (src.explorer_view), so a colour means the same value in both views.
const WIND_STOPS = [[0,"#2c4a80"],[3,"#3480c4"],[6,"#36b0c9"],[10,"#5cc79a"],[14,"#e6d36a"],[20,"#f08c46"],[28,"#d6455a"],[40,"#9b2f7f"]];
const RAIN_STOPS = [[0.1,"#5ac8fa"],[1,"#2f8cf0"],[3,"#3b57d6"],[8,"#7b3fd1"],[20,"#c42f8c"],[50,"#f04b4b"]];
const SNOW_STOPS = [[0.1,"#dff1ff"],[1,"#bcd7ff"],[3,"#9fb2ff"],[8,"#b28cff"]];
const MIXED_STOPS = [[0.1,"#ffb0d0"],[1,"#ff7fb4"],[5,"#e84d8a"]];
const LAYERS = {
  temperature: { label: "Temperature", icon: "thermostat", vars: ["t2m"], night: 0.2 },
  wind: { label: "Wind", icon: "air", vars: ["u10", "v10"], night: 0.2 },
  precipitation: { label: "Precipitation", icon: "rainy", vars: ["prate", "ptype"], optional: ["ptype"], night: 0.2 },
  clouds: { label: "Cloud cover", icon: "cloud", vars: ["tcc"], night: 0.65 },
  none: { label: "Earth only", icon: "public", vars: [], night: 0 },
};
const CACHE_MAX = 24;
const WIND_K = 0.03;          // degrees moved per frame per m/s (visual speed of the particles)

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
const fmtWind = (ms, u) => (u === "km/h" ? ms * 3.6 : u === "mph" ? ms * 2.23694 : u === "kn" ? ms * 1.94384 : ms);
const fmtRate = (mmh, u) => (u === "in" ? mmh / 25.4 : mmh);

// Subsolar point (where the sun is overhead) for a date: the standard low-precision solar position
// (Astronomical Almanac / USNO), accurate to about 0.01° in declination for this century.
function subsolar(date) {
  const R = Math.PI / 180, d = date.getTime() / 86400000 - 10957.5;     // days since J2000.0
  const g = (357.529 + 0.98560028 * d) * R, q = 280.459 + 0.98564736 * d;
  const L = (q + 1.915 * Math.sin(g) + 0.020 * Math.sin(2 * g)) * R, e = (23.439 - 0.00000036 * d) * R;
  const dec = Math.asin(Math.sin(e) * Math.sin(L)) / R;
  const ra = Math.atan2(Math.cos(e) * Math.sin(L), Math.cos(L)) / R;
  const gmst = 280.46061837 + 360.98564736629 * d;
  return { lat: dec, lon: ((((ra - gmst) % 360) + 540) % 360) - 180 };
}

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
          ${Object.entries(LAYERS).map(([k, l]) => `<button type="button" class="czg-layer" data-layer="${k}" aria-pressed="${k === "temperature"}" aria-label="${l.label}" title="${l.label}"><span class="ico" aria-hidden="true">${l.icon}</span><span class="t">${l.label}</span></button>`).join("")}
        </div>
      </div>
      <div class="czg-side">
        <button type="button" class="czg-btn" data-act="zoomin" aria-label="Zoom in"><span class="ico">add</span></button>
        <button type="button" class="czg-btn" data-act="zoomout" aria-label="Zoom out"><span class="ico">remove</span></button>
        <button type="button" class="czg-btn" data-act="reset" aria-label="Reset view" title="Reset view"><span class="ico">public</span></button>
        <button type="button" class="czg-btn" data-act="city" aria-label="Turn to the selected place" title="Turn to the selected place"><span class="ico">my_location</span></button>
        <button type="button" class="czg-btn" data-act="sun" aria-pressed="true" aria-label="Day and night lighting" title="Day and night lighting (sun position at the selected time)"><span class="ico">routine</span></button>
        <button type="button" class="czg-btn" data-act="motion" aria-pressed="true" aria-label="Wind motion" title="Animate wind particles" hidden><span class="ico">animation</span></button>
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

  // Globe group: rotated by the controls. The sun direction lives in the globe's own frame (sunL) and is
  // turned into world space every frame (sunW), which all lit materials share.
  const globe = new THREE.Group(); scene.add(globe); S.globe = globe;
  S.sunL = new THREE.Vector3(1, 0, 0); S.sunW = new THREE.Vector3(1, 0, 0);
  S.uLit = { value: 1 };
  const loader = new THREE.TextureLoader();
  const load = (url) => new Promise((res, rej) => loader.load(new URL(url, document.baseURI).href, res, undefined, rej));
  const [dayTex, nightTex] = await Promise.all([
    load(EARTH_URL).catch(() => { throw new Error("the Earth texture didn't load"); }),
    load(NIGHT_URL).catch(() => null),                    // optional: without it the night side is just dark
  ]);
  const aniso = Math.min(8, renderer.capabilities.getMaxAnisotropy());
  for (const t of [dayTex, nightTex]) if (t) { t.colorSpace = THREE.SRGBColorSpace; t.anisotropy = aniso; }
  S.nightTex = nightTex || new THREE.DataTexture(new Uint8Array([3, 4, 10, 255]), 1, 1);
  S.nightTex.needsUpdate = true; S.dayTex = dayTex; S.hasLights = !!nightTex;
  const VS = `varying vec2 vUv; varying vec3 vN; varying vec3 vW;
    void main(){ vUv = uv; vN = normalize(mat3(modelMatrix) * normal); vec4 w = modelMatrix * vec4(position, 1.0); vW = w.xyz;
      gl_Position = projectionMatrix * viewMatrix * w; }`;
  const earthMat = new THREE.ShaderMaterial({
    uniforms: { dayMap: { value: dayTex }, nightMap: { value: S.nightTex }, sunDir: { value: S.sunW }, lit: S.uLit },
    vertexShader: VS,
    fragmentShader: `uniform sampler2D dayMap; uniform sampler2D nightMap; uniform vec3 sunDir; uniform float lit;
      varying vec2 vUv; varying vec3 vN; varying vec3 vW;
      void main(){
        vec3 n = normalize(vN), day = texture2D(dayMap, vUv).rgb, night = texture2D(nightMap, vUv).rgb;
        float d = dot(n, normalize(sunDir)), t = smoothstep(-0.10, 0.12, d);
        vec3 sunlit = mix(night * 1.5 + day * 0.035, day * (0.22 + 0.88 * max(d, 0.0)), t);
        vec3 even = day * (0.62 + 0.42 * max(dot(n, normalize(cameraPosition - vW)), 0.0));
        gl_FragColor = vec4(mix(even, sunlit, lit), 1.0);
        #include <tonemapping_fragment>
        #include <colorspace_fragment>
      }`,
  });
  const earthGeo = new THREE.SphereGeometry(1, 128, 64);
  S.earthMesh = new THREE.Mesh(earthGeo, earthMat); globe.add(S.earthMesh);

  // Weather layer sphere (texture painted from the GFS grid), slightly dimmed on the night side.
  S.overlayCanvas = document.createElement("canvas"); S.overlayCanvas.width = 720; S.overlayCanvas.height = 360;
  S.overlayTex = new THREE.CanvasTexture(S.overlayCanvas);
  S.overlayTex.colorSpace = THREE.SRGBColorSpace;
  S.uNight = { value: LAYERS.temperature.night };
  const overlay = new THREE.Mesh(new THREE.SphereGeometry(1.004, 128, 64), new THREE.ShaderMaterial({
    transparent: true, depthWrite: false,
    uniforms: { map: { value: S.overlayTex }, sunDir: { value: S.sunW }, lit: S.uLit, nightDim: S.uNight },
    vertexShader: VS,
    fragmentShader: `uniform sampler2D map; uniform vec3 sunDir; uniform float lit; uniform float nightDim;
      varying vec2 vUv; varying vec3 vN; varying vec3 vW;
      void main(){
        vec4 c = texture2D(map, vUv);
        float t = smoothstep(-0.10, 0.12, dot(normalize(vN), normalize(sunDir)));
        float k = mix(1.0, mix(1.0 - nightDim, 1.0, t), lit);
        gl_FragColor = vec4(c.rgb * k, c.a);
        #include <colorspace_fragment>
      }`,
  }));
  overlay.renderOrder = 1; globe.add(overlay); S.overlay = overlay;

  // Atmosphere rim (fresnel glow on a back-facing shell), brighter on the day side when lit:
  // a visual approximation, not physical scattering.
  const atmo = new THREE.Mesh(new THREE.SphereGeometry(1.07, 96, 48), new THREE.ShaderMaterial({
    side: THREE.BackSide, transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
    uniforms: { sunDir: { value: S.sunW }, lit: S.uLit },
    vertexShader: "varying vec3 vN; varying vec3 vP; varying vec3 vWN; void main(){ vN = normalize(normalMatrix*normal); vWN = normalize(mat3(modelMatrix)*normal); vec4 mv = modelViewMatrix*vec4(position,1.0); vP = mv.xyz; gl_Position = projectionMatrix*mv; }",
    fragmentShader: "uniform vec3 sunDir; uniform float lit; varying vec3 vN; varying vec3 vP; varying vec3 vWN; void main(){ float f = pow(clamp(1.0 - abs(dot(normalize(-vP), vN)), 0.0, 1.0), 2.4); float day = mix(1.0, 0.3 + 0.9 * smoothstep(-0.35, 0.45, dot(normalize(vWN), normalize(sunDir))), lit); gl_FragColor = vec4(0.29,0.60,1.0,1.0) * f * 1.35 * day; }",
  }));
  scene.add(atmo); S.atmo = atmo;
  scene.add(camera);

  // Markers.
  S.cityMarker = new THREE.Mesh(new THREE.SphereGeometry(0.012, 16, 12), new THREE.MeshBasicMaterial({ color: 0xf2a93b }));
  S.pickMarker = new THREE.Mesh(new THREE.SphereGeometry(0.010, 16, 12), new THREE.MeshBasicMaterial({ color: 0xffffff }));
  S.cityMarker.visible = S.pickMarker.visible = false;
  globe.add(S.cityMarker, S.pickMarker);
  buildParticles(S);

  // Orientation: rotX (tilt) and rotY (spin); the front of the globe faces the camera along +z.
  S.rot = { x: 0.35, y: 0 }; S.vel = { x: 0, y: 0 };
  faceLonLat(S, 10, 20);

  layout(S); setupControls(S, setTriggerValue);
  S.ro = new ResizeObserver(() => { layout(S); requestRender(S); }); S.ro.observe(stage);
  // Pause the wind animation while the globe is off screen or the tab is hidden.
  S.inView = true; S.motion = !S.reduce;
  S.el('[data-act="motion"]').setAttribute("aria-pressed", String(S.motion));
  S.io = new IntersectionObserver((es) => { S.inView = es[0].isIntersecting; requestRender(S); }); S.io.observe(stage);
  S.onVis = () => requestRender(S); document.addEventListener("visibilitychange", S.onVis);
  setSun(S, new Date());

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
    if (a === "sun") { S.uLit.value = S.uLit.value ? 0 : 1; b.setAttribute("aria-pressed", String(!!S.uLit.value)); timeLabel(S); requestRender(S); }
    if (a === "motion") { S.motion = !S.motion; b.setAttribute("aria-pressed", String(S.motion)); startWind(S); legend(S); }
  });

  S.cache = new Map(); S.layer = "temperature"; S.frameIdx = 0; S.fields = {}; S.mstatus = ["", ""]; S.ready = true;
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
  if (S.io) S.io.disconnect();
  if (S.onVis) document.removeEventListener("visibilitychange", S.onVis);
  for (const t of [S.dayTex, S.nightTex, S.overlayTex]) if (t) t.dispose();
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
  S.sunW.copy(S.sunL).applyQuaternion(S.globe.quaternion);
  S.camera.position.set(0, 0, S.dist); S.camera.lookAt(0, 0, 0);
  const animate = windAnimating(S);
  if (animate) stepParticles(S);
  S.renderer.render(S.scene, S.camera);
  if (moving || animate) requestRender(S);
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
    S.mstatus = m.status === "complete" ? ["", ""] : [`Preparing forecast steps: ${S.frames.length} of ${m.frames.length} ready…`, "info"];
    S.status(...S.mstatus);
    S.el(".czg-attr").title = S.el(".czg-attr").textContent = `${m.attribution} · ${m.grid.resolution} grid · run ${m.run_utc.replace("T", " ")} · Earth: NASA Blue Marble and Black Marble (GIBS) · Rendering: three.js`;
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

function clearOverlay(S) {
  const ctx = S.overlayCanvas.getContext("2d"); ctx.clearRect(0, 0, S.overlayCanvas.width, S.overlayCanvas.height);
  S.overlayTex.needsUpdate = true; S.fields = {}; S.wind.lines.visible = false;
}

async function paint(S) {
  if (!S.frames || !S.frames.length) return;
  const L_ = S.layer, info = LAYERS[L_];
  S.uNight.value = info.night;
  if (L_ === "none") { clearOverlay(S); readout(S); requestRender(S); return; }
  const fr = S.frames[S.frameIdx];
  if (S.abort) S.abort.abort();
  const ac = new AbortController(); S.abort = ac; const token = (S.token = (S.token || 0) + 1);
  let got;
  try { got = await Promise.all(info.vars.map((v) => getField(S, fr.fhr, v, ac.signal))); }
  catch (e) { if (e.name !== "AbortError") S.status("This forecast step couldn't be loaded (" + e.message + ").", "error"); return; }
  if (token !== S.token) return;
  const fields = {}; info.vars.forEach((v, i) => { if (got[i]) fields[v] = got[i]; });
  const missing = info.vars.filter((v) => !fields[v] && !(info.optional || []).includes(v));
  if (missing.length) { clearOverlay(S); S.status(`${info.label} isn't available for this step.`, "warn"); readout(S); requestRender(S); return; }
  S.status(...S.mstatus);
  S.fields = fields;
  const ctx = S.overlayCanvas.getContext("2d"), W = S.overlayCanvas.width, H = S.overlayCanvas.height;
  const img = ctx.createImageData(W, H), px = img.data;
  const draw = (fn) => {
    for (let y = 0; y < H; y++) {
      const lat = 90 - (y + 0.5) * 180 / H;
      for (let x = 0; x < W; x++) {
        const c = fn(lat, -180 + (x + 0.5) * 360 / W); if (!c) continue;
        const i = (y * W + x) * 4; px[i] = c[0]; px[i + 1] = c[1]; px[i + 2] = c[2]; px[i + 3] = c[3];
      }
    }
  };
  if (L_ === "temperature") {
    const s = sampler(fields.t2m), ramp = makeRamp(TEMP_STOPS);
    draw((la, lo) => { const v = s(la, lo); if (!isFinite(v)) return null; const c = ramp(v); return [c[0], c[1], c[2], 170]; });
  } else if (L_ === "wind") {
    const u = sampler(fields.u10), v = sampler(fields.v10), ramp = makeRamp(WIND_STOPS);
    draw((la, lo) => { const a = u(la, lo), b = v(la, lo); if (!isFinite(a) || !isFinite(b)) return null; const c = ramp(Math.hypot(a, b)); return [c[0], c[1], c[2], 120]; });
  } else if (L_ === "precipitation") {
    const rate = sampler(fields.prate), type = fields.ptype ? sampler(fields.ptype) : null;
    const ramps = { 1: makeRamp(RAIN_STOPS), 2: makeRamp(SNOW_STOPS), 3: makeRamp(MIXED_STOPS) };
    draw((la, lo) => {
      const r = rate(la, lo); if (!(r >= 0.1)) return null;          // below 0.1 mm/h is left clear, as on the map
      let t = type ? Math.round(type(la, lo)) : 1; if (!(t >= 1 && t <= 3)) t = 1;
      const c = ramps[t](r); return [c[0], c[1], c[2], Math.min(235, 120 + r * 18)];
    });
  } else if (L_ === "clouds") {
    const s = sampler(fields.tcc);
    draw((la, lo) => { const v = s(la, lo); if (!(v > 5)) return null; return [240, 244, 250, Math.round(v / 100 * 215)]; });
  }
  ctx.putImageData(img, 0, 0); S.overlayTex.needsUpdate = true;
  if (L_ === "wind") startWind(S); else S.wind.lines.visible = false;
  readout(S); requestRender(S);
}

// --- Wind particles: points on the sphere moved by the frame's u/v vectors, drawn as fading trails ---
function buildParticles(S) {
  const THREE = S.THREE, N = window.innerWidth < 640 ? 1800 : 3600, T = 10;
  const pos = new Float32Array(N * (T - 1) * 6), col = new Float32Array(N * (T - 1) * 8);
  const geo = new THREE.BufferGeometry();
  geo.setAttribute("position", new THREE.BufferAttribute(pos, 3).setUsage(THREE.DynamicDrawUsage));
  geo.setAttribute("color", new THREE.BufferAttribute(col, 4).setUsage(THREE.DynamicDrawUsage));
  const lines = new THREE.LineSegments(geo, new THREE.LineBasicMaterial({ vertexColors: true, transparent: true, depthWrite: false }));
  lines.frustumCulled = false; lines.renderOrder = 2; lines.visible = false;
  S.globe.add(lines);
  // Colour lookup by speed (0–60 m/s in 0.5 m/s steps), converted to linear for the GPU.
  const ramp = makeRamp(WIND_STOPS), lut = new Float32Array(121 * 3), c = new THREE.Color();
  for (let i = 0; i <= 120; i++) {
    const v = ramp(i / 2); c.setRGB(Math.min(255, v[0] + 40) / 255, Math.min(255, v[1] + 40) / 255, Math.min(255, v[2] + 40) / 255, THREE.SRGBColorSpace);
    lut[i * 3] = c.r; lut[i * 3 + 1] = c.g; lut[i * 3 + 2] = c.b;
  }
  S.wind = { N, T, pos, col, geo, lines, lut, tx: new Float32Array(N * T * 3),
    lat: new Float32Array(N), lon: new Float32Array(N), age: new Float32Array(N), spd: new Float32Array(N) };
}
function windAnimating(S) {
  return S.layer === "wind" && S.motion && S.wind.lines.visible && S.inView && !document.hidden && !!S.fields.u10;
}
function startWind(S) {
  const W = S.wind;
  if (S.layer !== "wind" || !S.fields.u10) return;
  S.su = sampler(S.fields.u10); S.sv = sampler(S.fields.v10);
  for (let i = 0; i < W.N; i++) respawn(S, i);
  if (!S.motion) for (let i = 0; i < W.N; i++) for (let k = 0; k < W.T - 1; k++) if (!advance(S, i)) break;  // static streamlines
  writeTrails(S); W.lines.visible = true; requestRender(S);
}
function headXYZ(W, i, r) {
  const D = Math.PI / 180, th = (90 - W.lat[i]) * D, ph = (W.lon[i] + 180) * D, b = i * W.T * 3;
  W.tx[b] = -r * Math.cos(ph) * Math.sin(th); W.tx[b + 1] = r * Math.cos(th); W.tx[b + 2] = r * Math.sin(ph) * Math.sin(th);
}
function respawn(S, i) {
  const W = S.wind;
  W.lat[i] = Math.asin(2 * Math.random() - 1) * 180 / Math.PI; W.lon[i] = Math.random() * 360 - 180;
  W.age[i] = 40 + Math.random() * 60; W.spd[i] = 0;
  headXYZ(W, i, 1.008);
  const b = i * W.T * 3; for (let k = 1; k < W.T; k++) { W.tx[b + k * 3] = W.tx[b]; W.tx[b + k * 3 + 1] = W.tx[b + 1]; W.tx[b + k * 3 + 2] = W.tx[b + 2]; }
}
function advance(S, i) {
  const W = S.wind, a = S.su(W.lat[i], W.lon[i]), v = S.sv(W.lat[i], W.lon[i]);
  if (!isFinite(a) || !isFinite(v)) return false;
  const lat = W.lat[i] + v * WIND_K, lon = W.lon[i] + a * WIND_K / Math.max(0.15, Math.cos(W.lat[i] * Math.PI / 180));
  if (Math.abs(lat) > 88) return false;
  W.lat[i] = lat; W.lon[i] = ((lon + 540) % 360) - 180; W.spd[i] = Math.hypot(a, v);
  const b = i * W.T * 3; W.tx.copyWithin(b + 3, b, b + (W.T - 1) * 3);   // shift the trail, newest point first
  headXYZ(W, i, 1.008);
  return true;
}
function stepParticles(S) {
  const W = S.wind;
  for (let i = 0; i < W.N; i++) if (W.age[i]-- <= 0 || !advance(S, i)) respawn(S, i);
  writeTrails(S);
}
function writeTrails(S) {
  const W = S.wind, T = W.T, S1 = T - 1;
  for (let i = 0; i < W.N; i++) {
    const b = i * T * 3, li = Math.min(120, Math.round(W.spd[i] * 2)) * 3;
    const r = W.lut[li], g = W.lut[li + 1], bl = W.lut[li + 2];
    for (let k = 0; k < S1; k++) {
      const o = (i * S1 + k) * 6, oc = (i * S1 + k) * 8;
      W.pos[o] = W.tx[b + k * 3]; W.pos[o + 1] = W.tx[b + k * 3 + 1]; W.pos[o + 2] = W.tx[b + k * 3 + 2];
      W.pos[o + 3] = W.tx[b + k * 3 + 3]; W.pos[o + 4] = W.tx[b + k * 3 + 4]; W.pos[o + 5] = W.tx[b + k * 3 + 5];
      const a0 = 0.95 * (1 - k / S1), a1 = 0.95 * (1 - (k + 1) / S1);
      W.col[oc] = r; W.col[oc + 1] = g; W.col[oc + 2] = bl; W.col[oc + 3] = a0;
      W.col[oc + 4] = r; W.col[oc + 5] = g; W.col[oc + 6] = bl; W.col[oc + 7] = a1;
    }
  }
  W.geo.attributes.position.needsUpdate = true; W.geo.attributes.color.needsUpdate = true;
}

function setSun(S, date) {
  const p = subsolar(date);
  S.sunL.copy(toVec(S.THREE, p.lat, p.lon, 1)); S.sun = p;
  requestRender(S);
}

function setFrame(S, idx) {
  if (!S.frames || !S.frames.length) return;
  S.frameIdx = Math.max(0, Math.min(idx, S.frames.length - 1));
  S.el(".czg-slider").value = String(S.frameIdx);
  setSun(S, new Date(S.frames[S.frameIdx].valid_utc)); readout(S);
  timeLabel(S);
  layout(S);
  paint(S);
}
function timeLabel(S) {
  if (!S.frames || !S.frames.length) return;
  const f = S.frames[S.frameIdx], t = new Date(f.valid_utc);
  const local = t.toLocaleString(undefined, { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
  const sun = S.uLit.value ? " · daylight and night as at this time" : "";
  S.el(".czg-tl-label").innerHTML = `<b>${local}</b> <span class="mut">(your time) · +${f.fhr} h from the ${S.manifest.run_utc.slice(11, 16)} UTC GFS run · forecast${f.fhr === 0 ? " (analysis)" : ""}${sun}</span>`;
  S.el(".czg-slider").setAttribute("aria-valuetext", `${local}, plus ${f.fhr} hours`);
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
  S.el('[data-act="motion"]').hidden = key !== "wind";
  legend(S); layout(S); paint(S);
}

function legend(S) {
  const el = S.el(".czg-legend"), u = S.units || {}, L_ = S.layer;
  const bar = (stops, conv, unit, digits = 0) => {
    const g = stops.map(([v, c], i) => `${c} ${Math.round(i / (stops.length - 1) * 100)}%`).join(",");
    const ticks = stops.filter((_, i) => i % Math.ceil(stops.length / 5) === 0 || i === stops.length - 1)
      .map(([v]) => `<span>${conv(v).toFixed(digits)}</span>`).join("");
    return `<div class="bar" style="background:linear-gradient(90deg,${g})"></div><div class="ticks">${ticks}</div><div class="unit">${unit}</div>`;
  };
  const head = (t) => `<div class="lg-h"><span class="ico" aria-hidden="true">${LAYERS[L_].icon}</span><b>${t}</b><span class="kind">GFS forecast</span></div>`;
  let html;
  if (L_ === "none") html = `<div class="lg-h"><span class="ico" aria-hidden="true">public</span><b>Earth only</b></div><div class="note">NASA Blue Marble by day and Black Marble city lights by night: composites, not current conditions.</div>`;
  else if (L_ === "temperature") html = head("Temperature at 2 m") + bar(TEMP_STOPS, (v) => fmtTemp(v, u.temperature || "°C"), u.temperature || "°C");
  else if (L_ === "wind") html = head("Wind at 10 m") + bar(WIND_STOPS, (v) => fmtWind(v, u.wind || "km/h"), u.wind || "km/h") +
    `<div class="note">${S.motion ? "Particles move with the forecast wind vectors." : "Streamlines follow the forecast wind vectors."}</div>`;
  else if (L_ === "precipitation") {
    const pu = u.precipitation === "in" ? "in/h" : "mm/h", d = u.precipitation === "in" ? 2 : 1;
    html = head("Precipitation rate") + bar(RAIN_STOPS, (v) => fmtRate(v, u.precipitation), pu, d) +
      `<div class="types"><span><i style="background:#2f8cf0"></i>Rain</span><span><i style="background:#bcd7ff"></i>Snow</span><span><i style="background:#ff7fb4"></i>Mixed / freezing</span></div><div class="note">Model forecast, not radar. Below 0.1 mm/h is left clear.</div>`;
  } else if (L_ === "clouds") html = head("Total cloud cover") + `<div class="bar" style="background:linear-gradient(90deg,rgba(240,244,250,0),rgba(240,244,250,.85))"></div><div class="ticks"><span>0</span><span>50</span><span>100</span></div><div class="unit">%</div><div class="note">All cloud levels together; low, mid and high cloud are not split.</div>`;
  el.innerHTML = html;
}

function readout(S) {
  const el = S.el(".czg-readout");
  if (!S.picked) { el.innerHTML = `<span class="mut">Click the globe to read the value at a point.</span>`; return; }
  const { lat, lon } = S.picked, u = S.units || {}, f = S.fields || {};
  const coord = `${Math.abs(lat).toFixed(2)}°${lat >= 0 ? "N" : "S"}, ${Math.abs(lon).toFixed(2)}°${lon >= 0 ? "E" : "W"}`;
  const s = (v) => (f[v] ? sampler(f[v])(lat, lon) : NaN), na = "Not available";
  let val = "";
  if (S.layer === "temperature") { const x = s("t2m"); val = isFinite(x) ? `${fmtTemp(x, u.temperature || "°C").toFixed(1)} ${u.temperature || "°C"}` : na; }
  else if (S.layer === "wind") {
    const a = s("u10"), b = s("v10"), wu = u.wind || "km/h";
    if (isFinite(a) && isFinite(b)) { const from = (Math.atan2(-a, -b) * 180 / Math.PI + 360) % 360; const dirs = ["N","NE","E","SE","S","SW","W","NW"]; val = `${fmtWind(Math.hypot(a, b), wu).toFixed(0)} ${wu} from ${dirs[Math.round(from / 45) % 8]} (${from.toFixed(0)}°)`; } else val = na;
  }
  else if (S.layer === "precipitation") { const x = s("prate"), t = f.ptype ? Math.round(s("ptype")) : NaN; val = isFinite(x) ? `${fmtRate(x, u.precipitation).toFixed(u.precipitation === "in" ? 2 : 1)} ${u.precipitation === "in" ? "in/h" : "mm/h"}${x >= 0.1 && t >= 1 && t <= 3 ? " · " + ["rain", "snow", "mixed/freezing"][t - 1] : ""}` : na; }
  else if (S.layer === "clouds") { const x = s("tcc"); val = isFinite(x) ? `${x.toFixed(0)}% cloud cover` : na; }
  const dn = S.sun ? (dayAt(S, lat, lon) ? "daytime" : "night") + " at this time" : "";
  el.innerHTML = `<b>${coord}</b>${val ? ` · ${val}` : ""}<div class="mut">${S.layer === "none" ? "" : "GFS 1° grid value (interpolated) at the selected step"}${S.layer !== "none" && dn ? " · " : ""}${dn}</div>`;
}
function dayAt(S, lat, lon) {
  // Sun above the horizon: angular distance from the subsolar point under 90°.
  const R = Math.PI / 180, p = S.sun;
  return Math.sin(lat * R) * Math.sin(p.lat * R) + Math.cos(lat * R) * Math.cos(p.lat * R) * Math.cos((lon - p.lon) * R) > 0;
}
"""

CSS = r"""
.czg,.czg *{box-sizing:border-box}
.czg{position:relative;height:clamp(460px,calc(100vh - 220px),1000px);border-radius:22px;overflow:hidden;
  background:radial-gradient(ellipse at 50% 45%,#0b1838 0%,#060d22 45%,#040914 100%);border:1px solid rgba(140,160,195,.18);
  box-shadow:0 2px 8px rgba(2,6,18,.4),0 24px 60px rgba(2,6,18,.45);color:#eaf0fa;font-family:inherit;
  container-type:inline-size}
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
.czg-btn[aria-pressed="true"]{border-color:rgba(242,169,59,.85);color:#ffd58f;box-shadow:inset 0 0 0 1px rgba(242,169,59,.35)}
.czg-btn[hidden]{display:none}
.czg-legend .types{display:flex;flex-wrap:wrap;gap:10px;margin-top:6px}
.czg-legend .types i{display:inline-block;width:10px;height:10px;border-radius:3px;margin-right:4px;vertical-align:-1px}
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
/* Layer names need about 760px; narrower frames show icons only (names stay in title and the legend). */
@container (max-width:820px){.czg-layer .t{display:none}}
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
