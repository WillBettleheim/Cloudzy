"""The Weather Explorer's 3D globe: a Streamlit custom component (components v2) on three.js.

What it draws
- Earth: NASA Blue Marble (shaded relief and bathymetry) on the day side and NASA VIIRS Black Marble 2016
  (city lights) on the night side, both public-domain composites from NASA GIBS, not current conditions.
  The sun is placed at its real position for the selected forecast time (a standard low-precision solar
  formula, well under 1° of error), so the day/night terminator is real. Lighting can be switched off
  for even illumination. The blue rim is a visual atmosphere effect, not a physical scattering model, and
  the background stars are decorative (not a star catalogue).
- Layers from the same GFS frames the 2D map uses (src.explorer_data), with the same colour scales,
  thresholds and units: temperature, wind (speed colour plus particles moved by the real 10 m u/v
  vectors), precipitation with the GFS rain/snow/mixed type, cloud cover, pressure with isobars traced on
  the 1° grid, gusts, humidity, CAPE, snow depth, and GFS-Wave significant wave height. The 1° grid is
  sampled bilinearly, exactly as on the map; nothing is invented between real grid values. Layers that a
  run or step doesn't have are marked unavailable, never filled in.

Interaction
- Drag to rotate, wheel or pinch to zoom, arrow keys, +/- and Home on the focused globe; buttons for zoom,
  reset, the selected place and fullscreen. A place chosen with the page's search (`data.focus`) is flown
  to and selected. Click or tap selects a point and Python receives it.
- Essential layers sit in a compact toolbar; every layer, display options and the layer's source are in
  a collapsible panel (a bottom sheet on phones). The legend collapses to its title.
- Timeline: play/pause, previous/next, a slider over the run's real valid times, and "Now"; the label
  shows the local time zone and UTC. Playback stops at the last available step.
- Rendering happens only while something changes, the camera flies, playback runs or wind particles
  move on screen. Particles and playback pause when the globe is off screen or the tab is hidden;
  under reduced motion there are no camera flights or particles (static streamlines instead).

Reliability
- No WebGL, a lost WebGL context or a failed day texture → the component reports `fallback` and the page
  switches to the 2D map. A failed night texture only loses the city lights. All GPU resources, timers
  and listeners are released when the component goes away.
"""

from __future__ import annotations

JS = r"""
const THREE_URL = "app/static/vendor/three/three.module.js";
const EARTH_URL = "app/static/vendor/earth/blue-marble-4096.jpg";
const NIGHT_URL = "app/static/vendor/earth/black-marble-4096.jpg";
const GEO_URL = "app/static/vendor/geo";           // Natural Earth boundaries, labels and places (src.geo)
const STATE_PPD = 10;                               // pixels per degree from which states/provinces are explored
const MIN_REGION_CELLS = 3;                         // fewer 1° cells than this → no regional statistics
// Same scales as the 2D map (src.explorer_view), so a colour means the same value in both views.
const TEMP_STOPS = [[-40,"#2a1b5f"],[-25,"#3c3fae"],[-10,"#3f7fd6"],[0,"#62b6e0"],[8,"#8fd3c0"],[16,"#e8e08c"],[24,"#f5a54a"],[32,"#e5582f"],[42,"#a31d3a"]];
const WIND_STOPS = [[0,"#2c4a80"],[3,"#3480c4"],[6,"#36b0c9"],[10,"#5cc79a"],[14,"#e6d36a"],[20,"#f08c46"],[28,"#d6455a"],[40,"#9b2f7f"]];
const GUST_STOPS = [[0,"#2c4a80"],[5,"#3480c4"],[10,"#36b0c9"],[15,"#5cc79a"],[20,"#e6d36a"],[28,"#f08c46"],[36,"#d6455a"],[50,"#9b2f7f"]];
const HUM_STOPS = [[0,"#8a5a3c"],[25,"#c79a5b"],[50,"#d9d6a0"],[75,"#6cc0a5"],[100,"#2a7fb8"]];
const CAPE_STOPS = [[0,"#000000"],[100,"#fff3a8"],[500,"#ffd36b"],[1000,"#ff9d4d"],[2000,"#f0544f"],[3500,"#b8336a"],[5000,"#6c2a8f"]];
const PRESS_STOPS = [[960,"#4b2c8c"],[985,"#3b6fb6"],[1000,"#5fa6c9"],[1013,"#93b7c2"],[1025,"#d9b77b"],[1040,"#d9822b"]];
const SNOWD_STOPS = [[0.5,"#dbe9ff"],[5,"#b5ceff"],[20,"#8da7f0"],[50,"#7a6fd6"],[100,"#6a3fb5"],[200,"#4b1f80"]];
const WAVE_STOPS = [[0,"#1d3b6e"],[1,"#2b6cb0"],[2,"#2fa2c4"],[3,"#48c19a"],[4,"#d9d06a"],[6,"#f0954a"],[9,"#d6455a"],[14,"#8f2a7a"]];
const RAIN_STOPS = [[0.1,"#5ac8fa"],[1,"#2f8cf0"],[3,"#3b57d6"],[8,"#7b3fd1"],[20,"#c42f8c"],[50,"#f04b4b"]];
const SNOW_STOPS = [[0.1,"#dff1ff"],[1,"#bcd7ff"],[3,"#9fb2ff"],[8,"#b28cff"]];
const MIXED_STOPS = [[0.1,"#ffb0d0"],[1,"#ff7fb4"],[5,"#e84d8a"]];
// group: "essential" layers are in the toolbar; all are in the panel. night: how much the layer dims at night.
const LAYERS = {
  temperature: { label: "Temperature", icon: "thermostat", vars: ["t2m"], group: "essential", night: 0.2 },
  wind: { label: "Wind", icon: "air", vars: ["u10", "v10"], group: "essential", night: 0.2 },
  precipitation: { label: "Precipitation", icon: "rainy", vars: ["prate", "ptype"], optional: ["ptype"], group: "essential", night: 0.2 },
  clouds: { label: "Cloud cover", icon: "cloud", vars: ["tcc"], group: "essential", night: 0.65 },
  pressure: { label: "Pressure", icon: "compress", vars: ["prmsl"], group: "essential", night: 0.2 },
  gusts: { label: "Wind gusts", icon: "storm", vars: ["gust"], group: "more", night: 0.2 },
  humidity: { label: "Humidity", icon: "humidity_percentage", vars: ["rh2m"], group: "more", night: 0.2 },
  cape: { label: "CAPE", icon: "bolt", vars: ["cape"], group: "more", night: 0.2 },
  snowdepth: { label: "Snow depth", icon: "ac_unit", vars: ["snod"], group: "more", night: 0.2 },
  waves: { label: "Wave height", icon: "waves", vars: ["swh"], group: "more", night: 0.2, wave: true },
  none: { label: "Earth only", icon: "public", vars: [], group: "base", night: 0 },
};
// Scalar layers drawn straight from one field: colour stops, pixel alpha (0–255) and a "left clear below" floor.
const SCALAR = {
  temperature: { v: "t2m", stops: TEMP_STOPS, alpha: 170 },
  pressure: { v: "prmsl", stops: PRESS_STOPS, alpha: 72 },
  gusts: { v: "gust", stops: GUST_STOPS, alpha: 140 },
  humidity: { v: "rh2m", stops: HUM_STOPS, alpha: 128 },
  cape: { v: "cape", stops: CAPE_STOPS, alpha: 155, floor: 100 },
  snowdepth: { v: "snod", stops: SNOWD_STOPS, alpha: 205, floor: 0.5 },
  waves: { v: "swh", stops: WAVE_STOPS, alpha: 185 },
};
const CACHE_MAX = 24;
const WIND_K = 0.03;          // degrees moved per frame per m/s (visual speed of the particles)
const PLAY_MS = 900;          // pause on each step during playback (after the step has been drawn)

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
const esc = (t) => String(t).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const ease = (t) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);

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
  if (S.el) S.el(".czg-loading").hidden = true;
  setTriggerValue("fallback", { reason: msg, t: Date.now() });
}

function webglAvailable() {
  try { const c = document.createElement("canvas"); return !!(c.getContext("webgl2") || c.getContext("webgl")); }
  catch (e) { return false; }
}

function layerButton(k, l, cls) {
  return `<button type="button" class="${cls}" data-layer="${k}" aria-pressed="${k === "temperature"}" aria-label="${l.label}" title="${l.label}"><span class="ico" aria-hidden="true">${l.icon}</span><span class="t">${l.label}</span><span class="na" aria-hidden="true"></span></button>`;
}

async function init(S, root, setTriggerValue) {
  S.trigger = setTriggerValue;
  const wrap = document.createElement("div");      // append: Streamlit keeps this component's CSS in root
  root.appendChild(wrap);
  const entries = Object.entries(LAYERS);
  const group = (g) => entries.filter(([, l]) => l.group === g).map(([k, l]) => layerButton(k, l, "czg-opt")).join("");
  wrap.innerHTML = `
    <div class="czg" role="region" aria-label="3D globe">
      <div class="czg-stage" tabindex="0" aria-label="3D globe. Drag or use arrow keys to rotate, plus and minus to zoom, Home to reset, click to select a point."></div>
      <div class="czg-loading" role="status"><span class="spin" aria-hidden="true"></span><span>Loading the globe…</span></div>
      <div class="czg-top">
        <div class="czg-layers" role="toolbar" aria-label="Main layers">
          ${entries.filter(([, l]) => l.group === "essential").map(([k, l]) => layerButton(k, l, "czg-layer")).join("")}
          <button type="button" class="czg-layer more" data-act="panel" aria-expanded="false" aria-controls="czg-panel" title="All layers and display options"><span class="ico" aria-hidden="true">tune</span><span class="t">More</span></button>
        </div>
      </div>
      <nav class="czg-crumbs" aria-label="Geographic level"></nav>
      <div class="czg-labels" aria-hidden="true"></div>
      <div class="czg-tag" aria-hidden="true"></div>
      <div class="czg-side" role="toolbar" aria-label="View">
        <button type="button" class="czg-btn" data-act="zoomin" aria-label="Zoom in" title="Zoom in"><span class="ico">add</span></button>
        <button type="button" class="czg-btn" data-act="zoomout" aria-label="Zoom out" title="Zoom out"><span class="ico">remove</span></button>
        <button type="button" class="czg-btn" data-act="reset" aria-label="Reset to the whole Earth" title="Reset to the whole Earth (Home)"><span class="ico">public</span></button>
        <button type="button" class="czg-btn" data-act="up" aria-label="Up one level" title="Up one level (Backspace)" disabled><span class="ico">arrow_upward</span></button>
        <button type="button" class="czg-btn" data-act="city" aria-label="Fly to the selected place" title="Fly to the selected place"><span class="ico">my_location</span></button>
        <button type="button" class="czg-btn" data-act="fs" aria-pressed="false" aria-label="Fullscreen" title="Fullscreen"><span class="ico">fullscreen</span></button>
      </div>
      <aside class="czg-panel" id="czg-panel" hidden aria-label="Layers and display">
        <div class="ph"><b>Layers</b><button type="button" class="czg-btn" data-act="panel-close" aria-label="Close layers panel"><span class="ico">close</span></button></div>
        <div class="pg"><div class="pt">Essential</div>${group("essential")}</div>
        <div class="pg"><div class="pt">More layers</div>${group("more")}</div>
        <div class="pg"><div class="pt">Base</div>${group("base")}</div>
        <div class="pg"><div class="pt">Display</div>
          <label class="tog"><input type="checkbox" data-opt="lit" checked /> Day and night (sun at the selected time)</label>
          <label class="tog"><input type="checkbox" data-opt="motion" checked /> Animate wind particles</label>
          <label class="tog"><input type="checkbox" data-opt="geo" checked /> Borders and place names</label>
          <label class="tog"><input type="checkbox" data-opt="stars" checked /> Background stars (decorative)</label>
        </div>
        <div class="pg about"><div class="pt">About this layer</div><div class="czg-about"></div></div>
      </aside>
      <div class="czg-legend" aria-live="polite"></div>
      <div class="czg-readout" aria-live="polite"></div>
      <div class="czg-status" role="status"></div>
      <div class="czg-time">
        <button type="button" class="czg-btn" data-act="play" aria-pressed="false" aria-label="Play the forecast" title="Play the forecast"><span class="ico">play_arrow</span></button>
        <button type="button" class="czg-btn" data-act="prev" aria-label="Previous forecast step" title="Previous step"><span class="ico">skip_previous</span></button>
        <button type="button" class="czg-btn" data-act="next" aria-label="Next forecast step" title="Next step"><span class="ico">skip_next</span></button>
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
  renderer.setClearColor(0x03070f, 1);
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  stage.appendChild(renderer.domElement);
  renderer.domElement.addEventListener("webglcontextlost", (e) => { e.preventDefault(); fail(S, setTriggerValue, "The graphics context was lost."); });
  S.renderer = renderer;

  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(32, 1, 0.1, 200);
  S.scene = scene; S.camera = camera;
  S.dist = 4;
  S.minDist = 1.25; S.maxDist = 12;

  // Globe group: rotated by the controls. The sun direction lives in the globe's own frame (sunL) and is
  // turned into world space every frame (sunW), which all lit materials share.
  const globe = new THREE.Group(); scene.add(globe); S.globe = globe;
  S.sunL = new THREE.Vector3(1, 0, 0); S.sunW = new THREE.Vector3(1, 0, 0);
  S.uLit = { value: 1 }; S.uFade = { value: 1 };
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
  S.earthMesh = new THREE.Mesh(new THREE.SphereGeometry(1, 128, 64), earthMat); globe.add(S.earthMesh);

  // Weather layer sphere (texture painted from the GFS grid), slightly dimmed on the night side and faded in
  // when the layer changes.
  S.overlayCanvas = document.createElement("canvas"); S.overlayCanvas.width = 720; S.overlayCanvas.height = 360;
  S.overlayTex = new THREE.CanvasTexture(S.overlayCanvas);
  S.overlayTex.colorSpace = THREE.SRGBColorSpace;
  S.uNight = { value: LAYERS.temperature.night };
  const overlay = new THREE.Mesh(new THREE.SphereGeometry(1.004, 128, 64), new THREE.ShaderMaterial({
    transparent: true, depthWrite: false,
    uniforms: { map: { value: S.overlayTex }, sunDir: { value: S.sunW }, lit: S.uLit, nightDim: S.uNight, fade: S.uFade },
    vertexShader: VS,
    fragmentShader: `uniform sampler2D map; uniform vec3 sunDir; uniform float lit; uniform float nightDim; uniform float fade;
      varying vec2 vUv; varying vec3 vN; varying vec3 vW;
      void main(){
        vec4 c = texture2D(map, vUv);
        float t = smoothstep(-0.10, 0.12, dot(normalize(vN), normalize(sunDir)));
        float k = mix(1.0, mix(1.0 - nightDim, 1.0, t), lit);
        gl_FragColor = vec4(c.rgb * k, c.a * fade);
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
    fragmentShader: "uniform vec3 sunDir; uniform float lit; varying vec3 vN; varying vec3 vP; varying vec3 vWN; void main(){ float f = pow(clamp(1.0 - abs(dot(normalize(-vP), vN)), 0.0, 1.0), 2.4); float day = mix(1.0, 0.3 + 0.9 * smoothstep(-0.35, 0.45, dot(normalize(vWN), normalize(sunDir))), lit); gl_FragColor = vec4(0.29,0.60,1.0,1.0) * f * 1.25 * day; }",
  }));
  scene.add(atmo); S.atmo = atmo;
  scene.add(camera);
  buildStars(S);

  // Markers.
  S.cityMarker = new THREE.Mesh(new THREE.SphereGeometry(0.012, 16, 12), new THREE.MeshBasicMaterial({ color: 0xf2a93b }));
  S.pickMarker = new THREE.Mesh(new THREE.SphereGeometry(0.010, 16, 12), new THREE.MeshBasicMaterial({ color: 0xffffff }));
  S.pickRing = new THREE.Mesh(new THREE.RingGeometry(0.018, 0.024, 40), new THREE.MeshBasicMaterial({ color: 0xffffff, transparent: true, opacity: 0.85, side: THREE.DoubleSide, depthWrite: false }));
  S.cityMarker.visible = S.pickMarker.visible = S.pickRing.visible = false;
  globe.add(S.cityMarker, S.pickMarker, S.pickRing);
  buildParticles(S);
  buildHighlight(S);

  // Orientation: rotX (tilt) and rotY (spin); the front of the globe faces the camera along +z.
  S.rot = { x: 0.35, y: 0 }; S.vel = { x: 0, y: 0 };
  Object.assign(S.rot, targetRot(S, 10, 20));

  layout(S); setupControls(S, setTriggerValue);
  S.ro = new ResizeObserver(() => { layout(S); requestRender(S); }); S.ro.observe(stage);
  // Pause animation while the globe is off screen or the tab is hidden.
  S.inView = true; S.motion = !S.reduce; S.stars.visible = true;
  S.el('[data-opt="motion"]').checked = S.motion;
  S.io = new IntersectionObserver((es) => { S.inView = es[0].isIntersecting; requestRender(S); }); S.io.observe(stage);
  S.onVis = () => requestRender(S); document.addEventListener("visibilitychange", S.onVis);
  S.onFs = () => { S.fs = !!document.fullscreenElement; fsButton(S); layout(S); requestRender(S); };
  document.addEventListener("fullscreenchange", S.onFs);
  setSun(S, new Date());

  for (const b of S.root.querySelectorAll("[data-layer]")) b.addEventListener("click", () => { setLayer(S, b.dataset.layer); if (b.classList.contains("czg-opt") && window.innerWidth < 640) panel(S, false); });
  for (const c of S.root.querySelectorAll("[data-opt]")) c.addEventListener("change", () => option(S, c.dataset.opt, c.checked));
  S.el(".czg-slider").addEventListener("input", (ev) => { stopPlay(S); setFrame(S, +ev.target.value); });
  S.el(".czg-panel").addEventListener("keydown", (e) => { if (e.key === "Escape") { panel(S, false); e.stopPropagation(); } });
  S.el(".czg").addEventListener("keydown", (e) => { if (e.key === "Escape" && S.expanded) toggleFs(S); });
  root.addEventListener("click", (ev) => {
    const b = ev.target.closest && ev.target.closest("[data-act]"); if (!b) return;
    const a = b.dataset.act;
    if (a === "zoomin") zoom(S, 0.8); if (a === "zoomout") zoom(S, 1 / 0.8);
    if (a === "reset") { resetView(S); setRegion(S, null, { notify: true }); }
    if (a === "up") goUp(S);
    if (a === "crumb") crumbTo(S, +b.dataset.i);
    if (a === "city") { const t = S.picked || S.data.city; if (t) flyTo(S, t.lon, t.lat, Math.min(S.dist, 2.4)); }
    if (a === "fs") toggleFs(S);
    if (a === "panel") panel(S, S.el(".czg-panel").hidden);
    if (a === "panel-close") panel(S, false);
    if (a === "play") (S.playing ? stopPlay(S) : play(S));
    if (a === "prev") { stopPlay(S); setFrame(S, S.frameIdx - 1); }
    if (a === "next") { stopPlay(S); setFrame(S, S.frameIdx + 1); }
    if (a === "now") { stopPlay(S); goNow(S); }
    if (a === "legend") { S.legendOpen = !S.legendOpen; try { localStorage.setItem("czg-legend", S.legendOpen ? "1" : "0"); } catch (e) {} legend(S); layout(S); }
  });

  S.cache = new Map(); S.layer = "temperature"; S.frameIdx = 0; S.fields = {}; S.mstatus = ["", ""];
  try { const v = localStorage.getItem("czg-legend"); S.legendOpen = v === null ? window.innerWidth >= 640 : v === "1"; } catch (e) { S.legendOpen = window.innerWidth >= 640; }
  S.ready = true; S.showGeo = true;
  S.el(".czg-loading").hidden = true;
  loadGeo(S);                                      // borders and names load alongside the forecast; never blocks
  S.status("Loading forecast data…");
  applyData(S);
  await loadManifest(S);
  goNow(S); layout(S); requestRender(S);
  S.poll = setInterval(() => { if (!S.root.isConnected) return teardown(S); loadManifest(S); }, 30000);
}

function teardown(S) {
  if (S.dead) return; S.dead = true;
  clearInterval(S.poll); clearTimeout(S.playTimer); cancelAnimationFrame(S.raf);
  if (S.abort) S.abort.abort();
  if (S.ro) S.ro.disconnect();
  if (S.unbind) S.unbind();
  if (S.io) S.io.disconnect();
  if (S.onVis) document.removeEventListener("visibilitychange", S.onVis);
  if (S.onFs) document.removeEventListener("fullscreenchange", S.onFs);
  for (const t of [S.dayTex, S.nightTex, S.overlayTex, S.hlTex]) if (t) t.dispose();
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

// Rotation that turns (lat, lon) to face the camera (+z), with a tilt that keeps the poles sensible.
function targetRot(S, lon, lat) {
  const p = toVec(S.THREE, 0, lon, 1);
  return { y: -Math.atan2(p.x, p.z), x: Math.max(-1.2, Math.min(1.2, lat * Math.PI / 180)) };
}

// Camera flight: rotation and distance ease together, rising a little for long hops. None under reduced motion.
function flyTo(S, lon, lat, dist) {
  const to = targetRot(S, lon, lat), d = dist === undefined ? S.dist : dist;
  let dy = to.y - S.rot.y; dy = Math.atan2(Math.sin(dy), Math.cos(dy));       // shortest way round
  S.vel.x = S.vel.y = 0; S.zoomed = dist !== undefined || S.zoomed;
  if (S.reduce || !S.ready) { S.rot.y += dy; S.rot.x = to.x; S.dist = d; S.flight = null; requestRender(S); return; }
  const arc = Math.acos(Math.max(-1, Math.min(1, toVec(S.THREE, lat, lon, 1).dot(currentFacing(S)))));
  S.flight = { t0: performance.now(), dur: 700 + 600 * arc / Math.PI, x0: S.rot.x, y0: S.rot.y, d0: S.dist,
               x1: to.x, y1: S.rot.y + dy, d1: d, hop: 0.9 * arc / Math.PI };
  requestRender(S);
}
function currentFacing(S) {
  // The point of the globe facing the camera, in the globe's own frame.
  return new S.THREE.Vector3(0, 0, 1).applyQuaternion(S.globe.quaternion.clone().invert());
}
function resetView(S) { S.zoomed = false; flyTo(S, 10, 20, fitDist(S)); S.zoomed = false; }

// --- Rendering on demand ---
function requestRender(S) {
  if (S.pending || !S.ready || S.dead) return;
  S.pending = true;
  S.raf = requestAnimationFrame(() => { S.pending = false; frame(S); });
}
function frame(S) {
  if (!S.root.isConnected) return teardown(S);
  let busy = false;
  if (S.flight) {
    const f = S.flight, t = Math.min(1, (performance.now() - f.t0) / f.dur), k = ease(t);
    S.rot.x = f.x0 + (f.x1 - f.x0) * k; S.rot.y = f.y0 + (f.y1 - f.y0) * k;
    S.dist = f.d0 + (f.d1 - f.d0) * k + Math.sin(Math.PI * t) * f.hop * f.d0;
    if (t >= 1) S.flight = null; else busy = true;
  } else if (!S.dragging && !S.reduce && (Math.abs(S.vel.x) > 1e-4 || Math.abs(S.vel.y) > 1e-4)) {
    S.rot.y += S.vel.y; S.rot.x += S.vel.x; S.vel.x *= 0.92; S.vel.y *= 0.92; busy = true;   // inertia
  }
  if (S.fadeT0) {
    const t = Math.min(1, (performance.now() - S.fadeT0) / 280);
    S.uFade.value = 0.15 + 0.85 * t; if (t >= 1) S.fadeT0 = 0; else busy = true;
  }
  S.dist = Math.max(S.minDist, Math.min(S.maxDist, S.dist));
  S.rot.x = Math.max(-1.35, Math.min(1.35, S.rot.x));
  S.globe.rotation.set(S.rot.x, S.rot.y, 0, "XYZ");
  S.sunW.copy(S.sunL).applyQuaternion(S.globe.quaternion);
  S.camera.position.set(0, 0, S.dist); S.camera.lookAt(0, 0, 0);
  const ms = Math.max(0.25, Math.min(1.2, (S.dist - 1) / 3));          // markers keep a similar size on screen
  S.cityMarker.scale.setScalar(ms); S.pickMarker.scale.setScalar(ms); S.pickRing.scale.setScalar(ms);
  if (S.geo) updateGeo(S);
  const animate = windAnimating(S);
  if (animate) stepParticles(S);
  S.renderer.render(S.scene, S.camera);
  if (busy || animate) requestRender(S);
}

function layout(S) { resize(S); placePanels(S); if (!S.zoomed && !S.flight) S.dist = fitDist(S); }

// Camera distance at which the globe and its rim fill about 92% of the free area's shorter side.
function fitDist(S) {
  const t = Math.tan(S.camera.fov * Math.PI / 360), h = S.view.h, side = Math.min(S.view.w, S.view.freeH);
  return Math.max(S.minDist, Math.min(S.maxDist, 1.08 * (h / 2) / (t * 0.46 * side)));
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
  else { top = 52; end = h - tl.offsetHeight - 26; }          // between the toolbar and the timeline
  S.view = { w, h, freeH: Math.max(120, end - top), offY: Math.round(h / 2 - (top + end) / 2) };
  if (S.view.offY) S.camera.setViewOffset(w, h, 0, S.view.offY, w, h); else S.camera.clearViewOffset();
}
function zoom(S, k) { S.zoomed = true; S.flight = null; S.dist = Math.max(S.minDist, Math.min(S.maxDist, S.dist * k)); requestRender(S); }

function setupControls(S, setTriggerValue) {
  const el = S.renderer.domElement, stage = S.el(".czg-stage");
  const pointers = new Map(); let start = null, lastPinch = null;
  const speed = () => 0.0055 * (S.dist - 0.9) / 2.7;
  const down = (e) => { el.setPointerCapture(e.pointerId); pointers.set(e.pointerId, [e.clientX, e.clientY]);
    S.dragging = true; S.flight = null; S.vel.x = S.vel.y = 0; start = { x: e.clientX, y: e.clientY, t: Date.now(), moved: 0 };
    el.style.cursor = "grabbing"; };
  const move = (e) => {
    if (!pointers.has(e.pointerId)) { if (e.pointerType === "mouse") hoverAt(S, e); return; }
    const prev = pointers.get(e.pointerId); pointers.set(e.pointerId, [e.clientX, e.clientY]);
    if (pointers.size === 2) {
      const [a, b] = [...pointers.values()]; const d = Math.hypot(a[0] - b[0], a[1] - b[1]);
      if (lastPinch) zoom(S, lastPinch / d); lastPinch = d; if (start) start.moved = 99; return;
    }
    const dx = e.clientX - prev[0], dy = e.clientY - prev[1];
    if (start) start.moved += Math.abs(dx) + Math.abs(dy);
    S.rot.y += dx * speed(); S.rot.x += dy * speed();
    S.vel.y = dx * speed(); S.vel.x = dy * speed();
    requestRender(S);
  };
  const up = (e) => {
    pointers.delete(e.pointerId); if (pointers.size < 2) lastPinch = null;
    if (pointers.size === 0) { S.dragging = false; el.style.cursor = "grab"; requestRender(S); }
    if (start && start.moved < 6 && Date.now() - start.t < 600) pick(S, e, setTriggerValue);
    start = null;
  };
  const wheel = (e) => { e.preventDefault(); zoom(S, e.deltaY > 0 ? 1.08 : 1 / 1.08); };
  const key = (e) => {
    const k = e.key, st = 0.08;
    if (k === "ArrowLeft") S.rot.y += st; else if (k === "ArrowRight") S.rot.y -= st;
    else if (k === "ArrowUp") S.rot.x += st; else if (k === "ArrowDown") S.rot.x -= st;
    else if (k === "+" || k === "=") return zoom(S, 0.85); else if (k === "-" || k === "_") return zoom(S, 1 / 0.85);
    else if (k === "Home") { e.preventDefault(); resetView(S); return setRegion(S, null, { notify: true }); }
    else if (k === "Backspace") { e.preventDefault(); return goUp(S); }
    else return;
    S.flight = null; e.preventDefault(); requestRender(S);
  };
  el.addEventListener("pointerdown", down); el.addEventListener("pointermove", move);
  el.addEventListener("pointerup", up); el.addEventListener("pointercancel", up);
  const leave = () => { if (S.hover) { S.hover = null; drawHighlight(S); } S.el(".czg-tag").classList.remove("show"); };
  el.addEventListener("pointerleave", leave);
  el.addEventListener("wheel", wheel, { passive: false }); stage.addEventListener("keydown", key);
  el.style.touchAction = "none"; el.style.cursor = "grab";
  S.unbind = () => { el.removeEventListener("pointerdown", down); el.removeEventListener("pointermove", move);
    el.removeEventListener("pointerup", up); el.removeEventListener("pointercancel", up);
    el.removeEventListener("wheel", wheel); stage.removeEventListener("keydown", key); el.removeEventListener("pointerleave", leave); };
}

async function pick(S, e, setTriggerValue) {
  const THREE = S.THREE, rect = S.renderer.domElement.getBoundingClientRect();
  const sx = e.clientX - rect.left, sy = e.clientY - rect.top;
  const ndc = new THREE.Vector2((sx / rect.width) * 2 - 1, -(sy / rect.height) * 2 + 1);
  const ray = new THREE.Raycaster(); ray.setFromCamera(ndc, S.camera);
  const hit = ray.intersectObject(S.earthMesh, false)[0];
  if (!hit) return;
  let { lat, lon } = toLatLon(S.globe.worldToLocal(hit.point.clone()));
  // A click within a few pixels of a shown city label selects that city (Natural Earth name and position).
  const city = (S.cityHits || []).find((c) => Math.hypot(c.x - sx, c.y - sy) < 9);
  if (city) { lat = city.lat; lon = city.lon; }
  lat = +lat.toFixed(3); lon = +lon.toFixed(3);
  selectPoint(S, lat, lon);
  const region = await regionAt(S, lon, lat);
  S.city = city ? { name: city.name, lat, lon } : null;
  setRegion(S, region, { notify: false });
  setTriggerValue("picked", { lat, lon, t: Date.now(), place: city ? city.name : null, region: regionInfo(S) });
}
function clearPoint(S) { S.picked = null; S.pickMarker.visible = S.pickRing.visible = false; readout(S); requestRender(S); }
function selectPoint(S, lat, lon) {
  const THREE = S.THREE;
  S.picked = { lat, lon };
  S.pickMarker.position.copy(toVec(THREE, lat, lon, 1.012)); S.pickMarker.visible = true;
  S.pickRing.position.copy(toVec(THREE, lat, lon, 1.006)); S.pickRing.lookAt(toVec(THREE, lat, lon, 2)); S.pickRing.visible = true;
  S.el('[data-act="city"]').disabled = false;
  readout(S); requestRender(S);
}

// --- Fullscreen, panel, options ---
function toggleFs(S) {
  const box = S.el(".czg");
  if (S.fs) { document.exitFullscreen && document.exitFullscreen(); return; }
  if (S.expanded) { S.expanded = false; box.classList.remove("expanded"); fsButton(S); layout(S); requestRender(S); return; }
  const fallback = () => { S.expanded = true; box.classList.add("expanded"); fsButton(S); layout(S); requestRender(S); };
  if (box.requestFullscreen) box.requestFullscreen().catch(fallback); else fallback();   // e.g. iPhone Safari
}
function fsButton(S) {
  const on = S.fs || S.expanded, b = S.el('[data-act="fs"]');
  b.setAttribute("aria-pressed", String(!!on)); b.title = on ? "Exit fullscreen" : "Fullscreen";
  b.setAttribute("aria-label", b.title); b.querySelector(".ico").textContent = on ? "fullscreen_exit" : "fullscreen";
}
function panel(S, open) {
  const p = S.el(".czg-panel"), b = S.el('[data-act="panel"]');
  p.hidden = !open; b.setAttribute("aria-expanded", String(open));
  if (open) { about(S); const f = p.querySelector('[aria-pressed="true"]') || p.querySelector("button"); f && f.focus(); }
  else b.focus();
}
function option(S, key, on) {
  if (key === "lit") { S.uLit.value = on ? 1 : 0; timeLabel(S); readout(S); }
  if (key === "motion") { S.motion = on; startWind(S); legend(S); }
  if (key === "stars") S.stars.visible = on;
  if (key === "geo") { S.showGeo = on; if (S.borders) S.borders.visible = on; S.geoDirty = true; }
  requestRender(S);
}

// --- Data ---
function applyData(S) {
  const d = S.data;
  S.units = d.units || { temperature: "°C" };
  if (d.city) {
    S.cityMarker.position.copy(toVec(S.THREE, d.city.lat, d.city.lon, 1.012)); S.cityMarker.visible = true;
  } else S.cityMarker.visible = false;
  // A place or region chosen with the page's search or browser: fly there and select it (once per choice).
  if (d.focus && d.focus.token !== S.focusToken) {
    S.focusToken = d.focus.token; S.centered = true;
    if (S.geo) applyFocus(S, d.focus); else S.pendingFocus = d.focus;
  } else if (d.city && !S.centered) { S.centered = true; Object.assign(S.rot, targetRot(S, d.city.lon, d.city.lat)); }
  S.el('[data-act="city"]').disabled = !(d.city || S.picked);
  legend(S); readout(S); requestRender(S);
}

async function loadManifest(S) {
  try {
    const r = await fetch(new URL(S.data.manifest, document.baseURI).href + "?t=" + Date.now(), { cache: "no-store" });
    if (r.status === 404) { S.status("Preparing global forecast data… the Earth is shown meanwhile."); return; }
    if (!r.ok) throw new Error("HTTP " + r.status);
    const m = await r.json();
    const first = !S.manifest, runChanged = S.manifest && m.run_id !== S.manifest.run_id;
    const updated = S.manifest && m.updated_utc !== S.manifest.updated_utc;
    S.manifest = m; S.frames = m.frames.filter((f) => f.ready);
    if (!S.frames.length) { S.status("Preparing global forecast data…"); return; }
    if (runChanged) S.cache.clear();
    S.el(".czg-slider").max = String(S.frames.length - 1);
    S.mstatus = m.status === "complete" ? ["", ""] : [`Preparing forecast steps: ${S.frames.length} of ${m.frames.length} ready…`, "info"];
    if (!S.warned) S.status(...S.mstatus);
    S.el(".czg-attr").title = S.el(".czg-attr").textContent = `${m.attribution} · ${m.grid.resolution} grid · run ${m.run_utc.replace("T", " ")} · Earth: NASA Blue Marble and Black Marble (GIBS) · Rendering: three.js`;
    availability(S);
    if (first || runChanged) { S.frameIdx = Math.min(S.frameIdx, S.frames.length - 1); await paint(S); }
    else if (updated) await paint(S);
  } catch (e) {
    S.status("Forecast data couldn't be loaded (" + e.message + "). The Earth is still shown; retrying shortly.", "error");
  }
}

// Which layers the run actually has: a layer is available at a step when none of its required fields is
// listed as missing for that step (and the run's encoding knows the field at all).
function layerOk(S, key, frame) {
  const l = LAYERS[key], enc = S.manifest ? S.manifest.encoding : {};
  const need = l.vars.filter((v) => !(l.optional || []).includes(v));
  return need.every((v) => enc[v] && !(frame.missing || []).includes(v));
}
function availability(S) {
  if (!S.frames) return;
  for (const b of S.root.querySelectorAll("[data-layer]")) {
    const k = b.dataset.layer; if (k === "none") continue;
    const n = S.frames.filter((f) => layerOk(S, k, f)).length, na = b.querySelector(".na");
    b.disabled = n === 0;
    const note = n === 0 ? "not in this run" : n < S.frames.length ? `${n} of ${S.frames.length} steps` : "";
    if (na) na.textContent = note;
    b.title = LAYERS[k].label + (note ? ` (${note})` : "");
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
  if (!enc || q.length !== 181 * 360) throw new Error("unexpected data for " + v);
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
  S.overlayTex.needsUpdate = true; S.fields = {}; S.wind.lines.visible = false; clearIsobars(S);
}

async function paint(S) {
  if (!S.frames || !S.frames.length) return;
  const L_ = S.layer, info = LAYERS[L_];
  S.uNight.value = info.night;
  if (L_ === "none") { clearOverlay(S); S.warned = false; S.status(...S.mstatus); readout(S); requestRender(S); return; }
  const fr = S.frames[S.frameIdx];
  if (S.abort) S.abort.abort();
  const ac = new AbortController(); S.abort = ac; const token = (S.token = (S.token || 0) + 1);
  let got;
  try { got = await Promise.all(info.vars.map((v) => (S.manifest.encoding[v] ? getField(S, fr.fhr, v, ac.signal) : null))); }
  catch (e) { if (e.name !== "AbortError") { S.warned = true; S.status("This forecast step couldn't be loaded (" + e.message + ").", "error"); } return; }
  if (token !== S.token) return;
  const fields = {}; info.vars.forEach((v, i) => { if (got[i]) fields[v] = got[i]; });
  const missing = info.vars.filter((v) => !fields[v] && !(info.optional || []).includes(v));
  if (missing.length) {
    clearOverlay(S); S.warned = true;
    S.status(`${info.label} isn't available for this step${info.wave ? " (the GFS-Wave file wasn't published)" : ""}. Try another step or layer.`, "warn");
    readout(S); requestRender(S); return;
  }
  S.warned = false; S.status(...S.mstatus);
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
  if (SCALAR[L_]) {
    const sc = SCALAR[L_], s = sampler(fields[sc.v]), ramp = makeRamp(sc.stops);
    draw((la, lo) => { const v = s(la, lo); if (!isFinite(v) || (sc.floor !== undefined && v < sc.floor)) return null; const c = ramp(v); return [c[0], c[1], c[2], sc.alpha]; });
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
  if (S.drawnLayer !== L_ && !S.reduce) { S.uFade.value = 0.15; S.fadeT0 = performance.now(); }   // smooth layer switch
  S.drawnLayer = L_;
  if (L_ === "wind") startWind(S); else S.wind.lines.visible = false;
  if (L_ === "pressure") isobars(S, fields.prmsl); else clearIsobars(S);
  readout(S); requestRender(S);
}

// --- Isobars: contours of mean sea-level pressure every 4 hPa, traced on the 1° grid (marching squares) ---
function clearIsobars(S) {
  if (S.iso) { S.globe.remove(S.iso); S.iso.geometry.dispose(); S.iso.material.dispose(); S.iso = null; }
}
function isobars(S, g) {
  clearIsobars(S);
  const out = [], R = 1.0065, D = Math.PI / 180;
  const push = (la, lo) => { const th = (90 - la) * D, ph = (lo + 180) * D; out.push(-R * Math.cos(ph) * Math.sin(th), R * Math.cos(th), R * Math.sin(ph) * Math.sin(th)); };
  const pts = new Float64Array(8);
  for (let y = 0; y < 180; y++) {
    const la0 = 90 - y, la1 = 89 - y;
    for (let x = 0; x < 360; x++) {
      const x1 = (x + 1) % 360;
      const a = g[y * 360 + x], b = g[y * 360 + x1], c = g[(y + 1) * 360 + x1], d = g[(y + 1) * 360 + x];
      if (!(isFinite(a) && isFinite(b) && isFinite(c) && isFinite(d))) continue;
      const lo = Math.min(a, b, c, d), hi = Math.max(a, b, c, d);
      for (let L = Math.ceil(lo / 4) * 4; L <= hi; L += 4) {
        let n = 0;
        // Corners: a (la0, x), b (la0, x+1), c (la1, x+1), d (la1, x); edges a-b, b-c, c-d, d-a.
        if ((a < L) !== (b < L)) { const t = (L - a) / (b - a); pts[n++] = la0; pts[n++] = x + t; }
        if ((b < L) !== (c < L)) { const t = (L - b) / (c - b); pts[n++] = la0 - t; pts[n++] = x + 1; }
        if ((c < L) !== (d < L)) { const t = (L - c) / (d - c); pts[n++] = la1; pts[n++] = x + 1 - t; }
        if ((d < L) !== (a < L)) { const t = (L - d) / (a - d); pts[n++] = la1 + t; pts[n++] = x; }
        for (let k = 0; k + 3 < n; k += 4) { push(pts[k], pts[k + 1]); push(pts[k + 2], pts[k + 3]); }
      }
    }
  }
  const geo = new S.THREE.BufferGeometry();
  geo.setAttribute("position", new S.THREE.Float32BufferAttribute(out, 3));
  S.iso = new S.THREE.LineSegments(geo, new S.THREE.LineBasicMaterial({ color: 0xffffff, transparent: true, opacity: 0.6, depthWrite: false }));
  S.iso.renderOrder = 2; S.globe.add(S.iso);
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

// Decorative background stars (fixed, seeded positions; not a star catalogue).
function buildStars(S) {
  const THREE = S.THREE, n = 1400, pos = new Float32Array(n * 3), col = new Float32Array(n * 3);
  let seed = 7; const rnd = () => ((seed = (seed * 16807) % 2147483647) / 2147483647);
  for (let i = 0; i < n; i++) {
    const z = 2 * rnd() - 1, t = 2 * Math.PI * rnd(), r = Math.sqrt(1 - z * z), k = 0.25 + 0.75 * Math.pow(rnd(), 3);
    pos[i * 3] = 80 * r * Math.cos(t); pos[i * 3 + 1] = 80 * z; pos[i * 3 + 2] = 80 * r * Math.sin(t);
    col[i * 3] = k * 0.9; col[i * 3 + 1] = k * 0.93; col[i * 3 + 2] = k;
  }
  const geo = new THREE.BufferGeometry();
  geo.setAttribute("position", new THREE.BufferAttribute(pos, 3)); geo.setAttribute("color", new THREE.BufferAttribute(col, 3));
  S.stars = new THREE.Points(geo, new THREE.PointsMaterial({ size: 1.4, sizeAttenuation: false, vertexColors: true, transparent: true, opacity: 0.8, depthWrite: false }));
  S.scene.add(S.stars);
}

function setSun(S, date) {
  const p = subsolar(date);
  S.sunL.copy(toVec(S.THREE, p.lat, p.lon, 1)); S.sun = p;
  requestRender(S);
}

// --- Timeline and playback ---
function setFrame(S, idx) {
  if (!S.frames || !S.frames.length) return Promise.resolve();
  S.frameIdx = Math.max(0, Math.min(idx, S.frames.length - 1));
  S.el(".czg-slider").value = String(S.frameIdx);
  setSun(S, new Date(S.frames[S.frameIdx].valid_utc)); readout(S);
  timeLabel(S);
  layout(S);
  return paint(S);
}
function timeLabel(S) {
  if (!S.frames || !S.frames.length) return;
  const f = S.frames[S.frameIdx], t = new Date(f.valid_utc);
  const local = t.toLocaleString(undefined, { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
  const tz = Intl.DateTimeFormat().resolvedOptions().timeZone || "local time";
  const utc = f.valid_utc.slice(11, 16) + " UTC";
  const sun = S.uLit.value ? " · daylight as at this time" : "";
  const step = `step ${S.frameIdx + 1} of ${S.frames.length}`;
  S.el(".czg-tl-label").innerHTML = `<b>${local}</b> <span class="mut">${esc(tz)} (${utc}) · +${f.fhr} h from the ${S.manifest.run_utc.slice(11, 16)} UTC GFS run · ${f.fhr === 0 ? "analysis" : "forecast"} · ${step}${sun}</span>`;
  S.el(".czg-slider").setAttribute("aria-valuetext", `${local} ${tz}, plus ${f.fhr} hours`);
}
function goNow(S) {
  if (!S.frames || !S.frames.length) return;
  const now = Date.now(); let best = 0, bd = Infinity;
  S.frames.forEach((f, i) => { const d = Math.abs(Date.parse(f.valid_utc) - now); if (d < bd) { bd = d; best = i; } });
  setFrame(S, best);
}
// Playback: step through the run's own valid times, waiting for each step to be drawn; stops at the last step.
function play(S) {
  if (!S.frames || S.frames.length < 2) return;
  if (S.frameIdx >= S.frames.length - 1) S.frameIdx = -1;      // at the end: play again from the start
  S.playing = true; playButton(S);
  const tick = async () => {
    if (!S.playing || S.dead) return;
    if (document.hidden || !S.inView) { S.playTimer = setTimeout(tick, 500); return; }
    if (S.frameIdx >= S.frames.length - 1) return stopPlay(S);
    await setFrame(S, S.frameIdx + 1);
    if (S.playing) S.playTimer = setTimeout(tick, PLAY_MS);
  };
  tick();
}
function stopPlay(S) { S.playing = false; clearTimeout(S.playTimer); playButton(S); }
function playButton(S) {
  const b = S.el('[data-act="play"]'), on = !!S.playing;
  b.setAttribute("aria-pressed", String(on)); b.title = on ? "Pause" : "Play the forecast";
  b.setAttribute("aria-label", b.title); b.querySelector(".ico").textContent = on ? "pause" : "play_arrow";
}
function setLayer(S, key) {
  if (!LAYERS[key]) return;
  S.layer = key;
  for (const b of S.root.querySelectorAll("[data-layer]")) b.setAttribute("aria-pressed", String(b.dataset.layer === key));
  const more = S.el('[data-act="panel"]');
  more.classList.toggle("on", LAYERS[key].group !== "essential");
  more.querySelector(".t").textContent = LAYERS[key].group === "more" ? LAYERS[key].label : "More";
  legend(S); about(S); layout(S); paint(S);
}

// --- Legend, layer details and point readout ---
function legend(S) {
  const el = S.el(".czg-legend"), u = S.units || {}, L_ = S.layer, info = LAYERS[L_];
  const bar = (stops, conv, unit, digits = 0) => {
    const g = stops.map(([v, c], i) => `${c} ${Math.round(i / (stops.length - 1) * 100)}%`).join(",");
    const ticks = stops.filter((_, i) => i % Math.ceil(stops.length / 5) === 0 || i === stops.length - 1)
      .map(([v]) => { const x = conv(v); return `<span>${digits || x === 0 || Math.abs(x) >= 1 ? x.toFixed(digits) : x.toFixed(1)}</span>`; }).join("");
    return `<div class="bar" style="background:linear-gradient(90deg,${g})"></div><div class="ticks">${ticks}</div><div class="unit">${unit}</div>`;
  };
  const titles = { temperature: "Temperature at 2 m", wind: "Wind at 10 m", precipitation: "Precipitation rate", clouds: "Total cloud cover",
    pressure: "Mean sea-level pressure", gusts: "Wind gusts (surface)", humidity: "Relative humidity at 2 m", cape: "CAPE (surface-based)",
    snowdepth: "Snow depth", waves: "Significant wave height", none: "Earth only" };
  let body = "";
  if (L_ === "none") body = `<div class="note">NASA Blue Marble by day and Black Marble city lights by night: composites, not current conditions.</div>`;
  else if (L_ === "temperature") body = bar(TEMP_STOPS, (v) => fmtTemp(v, u.temperature || "°C"), u.temperature || "°C");
  else if (L_ === "wind") body = bar(WIND_STOPS, (v) => fmtWind(v, u.wind || "km/h"), u.wind || "km/h") +
    `<div class="note">${S.motion ? "Particles move with the forecast wind vectors." : "Streamlines follow the forecast wind vectors."}</div>`;
  else if (L_ === "gusts") body = bar(GUST_STOPS, (v) => fmtWind(v, u.wind || "km/h"), u.wind || "km/h");
  else if (L_ === "precipitation") {
    const pu = u.precipitation === "in" ? "in/h" : "mm/h", d = u.precipitation === "in" ? 2 : 1;
    body = bar(RAIN_STOPS, (v) => fmtRate(v, u.precipitation), pu, d) +
      `<div class="types"><span><i style="background:#2f8cf0"></i>Rain</span><span><i style="background:#bcd7ff"></i>Snow</span><span><i style="background:#ff7fb4"></i>Mixed / freezing</span></div><div class="note">Model forecast, not radar. Below 0.1 mm/h is left clear.</div>`;
  } else if (L_ === "clouds") body = `<div class="bar" style="background:linear-gradient(90deg,rgba(240,244,250,0),rgba(240,244,250,.85))"></div><div class="ticks"><span>0</span><span>50</span><span>100</span></div><div class="unit">%</div><div class="note">All cloud levels together; low, mid and high cloud are not split.</div>`;
  else if (L_ === "pressure") body = bar(PRESS_STOPS, (v) => v, "hPa") + `<div class="note">Isobars every 4 hPa, traced on the 1° model grid.</div>`;
  else if (L_ === "humidity") body = bar(HUM_STOPS, (v) => v, "%");
  else if (L_ === "cape") body = bar(CAPE_STOPS.slice(1), (v) => v, "J/kg") + `<div class="note">Instability available to storms, not a storm forecast. Below 100 J/kg is left clear.</div>`;
  else if (L_ === "snowdepth") body = bar(SNOWD_STOPS, (v) => v, "cm") + `<div class="note">Snow on the ground (model). Below 0.5 cm is left clear.</div>`;
  else if (L_ === "waves") body = bar(WAVE_STOPS, (v) => v, "m") + `<div class="note">GFS-Wave: wind waves and swell combined. Land and sea ice are left clear.</div>`;
  const kind = L_ === "none" ? "" : `<span class="kind">${info.wave ? "GFS-Wave" : "GFS"} forecast</span>`;
  el.classList.toggle("collapsed", !S.legendOpen);
  el.innerHTML = `<button type="button" class="lg-h" data-act="legend" aria-expanded="${!!S.legendOpen}" title="${S.legendOpen ? "Collapse" : "Expand"} the legend"><span class="ico" aria-hidden="true">${info.icon}</span><b>${titles[L_]}</b>${kind}<span class="ico chev" aria-hidden="true">${S.legendOpen ? "expand_more" : "expand_less"}</span></button><div class="lg-b">${body}</div>`;
}

function about(S) {
  const el = S.el(".czg-about"); if (!el) return;
  const L_ = S.layer, info = LAYERS[L_], m = S.manifest;
  if (L_ === "none" || !m) { el.innerHTML = `<p>Earth imagery: NASA Blue Marble (day) and VIIRS Black Marble 2016 (night) via NASA GIBS, public domain. Composites, not current conditions.</p>`; return; }
  const src = info.wave ? (m.wave_source || "NOAA GFS-Wave") : m.source;
  const n = S.frames ? S.frames.filter((f) => layerOk(S, L_, f)).length : 0;
  el.innerHTML = `<p><b>Source:</b> ${esc(src)}. Public domain (NOAA).</p>
    <p><b>Kind:</b> numerical model forecast; the +0 h step is the model analysis. Not radar, satellite or station observations.</p>
    <p><b>Grid:</b> 1° (about 110 km), values between grid points interpolated for display.</p>
    <p><b>Times:</b> run ${esc(m.run_utc.replace("T", " "))}, 3-hourly to +72 h; available for ${n} of ${S.frames ? S.frames.length : 0} steps.</p>`;
}

function readout(S) {
  const el = S.el(".czg-readout");
  if (!S.picked) {
    const rs = regionStats(S);
    el.innerHTML = rs ? `<div class="rs">${rs}</div>` : `<span class="mut">Click the globe to select a place or region.</span>`; return;
  }
  const { lat, lon } = S.picked, u = S.units || {}, f = S.fields || {};
  const coord = `${Math.abs(lat).toFixed(2)}°${lat >= 0 ? "N" : "S"}, ${Math.abs(lon).toFixed(2)}°${lon >= 0 ? "E" : "W"}`;
  const s = (v) => (f[v] ? sampler(f[v])(lat, lon) : NaN), na = "Not available";
  let val = "";
  const L_ = S.layer;
  if (L_ === "temperature") { const x = s("t2m"); val = isFinite(x) ? `${fmtTemp(x, u.temperature || "°C").toFixed(1)} ${u.temperature || "°C"}` : na; }
  else if (L_ === "wind") {
    const a = s("u10"), b = s("v10"), wu = u.wind || "km/h";
    if (isFinite(a) && isFinite(b)) { const from = (Math.atan2(-a, -b) * 180 / Math.PI + 360) % 360; const dirs = ["N","NE","E","SE","S","SW","W","NW"]; val = `${fmtWind(Math.hypot(a, b), wu).toFixed(0)} ${wu} from ${dirs[Math.round(from / 45) % 8]} (${from.toFixed(0)}°)`; } else val = na;
  }
  else if (L_ === "gusts") { const x = s("gust"), wu = u.wind || "km/h"; val = isFinite(x) ? `${fmtWind(x, wu).toFixed(0)} ${wu} gusts` : na; }
  else if (L_ === "precipitation") { const x = s("prate"), t = f.ptype ? Math.round(s("ptype")) : NaN; val = isFinite(x) ? `${fmtRate(x, u.precipitation).toFixed(u.precipitation === "in" ? 2 : 1)} ${u.precipitation === "in" ? "in/h" : "mm/h"}${x >= 0.1 && t >= 1 && t <= 3 ? " · " + ["rain", "snow", "mixed/freezing"][t - 1] : ""}` : na; }
  else if (L_ === "clouds") { const x = s("tcc"); val = isFinite(x) ? `${x.toFixed(0)}% cloud cover` : na; }
  else if (L_ === "pressure") { const x = s("prmsl"); val = isFinite(x) ? `${x.toFixed(1)} hPa` : na; }
  else if (L_ === "humidity") { const x = s("rh2m"); val = isFinite(x) ? `${x.toFixed(0)}% relative humidity` : na; }
  else if (L_ === "cape") { const x = s("cape"); val = isFinite(x) ? `${x.toFixed(0)} J/kg` : na; }
  else if (L_ === "snowdepth") { const x = s("snod"); val = isFinite(x) ? `${x.toFixed(x < 10 ? 1 : 0)} cm snow depth` : na; }
  else if (L_ === "waves") { const x = s("swh"); val = isFinite(x) ? `${x.toFixed(1)} m significant wave height` : (f.swh ? "No wave data (land or sea ice)" : na); }
  const dn = S.sun ? (dayAt(S, lat, lon) ? "daytime" : "night") + " at this time" : "";
  const rs = regionStats(S);
  el.innerHTML = (rs ? `<div class="rs">${rs}</div>` : "") + `<b>${coord}</b>${val ? ` · ${val}` : ""}<div class="mut">${L_ === "none" ? "" : "GFS 1° grid value (interpolated) at the selected step"}${L_ !== "none" && dn ? " · " : ""}${dn}</div>`;
}
function dayAt(S, lat, lon) {
  // Sun above the horizon: angular distance from the subsolar point under 90°.
  const R = Math.PI / 180, p = S.sun;
  return Math.sin(lat * R) * Math.sin(p.lat * R) + Math.cos(lat * R) * Math.cos(p.lat * R) * Math.cos((lon - p.lon) * R) > 0;
}

// --- Geography: borders, progressive labels, regions and the breadcrumb (Natural Earth; India's point of view) ---
async function loadGeo(S) {
  try {
    const r = await fetch(new URL(`${GEO_URL}/countries.json`, document.baseURI));
    if (!r.ok) throw new Error("HTTP " + r.status);
    const g = await r.json(); if (S.dead) return;
    S.geo = { countries: g.countries, conts: g.continents, view: g.view, adm1: new Map(), loading: new Map(), places: null };
    for (const c of S.geo.countries.concat(S.geo.conts)) c.v = toVec(S.THREE, c.lat, c.lon, 1);
    buildBorders(S);
    if (S.pendingFocus) { const f = S.pendingFocus; S.pendingFocus = null; applyFocus(S, f); }
    S.geoDirty = true; crumbs(S); requestRender(S);
    fetch(new URL(`${GEO_URL}/places.json`, document.baseURI)).then((x) => (x.ok ? x.json() : null)).then((p) => {
      if (!p || S.dead) return;
      S.geo.places = p.places;
      S.geo.placeV = new Float32Array(p.places.length * 3);
      p.places.forEach((c, i) => { const v = toVec(S.THREE, c[1], c[2], 1); S.geo.placeV.set([v.x, v.y, v.z], i * 3); });
      S.geoDirty = true; requestRender(S);
    }).catch(() => {});
  } catch (e) {
    S.geoError = "Borders and place names couldn't be loaded (" + e.message + "); weather layers still work."; crumbs(S);
  }
}
function pushXYZ(out, lat, lon, R) {
  const D = Math.PI / 180, th = (90 - lat) * D, ph = (lon + 180) * D;
  out.push(-R * Math.cos(ph) * Math.sin(th), R * Math.cos(th), R * Math.sin(ph) * Math.sin(th));
}
// One boundary edge as short chords (long edges are split so they stay above the curved surface).
function seg(out, x1, y1, x2, y2, R) {
  const n = Math.max(1, Math.ceil(Math.max(Math.abs(x2 - x1), Math.abs(y2 - y1)) / 1.5));
  let px = x1, py = y1;
  for (let k = 1; k <= n; k++) { const x = x1 + (x2 - x1) * k / n, y = y1 + (y2 - y1) * k / n; pushXYZ(out, py, px, R); pushXYZ(out, y, x, R); px = x; py = y; }
}
function ringSegments(polys, R, out, keep) {
  for (const r of polys) for (let i = 0; i + 3 < r.length; i += 2) {
    if (keep && !keep((r[i] + r[i + 2]) / 2, (r[i + 1] + r[i + 3]) / 2)) continue;
    seg(out, r[i], r[i + 1], r[i + 2], r[i + 3], R);
  }
}
function lineMesh(S, pos, color, opacity) {
  const g = new S.THREE.BufferGeometry(); g.setAttribute("position", new S.THREE.Float32BufferAttribute(pos, 3));
  const m = new S.THREE.LineSegments(g, new S.THREE.LineBasicMaterial({ color, transparent: true, opacity, depthWrite: false }));
  m.renderOrder = 3; m.frustumCulled = false; return m;
}
function disposeMesh(S, m) { if (!m) return; S.globe.remove(m); m.geometry.dispose(); m.material.dispose(); }
function buildBorders(S) {
  const pos = []; for (const c of S.geo.countries) ringSegments(c.polys, 1.0055, pos);
  S.borders = lineMesh(S, pos, 0xdfe8f5, 0.4); S.borders.visible = S.showGeo; S.globe.add(S.borders);
}
function inRings(polys, lon, lat) {
  let c = false;
  for (const r of polys) for (let i = 0, n = r.length, j = n - 2; i < n; j = i, i += 2) {
    const yi = r[i + 1], yj = r[j + 1];
    if ((yi > lat) !== (yj > lat) && lon < (r[j] - r[i]) * (lat - yi) / (yj - yi) + r[i]) c = !c;
  }
  return c;
}
const inBox = (b, lon, lat) => lon >= b[0] && lon <= b[2] && lat >= b[1] && lat <= b[3];
function ringDistance(polys, lon, lat) {
  let best = Infinity;
  for (const r of polys) for (let i = 0; i + 3 < r.length; i += 2) {
    const x1 = r[i], y1 = r[i + 1], dx = r[i + 2] - x1, dy = r[i + 3] - y1;
    const t = Math.max(0, Math.min(1, ((lon - x1) * dx + (lat - y1) * dy) / Math.max(dx * dx + dy * dy, 1e-12)));
    best = Math.min(best, Math.hypot(x1 + t * dx - lon, y1 + t * dy - lat));
  }
  return best;
}
// The country under a point; simplified outlines can cut off a coastal point, so within 0.15° of an outline the
// nearest country counts.
function countryAt(S, lon, lat) {
  if (!S.geo) return null;
  let near = null, nd = 0.15;
  for (const c of S.geo.countries) {
    const b = c.bbox; if (lon < b[0] - 0.15 || lon > b[2] + 0.15 || lat < b[1] - 0.15 || lat > b[3] + 0.15) continue;
    if (inRings(c.polys, lon, lat)) return c;
    const d = ringDistance(c.polys, lon, lat); if (d < nd) { nd = d; near = c; }
  }
  return near;
}
function unitAt(S, c, lon, lat) {
  const d = S.geo && S.geo.adm1.get(c.a3); if (!d) return null;
  let near = null, nd = 0.1;
  for (const u of d.units) {
    const b = u.bbox; if (lon < b[0] - 0.1 || lon > b[2] + 0.1 || lat < b[1] - 0.1 || lat > b[3] + 0.1) continue;
    if (inRings(u.polys, lon, lat) && (!u.clip || inRings(c.polys, lon, lat))) return u;
    const dd = ringDistance(u.polys, lon, lat); if (dd < nd) { nd = dd; near = u; }
  }
  return near;
}
// Divisions of one country, fetched when that country is explored; at most 8 kept (oldest dropped first).
function loadAdm1(S, a3) {
  const G = S.geo; if (!G) return Promise.resolve(null);
  if (G.adm1.has(a3)) { const d = G.adm1.get(a3); G.adm1.delete(a3); G.adm1.set(a3, d); return Promise.resolve(d); }
  if (G.loading.has(a3)) return G.loading.get(a3);
  const p = fetch(new URL(`${GEO_URL}/admin1/${a3}.json`, document.baseURI)).then((r) => (r.ok ? r.json() : null)).then((d) => {
    G.loading.delete(a3); if (!d || S.dead) return null;
    const pos = []; for (const l of d.lines) for (let i = 0; i + 3 < l.length; i += 2) seg(pos, l[i], l[i + 1], l[i + 2], l[i + 3], 1.0058);
    d.mesh = lineMesh(S, pos, 0xc9d8ee, 0.36); d.mesh.visible = false; S.globe.add(d.mesh);
    for (const u of d.units) u.v = toVec(S.THREE, u.lat, u.lon, 1);
    G.adm1.set(a3, d);
    for (const [k, old] of G.adm1) { if (G.adm1.size <= 8) break; if (S.sel && S.sel.a3 === k) continue; disposeMesh(S, old.mesh); G.adm1.delete(k); }
    S.geoDirty = true; requestRender(S); return d;
  }).catch(() => { G.loading.delete(a3); return null; });
  G.loading.set(a3, p); return p;
}
function pixelsPerDegree(S) {
  const t = Math.tan(S.camera.fov * Math.PI / 360);
  return (S.view.h / 2) / (t * Math.max(0.02, S.dist - 1)) * Math.PI / 180;
}
// The region under a point: a state/province when zoomed in far enough (or `deep`), else the country.
async function regionAt(S, lon, lat, deep) {
  const c = countryAt(S, lon, lat); if (!c) return null;
  if (c.adm1 && (deep || pixelsPerDegree(S) >= STATE_PPD)) {
    await loadAdm1(S, c.a3);
    const u = unitAt(S, c, lon, lat);
    if (u) return { kind: "admin1", a3: c.a3, id: u.id, name: u.name, f: u, c };
  }
  return { kind: "country", a3: c.a3, id: c.a3, name: c.name, f: c, c };
}
function hoverAt(S, e) {
  const now = performance.now(); if (!S.geo || !S.showGeo || now - (S.hoverT || 0) < 60) return; S.hoverT = now;
  const THREE = S.THREE, rect = S.renderer.domElement.getBoundingClientRect();
  const ray = new THREE.Raycaster(); ray.setFromCamera(new THREE.Vector2(((e.clientX - rect.left) / rect.width) * 2 - 1, -((e.clientY - rect.top) / rect.height) * 2 + 1), S.camera);
  const hit = ray.intersectObject(S.earthMesh, false)[0], tag = S.el(".czg-tag");
  let r = null;
  if (hit) {
    const { lat, lon } = toLatLon(S.globe.worldToLocal(hit.point.clone())), c = countryAt(S, lon, lat);
    if (c) {
      const u = pixelsPerDegree(S) >= STATE_PPD ? unitAt(S, c, lon, lat) : null;
      r = u ? { kind: "admin1", a3: c.a3, id: u.id, name: u.name, f: u, c, type: u.type } : { kind: "country", a3: c.a3, id: c.a3, name: c.name, f: c, c };
    }
  }
  if (!sameRegion(r, S.hover)) { S.hover = r; drawHighlight(S); }
  if (r) { tag.textContent = r.name + (r.type ? ` · ${r.type}` : ""); tag.style.transform = `translate(${e.clientX - rect.left + 14}px, ${e.clientY - rect.top + 12}px)`; tag.classList.add("show"); }
  else tag.classList.remove("show");
}
const sameRegion = (a, b) => (!a && !b) || (a && b && a.kind === b.kind && a.id === b.id && a.name === b.name);
function regionInfo(S) {
  const s = S.sel; if (!s) return null;
  const f = S.frames && S.frames[S.frameIdx];
  return { kind: s.kind, a3: s.a3 || null, id: s.id || null, name: s.name, fhr: f ? f.fhr : null };
}
function setRegion(S, r, opt) {
  if (!sameRegion(r, S.sel)) S.selMask = null;
  S.sel = r;
  disposeMesh(S, S.selLine); S.selLine = null;
  if (r && r.f && r.f.polys) {
    const pos = [], c = r.c, keep = r.kind === "admin1" && r.f.clip ? (x, y) => inRings(c.polys, x, y) : null;
    ringSegments(r.f.polys, 1.0075, pos, keep);
    S.selLine = lineMesh(S, pos, 0xf2a93b, 0.95); S.selLine.renderOrder = 4; S.globe.add(S.selLine);
  }
  drawHighlight(S); crumbs(S); readout(S); S.geoDirty = true; requestRender(S);
  if (opt && opt.notify && S.trigger) S.trigger("region", { region: regionInfo(S), t: Date.now() });
}
function buildHighlight(S) {
  S.hl = document.createElement("canvas"); S.hl.width = 2048; S.hl.height = 1024;
  S.hlTex = new S.THREE.CanvasTexture(S.hl); S.hlTex.colorSpace = S.THREE.SRGBColorSpace;
  const m = new S.THREE.Mesh(new S.THREE.SphereGeometry(1.0045, 128, 64), new S.THREE.MeshBasicMaterial({ map: S.hlTex, transparent: true, depthWrite: false }));
  m.renderOrder = 2; S.globe.add(m);
}
// Hover and selection fills, painted in longitude/latitude (which is how the sphere is textured). A division
// marked "clip" is limited to its own country's outline.
function drawHighlight(S) {
  const ctx = S.hl.getContext("2d"), W = S.hl.width, H = S.hl.height;
  ctx.clearRect(0, 0, W, H);
  const path = (polys) => {
    const p = new Path2D();
    for (const r of polys) { for (let i = 0; i < r.length; i += 2) { const x = (r[i] + 180) / 360 * W, y = (90 - r[i + 1]) / 180 * H; if (i) p.lineTo(x, y); else p.moveTo(x, y); } p.closePath(); }
    return p;
  };
  const fill = (g, color) => {
    if (!g || !g.f || !g.f.polys) return;
    ctx.save(); if (g.kind === "admin1" && g.f.clip) ctx.clip(path(g.c.polys), "evenodd");
    ctx.fillStyle = color; ctx.fill(path(g.f.polys), "evenodd"); ctx.restore();
  };
  if (S.hover && !sameRegion(S.hover, S.sel)) fill(S.hover, "rgba(255,255,255,0.12)");
  fill(S.sel, "rgba(242,169,59,0.16)");
  S.hlTex.needsUpdate = true; requestRender(S);
}

// Labels: continents when far away, then countries, then states/provinces and cities as the camera comes
// closer. Each label must fit on screen without overlapping a more important one; labels on the far side or
// at the horizon are hidden. Recomputed only when the camera or the data changes.
function updateGeo(S) {
  const key = `${S.rot.x.toFixed(4)}|${S.rot.y.toFixed(4)}|${S.dist.toFixed(4)}|${S.view.w}|${S.view.h}|${S.view.offY}`;
  if (key === S.geoKey && !S.geoDirty) return;
  S.geoKey = key; S.geoDirty = false;
  const p = pixelsPerDegree(S), deep = S.showGeo && p >= STATE_PPD * 0.8;
  for (const [, d] of S.geo.adm1) d.mesh.visible = deep;
  if (deep) {
    const f = toLatLon(currentFacing(S)), c = countryAt(S, f.lon, f.lat);
    if (c && c.adm1 && !S.geo.adm1.has(c.a3)) loadAdm1(S, c.a3);
  }
  labels(S, p);
}
function labels(S, p) {
  const box = S.el(".czg-labels"); S.cityHits = [];
  if (!S.showGeo) { box.replaceChildren(); S.labelEls = new Map(); return; }
  const THREE = S.THREE, q = S.globe.quaternion, w = S.view.w, h = S.view.h, horizon = 1 / S.dist;
  const face = currentFacing(S), cap = horizon - 0.02;          // cos of the visible cap's angular radius (approx.)
  const v = new THREE.Vector3(), cands = [];
  const add = (key, text, local, cls, pri, extra, r) => cands.push({ key, text, local, cls, pri, extra, r });
  const sel = S.sel;
  if (p < 11) for (const c of S.geo.conts) add("k:" + c.name, c.name, c.v, "cont", 0);
  if (p >= 4) for (const c of S.geo.countries) {
    if (c.r * p > 22 || (sel && sel.a3 === c.a3)) add("c:" + c.a3, c.name, c.v, "ctry" + (sel && sel.kind === "country" && sel.a3 === c.a3 ? " sel" : ""), 10 + c.rank, null, c.r);
  }
  if (p >= STATE_PPD) for (const [, d] of S.geo.adm1) for (const u of d.units) {
    if (u.r * p > 14 || (sel && sel.id === u.id)) add("a:" + u.id, u.name, u.v, "adm" + (sel && sel.id === u.id ? " sel" : ""), 16 + u.rank, null, u.r);
  }
  if (S.geo.places && p >= 7) {
    const maxSr = p < 12 ? 1 : p < 22 ? 3 : p < 40 ? 5 : p < 70 ? 7 : 10, P = S.geo.places, V = S.geo.placeV;
    for (let i = 0; i < P.length && P[i][3] <= maxSr; i++) {
      if (V[i * 3] * face.x + V[i * 3 + 1] * face.y + V[i * 3 + 2] * face.z < cap) continue;   // far side
      add("p:" + i, P[i][0], new THREE.Vector3(V[i * 3], V[i * 3 + 1], V[i * 3 + 2]), "city" + (P[i][7] === 2 ? " cap" : ""), 19 + P[i][3] * 3, P[i]);
    }
  }
  cands.sort((a, b) => a.pri - b.pri);
  const placed = [], shown = new Map(), names = new Set(), CH = { cont: 12.5, ctry: 7.4, adm: 6.4, city: 6.2 };
  for (const c of cands) {
    if (shown.size >= 110) break;
    v.copy(c.local).applyQuaternion(q);
    if (v.z < horizon + 0.03) continue;                                       // behind the globe or at the edge
    v.project(S.camera);
    const x = (v.x + 1) / 2 * w, y = (1 - v.y) / 2 * h;
    const kind = c.cls.split(" ")[0], bw = c.text.length * CH[kind] + (kind === "city" ? 16 : 10), bh = kind === "cont" ? 20 : 16;
    const b = kind === "city" ? [x - 5, y - bh / 2, x - 5 + bw, y + bh / 2] : [x - bw / 2, y - bh / 2, x + bw / 2, y + bh / 2];
    if (b[0] < 2 || b[2] > w - 2 || b[1] < 2 || b[3] > h - 2) continue;      // the whole label must be in view
    if (c.r && !c.cls.includes("sel") && 2 * c.r * p < 0.6 * bw) continue;   // a region's name only once it's about as wide
    if (placed.some((o) => b[0] < o[2] + 2 && b[2] + 2 > o[0] && b[1] < o[3] + 1 && b[3] + 1 > o[1])) continue;
    if (kind === "city" && names.has(c.text)) continue;                   // e.g. Puducherry the city and the territory
    placed.push(b); names.add(c.text);
    const zc = c.local.clone().applyQuaternion(q).z, fade = Math.max(0, Math.min(1, (zc - horizon) / 0.12));
    shown.set(c.key, { c, x, y, fade, kind });
    if (kind === "city") S.cityHits.push({ x, y, name: c.text, lat: c.extra[1], lon: c.extra[2] });
  }
  const els = S.labelEls || (S.labelEls = new Map());
  for (const [k, el] of els) if (!shown.has(k)) { el.remove(); els.delete(k); }
  for (const [k, s] of shown) {
    let el = els.get(k);
    if (!el) { el = document.createElement("span"); el.textContent = s.c.text; box.appendChild(el); els.set(k, el); }
    el.className = "lb " + s.c.cls;
    el.style.transform = s.kind === "city" ? `translate(${s.x - 4}px, ${s.y}px) translateY(-50%)` : `translate(${s.x}px, ${s.y}px) translate(-50%, -50%)`;
    el.style.opacity = String(s.fade);
  }
}

// Camera distance that frames a region of angular radius `r` (degrees) in the free part of the view.
function distFor(S, r) {
  const t = Math.tan(S.camera.fov * Math.PI / 360), side = Math.min(S.view.w, S.view.freeH), R = Math.min(r, 80) * Math.PI / 180;
  const d = Math.cos(R) + Math.sin(R) * (S.view.h / 2) / (t * 0.42 * side);
  return Math.max(S.minDist + 0.04, Math.min(fitDist(S), d));
}
function flyToRegion(S, r) {
  if (!r) return resetView(S);
  if (r.kind === "continent") return flyTo(S, r.f.lon, r.f.lat, distFor(S, r.f.r));
  flyTo(S, r.f.lon, r.f.lat, distFor(S, Math.max(r.f.r, 0.6)));
}
// A place or region chosen in the page (search, saved place, region browser).
async function applyFocus(S, f) {
  const G = S.geo;
  if (f.kind === "continent") {
    const k = G.conts.find((c) => c.name === f.name);
    if (k) { S.city = null; clearPoint(S); const r = { kind: "continent", id: k.name, name: k.name, f: k }; setRegion(S, r); flyToRegion(S, r); }
    return;
  }
  if (f.kind === "country" || f.kind === "admin1") {
    const c = G.countries.find((x) => x.a3 === f.a3); if (!c) return;
    let r = { kind: "country", a3: c.a3, id: c.a3, name: c.name, f: c, c };
    if (f.kind === "admin1") {
      const d = await loadAdm1(S, c.a3), u = d && d.units.find((x) => x.id === f.id);
      if (u) r = { kind: "admin1", a3: c.a3, id: u.id, name: u.name, f: u, c };
    }
    S.city = null; clearPoint(S); setRegion(S, r); flyToRegion(S, r);
    return;
  }
  // A city or a coordinate: select the point, then the state/province (or country) it lies in.
  const lat = +f.lat, lon = +f.lon;
  selectPoint(S, lat, lon);
  S.city = f.kind === "city" || f.kind === undefined ? { name: f.name || (f.label || "").split(",")[0], lat, lon } : null;
  flyTo(S, lon, lat, Math.max(S.minDist + 0.05, Math.min(S.dist, 1.45)));
  setRegion(S, await regionAt(S, lon, lat, true));
}

// Breadcrumb: Earth › continent › country › state/province › city. Every step above the current one is a
// button that flies back to that level.
function crumbList(S) {
  const s = S.sel, out = [{ kind: "earth", name: "Earth" }];
  const c = s && s.c ? s.c : null;
  if (s && s.kind === "continent") out.push({ kind: "continent", name: s.name, f: s.f });
  if (c) {
    const k = S.geo.conts.find((x) => x.name === c.cont);
    if (k) out.push({ kind: "continent", name: k.name, f: k });
    out.push({ kind: "country", a3: c.a3, id: c.a3, name: c.name, f: c, c });
    if (s.kind === "admin1") out.push({ kind: "admin1", a3: c.a3, id: s.id, name: s.name, f: s.f, c });
  }
  if (S.city) out.push({ kind: "city", name: S.city.name, lat: S.city.lat, lon: S.city.lon });
  return out;
}
function crumbs(S) {
  const nav = S.el(".czg-crumbs"); if (!nav) return;
  if (!S.geo) { nav.innerHTML = S.geoError ? `<span class="err">${esc(S.geoError)}</span>` : ""; return; }
  const list = crumbList(S); S.crumbs = list;
  nav.innerHTML = list.map((it, i) => i === list.length - 1
    ? `<span class="cur" aria-current="location">${esc(it.name)}</span>`
    : `<button type="button" class="cr" data-act="crumb" data-i="${i}" title="Go to ${esc(it.name)}">${esc(it.name)}</button><span class="sep" aria-hidden="true">›</span>`).join("");
  S.el('[data-act="up"]').disabled = list.length < 2;
}
function crumbTo(S, i) {
  const it = S.crumbs && S.crumbs[i]; if (!it) return;
  if (it.kind === "earth") { S.city = null; resetView(S); return setRegion(S, null, { notify: true }); }
  if (it.kind === "city") return flyTo(S, it.lon, it.lat, Math.min(S.dist, 1.45));
  S.city = null;
  const r = it.kind === "continent" ? { kind: "continent", id: it.name, name: it.name, f: it.f } : it;
  setRegion(S, r, { notify: true }); flyToRegion(S, r);
}
function goUp(S) { if (S.crumbs && S.crumbs.length > 1) crumbTo(S, S.crumbs.length - 2); }

// Regional statistics for the shown layer and step: area-weighted (cos latitude) over the 1° grid cells whose
// centres lie inside the selected country or state (and inside its country, for a "clip" division).
function regionMask(S) {
  const s = S.sel; if (!s || !s.f || !s.f.polys) return null;
  if (S.selMask && S.selMask.key === s.kind + s.id) return S.selMask;
  const [x0, y0, x1, y1] = s.f.bbox, idx = [], wts = [];
  for (let lat = Math.ceil(y0); lat <= Math.floor(y1); lat++) for (let lon = Math.ceil(x0); lon <= Math.floor(x1); lon++) {
    if (!inRings(s.f.polys, lon, lat) || (s.kind === "admin1" && !inRings(s.c.polys, lon, lat))) continue;
    idx.push((90 - lat) * 360 + ((lon % 360) + 360) % 360); wts.push(Math.cos(lat * Math.PI / 180));
  }
  S.selMask = { key: s.kind + s.id, idx, wts, n: idx.length };
  return S.selMask;
}
function regionStats(S) {
  const s = S.sel; if (!s || s.kind === "continent") return s ? `<b>${esc(s.name)}</b>` : "";
  const m = regionMask(S), L_ = S.layer, f = S.fields || {}, u = S.units || {};
  const head = `<b>${esc(s.name)}</b>${s.kind === "admin1" && s.f.type ? ` <span class="mut">${esc(s.f.type)}</span>` : ""}`;
  if (!m) return head;
  if (m.n < MIN_REGION_CELLS) return `${head}<div class="mut">Smaller than the 1° forecast grid (${m.n} cell${m.n === 1 ? "" : "s"} inside), so no regional value; click a point for its grid value.</div>`;
  if (L_ === "none") return `${head}<div class="mut">${m.n} grid cells. Choose a weather layer for regional values.</div>`;
  const agg = (g, fn) => {               // area-weighted mean, min, max over finite values; fn maps cell → value
    let sw = 0, sv = 0, mn = Infinity, mx = -Infinity, k = 0;
    for (let i = 0; i < m.n; i++) { const x = fn(m.idx[i]); if (!isFinite(x)) continue; sw += m.wts[i]; sv += m.wts[i] * x; mn = Math.min(mn, x); mx = Math.max(mx, x); k++; }
    return k ? { mean: sv / sw, min: mn, max: mx, k, sw } : null;
  };
  const frac = (fn) => { let sw = 0, sy = 0; for (let i = 0; i < m.n; i++) { const x = fn(m.idx[i]); if (x === null) continue; sw += m.wts[i]; if (x) sy += m.wts[i]; } return sw ? sy / sw : NaN; };
  const one = (v, d = 0) => v.toFixed(d);
  let txt = "";
  if (L_ === "temperature" && f.t2m) { const a = agg(f, (i) => f.t2m[i]), tu = u.temperature || "°C"; if (a) txt = `average ${one(fmtTemp(a.mean, tu), 1)} ${tu} (${one(fmtTemp(a.min, tu))} to ${one(fmtTemp(a.max, tu))})`; }
  else if (L_ === "wind" && f.u10) { const a = agg(f, (i) => Math.hypot(f.u10[i], f.v10[i])), wu = u.wind || "km/h"; if (a) txt = `average ${one(fmtWind(a.mean, wu))} ${wu}, strongest ${one(fmtWind(a.max, wu))} ${wu}`; }
  else if (L_ === "gusts" && f.gust) { const a = agg(f, (i) => f.gust[i]), wu = u.wind || "km/h"; if (a) txt = `average gusts ${one(fmtWind(a.mean, wu))} ${wu}, strongest ${one(fmtWind(a.max, wu))} ${wu}`; }
  else if (L_ === "precipitation" && f.prate) { const a = agg(f, (i) => f.prate[i]), pc = frac((i) => (isFinite(f.prate[i]) ? f.prate[i] >= 0.1 : null)), pu = u.precipitation === "in" ? "in/h" : "mm/h"; if (a) txt = `precipitation over ${one(pc * 100)}% of the area, heaviest ${fmtRate(a.max, u.precipitation).toFixed(u.precipitation === "in" ? 2 : 1)} ${pu}`; }
  else if (L_ === "clouds" && f.tcc) { const a = agg(f, (i) => f.tcc[i]); if (a) txt = `average cloud cover ${one(a.mean)}%`; }
  else if (L_ === "pressure" && f.prmsl) { const a = agg(f, (i) => f.prmsl[i]); if (a) txt = `average ${one(a.mean, 1)} hPa (${one(a.min)} to ${one(a.max)})`; }
  else if (L_ === "humidity" && f.rh2m) { const a = agg(f, (i) => f.rh2m[i]); if (a) txt = `average humidity ${one(a.mean)}%`; }
  else if (L_ === "cape" && f.cape) { const a = agg(f, (i) => f.cape[i]); if (a) txt = `average ${one(a.mean)} J/kg, highest ${one(a.max)} J/kg`; }
  else if (L_ === "snowdepth" && f.snod) { const a = agg(f, (i) => f.snod[i]), sc = frac((i) => (isFinite(f.snod[i]) ? f.snod[i] >= 0.5 : null)); if (a) txt = `snow on ${one(sc * 100)}% of the area, deepest ${one(a.max)} cm`; }
  else if (L_ === "waves" && f.swh) { const a = agg(f, (i) => f.swh[i]); txt = a ? `sea cells only (${a.k}): average ${one(a.mean, 1)} m, highest ${one(a.max, 1)} m` : "no sea cells inside"; }
  return `${head}<div>${txt || "not available at this step"}</div><div class="mut">GFS: area-weighted over the ${m.n} grid cells (1°) whose centres are inside, at the selected step</div>`;
}
"""

CSS = r"""
.czg,.czg *{box-sizing:border-box}
.czg{position:relative;height:clamp(520px,calc(100vh - 96px),1200px);border-radius:22px;overflow:hidden;
  background:radial-gradient(ellipse at 50% 45%,#0a1530 0%,#050b1c 50%,#03070f 100%);border:1px solid rgba(140,160,195,.18);
  box-shadow:0 2px 8px rgba(2,6,18,.4),0 24px 60px rgba(2,6,18,.45);color:#eaf0fa;font-family:inherit;
  container-type:inline-size}
.czg:fullscreen{height:100vh;border-radius:0;border:0}
.czg.expanded{position:fixed;inset:0;z-index:999990;height:100vh;height:100dvh;border-radius:0;border:0}
.czg-stage{position:absolute;inset:0}
.czg-stage:focus-visible{outline:3px solid #4aa3ff;outline-offset:-3px}
.czg-stage canvas{display:block}
.ico{font-family:"Material Symbols Rounded";font-weight:normal;font-style:normal;font-size:20px;line-height:1;
  display:inline-block;-webkit-font-smoothing:antialiased;font-feature-settings:"liga";vertical-align:middle}
.mut{opacity:.72;font-weight:400}
.czg-loading{position:absolute;inset:0;z-index:7;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:14px;
  font-size:.9rem;color:#cfdcf0;background:radial-gradient(ellipse at 50% 45%,#0a1530 0%,#03070f 70%)}
.czg-loading[hidden]{display:none}
.czg-loading .spin{width:38px;height:38px;border-radius:50%;border:3px solid rgba(74,163,255,.25);border-top-color:#4aa3ff;animation:czg-spin 1s linear infinite}
@keyframes czg-spin{to{transform:rotate(360deg)}}
.czg-top{position:absolute;top:10px;left:10px;right:56px;z-index:5;display:flex;pointer-events:none}
.czg-layers{display:flex;gap:4px;padding:4px;border-radius:14px;pointer-events:auto;background:rgba(6,13,32,.78);
  border:1px solid rgba(120,150,200,.22);backdrop-filter:blur(6px);-webkit-backdrop-filter:blur(6px)}
.czg-layer,.czg-opt{display:flex;align-items:center;gap:6px;white-space:nowrap;border:1px solid transparent;border-radius:10px;
  padding:6px 10px;background:transparent;color:#d6e2f5;font:inherit;font-size:.84rem;cursor:pointer;
  transition:background .15s ease,border-color .15s ease}
.czg-layer:hover,.czg-opt:hover{background:rgba(74,163,255,.14)}
.czg-layer[aria-pressed="true"],.czg-layer.more.on,.czg-opt[aria-pressed="true"]{background:linear-gradient(180deg,rgba(74,163,255,.32),rgba(74,163,255,.14));
  border-color:rgba(74,163,255,.8);color:#fff;box-shadow:0 0 0 1px rgba(74,163,255,.35),inset 0 -2px 0 #f2a93b}
.czg-layer .ico,.czg-opt .ico{font-size:18px}
.czg-layer .na{display:none}
.czg-layer:disabled,.czg-opt:disabled{opacity:.4;cursor:not-allowed}
.czg button:focus-visible,.czg input:focus-visible{outline:3px solid #4aa3ff;outline-offset:2px}
.czg-side{position:absolute;right:10px;top:10px;z-index:5;display:flex;flex-direction:column;gap:6px}
.czg-btn{display:inline-flex;align-items:center;justify-content:center;min-width:36px;height:36px;border-radius:10px;
  border:1px solid rgba(120,150,200,.28);background:rgba(6,13,32,.8);color:#e9f1fc;cursor:pointer;font:inherit;padding:0 8px}
.czg-btn:hover{background:rgba(74,163,255,.2)}
.czg-btn:disabled{opacity:.4;cursor:not-allowed}
.czg-btn.now{font-size:.8rem;font-weight:600}
.czg-btn[aria-pressed="true"]{border-color:rgba(242,169,59,.85);color:#ffd58f;box-shadow:inset 0 0 0 1px rgba(242,169,59,.35)}
.czg-panel{position:absolute;top:10px;right:56px;bottom:10px;z-index:8;width:min(300px,calc(100% - 76px));overflow:auto;
  padding:10px 12px 14px;border-radius:16px;background:rgba(6,13,32,.94);border:1px solid rgba(120,150,200,.28);
  box-shadow:0 18px 50px rgba(0,0,0,.45);font-size:.84rem;overscroll-behavior:contain}
.czg-panel[hidden]{display:none}
.czg-panel .ph{display:flex;align-items:center;justify-content:space-between;font-family:Outfit,Inter,sans-serif;font-size:1rem;margin-bottom:4px}
.czg-panel .pg{margin-top:10px}
.czg-panel .pt{font-size:.7rem;letter-spacing:.08em;text-transform:uppercase;opacity:.65;margin:0 0 4px 2px}
.czg-opt{width:100%;justify-content:flex-start;padding:7px 8px}
.czg-opt .na{margin-left:auto;font-size:.72rem;opacity:.7}
.czg-panel .tog{display:flex;gap:8px;align-items:flex-start;padding:5px 2px;cursor:pointer;line-height:1.35}
.czg-panel .tog input{accent-color:#f2a93b;margin-top:2px}
.czg-about p{margin:0 0 6px;opacity:.85;line-height:1.4}
.czg-legend{position:absolute;left:10px;bottom:104px;z-index:5;width:min(290px,calc(100% - 20px));padding:6px 10px 8px;border-radius:12px;
  background:rgba(6,13,32,.8);border:1px solid rgba(120,150,200,.22);font-size:.78rem;backdrop-filter:blur(6px);-webkit-backdrop-filter:blur(6px)}
.czg-legend.collapsed{width:auto;max-width:calc(100% - 20px);padding-bottom:6px}
.czg-legend.collapsed .lg-b{display:none}
.lg-h{display:flex;align-items:center;gap:6px;width:100%;white-space:nowrap;padding:2px 0;margin:0 0 6px;border:0;background:none;color:inherit;
  font-family:Outfit,Inter,sans-serif;font-size:.9rem;cursor:pointer;text-align:left}
.czg-legend.collapsed .lg-h{margin:0}
.lg-h .kind{margin-left:auto;font-size:.68rem;padding:1px 7px;border-radius:999px;border:1px solid rgba(74,163,255,.6);color:#9ccaff}
.lg-h .chev{font-size:18px;opacity:.7}
.czg-legend .bar{height:9px;border-radius:5px;border:1px solid rgba(255,255,255,.15)}
.czg-legend .ticks{display:flex;justify-content:space-between;margin-top:3px;opacity:.85;font-variant-numeric:tabular-nums}
.czg-legend .unit{text-align:right;opacity:.7}
.czg-legend .note{opacity:.75;margin-top:2px}
.czg-legend .types{display:flex;flex-wrap:wrap;gap:10px;margin-top:6px}
.czg-legend .types i{display:inline-block;width:10px;height:10px;border-radius:3px;margin-right:4px;vertical-align:-1px}
.czg-readout{position:absolute;right:10px;bottom:104px;z-index:5;max-width:min(320px,calc(100% - 20px));padding:7px 11px;border-radius:12px;
  background:rgba(6,13,32,.8);border:1px solid rgba(120,150,200,.22);font-size:.78rem;backdrop-filter:blur(6px);-webkit-backdrop-filter:blur(6px)}
.czg-time{position:absolute;left:10px;right:10px;bottom:20px;z-index:5;display:flex;flex-wrap:wrap;align-items:center;gap:6px;
  padding:6px 10px;border-radius:14px;background:rgba(6,13,32,.82);border:1px solid rgba(120,150,200,.22);
  backdrop-filter:blur(6px);-webkit-backdrop-filter:blur(6px)}
.czg-slider{flex:1;min-width:80px;accent-color:#f2a93b;height:24px;cursor:pointer}
.czg-tl-label{flex-basis:100%;font-size:.76rem;font-variant-numeric:tabular-nums;line-height:1.35}
.czg-tl-label b{font-family:Outfit,Inter,sans-serif;font-size:.98rem;font-weight:500}
.czg-status{position:absolute;left:50%;top:96px;transform:translateX(-50%);z-index:6;max-width:calc(100% - 40px);padding:7px 14px;
  border-radius:999px;background:rgba(6,13,32,.92);border:1px solid rgba(74,163,255,.5);font-size:.8rem;display:none;text-align:center}
.czg-status.show{display:block}
.czg-status.error{border-color:#e2566a}
.czg-status.warn{border-color:#e39a1c}
.czg-attr{position:absolute;right:8px;bottom:3px;z-index:5;font-size:.64rem;opacity:.7;max-width:calc(100% - 16px);
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.czg-labels{position:absolute;inset:0;z-index:3;pointer-events:none;overflow:hidden}
.lb{position:absolute;left:0;top:0;white-space:nowrap;color:#f2f6fc;font-size:11px;line-height:1.1;will-change:transform;
  text-shadow:0 0 3px #000,0 0 7px rgba(0,0,0,.85),0 1px 2px #000;transition:opacity .2s}
.lb.cont{font-family:Outfit,Inter,sans-serif;font-size:15px;font-weight:300;letter-spacing:.22em;text-transform:uppercase;color:rgba(235,242,252,.92)}
.lb.ctry{font-size:12.5px;font-weight:600;letter-spacing:.02em}
.lb.adm{font-size:11px;font-weight:500;font-style:italic;color:#d9e6f7}
.lb.city{font-size:10.5px;padding-left:9px;color:#eef3fa}
.lb.city::before{content:"";position:absolute;left:0;top:50%;width:5px;height:5px;margin-top:-2.5px;border-radius:50%;
  background:#fff;box-shadow:0 0 0 1.5px rgba(0,0,0,.6)}
.lb.city.cap::before{background:#f2a93b}
.lb.sel{color:#ffd58f}
.czg-tag{position:absolute;left:0;top:0;z-index:6;pointer-events:none;padding:3px 8px;border-radius:8px;white-space:nowrap;
  background:rgba(6,13,32,.9);border:1px solid rgba(120,150,200,.3);font-size:.75rem;opacity:0;transition:opacity .12s}
.czg-tag.show{opacity:1}
.czg-crumbs{position:absolute;top:58px;left:10px;z-index:5;max-width:calc(100% - 76px);display:flex;flex-wrap:wrap;align-items:center;
  gap:2px 3px;padding:3px 8px;border-radius:10px;background:rgba(6,13,32,.74);border:1px solid rgba(120,150,200,.2);font-size:.78rem;
  backdrop-filter:blur(6px);-webkit-backdrop-filter:blur(6px)}
.czg-crumbs:empty{display:none}
.czg-crumbs .cr{background:none;border:0;color:#9ccaff;font:inherit;cursor:pointer;padding:2px 3px;border-radius:6px}
.czg-crumbs .cr:hover{text-decoration:underline}
.czg-crumbs .cur{color:#fff;font-weight:600;padding:2px 3px}
.czg-crumbs .sep{opacity:.5}
.czg-crumbs .err{color:#ffb4bf}
.czg-readout .rs{margin-bottom:5px;padding-bottom:5px;border-bottom:1px solid rgba(120,150,200,.22)}
@media (prefers-reduced-motion:reduce){.lb,.czg-tag{transition:none}}
/* Layer names need about 820px; narrower frames show icons only (names stay in title, the legend and the panel). */
@container (max-width:860px){.czg-layer .t{display:none}.czg-layer.more.on .t{display:inline}}
@container (max-width:640px){
  .czg-panel{top:auto;left:0;right:0;bottom:0;width:auto;max-height:72%;border-radius:18px 18px 0 0}
  .czg-layer.more.on .t{display:none}
}
@media (max-width:640px){
  .czg{height:clamp(440px,calc(100vh - 120px),820px);border-radius:14px}
  .czg:fullscreen,.czg.expanded{border-radius:0}
  .czg-readout{display:none}
  .czg-legend{bottom:118px;width:calc(100% - 20px)}
  .czg-legend .note{display:none}
}
@media (max-width:640px){.czg{height:clamp(440px,calc(100dvh - 150px),820px)}}
@media (max-height:500px) and (orientation:landscape){.czg{height:max(300px,calc(100vh - 70px));height:max(300px,calc(100dvh - 70px))}}
@media (pointer:coarse){.czg-legend .lg-h{min-height:40px}.czg-slider{height:36px}}
@media (prefers-reduced-motion:reduce){.czg-layer,.czg-opt{transition:none}.czg-loading .spin{animation:none}}
"""

_component = None


def globe(data: dict, key: str = "cloudzy_globe"):
    """Mount the globe. `.picked` → {lat, lon, place, region} after a click; `.region` → {region} after breadcrumb
    or reset navigation; `.fallback` → {reason} if 3D can't run.

    `data`: manifest URL, units, the Weather page's place (`city`) and an optional `focus`
    {lat, lon, label, token}: a place chosen with the page's search, flown to once per token."""
    global _component
    import streamlit as st
    if _component is None:
        _component = st.components.v2.component("cloudzy_globe", js=JS, css=CSS)
    return _component(key=key, data=data, on_picked_change=lambda: None, on_fallback_change=lambda: None,
                       on_region_change=lambda: None)
