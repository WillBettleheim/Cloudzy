# Cloudzy architecture

Cloudzy is a Python project. It has a Streamlit front end and two separate
halves: one serves the live weather app, and one runs the offline machine-learning
experiments behind it. The app never trains a model and never fits anything per
request.

```
                          ┌───────────────────────── Open-Meteo (free, CC BY 4.0) ─────────────────────────┐
                          │ Geocoding API     Forecast API        Historical Forecast API     Archive API   │
                          └──────┬──────────────┬──────────────────────┬───────────────────────────┬───────┘
                                 │              │                      │                           │
   LIVE APP (per request, cached 10 min)        │                      │  OFFLINE (batch commands) │
   ─────────────────────────────────────        │                      │  ─────────────────────────│────────
   src/data_collection.fetch_json  ◄── one HTTP client: timeouts, retries, Retry-After, clear errors ──►
          │                                                            │                           │
   src/provider.py ── search_locations, fetch_forecast                 │      src/data_validation.py
          │   (validated, metric, time-zone aware)                     │      src/features.py (27 features)
          ▼                                                            │      src/train.py, multistep.py,
   src/units.py ── display units      src/insights.py ── summary,      │      shift.py, compact.py
   src/preferences.py ── per browser  alerts, suggestions, briefing    │      → models/…compact.joblib (2 MB)
          │                                   │                        │      → reports/*.json
          ▼                                   ▼                        │      src/lab_data.py → reports/lab/
   app_pages/weather.py ◄── src/weather_view.py (HTML), src/ui.py (charts)   (refit, 17 reproduction checks)
          │                                                            │      src/refit.py (monthly)
          │  Delhi only (≤ 15 km)                                      │
          ▼                                                            │
   Browser ── navigator.geolocation (one-time, with permission) ── src/geolocation.py
          │   validate → exact coords to Forecast API; rounded coords to Nominatim (place name)
          ▼
   src/briefing.cloudzy_block ──► src/service.py ── live IFS inputs ───┘
          │                        (validates 25 h, refuses if incomplete)
          ▼
   src/monitoring.py ── records each prediction once (SQLite) ─► evaluated against the archive
                        at the exact target hour (Predictions page button, or python -m src.monitoring)

   app_pages/lab.py ◄── reads committed reports/lab/ + reports/*.json, model sidecar, tracking aggregates
   app_pages/predictions.py ◄── src/monitoring.py (query, aggregate, evaluate)
   app_pages/history.py ◄── src/history.py (Archive API on demand: hourly, and daily for comparisons)
   app_pages/settings.py, places.py, alerts.py ◄── src/preferences.py (+ src/app_data.py for alerts)

   Open-Meteo Air Quality API (CAMS) ──► src/air_quality.py (validated, local time, coverage trimmed)
          │                               └─ src/aqi.py: standards, categories, India NAQI (CPCB method)
          ├─► app_pages/air_quality.py ◄── src/air_view.py (hero, pollutants, charts)
          ├─► src/outdoor.py (rule engine) + Forecast API ──► app_pages/outdoor.py
          └─► src/air_alerts.py (air, PM2.5, saved plans) ──► Weather and Alerts pages
```

Air quality and Outdoor Intelligence are documented in [AIR_QUALITY.md](AIR_QUALITY.md).

```
   USGS · GDACS · NHC · NWS · NASA FIRMS · U.S. Drought Monitor · USGS volcanoes · GVP · NOAA tsunami
          │  (server side, src/hazards/<provider>.py: validate, normalise to HazardEvent)
          ▼
   src/hazards/store.py ── per-source cache and TTL, background refresh, last-good fallback,
          │                 cross-source merging, content-hashed files in static/earth/
          ▼
   app_pages/earth.py ◄── src/earth_view.py (MapLibre globe, browser) ◄── static/earth/*.json
          │                 + OpenFreeMap tiles, NASA GIBS imagery, AWS terrain tiles
          └─ src/hazards/analysis.py (filters, summaries, nearby settlements), src/earth_search.py
```

Earth Intelligence is documented in [EARTH_INTELLIGENCE.md](EARTH_INTELLIGENCE.md).

## Layers

| Layer | Modules | Responsibility |
|---|---|---|
| HTTP | `data_collection.fetch_json` | Every network call goes through this one function, which handles timeouts, retries with backoff, HTTP 429 `Retry-After`, 4xx/5xx handling and user-readable errors. `INTERACTIVE` settings fail within about 25 s. Batch downloads are patient. |
| Data services | `provider`, `air_quality`, `hazards.*` (one module per disaster source), `history`, `service`, `data_validation`, `geolocation`, `earth_search` | Validate units, time zones, array lengths and physical bounds. Missing values stay missing; optional variables a model lacks are reported as unavailable; wrong units are fatal. |
| Domain logic | `insights`, `aqi`, `outdoor`, `air_alerts`, `units`, `preferences`, `monitoring` | Pure functions, testable without Streamlit or the network. |
| Presentation | `theme` (design tokens, global CSS, icons, chips), `weather_view`, `air_view`, `earth_view`, `ui`, `lab_ui` | Build HTML and Plotly figures from data and units. They never fetch anything. |
| Pages | `app_pages/{weather,air_quality,outdoor,history,alerts,predictions,lab,places,settings}.py`, `streamlit_app.py`, with `app_data` (shared cached calls), `place_select` (place switcher) and `nav` (safe page links) | Lay out the page, cache results, and handle loading, empty and error states. |
| ML (offline) | `features`, `splits`, `models`, `train`, `multistep`, `shift`, `compact`, `refit`, `lab_data` | Chronological experiments, the deployed bundle and reproducible Lab data. |

## Data types are never mixed

| Type | Source | Shown as |
|---|---|---|
| Provider forecast | Forecast API (`best_match`) | The main weather app: hero, hourly, 7-day, details |
| Hazard records | USGS, GDACS, NHC, NWS, FIRMS, USDM, USGS volcanoes, GVP, NOAA tsunami centres | Earth Intelligence. Every record has a kind (observed, forecast, official warning, automatic estimate, satellite detection, assessment, catalogue) |
| Air-quality forecast | Air Quality API (CAMS) | Air Quality and Outdoor pages, labelled "model estimate, not a station reading". Earlier hours are model values, never called observations |
| Cloudzy Core prediction | `service.py` + compact bundle, Delhi only | A separate "🧪 Cloudzy Core · experimental" section |
| Historical reanalysis | Archive API | History page, labelled "historical reanalysis"; also the reference for tracked predictions |
| Evaluation results | `reports/`, `reports/lab/` (committed) | Lab scoreboard, breakdowns, explainability |
| Tracked predictions | `data/monitoring/cloudzy_predictions.sqlite` (local only) | Predictions page; summary in Lab → Live tracking |

## Caching and request budget

- **Cache:** search, forecasts, air quality, Cloudzy results and history are cached by
  `st.cache_data`. Forecasts and Cloudzy results live for 10 minutes, air quality
  for 15 minutes, history and comparison years for 6 hours.
- **Freshness:** the page shows when its data was retrieved, so a cached
  forecast is never presented as freshly fetched.
- **Request budget:** one place costs at most four requests (geocoding,
  forecast, air quality, and Cloudzy's inputs near Delhi). That is far inside Open-Meteo's
  free limits: 600 per minute, 5,000 per hour, 10,000 per day.

## Per-visitor state (in each visitor's browser)

| Where | Contents |
|---|---|
| `localStorage["cloudzy-preferences-v1"]` (`src/browser_prefs.py`) | Unit override, favourites and home city (city coordinates), alert thresholds, categories and dismissals, summary style, auto-locate and notification choices (yes/no), AQI standard, outdoor planner choices, up to 5 saved outdoor windows (city coordinates and times). Never device coordinates or health information |
| `localStorage["cloudzy-notified-alerts"]` (`src/notify.py`) | Keys of alerts already sent as browser notifications |

`streamlit_app.py` reads the settings once per session, before any page draws,
and pages save through `src.preferences`. Stored settings are treated as
untrusted input and validated field by field. If the browser blocks storage,
settings last for the session only. Setting `CLOUDZY_PREFS_STORE=file` (or
`CLOUDZY_PREFS_PATH`) switches back to one JSON file for a single local user;
the tests use that mode.

## Server state (never committed, shared by every visitor)

| Path | Contents | Override |
|---|---|---|
| `data/monitoring/` | `cloudzy_predictions.sqlite` (predictions, refusals); the old `predictions.jsonl` is imported once and kept | `CLOUDZY_MONITORING_DIR`, `CLOUDZY_MONITORING=0` |
| `data/live_snapshots/` | Raw live-input responses, deleted after 30 days | `CLOUDZY_SNAPSHOTS=0` |
| `static/explorer/` | GFS forecast frames for the Weather Explorer (newest complete run only, ~31 MB) | `CLOUDZY_EXPLORER_DIR`, `CLOUDZY_EXPLORER_REFRESH=0` |
| `static/earth/` | Earth Intelligence layers (events, fire detections, drought, volcano catalogue; ~4 MB, 3 versions each) | `CLOUDZY_EARTH_DIR` |
| Memory | Hazard feed cache (one entry per source and query) | |
| Memory | Place-name lookups (at most 1,000, oldest dropped first); Streamlit's data caches (public weather data only) | |

## Tests

- **Offline suite:** `python -m pytest` runs 824 tests and needs no network.
  `tests/conftest.py` points preferences, monitoring and snapshots at temporary
  folders, so tests never touch real user data.
- **Live checks:** `python -m pytest -m integration` runs 12 tests against the
  real APIs (including the Air Quality API and six hazard feeds).
- **App states:** headless `streamlit.testing.v1.AppTest` covers every page
  state, with the network replaced by fakes.

## Device location

`src/geolocation.py` holds a components-v2 script that runs in the page (no iframe, so no iframe
permission policy is involved). It answers each numbered request exactly once: coordinates, or a
reason code. Python validates the result and applies it only if the request number matches the one
pending. The coordinates live in `st.session_state` only. Exact coordinates go to the Open-Meteo
Forecast API; coordinates rounded to 2 decimals go to Nominatim, cached and throttled to 1 request
per second.

## Cloudzy Core pipeline and artifacts

`python -m src.pipeline {fetch,train,evaluate,all} [--city … --country … | --lat --lon --name --timezone] [--start --end]`
wires `data_collection` → `data_validation` → `features` → `splits` → `train` → `artifacts` for
any location and period. Every model file has a `.json` sidecar describing it (version, SHA-256,
features with units, target, provenance, splits, metrics). `artifacts.load_trusted` verifies the
structure, feature schema and checksum before the app or the evaluator uses a model.

## Prediction tracking

- **Record:** `monitoring.record_prediction` stores one row per (model version,
  location, issue time, horizon, variable). Times are converted to UTC first and
  must carry a time zone, so the same prediction written with another offset is
  still one record. The row ID is a SHA-256 of those fields, so re-renders and
  retries can't duplicate a record (`INSERT OR IGNORE`). Non-finite values, or
  target times that aren't issue + horizon, are rejected.
- **Evaluate:** `monitoring.evaluate` only requests reference data for pending
  records whose target date is more than the reanalysis lag (6 days, by the UTC
  date) in the past; newer records cost no request. It matches the exact UTC
  hour and checks the reference is in UTC and °C. Updates apply only while a
  record is still pending, so repeated or overlapping runs can't double-count.
  - Reference value missing for that hour: the record stays pending with a
    note, and is retried on every later check.
  - Still missing 30 days after the target hour: `unavailable`, with the reason.
  - Request failed: no record changes; the failure is stored.
  Every run, successful or not, is stored as `meta.last_check` (time, counts,
  how far the reference data reached, error).
- **Status breakdown:** `monitoring.classify` explains each record: evaluated;
  waiting for the target hour; waiting for publication; due on the next run;
  checked with the value still missing; unavailable. Each record also gets the
  first date it's checkable.
- **Aggregate:** only `evaluated` rows count, one model version at a time.
  Results carry sample counts, issue times and the date range, and are flagged
  as not conclusive below 30 issue times. Reference values are reanalysis (a
  model reconstruction), not station readings; the most recent ones are
  preliminary (ERA5T) and may be revised slightly later.
- **Scheduling:** `python -m src.monitoring` exits with status 1 when the
  reference request fails, so cron or launchd can run it daily (see README).

## Alerts and notifications

- **What they are:** threshold rules in `insights.alerts`, documented on the
  Weather page and in the Settings page. They are never official warnings.
- **Identity:** each alert's key is its kind plus its time window, which gives
  one alert per kind and window. Dismissing stores that key for 24 hours, so a
  new or changed window shows again.
- **Browser notifications** (`src/notify.py`): optional, off by default.
  - Permission is requested only from a click on the component's own button.
  - Each key notifies once (tracked in the browser's localStorage).
  - Notifications appear only while a tab is open: there's no service worker or
    push.
  - If notifications are blocked or unsupported, alerts stay on the page.

## Weather Explorer

```
NOAA GFS on AWS (.idx + GRIB2, byte ranges) ──► src/explorer_data.py (background thread)
   decode with ecCodes, check the 1° grid, convert units, uint16 encode (65535 = missing)
   ──► static/explorer/<run>/fXXX_<field>.bin + manifest.json   (newest complete run only)
        served by Streamlit's static server (/app/static, enableStaticServing)
app_pages/explorer.py ──► src/explorer_view.py (components v2 + Leaflet, in the browser)
   fetches manifest and frames; renders rasters, wind particles and isobars on canvases;
   bounded frame cache (64 fields); stale requests aborted; map click → point (Python)
   → Open-Meteo point forecast (src.app_data) and, near Delhi only, Cloudzy Core
```

All layers are GFS model forecast fields; the page never calls them observations or radar.

## Design system

**Art direction: "Atmospheric Intelligence."** Deep ink and midnight surfaces, electric sky and cobalt accents
used sparingly, cloud-white and silver text, and amber reserved for sunlight and highlights.

- **Hierarchy:** one focal point per screen, editorial headers (`theme.page_header`), and hairline sections and
  readouts rather than a box around everything.
- **Weather hero:** `weather_view.atmosphere()` picks the sky treatment from the actual WMO code, `is_day` and local
  sunrise/sunset. Effects are CSS only, moved by transform, marked `aria-hidden`, and static under reduced motion.
  The page says the sky is decoration, not imagery.
- **Type:** Inter for the interface and Outfit for display figures. Both are SIL OFL, self-hosted in
  `static/vendor/fonts` and registered with `[[theme.fontFaces]]`.

`src/theme.py` holds the tokens, exposed as `--cz-*` CSS custom properties:
- colours (midnight, navy, sky, cloud, violet, sunshine and status colours);
- spacing, radii, type sizes, shadows and motion timings.

`streamlit_app.py` injects the global styles once:
- sidebar styling;
- metric and container cards;
- visible keyboard focus;
- wrapping of long names;
- a reduced-motion override.

Component styles in `src/weather_view.py` use the same tokens. Surfaces and borders are translucent, so they read well
on both Streamlit themes. Icons are Material Symbols, which Streamlit already ships:
- `:material/name:` in widgets;
- `theme.icon()` in custom HTML, hidden from screen readers unless given a label.

Status is never shown by colour alone: chips carry an icon and text.
