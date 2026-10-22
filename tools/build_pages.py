"""Build Cloudzy's static edition for GitHub Pages (https://willbettleheim.github.io/Cloudzy/).

Cloudzy is a Streamlit app: every page is rendered by Python on a server, which GitHub Pages can't run. This builds
a separate, clearly labelled static site from the app's own parts, and leaves the Python app untouched:

| Page | What runs | Data |
|---|---|---|
| Home | static HTML | feature status, screenshots of the full app |
| Weather | browser JavaScript | Open-Meteo forecast and air quality, fetched live by the visitor's browser |
| Earth | the app's own globe (src/earth_view.py, unchanged) | a snapshot of the hazard feeds taken by this build, labelled with its time |
| Model Lab | static HTML | the committed evaluation reports (reports/*.json); no model runs in the browser |

Every link is relative, so the site works under the /Cloudzy/ project path and any other base path. Pages are
separate HTML files, so refreshing any of them works without client-side routing; 404.html covers mistyped URLs.

Security: no secrets exist or are needed (all sources are key-less). Each page carries a Content Security Policy
(meta tag; only self-hosted scripts, connections only to the listed data and map hosts) and a referrer policy.
After building, `verify()` checks every page, link and asset, rejects development files (Python, models,
databases, environment files) and scans the output for secret-like strings; the build fails if anything is wrong.

Run:  python tools/build_pages.py [--out _site] [--no-live]     (--no-live: skip the hazard snapshot, for offline
      builds; the Earth page then explains that no snapshot is available)
      python tools/build_pages.py --check-only --out _site      (verify an existing build)
Preview under the real base path:  python tools/serve_pages.py
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from datetime import UTC, datetime
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SITE_SRC = ROOT / "site"
REPO_URL = "https://github.com/WillBettleheim/Cloudzy"
PAGES_URL = "https://willbettleheim.github.io/Cloudzy/"
BASE_PATH = "/Cloudzy/"           # GitHub Pages project path; used only by 404.html (see not_found)

CSP = "; ".join((
    "default-src 'none'",
    "script-src 'self'",
    "style-src 'self' 'unsafe-inline'",              # the globe and MapLibre set inline styles
    "img-src 'self' data: blob: https://gibs.earthdata.nasa.gov https://tiles.openfreemap.org",
    "font-src 'self'",
    "connect-src 'self' https://api.open-meteo.com https://geocoding-api.open-meteo.com "
    "https://air-quality-api.open-meteo.com https://tiles.openfreemap.org https://gibs.earthdata.nasa.gov "
    "https://s3.amazonaws.com",
    "worker-src 'self' blob:",                         # MapLibre's tile workers
    "child-src blob:",
    "base-uri 'self'",
    "form-action 'none'",
    "object-src 'none'",
))

NAV = (("index.html", "Home", "home"), ("weather.html", "Weather", "partly_cloudy_day"),
       ("earth.html", "Earth", "travel_explore"), ("lab.html", "Model Lab", "science"))

SHOTS = (("Cloudzy Board.png", "Overview: the essentials for one place, each labelled by the kind of data it is"),
         ("Cloudzy briefing board.png", "Weather: today's briefing and Cloudzy Core's prediction with its 80% range"),
         ("Earth Intelligence 1.png", "Earth Intelligence in the full app"),
         ("Earth Intelligence 2.png", "Earth Intelligence: events, warnings and details"),
         ("AQI Feature 1.png", "Air Quality: the regional index and pollutants"),
         ("Outdoor Intelligence.png", "Outdoor Intelligence: better times for outdoor activity"),
         ("Alerts and news.png", "Alerts"),
         ("Weather Forecast .png", "Forecast charts"))

# What works where. Kinds: ok (static), api (browser calls a public API), snap (build-time snapshot), py (Python).
FEATURES = (
    ("Weather forecast and air quality for any place", "api", "Open-Meteo, from your browser"),
    ("Earth Intelligence globe, situation selector, filters, timeline", "snap", "Hazard feeds as of the last build"),
    ("Model Lab: evaluation results, station check, upgrade experiment", "ok", "Committed reports"),
    ("Cloudzy Core live predictions and 80% ranges", "py", "Needs the Python model at run time"),
    ("Prediction History (tracking against the reanalysis)", "py", "Needs a server database"),
    ("Weather Explorer (global GFS maps and 3D globe)", "py", "Needs Python to decode forecast grids"),
    ("Outdoor Intelligence, Alerts, briefings and Overview tiles", "py", "Rule engines in Python"),
    ("History & Trends, Places and Settings", "py", "Python pages"),
    ("Live hazard refresh, search by place and event details panel", "py", "Server-side feed fetching"),
)
KIND_LABEL = {"ok": "Static", "api": "Live in browser", "snap": "Build snapshot", "py": "Full app only"}

FORBIDDEN_SUFFIXES = {".py", ".pyc", ".joblib", ".pkl", ".pickle", ".sqlite", ".db", ".env", ".toml", ".ipynb",
                      ".grib2", ".nc", ".parquet", ".csv"}
SECRET_RE = re.compile(r"(AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|sk-[A-Za-z0-9]{20,}|"
                       r"xox[baprs]-[A-Za-z0-9-]{10,}|AIza[0-9A-Za-z_-]{35}|-----BEGIN [A-Z ]*PRIVATE KEY-----|"
                       r"(?i:(api[_-]?key|secret|token|password)[\"' ]*[:=][\"' ]*[A-Za-z0-9/+_-]{16,}))")
MAX_SITE_MB = 80


# --- Layout --------------------------------------------------------------------------------------------

def page(title: str, active: str, body: str, description: str, scripts: tuple[str, ...] = ()) -> str:
    nav = "".join(f'<a href="{href}"{" aria-current=\"page\"" if href == active else ""}>'
                  f'<span class="ico" aria-hidden="true">{ico}</span>{escape(label)}</a>' for href, label, ico in NAV)
    js = "".join(f'<script type="module" src="{s}"></script>' for s in scripts)
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta http-equiv="Content-Security-Policy" content="{CSP}">
<meta name="referrer" content="strict-origin-when-cross-origin">
<meta name="description" content="{escape(description)}">
<meta name="color-scheme" content="dark">
<title>{escape(title)} · Cloudzy</title>
<link rel="icon" href="assets/cloudzy_icon.svg" type="image/svg+xml">
<link rel="stylesheet" href="css/site.css">
</head>
<body>
<a class="skip" href="#main">Skip to content</a>
<header class="top"><div class="in">
<a class="brand" href="index.html"><img src="assets/cloudzy_icon.svg" alt="" width="34" height="34">Cloudzy</a>
<nav class="nav" aria-label="Main">{nav}</nav>
</div></header>
<main id="main">
<p class="banner"><span class="ico" aria-hidden="true">info</span><span><b>Static edition.</b> This site runs without
a server, so some features are presentations of the full Python app. Every value is labelled with where it comes
from. <a href="{REPO_URL}">Run the full app</a>.</span></p>
{body}
</main>
<footer><p>Cloudzy · weather with a little intelligence · <a href="{REPO_URL}">source on GitHub</a> (MIT) ·
<a href="{REPO_URL}/blob/main/SECURITY.md">security policy</a>. Weather data by Open-Meteo.com (CC BY 4.0); air
quality: Copernicus Atmosphere Monitoring Service; hazards: USGS, GDACS, NOAA, NASA FIRMS, U.S. Drought Monitor,
Smithsonian GVP; maps: © OpenFreeMap, © OpenMapTiles, © OpenStreetMap contributors, NASA GIBS, AWS Terrain Tiles.
Situational awareness only: not an official forecast or warning service.</p></footer>
{js}
</body>
</html>
"""


def header(eyebrow: str, icon: str, title: str, sub: str) -> str:
    return (f'<p class="eyebrow"><span class="ico" aria-hidden="true">{icon}</span>{escape(eyebrow)}</p>'
            f'<h1>{escape(title)}</h1><p class="sub">{escape(sub)}</p>')


def table(headers: list[str], rows: list[list], numeric: set[int] = frozenset()) -> str:
    th = "".join(f'<th scope="col">{escape(h)}</th>' for h in headers)
    trs = "".join("<tr>" + "".join(f'<td{" class=\"num\"" if i in numeric else ""}>{escape(str(c))}</td>'
                                   for i, c in enumerate(r)) + "</tr>" for r in rows)
    return f'<div class="table"><table><thead><tr>{th}</tr></thead><tbody>{trs}</tbody></table></div>'


# --- Pages ---------------------------------------------------------------------------------------------

def home(shots: list[tuple[str, str]]) -> str:
    feats = table(["Feature", "On this site", "Why"],
                  [[name, KIND_LABEL[kind], note] for name, kind, note in FEATURES])
    gallery = "".join(f'<figure><img src="assets/shots/{escape(f)}" alt="{escape(c)}" loading="lazy">'
                      f"<figcaption>{escape(c)}</figcaption></figure>" for f, c in shots)
    body = header("Cloudzy", "cloud", "Weather with a little intelligence",
                  "A weather and hazard-awareness app built around one question: can a small machine-learning model "
                  "honestly improve on a global weather forecast for one city, and how do you prove it?")
    body += f"""<div class="actions"><a class="btn" href="weather.html"><span class="ico" aria-hidden="true">partly_cloudy_day</span>Weather</a>
<a class="btn ghost" href="earth.html"><span class="ico" aria-hidden="true">travel_explore</span>Earth globe</a>
<a class="btn ghost" href="lab.html"><span class="ico" aria-hidden="true">science</span>What the model found</a></div>
<h2>What works on this site</h2>
<p class="muted">GitHub Pages serves static files only. The full app (Python and Streamlit) is in the repository and
runs locally with <code>streamlit run streamlit_app.py</code>.</p>
{feats}
<p class="caption">Live in browser: your browser asks Open-Meteo directly; nothing goes through Cloudzy. Build snapshot:
data fetched when this site was last built, shown with that time. Full app only: needs Python on a server.</p>
<h2>The full app</h2>
<div class="shots">{gallery}</div>"""
    return page("Home", "index.html", body, "Cloudzy: weather, air quality, hazards and an honestly tested ML model.")


def weather() -> str:
    body = header("Today", "partly_cloudy_day", "Weather",
                  "The forecast for any place, fetched by your browser from Open-Meteo. These are the provider's "
                  "model estimates, not station readings; Cloudzy Core's own predictions need the full app.")
    body += """<form class="search" id="wx-form" role="search">
<label for="wx-q" class="muted" style="flex-basis:100%">Search for a city or town</label>
<input id="wx-q" type="search" maxlength="80" placeholder="e.g. Mumbai, Reykjavik, Lagos" autocomplete="off">
<button class="btn" type="submit"><span class="ico" aria-hidden="true">search</span>Search</button>
</form>
<ul class="matches" id="wx-matches" aria-label="Matching places"></ul>
<p id="wx-status" class="status" role="status" hidden></p>
<section id="wx-result" hidden>
<div class="hero"><p class="place" id="wx-place"></p><p class="muted" id="wx-time"></p>
<p class="temp" id="wx-temp"></p><p class="cond" id="wx-cond"></p><p class="muted" id="wx-feels"></p></div>
<h2>Next 48 hours</h2><div class="chart" id="wx-chart"></div>
<p class="caption">Temperature, hourly, in the place's local time. Source: Open-Meteo forecast (best-match model blend).</p>
<h2>Next 7 days</h2>
<div class="table" id="wx-days"><table><thead><tr><th scope="col">Day</th><th scope="col">Conditions</th>
<th scope="col">High / low</th><th scope="col">Rain chance</th></tr></thead><tbody></tbody></table></div>
<h2>Air quality now</h2><div class="stats" id="wx-aq"></div>
<p class="caption">CAMS model estimates via the Open-Meteo Air Quality API, not monitoring-station readings.</p>
</section>"""
    return page("Weather", "weather.html", body, "Live forecast and air quality for any place.", ("js/weather.js",))


def earth(meta: dict) -> str:
    if meta.get("available"):
        rows = [[s["name"], s["label"], s.get("records", "")] for s in meta["statuses"]]
        status = (f'<p class="status"><b>Snapshot taken {escape(meta["taken_utc"])} UTC.</b> The events on the globe '
                  "are real records from the feeds listed below at that time; they are not refreshed until the site is "
                  "rebuilt. For current events, use the full app or the sources themselves.</p>")
        feeds = ('<details class="feeds"><summary><span class="ico" aria-hidden="true">sensors</span>Feed status at the '
                 "snapshot</summary>" + table(["Source", "Status", "Records"], rows, {2}) + "</details>")
    else:
        status = ('<p class="status error">No hazard snapshot was included in this build, so the globe has no events '
                  "to draw. The full app fetches the feeds live.</p>")
        feeds = ""
    body = header("Planet · disasters and hazards", "travel_explore", "Earth Intelligence",
                  "The full app's 3D globe with its disaster situation selector, presets, filters and timeline. "
                  "Situational awareness only, not an official warning service.")
    body += status + '<p id="globe-note" hidden></p><div id="globe" aria-label="Earth Intelligence globe"></div>' + feeds
    body += ('<p class="caption">In an emergency, follow your local emergency services and national weather or '
             "disaster agency. No event shown nearby doesn't mean an area is safe.</p>")
    return page("Earth Intelligence", "earth.html", body, "Real disasters and hazards on a 3D globe.", ("js/earth.js",))


def lab(reports: Path) -> str:
    def load(name):
        p = reports / name
        return json.loads(p.read_text()) if p.exists() else None

    mos, se, sm, iv, rp = (load(n) for n in ("mos_delhi.json", "station_eval_delhi.json", "station_model_delhi.json",
                                              "intervals_delhi.json", "reproducibility.json"))
    body = header("Cloudzy Core · offline evaluation", "science", "Model Lab",
                  "What Cloudzy's temperature model for New Delhi achieved, from the evaluation reports in the "
                  "repository. These are offline test results computed by the Python pipeline; no model runs here.")
    if mos:
        rows = []
        for h, r in mos["horizons"].items():
            t, d = r["test"], r["test"]["candidate_minus_control"]
            rows.append([f"+{h} h", t["persistence"]["mae_c"], t["lagged_ifs_forecast"]["mae_c"], t["control"]["mae_c"],
                         t["candidate"]["mae_c"], f"{d['mae_diff_c']:+.3f} [{d['ci95_low_c']:+.3f}, {d['ci95_high_c']:+.3f}]"])
        body += ("<h2>The October 2026 upgrade (against the reanalysis)</h2>"
                 f"<p>Test period {escape(mos['spans']['test'][0])} to {escape(mos['spans']['test'][1])} (exclusive), "
                 "decision rule written before the results were seen. Mean absolute error, °C.</p>"
                 + table(["Ahead", "Persistence", "Lagged ECMWF", "Previous Cloudzy", "New Cloudzy", "New − previous [95% CI]"],
                         rows, {1, 2, 3, 4}))
        body += f'<p class="caption">Decision: {"adopted" if mos["decision"]["adopt"] else "not adopted"}.</p>'
    if se:
        rows = [[f"+{h} h", r["test"][se["primary"]]["candidate"]["mae_c"], r["test"][se["primary"]]["ifs_at_target"]["mae_c"],
                 r["test"][se["primary"]]["reanalysis"]["mae_c"]] for h, r in se["horizons"].items()]
        body += ("<h2>Against a real thermometer</h2><p>Routine METAR readings from Delhi's Palam airport station. "
                 "<b>Against the station, the raw ECMWF forecast is closer than Cloudzy at every horizon</b>: the "
                 "reanalysis Cloudzy learns from runs about 1.5 °C colder than the city at night.</p>"
                 + table(["Ahead", "Cloudzy", "ECMWF value", "Reanalysis (Cloudzy's target)"], rows, {1, 2, 3}))
    if sm:
        rows = [[f"+{h} h", r["test"]["candidate_A"]["mae_c"], r["test"]["candidate_B"]["mae_c"],
                 r["test"]["raw_ecmwf"]["mae_c"], r["test"]["station_persistence"]["mae_c"]] for h, r in sm["horizons"].items()]
        dec = sm["decision"]
        body += ("<h2>Trained on the station's own readings</h2><p>A pre-registered experiment. With the station's "
                 "latest readings as inputs (B), Cloudzy roughly halves ECMWF's error one hour ahead and wins up to "
                 f"three hours, but is worse from 12 hours. Verdict under the rules: A {escape(dec['A']['verdict'])}; "
                 f"B {escape(dec['B']['verdict'])}.</p>"
                 + table(["Ahead", "A: station target", "B: + station inputs", "ECMWF value", "Station persistence"],
                         rows, {1, 2, 3, 4}))
    if iv:
        rows = []
        for h, r in iv["horizons"].items():
            d = r["deployed"]["levels"]
            rows.append([f"+{h} h", f"{d['0.80']['q_lo_c']:+.2f} to {d['0.80']['q_hi_c']:+.2f}",
                         f"{d['0.80']['validation']['coverage']:.1%}", f"{d['0.95']['validation']['coverage']:.1%}"])
        body += ("<h2>Prediction ranges</h2><p>Split-conformal ranges, checked on a month the deployed model never saw.</p>"
                 + table(["Ahead", "80% range around prediction, °C", "80% coverage", "95% coverage"], rows, {2, 3}))
    if rp:
        ok = sum(m.get("status") == "reproduced" for m in rp["models"].values())
        body += (f"<h2>Reproducibility</h2><p>{ok} of {len(rp['models'])} described models reproduce their recorded "
                 f"metric exactly from cached data (<code>python -m src.reproduce</code>, {escape(rp['created_at_utc'][:10])}).</p>")
    body += (f'<p class="caption">Full details: <a href="{REPO_URL}/blob/main/MODEL_CARD.md">model card</a>. Experimental '
             "student project; not an operational forecast.</p>")
    return page("Model Lab", "lab.html", body, "How Cloudzy's temperature model was tested, including where it falls short.")


def not_found() -> str:
    body = header("404", "explore_off", "Page not found",
                  "This page doesn't exist on Cloudzy's static site. It may belong to the full Python app.")
    body += '<div class="actions"><a class="btn" href="index.html">Go to the home page</a></div>'
    # GitHub Pages serves 404.html at whatever URL was requested, so its relative links need a fixed base.
    return page("Not found", "", body, "Page not found.").replace("<head>\n", f'<head>\n<base href="{BASE_PATH}">\n', 1)


# --- Data ------------------------------------------------------------------------------------------------

def hazard_snapshot(out: Path) -> tuple[dict | None, dict]:
    """Fetch the hazard feeds once (as the app does), write the globe's layer files and return its data payload."""
    import pandas as pd
    os.environ["CLOUDZY_EARTH_DIR"] = str(out / "app" / "static" / "earth")
    from src.hazards import situations as SIT
    from src.hazards.schema import KINDS
    from src.hazards.sources import SOURCES
    from src.hazards.store import Query, store
    snap = store().snapshot(Query("week", 2.5), wait_s=40)
    now = pd.Timestamp.now(tz="UTC")
    events = snap.events
    statuses = [{"source": s, "name": SOURCES[s].name, "label": st.label} for s, st in snap.statuses.items()]
    data = {
        "layers": snap.layers, "statuses": statuses, "situations": SIT.selector_data(events),
        "presets": {k: {"label": label, "keys": list(keys)} for k, (label, keys) in SIT.PRESETS.items()},
        "record_layers": SIT.LAYERS, "basins": list(SIT.BASINS), "sit_sync": None, "ml": None,
        "chips": {"period": None, "region": None}, "kinds": KINDS,
        "source_names": {k: s.name for k, s in SOURCES.items()},
        "period": {"start": int((now - pd.Timedelta(days=7)).timestamp() * 1000), "end": int(now.timestamp() * 1000)},
        "imagery_day": (now - pd.Timedelta(days=1)).strftime("%Y-%m-%d"),
        "attribution": "Data: USGS, GDACS, NOAA (NHC, NWS, tsunami centres), NASA FIRMS, U.S. Drought Monitor, "
                       "Smithsonian GVP · snapshot " + now.strftime("%Y-%m-%d %H:%M UTC"),
        "focus": None, "region": None, "selected": None, "selected_geometry": None,
    }
    meta = {"available": True, "taken_utc": now.strftime("%Y-%m-%d %H:%M"), "events": len(events),
            "statuses": [{"name": SOURCES[s].name, "label": st.label, "records": st.count} for s, st in snap.statuses.items()]}
    return data, meta


def copy_assets(out: Path) -> list[tuple[str, str]]:
    import streamlit
    from src.earth_view import CSS, JS
    from src.provider import WMO_CODES
    from src.theme import _vars
    shutil.copytree(SITE_SRC / "js", out / "js")
    (out / "css").mkdir()
    (out / "css" / "site.css").write_text(":root{" + _vars() + "}\n" + (SITE_SRC / "css" / "site.css").read_text())
    (out / "js" / "earth-globe.js").write_text(JS)            # the app's globe component, unchanged
    (out / "css" / "earth-globe.css").write_text(CSS)
    (out / "data").mkdir()
    (out / "data" / "wmo.json").write_text(json.dumps({str(k): v for k, v in WMO_CODES.items()}))
    assets = out / "assets"
    (assets / "fonts").mkdir(parents=True)
    for f in ("Inter-latin.woff2", "Outfit-latin.woff2", "OFL-inter.txt", "OFL-outfit.txt"):
        shutil.copy(ROOT / "static" / "vendor" / "fonts" / f, assets / "fonts" / f)
    icon_font = next((Path(streamlit.__file__).parent / "static").rglob("MaterialSymbols-Rounded*.woff2"))
    shutil.copy(icon_font, assets / "fonts" / "MaterialSymbols-Rounded.woff2")
    (assets / "fonts" / "MaterialSymbols-NOTICE.txt").write_text(
        "Material Symbols (Google), Apache License 2.0, copied from the Streamlit package.\n")
    for f in ("cloudzy_icon.svg", "cloudzy_logo.svg"):
        shutil.copy(ROOT / "assets" / f, assets / f)
    maplibre = out / "app" / "static" / "vendor" / "maplibre"       # the path the globe component loads from
    maplibre.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(ROOT / "static" / "vendor" / "maplibre", maplibre)
    shots = []
    (assets / "shots").mkdir()
    for name, caption in SHOTS:
        src = ROOT / "images" / name
        if src.exists():
            clean = re.sub(r"[^a-z0-9]+", "-", src.stem.lower()).strip("-") + src.suffix.lower()
            shutil.copy(src, assets / "shots" / clean)
            shots.append((clean, caption))
    return shots


def build(out: Path, live: bool = True) -> dict:
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    shots = copy_assets(out)
    meta: dict = {"available": False}
    if live:
        data, meta = hazard_snapshot(out)
        (out / "data" / "earth.json").write_text(json.dumps(data, separators=(",", ":"), default=str))
    pages = {"index.html": home(shots), "weather.html": weather(), "earth.html": earth(meta),
             "lab.html": lab(ROOT / "reports"), "404.html": not_found()}
    for name, html in pages.items():
        (out / name).write_text(html)
    (out / ".nojekyll").write_text("")                     # serve files as they are (no Jekyll processing)
    (out / "build-info.json").write_text(json.dumps({
        "built_at_utc": datetime.now(UTC).isoformat(timespec="seconds"), "hazard_snapshot": meta.get("taken_utc"),
        "commit": os.environ.get("GITHUB_SHA", "local")}))
    return meta


# --- Verification -----------------------------------------------------------------------------------------

def verify(out: Path, need_snapshot: bool = False) -> list[str]:
    problems = []
    for f in ("index.html", "weather.html", "earth.html", "lab.html", "404.html", "css/site.css", "css/earth-globe.css",
              "js/weather.js", "js/earth.js", "js/earth-globe.js", "data/wmo.json", "assets/cloudzy_icon.svg",
              "assets/fonts/MaterialSymbols-Rounded.woff2", "app/static/vendor/maplibre/maplibre-gl.js", ".nojekyll"):
        if not (out / f).is_file():
            problems.append(f"missing {f}")
    if need_snapshot and not (out / "data" / "earth.json").is_file():
        problems.append("missing data/earth.json (hazard snapshot)")
    total = 0
    for f in out.rglob("*"):
        if not f.is_file():
            continue
        total += f.stat().st_size
        rel = f.relative_to(out).as_posix()
        if f.suffix.lower() in FORBIDDEN_SUFFIXES or f.name.startswith(".env") or "secret" in f.name.lower():
            problems.append(f"development or private file in the site: {rel}")
        if f.suffix.lower() in {".html", ".js", ".css", ".json", ".txt", ".svg"}:
            text = f.read_text(errors="ignore")
            if SECRET_RE.search(text):
                problems.append(f"secret-like string in {rel}")            # the value itself is never printed
            if str(ROOT) in text or "/Users/" in text:
                problems.append(f"local file path in {rel}")
        if f.suffix == ".html":
            text = f.read_text()
            if "Content-Security-Policy" not in text:
                problems.append(f"no Content Security Policy in {rel}")
            for ref in re.findall(r'(?<!base )(?:href|src)="([^"]+)"', text):
                if ref.startswith(("http://",)):
                    problems.append(f"insecure http link in {rel}: {ref}")
                if ref.startswith(("https://", "mailto:", "#", "data:")):
                    continue
                if ref.startswith("/"):
                    problems.append(f"root-relative link breaks under /Cloudzy/: {rel} → {ref}")
                    continue
                if f.name == "404.html":                                   # resolved against <base>, i.e. the site root
                    target = out / ref.split("#")[0].split("?")[0]
                else:
                    target = (f.parent / ref.split("#")[0].split("?")[0])
                if not target.exists():
                    problems.append(f"broken link in {rel}: {ref}")
    earth_json = out / "data" / "earth.json"
    if earth_json.is_file():
        layers = json.loads(earth_json.read_text()).get("layers", {})
        for name, url in layers.items():
            if not (out / url).is_file():
                problems.append(f"globe layer {name} missing: {url}")
    if total > MAX_SITE_MB * 1e6:
        problems.append(f"site is {total / 1e6:.0f} MB (limit {MAX_SITE_MB} MB)")
    return problems


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="_site")
    ap.add_argument("--no-live", action="store_true", help="skip the hazard snapshot (offline build)")
    ap.add_argument("--check-only", action="store_true", help="verify an existing build without rebuilding")
    args = ap.parse_args()
    out = (ROOT / args.out).resolve() if not Path(args.out).is_absolute() else Path(args.out)
    if not args.check_only:
        meta = build(out, live=not args.no_live)
        print(f"Built {out} · hazard snapshot: {meta.get('taken_utc') or 'none'} · events: {meta.get('events', 0)}")
    problems = verify(out, need_snapshot=not args.no_live and not args.check_only)
    size = sum(f.stat().st_size for f in out.rglob("*") if f.is_file()) / 1e6
    if problems:
        print(f"Verification FAILED ({len(problems)} problems):")
        for p in problems:
            print("  -", p)
        sys.exit(1)
    print(f"Verification passed: {sum(1 for _ in out.rglob('*.html'))} pages, {size:.1f} MB, no secrets, no "
          "development files, all links and assets resolve.")


if __name__ == "__main__":
    main()
