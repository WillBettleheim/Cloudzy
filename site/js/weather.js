// Cloudzy static edition: the forecast for a place, fetched in the visitor's browser from Open-Meteo (free,
// key-less, CORS-enabled). Nothing is computed by Cloudzy Core here; every value is the provider's forecast.
// All text from the APIs is inserted with textContent, never as HTML.

const API = "https://api.open-meteo.com/v1/forecast";
const GEO = "https://geocoding-api.open-meteo.com/v1/search";
const AQ = "https://air-quality-api.open-meteo.com/v1/air-quality";
const DEFAULT = { name: "New Delhi", admin1: "Delhi", country: "India", latitude: 28.6139, longitude: 77.209 };
const SAVE_KEY = "cloudzy-static-place";
const $ = (sel) => document.querySelector(sel);
let WMO = {};

function el(tag, attrs, ...children) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (k === "class") n.className = v; else n.setAttribute(k, v);
  }
  for (const c of children) n.append(c instanceof Node ? c : document.createTextNode(String(c)));
  return n;
}

function status(text, kind) {
  const s = $("#wx-status");
  s.textContent = text || "";
  s.className = "status" + (kind ? " " + kind : "");
  s.hidden = !text;
}

async function getJSON(url, params) {
  const u = new URL(url);
  for (const [k, v] of Object.entries(params)) u.searchParams.set(k, v);
  const ctl = new AbortController();
  const t = setTimeout(() => ctl.abort(), 15000);
  try {
    const r = await fetch(u, { signal: ctl.signal, referrerPolicy: "no-referrer" });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    return await r.json();
  } finally { clearTimeout(t); }
}

function placeLabel(p) { return [p.name, p.admin1, p.country].filter(Boolean).join(", "); }

function loadPlace() {
  try { const v = JSON.parse(localStorage.getItem(SAVE_KEY) || "null"); if (v && isFinite(v.latitude)) return v; }
  catch (e) { /* storage blocked: use the default */ }
  return DEFAULT;
}

function savePlace(p) { try { localStorage.setItem(SAVE_KEY, JSON.stringify(p)); } catch (e) { /* ignore */ } }

function fmtTime(iso, opts) { return new Date(iso).toLocaleString([], opts); }

// A dependency-free SVG line chart: temperature for the next 48 hours, with min/max labels.
function chart(times, temps, unit) {
  const W = 720, H = 220, P = { l: 38, r: 10, t: 14, b: 28 };
  const lo = Math.floor(Math.min(...temps)) - 1, hi = Math.ceil(Math.max(...temps)) + 1;
  const x = (i) => P.l + (i / (temps.length - 1)) * (W - P.l - P.r);
  const y = (v) => P.t + (1 - (v - lo) / (hi - lo)) * (H - P.t - P.b);
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", `Temperature for the next 48 hours, from ${Math.min(...temps).toFixed(0)} to ${Math.max(...temps).toFixed(0)} ${unit}`);
  const add = (tag, a, text) => { const n = document.createElementNS(ns, tag); for (const [k, v] of Object.entries(a)) n.setAttribute(k, v); if (text != null) n.textContent = text; svg.append(n); return n; };
  for (let v = Math.ceil(lo); v <= hi; v += Math.max(1, Math.round((hi - lo) / 4))) {
    add("line", { x1: P.l, x2: W - P.r, y1: y(v), y2: y(v), class: "grid" });
    add("text", { x: P.l - 6, y: y(v) + 4, class: "tick", "text-anchor": "end" }, `${v}°`);
  }
  temps.forEach((_, i) => { if (i % 6 === 0) add("text", { x: x(i), y: H - 8, class: "tick", "text-anchor": "middle" }, fmtTime(times[i], { hour: "2-digit", minute: "2-digit" })); });
  const pts = temps.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  add("polyline", { points: pts, class: "line" });
  return svg;
}

function render(place, f, aq) {
  const c = f.current, u = f.current_units;
  $("#wx-place").textContent = placeLabel(place);
  $("#wx-time").textContent = `${fmtTime(c.time, { weekday: "long", hour: "2-digit", minute: "2-digit" })} local time (${f.timezone_abbreviation || f.timezone})`;
  $("#wx-temp").textContent = `${Math.round(c.temperature_2m)}°`;
  $("#wx-cond").textContent = WMO[c.weather_code] || `Weather code ${c.weather_code}`;
  $("#wx-feels").textContent = `Feels like ${Math.round(c.apparent_temperature)}° · humidity ${c.relative_humidity_2m}${u.relative_humidity_2m} · wind ${Math.round(c.wind_speed_10m)} ${u.wind_speed_10m} · precipitation ${c.precipitation} ${u.precipitation}`;

  const now = Date.now();
  let start = f.hourly.time.findIndex((t) => new Date(t).getTime() >= now - 3600000);
  if (start < 0) start = 0;
  const times = f.hourly.time.slice(start, start + 48), temps = f.hourly.temperature_2m.slice(start, start + 48);
  const box = $("#wx-chart");
  box.replaceChildren(times.length > 2 ? chart(times, temps, f.hourly_units.temperature_2m) : el("p", { class: "muted" }, "No hourly data in this response."));

  const tbody = $("#wx-days tbody");
  tbody.replaceChildren(...f.daily.time.map((d, i) => el("tr", {},
    el("th", { scope: "row" }, new Date(d + "T12:00").toLocaleDateString([], { weekday: "short", day: "numeric", month: "short" })),
    el("td", {}, WMO[f.daily.weather_code[i]] || "—"),
    el("td", { class: "num" }, `${Math.round(f.daily.temperature_2m_max[i])}° / ${Math.round(f.daily.temperature_2m_min[i])}°`),
    el("td", { class: "num" }, f.daily.precipitation_probability_max[i] == null ? "—" : `${f.daily.precipitation_probability_max[i]}%`))));

  const aqBox = $("#wx-aq");
  if (aq && aq.current) {
    const a = aq.current;
    aqBox.replaceChildren(
      el("div", { class: "stat" }, el("span", { class: "k" }, "US AQI"), el("span", { class: "v" }, a.us_aqi ?? "—")),
      el("div", { class: "stat" }, el("span", { class: "k" }, "European AQI"), el("span", { class: "v" }, a.european_aqi ?? "—")),
      el("div", { class: "stat" }, el("span", { class: "k" }, "PM2.5 µg/m³"), el("span", { class: "v" }, a.pm2_5 ?? "—")),
      el("div", { class: "stat" }, el("span", { class: "k" }, "PM10 µg/m³"), el("span", { class: "v" }, a.pm10 ?? "—")));
  } else {
    aqBox.replaceChildren(el("p", { class: "muted" }, "Air quality couldn't be loaded for this place right now."));
  }
  $("#wx-result").hidden = false;
}

async function show(place) {
  status("Loading the forecast…", "loading");
  try {
    const params = { latitude: place.latitude, longitude: place.longitude, timezone: "auto", forecast_days: 7,
      current: "temperature_2m,apparent_temperature,relative_humidity_2m,weather_code,wind_speed_10m,precipitation",
      hourly: "temperature_2m", daily: "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max" };
    const [f, aq] = await Promise.all([
      getJSON(API, params),
      getJSON(AQ, { latitude: place.latitude, longitude: place.longitude, current: "us_aqi,european_aqi,pm2_5,pm10" }).catch(() => null),
    ]);
    if (!f || !f.current || !f.hourly || !f.daily) throw new Error("unexpected response");
    render(place, f, aq);
    savePlace(place);
    status("");
  } catch (e) {
    $("#wx-result").hidden = true;
    status(`The forecast couldn't be loaded (${e.name === "AbortError" ? "no response within 15 s" : e.message}). Check your connection and try again; Open-Meteo may also be busy.`, "error");
  }
}

async function search(ev) {
  ev.preventDefault();
  const q = $("#wx-q").value.trim();
  const list = $("#wx-matches");
  list.replaceChildren();
  if (q.length < 2) { status("Type at least two letters.", "error"); return; }
  status("Searching…", "loading");
  try {
    const d = await getJSON(GEO, { name: q, count: 6, language: "en", format: "json" });
    const results = (d && d.results) || [];
    if (!results.length) { status(`No place matches “${q}”.`, "error"); return; }
    status("");
    list.replaceChildren(...results.map((p) => {
      const b = el("button", { type: "button", class: "match" }, placeLabel(p));
      b.addEventListener("click", () => { list.replaceChildren(); show(p); });
      return el("li", {}, b);
    }));
  } catch (e) {
    status(`Search is unavailable right now (${e.message}).`, "error");
  }
}

async function main() {
  try { WMO = await (await fetch("data/wmo.json")).json(); } catch (e) { WMO = {}; }
  $("#wx-form").addEventListener("submit", search);
  show(loadPlace());
}

main();
